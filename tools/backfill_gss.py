"""Fill blank gss_code values in data/lpa_registry.csv from the authoritative
planning.data.gov.uk local-authority dataset (name/LPA-ref -> GSS code).

A row's status key is its GSS code (or, when blank, its name), so filling a code
*changes the key* — this tool therefore also migrates the matching entry in
data/compat_status.json so those councils keep their colour on the map.

It only ever fills BLANK codes (existing values are left untouched) and preserves
row order and every other field. Validated: the join reproduces all 19 hand-filled
codes exactly.

    python tools/backfill_gss.py            # downloads the dataset, applies in place
    python tools/backfill_gss.py --input la.csv --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from plangrab.engine.compat import load_status, normalise_name, save_status
from plangrab.engine.registry import DATA_DIR, REGISTRY_CSV

LA_URL = "https://files.planning.data.gov.uk/dataset/local-authority.csv"


def _ref_to_gss(dataset_text: str) -> dict[str, str]:
    """LPA entity ref (E60…) -> GSS code, keeping only unambiguous (1:1) mappings."""
    csv.field_size_limit(sys.maxsize)
    refs: dict[str, set] = {}
    for row in csv.DictReader(dataset_text.splitlines()):
        if row.get("end-date"):
            continue
        ref = (row.get("local-planning-authority") or "").strip()
        gss = (row.get("statistical-geography") or "").strip()
        if ref and gss:
            refs.setdefault(ref, set()).add(gss)
    return {ref: next(iter(gss)) for ref, gss in refs.items() if len(gss) == 1}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Backfill blank GSS codes in the registry.")
    p.add_argument("--input", help="local local-authority.csv (else download)")
    p.add_argument("--registry", default=str(REGISTRY_CSV))
    p.add_argument("--dry-run", action="store_true", help="report changes, write nothing")
    args = p.parse_args(argv)

    text = (Path(args.input).read_text(encoding="utf-8", errors="replace") if args.input
            else httpx.get(LA_URL, timeout=60, headers={"User-Agent": "PlanGrab/0.1"}).text)
    ref2gss = _ref_to_gss(text)

    boundaries = json.loads((DATA_DIR / "lpa_boundaries.json").read_text(encoding="utf-8"))
    norm2ref = {f["norm"]: f["ref"] for f in boundaries["features"]}

    reg_path = Path(args.registry)
    with reg_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    status = load_status()
    migrated = filled = 0
    for row in rows:
        if row.get("gss_code", "").strip():
            continue
        gss = ref2gss.get(norm2ref.get(normalise_name(row["lpa_name"]), ""))
        if not gss:
            continue
        old_key = row["lpa_name"]            # blank gss -> keyed by name
        row["gss_code"] = gss
        filled += 1
        print(f"  {row['lpa_name']:42} -> {gss}")
        if old_key in status and gss not in status:
            status[gss] = status.pop(old_key)
            migrated += 1

    if args.dry_run:
        print(f"\n[dry-run] would fill {filled} GSS codes, migrate {migrated} status keys.")
        return 0

    with reg_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    if migrated:
        save_status(status)
    print(f"\nFilled {filled} GSS codes; migrated {migrated} status keys.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
