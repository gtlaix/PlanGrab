"""Download discovered documents to disk: session, retries, naming, manifest.

Design goals: stream large files (never hold them in memory), be polite (delay +
honest UA), be resilient (per-file try/except — one bad document never aborts the
run), and be resumable (skip files already on disk; write a manifest).
"""
from __future__ import annotations

import csv
import logging
import mimetypes
import re
import time
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import unquote, urlparse

import httpx

from .base import Scraper
from .config import Config
from .models import DocMeta, FetchResult
from .naming import dedupe, render_filename, sanitise

log = logging.getLogger("plangrab")

ProgressFn = Callable[[dict], None]

# Content-Type -> extension overrides where mimetypes is absent/ugly.
_CT_EXT = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/tiff": ".tif",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}


def _tls_verify(config: Config):
    """What to pass as httpx's ``verify=``.

    Corporate networks often intercept TLS and re-sign it with a company CA that
    Python's bundled certifi store doesn't know — every https request then fails
    with CERTIFICATE_VERIFY_FAILED (the ``_ssl.c:…`` error). ``truststore`` makes
    Python use the *operating system's* certificate store (where IT installs that
    CA), which fixes it transparently. Falls back to certifi where truststore
    isn't available (needs Python 3.10+). ``network.tls_verify = false`` in
    config.toml is the last-resort escape hatch.
    """
    if not config.tls_verify:
        return False
    try:
        import ssl
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # ImportError, or truststore unsupported on this platform
        return True


def make_client(config: Config, user_agent: Optional[str] = None) -> httpx.Client:
    """A configured, redirect-following session.

    ``user_agent`` overrides the engine default (use :func:`user_agent_for` to
    resolve the right one for a given scraper/system).
    """
    return httpx.Client(
        headers={"User-Agent": user_agent or config.user_agent},
        follow_redirects=True,
        timeout=config.timeout,
        verify=_tls_verify(config),
    )


def user_agent_for(scraper: Scraper, config: Config) -> str:
    """Resolve the UA for a scraper: config override -> scraper default -> global.

    Lets a WAF-fronted system (e.g. Northgate) present a browser-like UA while
    IDOX keeps the honest one, all overridable via config's [user_agents] table.
    """
    return (
        config.system_user_agents.get(scraper.system_id)
        or scraper.user_agent
        or config.user_agent
    )


def download_all(
    scraper: Scraper,
    docs: Iterable[DocMeta],
    out_dir: str | Path,
    config: Config,
    client: Optional[httpx.Client] = None,
    progress: Optional[ProgressFn] = None,
) -> list[FetchResult]:
    """Download every doc into ``out_dir``. Returns a result per document."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    docs = list(docs)
    own_client = client is None
    client = client or make_client(config)
    taken: set[str] = {p.name.lower() for p in out.iterdir() if p.is_file()}

    results: list[FetchResult] = []
    try:
        for i, doc in enumerate(docs):
            if i > 0:
                time.sleep(config.request_delay)
            result = _download_one(scraper, doc, out, config, client, taken)
            results.append(result)
            if progress:
                progress({
                    "type": "file",
                    "index": doc.index,
                    "total": doc.total,
                    "title": doc.title,
                    "filename": result.filename,
                    "status": result.status,
                    "error": result.error,
                })
    finally:
        if own_client:
            client.close()

    _write_manifest(out, results)
    return results


def _download_one(
    scraper: Scraper,
    doc: DocMeta,
    out: Path,
    config: Config,
    client: httpx.Client,
    taken: set[str],
) -> FetchResult:
    try:
        url = scraper.resolve_download(client, doc)

        # Skip-existing check using a best-guess extension, so an already-present
        # file is not re-downloaded on a resumed run.
        guess_ext = _ext_from_url(url) or _ext_from_hint(doc.file_hint) or ".bin"
        tentative = render_filename(doc, guess_ext, config.naming_template, config.date_format)
        if tentative.lower() in taken or (out / tentative).exists():
            log.info("skip (exists): %s", tentative)
            return FetchResult(doc, tentative, "skipped")

        resp = _get_with_retries(client, url, config)
        ext = (
            _ext_from_content_disposition(resp.headers.get("content-disposition"))
            or _ext_from_url(url)
            or _ext_from_content_type(resp.headers.get("content-type"))
            or _ext_from_hint(doc.file_hint)
            or ".bin"
        )
        filename = dedupe(
            render_filename(doc, ext, config.naming_template, config.date_format),
            taken,
        )
        target = out / filename
        part = target.with_suffix(target.suffix + ".part")

        written = 0
        try:
            try:
                with part.open("wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        fh.write(chunk)
                        written += len(chunk)
            finally:
                resp.close()
            part.replace(target)
        except BaseException:
            part.unlink(missing_ok=True)  # don't leave a half-written .part behind
            raise

        log.info("downloaded %s (%d bytes)", filename, written)
        return FetchResult(doc, filename, "downloaded", bytes_written=written)

    except Exception as exc:  # never let one document abort the batch
        log.warning("FAILED doc %s (%s): %s", doc.index, doc.title, exc)
        name = sanitise(doc.title or f"doc-{doc.index}")
        return FetchResult(doc, name, "failed", error=str(exc))


def _get_with_retries(client: httpx.Client, url: str, config: Config) -> httpx.Response:
    """Streaming GET with exponential back-off on transient failures."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, config.max_retries + 1):
        try:
            resp = client.send(client.build_request("GET", url), stream=True)
            if resp.status_code in _TRANSIENT_STATUS:
                resp.close()
                raise httpx.HTTPStatusError(
                    f"transient {resp.status_code}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return resp
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            if attempt < config.max_retries:
                backoff = 0.5 * (2 ** (attempt - 1))
                log.info("retry %d/%d after %.1fs: %s", attempt, config.max_retries, backoff, exc)
                time.sleep(backoff)
    raise last_exc  # type: ignore[misc]


# -- extension derivation ------------------------------------------------

def _ext_from_content_disposition(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    m = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", value, re.I)
    if m:
        return _suffix(unquote(m.group(1).strip().strip('"')))
    return None


def _ext_from_url(url: str) -> Optional[str]:
    return _suffix(urlparse(url).path)


def _ext_from_content_type(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    ct = value.split(";")[0].strip().lower()
    if ct in _CT_EXT:
        return _CT_EXT[ct]
    return mimetypes.guess_extension(ct) if ct else None


def _ext_from_hint(hint: Optional[str]) -> Optional[str]:
    return _suffix(hint or "")


def _suffix(path: str) -> Optional[str]:
    suffix = Path(path).suffix.lower()
    # Guard against query-string junk and absurdly long "extensions".
    if suffix and 1 < len(suffix) <= 6 and re.fullmatch(r"\.[a-z0-9]+", suffix):
        return suffix
    return None


# -- manifest ------------------------------------------------------------

def _write_manifest(out: Path, results: list[FetchResult]) -> None:
    path = out / "manifest.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "index", "total", "title", "plan_number", "date",
            "doc_type", "source_url", "filename", "status", "error",
        ])
        for r in results:
            d = r.doc
            writer.writerow([
                d.index, d.total, d.title, d.plan_number or "",
                d.date.isoformat() if d.date else "",
                d.doc_type or "", d.source_url, r.filename, r.status, r.error or "",
            ])
