"""Lint data/lpa_registry.csv for the mistakes that creep in when rows are
pasted in by hand or from the harvester.

Two severities:
  ERROR — breaks routing or the map (duplicate domains, unknown system id, an
          example URL whose host isn't one of the row's domains).
  WARN  — cosmetic / will-not-show issues (name doesn't match a map boundary,
          duplicate council, blank example URL, malformed/blank GSS code).

    python tools/check_registry.py            # human report
    python tools/check_registry.py --errors-only   # CI/test mode (exit 1 on ERROR)

Used by tests/test_registry_data.py to keep the live registry clean.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plangrab.engine.compat import normalise_name
from plangrab.engine.registry import DATA_DIR, SYSTEMS, Registry

GSS_RE = re.compile(r"^[ENWS]\d{8}$")


def check(registry: Registry, boundary_norms: set[str]) -> list[tuple[str, str]]:
    """Return a list of (severity, message). severity in {'ERROR','WARN'}."""
    issues: list[tuple[str, str]] = []
    host_to_rows: dict[str, list[str]] = {}
    norm_counts: Counter = Counter()

    for rec in registry.records:
        norm_counts[normalise_name(rec.lpa_name)] += 1
        for host in rec.domains:
            host_to_rows.setdefault(host.lower(), []).append(rec.lpa_name)

        if rec.system not in SYSTEMS:
            issues.append(("ERROR", f"{rec.lpa_name}: system '{rec.system}' has no scraper "
                                    f"(known: {', '.join(SYSTEMS)})"))

        if not rec.example_application_url:
            issues.append(("WARN", f"{rec.lpa_name}: blank example_application_url "
                                   f"(smoke test can't verify it)"))
        else:
            ex_host = urlparse(rec.example_application_url).netloc.lower()
            if rec.domains and ex_host not in {d.lower() for d in rec.domains}:
                issues.append(("ERROR", f"{rec.lpa_name}: example URL host '{ex_host}' "
                                        f"isn't in domains {rec.domains}"))

        if normalise_name(rec.lpa_name) not in boundary_norms:
            issues.append(("WARN", f"{rec.lpa_name}: name doesn't match any England map "
                                   f"boundary — it won't colour on the map"))

        if rec.gss_code and not GSS_RE.match(rec.gss_code):
            issues.append(("WARN", f"{rec.lpa_name}: GSS code '{rec.gss_code}' looks malformed"))

    for host, names in host_to_rows.items():
        uniq = sorted(set(names))
        if len(names) > 1 and len(uniq) == 1:
            # Same council, same domain, multiple rows -> a true redundant duplicate.
            issues.append(("ERROR", f"domain '{host}' duplicated for the same council: {uniq[0]}"))
        elif len(names) > 1:
            # Different councils sharing one portal (e.g. Bromsgrove & Redditch) is
            # legitimate; just note that host lookup resolves to one name for display.
            issues.append(("WARN", f"domain '{host}' shared by {', '.join(uniq)} "
                                   f"(shared portal — host lookup shows one name)"))
    for norm, n in norm_counts.items():
        if n > 1:
            issues.append(("WARN", f"council '{norm}' appears in {n} rows (duplicate paste?)"))

    return issues


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Lint the LPA registry.")
    p.add_argument("--registry")
    p.add_argument("--errors-only", action="store_true",
                   help="print/exit on ERROR only (test mode)")
    args = p.parse_args(argv)

    registry = Registry.load(args.registry)
    bpath = DATA_DIR / "lpa_boundaries.json"
    boundary_norms = {f["norm"] for f in json.loads(bpath.read_text(encoding="utf-8"))["features"]} \
        if bpath.exists() else set()

    issues = check(registry, boundary_norms)
    errors = [m for sev, m in issues if sev == "ERROR"]
    warns = [m for sev, m in issues if sev == "WARN"]

    for m in errors:
        print(f"  ERROR  {m}")
    if not args.errors_only:
        for m in warns:
            print(f"  warn   {m}")

    print(f"\n{len(registry.records)} rows checked — {len(errors)} errors, {len(warns)} warnings.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
