# PlanGrab

Bulk-download **every** document for a single UK planning application, each saved
in its **original format** (PDF / Word / Excel / image — never a zip) and renamed
from the metadata on the page, into a folder you choose.

LPA portals rarely have a "download all" button. This is that button.

---

## Quick start

### macOS (development)
```bash
./run.sh                 # creates .venv, installs deps, opens the web UI
```
Or use the CLI directly:
```bash
python plangrab/cli.py "<documents-page-url>" "<output-folder>"
python plangrab/cli.py "<url>" "<folder>" --list-only     # preview, no download
python plangrab/cli.py "<url>" "<folder>" --limit 5        # just the first 5 (sampling)
python -m plangrab.selftest                                # is the install healthy + online?
```
The CLI prints clean one-line-per-file progress; full detail (and every request)
goes to `plangrab.log` in the output folder.

### Windows (the locked-down target PC — no install, no admin)
1. Get the portable folder (`dist/PlanGrab-win64.zip`, built with `./build_portable.sh` on a Mac — see **Portable packaging**). Unzip it anywhere, including a synced folder.
2. **Self-test first.** Right-click `Run-Check.ps1` → *Run with PowerShell* (or run
   `.\python\python.exe -m plangrab.selftest`). It confirms Python runs, the
   dependencies and folder picker are present, the data loads, and — the usual
   make-or-break on a corporate PC — that this machine can actually **reach a
   council site** (proxies/firewalls often block outbound HTTPS). All green = good to go.
3. Run `Run.ps1` (right-click → *Run with PowerShell*, or `.\Run.ps1`). It starts
   a local-only web server and opens your browser. Close the console to stop it.

---

## How it works

```
plangrab/
  engine/            # GUI-independent document engine (the core)
    models.py        # DocMeta / FetchResult dataclasses
    base.py          # Scraper interface (incl. per-system user_agent)
    idox.py          # IDOX "Public Access" scraper
    northgate.py     # Northgate / NEC Public Access scraper (working; see findings)
    registry.py      # data-driven: URL -> which Scraper + which LPA
    compat.py        # status taxonomy + compat_status.json read/write
    naming.py        # filename template + sanitisation + de-dup
    download.py      # session, retries, streaming, skip-existing, manifest
    config.py        # loads config.toml
  web/
    app.py           # FastAPI: /api/discover, /api/download, /api/compat, /api/smoke-test,
                     #   /api/ping (+ CORS/Private-Network access for the hosted UI)
    server.py        # binds a known port ([server] ports), opens the browser
    static/          # downloader UI + dashboard.html (drop a Claude Design build in here)
  cli.py             # full engine, no GUI — build/test this first
  selftest.py        # `python -m plangrab.selftest` — is the install healthy + online?
data/
  lpa_registry.csv   # HUMAN-OWNED single source of truth (council -> system + URLs)
  compat_status.json # MACHINE-WRITTEN by the smoke test (never hand-edit)
  lpa_boundaries.json# MACHINE-BUILT LPA boundaries for the dashboard map
tools/
  smoke_test.py      # discover-only compatibility check -> compat_status.json
  seed_registry.py   # seed the registry from planning.data.gov.uk (proposes only)
  idox_probe.py      # probe candidate hosts for the IDOX signature (proposes only)
  harvest_examples.py# find a working example documents URL per IDOX council (proposes only)
  build_systems.py   # classify every England LPA by planning system (UKPlanning join)
  idox_candidate_hosts.py # candidate hosts for the addable (blue) IDOX councils
  build_map.py       # build the coverage-map boundaries from the gov geometry
  check_registry.py  # lint the registry (duplicate domains, bad names, bad systems)
  backfill_gss.py    # fill blank GSS codes from planning.data.gov.uk (+ migrate status)
  canonicalise_names.py # rename councils to official LPA names (match the map exactly)
tests/               # offline test suite (no network, no pytest) -> python tests/run_all.py
config.toml          # naming, politeness, per-system UA, LPA name registry
Run.ps1 / run.sh     # launchers (Windows / macOS)
Run-Check.ps1        # Windows: double-click to self-test the install
build_portable.sh    # assembles the no-admin Windows folder from a Mac
requirements.txt
```

The **engine is completely independent of any UI** — the CLI, the web app, the
dashboard and the smoke test are all thin wrappers over the same engine calls.

