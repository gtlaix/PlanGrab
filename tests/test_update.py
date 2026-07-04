"""Offline tests for the registry self-update — validation guards (a CDN error
page must never clobber real data), atomic replace, unchanged-file skip, and
the enabled=false opt-out. Run:  python tests/test_update.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plangrab.engine.config import Config
from plangrab.engine.update import REFRESH_FILES, _valid, refresh_registry

checks = 0


def eq(got, want, label):
    global checks
    checks += 1
    assert got == want, f"{label}:\n  got : {got!r}\n  want: {want!r}"


GOOD_REG = ("lpa_name,gss_code,system,domains,portal_base_url,example_application_url,notes\n"
            "Bristol,E06000023,idox,pa.bristol.gov.uk,https://x/,https://x/app,ok\n")
GOOD_STATUS = json.dumps({"E06000023": {"status": "ok"}})
GOOD_SYSTEMS = "norm,lpa_name,system\nbristol,Bristol,idox\n"


def test_validation_guards():
    eq(_valid("lpa_registry.csv", GOOD_REG), True, "real registry accepted")
    eq(_valid("lpa_registry.csv", "<html>404 Not Found</html>"), False,
       "error page rejected as registry")
    eq(_valid("lpa_registry.csv", "a,b\n1,2\n"), False, "wrong columns rejected")
    eq(_valid("compat_status.json", GOOD_STATUS), True, "real status accepted")
    eq(_valid("compat_status.json", "<html></html>"), False, "html rejected as json")
    eq(_valid("compat_status.json", "[1,2]"), False, "non-dict json rejected")
    eq(_valid("lpa_systems.csv", GOOD_SYSTEMS), True, "systems csv accepted")
    eq(_valid("lpa_systems.csv", ""), False, "empty file rejected")


def test_refresh_updates_and_skips():
    payload = {"lpa_registry.csv": GOOD_REG, "compat_status.json": GOOD_STATUS,
               "lpa_systems.csv": GOOD_SYSTEMS}
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "lpa_registry.csv").write_text("stale", encoding="utf-8")
        (d / "compat_status.json").write_text(GOOD_STATUS, encoding="utf-8")  # already current
        cfg = Config()

        def fake_fetch(url):
            return payload[url.rsplit("/", 1)[1]]

        updated = refresh_registry(cfg, fetch=fake_fetch, data_dir=d)
        eq(sorted(updated), ["lpa_registry.csv", "lpa_systems.csv"],
           "changed + missing files updated; identical file skipped")
        eq((d / "lpa_registry.csv").read_text(encoding="utf-8"), GOOD_REG,
           "registry content replaced")
        eq(list(d.glob("*.tmp")), [], "no temp files left behind")


def test_refresh_never_clobbers_on_bad_fetch():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        for name in REFRESH_FILES:
            (d / name).write_text("KEEP", encoding="utf-8")
        updated = refresh_registry(Config(), fetch=lambda url: None, data_dir=d)
        eq(updated, [], "offline -> nothing updated")
        updated = refresh_registry(Config(), fetch=lambda url: "<html>captive portal</html>",
                                   data_dir=d)
        eq(updated, [], "garbage responses -> nothing updated")
        for name in REFRESH_FILES:
            eq((d / name).read_text(encoding="utf-8"), "KEEP", f"{name} untouched")


def test_crlf_idempotent():
    # The real CSVs have CRLF line endings; a text-mode compare normalises them
    # away and re-writes every run (and corrupts to CR-CRLF on Windows). The
    # updater must be byte-exact: same content twice -> second run is a no-op.
    crlf_reg = GOOD_REG.replace("\n", "\r\n")
    payload = {"lpa_registry.csv": crlf_reg, "compat_status.json": GOOD_STATUS,
               "lpa_systems.csv": GOOD_SYSTEMS}
    fake_fetch = lambda url: payload[url.rsplit("/", 1)[1]]
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        first = refresh_registry(Config(), fetch=fake_fetch, data_dir=d)
        eq(len(first), 3, "first run writes all files")
        eq((d / "lpa_registry.csv").read_bytes(), crlf_reg.encode(), "CRLF preserved byte-exactly")
        second = refresh_registry(Config(), fetch=fake_fetch, data_dir=d)
        eq(second, [], "second run is a no-op (byte-exact comparison)")


def test_opt_out():
    called = []
    updated = refresh_registry(Config(registry_update=False),
                               fetch=lambda url: called.append(url), data_dir=Path("/nonexistent"))
    eq(updated, [], "disabled -> no updates")
    eq(called, [], "disabled -> no network calls")


if __name__ == "__main__":
    test_validation_guards()
    test_refresh_updates_and_skips()
    test_refresh_never_clobbers_on_bad_fetch()
    test_crlf_idempotent()
    test_opt_out()
    print(f"OK — {checks} registry-update checks passed.")
