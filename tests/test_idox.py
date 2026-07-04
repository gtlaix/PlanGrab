"""Offline tests for IdoxScraper.discover — column mapping, metadata extraction,
the doc-type title fallback, and the /Disclaimer interstitial handling.

Uses hand-built fixtures (tests/fixtures/*.html) + a fake httpx client, so no
network. Run:  python tests/test_idox.py
"""
import sys
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plangrab.engine.idox import IdoxScraper

FIX = Path(__file__).resolve().parent / "fixtures"
checks = 0


def eq(got, want, label):
    global checks
    checks += 1
    assert got == want, f"{label}:\n  got : {got!r}\n  want: {want!r}"


class FakeResponse:
    def __init__(self, text, url, status_code=200):
        self.text = text
        self.url = url            # str — IdoxScraper._fetch does `"/Disclaimer" in str(resp.url)`
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


class FakeClient:
    """Returns queued GET responses in order; records POSTs."""
    def __init__(self, get_responses):
        self._gets = list(get_responses)
        self.posts = []

    def get(self, url, **kw):
        return self._gets.pop(0)

    def post(self, url, **kw):
        self.posts.append(url)
        return FakeResponse("", url)


def _fixture(name):
    return (FIX / name).read_text(encoding="utf-8")


PAGE_URL = "https://pa.example.gov.uk/online-applications/applicationDetails.do?activeTab=documents&keyVal=ABC123"


def test_5col():
    client = FakeClient([FakeResponse(_fixture("idox_5col.html"), PAGE_URL)])
    docs = IdoxScraper("Example", "https://pa.example.gov.uk").discover(client, PAGE_URL)
    eq(len(docs), 2, "5col: doc count")
    eq((docs[0].index, docs[0].total), (1, 2), "5col: index/total")
    eq(docs[0].title, "OFFICER DELEGATED REPORT", "5col: title from Description")
    eq(docs[0].doc_type, "Report", "5col: doc_type")
    eq(docs[0].date, date(2025, 12, 23), "5col: parsed date")
    eq(docs[0].doc_id, "8616807", "5col: doc_id from checkbox")
    eq(docs[0].source_url.endswith("OFFICER_DELEGATED_REPORT-8616807.pdf"), True, "5col: file url")
    eq(docs[0].source_url.startswith("https://pa.example.gov.uk/online-applications/files/"),
       True, "5col: absolute file url")
    eq(docs[1].title, "PHOTOGRAPH", "5col: row2 title")
    eq(docs[1].source_url.endswith(".jpeg"), True, "5col: original (non-pdf) extension preserved")


def test_6col_measure_and_blank_description():
    client = FakeClient([FakeResponse(_fixture("idox_6col.html"), PAGE_URL)])
    docs = IdoxScraper("Example", "https://pa.example.gov.uk").discover(client, PAGE_URL)
    eq(len(docs), 2, "6col: doc count (Measure column handled)")
    eq(docs[0].title, "DISMISSED", "6col: title maps past the Measure column")
    eq(docs[0].doc_type, "Appeal Decision", "6col: doc_type past Measure column")
    eq(docs[0].date, date(2026, 2, 13), "6col: date")
    # Row 2 has a blank Description -> title falls back to Document Type.
    eq(docs[1].title, "Appeal Questionnaire", "6col: blank description falls back to doc_type")
    eq(docs[1].source_url.endswith(".docx"), True, "6col: docx extension")


def test_disclaimer_interstitial():
    # First GET lands on /Disclaimer; scraper should POST Accept then re-GET docs.
    disclaimer = FakeResponse(_fixture("idox_disclaimer.html"),
                              "https://pa.example.gov.uk/Disclaimer?returnUrl=%2Fonline-applications%2F")
    docs_page = FakeResponse(_fixture("idox_5col.html"), PAGE_URL)
    client = FakeClient([disclaimer, docs_page])
    docs = IdoxScraper("Example", "https://pa.example.gov.uk").discover(client, PAGE_URL)
    eq(len(docs), 2, "disclaimer: docs found after accepting")
    eq(len(client.posts), 1, "disclaimer: Accept was POSTed once")
    eq("/Disclaimer/Accept" in client.posts[0], True, "disclaimer: posted to the Accept endpoint")


def test_normalise_url_forces_documents_tab():
    # A summary-tab URL should be rewritten to the documents tab.
    summary = "https://pa.example.gov.uk/online-applications/applicationDetails.do?activeTab=summary&keyVal=ABC123"
    out = IdoxScraper._normalise_url(summary)
    eq(parse_qs(urlparse(out).query)["activeTab"], ["documents"], "normalise: activeTab=documents")
    eq("keyVal=ABC123" in out, True, "normalise: keyVal preserved")


def test_missing_table_raises():
    client = FakeClient([FakeResponse("<html><body>no table here</body></html>", PAGE_URL)])
    try:
        IdoxScraper("Example", "https://pa.example.gov.uk").discover(client, PAGE_URL)
        raise AssertionError("expected LookupError for a page with no documents table")
    except LookupError:
        global checks
        checks += 1


if __name__ == "__main__":
    test_5col()
    test_6col_measure_and_blank_description()
    test_disclaimer_interstitial()
    test_normalise_url_forces_documents_tab()
    test_missing_table_raises()
    print(f"OK — {checks} IDOX checks passed.")
