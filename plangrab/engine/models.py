"""Plain data structures shared across the engine.

Kept deliberately dependency-free so every other module (scrapers, naming,
download, web layer) can import these without pulling in HTTP or parsing libs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class DocMeta:
    """Metadata for a single planning document, as discovered on a listing page.

    Positional fields (``index``/``total``) are filled in by the scraper once the
    whole list is known, so the downloader can render "007 of 142" style names.
    """

    title: str                       # human description, e.g. "Proposed Ground Floor Plan"
    source_url: str                  # absolute URL of the actual file to download
    index: int = 0                   # 1-based position in the listing
    total: int = 0                   # total number of documents in the listing
    plan_number: Optional[str] = None  # drawing/plan reference, if the site exposes one
    date: Optional[date] = None      # date published, if available
    doc_type: Optional[str] = None   # e.g. "Application Form", "Photo" (not used in name by default)
    doc_id: Optional[str] = None     # stable per-document id from the portal (dedup/debug)
    file_hint: Optional[str] = None  # original filename hint from the page, for extension fallback

    # Populated after a fetch; not part of discovery.
    extra: dict = field(default_factory=dict)


@dataclass
class FetchResult:
    """Outcome of downloading one document."""

    doc: DocMeta
    filename: str                    # final, sanitised filename written to disk
    status: str                      # "downloaded" | "skipped" | "failed"
    bytes_written: int = 0
    error: Optional[str] = None
