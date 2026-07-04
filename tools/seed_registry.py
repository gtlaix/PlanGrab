"""Seed the LPA registry from the government's canonical LPA dataset.

Pulls https://files.planning.data.gov.uk/dataset/local-planning-authority.csv
(names + planning.data.gov.uk references for every Local Planning Authority in
England) and produces a **proposed** registry at ``data/lpa_registry.seed.csv``.

It NEVER overwrites the human-owned ``data/lpa_registry.csv``. Existing confirmed
rows are preserved (matched to the dataset by normalised name); every other LPA
is added as a ``system=unknown`` stub for a human to enrich (portal URL + system)
and then copy across. Re-runnable; the LPA list shifts as councils reorganise.

    python tools/seed_registry.py                 # download + propose
    python tools/seed_registry.py --input lpa.csv # use a local dataset copy

The dataset gives no portal URLs — mapping each council to its planning portal and
system is the labour-intensive part. The smoke test then keeps it honest.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from plangrab.engine.registry import REGISTRY_CSV, Registry

DATASET_URL = "https://files.planning.data.gov.uk/dataset/local-planning-authority.csv"
SEED_OUT = Path(__file__).resolve().parent.parent / "data" / "lpa_registry.seed.csv"
COLUMNS = ["lpa_name", "gss_code", "system", "domains",
           "portal_base_url", "example_application_url", "notes"]

# Words to drop when comparing a dataset name to a registry name.
_NOISE = re.compile(
    r"\b(lpa|council|city of|city|borough|district|county|metropolitan|royal|the|"
    r"unitary|authority|combined)\b", re.I)


def normalise(name: str) -> str:
    name = name.lower().replace("&", "and")
    name = _NOISE.sub(" ", name)
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def fetch_dataset(dest: Path) -> Path:
    """Stream the dataset to ``dest`` (it's ~45 MB of geometry we'll mostly skip)."""
    print(f"Downloading {DATASET_URL} …")
    with httpx.Client(follow_redirects=True, timeout=120,
                      headers={"User-Agent": "PlanGrab/0.1 (registry seeder)"}) as c:
        with c.stream("GET", DATASET_URL) as r:
            r.raise_for_status()
            with dest.open("wb") as f:
                for chunk in r.iter_bytes(65536):
                    f.write(chunk)
    return dest


def read_lpas(path: Path) -> list[dict]:
    """Active LPAs only (no end-date). Keep name + planning.data.gov.uk reference."""
    csv.field_size_limit(sys.maxsize)  # geometry columns are huge
    out = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("end-date"):
                continue
            name = (row.get("name") or "").strip()
            if not name:
                continue
            name = re.sub(r"\s+LPA$", "", name)  # "Bristol, City of LPA" -> "Bristol, City of"
            out.append({"name": name, "ref": (row.get("reference") or "").strip()})
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Seed the LPA registry from planning.data.gov.uk.")
    p.add_argument("--input", help="local dataset CSV (skip download)")
    p.add_argument("--out", default=str(SEED_OUT), help="proposed seed CSV to write")
    args = p.parse_args(argv)

    dataset = Path(args.input) if args.input else fetch_dataset(
        Path(__file__).resolve().parent.parent / "data" / "_lpa_dataset.csv")
    lpas = read_lpas(dataset)
    print(f"Dataset: {len(lpas)} active LPAs.")

    existing = Registry.load()
    by_norm = {normalise(r.lpa_name): r for r in existing.records}
    matched: set[str] = set()
    today = date.today().isoformat()

    proposed: list[dict] = []
    new_count = 0
    for lpa in lpas:
        key = normalise(lpa["name"])
        rec = by_norm.get(key)
        if rec is not None:  # preserve the confirmed row as-is
            matched.add(key)
            proposed.append({
                "lpa_name": rec.lpa_name, "gss_code": rec.gss_code, "system": rec.system,
                "domains": "|".join(rec.domains), "portal_base_url": rec.portal_base_url,
                "example_application_url": rec.example_application_url, "notes": rec.notes,
            })
        else:  # new stub for human enrichment
            new_count += 1
            proposed.append({
                "lpa_name": lpa["name"], "gss_code": "", "system": "unknown",
                "domains": "", "portal_base_url": "", "example_application_url": "",
                "notes": f"seeded {today}; pdg_ref={lpa['ref']}; needs portal URL + system",
            })

    # Don't lose hand-added registry rows the dataset didn't match (e.g. renamed).
    unmatched_existing = [r for r in existing.records if normalise(r.lpa_name) not in matched]
    for rec in unmatched_existing:
        proposed.append({
            "lpa_name": rec.lpa_name, "gss_code": rec.gss_code, "system": rec.system,
            "domains": "|".join(rec.domains), "portal_base_url": rec.portal_base_url,
            "example_application_url": rec.example_application_url,
            "notes": (rec.notes + " | not matched in latest dataset").strip(" |"),
        })

    proposed.sort(key=lambda r: r["lpa_name"])
    out = Path(args.out)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(proposed)

    print(f"\nProposed registry -> {out}")
    print(f"  preserved (confirmed): {len(matched)}")
    print(f"  new unknown stubs:     {new_count}")
    print(f"  existing unmatched:    {len(unmatched_existing)}")
    print("\nReview the seed file, then merge approved rows into data/lpa_registry.csv.")
    print("Next: enrich 'unknown' rows (portal URL + system), e.g. via tools/idox_probe.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
