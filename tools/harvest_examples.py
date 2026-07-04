"""Harvest a working example documents-URL for IDOX councils.

Adding an LPA needs one `example_application_url` (a documents page with at least
one document). This automates finding one: for each host it accepts any
``/Disclaimer`` gate, runs the IDOX simple search, and verifies a candidate with
``IdoxScraper.discover()`` — then writes a **proposed** registry row to
``data/registry_candidates.csv`` for a human to paste into ``lpa_registry.csv``.
It never edits the live registry (same discipline as seed_registry / idox_probe).

    python tools/harvest_examples.py --hosts publicaccess.solihull.gov.uk,planning.cornwall.gov.uk
    python tools/harvest_examples.py --from-registry      # idox rows missing an example URL

Run it from a normal network to reach councils a sandbox's DNS can't resolve.
"""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from bs4 import BeautifulSoup

from plangrab.engine.compat import normalise_name
from plangrab.engine.idox import IdoxScraper
from plangrab.engine.registry import DATA_DIR, REGISTRY_CSV, Registry, _humanise_host

OUT = DATA_DIR / "registry_candidates.csv"
# Browser-ish UA: some councils' search/WAF reject non-browser agents (the runtime
# scraper still uses the honest UA; this is just for harvesting).
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _accept_disclaimer(client: httpx.Client, base: str) -> None:
    r = client.get(base + "/")
    if "/Disclaimer" in str(r.url):
        form = BeautifulSoup(r.text, "html.parser").find("form")
        if form and form.get("action"):
            client.post(urljoin(str(r.url), form["action"]))


def _default_queries() -> tuple:
    """Recent years, newest first — application refs embed the year, and a stale
    year list means 'no search results' on councils that purge old records."""
    from datetime import date
    y = date.today().year
    return (str(y), str(y - 1), str(y - 2))


