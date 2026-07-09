"""PlanGrab command-line interface — the full engine, no GUI.

    python -m plangrab.cli <documents-page-url> <output-folder>
    python plangrab/cli.py <documents-page-url> <output-folder> [--list-only] [--limit N]

Instead of a URL you can give a reference + council, and PlanGrab looks the
documents page up itself:

    python -m plangrab.cli --ref 23/02163/COND --council pa.bristol.gov.uk <output-folder>

This is the core deliverable; the web app simply wraps the same engine calls.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

# Allow running both as `python -m plangrab.cli` and `python plangrab/cli.py`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plangrab.engine import (
    Config, ReferenceLookupError, UnknownSystemError, download_all, get_scraper,
    make_client, user_agent_for,
)


def _setup_logging(folder: Path) -> None:
    """Full detail goes to plangrab.log; the console stays quiet (warnings/errors
    only), so live per-file progress on stdout isn't drowned out by request logs."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(folder / "plangrab.log", encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.WARNING)
    sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.handlers[:] = [fh, sh]
    for noisy in ("httpx", "httpcore"):           # don't echo every HTTP request
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _progress(ev: dict) -> None:
    """Clean one-line-per-file progress on stdout, e.g. '[ 3/142] downloaded …'."""
    if ev.get("type") != "file":
        return
    idx, tot = ev.get("index", 0), ev.get("total", 0)
    width = len(str(tot)) if tot else 3
    line = f"  [{idx:>{width}}/{tot}] {ev['status']:<10} {ev['filename']}"
    if ev.get("error"):
        line += f"  ({ev['error']})"
    print(line, flush=True)


def _council_url(council: str) -> str:
    """Normalise a --council value (host or base URL) to a URL get_scraper accepts.

    A bare host (``pa.bristol.gov.uk``) gets an https scheme and the standard
    ``/online-applications/`` path so the registry/signature lookup resolves it
    to the IDOX scraper.
    """
    council = council.strip()
    if not re.match(r"^https?://", council):
        council = "https://" + council.strip("/") + "/online-applications/"
    return council


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bulk-download UK planning documents.")
    parser.add_argument("url", nargs="?",
                        help="The application's documents-page URL (or use --ref/--council)")
    parser.add_argument("folder", help="Output folder for the downloaded files")
    parser.add_argument("--ref", metavar="REFERENCE",
                        help="Look the documents page up by application reference "
                             "(e.g. 23/02163/COND); requires --council")
    parser.add_argument("--council", metavar="PORTAL",
                        help="Council portal for --ref: a host (pa.bristol.gov.uk) or "
                             "its base URL (https://pa.bristol.gov.uk/online-applications/)")
    parser.add_argument("--list-only", action="store_true", help="Discover and list; do not download")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="Download only the first N documents (handy for sampling/testing)")
    parser.add_argument("--config", help="Path to config.toml (optional)")
    args = parser.parse_args(argv)

    if args.ref and args.url:
        parser.error("give either a URL or --ref, not both")
    if not args.ref and not args.url:
        parser.error("give a documents-page URL, or --ref with --council")
    if args.ref and not args.council:
        parser.error("--ref requires --council")

    out = Path(args.folder).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    _setup_logging(out)

    config = Config.load(args.config)

    # The scraper is chosen from the URL, or from the council when using --ref.
    lookup_url = args.url or _council_url(args.council)
    try:
        scraper = get_scraper(lookup_url, config.lpa_registry)
    except UnknownSystemError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"LPA:    {scraper.lpa_name}")
    print(f"System: {scraper.system_name}")

    client = make_client(config, user_agent_for(scraper, config))
    try:
        url = args.url
        if args.ref:
            print(f"Resolving reference {args.ref}…")
            try:
                url = scraper.resolve_reference(client, args.ref)
            except ReferenceLookupError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(f"Found: {url}")

        print("Discovering documents…")
        docs = scraper.discover(client, url)
        print(f"Found {len(docs)} document(s).\n")
        preview = docs if args.list_only else docs[:20]
        for d in preview:
            date = d.date.strftime(config.date_format) if d.date else "—"
            print(f"  {d.index:>3}/{d.total}  [{date}]  {d.title}")
        if len(preview) < len(docs):
            print(f"  … and {len(docs) - len(preview)} more (use --list-only to see all)")

        if args.list_only or not docs:
            return 0

        if args.limit and len(docs) > args.limit:
            full = len(docs)
            docs = docs[:args.limit]
            for i, d in enumerate(docs, start=1):   # renumber so names read "1 of N"
                d.index, d.total = i, len(docs)
            print(f"\n(--limit: downloading the first {len(docs)} of {full})")

        print(f"\nDownloading {len(docs)} to {out} …\n")
        results = download_all(scraper, docs, out, config, client=client, progress=_progress)
    finally:
        client.close()

    ok = sum(r.status == "downloaded" for r in results)
    skipped = sum(r.status == "skipped" for r in results)
    failed = [r for r in results if r.status == "failed"]
    print(f"\nDone: {ok} downloaded, {skipped} skipped, {len(failed)} failed.")
    print(f"Manifest: {out / 'manifest.csv'}")
    if failed:
        print("\nFailures:")
        for r in failed:
            print(f"  [{r.doc.index}] {r.doc.title}: {r.error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
