"""Guard the *live* data/lpa_registry.csv against the mistakes that creep in when
rows are pasted by hand or from the harvester (duplicate domains for one council,
unknown system ids, example URLs whose host isn't in the row's domains).

Asserts zero ERROR-severity issues; warnings (shared portals, blank GSS, cosmetic
names) are allowed. Run:  python tests/test_registry_data.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plangrab.engine.registry import DATA_DIR, Registry
from tools.check_registry import check

checks = 0


def test_live_registry_has_no_errors():
    global checks
    registry = Registry.load()
    bpath = DATA_DIR / "lpa_boundaries.json"
    norms = {f["norm"] for f in json.loads(bpath.read_text(encoding="utf-8"))["features"]} \
        if bpath.exists() else set()
    errors = [m for sev, m in check(registry, norms) if sev == "ERROR"]
    checks += 1
    assert not errors, "registry has ERROR-level issues:\n  " + "\n  ".join(errors)


if __name__ == "__main__":
    test_live_registry_has_no_errors()
    print(f"OK — {checks} registry-data check passed (live registry is clean).")
