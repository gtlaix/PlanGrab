"""Pick the right :class:`Scraper` for a URL — data-driven.

Guiding principle: **"system" is code; "LPA" is data.**

* A *system* (idox, northgate, …) is a :class:`Scraper` subclass — behaviour,
  registered in :data:`SYSTEMS` below.
* An *LPA* is a row in ``data/lpa_registry.csv`` pointing at a system. Adding a
  council on an already-supported system needs **zero code** — just a new row.

The registry CSV is human-owned and version-controlled; automation (the smoke
test) writes status to ``data/compat_status.json`` instead and must never touch
the CSV.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .base import Scraper
from .civica_w2 import CivicaW2Scraper
from .idox import IdoxScraper

# Optional systems: imported lazily-tolerantly so a missing module never breaks
# the whole registry while a new scraper is being added.
try:
    from .northgate import NorthgateScraper  # noqa: F401
    _NORTHGATE = NorthgateScraper
except Exception:  # pragma: no cover - northgate not built yet
    _NORTHGATE = None

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
REGISTRY_CSV = DATA_DIR / "lpa_registry.csv"

# system id -> Scraper class. Adding one entry here lights up every LPA whose
# registry row names that system. This is the high-leverage extension point.
SYSTEMS: dict[str, type[Scraper]] = {"idox": IdoxScraper}
if _NORTHGATE is not None:
    SYSTEMS["northgate"] = _NORTHGATE
SYSTEMS["civica_w2"] = CivicaW2Scraper

# Path signatures used to *guess* a system for a host that isn't in the registry,
# so pasting a URL for an un-catalogued council still works where we can tell.
SIGNATURES: list[tuple[str, str]] = [
    ("/online-applications/", "idox"),
    ("/Northgate/PlanningExplorer/", "northgate"),
    ("/PublicAccess_", "northgate"),  # NEC/Northgate document server (the docs URL users paste)
    ("dialog.page", "civica_w2"),     # Civica W2/Comino docs page (Shale dialog)
    ("/StreamDocPage/", "civica_w2"),
]


class UnknownSystemError(Exception):
    """The URL matches no registry domain and no known system signature."""


class UnsupportedSystemError(Exception):
    """The LPA is known, but its system has no scraper registered yet."""


@dataclass
class LpaRecord:
    lpa_name: str
    gss_code: str = ""
    system: str = "unknown"
    domains: list[str] = field(default_factory=list)
    portal_base_url: str = ""
    example_application_url: str = ""
    notes: str = ""

    @property
    def key(self) -> str:
        """Stable status key: GSS code if present, else the name."""
        return self.gss_code or self.lpa_name


class Registry:
    """Loaded view of ``lpa_registry.csv`` with host lookup + scraper selection."""

    def __init__(self, records: list[LpaRecord]):
        self.records = records
        self._by_host: dict[str, LpaRecord] = {}
        for rec in records:
            for host in rec.domains:
                self._by_host[host.lower()] = rec

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Registry":
        path = Path(path) if path else REGISTRY_CSV
        records: list[LpaRecord] = []
        if path.exists():
            with path.open(newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    records.append(LpaRecord(
                        lpa_name=(row.get("lpa_name") or "").strip(),
                        gss_code=(row.get("gss_code") or "").strip(),
                        system=(row.get("system") or "unknown").strip().lower(),
                        domains=[d.strip() for d in (row.get("domains") or "").split("|") if d.strip()],
                        portal_base_url=(row.get("portal_base_url") or "").strip(),
                        example_application_url=(row.get("example_application_url") or "").strip(),
                        notes=(row.get("notes") or "").strip(),
                    ))
        return cls(records)

    def match_host(self, url: str) -> LpaRecord | None:
        return self._by_host.get(urlparse(url).netloc.lower())

    def scraper_for(self, url: str) -> Scraper:
        """Return a ready scraper for ``url``.

        Order: (1) host in registry → use its system; (2) host unknown but URL
        matches a known signature → handle it anyway (so pasting an un-catalogued
        IDOX council still works), naming it from the host; (3) otherwise raise.
        """
        parts = urlparse(url)
        if not parts.scheme or not parts.netloc:
            raise UnknownSystemError(f"Not a valid URL: {url!r}")
        host = parts.netloc.lower()
        base_url = f"{parts.scheme}://{parts.netloc}"

        rec = self._by_host.get(host)
        if rec is not None:
            scraper_cls = SYSTEMS.get(rec.system)
            if scraper_cls is None:
                raise UnsupportedSystemError(
                    f"{rec.lpa_name} runs on '{rec.system}', which has no scraper yet. "
                    f"Add a {rec.system.title()}Scraper and register it in SYSTEMS."
                )
            return scraper_cls(lpa_name=rec.lpa_name, base_url=base_url)

        # Host not in the registry: fall back to signature detection.
        for signature, system in SIGNATURES:
            if signature in parts.path and system in SYSTEMS:
                return SYSTEMS[system](lpa_name=_humanise_host(host), base_url=base_url)

        guessed = next((s for sig, s in SIGNATURES if sig in parts.path), None)
        hint = (
            f" This looks like a '{guessed}' site not yet in the registry — "
            f"add a row for '{host}' to data/lpa_registry.csv."
            if guessed else ""
        )
        raise UnknownSystemError(
            f"Don't recognise '{host}'. It's not in the LPA registry and its URL "
            f"matches no supported system signature.{hint}"
        )


# -- module-level convenience (back-compatible with existing callers) --------

_default: Registry | None = None


def default_registry() -> Registry:
    global _default
    if _default is None:
        _default = Registry.load()
    return _default


def get_scraper(url: str, registry: Registry | object | None = None) -> Scraper:
    """Return a scraper for ``url``. ``registry`` may be a :class:`Registry`;
    anything else (including the old config dict) falls back to the default."""
    if not isinstance(registry, Registry):
        registry = default_registry()
    return registry.scraper_for(url)


def _humanise_host(host: str) -> str:
    label = host.split(".")[1] if host.count(".") >= 2 else host.split(".")[0]
    return label.replace("-", " ").title()