### Adding support for another council / system — "system is code, LPA is data"
- **Another council on a supported system:** add **one row** to
  `data/lpa_registry.csv` (`system` + `domains` + an `example_application_url`).
  No code. (Pasting a URL for an un-catalogued IDOX/Northgate host even works
  without a row — signature detection handles it — but a row gives it a name and
  puts it on the dashboard.)
- **A different portal *system* (new software):** write a `Scraper` subclass in
  `engine/`, register it in `SYSTEMS` in `registry.py`, and add its path signature
  to `SIGNATURES`. No site logic lives anywhere else. A known LPA whose system has
  no scraper yet reports `unsupported`; a truly unknown host fails with a clear,
  signature-aware message.

### The registry (data) vs status (machine) split
- `data/lpa_registry.csv` is **human-owned and version-controlled** — automation
  never rewrites it.
- `data/compat_status.json` is **written only by the smoke test**, keyed by GSS
  code (or LPA name when none). Keeping them separate stops automated runs from
  churning the file you hand-edit. The dashboard merges the two at request time.

---

## IDOX findings (reverse-engineering notes)

Verified June 2026 against South Gloucestershire and North Somerset (Bristol was
returning HTTP 500 site-wide at the time — a transient outage, not a structural
difference; it runs the same IDOX software and uses the identical layout).

- **HTTP-only is sufficient.** IDOX document pages are fully server-rendered.
  Plain `httpx` + BeautifulSoup works — **no JavaScript, no headless browser, no
  session warm-up, and no "accept terms" interstitial** was needed on any test
  site. We still carry a normal session (cookies, honest User-Agent, referer).
- **URL signature:** `…/online-applications/applicationDetails.do?activeTab=documents&keyVal=<KEY>`.
  The scraper rewrites `activeTab` to `documents`, so you can paste any tab of the
  application and still get the document list.
- **Document table:** `<table id="Documents">`. Each `<tr>` (after the header) is
  one document.
- **Column order is NOT fixed between councils** — North Somerset inserts a
  "Measure" column that South Gloucestershire doesn't. So columns are mapped by
  **header (`<th>`) text** (`Date Published`, `Document Type`, `Description`,
  `View`), never by fixed index. This was the single most important finding.
- **The file link is direct.** Each row's *View* `<a>` href is a direct link to
  the file: `/online-applications/files/<HASH>/[pdf/]<appref>-<NAME>-<ID>.<ext>`.
  Despite the `recaptcha-link` CSS class on it, **no captcha is enforced
  server-side** — the file streams directly given a normal request. We
  deliberately ignore the bulk "Download Files" button, which yields a ZIP.
- **Metadata mapping:** `title` = the *Description* column; `date` = *Date
  Published* (rendered `DD Mon YYYY`, e.g. `29 Oct 2025`); `doc_type` = *Document
  Type*. Some councils leave *Description* blank (seen on North Somerset appeal
  rows) — we fall back to the Document Type as the title rather than emit a blank.
- **No per-document "plan number".** IDOX does not expose a drawing/plan number as
  its own column. The reference embedded in the file URL (e.g. `P25_02358_CLE`) is
  the *application* reference and is identical for every document, so it is not a
  per-file plan number. `plan_number` is therefore left empty for IDOX and the
  naming template omits that segment cleanly. (`doc_id`, the stable per-document
  id from the checkbox, is captured for the manifest/debugging.)
- **Extensions are preserved from the real file**, derived in order from
  `Content-Disposition` → URL path → `Content-Type` → the page's filename hint.
  Confirmed across `.pdf`, `.jpeg`, `.docx`, and `.msg` in testing — nothing is
  forced to `.pdf`.
- **Pagination:** the documents tab lists **all** documents on one server-rendered
  page — re-confirmed against a 108-document application (West Oxfordshire) with
  **zero** pagination markup. No pagination handling is needed; if a future council
  ever paginates, that's a contained addition to `idox.py`.
- **Site differences:** the only structural difference found was the extra
  "Measure" column on North Somerset, already handled by header-based mapping.
  Bristol was down during testing but is the same software.

---

## Northgate findings (working — `engine/northgate.py`)

The second supported system. **Validated end-to-end against Runnymede** (223
documents discovered, real `.xlsx`/`.pdf` downloads). The key realisation:
Northgate Planning Explorer (`/Northgate/PlanningExplorer/`) links out to a
**separate NEC document server** for the actual files — the URL the user pastes —
and *that* page is cleanly parseable.

