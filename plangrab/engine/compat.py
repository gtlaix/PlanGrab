"""Compatibility status: the fixed taxonomy + read/write of compat_status.json.

Shared by the smoke test (writer) and the dashboard (reader). The status file is
machine-owned and keyed by GSS code (or LPA name when no GSS code exists). The
human-owned ``lpa_registry.csv`` is never written here.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from .registry import DATA_DIR, Registry

# Words dropped when matching an LPA name across datasets ("Plymouth City Council"
# vs "Plymouth LPA" -> both normalise to "plymouth"). Shared by the registry
# seeder and the coverage map so their joins agree.
_NOISE = re.compile(
    r"\b(lpa|council|city of|city|borough|district|county|metropolitan|royal|the|"
    r"unitary|authority|combined)\b", re.I)


def normalise_name(name: str) -> str:
    name = (name or "").lower().replace("&", "and")
    name = _NOISE.sub(" ", name)
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    return re.sub(r"\s+", " ", name).strip()

COMPAT_JSON = DATA_DIR / "compat_status.json"

# Fixed status taxonomy (also the order shown in dashboard summaries).
OK = "ok"
NO_DOCUMENTS = "no_documents"
STALE_EXAMPLE = "stale_example"
AUTH_OR_TERMS = "auth_or_terms"
PARSE_ERROR = "parse_error"
UNSUPPORTED = "unsupported"
NETWORK_ERROR = "network_error"
UNCHECKED = "unchecked"  # never a stored value; the default for rows with no entry

ALL_STATUSES = [
    OK, NO_DOCUMENTS, STALE_EXAMPLE, AUTH_OR_TERMS,
    PARSE_ERROR, UNSUPPORTED, NETWORK_ERROR, UNCHECKED,
]
# Statuses that need a human: refresh a URL, or fix/add a scraper.
ATTENTION = {STALE_EXAMPLE, PARSE_ERROR, NO_DOCUMENTS, AUTH_OR_TERMS, UNSUPPORTED}


def load_status(path: str | Path | None = None) -> dict:
    path = Path(path) if path else COMPAT_JSON
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_status(data: dict, path: str | Path | None = None) -> None:
    path = Path(path) if path else COMPAT_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys keeps diffs stable across incremental writes.
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def days_since(iso_date: Optional[str]) -> Optional[int]:
    if not iso_date:
        return None
    try:
        d = datetime.strptime(iso_date, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (date.today() - d).days


def merge_for_dashboard(registry: Registry, status: dict) -> list[dict]:
    """One dict per registry row, with its current status merged in."""
    rows = []
    for rec in registry.records:
        st = status.get(rec.key, {})
        rows.append({
            "lpa_name": rec.lpa_name,
            "gss_code": rec.gss_code,
            "system": rec.system,
            "domains": rec.domains,
            "portal_base_url": rec.portal_base_url,
            "example_application_url": rec.example_application_url,
            "notes": rec.notes,
            "status": st.get("status", UNCHECKED),
            "doc_count": st.get("doc_count"),
            "last_checked": st.get("last_checked"),
            "message": st.get("message", ""),
        })
    return rows
