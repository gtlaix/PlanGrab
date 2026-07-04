"""Generate the public, static coverage site into ``docs/`` (GitHub Pages).

The hosted site is the "does my council work?" page: the coverage map, the
searchable council table, and a download link for the Windows bundle. It makes
**zero requests to council portals** — downloads always run from the user's own
machine/IP (see README: "hosting the downloader centrally gets blocked").

Rather than duplicating the dashboard, this transforms the live app's own
assets: the HTML/JS/CSS are copied from ``plangrab/web/static`` with the
app-only bits swapped out (API fetches -> baked JSON files; the Downloader
toggle -> a GitHub link; the re-run button -> hidden), and the JSON payloads
are produced by importing the same functions the web app serves.

    python tools/build_site.py        # -> docs/  (commit + push to publish)
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from plangrab.web.app import STATIC_DIR, _last_updated, compat, coverage_map  # noqa: E402

DOCS = ROOT / "docs"
REPO_URL = "https://github.com/gtlaix/PlanGrab"
DOWNLOAD_URL = f"{REPO_URL}/releases/latest/download/PlanGrab-win64.zip"

HERO = f"""
    <section class="card hero">
      <p><strong>PlanGrab</strong> bulk-downloads every document on a UK planning
      application — original files, sensibly named — from council portals that
      offer no &ldquo;download all&rdquo; button.</p>
      <p>It runs on <strong>your own computer</strong> (downloads come from your
      IP, politely), needs <strong>no installation and no admin rights</strong>:
      unzip and run.</p>
      <p class="hero-actions">
        <a class="download-btn" href="{DOWNLOAD_URL}">&#11015;&#65039; Download for Windows (~19&nbsp;MB)</a>
        <a class="repo-link" href="{REPO_URL}">Source &amp; docs on GitHub</a>
      </p>
    </section>
"""

HERO_CSS = """
/* --- hosted-site additions --- */
.hero p { margin: 0.4em 0; }
.hero-actions { display: flex; gap: 1em; align-items: center; flex-wrap: wrap; margin-top: 0.9em !important; }
.download-btn { display: inline-block; background: var(--accent, #2f7d4f); color: #fff !important;
  padding: 0.55em 1.1em; border-radius: 8px; font-weight: 600; text-decoration: none; }
.download-btn:hover { filter: brightness(1.1); }
.repo-link { opacity: 0.85; }
.brand-github { margin-left: auto; font-weight: 600; }
"""


def build() -> None:
    DOCS.mkdir(exist_ok=True)

    # --- data (same functions the local web app serves) ---
    (DOCS / "compat.json").write_text(json.dumps(compat()), encoding="utf-8")
    (DOCS / "coverage-map.json").write_text(json.dumps(coverage_map()), encoding="utf-8")

    # --- HTML: adapt the dashboard page ---
    html = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    html = html.replace("{{LAST_UPDATED}}", _last_updated())
    html = html.replace("<title>PlanGrab — LPA Coverage</title>",
                        "<title>PlanGrab — bulk-download UK planning documents</title>")
    # local asset paths
    html = html.replace('href="/static/', 'href="').replace('src="/static/dashboard.js"', 'src="site.js"')
    # the Downloader page doesn't exist online -> replace the toggle with a GitHub link
    html = re.sub(r'<div class="page-toggle.*?</div>',
                  f'<a class="brand-github" href="{REPO_URL}">GitHub &#8599;</a>',
                  html, flags=re.S)
    # background re-checks only make sense in the local app
    html = html.replace('<button id="recheck" class="secondary"',
                        '<button id="recheck" class="secondary" hidden')
    html = html.replace(
        '<p class="tagline">LPAs currently compatible with PlanGrab.</p>',
        '<p class="tagline">Councils the tool currently works with — searchable below.</p>')
    # hero with the download button, above the coverage numbers
    html = html.replace('<section id="coverage"', HERO + '\n    <section id="coverage"')
    (DOCS / "index.html").write_text(html, encoding="utf-8")

    # --- JS: same dashboard logic, reading baked JSON instead of the API ---
    js = (STATIC_DIR / "dashboard.js").read_text(encoding="utf-8")
    js = js.replace('fetch("/api/compat")', 'fetch("compat.json")')
    js = js.replace('fetch("/api/coverage-map")', 'fetch("coverage-map.json")')
    (DOCS / "site.js").write_text(js, encoding="utf-8")

    # --- CSS: verbatim + hosted-site additions ---
    for name in ("styles.css", "dashboard.css"):
        (DOCS / name).write_text((STATIC_DIR / name).read_text(encoding="utf-8"),
                                 encoding="utf-8")
    with (DOCS / "dashboard.css").open("a", encoding="utf-8") as f:
        f.write(HERO_CSS)

    # GitHub Pages: serve files as-is (no Jekyll processing)
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")

    n = len(json.loads((DOCS / "compat.json").read_text())["rows"])
    print(f"docs/ built: {n} councils, generated {date.today().isoformat()}")
    print("Publish: commit + push, then enable GitHub Pages (branch: master, folder: /docs).")


if __name__ == "__main__":
    build()
