"""Harvest a working example documents-URL for Northgate / NEC Planning Explorer
councils — the Planning-Explorer analogue of tools/harvest_examples.py (IDOX).

Per host it: finds the GeneralSearch page (trying the known path variants), runs
a date-range search for recent applications, opens each result's detail page and
follows its documents link. NEC councils pair the search with *different*
document backends, so the link is classified and verified with the matching
scraper:

* ``…/RunThirdPartySearch?…``      -> NEC Public Access docs  (system northgate)
* ``…dialog.page?…viewdocs=true``  -> Civica W2 / Comino docs (system civica_w2)

Both are supported systems, so a confirmed council is a pure registry-row add.

    python tools/harvest_northgate.py                     # data/northgate_candidate_hosts.txt
    python tools/harvest_northgate.py --hosts planning.southtyneside.info
    python tools/harvest_northgate.py --apply             # add confirmed rows + smoke-test

Run it from a normal network: many of these hosts don't resolve from a sandbox.
"""
from __future__ import annotations

import argparse
import html as htmllib
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from plangrab.engine.civica_w2 import CivicaW2Scraper
from plangrab.engine.northgate import NorthgateScraper
from plangrab.engine.registry import DATA_DIR, Registry, _humanise_host
from tools.harvest_examples import _apply, _write_candidates

HOSTS_FILE = DATA_DIR / "northgate_candidate_hosts.txt"
OUT = DATA_DIR / "registry_candidates.csv"

# Planning Explorer ships under several path skins; try until one answers.
SEARCH_PATHS = [
    "/Northgate/PlanningExplorer/GeneralSearch.aspx",
    "/Northgate/PlanningExplorerAA/GeneralSearch.aspx",
    "/Northgate/PlanningExplorer17/GeneralSearch.aspx",
    "/PlanningExplorer17/GeneralSearch.aspx",
    "/PlanningExplorer/GeneralSearch.aspx",
    "/Northgate/EnglishPlanningExplorer/generalsearch.aspx",
]

# Doc-link signature -> (system id, scraper class). ExternalEntryPoint.aspx is
# the NEC docs server's front door (Blackburn links it); it redirects to the
# RunThirdPartySearch page, so both signatures mean the same system.
DOC_SYSTEMS = {
    "RunThirdPartySearch": ("northgate", NorthgateScraper),
    "ExternalEntryPoint.aspx": ("northgate", NorthgateScraper),
    "viewdocs=true": ("civica_w2", CivicaW2Scraper),
}

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_HIDDEN = re.compile(r'<input[^>]+type="hidden"[^>]+name="([^"]+)"[^>]*value="([^"]*)"')
_DETAIL = re.compile(r'href="([^"]*StdDetails\.aspx[^"]*)"', re.I)
_DOCLINK = re.compile(
    r'href="([^"]*(?:RunThirdPartySearch|ExternalEntryPoint\.aspx|viewdocs=true)[^"]*)"', re.I)


def _clean_href(raw: str) -> str:
    """Planning Explorer hrefs embed literal CR/LF/tabs inside query values."""
    return re.sub(r"\s+", "", htmllib.unescape(raw))


def _find_search_page(client: httpx.Client, host: str):
    """Return (search_url, html) for the first Planning Explorer path that answers."""
    for scheme in ("https", "http"):
        for path in SEARCH_PATHS:
            url = f"{scheme}://{host}{path}"
            try:
                r = client.get(url)
            except (httpx.TimeoutException, httpx.TransportError):
                break  # scheme unreachable; try the other one
            if r.status_code == 200 and "dateStart" in r.text:
                return str(r.url), r.text
    return None, None


