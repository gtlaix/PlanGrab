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

from .base import ReferenceLookupError, Scraper
from .models import DocMeta

# IDOX dates render as "29 Oct 2025".
_DATE_FMT = "%d %b %Y"

# keyVal query param on an applicationDetails.do link.
_KEYVAL_RE = re.compile(r"keyVal=([A-Za-z0-9]+)")


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

    def resolve_reference(self, client: httpx.Client, reference: str) -> str:
        """Resolve a human application reference to its documents-page URL.

        IDOX's ``keyVal`` is opaque for modern applications, so the reference
        can't be turned into a URL directly — it has to be looked up by
        running the portal's own **simple search** (a form POST; a GET to
        ``simpleSearchResults.do`` just returns the empty form). The results
        are server-rendered, so plain httpx + an HTML parse is enough — the
        same mechanism ``tools/harvest_examples.py`` already uses to harvest
        example URLs; no headless browser needed.

        A search can return several applications, each with its own keyVal —
        e.g. searching a permission reference also surfaces a later COND whose
        *description* quotes it. So we match each result row's own ``Ref. No``
        field, never the first row or free row text, and raise rather than
        guess if nothing matches (:class:`ReferenceLookupError`).
        """
        reference = reference.strip()
        if not reference:
            raise ReferenceLookupError("No application reference supplied.")

        oa = f"{self.base_url}/online-applications"
        search_page = f"{oa}/search.do?action=simple&searchType=Application"
        # _fetch clears any /Disclaimer gate and leaves the session cookie on
        # the client, so the POST below carries it through.
        resp = self._fetch(client, search_page)
        hidden = self._hidden_inputs(resp.text)
        results = client.post(
            f"{oa}/simpleSearchResults.do?action=firstPage",
            data={**hidden,
                  "searchCriteria.simpleSearchString": reference,
                  "searchType": "Application",
                  "searchCriteria.simpleSearch": "true"},
            headers={"Referer": search_page},
        )
        results.raise_for_status()

        keyval = self._keyval_for_reference(results, reference)
        if not keyval:
            raise ReferenceLookupError(
                f"No application matching reference {reference!r} was found on "
                f"{self.lpa_name}'s portal. Check the reference (including its "
                f"suffix, e.g. /F or /COND) and that it belongs to this council."
            )

        url = self._normalise_url(f"{oa}/applicationDetails.do?keyVal={keyval}")
        # Re-verify: a wrong keyVal then surfaces as an error, not a bad link.
        verify = self._fetch(client, url)
        if not self._echoes(verify.text, reference):
            raise ReferenceLookupError(
                f"Resolved a page for reference {reference!r}, but it didn't "
                f"match the reference — the portal layout may have changed."
            )
        return url

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
    def _hidden_inputs(html: str) -> dict:
        """Name->value for every hidden field on the search form.

        IDOX seeds the simple-search POST with hidden inputs (CSRF-ish tokens,
        default criteria); replaying them keeps the search working across
        councils that vary the set.
        """
        soup = BeautifulSoup(html, "html.parser")
        out: dict[str, str] = {}
        for inp in soup.find_all("input", attrs={"type": "hidden"}):
            name = inp.get("name")
            if name:
                out[name] = inp.get("value", "")
        return out

    @classmethod
    def _keyval_for_reference(cls, response, reference: str) -> str | None:
        """Pick the keyVal for ``reference`` from a search-results response.

        Two shapes: a single exact match redirects straight to the detail page
        (take its lone keyVal, once we've confirmed the page echoes the
        reference); otherwise iterate the result rows and take the keyVal only
        from the row whose own ``Ref. No`` field equals the reference. Returns
        ``None`` (never a guess) if nothing matches.
        """
        soup = BeautifulSoup(response.text, "html.parser")
        rows = soup.select("li.searchresult") or soup.select(".searchresult")

        if not rows:
            # Single-match redirect: we're already on the detail page. Only
            # trust it if the page genuinely echoes the reference.
            if cls._echoes(soup.get_text(" ", strip=True), reference):
                link = soup.find("a", href=_KEYVAL_RE)
                if link:
                    m = _KEYVAL_RE.search(link.get("href", ""))
                    if m:
                        return m.group(1)
            return None

        # Match the row's OWN "Ref. No:" field — not free row text, which can
        # quote another application's reference (the COND-quoting-parent trap).
        # The trailing (?![\w/]) stops a reference matching a longer one it is
        # a prefix of.
        ref_field = re.compile(
            r"Ref(?:erence)?\.?\s*No\.?\s*:?\s*" + re.escape(reference) + r"(?![\w/])",
            re.IGNORECASE,
        )
        for row in rows:
            if ref_field.search(row.get_text(" ", strip=True)):
                link = row.find("a", href=_KEYVAL_RE)
                if link:
                    m = _KEYVAL_RE.search(link.get("href", ""))
                    if m:
                        return m.group(1)
        return None

    @staticmethod
    def _echoes(text: str, reference: str) -> bool:
        """True if the page text carries the reference (with or without slashes)."""
        return reference in text or reference.replace("/", "") in text

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
