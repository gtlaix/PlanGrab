"""Offline tests for CivicaW2Scraper.discover — parsing the W2/Comino documents
table, doc-number extraction, verbose-date parsing, and the title/description
fallback. Run:  python tests/test_civica_w2.py
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plangrab.engine.civica_w2 import CivicaW2Scraper

FIX = Path(__file__).resolve().parent / "fixtures"
PAGE_URL = ("https://planning.example.gov.uk/Planning/dialog.page?"
            "org.apache.shale.dialog.DIALOG_NAME=gfplanningsearch"
            "&Param=lg.Planning&SDescription=0129/2026&viewdocs=true")
checks = 0


def eq(got, want, label):
    global checks
    checks += 1
    assert got == want, f"{label}:\n  got : {got!r}\n  want: {want!r}"


class FakeResponse:
    def __init__(self, text, url, status_code=200):
        self.text, self.url, self.status_code = text, url, status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


class FakeClient:
    def __init__(self, response):
        self._response = response

    def get(self, url, **kw):
        return self._response


def test_discover_parses_table():
    html = (FIX / "civica_w2_documents.html").read_text(encoding="utf-8")
    client = FakeClient(FakeResponse(html, PAGE_URL))
    docs = CivicaW2Scraper("Example", "https://planning.example.gov.uk").discover(client, PAGE_URL)

    eq(len(docs), 2, "doc count from table rows")
    eq((docs[0].index, docs[0].total), (1, 2), "index/total")
    eq(docs[0].doc_id, "5005535", "doc_id from DocNo query param")
    # Document Title blank -> Description becomes the title (and doc_type).
    eq(docs[0].title, "Plan - Floor & Elevation(s)", "title falls back to Description")
    eq(docs[0].doc_type, "Plan - Floor & Elevation(s)", "doc_type from Description")
    eq(docs[0].date, date(2026, 6, 11), "verbose 'Thursday, June 11, 2026' parsed")
    eq(docs[0].source_url,
       "https://planning.example.gov.uk/Planning/StreamDocPage/obj.pdf?DocNo=5005535&PDF=true&content=obj.pdf",
       "absolute StreamDocPage download URL")
    eq(docs[0].file_hint, "5005535.pdf", "file_hint is always a PDF rendition")
    # Row 2 has a real Document Title -> it wins over Description.
    eq(docs[1].title, "Site plan as amended", "Document Title preferred when present")
    eq(docs[1].doc_type, "Plan - Location/Site/Block", "row2 doc_type")
    eq(docs[1].date, date(2026, 1, 5), "row2 date")


def test_no_rows_raises():
    client = FakeClient(FakeResponse("<html><body>nothing here</body></html>", PAGE_URL))
    try:
        CivicaW2Scraper("Example", "https://planning.example.gov.uk").discover(client, PAGE_URL)
        raise AssertionError("expected LookupError when no StreamDocPage links present")
    except LookupError:
        global checks
        checks += 1


def test_parse_date_variants():
    eq(CivicaW2Scraper._parse_date("Thursday, June 11, 2026"), date(2026, 6, 11),
       "weekday-prefixed US date")
    eq(CivicaW2Scraper._parse_date("June 11, 2026"), date(2026, 6, 11), "no weekday")
    eq(CivicaW2Scraper._parse_date("11/06/2026"), date(2026, 6, 11), "DD/MM/YYYY fallback")
    eq(CivicaW2Scraper._parse_date(""), None, "empty -> None")
    eq(CivicaW2Scraper._parse_date("not a date"), None, "garbage -> None")


if __name__ == "__main__":
    test_discover_parses_table()
    test_no_rows_raises()
    test_parse_date_variants()
    print(f"OK — {checks} Civica W2 checks passed.")
