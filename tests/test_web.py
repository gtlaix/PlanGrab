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

from plangrab.web.app import app, _config

client = TestClient(app)
checks = 0
ALLOWED_ORIGIN = _config.allowed_origin


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


# --- Hosted-UI transport: liveness probe + cross-origin access -------------
# The GitHub Pages downloader is a cross-origin caller of the local helper, so it
# relies on /api/ping for discovery and on CORS + Private Network Access headers.

def test_api_ping_shape():
    data = client.get("/api/ping").json()
    eq(data["app"], "plangrab", "ping: identifies the app")
    ok(isinstance(data.get("version"), str) and data["version"], "ping: version is a non-empty string")


def test_cors_allows_pages_origin():
    r = client.get("/api/ping", headers={"Origin": ALLOWED_ORIGIN})
    eq(r.headers.get("access-control-allow-origin"), ALLOWED_ORIGIN,
       "cors: configured Pages origin is echoed back")


def test_cors_preflight_grants_private_network():
    r = client.options("/api/download", headers={
        "Origin": ALLOWED_ORIGIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
        "Access-Control-Request-Private-Network": "true",
    })
    ok(r.status_code in (200, 204), "cors: preflight succeeds")
    eq(r.headers.get("access-control-allow-origin"), ALLOWED_ORIGIN,
       "cors: preflight echoes the allowed origin")
    eq(r.headers.get("access-control-allow-private-network"), "true",
       "pna: private-network access granted on preflight")


def test_cors_blocks_unknown_origin():
    r = client.get("/api/ping", headers={"Origin": "https://evil.example.com"})
    ok(not r.headers.get("access-control-allow-origin"),
       "cors: an untrusted origin is not granted access")


def test_api_pick_folder_degrades_gracefully():
    # No display / native dialog in the test env: the picker subprocess just fails.
    # The endpoint must still return the contract (a string path — empty here so
    # the UI falls back to a typed path), never 500. Windows uses PowerShell; this
    # exercises the failure/fallback branch shared by both pickers.
    r = client.get("/api/pick-folder")
    eq(r.status_code, 200, "pick-folder: always 200 (never crashes the server)")
    data = r.json()
    ok(isinstance(data.get("path"), str), "pick-folder: returns a string path (typed-path fallback)")


def test_api_batch_download_streams():
    # Fake the engine's batch driver so no council is touched; assert the endpoint
    # streams the batch events through unchanged (batch-start … item-done … done).
    import plangrab.web.app as appmod

    def fake_batch(references, council, folder, config, *, client=None, progress=None):
        progress({"type": "batch-start", "total": len(references)})
        for i, ref in enumerate(references, start=1):
            progress({"type": "item-done", "index": i, "reference": ref, "status": "ok",
                      "url": "u", "folder": "f", "downloaded": 1, "skipped": 0,
                      "failed": 0, "error": None})
        progress({"type": "done", "summary": {"applications": len(references), "ok": len(references),
                  "no_documents": 0, "not_found": 0, "failed": 0, "downloaded": len(references),
                  "folder": folder, "manifest": "m"}})
        return []

    orig = appmod.download_batch
    appmod.download_batch = fake_batch
    try:
        r = client.post("/api/batch-download", json={
            "council": "https://x/online-applications/",
            "references": ["23/0001/A", "23/0002/B"], "folder": "/tmp/out"})
        eq(r.status_code, 200, "batch: 200")
        events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
        types = [e["type"] for e in events]
        eq(types[0], "batch-start", "batch: stream starts with batch-start")
        eq(types[-1], "done", "batch: stream ends with done")
        eq(sum(t == "item-done" for t in types), 2, "batch: one item-done per reference")
    finally:
        appmod.download_batch = orig


if __name__ == "__main__":
    test_pages_render_with_injected_date()
    test_api_compat_shape()
    test_api_coverage_map_shape()
    test_api_discover_bad_url_is_400()
    test_api_councils_shape()
    test_api_resolve_bad_council_is_400()
    test_api_resolve_blank_reference_is_400()
    test_api_ping_shape()
    test_cors_allows_pages_origin()
    test_cors_preflight_grants_private_network()
    test_cors_blocks_unknown_origin()
    test_api_pick_folder_degrades_gracefully()
    test_api_batch_download_streams()
    print(f"OK — {checks} web checks passed.")
