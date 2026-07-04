"""Offline tests for the harvesters' pure helpers — the search-query defaults
(a stale year list silently cost councils once), Planning Explorer href
cleaning, doc-backend classification, and the system-aware candidates writer.
Run:  python tests/test_harvest_tools.py
"""
import csv
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.harvest_examples import _default_queries, _write_candidates
from tools.harvest_northgate import DOC_SYSTEMS, _DOCLINK, _clean_href

checks = 0


def eq(got, want, label):
    global checks
    checks += 1
    assert got == want, f"{label}:\n  got : {got!r}\n  want: {want!r}"


def test_default_queries_track_today():
    y = date.today().year
    eq(_default_queries(), (str(y), str(y - 1), str(y - 2)),
       "queries are the last three years, newest first")


def test_clean_href():
    # Planning Explorer hrefs embed entity-encoded CR/LF + tabs inside values.
    raw = "StdDetails.aspx?PT=Planning Applications On-Line&amp;PARAM0=&#xD;&#xA;\t\t\t385288&amp;XSLT="
    eq(_clean_href(raw),
       "StdDetails.aspx?PT=PlanningApplicationsOn-Line&PARAM0=385288&XSLT=",
       "entities unescaped, all embedded whitespace stripped")


def test_doclink_classification():
    cases = [
        ('<a href="http://dms.x.gov.uk/PublicAccess_LIVE/SearchResult/RunThirdPartySearch?FileSystemId=EN&FOLDER1_REF=1/2/3">d</a>',
         "northgate"),
        ('<a href="http://dms.x.gov.uk/Publicaccess_LIVE/ExternalEntryPoint.aspx?SEARCH_TYPE=1">d</a>',
         "northgate"),  # Blackburn-style front door
        ('<a href="/Planning/dialog.page?org.apache.shale.dialog.DIALOG_NAME=gf&SDescription=0129/2026&viewdocs=true">d</a>',
         "civica_w2"),
    ]
    for html, want_system in cases:
        m = _DOCLINK.search(html)
        eq(bool(m), True, f"doc link matched in {html[:60]}…")
        url = m.group(1)
        system, _cls = next(v for sig, v in DOC_SYSTEMS.items() if sig.lower() in url.lower())
        eq(system, want_system, f"classified {url[:60]}…")
    # Unsupported backends must NOT match (they are reported, never added).
    for html in (
        '<a href="../../../MVM/Online/DMS/DocumentViewer.aspx?PK=1">d</a>',
        '<a href="../../northgate/documentexplorer/application/folderview.aspx?key=1">d</a>',
    ):
        eq(_DOCLINK.search(html), None, f"unsupported backend ignored: {html[:50]}…")


def test_write_candidates_is_system_aware():
    rows = [
        {"host": "publicaccess.ex1.gov.uk", "portal_base_url": "https://publicaccess.ex1.gov.uk/online-applications/",
         "example_application_url": "https://x/a", "doc_count": 3},                       # idox default
        {"host": "planning.ex2.gov.uk", "system": "northgate",
         "domains": "docs.ex2.gov.uk|planning.ex2.gov.uk",
         "portal_base_url": "http://docs.ex2.gov.uk/PublicAccess_LIVE/",
         "example_application_url": "http://docs.ex2.gov.uk/x", "doc_count": 8},
    ]
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "cand.csv"
        _write_candidates(rows, out)
        got = list(csv.DictReader(out.open(encoding="utf-8")))
    eq(got[0]["system"], "idox", "system defaults to idox")
    eq(got[0]["domains"], "publicaccess.ex1.gov.uk", "domains default to the host")
    eq(got[1]["system"], "northgate", "explicit system respected")
    eq(got[1]["domains"], "docs.ex2.gov.uk|planning.ex2.gov.uk",
       "multi-domain rows written as-is")


if __name__ == "__main__":
    test_default_queries_track_today()
    test_clean_href()
    test_doclink_classification()
    test_write_candidates_is_system_aware()
    print(f"OK — {checks} harvest-tool checks passed.")
