"""Scraper for the Civica W2 / Comino planning documents page.

Some councils front their planning register with NEC/Northgate Planning Explorer
but serve the actual files from Civica's W2 (Comino) document manager — e.g.
Tamworth. The application detail page's "View Related Documents" link is the URL
the user pastes::

    https://planning.tamworth.gov.uk/Planning/dialog.page?org.apache.shale.dialog.DIALOG_NAME=gfplanningsearch&Param=lg.Planning&SDescription=<app ref>&viewdocs=true

Verified July 2026 against Tamworth:

* The page is server-rendered (Apache Shale / JSF, ``xmlns:w2="comino.com"``)
  with one table — headers ``Document No | Description | Document Date |
  Document Title | Download Pdf version``.
* Each row's download link is direct and sessionless:
  ``/Planning/StreamDocPage/obj.pdf?DocNo=<n>&PDF=true&content=obj.pdf``
  (always a PDF rendition, served with the honest User-Agent).
* Dates are verbose US-style: ``Thursday, June 11, 2026``.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .base import Scraper
from .models import DocMeta


class CivicaW2Scraper(Scraper):
    system_name = "Civica W2 / Comino"
    system_id = "civica_w2"

    def discover(self, client: httpx.Client, url: str) -> list[DocMeta]:
        resp = client.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Anchor on the download links — the one stable, distinctive feature.
        links = [a for a in soup.find_all("a", href=True)
                 if "StreamDocPage" in a["href"]]
        if not links:
            raise LookupError(
                "No document rows found on the Civica W2 documents page. Either "
                "the application has no documents, or this isn't a "
                "'…dialog.page?…viewdocs=true' documents URL. URL: " + url
            )

        docs: list[DocMeta] = []
        for a in links:
            row = a.find_parent("tr")
            cells = row.find_all("td") if row else []
            texts = [c.get_text(" ", strip=True) for c in cells]
            doc_no = self._doc_no(a["href"], cells)
            description = texts[1] if len(texts) > 1 else ""
            doc_date = self._parse_date(texts[2]) if len(texts) > 2 else None
            doc_title = texts[3] if len(texts) > 3 else ""
            # "Document Title" is usually blank; "Description" carries the meaning.
            title = doc_title or description or f"Document {doc_no or '?'}"
            docs.append(DocMeta(
                title=title,
                source_url=urljoin(url, a["href"]),
                doc_type=description or None,
                date=doc_date,
                doc_id=doc_no,
                file_hint=f"{doc_no}.pdf" if doc_no else "document.pdf",
            ))

        total = len(docs)
        for i, doc in enumerate(docs, start=1):
            doc.index, doc.total = i, total
        return docs

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _doc_no(href: str, cells: list) -> str | None:
        """Document number: from the link's ``DocNo=`` query, else the first
        cell's submit-button value."""
        qs = parse_qs(urlparse(href).query)
        if qs.get("DocNo"):
            return qs["DocNo"][0]
        if cells:
            btn = cells[0].find("input")
            if btn and btn.get("value"):
                return btn["value"].strip()
        return None

    @staticmethod
    def _parse_date(value: str):
        """Parse 'Thursday, June 11, 2026' (weekday optional)."""
        token = (value or "").strip()
        if "," in token and token.split(",")[0].strip().isalpha():
            first = token.split(",")[0].strip()
            if first.lower() in ("monday", "tuesday", "wednesday", "thursday",
                                 "friday", "saturday", "sunday"):
                token = token.split(",", 1)[1].strip()
        for fmt in ("%B %d, %Y", "%d %B %Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(token, fmt).date()
            except ValueError:
                continue
        return None