- **Documents URL / signature:** e.g.
  `docs.runnymede.gov.uk/PublicAccess_LIVE/SearchResult/RunThirdPartySearch?FileSystemId=PL&FOLDER1_REF=<ref>`.
  Routed by the path signature `/PublicAccess_` (distinct from IDOX's
  `/online-applications/`; safe because IDOX's *path* is never `/PublicAccess…`
  even when its *host* is `publicaccess.<council>`).
- **Server-rendered JSON.** No JavaScript needed: the page embeds the document
  list as `…"Rows":[{"Guid","Doc_Type","Date_Received","Doc_Ref2"}, …]`. The
  scraper extracts that array (string-aware bracket matching) — `Doc_Ref2` is the
  title (with the original filename, whose embedded extension we strip from the
  title but keep as the extension hint), `Date_Received` is `DD/MM/YYYY HH:MM:SS`.
- **Download:** `…/Document/ViewDocument?id=<Guid>` serves the file in its original
  format (the endpoint base is read from the page's `viewDocumentUrl`). No captcha.
- **Vendor:** NEC (necsws.com), formerly Northgate Public Services — so this same
  scraper should handle other Northgate/NEC councils that use this document server.
- **WAF caveat (shaped a design choice):** several Northgate hosts return 503/403
  to non-browser User-Agents, so the scraper sets a browser-like `user_agent`
  (overridable via config's `[user_agents]`; IDOX keeps the honest UA). See
  `Scraper.user_agent` / `user_agent_for()`.
- **Adding more:** unlike IDOX there's no auto-harvester yet — paste one
  application's NEC documents URL per council as a `system=northgate` row. The 16
  Northgate councils show **blue** ("supported — ready to add") on the map.

---

## Hosted site (downloader + coverage) & self-updating registry

`python tools/build_site.py` regenerates `docs/`, which GitHub Pages serves
directly (Settings → Pages → branch `master`, folder `/docs`). It publishes two
pages, both transformed from the live app's own `static/` assets so the design
never diverges:

- **`index.html` — the Downloader.** A shareable URL anyone can open: type a
  reference (or paste a documents URL), pick a folder, download. It can't scrape
  council portals *itself* — browsers block a web page from reading cross-origin
  responses (CORS), and that's a browser rule, not something the app can switch
  off. So the hosted page drives a small **local helper** over
  `http://127.0.0.1`: the same engine, run on the user's own machine. **The
  fetches still leave from the user's IP** — deliberately, because a centrally
  hosted downloader would hammer council portals from one datacenter IP and get
  blocked. First-time users download and run the helper once (the page detects it
  and onboards them); a browser may show a one-off "allow local network" prompt.
  This trades a small local install for a bookmarkable URL and always-current UI.
- **`coverage.html` — the "does my council work?" dashboard.** Pure static data
  (baked JSON, zero council requests), the coverage map + searchable table.

Mechanics that make the hosted downloader reach the local helper live in
`web/app.py` (CORS for the configured Pages origin + Chrome's Private Network
Access preflight, and a cheap `/api/ping`), `web/server.py` (a *known* port from
`[server] ports` so the page can find the helper), and `static/app.js`
(auto-detects whether it's the helper's own UI or the hosted page, discovers the
helper, and gates the form behind a live connection). Configure the allowed
origin / ports under `[server]` in `config.toml`.

The local app completes the loop by **self-updating its council registry**: on
startup it quietly fetches `lpa_registry.csv` / `compat_status.json` /
`lpa_systems.csv` from the repo (`plangrab/engine/update.py`), so users gain
newly-harvested councils without re-downloading the bundle. Strictly
best-effort — offline or blocked networks keep the shipped data; responses are
validated so an error page can never clobber real files. Opt out or repoint via
`[registry_update]` in `config.toml`.

## Scaling across LPAs: registry, smoke test & dashboard

### Registry (`data/lpa_registry.csv`)
Human-owned source of truth. Columns: `lpa_name, gss_code, system, domains,
portal_base_url, example_application_url, notes`. `system` is one of the ids in
`registry.SYSTEMS` (`idox`, `northgate`, …) or `unknown`. Adding a council on a
supported system is a one-row edit.

### Compatibility smoke test (`tools/smoke_test.py`)
Calls **only** each scraper's `discover()` against its `example_application_url`
(lists documents, downloads nothing) and writes a status to
`data/compat_status.json`. It never edits the registry. Status taxonomy:
`ok`, `no_documents`, `stale_example`, `auth_or_terms`, `parse_error`,
`unsupported`, `network_error`. Resumable (skips rows checked within N days),
incremental writes, polite (honest/its per-system UA, delay, back-off, sequential).
```
python tools/smoke_test.py            # rows not checked in 14 days
python tools/smoke_test.py --all      # re-check everything
python tools/smoke_test.py --system idox
python tools/smoke_test.py --status parse_error,stale_example
```
`stale_example` (refresh the URL) and `parse_error` (a regression / structure
change) are the statuses that need a human.

### Dashboard (`/dashboard`)
Served by the same FastAPI app, linked from the downloader. Reads the registry +
status at request time and shows a searchable, sortable, filterable table with
colour-coded badges, plus a coverage strip (total, % supported, per-system
breakdown). Rows needing attention are marked. An optional **Re-run checks** button
streams `/api/smoke-test` progress and refreshes — the standalone script and the
button share the exact same classification code.

### Coverage map (`/api/coverage-map`)
A choropleth of all 308 English LPAs, coloured by what we know about each:
- **green** — the tool works there (registry + smoke-test `ok`);
- **red** — in the registry but not `ok`;
- **blue** — runs **IDOX** (a supported system) but isn't catalogued yet → just
  needs harvesting (134 of these as of writing);
- **amber** — runs another known system we don't support yet (Northgate, SwiftLG,
  Ocella, Civica, …), labelled by system on hover;
- **grey** — system not identified.

It's a self-contained inline SVG (no tiles, no internet — matters on the offline
PC). Hover for council + status/system; click to filter the table. Boundaries come
from `data/lpa_boundaries.json` (`tools/build_map.py`); the system colours come
from `data/lpa_systems.csv` (`tools/build_systems.py`, see below).

