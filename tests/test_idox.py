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

from plangrab.engine.base import ReferenceLookupError
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


# -- resolve_reference (reference -> documents URL) -----------------------

BASE = "https://pa.example.gov.uk"
SEARCH_PAGE = f"{BASE}/online-applications/search.do?action=simple&searchType=Application"
_SEARCH_FORM = (
    '<html><body><form>'
    '<input type="hidden" name="_csrf" value="tok123">'
    '<input type="text" name="searchCriteria.simpleSearchString">'
    '</form></body></html>'
)


class SearchClient:
    """Queued GETs + queued POSTs; records POST bodies for assertions."""
    def __init__(self, gets, posts):
        self._gets = list(gets)
        self._posts = list(posts)
        self.posts = []

    def get(self, url, **kw):
        return self._gets.pop(0)

    def post(self, url, **kw):
        self.posts.append((url, kw))
        return self._posts.pop(0)


def _detail(ref, keyval):
    return (f"<html><head><title>{ref} | Example</title></head><body>"
            f"<p>Ref. No: {ref}</p>"
            f'<a href="/online-applications/applicationDetails.do?activeTab=summary&keyVal={keyval}">Summary</a>'
            f"</body></html>")


def test_resolve_reference_disambiguates_by_ref_field():
    # Searching the permission 22/05126/LA also returns the COND that quotes it.
    # We must return the permission's keyVal, not the COND decoy.
    ref = "22/05126/LA"
    client = SearchClient(
        gets=[FakeResponse(_SEARCH_FORM, SEARCH_PAGE),
              FakeResponse(_detail(ref, "RK3S32DNMZE00"), f"{BASE}/online-applications/applicationDetails.do")],
        posts=[FakeResponse(_fixture("idox_search_results.html"), f"{BASE}/online-applications/simpleSearchResults.do")],
    )
    url = IdoxScraper("Example", BASE).resolve_reference(client, ref)
    eq("keyVal=RK3S32DNMZE00" in url, True, "resolve: picked the permission's keyVal (not the COND decoy)")
    eq("keyVal=RVIDUJDN06900" in url, False, "resolve: did NOT pick the COND decoy")
    eq(parse_qs(urlparse(url).query)["activeTab"], ["documents"], "resolve: normalised to documents tab")
    # The POST carried the reference and the form's hidden token.
    body = client.posts[0][1]["data"]
    eq(body["searchCriteria.simpleSearchString"], ref, "resolve: searched the reference")
    eq(body["_csrf"], "tok123", "resolve: replayed the form's hidden inputs")


def test_resolve_reference_matches_the_cond_when_asked():
    # The mirror case: asking for the COND itself returns the COND's keyVal.
    ref = "23/02163/COND"
    client = SearchClient(
        gets=[FakeResponse(_SEARCH_FORM, SEARCH_PAGE),
              FakeResponse(_detail(ref, "RVIDUJDN06900"), f"{BASE}/online-applications/applicationDetails.do")],
        posts=[FakeResponse(_fixture("idox_search_results.html"), f"{BASE}/online-applications/simpleSearchResults.do")],
    )
    url = IdoxScraper("Example", BASE).resolve_reference(client, ref)
    eq("keyVal=RVIDUJDN06900" in url, True, "resolve: matched the COND's own Ref. No")


def test_resolve_reference_single_match_redirect():
    # A single exact match redirects straight to the detail page (no result rows).
    ref = "89/00253/L"
    client = SearchClient(
        gets=[FakeResponse(_SEARCH_FORM, SEARCH_PAGE),
              FakeResponse(_detail(ref, "LEGACYKEY01"), f"{BASE}/online-applications/applicationDetails.do")],
        posts=[FakeResponse(_detail(ref, "LEGACYKEY01"), f"{BASE}/online-applications/applicationDetails.do")],
    )
    url = IdoxScraper("Example", BASE).resolve_reference(client, ref)
    eq("keyVal=LEGACYKEY01" in url, True, "resolve: single-match redirect keyVal")


def test_resolve_reference_no_match_raises():
    client = SearchClient(
        gets=[FakeResponse(_SEARCH_FORM, SEARCH_PAGE)],
        posts=[FakeResponse(_fixture("idox_search_results.html"), f"{BASE}/online-applications/simpleSearchResults.do")],
    )
    try:
        IdoxScraper("Example", BASE).resolve_reference(client, "99/99999/XYZ")
        raise AssertionError("expected ReferenceLookupError when nothing matches")
    except ReferenceLookupError:
        global checks
        checks += 1


def test_resolve_reference_verify_rejects_wrong_page():
    # keyVal resolves, but the verify page doesn't echo the reference -> error,
    # rather than returning a silently-wrong URL.
    ref = "22/05126/LA"
    client = SearchClient(
        gets=[FakeResponse(_SEARCH_FORM, SEARCH_PAGE),
              FakeResponse("<html><body>totally different application</body></html>",
                           f"{BASE}/online-applications/applicationDetails.do")],
        posts=[FakeResponse(_fixture("idox_search_results.html"), f"{BASE}/online-applications/simpleSearchResults.do")],
    )
    try:
        IdoxScraper("Example", BASE).resolve_reference(client, ref)
        raise AssertionError("expected ReferenceLookupError when verify page doesn't echo the ref")
    except ReferenceLookupError:
        global checks
        checks += 1


if __name__ == "__main__":
    test_5col()
    test_6col_measure_and_blank_description()
    test_disclaimer_interstitial()
    test_normalise_url_forces_documents_tab()
    test_missing_table_raises()
    test_resolve_reference_disambiguates_by_ref_field()
    test_resolve_reference_matches_the_cond_when_asked()
    test_resolve_reference_single_match_redirect()
    test_resolve_reference_no_match_raises()
    test_resolve_reference_verify_rejects_wrong_page()
    print(f"OK — {checks} IDOX checks passed.")
