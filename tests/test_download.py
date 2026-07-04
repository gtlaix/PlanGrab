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
from plangrab.engine.models import DocMeta, FetchResult

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


if __name__ == "__main__":
    test_ext_from_content_disposition()
    test_ext_from_url()
    test_ext_from_content_type()
    test_ext_from_hint_and_suffix_guard()
    test_manifest_writer()
    print(f"OK — {checks} download checks passed.")
