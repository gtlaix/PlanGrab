"""Batch download — resolve and download many application references in one run.

Reuses the single-application engine once per reference
(``resolve_reference`` -> ``discover`` -> ``download_all``), into a subfolder per
application, so **one bad reference never aborts the rest**. A single httpx
client is shared across the whole batch (polite + efficient — the "reuse the
context, don't spawn one per reference" rule).

All resolution stays httpx-only (no headless browser), so batch works for every
reference-capable system — IDOX, Northgate/NEC, Civica — with no extra
dependency. The per-council robustness (form-POST search, page-echo verify,
``Ref. No`` disambiguation) already lives in each scraper's
:meth:`resolve_reference`; batch just drives it in a loop.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import httpx

from .base import ReferenceLookupError
from .config import Config
from .download import download_all, make_client, user_agent_for
from .naming import sanitise
from .registry import get_scraper

ProgressFn = Callable[[dict], None]


@dataclass
class BatchItem:
    """Outcome for one reference in a batch.

    status: ``ok`` (documents downloaded), ``no_documents`` (resolved but the
    application has no documents), ``not_found`` (reference didn't resolve
    unambiguously), or ``failed`` (an error during discovery/download).
    """
    reference: str
    status: str
    url: Optional[str] = None
    folder: Optional[str] = None
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    message: str = ""


def _clean_references(references: Iterable[str]) -> list[str]:
    """Trim, drop blanks, and de-duplicate (case-insensitively) preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in references:
        ref = (raw or "").strip()
        if ref and ref.lower() not in seen:
            seen.add(ref.lower())
            out.append(ref)
    return out


def _emit(progress: Optional[ProgressFn], event: dict) -> None:
    if progress:
        progress(event)


def _subdir_for(parent: Path, reference: str, taken: set[str]) -> Path:
    """One subfolder per application, named from the reference (``/`` -> ``-``).

    Deterministic per reference so a resumed batch reuses the same folder (and
    ``download_all`` skips files already there). Only disambiguated if two
    *distinct* references sanitise to the same name within this batch.
    """
    base = sanitise(reference.replace("/", "-")) or "application"
    name, n = base, 2
    while name.lower() in taken:
        name, n = f"{base} ({n})", n + 1
    taken.add(name.lower())
    return parent / name


def download_batch(
    references: Iterable[str],
    council_base_url: str,
    parent_folder: str | Path,
    config: Config,
    *,
    client: Optional[httpx.Client] = None,
    progress: Optional[ProgressFn] = None,
) -> list[BatchItem]:
    """Resolve + download every reference for one council into ``parent_folder``.

    Each application lands in its own subfolder with its own ``manifest.csv``; a
    combined ``batch_manifest.csv`` is written at the top level. Emits progress
    events (``batch-start``, per-reference ``item-start`` / ``file`` /
    ``item-done``) and returns a :class:`BatchItem` per reference. Never raises
    for a single bad reference — that reference gets a ``not_found``/``failed``
    status and the batch continues.
    """
    refs = _clean_references(references)
    parent = Path(parent_folder).expanduser()
    parent.mkdir(parents=True, exist_ok=True)

    # One scraper (bound to the council) and one client for the whole batch. The
    # UA follows the council's system, e.g. a browser-like UA for Northgate WAFs.
    scraper = get_scraper(council_base_url, config.lpa_registry)
    own_client = client is None
    client = client or make_client(config, user_agent_for(scraper, config))

    _emit(progress, {"type": "batch-start", "total": len(refs)})
    items: list[BatchItem] = []
    taken_dirs: set[str] = set()
    try:
        for i, ref in enumerate(refs, start=1):
            _emit(progress, {"type": "item-start", "index": i, "total": len(refs),
                             "reference": ref})
            item = _download_one_reference(
                scraper, ref, i, parent, config, client, taken_dirs, progress)
            items.append(item)
            _emit(progress, {"type": "item-done", "index": i, "reference": ref,
                             "status": item.status, "url": item.url,
                             "folder": item.folder, "downloaded": item.downloaded,
                             "skipped": item.skipped, "failed": item.failed,
                             "error": item.message or None})
    finally:
        if own_client:
            client.close()

    _write_batch_manifest(parent, items)
    _emit(progress, {"type": "done", "summary": _summary(items, parent)})
    return items


def _download_one_reference(scraper, reference, index, parent, config, client,
                            taken_dirs, progress) -> BatchItem:
    try:
        url = scraper.resolve_reference(client, reference)
    except ReferenceLookupError as exc:
        return BatchItem(reference, "not_found", message=str(exc))
    except Exception as exc:  # network/portal error during resolution
        return BatchItem(reference, "failed", message=str(exc))

    try:
        docs = scraper.discover(client, url)
        if not docs:
            return BatchItem(reference, "no_documents", url=url,
                             message="Resolved, but the application has no documents.")
        subdir = _subdir_for(parent, reference, taken_dirs)

        def file_progress(event: dict) -> None:
            # Tag per-file events with which batch item they belong to.
            _emit(progress, {**event, "item_index": index, "reference": reference})

        results = download_all(scraper, docs, subdir, config,
                               client=client, progress=file_progress)
        downloaded = sum(r.status == "downloaded" for r in results)
        skipped = sum(r.status == "skipped" for r in results)
        failed = sum(r.status == "failed" for r in results)
        return BatchItem(
            reference, "ok", url=url, folder=str(subdir),
            downloaded=downloaded, skipped=skipped, failed=failed,
            message="" if failed == 0 else f"{failed} file(s) failed",
        )
    except Exception as exc:
        return BatchItem(reference, "failed", url=url, message=str(exc))


def _summary(items: list[BatchItem], parent: Path) -> dict:
    return {
        "applications": len(items),
        "ok": sum(it.status == "ok" for it in items),
        "no_documents": sum(it.status == "no_documents" for it in items),
        "not_found": sum(it.status == "not_found" for it in items),
        "failed": sum(it.status == "failed" for it in items),
        "downloaded": sum(it.downloaded for it in items),
        "folder": str(parent.resolve()),
        "manifest": str(parent / "batch_manifest.csv"),
    }


def _write_batch_manifest(parent: Path, items: list[BatchItem]) -> None:
    """One row per reference, summarising the batch (alongside each app's own
    manifest.csv). Best-effort — never let a manifest write abort the run."""
    try:
        with (parent / "batch_manifest.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["reference", "status", "documents_url", "folder",
                        "downloaded", "skipped", "failed", "message"])
            for it in items:
                w.writerow([it.reference, it.status, it.url or "", it.folder or "",
                            it.downloaded, it.skipped, it.failed, it.message])
    except OSError:
        pass
