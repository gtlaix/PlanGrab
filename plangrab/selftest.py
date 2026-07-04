"""PlanGrab self-test — confirm the install will actually run here.

Aimed at the locked-down work PC, where debugging is painful: it checks the
runtime, dependencies, the native folder picker, the bundled data, and — most
importantly — whether this machine can actually reach a council planning site
(corporate proxies/firewalls often block outbound traffic).

    python -m plangrab.selftest
    .\python\python.exe -m plangrab.selftest      (inside the portable bundle)

Exit code 0 if every critical check passes (a missing folder picker is only a
warning — the web UI falls back to a typed path).
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OK, WARN, FAIL = "OK", "WARN", "FAIL"


def _line(status: str, msg: str) -> None:
    print(f"  [{status:<4}] {msg}")


def run() -> int:
    print("PlanGrab self-test\n==================")
    critical_failed = False

    # 1. Python
    v = sys.version_info
    if v >= (3, 8):
        _line(OK, f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        _line(FAIL, f"Python {v.major}.{v.minor} — needs 3.8+")
        critical_failed = True

    # 2. Dependencies
    missing = [m for m in ("httpx", "bs4", "lxml", "fastapi", "uvicorn")
               if not _importable(m)]
    if missing:
        _line(FAIL, f"Missing dependencies: {', '.join(missing)} (is ./lib on the path?)")
        critical_failed = True
    else:
        _line(OK, "Dependencies present (httpx, beautifulsoup4, lxml, fastapi, uvicorn)")

    # 3. TOML parser
    if _importable("tomllib"):
        _line(OK, "TOML config parser (stdlib tomllib)")
    elif _importable("tomli"):
        _line(OK, "TOML config parser (tomli)")
    else:
        _line(FAIL, "No TOML parser (need tomllib on 3.11+ or the tomli package)")
        critical_failed = True

    # 4. Folder picker (non-critical)
    if _importable("tkinter"):
        _line(OK, "tkinter available (native folder picker will work)")
    else:
        _line(WARN, "tkinter missing — folder 'Browse…' falls back to a typed path")

    # 5/6/7. Data + engine
    try:
        from plangrab.engine import Registry
        from plangrab.engine.registry import SYSTEMS
        recs = Registry.load().records
        if recs:
            _line(OK, f"Registry loaded ({len(recs)} councils, systems: {', '.join(SYSTEMS)})")
        else:
            _line(WARN, "Registry is empty (data/lpa_registry.csv missing or blank)")
        try:
            from tools.check_registry import check
            import json
            from plangrab.engine.registry import DATA_DIR
            bpath = DATA_DIR / "lpa_boundaries.json"
            norms = {f["norm"] for f in json.loads(bpath.read_text(encoding="utf-8"))["features"]} \
                if bpath.exists() else set()
            errs = [m for sev, m in check(Registry.load(), norms) if sev == "FAIL" or sev == "ERROR"]
            _line(OK if not errs else WARN,
                  f"Registry integrity ({len(errs)} errors)")
        except Exception as exc:
            _line(WARN, f"Registry integrity check skipped: {exc}")
        sample = next((r for r in recs if r.example_application_url), None)
    except Exception as exc:
        _line(FAIL, f"Engine failed to load: {exc}")
        critical_failed = True
        sample = None

    # 8. Outbound connectivity to a real council (the make-or-break check)
    if sample is not None:
        critical_failed |= not _check_connectivity(sample)
    else:
        _line(WARN, "No example URL to test connectivity with")

    print()
    if critical_failed:
        print("Some critical checks FAILED — see above. PlanGrab may not run here.")
        return 1
    print("All checks passed — PlanGrab should run on this machine.")
    return 0


def _importable(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def _check_connectivity(sample) -> bool:
    """Try to reach one real council portal; reports proxy/firewall problems clearly."""
    import httpx
    url = sample.portal_base_url or sample.example_application_url
    try:
        r = httpx.get(url, timeout=12, follow_redirects=True,
                      headers={"User-Agent": "PlanGrab/0.1 selftest"})
        _line(OK, f"Outbound network OK — reached {sample.lpa_name} (HTTP {r.status_code})")
        return True
    except Exception as exc:
        _line(FAIL, f"Couldn't reach {sample.lpa_name} ({type(exc).__name__}). "
                    f"Check proxy/firewall — outbound HTTPS may be blocked.")
        return False


if __name__ == "__main__":
    raise SystemExit(run())
