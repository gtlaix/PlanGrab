"""Scraper for IDOX "Public Access" planning portals.

IDOX is the most common UK LPA system. Its application pages share the path
``/online-applications/applicationDetails.do`` and a documents tab reached with
``?activeTab=documents&keyVal=...``. The documents tab is fully server-rendered,
so plain HTTP (httpx) + an HTML parse is sufficient — no JavaScript, no headless
browser. See README.md ("IDOX findings") for the reverse-engineering notes.

Key facts this scraper relies on (verified June 2026 against South
Gloucestershire and North Somerset):

* The document list lives in ``<table id="Documents">``.
* **Column order is NOT fixed between councils** — some insert a "Measure"
  column. So columns are mapped by their header (``<th>``) text, never by index.
* Each row's View ``<a>`` href is a *direct* link to the file
  (``/online-applications/files/<HASH>/[pdf/]<name>.<ext>``). Despite the
  ``recaptcha-link`` CSS class, no captcha is enforced server-side; the file is
  served directly given a normal session + referer.
* There is a bulk "Download Files" button that yields a ZIP — we deliberately
  ignore it and fetch each file in its original format instead.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlparse, urlencode, urlunparse

import httpx
from bs4 import BeautifulSoup

from .base import Scraper
from .models import DocMeta

# IDOX dates render as "29 Oct 2025".
_DATE_FMT = "%d %b %Y"


class IdoxScraper(Scraper):
    system_name = "IDOX Public Access"
    system_id = "idox"
    # IDOX accepts the honest UA, so leave user_agent = None (engine default).

    def discover(self, client: httpx.Client, url: str) -> list[DocMeta]:
        url = self._normalise_url(url)
        resp = self._fetch(client, url)
        soup = BeautifulSoup(resp.text, "html.parser")

        table = soup.find("table", id="Documents")
        if table is None:
            raise LookupError(
                "No documents table found on the page. Either the application "
                "has no documents, the URL is not an IDOX documents tab, or the "
                "portal returned an error/terms page. URL: " + url
            )

        col = self._column_map(table)
        docs: list[DocMeta] = []
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue  # header row

            link = self._find_file_link(row)
            if link is None:
                continue  # not a document row

            href = urljoin(url, link["href"])
            doc_type = self._cell_text(cells, col.get("document type")) or None
            # Prefer the Description; some councils leave it blank, in which case
            # the Document Type is the most meaningful label available.
            title = (
                self._cell_text(cells, col.get("description"))
                or self._title_from_link(link)
                or doc_type
                or "document"
            )
            doc = DocMeta(
                title=title,
                source_url=href,
                doc_type=doc_type,
                date=self._parse_date(self._cell_text(cells, col.get("date published"))),
                doc_id=self._doc_id(row, href),
                file_hint=self._file_hint(row, href),
            )
            docs.append(doc)

        total = len(docs)
        for i, doc in enumerate(docs, start=1):
            doc.index = i
            doc.total = total
        return docs

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _fetch(client: httpx.Client, url: str) -> httpx.Response:
        """GET ``url``, transparently clearing an IDOX "accept disclaimer" gate.

        Some councils (e.g. Somerset, BCP) redirect ``/online-applications/`` to a
        ``/Disclaimer`` page whose "Agree" form POSTs to ``/Disclaimer/Accept`` and
        sets a session cookie. We submit it once, then retry the original URL; the
        cookie then carries through to document downloads on the same client.
        """
        resp = client.get(url)
        if "/Disclaimer" in str(resp.url):
            soup = BeautifulSoup(resp.text, "html.parser")
            form = soup.find("form", action=re.compile("Disclaimer/Accept", re.I)) or soup.find("form")
            if form and form.get("action"):
                client.post(urljoin(str(resp.url), form["action"]))
                resp = client.get(url)
        resp.raise_for_status()
        return resp

    @staticmethod
    def _normalise_url(url: str) -> str:
        """Force the URL onto the documents tab, preserving keyVal.

        Lets a user paste any tab of the application (summary, details…) and
        still get the document list.
        """
        parts = urlparse(url)
        qs = parse_qs(parts.query)
        qs["activeTab"] = ["documents"]
        new_query = urlencode({k: v[0] for k, v in qs.items()})
        return urlunparse(parts._replace(query=new_query))

    @staticmethod
    def _column_map(table) -> dict:
        """Map lower-cased header text -> column index, from the first <th> row."""
        mapping: dict[str, int] = {}
        header_row = table.find("tr")
        if header_row is None:
            return mapping
        for i, th in enumerate(header_row.find_all("th")):
            text = th.get_text(strip=True).lower()
            if text:
                mapping[text] = i
        return mapping

    @staticmethod
    def _cell_text(cells, index) -> str:
        if index is None or index >= len(cells):
            return ""
        return cells[index].get_text(" ", strip=True)

    @staticmethod
    def _find_file_link(row):
        """Return the <a> that points at the actual file, or None."""
        for a in row.find_all("a", href=True):
            if "/online-applications/files/" in a["href"]:
                return a
        return None

    @staticmethod
    def _title_from_link(link) -> str:
        # Fallback when there is no Description column text: parse the link title
        # e.g. 'View CABIN INVOICE document (ID 8565684, ...)'.
        title = link.get("title", "")
        m = re.match(r"View (.+?) document", title)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _doc_id(row, href: str) -> str | None:
        # Checkbox id is 'chk-<id>-document'; href ends '...-<id>.<ext>'.
        chk = row.find("input", attrs={"name": "file"})
        if chk and chk.get("id"):
            m = re.search(r"chk-(\d+)-document", chk["id"])
            if m:
                return m.group(1)
        m = re.search(r"-(\d+)\.[A-Za-z0-9]+$", href)
        return m.group(1) if m else None

    @staticmethod
    def _file_hint(row, href: str) -> str | None:
        # The checkbox value holds 'HASH/ORIGINALNAME-ID.ext' — a good extension
        # fallback when neither Content-Disposition nor URL is conclusive.
        chk = row.find("input", attrs={"name": "file"})
        if chk and chk.get("value"):
            return chk["value"].split("/")[-1]
        return href.rsplit("/", 1)[-1] if "/" in href else None

    @staticmethod
    def _parse_date(text: str):
        text = (text or "").strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, _DATE_FMT).date()
        except ValueError:
            return None
