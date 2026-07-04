"""Classify every England LPA by its planning-portal system.

Joins the canonical England LPA list (from the coverage-map boundaries, itself
derived from planning.data.gov.uk) to the UKPlanning project's authoritative
``scraper_list.csv`` (authority -> planning system), and writes
``data/lpa_systems.csv`` (norm, lpa_name, system). The dashboard map uses this to
colour councils we don't yet support by the system they actually run.

    python tools/build_systems.py                 # downloads scraper_list.csv
    python tools/build_systems.py --scraper-list /path/to/scraper_list.csv

Caveat: the UKPlanning list is ~2017-era, so councils reorganised since (several
unitaries) may not match and stay unclassified — that's honest, not an error.
The smoke test, not this file, is the source of truth for what actually works.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from plangrab.engine.compat import normalise_name
from plangrab.engine.registry import DATA_DIR

SCRAPER_LIST_URL = "https://raw.githubusercontent.com/aspeakman/UKPlanning/master/scraper_list.csv"
BOUNDARIES = DATA_DIR / "lpa_boundaries.json"
OUT = DATA_DIR / "lpa_systems.csv"


def tight(name: str) -> str:
    """Join key tolerant of spaces and 'and' ("Barking and Dagenham" == "BarkingDagenham")."""
    return "".join(w for w in normalise_name(name).split() if w != "and")


def to_system(scraper_type: str) -> str:
    """Map a UKPlanning scraper_type to our system id."""
    s = scraper_type.strip().lower()
    if s.startswith("idox"):
        return "idox"
    if s == "planningexplorer":
        return "northgate"
    return s or "unknown"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Classify England LPAs by planning system.")
    p.add_argument("--scraper-list", help="local scraper_list.csv (else download)")
    p.add_argument("--boundaries", default=str(BOUNDARIES))
    p.add_argument("--out", default=str(OUT))
    args = p.parse_args(argv)

    if args.scraper_list:
        text = Path(args.scraper_list).read_text(encoding="utf-8", errors="replace")
    else:
        print(f"Downloading {SCRAPER_LIST_URL} …")
        text = httpx.get(SCRAPER_LIST_URL, timeout=30,
                         headers={"User-Agent": "PlanGrab/0.1"}).text

    sysmap: dict[str, str] = {}
    for row in csv.DictReader(text.splitlines()):
        if row.get("disabled", "").strip().lower() == "false":
            sysmap[tight(row["scraper"])] = to_system(row["scraper_type"])

    england = json.loads(Path(args.boundaries).read_text(encoding="utf-8"))["features"]
    rows, counts = [], {}
    for feat in england:
        system = sysmap.get(tight(feat["name"]), "unknown")
        rows.append({"norm": feat["norm"], "lpa_name": feat["name"], "system": system})
        counts[system] = counts.get(system, 0) + 1

    rows.sort(key=lambda r: r["lpa_name"])
    with Path(args.out).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["norm", "lpa_name", "system"])
        w.writeheader()
        w.writerows(rows)

    classified = sum(v for k, v in counts.items() if k != "unknown")
    print(f"Classified {classified}/{len(england)} England LPAs -> {args.out}")
    for sysname, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {sysname:18} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
