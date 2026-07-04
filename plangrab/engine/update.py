"""Self-updating council registry.

On startup the local app quietly fetches the latest registry data from the
project repo, so users gain newly-added councils without re-downloading the
bundle. Strictly best-effort: offline, blocked, or serving garbage all mean
"keep the shipped files" — a failed update must never break a working app.

Controlled by ``[registry_update]`` in config.toml (enabled by default).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import threading
from pathlib import Path
from typing import Callable, Optional

from .config import Config
from .registry import DATA_DIR

log = logging.getLogger("plangrab")

# Small, frequently-changing data files. Boundaries (~400 KB, effectively
# static) are deliberately excluded.
REFRESH_FILES = ("lpa_registry.csv", "compat_status.json", "lpa_systems.csv")

_REQUIRED_REGISTRY_COLS = {"lpa_name", "system", "domains", "example_application_url"}


def _valid(name: str, text: str) -> bool:
    """Cheap sanity checks so a CDN error page can never clobber real data."""
    try:
        if name.endswith(".json"):
            return isinstance(json.loads(text), dict)
        rows = list(csv.DictReader(io.StringIO(text)))
        if name == "lpa_registry.csv":
            return bool(rows) and _REQUIRED_REGISTRY_COLS <= set(rows[0].keys())
        return bool(rows)  # lpa_systems.csv
    except Exception:
        return False


def refresh_registry(config: Config, fetch: Optional[Callable[[str], Optional[str]]] = None,
                     data_dir: Path = DATA_DIR) -> list[str]:
    """Fetch REFRESH_FILES from ``config.registry_update_url``; atomically
    replace any that changed and validate. Returns the filenames updated."""
    if not config.registry_update:
        return []
    if fetch is None:
        import httpx

        from .download import _tls_verify

        def fetch(url: str) -> Optional[str]:
            try:
                r = httpx.get(url, timeout=8, follow_redirects=True,
                              headers={"User-Agent": config.user_agent},
                              verify=_tls_verify(config))
                return r.text if r.status_code == 200 else None
            except Exception:
                return None

    updated = []
    base = config.registry_update_url.rstrip("/")
    for name in REFRESH_FILES:
        text = fetch(f"{base}/{name}")
        if text is None or not _valid(name, text):
            continue
        # Compare and write BYTES: the CSVs contain CRLF line endings, which
        # read_text() would normalise (breaking the comparison) and write_text()
        # would mangle to CR-CRLF on Windows.
        raw = text.encode("utf-8")
        target = data_dir / name
        if target.exists() and target.read_bytes() == raw:
            continue
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(target)
        updated.append(name)
    if updated:
        log.info("Registry updated from %s: %s", base, ", ".join(updated))
    return updated


def start_background_refresh(config: Config) -> None:
    """Fire-and-forget refresh so startup is never delayed by the network."""
    threading.Thread(target=refresh_registry, args=(config,),
                     name="registry-refresh", daemon=True).start()
