"""Build a high-quality IDOX candidate-host list from the UKPlanning project's
scraper modules — which carry the *real* portal hostname for ~190 IDOX councils,
including the irregular domains that slug-guessing (tools/idox_candidate_hosts.py)
can never find (e.g. boppa.poole.gov.uk, isa.chiltern.gov.uk, idox.cambridge.gov.uk).

Writes the hosts not already in the registry to ``data/idox_candidate_hosts.txt``,
ready for:  python tools/harvest_examples.py --hosts-file data/idox_candidate_hosts.txt --apply

The URLs are ~2017-era so some have moved (the harvester skips dead hosts), but the
hit-rate is far higher than guessing. Non-England councils (Wales) may slip in; they
just won't colour the England map and show as a check_registry warning.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from plangrab.engine.registry import DATA_DIR, Registry

RAW = "https://raw.githubusercontent.com/aspeakman/UKPlanning/master/ukplanning"
MODULES = [
    "scrapers/dates/idox.py", "scrapers/dates/idox2.py", "scrapers/dates/idoxendexc.py",
    "scrapers/dates/idoxcrumb.py", "scrapers/reqs/idoxreq.py",
]
OUT = DATA_DIR / "idox_candidate_hosts.txt"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Real IDOX hosts from UKPlanning.")
    p.add_argument("--out", default=str(OUT))
    args = p.parse_args(argv)

    hosts: set[str] = set()
    with httpx.Client(timeout=30, headers={"User-Agent": "PlanGrab/0.1"}) as c:
        for mod in MODULES:
            try:
                text = c.get(f"{RAW}/{mod}").text
            except Exception as exc:
                print(f"  (skipped {mod}: {exc})", file=sys.stderr)
                continue
            for m in re.finditer(r"['\"](https?://[^'\"]*online-applications[^'\"]*)['\"]", text):
                host = urlparse(m.group(1)).netloc.lower()
                if host:
                    hosts.add(host)

    already = {d.lower() for r in Registry.load().records for d in r.domains}
    new = sorted(h for h in hosts if h not in already)

    Path(args.out).write_text(
        "# Real IDOX portal hosts (from the UKPlanning project), not yet in the registry.\n"
        "# Harvest + add them all with:\n"
        "#   python tools/harvest_examples.py --hosts-file data/idox_candidate_hosts.txt --apply\n"
        + "\n".join(new) + "\n", encoding="utf-8")
    print(f"{len(hosts)} IDOX hosts found, {len(new)} not yet in the registry -> {args.out}")
    print("Next: python tools/harvest_examples.py --hosts-file " + args.out + " --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
