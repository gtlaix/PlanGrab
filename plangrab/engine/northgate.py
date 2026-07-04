"""Scraper for the NEC / Northgate "Public Access" document server.

Northgate Planning Explorer (`/Northgate/PlanningExplorer/`) links out to a
separate NEC document system for the actual files — e.g. Runnymede's
``docs.runnymede.gov.uk/PublicAccess_LIVE/SearchResult/RunThirdPartySearch?FileSystemId=PL&FOLDER1_REF=<ref>``.
That documents page is the URL the user pastes, and it is fully server-rendered:
it embeds a JSON model ``{"Columns":[…], "Rows":[…]}`` listing every document.

Verified June 2026 against Runnymede (vendor confirmed as NEC, necsws.com — the
former Northgate Public Services):

* Each row: ``Guid`` (document id), ``Doc_Type``, ``Date_Received``
  (``DD/MM/YYYY HH:MM:SS`` — but locale-dependent: Blackburn serves US
  ``MM/DD/YYYY``, so the format is detected per page), ``Doc_Ref2``
  (description + original filename).
* The file is served by ``…/Document/ViewDocument?id=<Guid>`` in its original
  format (no captcha, no JS needed — the JSON is in the initial HTML).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import urljoin

import httpx

from .base import Scraper
from .models import DocMeta

# These hosts can sit behind a WAF that rejects non-browser agents.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_DEFAULT_VIEW = "/PublicAccess_Live/Document/ViewDocument"


class NorthgateScraper(Scraper):
    system_name = "Northgate / NEC Public Access"
    system_id = "northgate"
    user_agent = _BROWSER_UA

    def discover(self, client: httpx.Client, url: str) -> list[DocMeta]:
        resp = client.get(url)
        resp.raise_for_status()

        rows = self._extract_rows(resp.text)
        if not rows:
            raise LookupError(
                "No document rows found on the NEC/Northgate documents page. Either "
                "the application has no documents, or this isn't a "
                "'…/PublicAccess…/SearchResult/RunThirdPartySearch' documents URL. "
                "URL: " + url
            )

        # The page tells us the download endpoint; fall back to the known default.
        view = self._view_url(resp.text)

        # NEC servers format Date_Received per their locale config: Runnymede is
        # UK DD/MM/YYYY, Blackburn is US MM/DD/YYYY. Detect once per page.
        date_fmt = self._detect_date_format(r.get("Date_Received") for r in rows)

        docs: list[DocMeta] = []
        for row in rows:
            guid = row.get("Guid")
            if not guid:
                continue
            raw = (row.get("Doc_Ref2") or row.get("Doc_Type") or "document").strip()
            docs.append(DocMeta(
                # Doc_Ref2 embeds the original filename *with* extension; strip it
                # from the title (the real extension is appended at download time)
                # but keep it as the file_hint so the extension can be derived.
                title=self._strip_ext(raw),
                source_url=urljoin(url, f"{view}?id={guid}"),
                doc_type=(row.get("Doc_Type") or "").strip() or None,
                date=self._parse_date(row.get("Date_Received"), date_fmt),
                doc_id=guid,
                file_hint=raw,
            ))

        total = len(docs)
        for i, doc in enumerate(docs, start=1):
            doc.index, doc.total = i, total
        return docs

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _extract_rows(html: str) -> list:
        """Pull the embedded ``"Rows":[ … ]`` document array out of the page.

        Matches brackets with JSON string-awareness so quotes/brackets inside
        document titles don't break the extraction.
        """
        key = html.find('"Rows":')
        if key == -1:
            return []
        start = html.find("[", key)
        if start == -1:
            return []
        depth, in_str, esc = 0, False, False
        for i in range(start, len(html)):
            ch = html[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(html[start:i + 1])
                    except json.JSONDecodeError:
                        return []
        return []

    # Real document extensions seen on these portals; used to de-duplicate the
    # extension that Doc_Ref2 embeds in the title.
    _EXTS = {"pdf", "doc", "docx", "xls", "xlsx", "xlsm", "csv", "rtf", "txt",
             "jpg", "jpeg", "png", "gif", "tif", "tiff", "bmp", "msg", "eml",
             "zip", "dwg", "dxf", "ppt", "pptx"}

    @classmethod
    def _strip_ext(cls, title: str) -> str:
        stem, dot, ext = title.rpartition(".")
        if dot and ext.lower() in cls._EXTS:
            return stem.strip()
        return title

    @staticmethod
    def _view_url(html: str) -> str:
        m = re.search(r"viewDocumentUrl\s*=\s*'([^']+)'", html)
        return m.group(1) if m else _DEFAULT_VIEW

    @staticmethod
    def _detect_date_format(values) -> str:
        """Decide DD/MM vs MM/DD from a page's dates: any first component > 12
        proves day-first, any second component > 12 proves month-first. If every
        date is ambiguous, default to UK day-first."""
        for value in values:
            token = (value or "").strip().split(" ")[0]
            parts = token.split("/")
            if len(parts) != 3:
                continue
            try:
                a, b = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            if a > 12:
                return "%d/%m/%Y"
            if b > 12:
                return "%m/%d/%Y"
        return "%d/%m/%Y"

    @staticmethod
    def _parse_date(value, fmt: str = "%d/%m/%Y"):
        if not value:
            return None
        token = value.strip().split(" ")[0]  # "06/09/2026 16:26:11" -> "06/09/2026"
        try:
            return datetime.strptime(token, fmt).date()
        except ValueError:
            return None
