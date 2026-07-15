"""Generate the public, static PlanGrab site into ``docs/`` (GitHub Pages).

The hosted site has two pages, both built by transforming the live app's own
assets (so the design never diverges):

* ``index.html`` — the **Downloader** UI. On GitHub Pages it can't scrape LPA
  portals itself (browsers block cross-origin reads), so it drives a small local
  helper over ``http://127.0.0.1`` — downloads still run from the user's own
  machine/IP. The page's ``app.js`` auto-detects whether it is being served by
  the local helper (relative API) or from Pages (probe localhost), so the file is
  copied **verbatim** here; only the asset paths and page-nav links are rewritten.
* ``coverage.html`` — the "does my council work?" dashboard: coverage map +
  searchable table. It makes **zero requests to council portals**; its API fetches
  are swapped for baked JSON files produced by the same functions the web app serves.

    python tools/build_site.py        # -> docs/  (commit + push to publish)
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from plangrab.web.app import STATIC_DIR, _last_updated, compat, coverage_map  # noqa: E402

DOCS = ROOT / "docs"
REPO_URL = "https://github.com/gtlaix/PlanGrab"
DOWNLOAD_URL = f"{REPO_URL}/releases/latest/download/PlanGrab-win64.zip"

# Page-nav links point at absolute app routes locally ("/", "/dashboard"); on the
# static site they become sibling files. Applied to both pages.
NAV_REWRITES = {'href="/"': 'href="index.html"', 'href="/dashboard"': 'href="coverage.html"'}


def _rewrite_nav(html: str) -> str:
    for src, dst in NAV_REWRITES.items():
        html = html.replace(src, dst)
    return html


def _build_downloader() -> None:
    """docs/index.html — the Downloader UI, talking to the local helper."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("{{LAST_UPDATED}}", _last_updated())
    # Local asset paths (/static/foo) -> siblings (foo).
    html = html.replace('href="/static/', 'href="').replace('src="/static/', 'src="')
    html = _rewrite_nav(html)
    (DOCS / "index.html").write_text(html, encoding="utf-8")

    # app.js is deployment-agnostic (it auto-detects the API base) — copy as-is.
    shutil.copyfile(STATIC_DIR / "app.js", DOCS / "app.js")


def _build_coverage() -> None:
    """docs/coverage.html + baked JSON — the coverage dashboard (no portal calls)."""
    (DOCS / "compat.json").write_text(json.dumps(compat()), encoding="utf-8")
    (DOCS / "coverage-map.json").write_text(json.dumps(coverage_map()), encoding="utf-8")

    html = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    html = html.replace("{{LAST_UPDATED}}", _last_updated())
    html = html.replace("<title>PlanGrab — LPA Coverage</title>",
                        "<title>PlanGrab — LPA coverage</title>")
    # local asset paths + dashboard.js -> site.js
    html = html.replace('href="/static/', 'href="').replace('src="/static/dashboard.js"', 'src="site.js"')
    html = _rewrite_nav(html)  # Downloader tab now links to the real index.html
    # Background re-checks only make sense in the local app.
    html = html.replace('<button id="recheck" class="secondary"',
                        '<button id="recheck" class="secondary" hidden')
    html = html.replace(
        '<p class="tagline">LPAs currently compatible with PlanGrab.</p>',
        '<p class="tagline">Councils the tool currently works with — searchable below.</p>')
    (DOCS / "coverage.html").write_text(html, encoding="utf-8")

    # JS: same dashboard logic, reading baked JSON instead of the API.
    js = (STATIC_DIR / "dashboard.js").read_text(encoding="utf-8")
    js = js.replace('fetch("/api/compat")', 'fetch("compat.json")')
    js = js.replace('fetch("/api/coverage-map")', 'fetch("coverage-map.json")')
    (DOCS / "site.js").write_text(js, encoding="utf-8")


def build() -> None:
    DOCS.mkdir(exist_ok=True)

    _build_downloader()
    _build_coverage()

    # CSS: verbatim (styles.css already carries the onboarding/helper styling).
    for name in ("styles.css", "dashboard.css"):
        (DOCS / name).write_text((STATIC_DIR / name).read_text(encoding="utf-8"),
                                 encoding="utf-8")

    # GitHub Pages: serve files as-is (no Jekyll processing)
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")

    n = len(json.loads((DOCS / "compat.json").read_text())["rows"])
    print(f"docs/ built: downloader + coverage ({n} councils), generated {date.today().isoformat()}")
    print("Publish: commit + push, then enable GitHub Pages (branch: master, folder: /docs).")


if __name__ == "__main__":
    build()
