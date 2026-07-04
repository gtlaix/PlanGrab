"""FastAPI wrapper around the engine — a local-only web UI.

The backend is a thin shell over :mod:`plangrab.engine`; all real work lives in
the engine so the same logic backs the CLI and (later) any other front-end.

JSON CONTRACT (stable; the Claude Design frontend in ``static/`` targets this)
=============================================================================

POST /api/discover
  request : {"url": "<documents-page-url>"}
  200     : {"lpa": str, "system": str, "count": int,
             "documents": [ {"index": int, "total": int, "title": str,
                             "date": str|null, "doc_type": str|null,
                             "plan_number": str|null, "source_url": str}, ... ]}
  400     : {"error": "<message>"}              # unknown system / bad URL / no table

POST /api/download   (streaming response, media type application/x-ndjson)
  request : {"url": str, "folder": str}
  stream  : one JSON object per line, in order:
              {"type": "discovered", "count": int}
              {"type": "file", "index": int, "total": int, "title": str,
               "filename": str, "status": "downloaded"|"skipped"|"failed",
               "error": str|null}              # one per document, as it finishes
              {"type": "done", "summary": {"downloaded": int, "skipped": int,
               "failed": int, "folder": str, "manifest": str}}
            …or, on a fatal pre-download error:
              {"type": "error", "message": str}

GET /api/pick-folder
  200     : {"path": "<absolute path>"}         # native dialog; "" if cancelled
  200     : {"path": "", "error": "<message>"}  # dialog unavailable -> UI uses manual field

GET /
  Serves static/index.html (the UI).
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from plangrab.engine import (
    Config, Registry, UnknownSystemError, download_all, get_scraper, make_client,
    user_agent_for,
)
from plangrab.engine.compat import (
    ALL_STATUSES, OK, UNCHECKED, days_since, load_status, merge_for_dashboard,
    normalise_name, save_status,
)
from plangrab.engine.registry import DATA_DIR, SYSTEMS

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="PlanGrab")
_config = Config.load()


class DiscoverRequest(BaseModel):
    url: str


class DownloadRequest(BaseModel):
    url: str
    folder: str


def _doc_json(d) -> dict:
    return {
        "index": d.index,
        "total": d.total,
        "title": d.title,
        "date": d.date.strftime(_config.date_format) if d.date else None,
        "doc_type": d.doc_type,
        "plan_number": d.plan_number,
        "source_url": d.source_url,
    }


@app.post("/api/discover")
def discover(req: DiscoverRequest):
    try:
        scraper = get_scraper(req.url, _config.lpa_registry)
    except UnknownSystemError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    client = make_client(_config, user_agent_for(scraper, _config))
    try:
        docs = scraper.discover(client, req.url)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    finally:
        client.close()

    return {
        "lpa": scraper.lpa_name,
        "system": scraper.system_name,
        "count": len(docs),
        "documents": [_doc_json(d) for d in docs],
    }


@app.post("/api/download")
def download(req: DownloadRequest):
    def gen():
        events: "queue.Queue" = queue.Queue()

        def progress(ev: dict) -> None:
            events.put(ev)

        def work() -> None:
            client = None
            try:
                scraper = get_scraper(req.url, _config.lpa_registry)
                client = make_client(_config, user_agent_for(scraper, _config))
                docs = scraper.discover(client, req.url)
                events.put({"type": "discovered", "count": len(docs)})
                results = download_all(scraper, docs, req.folder, _config,
                                       client=client, progress=progress)
                folder = str(Path(req.folder).expanduser().resolve())
                events.put({"type": "done", "summary": {
                    "downloaded": sum(r.status == "downloaded" for r in results),
                    "skipped": sum(r.status == "skipped" for r in results),
                    "failed": sum(r.status == "failed" for r in results),
                    "folder": folder,
                    "manifest": str(Path(folder) / "manifest.csv"),
                }})
            except Exception as exc:
                events.put({"type": "error", "message": str(exc)})
            finally:
                if client is not None:
                    client.close()
                events.put(None)  # sentinel: stream complete

        threading.Thread(target=work, daemon=True).start()
        while True:
            ev = events.get()
            if ev is None:
                break
            yield json.dumps(ev) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.get("/api/pick-folder")
def pick_folder():
    """Open a native folder picker (tkinter), in an isolated subprocess.

    Running Tk in its own process keeps it off the server's worker threads (Tk
    must own the main thread, and macOS is strict about it). If tkinter is
    missing or the dialog can't open, return an empty path + error so the UI can
    fall back to the manual path field.
    """
    try:
        out = subprocess.run(
            [sys.executable, "-c", _PICKER_SCRIPT],
            capture_output=True, text=True, timeout=300,
        )
        path = out.stdout.strip()
        if out.returncode != 0 and not path:
            return {"path": "", "error": out.stderr.strip() or "folder picker unavailable"}
        return {"path": path}
    except Exception as exc:
        return {"path": "", "error": str(exc)}


_PICKER_SCRIPT = (
    "import tkinter as tk\n"
    "from tkinter import filedialog\n"
    "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
    "p = filedialog.askdirectory(title='Choose download folder')\n"
    "r.destroy()\n"
    "print(p or '')\n"
)


def _summarise(rows: list[dict]) -> dict:
    """Coverage totals + per-system and per-status breakdowns for the header."""
    total = len(rows)
    by_status: dict[str, int] = {}
    by_system: dict[str, dict] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        sysd = by_system.setdefault(r["system"], {"total": 0, "ok": 0, "by_status": {}})
        sysd["total"] += 1
        sysd["by_status"][r["status"]] = sysd["by_status"].get(r["status"], 0) + 1
        if r["status"] == OK:
            sysd["ok"] += 1
    ok = by_status.get(OK, 0)
    return {
        "total": total,
        "ok": ok,
        "supported_pct": round(100 * ok / total) if total else 0,
        "by_status": by_status,
        "by_system": by_system,
        "statuses": ALL_STATUSES,
    }


@app.get("/api/compat")
def compat():
    """Merge registry + status at request time (so edits show without a restart)."""
    registry = Registry.load()
    rows = merge_for_dashboard(registry, load_status())
    summary = _summarise(rows)
    # Coverage is measured against *all* English LPAs (the map's boundary set),
    # not just the rows currently in the registry.
    summary["total_lpas"] = len(_boundaries()["features"]) or summary["total"]
    summary["covered_pct"] = (
        round(100 * summary["ok"] / summary["total_lpas"]) if summary["total_lpas"] else 0
    )
    return {"rows": rows, "summary": summary}


_boundaries_cache: Optional[dict] = None
_systems_cache: Optional[dict] = None


def _boundaries() -> dict:
    global _boundaries_cache
    if _boundaries_cache is None:
        path = DATA_DIR / "lpa_boundaries.json"
        _boundaries_cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() \
            else {"viewBox": "0 0 1000 1193", "features": []}
    return _boundaries_cache


def _systems() -> dict:
    """norm -> planning system, for LPAs we don't support yet (built by tools/build_systems.py)."""
    global _systems_cache
    if _systems_cache is None:
        path = DATA_DIR / "lpa_systems.csv"
        m: dict = {}
        if path.exists():
            import csv
            with path.open(encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    m[row["norm"]] = row["system"]
        _systems_cache = m
    return _systems_cache


@app.get("/api/coverage-map")
def coverage_map():
    """LPA boundary paths coloured for the dashboard choropleth.

    green (ok) = the tool works there; red = in the registry but not ok; amber
    (known) = runs a planning system we've identified but don't support yet; grey
    (unknown) = system not catalogued.
    """
    boundaries = _boundaries()
    registry = Registry.load()
    status = load_status()
    systems = _systems()
    by_norm = {normalise_name(r.lpa_name): r for r in registry.records}

    features = []
    counts = {"ok": 0, "fail": 0, "addable": 0, "known": 0, "unknown": 0}
    known_systems: dict[str, int] = {}
    for f in boundaries["features"]:
        rec = by_norm.get(f["norm"])
        system = rec.system if rec else systems.get(f["norm"], "unknown")
        if rec is not None:
            entry = status.get(rec.key, {})
            st = entry.get("status", UNCHECKED)
            dc = entry.get("doc_count")
            cat = "ok" if st == OK else ("unknown" if st == UNCHECKED else "fail")
        elif system in SYSTEMS:
            # On a system we have a scraper for — just not catalogued yet.
            cat, st, dc = "addable", system, None
        elif system != "unknown":
            cat, st, dc = "known", system, None
            known_systems[system] = known_systems.get(system, 0) + 1
        else:
            cat, st, dc = "unknown", UNCHECKED, None
        counts[cat] += 1
        features.append({"name": f["name"], "d": f["d"], "status": st,
                         "category": cat, "system": system, "doc_count": dc})
    return {"viewBox": boundaries["viewBox"], "features": features,
            "counts": counts, "known_systems": known_systems}


class SmokeRequest(BaseModel):
    system: Optional[str] = None
    only_attention: bool = False  # re-check only rows currently needing attention
    days: int = 0                 # 0 = re-check all selected rows regardless of age


@app.post("/api/smoke-test")
def smoke_test(req: SmokeRequest):
    """Optional: run discover-only checks in the background, streaming progress.

    Mirrors tools/smoke_test.py and writes the same compat_status.json, so the
    standalone script and this button stay interchangeable.
    """
    from plangrab.engine.compat import ATTENTION
    from tools.smoke_test import check_lpa  # reuse the exact classification logic

    def gen():
        events: "queue.Queue" = queue.Queue()

        def work():
            client = make_client(_config)
            try:
                registry = Registry.load()
                status = load_status()
                rows = registry.records
                if req.system:
                    rows = [r for r in rows if r.system == req.system]
                if req.only_attention:
                    rows = [r for r in rows
                            if status.get(r.key, {}).get("status", "unchecked") in ATTENTION
                            or r.key not in status]
                if req.days:
                    rows = [r for r in rows
                            if (days_since(status.get(r.key, {}).get("last_checked")) or 9999) >= req.days]
                events.put({"type": "start", "total": len(rows)})
                for i, rec in enumerate(rows):
                    res = check_lpa(client, registry, rec, _config)
                    status[rec.key] = res
                    save_status(status)
                    events.put({"type": "row", "lpa_name": rec.lpa_name,
                                "status": res["status"], "doc_count": res["doc_count"]})
                events.put({"type": "done", "summary": _summarise(
                    merge_for_dashboard(registry, status))})
            except Exception as exc:
                events.put({"type": "error", "message": str(exc)})
            finally:
                client.close()
                events.put(None)

        threading.Thread(target=work, daemon=True).start()
        while True:
            ev = events.get()
            if ev is None:
                break
            yield json.dumps(ev) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


# --- "Last updated" date, injected into both pages -------------------------
# Reflects when the *program* was last changed (newest source-file mtime), so the
# byline updates itself on every edit/redeploy without anyone touching the HTML.
_SRC_EXT = {".py", ".html", ".css", ".js", ".toml", ".csv", ".json"}
_SKIP_DIRS = {".venv", "dist", "__pycache__", ".git", "node_modules", "scratchpad",
              ".claude", "python", "lib"}  # bundled runtime/deps excluded
_last_updated_value: Optional[str] = None


def _last_updated() -> str:
    global _last_updated_value
    if _last_updated_value is None:
        root = DATA_DIR.parent
        latest = 0.0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                if fn.startswith("_") or Path(fn).suffix not in _SRC_EXT:
                    continue
                try:
                    latest = max(latest, os.stat(os.path.join(dirpath, fn)).st_mtime)
                except OSError:
                    pass
        d = date.fromtimestamp(latest) if latest else date.today()
        _last_updated_value = f"{d.day} {d:%B %Y}"  # e.g. "28 June 2026" (Windows-safe)
    return _last_updated_value


def _render_page(filename: str) -> HTMLResponse:
    html = (STATIC_DIR / filename).read_text(encoding="utf-8")
    return HTMLResponse(html.replace("{{LAST_UPDATED}}", _last_updated()))


@app.get("/dashboard")
def dashboard():
    return _render_page("dashboard.html")


# Static UI last so /api/* routes win.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return _render_page("index.html")
