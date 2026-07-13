"""Offline tests for the FastAPI layer using Starlette's TestClient — no council
network is touched (only the local registry/boundaries/status files and the
error path of /api/discover). Run:  python tests/test_web.py

Note: /api/pick-folder and the live /api/discover|/api/download paths are NOT
tested here — they open a native dialog / hit council sites.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from plangrab.web.app import app

client = TestClient(app)
checks = 0


def eq(got, want, label):
    global checks
    checks += 1
    assert got == want, f"{label}:\n  got : {got!r}\n  want: {want!r}"


def ok(cond, label):
    global checks
    checks += 1
    assert cond, label


def test_pages_render_with_injected_date():
    for path in ("/", "/dashboard"):
        r = client.get(path)
        eq(r.status_code, 200, f"{path} renders")
        ok("PlanGrab" in r.text, f"{path} shows PlanGrab brand")
        ok("{{LAST_UPDATED}}" not in r.text, f"{path} date token substituted")
        ok("Developed with Claude Code by George Lewis" in r.text, f"{path} byline present")


def test_api_compat_shape():
    data = client.get("/api/compat").json()
    s = data["summary"]
    ok("rows" in data and len(data["rows"]) > 0, "compat: has rows")
    ok(s["total_lpas"] >= 300, "compat: total_lpas is all England (~308)")
    ok(0 <= s["covered_pct"] <= 100, "compat: covered_pct in range")
    ok(s["ok"] <= s["total_lpas"], "compat: ok <= total_lpas")


def test_api_coverage_map_shape():
    data = client.get("/api/coverage-map").json()
    ok(data["viewBox"].startswith("0 0 "), "map: viewBox present")
    feats = data["features"]
    ok(len(feats) >= 300, "map: ~308 boundary features")
    counts = data["counts"]
    for cat in ("ok", "fail", "addable", "known", "unknown"):
        ok(cat in counts, f"map: counts has '{cat}'")
    eq(sum(counts.values()), len(feats), "map: category counts sum to feature count")
    ok(all(set(f) >= {"name", "d", "category", "system"} for f in feats[:5]),
       "map: features carry name/path/category/system")


def test_api_discover_bad_url_is_400():
    r = client.post("/api/discover", json={"url": "https://example.com/not-a-portal"})
    eq(r.status_code, 400, "discover: unknown system -> 400")
    ok("error" in r.json(), "discover: error message returned")


def test_api_councils_shape():
    data = client.get("/api/councils").json()
    councils = data["councils"]
    ok(len(councils) > 0, "councils: non-empty")
    ok(all(set(c) >= {"name", "base_url", "system", "supports_reference"} for c in councils[:5]),
       "councils: each has name/base_url/system/supports_reference")
    names = [c["name"] for c in councils]
    eq(names, sorted(names, key=str.lower), "councils: sorted by name")
    bristol = next((c for c in councils if c["name"].startswith("Bristol")), None)
    ok(bristol is not None, "councils: Bristol present")
    ok(bristol["supports_reference"] is True, "councils: IDOX supports reference search")
    ok(bristol["base_url"].startswith("https://"), "councils: base_url is a URL")


def test_api_resolve_bad_council_is_400():
    # No network: an unknown council fails at scraper selection, before any fetch.
    r = client.post("/api/resolve",
                    json={"council": "https://example.com/nope", "reference": "23/02163/COND"})
    eq(r.status_code, 400, "resolve: unknown council -> 400")
    ok("error" in r.json(), "resolve: error message returned")


def test_api_resolve_blank_reference_is_400():
    r = client.post("/api/resolve",
                    json={"council": "https://pa.bristol.gov.uk/online-applications/", "reference": "  "})
    eq(r.status_code, 400, "resolve: blank reference -> 400")
    ok("error" in r.json(), "resolve: blank-reference error returned")


def _ndjson(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def test_api_download_batch_empty_refs_streams_error():
    # No references -> a clean error event, no network touched.
    r = client.post("/api/download-batch",
                    json={"council": "https://pa.bristol.gov.uk/online-applications/",
                          "references": [], "folder": "/tmp/pg_batch_test"})
    eq(r.status_code, 200, "batch: streaming response is 200")
    events = _ndjson(r)
    ok(any(e["type"] == "error" for e in events), "batch: empty refs -> error event")


def test_api_download_batch_bad_council_streams_error():
    # Unknown council fails at scraper selection (before any fetch) -> error event.
    r = client.post("/api/download-batch",
                    json={"council": "https://example.com/nope",
                          "references": ["23/02163/COND"], "folder": "/tmp/pg_batch_test"})
    eq(r.status_code, 200, "batch: streaming response is 200")
    events = _ndjson(r)
    ok(any(e["type"] == "error" for e in events), "batch: unknown council -> error event")


if __name__ == "__main__":
    test_pages_render_with_injected_date()
    test_api_compat_shape()
    test_api_coverage_map_shape()
    test_api_discover_bad_url_is_400()
    test_api_councils_shape()
    test_api_resolve_bad_council_is_400()
    test_api_resolve_blank_reference_is_400()
    test_api_download_batch_empty_refs_streams_error()
    test_api_download_batch_bad_council_streams_error()
    print(f"OK — {checks} web checks passed.")
