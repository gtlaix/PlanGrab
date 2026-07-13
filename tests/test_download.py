"""Offline tests for the download helpers: extension derivation (which preserves
the original file format) and the manifest writer. Run: python tests/test_download.py
"""
import csv
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plangrab.engine import download as dl
from plangrab.engine.base import ReferenceLookupError
from plangrab.engine.config import Config
from plangrab.engine.models import DocMeta, FetchResult
from plangrab.engine.naming import reference_folder

checks = 0


def eq(got, want, label):
    global checks
    checks += 1
    assert got == want, f"{label}:\n  got : {got!r}\n  want: {want!r}"


def test_ext_from_content_disposition():
    eq(dl._ext_from_content_disposition('attachment; filename="plan.PDF"'), ".pdf", "CD quoted filename")
    eq(dl._ext_from_content_disposition("inline; filename*=UTF-8''report.docx"), ".docx", "CD filename* utf-8")
    eq(dl._ext_from_content_disposition(None), None, "CD none")
    eq(dl._ext_from_content_disposition("attachment"), None, "CD no filename")


def test_ext_from_url():
    eq(dl._ext_from_url("https://x/online-applications/files/H/pdf/a-b-1.pdf"), ".pdf", "url pdf")
    eq(dl._ext_from_url("https://x/files/H/photo-2.jpeg"), ".jpeg", "url jpeg")
    eq(dl._ext_from_url("https://x/files/H/doc?id=7"), None, "url no extension")


def test_ext_from_content_type():
    eq(dl._ext_from_content_type("application/pdf"), ".pdf", "ct pdf")
    eq(dl._ext_from_content_type("image/jpeg"), ".jpg", "ct jpeg")
    eq(dl._ext_from_content_type(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"), ".xlsx", "ct xlsx")
    eq(dl._ext_from_content_type("text/html; charset=utf-8"), ".html", "ct html (mimetypes)")
    eq(dl._ext_from_content_type(None), None, "ct none")


def test_ext_from_hint_and_suffix_guard():
    eq(dl._ext_from_hint("APPLICATION_FORM-8552601.pdf"), ".pdf", "hint pdf")
    eq(dl._suffix("file.superlongextension"), None, "suffix rejects absurd extension")
    eq(dl._suffix("noext"), None, "suffix none when no dot")


def test_manifest_writer():
    docs = [
        DocMeta(title="Site Plan", source_url="https://x/a.pdf", index=1, total=2,
                date=date(2025, 1, 1), doc_type="Plan", plan_number="A-1"),
        DocMeta(title="Photo", source_url="https://x/b.jpg", index=2, total=2),
    ]
    results = [
        FetchResult(docs[0], "001 of 002 - Site Plan.pdf", "downloaded", bytes_written=10),
        FetchResult(docs[1], "002 of 002 - Photo.jpg", "failed", error="boom"),
    ]
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        dl._write_manifest(out, results)
        rows = list(csv.DictReader((out / "manifest.csv").open(encoding="utf-8")))
    eq(len(rows), 2, "manifest: row count")
    eq(rows[0]["filename"], "001 of 002 - Site Plan.pdf", "manifest: filename")
    eq(rows[0]["status"], "downloaded", "manifest: status")
    eq(rows[0]["plan_number"], "A-1", "manifest: plan_number")
    eq(rows[0]["date"], "2025-01-01", "manifest: ISO date")
    eq(rows[1]["status"], "failed", "manifest: failed status")
    eq(rows[1]["error"], "boom", "manifest: error recorded")


# -- batch download (references -> per-application subfolders) -------------

def test_reference_folder_naming():
    eq(reference_folder("87/03602/L", 1, 3), "01. 87.03602.L", "folder: slashes->dots, 2-wide index")
    eq(reference_folder("23/02163/COND", 2, 3), "02. 23.02163.COND", "folder: second application")
    eq(reference_folder("X/1", 5, 120), "005. X.1", "folder: index widens to total")


class _FakeScraper:
    system_id = "idox"
    system_name = "Fake"
    user_agent = None

    def __init__(self, fail=()):
        self.lpa_name = "Fake Council"
        self._fail = set(fail)

    def resolve_reference(self, client, ref):
        if ref in self._fail:
            raise ReferenceLookupError(f"no match for {ref}")
        return f"https://x/online-applications/applicationDetails.do?activeTab=documents&keyVal=K{ref}"

    def discover(self, client, url):
        return [DocMeta(title="doc", source_url="https://x/a.pdf", index=1, total=1)]


def _fake_download_all(scraper, docs, out_dir, config, client=None, progress=None):
    """Stand-in for download_all: makes the folder, reports one download per doc."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for d in docs:
        results.append(FetchResult(d, "doc.pdf", "downloaded", bytes_written=1))
        if progress:
            progress({"type": "file", "index": d.index, "total": d.total,
                      "title": d.title, "filename": "doc.pdf",
                      "status": "downloaded", "error": None})
    return results


def test_download_references_per_folder_and_failsafe():
    events = []
    orig = dl.download_all
    dl.download_all = _fake_download_all  # exercise orchestration, not the HTTP path
    try:
        with tempfile.TemporaryDirectory() as d:
            parent = Path(d)
            scraper = _FakeScraper(fail={"99/BAD/L"})
            summaries = dl.download_references(
                scraper, ["87/03602/L", "99/BAD/L", "23/02163/COND"], parent,
                Config(request_delay=0), client=object(), progress=events.append)

            # Each application saved into its own numbered subfolder.
            eq((parent / "01. 87.03602.L").is_dir(), True, "batch: first subfolder created")
            eq((parent / "03. 23.02163.COND").is_dir(), True, "batch: third subfolder created")
            # The bad reference did NOT abort the run; it's flagged, the rest continue.
            eq([s["status"] for s in summaries], ["ok", "not_found", "ok"], "batch: fail-safe per app")
            eq(summaries[0]["downloaded"], 1, "batch: first app counted its download")
            eq(summaries[1]["folder"], "02. 99.BAD.L", "batch: failed app still names a folder")
    finally:
        dl.download_all = orig

    # Progress carried app-level framing + tagged file events.
    types = [e["type"] for e in events]
    eq(types.count("app_start"), 3, "batch: an app_start per reference")
    eq(types.count("app_done"), 3, "batch: an app_done per reference")
    first_file = next(e for e in events if e["type"] == "file")
    eq(first_file["app_index"], 1, "batch: file events tagged with their app_index")


if __name__ == "__main__":
    test_ext_from_content_disposition()
    test_ext_from_url()
    test_ext_from_content_type()
    test_ext_from_hint_and_suffix_guard()
    test_manifest_writer()
    test_reference_folder_naming()
    test_download_references_per_folder_and_failsafe()
    print(f"OK — {checks} download checks passed.")
