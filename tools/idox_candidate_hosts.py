"""Generate candidate hosts for England IDOX councils not yet in the registry.

Reads the system classification (``data/lpa_systems.csv``, built by
``tools/build_systems.py``), takes every LPA classified ``idox`` that isn't already
in ``lpa_registry.csv``, and writes likely hostnames to
``data/idox_candidate_hosts.txt`` — ready to feed to the harvester:

    python tools/build_systems.py            # classify (once)
    python tools/idox_candidate_hosts.py     # -> data/idox_candidate_hosts.txt
    python tools/harvest_examples.py --hosts-file data/idox_candidate_hosts.txt

Slug-guessing catches the tidy domains (publicaccess.<council>.gov.uk); councils on
irregular hosts won't be guessed and just need one application URL pasted in.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plangrab.engine.compat import normalise_name
from plangrab.engine.registry import DATA_DIR, Registry
from tools.idox_probe import slug_candidates

SYSTEMS_CSV = DATA_DIR / "lpa_systems.csv"
OUT = DATA_DIR / "idox_candidate_hosts.txt"
# The most common IDOX host shapes; kept short so the harvested file stays runnable.
PATTERNS = ["publicaccess.{s}.gov.uk", "planning.{s}.gov.uk", "pa.{s}.gov.uk"]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Generate IDOX candidate hosts to harvest.")
    p.add_argument("--systems", default=str(SYSTEMS_CSV))
    p.add_argument("--out", default=str(OUT))
    args = p.parse_args(argv)

    systems_path = Path(args.systems)
    if not systems_path.exists():
        print(f"No {systems_path}; run tools/build_systems.py first.", file=sys.stderr)
        return 1

    already = {normalise_name(r.lpa_name) for r in Registry.load().records}
    lines: list[str] = []
    seen: set[str] = set()
    n_councils = 0
    with systems_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["system"] != "idox" or row["norm"] in already:
                continue
            n_councils += 1
            lines.append(f"# {row['lpa_name']}")
            # Use the first (primary) slug for all patterns + the hyphenated variant
            # for publicaccess, which together cover most real IDOX hosts.
            slugs = slug_candidates(row["lpa_name"])
            primary = slugs[0] if slugs else ""
            hyphen = next((s for s in slugs if "-" in s), "")
            for host in [pat.format(s=primary) for pat in PATTERNS] + (
                    [f"publicaccess.{hyphen}.gov.uk"] if hyphen else []):
                if host not in seen:
                    seen.add(host)
                    lines.append(host)

    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{n_councils} addable IDOX councils -> {len(seen)} candidate hosts -> {args.out}")
    print("Next: python tools/harvest_examples.py --hosts-file " + args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
