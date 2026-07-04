"""Offline tests for NorthgateScraper.discover — parsing the embedded JSON
document model, the ViewDocument URL, date parsing, and the title extension
de-duplication. Run:  python tests/test_northgate.py
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plangrab.engine.northgate import NorthgateScraper

FIX = Path(__file__).resolve().parent / "fixtures"
PAGE_URL = ("https://docs.example.gov.uk/PublicAccess_LIVE/SearchResult/"
            "RunThirdPartySearch?FileSystemId=PL&FOLDER1_REF=EX.24/1388")
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


def test_discover_parses_json_model():
    html = (FIX / "northgate_documents.html").read_text(encoding="utf-8")
    client = FakeClient(FakeResponse(html, PAGE_URL))
    docs = NorthgateScraper("Example", "https://docs.example.gov.uk").discover(client, PAGE_URL)

    eq(len(docs), 2, "doc count from JSON Rows")
    eq((docs[0].index, docs[0].total), (1, 2), "index/total")
    eq(docs[0].doc_id, "907B10393E1B4A45AB23E304501B57AD", "doc_id from Guid")
    eq(docs[0].doc_type, "Appeal - Other Documents", "doc_type from Doc_Type")
    eq(docs[0].date, date(2026, 9, 6), "date parsed from DD/MM/YYYY HH:MM:SS")
    # Title keeps the description but drops the embedded file extension…
    eq(docs[0].title, "Appellant's Final Comments: Habitat Condition Assessments",
       "title with embedded .xlsx stripped")
    # …while file_hint keeps it so the extension can still be derived.
    eq(docs[0].file_hint.endswith(".xlsx"), True, "file_hint keeps original extension")
    eq(docs[0].source_url,
       "https://docs.example.gov.uk/PublicAccess_Live/Document/ViewDocument?id=907B10393E1B4A45AB23E304501B57AD",
       "ViewDocument download URL from page template + Guid")
    eq(docs[1].title, "Application Form (redacted)", "row2 title ext stripped")
    eq(docs[1].date, date(2025, 1, 2), "row2 date")


def test_no_rows_raises():
    client = FakeClient(FakeResponse("<html><body>no model here</body></html>", PAGE_URL))
    try:
        NorthgateScraper("Example", "https://docs.example.gov.uk").discover(client, PAGE_URL)
        raise AssertionError("expected LookupError when no Rows present")
    except LookupError:
        global checks
        checks += 1


def test_strip_ext_guard():
    # Only real document extensions are stripped; version-like suffixes are kept.
    eq(NorthgateScraper._strip_ext("Plan rev2.1"), "Plan rev2.1", "non-extension suffix kept")
    eq(NorthgateScraper._strip_ext("Site Plan.PDF"), "Site Plan", "uppercase extension stripped")


def test_date_format_detection():
    # NEC servers are locale-configured: Runnymede is DD/MM, Blackburn is MM/DD.
    detect = NorthgateScraper._detect_date_format
    eq(detect(["06/18/2026 00:00:00", "05/22/2026 00:00:00"]), "%m/%d/%Y",
       "second component >12 proves month-first (Blackburn)")
    eq(detect(["18/06/2026 10:00:00"]), "%d/%m/%Y",
       "first component >12 proves day-first (Runnymede)")
    eq(detect(["06/09/2026 16:26:11", "02/01/2025 09:00:00"]), "%d/%m/%Y",
       "all-ambiguous page defaults to UK day-first")
    eq(detect([None, "", "garbage"]), "%d/%m/%Y", "unparseable values ignored")
    # …and _parse_date honours the detected format.
    eq(NorthgateScraper._parse_date("06/18/2026 00:00:00", "%m/%d/%Y"),
       date(2026, 6, 18), "US-format date parsed with detected format")


if __name__ == "__main__":
    test_discover_parses_json_model()
    test_no_rows_raises()
    test_strip_ext_guard()
    test_date_format_detection()
    print(f"OK — {checks} Northgate checks passed.")