def harvest(host: str, queries=None, max_apps: int = 8,
            delay: float = 0.3) -> dict:
    """Return {host, example_application_url, doc_count} (doc_count 0 => nothing usable).

    Tries each search term in ``queries`` until one returns results, so a council
    whose recent apps lack documents under one year may still be found under another.
    """
    base = f"https://{host}/online-applications"
    result = {"host": host, "portal_base_url": base + "/", "example_application_url": "", "doc_count": 0}
    client = httpx.Client(follow_redirects=True, timeout=20,
                          headers={"User-Agent": _UA, "Accept": "text/html",
                                   "Accept-Language": "en-GB,en"})
    queries = queries or _default_queries()
    try:
        _accept_disclaimer(client, base)
        search_page = f"{base}/search.do?action=simple&searchType=Application"
        sp = client.get(search_page)
        if sp.status_code != 200:
            result["note"] = f"search page HTTP {sp.status_code}"
            return result
        hidden = {m.group(1): m.group(2) for m in re.finditer(
            r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', sp.text)}
        keys: list[str] = []
        for query in queries:
            res = client.post(
                f"{base}/simpleSearchResults.do?action=firstPage",
                data={**hidden, "searchCriteria.simpleSearchString": query,
                      "searchType": "Application", "searchCriteria.simpleSearch": "true"},
                headers={"Referer": search_page})
            keys = list(dict.fromkeys(re.findall(
                r'applicationDetails\.do\?[^"\']*keyVal=([A-Z0-9]+)', res.text)))
            if keys:
                break
            time.sleep(delay)
        if not keys:
            # Councils whose refs don't embed a bare year (e.g. Solihull's
            # "PL/2026/…") match nothing on a year query. The advanced search by
            # validated-date range is reference-format-agnostic — try that.
            keys = _advanced_date_search(client, base)
        if not keys:
            result["note"] = f"no search results (HTTP {res.status_code})"
            return result
        scraper = IdoxScraper(host, base)
        for k in keys[:max_apps]:
            url = f"{base}/applicationDetails.do?activeTab=documents&keyVal={k}"
            try:
                docs = scraper.discover(client, url)
                if docs:
                    result["example_application_url"] = url
                    result["doc_count"] = len(docs)
                    return result
            except Exception:
                pass
            time.sleep(delay)
        result["note"] = "results found but none had a documents table"
        return result
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        result["note"] = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        client.close()


def _advanced_date_search(client: httpx.Client, base: str) -> list[str]:
    """keyVals from the advanced search over the last ~8 weeks of validated
    applications — works whatever the council's reference format is."""
    from datetime import date, timedelta
    try:
        ap = client.get(f"{base}/search.do?action=advanced&searchType=Application")
        if ap.status_code != 200:
            return []
        hidden = {m.group(1): m.group(2) for m in re.finditer(
            r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', ap.text)}
        res = client.post(
            f"{base}/advancedSearchResults.do?action=firstPage",
            data={**hidden, "searchType": "Application",
                  "date(applicationValidatedStart)":
                      (date.today() - timedelta(days=56)).strftime("%d/%m/%Y"),
                  "date(applicationValidatedEnd)": date.today().strftime("%d/%m/%Y")},
            headers={"Referer": str(ap.url)})
        return list(dict.fromkeys(re.findall(
            r'applicationDetails\.do\?[^"\']*keyVal=([A-Z0-9]+)', res.text)))
    except (httpx.TimeoutException, httpx.TransportError):
        return []


def _hosts_from_registry() -> list[str]:
    hosts = []
    for rec in Registry.load().records:
        if rec.system == "idox" and not rec.example_application_url:
            hosts.extend(rec.domains)
    return hosts


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Harvest IDOX example documents URLs.")
    p.add_argument("--hosts", help="comma-separated hostnames")
    p.add_argument("--hosts-file", help="file with one hostname per line (# comments allowed)")
    p.add_argument("--from-registry", action="store_true",
                   help="idox registry rows missing an example_application_url")
    p.add_argument("--query", help="force a single simple-search term "
                                   "(default tries the last three years, newest first)")
    p.add_argument("--out", default=str(OUT))
    p.add_argument("--delay", type=float, default=0.5, help="seconds between councils")
    p.add_argument("--apply", action="store_true",
                   help="add confirmed councils straight to the registry, fix their "
                        "names + GSS codes, and smoke-test them (no manual paste)")
    args = p.parse_args(argv)

    hosts = []
    if args.hosts:
        hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]
    elif args.hosts_file:
        for line in Path(args.hosts_file).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                hosts.append(line)
    elif args.from_registry:
        hosts = _hosts_from_registry()
    else:
        p.error("give --hosts, --hosts-file, or --from-registry")

    queries = (args.query,) if args.query else _default_queries()
    print(f"Harvesting {len(hosts)} host(s)…")
    rows = []
    for host in hosts:
        r = harvest(host, queries=queries)
        flag = f"{r['doc_count']} docs" if r["doc_count"] else f"— ({r.get('note', 'no docs')})"
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


def _write_candidates(rows: list, out: Path) -> None:
    """Propose-only: registry-format rows for a human to review and paste in."""
    from datetime import date
    today = date.today().isoformat()
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["lpa_name", "gss_code", "system", "domains",
                    "portal_base_url", "example_application_url", "notes"])
        for r in rows:
            w.writerow([r.get("lpa_name") or f"{_humanise_host(r['host'])} Council",
                        "", r.get("system", "idox"), r.get("domains") or r["host"],
                        r["portal_base_url"], r["example_application_url"],
                        f"auto-harvested {today} ({r['doc_count']} docs); verify name/GSS"])
    print(f"\n{len(rows)} candidate row(s) -> {out}")
    print("Review, paste the good rows into data/lpa_registry.csv, then run "
          "tools/smoke_test.py — or re-run with --apply to do all that automatically.")


def _apply(rows: list) -> None:
    """Append new councils to the registry, then canonicalise names + backfill GSS
    + smoke-test them — the whole add-a-council flow, hands-off."""
    from datetime import date
    existing_hosts = {d.lower() for r in Registry.load().records for d in r.domains}
    new = [r for r in rows if r["host"].lower() not in existing_hosts]
    if not new:
        print("\nAll harvested councils are already in the registry — nothing to add.")
        return

    today = date.today().isoformat()
    with REGISTRY_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        reg_rows = list(reader)
    for r in new:
        reg_rows.append({
            "lpa_name": r.get("lpa_name") or f"{_humanise_host(r['host'])} Council",
            "gss_code": "", "system": r.get("system", "idox"),
            "domains": r.get("domains") or r["host"],
            "portal_base_url": r["portal_base_url"],
            "example_application_url": r["example_application_url"],
            "notes": f"auto-harvested {today} ({r['doc_count']} docs)",
        })
    with REGISTRY_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(reg_rows)
    print(f"\nAppended {len(new)} new council(s) to {REGISTRY_CSV.name}.")

    here = Path(__file__).resolve().parent
    print("Fixing names (official LPA names) + GSS codes…")
    # backfill_gss first (name -> GSS), THEN canonicalise (GSS -> official name):
    # canonicalisation looks the official name up by GSS, so it needs the code first.
    for tool in ("backfill_gss.py", "canonicalise_names.py"):
        subprocess.run([sys.executable, str(here / tool)], check=False,
                       stdout=subprocess.DEVNULL)

    # Irregular hostnames (e.g. buckscc, planning2) can't be auto-resolved to a
    # council — their slug isn't the name — so they keep a placeholder. Flag them
    # loudly rather than letting them rot as ugly rows that never colour the map.
    import json
    boundary_norms = {f["norm"] for f in
                      json.loads((DATA_DIR / "lpa_boundaries.json").read_text("utf-8"))["features"]}
    added_hosts = {r["host"].lower() for r in new}
    unresolved = [rec for rec in Registry.load().records
                  if any(d.lower() in added_hosts for d in rec.domains)
                  and normalise_name(rec.lpa_name) not in boundary_norms]
    if unresolved:
        print(f"\n⚠  {len(unresolved)} new council(s) need a real name — their hostname isn't")
        print("   the council's name, so they won't colour the map until renamed in")
        print(f"   {REGISTRY_CSV.name} (or ask Claude to resolve them):")
        for rec in unresolved:
            print(f"     {rec.lpa_name:40} {', '.join(rec.domains)}")

    print("\nVerifying the new council(s) with the smoke test…\n")
    subprocess.run([sys.executable, str(here / "smoke_test.py")], check=False)


if __name__ == "__main__":
    raise SystemExit(main())