def harvest(host: str, max_apps: int = 8, delay: float = 0.4) -> dict:
    """Return {host, system, domains, portal_base_url, example_application_url,
    doc_count} (doc_count 0 => nothing usable)."""
    result = {"host": host, "system": "", "domains": "", "portal_base_url": "",
              "example_application_url": "", "doc_count": 0}
    client = httpx.Client(follow_redirects=True, timeout=25,
                          headers={"User-Agent": _UA, "Accept": "text/html",
                                   "Accept-Language": "en-GB,en"})
    try:
        search_url, page = _find_search_page(client, host)
        if not search_url:
            result["note"] = "no Planning Explorer search page found"
            return result

        # Date-range search over the last ~3 months. Date format follows the
        # server locale (Blackburn is US-configured), so try UK then US.
        start, end = date.today() - timedelta(days=92), date.today()
        detail_links: list[str] = []
        for fmt in ("%d/%m/%Y", "%m/%d/%Y"):
            hidden = dict(_HIDDEN.findall(page))
            res = client.post(search_url, data={
                **hidden, "rbGroup": "rbRange",
                "dateStart": start.strftime(fmt), "dateEnd": end.strftime(fmt),
                "csbtnSearch": "Search"},
                headers={"Referer": search_url})
            detail_links = list(dict.fromkeys(
                _clean_href(m) for m in _DETAIL.findall(res.text)))
            if detail_links:
                break
            time.sleep(delay)
        if not detail_links:
            result["note"] = f"search returned no applications (HTTP {res.status_code})"
            return result

        other_backend = ""
        for link in detail_links[:max_apps]:
            detail_url = urljoin(str(res.url), quote(link, safe=":/?&=.%"))
            try:
                dr = client.get(detail_url, headers={"Referer": str(res.url)})
            except (httpx.TimeoutException, httpx.TransportError):
                continue
            m = _DOCLINK.search(dr.text)
            if not m:
                # e.g. South Tyneside's MVM DocumentViewer (which serves no docs)
                alt = re.search(r'href="[^"]*/([A-Za-z]+Viewer\.aspx|folderview\.aspx)', dr.text, re.I)
                if alt:
                    other_backend = alt.group(1)
                time.sleep(delay)
                continue
            docs_url = urljoin(detail_url, _clean_href(m.group(1)))
            system, scraper_cls = next(
                (v for sig, v in DOC_SYSTEMS.items() if sig.lower() in docs_url.lower()))
            try:
                # Resolve entry-point redirects so the stored example URL is the
                # canonical documents page (ExternalEntryPoint -> RunThirdPartySearch).
                landed = client.get(docs_url, headers={"Referer": detail_url})
                if landed.status_code == 200:
                    docs_url = str(landed.url)
                docs = scraper_cls(_humanise_host(host), docs_url).discover(client, docs_url)
            except Exception:
                time.sleep(delay)
                continue
            if docs:
                p = urlparse(docs_url)
                root = p.path.lstrip("/").split("/")[0]
                result.update({
                    "system": system,
                    "domains": "|".join(dict.fromkeys([p.netloc.lower(), host.lower()])),
                    "portal_base_url": f"{p.scheme}://{p.netloc}/" + (f"{root}/" if root else ""),
                    "example_application_url": docs_url,
                    "doc_count": len(docs),
                })
                return result
            time.sleep(delay)
        result["note"] = (f"docs served by unsupported backend ({other_backend})"
                          if other_backend else
                          "applications found but no verifiable documents link")
        return result
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        result["note"] = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        client.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Harvest Northgate/NEC example documents URLs.")
    p.add_argument("--hosts", help="comma-separated Planning Explorer hostnames")
    p.add_argument("--hosts-file", default=str(HOSTS_FILE),
                   help=f"file with one hostname per line (default {HOSTS_FILE.name})")
    p.add_argument("--out", default=str(OUT))
    p.add_argument("--delay", type=float, default=0.5, help="seconds between councils")
    p.add_argument("--apply", action="store_true",
                   help="add confirmed councils straight to the registry, fix their "
                        "names + GSS codes, and smoke-test them")
    args = p.parse_args(argv)

    if args.hosts:
        hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]
    else:
        hosts = []
        for line in Path(args.hosts_file).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                hosts.append(line)

    known = {d.lower() for r in Registry.load().records for d in r.domains}
    todo = [h for h in hosts if h.lower() not in known]
    if len(todo) < len(hosts):
        print(f"({len(hosts) - len(todo)} host(s) already in the registry — skipped)")

    print(f"Harvesting {len(todo)} Northgate/NEC host(s)…")
    rows = []
    for host in todo:
        r = harvest(host)
        flag = (f"{r['doc_count']} docs via {r['system']}" if r["doc_count"]
                else f"— ({r.get('note', 'no docs')})")
        print(f"  {'OK ' if r['doc_count'] else 'skip'} {host}: {flag}")
        if r["doc_count"]:
            rows.append(r)
        time.sleep(args.delay)

    if not rows:
        print("\nNo usable example URLs found for that set.")
        return 0
    if args.apply:
        _apply(rows)
    else:
        _write_candidates(rows, Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
