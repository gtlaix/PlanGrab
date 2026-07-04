"""PlanGrab compatibility smoke test.

For each LPA in the registry, call ONLY the scraper's ``discover()`` against its
``example_application_url`` — list documents, download nothing — and record a
status into ``data/compat_status.json``. Never touches ``lpa_registry.csv``.

    python tools/smoke_test.py                 # check rows not seen in 14 days
    python tools/smoke_test.py --all           # re-check everything
    python tools/smoke_test.py --system idox   # only IDOX rows
    python tools/smoke_test.py --status parse_error,stale_example   # only these

Resumable: progress is written after every row, so an interrupted run loses
nothing. Polite: honest UA, configurable delay, sequential, light retries.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path

# Allow `python tools/smoke_test.py` from anywhere in the project.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from plangrab.engine import Config, Registry, make_client, user_agent_for
from plangrab.engine.compat import (
    AUTH_OR_TERMS, NETWORK_ERROR, NO_DOCUMENTS, OK, PARSE_ERROR, STALE_EXAMPLE,
    UNSUPPORTED, days_since, load_status, save_status,
)
from plangrab.engine.registry import LpaRecord, UnknownSystemError, UnsupportedSystemError

log = logging.getLogger("plangrab.smoke")

_STALE_MARKERS = ("not found", "no longer available", "cannot be found",
                  "unable to find", "invalid key", "page is not available")
_TERMS_MARKERS = ("terms of use", "i agree", "accept these terms", "disclaimer",
                  "please accept", "terms and conditions")


def _result(status: str, doc_count=None, message: str = "") -> dict:
    return {
        "status": status,
        "doc_count": doc_count,
        "last_checked": date.today().isoformat(),
        "message": message[:300],
    }


def check_lpa(client: httpx.Client, registry: Registry, rec: LpaRecord, config: Config) -> dict:
    """Run one discover() and classify the outcome into the fixed taxonomy."""
    url = rec.example_application_url
    if not url:
        return _result(STALE_EXAMPLE, message="no example_application_url in registry")

    try:
        scraper = registry.scraper_for(url)
    except (UnsupportedSystemError, UnknownSystemError) as exc:
        return _result(UNSUPPORTED, message=str(exc))

    # Present the right UA for this system (e.g. browser-like for WAF-fronted ones).
    client.headers["User-Agent"] = user_agent_for(scraper, config)

    try:
        docs = _discover_with_retries(client, scraper, url, config)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in (404, 410):
            return _result(STALE_EXAMPLE, message=f"HTTP {code}")
        if code in (401, 403):
            return _result(AUTH_OR_TERMS, message=f"HTTP {code}")
        return _result(NETWORK_ERROR, message=f"HTTP {code}")
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        return _result(NETWORK_ERROR, message=f"{type(exc).__name__}: {exc}")
    except LookupError as exc:
        # 200 but no document table: disambiguate stale vs terms vs real parse fail.
        return _classify_no_table(client, url, str(exc))
    except Exception as exc:  # unexpected structure -> regression signal
        return _result(PARSE_ERROR, message=f"{type(exc).__name__}: {exc}")

    if docs:
        return _result(OK, doc_count=len(docs))
    return _result(NO_DOCUMENTS, message="page parsed but 0 documents found")


def _discover_with_retries(client, scraper, url, config):
    last = None
    for attempt in range(1, config.max_retries + 1):
        try:
            return scraper.discover(client, url)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = exc
            if attempt < config.max_retries:
                time.sleep(0.5 * (2 ** (attempt - 1)))
    raise last


def _classify_no_table(client, url, base_msg):
    try:
        r = client.get(url)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        return _result(NETWORK_ERROR, message=f"{type(exc).__name__}: {exc}")
    if r.status_code in (404, 410):
        return _result(STALE_EXAMPLE, message=f"HTTP {r.status_code}")
    text = r.text.lower()
    if any(m in text for m in _STALE_MARKERS):
        return _result(STALE_EXAMPLE, message="page indicates application not found")
    if any(m in text for m in _TERMS_MARKERS):
        return _result(AUTH_OR_TERMS, message="terms/consent interstitial detected")
    return _result(PARSE_ERROR, message=base_msg or "document table not found")


def _should_check(rec, status, args) -> bool:
    entry = status.get(rec.key)
    if args.system and rec.system != args.system:
        return False
    if args.status:  # only rows currently in one of these statuses
        current = entry.get("status") if entry else "unchecked"
        return current in args.status
    if args.all or entry is None:
        return True
    age = days_since(entry.get("last_checked"))
    return age is None or age >= args.days


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="PlanGrab LPA compatibility smoke test.")
    p.add_argument("--all", action="store_true", help="re-check every row")
    p.add_argument("--days", type=int, default=14, help="skip rows checked within N days (default 14)")
    p.add_argument("--system", help="only rows with this system, e.g. idox")
    p.add_argument("--status", help="only rows currently in these statuses (comma-separated)")
    p.add_argument("--registry", help="path to lpa_registry.csv")
    p.add_argument("--out", help="path to compat_status.json")
    p.add_argument("--config", help="path to config.toml")
    args = p.parse_args(argv)
    args.status = [s.strip() for s in args.status.split(",")] if args.status else None

    config = Config.load(args.config)
    registry = Registry.load(args.registry)
    status = load_status(args.out)

    log_path = Path("data"); log_path.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path / "smoke_test.log", encoding="utf-8"),
                  logging.StreamHandler(sys.stderr)],
    )

    todo = [rec for rec in registry.records if _should_check(rec, status, args)]
    log.info("smoke test: %d of %d rows to check", len(todo), len(registry.records))

    client = make_client(config)
    try:
        for i, rec in enumerate(todo):
            if i > 0:
                time.sleep(config.request_delay)
            res = check_lpa(client, registry, rec, config)
            status[rec.key] = res
            save_status(status, args.out)  # incremental: survive interruption
            log.info("%-45s %-14s %s", rec.lpa_name, res["status"],
                     f"({res['doc_count']} docs)" if res["doc_count"] else res["message"])
    finally:
        client.close()

    # Summary: counts per status + coverage.
    counts: dict[str, int] = {}
    for v in status.values():
        counts[v["status"]] = counts.get(v["status"], 0) + 1
    total = len(registry.records)
    ok = counts.get(OK, 0)
    print("\n=== Smoke test summary ===")
    for st in sorted(counts):
        print(f"  {st:<16} {counts[st]}")
    print(f"  coverage: {ok}/{total} ok ({100*ok/total:.0f}%)" if total else "  (registry empty)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