### Classifying every LPA by system (`tools/build_systems.py`)
Joins the England LPA list to the authoritative **UKPlanning** `scraper_list.csv`
(authority → planning system) to label all 308 councils. ~80% classify cleanly
(the rest are post-2017 reorganisations); IDOX is ~55% of England, which is why the
IDOX scraper is the high-leverage one. Output: `data/lpa_systems.csv`.

### Expanding IDOX coverage in bulk
One command does the whole thing — harvest, add to the registry, fix names + GSS
codes, and smoke-test — with `--apply`:
```
python tools/idox_hosts_from_ukplanning.py   # -> data/idox_candidate_hosts.txt (REAL hosts)
python tools/harvest_examples.py --hosts-file data/idox_candidate_hosts.txt --apply
```
`idox_hosts_from_ukplanning.py` pulls the *actual* portal hostnames for ~190 IDOX
councils from the UKPlanning project — far better than the slug-guessing fallback
(`tools/idox_candidate_hosts.py`), because it finds irregular domains like
`boppa.poole.gov.uk` or `isa.chiltern.gov.uk` that guessing never could.
`--apply` appends each confirmed council, runs `tools/canonicalise_names.py` (official
LPA names, matching the map) and `tools/backfill_gss.py` (GSS codes), then smoke-tests
the new rows — no manual review/paste. Drop `--apply` to instead write proposals to
`data/registry_candidates.csv` for a human to paste in. Each council added turns from
blue to green automatically; irregular-domain councils that slug-guessing misses just
need one application's documents URL added by hand.

> Supporting a non-IDOX system (the amber councils) means writing that vendor's
> `Scraper` subclass — Northgate/NEC is now done (blue on the map; paste a documents
> URL per council); Civica/Salesforce are JavaScript portals that need a captured
> request. That's per-vendor work, separate from the IDOX expansion above.

### Seeding & enriching (`tools/seed_registry.py`, `tools/idox_probe.py`)
- **Seed** the canonical LPA list (308 active councils) from
  `planning.data.gov.uk` into a **proposed** `data/lpa_registry.seed.csv` — it
  preserves confirmed rows (matched by normalised name) and stubs the rest as
  `system=unknown`. Re-runnable; never overwrites the live CSV.
- **Probe** `unknown` rows for the IDOX signature on predictable hostnames and
  write **proposals** to `data/idox_candidates.csv` for human approval. Slug-
  guessing catches tidy domains (e.g. `pa.bristol.gov.uk`); irregular ones
  (`n-somerset.gov.uk`, `southglos.gov.uk`) still need a manual cross-reference
  (Planning Portal LPA finder / PlanIt).

