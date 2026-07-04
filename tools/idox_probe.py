"""Probe candidate hosts for the IDOX "Public Access" signature.

Many IDOX councils expose ``/online-applications/`` under a predictable host
(``publicaccess.<slug>.gov.uk``, ``pa.<slug>.gov.uk``, ``planning.<slug>.gov.uk``,
…). For councils still marked ``unknown`` this tries those patterns, confirms the
IDOX signature, and writes **proposals** to ``data/idox_candidates.csv`` for a
human to approve into the registry. It never edits ``lpa_registry.csv``.

    python tools/idox_probe.py --names "Adur,Arun,Mendip"     # probe specific councils
    python tools/idox_probe.py --from-seed --limit 25         # probe unknown seed rows
    python tools/idox_probe.py --domains data/known_roots.csv # probe known domain roots

Slug-guessing only catches the "tidy" domains (e.g. pa.bristol.gov.uk); councils
on irregular domains (n-somerset.gov.uk, southglos.gov.uk) still need the manual
cross-reference noted in the README (Planning Portal LPA finder / PlanIt).

Polite: sequential, short timeout, configurable delay, DNS misses skipped quietly.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from plangrab.engine.registry import Registry

CANDIDATES_OUT = Path(__file__).resolve().parent.parent / "data" / "idox_candidates.csv"
SEED_CSV = Path(__file__).resolve().parent.parent / "data" / "lpa_registry.seed.csv"

HOST_PATTERNS = [
    "publicaccess.{s}.gov.uk", "pa.{s}.gov.uk", "planning.{s}.gov.uk",
    "planningonline.{s}.gov.uk", "pa.{s}.gov.uk", "idox.{s}.gov.uk",
]
# Strong IDOX markers seen in real Public Access HTML.
_IDOX_MARKERS = ("/online-applications-skin/", "public access", "idox", "simpleSearchResults",
                 "applicationDetails.do")


def slug_candidates(name: str) -> list[str]:
    base = name.lower().replace("&", "and")
    base = re.sub(r"\b(council|lpa|city of|borough|district|county|the)\b", " ", base)
    base = re.sub(r"[^a-z0-9 ]", " ", base)
    words = base.split()
    if not words:
        return []
    joined = "".join(words)
    hyphen = "-".join(words)
    out = [joined, hyphen]
    if len(words) > 1:
        out.append(words[0])  # e.g. "bristol" from "bristol city"
    # de-dup preserving order
    seen, uniq = set(), []
    for s in out:
        if s and s not in seen:
            seen.add(s); uniq.append(s)
    return uniq


def is_idox(client: httpx.Client, base_url: str) -> str | None:
    """Return the confirmed online-applications URL if ``base_url`` is IDOX."""
    url = base_url.rstrip("/") + "/online-applications/"
    try:
        r = client.get(url)
    except (httpx.TimeoutException, httpx.TransportError):
        return None
    if r.status_code >= 400:
        return None
    text = r.text.lower()
    if any(m.lower() in text for m in _IDOX_MARKERS):
        return str(r.url)
    return None


def probe_lpa(client: httpx.Client, name: str, delay: float) -> dict | None:
    tried: set[str] = set()
    for slug in slug_candidates(name):
        for pattern in HOST_PATTERNS:
            host = pattern.format(s=slug)
            if host in tried:
                continue
            tried.add(host)
            confirmed = is_idox(client, f"https://{host}")
            if confirmed:
                return {"lpa_name": name, "host": host, "portal_base_url": confirmed}
            time.sleep(delay)
    return None


def _names_from_seed(limit: int | None) -> list[str]:
    if not SEED_CSV.exists():
        print(f"No seed file at {SEED_CSV}; run tools/seed_registry.py first.", file=sys.stderr)
        return []
    names = []
    with SEED_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("system") or "").strip() == "unknown":
                names.append(row["lpa_name"])
    return names[:limit] if limit else names


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Probe candidate hosts for the IDOX signature.")
    p.add_argument("--names", help="comma-separated council names to probe")
    p.add_argument("--from-seed", action="store_true", help="probe unknown rows in the seed file")
    p.add_argument("--domains", help="CSV of lpa_name,domain_root to probe directly")
    p.add_argument("--limit", type=int, help="cap how many councils to probe")
    p.add_argument("--delay", type=float, default=0.5, help="seconds between probes (default 0.5)")
    p.add_argument("--out", default=str(CANDIDATES_OUT))
    args = p.parse_args(argv)

    names: list[str] = []
    domain_roots: dict[str, str] = {}
    if args.names:
        names = [n.strip() for n in args.names.split(",") if n.strip()]
    elif args.from_seed:
        names = _names_from_seed(args.limit)
    elif args.domains:
        with open(args.domains, newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) >= 2:
                    domain_roots[row[0].strip()] = row[1].strip()
        names = list(domain_roots)
    else:
        p.error("give --names, --from-seed, or --domains")
    if args.limit:
        names = names[:args.limit]

    print(f"Probing {len(names)} council(s)…")
    found: list[dict] = []
    with httpx.Client(follow_redirects=True, timeout=6,
                      headers={"User-Agent": "PlanGrab/0.1 (IDOX discovery probe)"}) as client:
        for name in names:
            if name in domain_roots:  # probe the known root directly
                confirmed = is_idox(client, f"https://{domain_roots[name]}")
                hit = {"lpa_name": name, "host": domain_roots[name],
                       "portal_base_url": confirmed} if confirmed else None
            else:
                hit = probe_lpa(client, name, args.delay)
            if hit:
                hit["checked"] = date.today().isoformat()
                found.append(hit)
                print(f"  ✓ {name}: {hit['host']}")
            time.sleep(args.delay)

    if found:
        out = Path(args.out)
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["lpa_name", "host", "portal_base_url", "checked"])
            w.writeheader()
            w.writerows(found)
        print(f"\n{len(found)} IDOX candidate(s) -> {out}")
        print("Review, find one example documents URL each, and add as system=idox rows.")
    else:
        print("\nNo IDOX candidates confirmed for that set.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
