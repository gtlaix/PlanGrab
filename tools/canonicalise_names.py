"""Rename registry councils to their official LPA names (as published on
planning.data.gov.uk, with the implied 'LPA' suffix removed) — the exact names
the coverage map uses. This makes the map and the dashboard table use identical
names, so clicking a council on the map filters the table correctly.

Safe to re-run (e.g. after adding harvested rows): it only rewrites lpa_name to
match the map boundary for the same council, and since every row is keyed by its
GSS code the saved smoke-status is unaffected.

    python tools/canonicalise_names.py            # apply
    python tools/canonicalise_names.py --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plangrab.engine.compat import normalise_name
from plangrab.engine.registry import DATA_DIR, REGISTRY_CSV


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Use official LPA names from the map boundaries.")
    p.add_argument("--registry", default=str(REGISTRY_CSV))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    boundaries = json.loads((DATA_DIR / "lpa_boundaries.json").read_text(encoding="utf-8"))
    official = {f["norm"]: f["name"] for f in boundaries["features"]}

    reg_path = Path(args.registry)
    with reg_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    renamed, unmatched = 0, []
    for row in rows:
        name = row["lpa_name"]
        off = official.get(normalise_name(name))
        if off is None:
            unmatched.append(name)
        elif off != name:
            print(f"  {name!r:48} -> {off!r}")
            row["lpa_name"] = off
            renamed += 1

    if unmatched:
        print("\nWARNING — no map boundary matched (left unchanged): " + ", ".join(unmatched))

    if args.dry_run:
        print(f"\n[dry-run] would rename {renamed} councils.")
        return 0

    with reg_path.open("w", newline="", encoding="utf-8") as f:
        # csv.writer quotes names containing commas (e.g. "Bristol, City of").
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"\nRenamed {renamed} councils to their official LPA names.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