**The labour-intensive part is the initial system + URL mapping per council.** Once
a row exists, the smoke test keeps its compatibility honest automatically.

---

## Naming

Template (configurable in `config.toml`, since it will likely change after use):
```
{index:03d} of {total:03d} - {title} - {plan_number} - {date}
```
Example: `007 of 142 - Proposed Ground Floor Plan - 01 Jan 2025.pdf`

- `index`/`total` are zero-padded to the width of `total`.
- The template is split on `" - "`; a segment whose field is empty is **dropped
  whole**, so missing metadata never leaves a dangling `" -  - "` (this is why the
  examples above have no empty `plan_number` gap).
- Names are sanitised for Windows (`\ / : * ? " < > |`, control chars, trailing
  dots/spaces removed; whitespace collapsed; length capped). Identical names get a
  ` (2)` suffix.
- The real extension is always appended from the downloaded file, never the
  template.

---

## Behaviour & robustness

- Polite by default: honest User-Agent, a configurable delay between requests
  (`request_delay`, default 0.7s), and exponential back-off retries on transient
  errors (429/5xx/network).
- Downloads are **streamed** to a `.part` file then atomically renamed.
- **Resume-friendly:** files already present are skipped.
- A **`manifest.csv`** is written to the output folder (index, title, plan number,
  date, doc type, source URL, final filename, status, error).
- One failed document never aborts the run; a per-file success/failure summary is
  printed at the end and streamed to the web UI.
