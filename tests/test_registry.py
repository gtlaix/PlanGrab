"""Offline tests for registry resolution (URL -> scraper / errors) and the
per-system User-Agent logic. Run:  python tests/test_registry.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plangrab.engine import Config, user_agent_for
from plangrab.engine.compat import normalise_name
from plangrab.engine.idox import IdoxScraper
from plangrab.engine.registry import (
    LpaRecord, Registry, UnknownSystemError, UnsupportedSystemError,
)

checks = 0


def eq(got, want, label):
    global checks
    checks += 1
    assert got == want, f"{label}:\n  got : {got!r}\n  want: {want!r}"


def raises(fn, exc, label):
    global checks
    checks += 1
    try:
        fn()
    except exc:
        return
    raise AssertionError(f"{label}: expected {exc.__name__}")


REG = Registry([
    LpaRecord(lpa_name="Bristol City Council", gss_code="E06000023", system="idox",
              domains=["pa.bristol.gov.uk"]),
    LpaRecord(lpa_name="Someplace Council", gss_code="", system="swiftlg",
              domains=["planning.someplace.gov.uk"]),
])


def test_known_host_resolves():
    s = REG.scraper_for("https://pa.bristol.gov.uk/online-applications/applicationDetails.do?keyVal=X")
    eq(s.lpa_name, "Bristol City Council", "known host -> registry name")
    eq(s.system_id, "idox", "known host -> idox system")


def test_unregistered_idox_signature_still_works():
    # Not in the registry, but the URL path is the IDOX signature.
    s = REG.scraper_for("https://publicaccess.newcouncil.gov.uk/online-applications/applicationDetails.do?keyVal=Y")
    eq(isinstance(s, IdoxScraper), True, "signature fallback -> IdoxScraper")
    eq(s.lpa_name, "Newcouncil", "signature fallback -> humanised host name")


def test_unsupported_system_raises():
    raises(lambda: REG.scraper_for("https://planning.someplace.gov.uk/some/path"),
           UnsupportedSystemError, "known LPA on unsupported system")


def test_unknown_host_raises():
    raises(lambda: REG.scraper_for("https://example.com/foo"),
           UnknownSystemError, "unknown host, no signature")


def test_invalid_url_raises():
    raises(lambda: REG.scraper_for("not a url"), UnknownSystemError, "invalid url")


def test_user_agent_resolution():
    cfg = Config()  # defaults: honest UA, no per-system overrides
    idox = IdoxScraper("X", "https://x.gov.uk")           # user_agent = None
    eq(user_agent_for(idox, cfg), cfg.user_agent, "idox -> honest default UA")

    # A scraper that sets its own UA (Northgate-style) should win over the default.
    class WafScraper(IdoxScraper):
        system_id = "waf"
        user_agent = "Mozilla/5.0 browser-like"
    eq(user_agent_for(WafScraper("Y", "https://y.gov.uk"), cfg),
       "Mozilla/5.0 browser-like", "scraper default UA used when set")

    # A config [user_agents] override beats the scraper default.
    cfg2 = Config(system_user_agents={"waf": "configured-ua"})
    eq(user_agent_for(WafScraper("Z", "https://z.gov.uk"), cfg2),
       "configured-ua", "config override wins")


def test_normalise_name():
    eq(normalise_name("Bristol, City of Council"), "bristol", "normalise strips 'city of'/'council'")
    eq(normalise_name("Barking & Dagenham"), "barking and dagenham", "normalise &->and")
    eq(normalise_name("West Oxfordshire District Council"), "west oxfordshire", "normalise district")


if __name__ == "__main__":
    test_known_host_resolves()
    test_unregistered_idox_signature_still_works()
    test_unsupported_system_raises()
    test_unknown_host_raises()
    test_invalid_url_raises()
    test_user_agent_resolution()
    test_normalise_name()
    print(f"OK — {checks} registry checks passed.")
