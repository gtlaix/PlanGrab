"""PlanGrab document engine — GUI-independent.

Public surface used by the CLI and the web layer:

    from plangrab.engine import Config, get_scraper, download_all, make_client
"""
from .base import ReferenceLookupError
from .batch import BatchItem, download_batch
from .config import Config
from .download import download_all, make_client, user_agent_for
from .models import DocMeta, FetchResult
from .registry import (
    LpaRecord,
    Registry,
    UnknownSystemError,
    UnsupportedSystemError,
    default_registry,
    get_scraper,
)

__all__ = [
    "BatchItem",
    "Config",
    "DocMeta",
    "FetchResult",
    "download_batch",
    "LpaRecord",
    "ReferenceLookupError",
    "Registry",
    "UnknownSystemError",
    "UnsupportedSystemError",
    "default_registry",
    "download_all",
    "get_scraper",
    "make_client",
    "user_agent_for",
]