- A `plangrab.log` is written to the output folder for debugging.
- **Corporate networks / TLS interception:** certificates are verified against the
  *operating system's* certificate store (via `truststore`), so a work proxy that
  re-signs HTTPS with a company CA installed by IT "just works". If you still see
  `CERTIFICATE_VERIFY_FAILED` / `_ssl.c:…` errors on a work machine, set
  `tls_verify = false` under `[network]` in `config.toml` as a last resort (it
  disables certificate checking for PlanGrab's requests only).

---

## Tests

A fully offline test suite (no network, no third-party test runner) — run it with:
```
python tests/run_all.py
```
Checks across 12 modules (`tests/test_*.py`), using small hand-built fixtures
(`tests/fixtures/`), a fake HTTP client, and Starlette's `TestClient` so nothing
hits a council site:
- **test_idox** — the heart of the app: column mapping (5-col vs the 6-col
  "Measure" layout), metadata extraction, the doc-type title fallback for blank
  descriptions, `/Disclaimer` interstitial handling, URL normalisation, and the
  missing-table error.
- **test_northgate** — parsing the NEC document server's embedded JSON model,
  ViewDocument URL construction, date parsing, title extension de-dup.
- **test_registry** — URL → scraper resolution (known host / signature fallback /
  unsupported / unknown) and per-system User-Agent precedence.
- **test_download** — extension derivation (Content-Disposition → URL →
  Content-Type → hint; what preserves the original format) and the manifest writer.
- **test_web** — the FastAPI layer (TestClient): page render + date injection,
  `/api/compat`, `/api/coverage-map`, the `/api/discover` error path, and the
  hosted-UI transport (`/api/ping`, CORS for the Pages origin + Private Network
  Access preflight, and rejection of untrusted origins).
- **test_registry_data** — guards the *live* `lpa_registry.csv` (via
  `tools/check_registry.py`) so a bad pasted row fails the suite.
- **test_compat / test_build_map / test_naming** — status I/O + dashboard merge,
  WKT parsing + closed-ring simplification + map build, and filename templating.

---

## Portable packaging (the no-admin Windows shell)

`build_portable.sh` assembles the whole runnable folder **from a Mac** and zips it:

```bash
./build_portable.sh        # -> dist/PlanGrab-win64.zip
```

It (1) downloads a relocatable **`python-build-standalone`** CPython for Windows
x64 (the `install_only` build); (2) **slims the runtime** — strips ~58 MB of
`.pdb` debug symbols and dev-only stdlib (`ensurepip`, `idlelib`, `lib2to3`,
`turtledemo`, `pydoc_data`, C `include/`), **and drops `tcl`/`tkinter`
entirely** (see below); (3) vendors every dependency as a **Windows wheel** into
`lib/` (correct `win_amd64` binaries even when built on macOS); and (4) copies
the source, data, and the `Run*.ps1` launchers, then zips with `-9`.

**Dropping tcl/tk is the big extraction-speed win.** tcl/tk is *thousands* of
tiny files, and on a locked-down PC every extracted file is individually
AV-scanned — so file count, not megabytes, is what made unzipping slow. It was
carried solely for the native "Browse…" folder picker, so that picker now uses a
**native PowerShell dialog** (`System.Windows.Forms.FolderBrowserDialog`, spawned
by `web/app.py`'s `/api/pick-folder`) instead of `tkinter.filedialog`. No bundled
runtime is needed for it — `Run.ps1` already launches via PowerShell — and if
PowerShell is somehow unavailable the UI falls back to a typed path. (dev on
macOS/Linux still uses the tkinter dialog.)

HTML is parsed with BeautifulSoup's stdlib `html.parser` rather than `lxml`
(verified to give identical results on IDOX pages) — one fewer compiled wheel, 8.5
MB lighter, and a more robust pure-Python vendoring step.

`Run.ps1` adds `lib/` and the folder to `PYTHONPATH` and runs
`python\python.exe -m plangrab.web.server` — nothing is installed, nothing touches
the registry or system Python.

### Where the locked-down / no-admin constraint shaped the design
- **No installer and no PyInstaller single-exe** — an unzip-and-run folder of
  plain `.py` plus a relocatable `python.exe`, which corporate AV/SmartScreen
  tolerate and which is transparent to debug.
- **PowerShell launcher**, not a `.bat` — PowerShell is confirmed runnable on the
  target and is more reliable here.
- **Folder picker runs in an isolated subprocess** and degrades gracefully to a
  manual path field if it can't open. On Windows it's a native **PowerShell**
  dialog (no tcl/tk to ship — see above); on macOS/Linux dev it's `tkinter`,
  which must own the main thread, hence the subprocess.
- **Dependencies restricted to pure-Python or prebuilt Windows wheels** — no
  compiler is ever required. (`uvicorn`, not `uvicorn[standard]`, to avoid the
  `uvloop`/`httptools` C builds.)

### Fallback shell (only if the smoke test fails)
If app-allowlisting blocks `python\python.exe` from starting, the engine logic in
`plangrab/engine/` can be reproduced as a **PowerShell-native** app
(`System.Net.Http.HttpClient` + `HtmlAgilityPack` DLL + `FolderBrowserDialog`)
with zero runtime to ship. The engine is kept cleanly separated for exactly this.
Not built yet — only needed if the smoke test fails.

---

## Supported LPAs

**116 councils** confirmed `ok` by the smoke test (as of 2 July 2026) — the live
list is `data/lpa_registry.csv` and the **LPA Coverage** page renders it with the
map. Three systems: **IDOX** (the vast majority), **Northgate / NEC Public
Access** (Runnymede, Blackburn with Darwen), and **Civica W2** (Tamworth). Growth
has come almost entirely from the harvesters (below) — each council is a one-row
registry add, no code. Two Welsh councils (Swansea, Torfaen) work but don't
colour the England-only map.

### Extending coverage
Adding an IDOX council is one row in `data/lpa_registry.csv` plus an example URL.
`tools/harvest_examples.py` automates finding that URL — it clears any `/Disclaimer`
gate, runs the IDOX simple search, and verifies a candidate with `discover()`,
writing proposals to `data/registry_candidates.csv`:
```
python tools/harvest_examples.py --hosts publicaccess.solihull.gov.uk,planning.cornwall.gov.uk
python tools/harvest_examples.py --from-registry     # idox rows still missing an example
```
Adding rows recolours the dashboard map automatically — **no `build_map.py` rerun**
(boundaries for all 308 LPAs already ship).

> **Run the national sweep from a normal network** — some `*.gov.uk` planning
> hosts don't resolve from a sandboxed connection. The current candidate list is
> `data/idox_candidate_hosts.txt` (see "Expanding IDOX coverage in bulk" above):
> ```
> python tools/harvest_examples.py --hosts-file data/idox_candidate_hosts.txt --apply
> ```

### Known non-additions
- **Solihull, Somerset (unified)** — IDOX skin present, but their search is
  non-standard (no results / `search.do` 404), so auto-harvest can't find an example
  URL. Add by hand-supplying one application's documents URL.
- **Exeter, Torridge, South Somerset (legacy)** — IDOX skin but **no public
  documents table**; nothing to download, excluded.
- **Birmingham** — runs **Northgate** (now supported); add it by pasting its NEC
  documents URL, though its host WAF-blocks the dev sandbox.
- **BANES** (webforms), **Rugby** (Agile), **Wiltshire** (Salesforce SPA) — other
  systems, out of scope.

### Notes from the SW sweep (councils that didn't make the cut)
- **Disclaimer interstitial handled:** some IDOX councils (Somerset, BCP) gate
  `/online-applications/` behind a `/Disclaimer` "Agree" page. The IDOX scraper now
  clears it automatically (`IdoxScraper._fetch` POSTs `/Disclaimer/Accept`, then
  retries; the cookie carries through to downloads). Verified it clears on Somerset.
- **Somerset Council** clears the disclaimer but returns 404 on the standard
  `search.do` (non-standard install) — needs a hand-supplied example URL.
- **Exeter, Torridge** show the IDOX skin but expose **no public documents table**
  (even for older applications), so there's nothing to download — excluded.
- **Cotswold** is IDOX but its search was timing out at harvest — pending an example.
- **BANES** is not IDOX (an `app.bathnes.gov.uk/webforms/planning/` system).
- **Wiltshire** is a Salesforce SPA (and already has a "download all"), excluded.

## Systems other than IDOX encountered (notes for future work)
- **Northgate / NEC Public Access** — ✅ **supported** (`engine/northgate.py`),
  validated against Runnymede. See "Northgate findings" above. (Northgate Public
  Services was acquired and rebranded **NEC Software Solutions** in 2021, so
  modern portals brand themselves NEC.) Real candidate search hosts for ~20 more
  Northgate councils (Birmingham, Liverpool, Hackney, Islington, Camden…) are in
  `data/northgate_candidate_hosts.txt`, and `tools/harvest_northgate.py --apply`
  sweeps them exactly like the IDOX harvester: it runs a date-range search on each
  host's Planning Explorer, follows an application's documents link, classifies
  the document backend (NEC Public Access `RunThirdPartySearch`/`ExternalEntryPoint`
  or Civica W2 `dialog.page…viewdocs`) and verifies it with the matching scraper
  before adding the row. Councils whose docs sit on an unsupported backend (e.g.
  South Tyneside's MVM DocumentViewer, which serves no files) are reported, not
  added. Validated end-to-end against Blackburn. Run it from a normal network —
  most of these hosts don't resolve from the dev sandbox.
- **Civica W2 / Comino** — ✅ **supported** (`engine/civica_w2.py`), validated
  against Tamworth (whose NEC Planning Explorer search links to a W2 documents
  page). The pasted URL is the application's "View Related Documents" link
  (`…/Planning/dialog.page?…&SDescription=<app ref>&viewdocs=true`); every file
  is a direct, sessionless PDF (`…/Planning/StreamDocPage/obj.pdf?DocNo=<n>…`).
- **SwiftLG ("Web APAS")** — the next-biggest vendor (~10 England councils: Dudley,
  Walsall, Preston, Mole Valley, Rutland, Cannock Chase, Redbridge, Slough, South
  Cambs, Warrington). **Server-rendered HTML — tractable, no captured request
  needed** (confirmed: the UKPlanning scraper parsed it as HTML, and detail pages
  are plain server URLs `…/swiftlg/apas/run/WPHAPPDETAIL.DisplayUrl?theApnID=<ref>&theTabNo=<n>`
  with documents on a tab). Not yet built: the old (2017) URLs have largely moved,
  and the live servers are slow/WAF'd — unreachable from the dev sandbox — so it
  needs **one live SwiftLG documents-page URL** to confirm the documents-tab markup,
  then it's a clean build (same path Northgate took).
- **Wandsworth "Planning Archive / IAM"** (`/planningcase/`) — bespoke ASP.NET;
  documents grouped by category, revealed via postback to `IAMLink.aspx?docid=…`
  which serves the file. Tractable over plain HTTP; not yet built.
- **Salesforce Public Sector Solutions** (e.g. Wiltshire's `/pr/s/…`) — a
  JavaScript SPA; documents load from the Salesforce Aura API. Auth plumbing is
  crackable pure-HTTP, but it needs a captured network call to finish and is more
  fragile (framework id rotates). Out of scope for now.
