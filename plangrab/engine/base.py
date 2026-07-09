"""The Scraper interface.

Adding support for a new planning-portal *system* (different software, e.g. a
non-IDOX vendor) means writing one new ``Scraper`` subclass and registering it
in :mod:`plangrab.engine.registry`. No site logic should live anywhere else.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from .models import DocMeta


class ReferenceLookupError(Exception):
    """Could not resolve a human application reference to a documents URL.

    Raised when a portal's search returns no unambiguous match for the given
    reference (no results, or several results none of whose own ``Ref. No``
    field equals it). We deliberately fail rather than guess — a
    confidently-wrong URL is worse than an honest "not found".
    """


class Scraper(ABC):
    """Abstract base for portal scrapers.

    A scraper is responsible for two things only:

    * :meth:`discover` — given the application's documents-page URL, return the
      list of documents with metadata and a directly-downloadable ``source_url``.
    * :meth:`resolve_download` — given one :class:`DocMeta`, return the final URL
      to stream from. For most portals the discovered ``source_url`` is already a
      direct link, so the default implementation just returns it; override when a
      row points at a viewer page that must be followed to the real file.
    """

    #: Human-readable name of the portal software, e.g. "IDOX Public Access".
    system_name: str = "unknown"

    #: Short system id used to key config (matches the registry CSV ``system``).
    system_id: str = "unknown"

    #: Per-system User-Agent override. ``None`` -> use the engine default. Some
    #: portals (e.g. Northgate behind a WAF) reject non-browser UAs, so their
    #: scraper sets a browser-like UA here.
    user_agent: str | None = None

    def __init__(self, lpa_name: str, base_url: str) -> None:
        self.lpa_name = lpa_name
        self.base_url = base_url.rstrip("/")

    @abstractmethod
    def discover(self, client: httpx.Client, url: str) -> list["DocMeta"]:
        """Return all documents for the application at ``url``."""
        raise NotImplementedError

    def resolve_download(self, client: httpx.Client, doc: "DocMeta") -> str:
        """Return the final URL to stream the file from. Override if indirect."""
        return doc.source_url

    def resolve_reference(self, client: httpx.Client, reference: str) -> str:
        """Return the documents-page URL for a human application reference.

        Lets a user who only has the reference number (e.g. ``23/02163/COND``)
        reach the documents page without first finding the URL by hand. Only
        systems whose portal exposes a searchable reference implement this;
        the default reports that the system doesn't support it yet.

        Raises :class:`ReferenceLookupError` when the reference can't be
        resolved unambiguously (never guesses).
        """
        raise ReferenceLookupError(
            f"Searching by reference isn't supported for {self.system_name} yet."
        )
