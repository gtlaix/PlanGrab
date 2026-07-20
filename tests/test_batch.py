"""Offline tests for the batch downloader (plangrab.engine.batch).

No network and no real scraper: the engine's ``get_scraper`` / ``download_all`` /
``make_client`` are swapped for fakes so we test the batch *orchestration* —
per-item status, one-bad-reference-doesn't-abort, subfolders, single-client
reuse, and the combined manifest. Run: python tests/test_batch.py
"""
import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plangrab.engine import batch as B
from plangrab.engine.base import ReferenceLookupError
from plangrab.engine.config import Config
from plangrab.engine.models import DocMeta, FetchResult

checks = 0


def eq(got, want, label):
    global checks
    checks += 1
    assert got == want, f"{label}:\n  got : {got!r}\n  want: {want!r}"


def ok(cond, label):
    global checks
    checks += 1
    assert cond, label


class DummyClient:
    def close(self):
        pass


class FakeScraper:
    """Scripts resolution/discovery by reference so we can exercise every branch."""
    lpa_name = "Testville"
    base_url = "https://portal"

    def __init__(self):
        self.clients_seen = []

    def resolve_reference(self, client, ref):
        self.clients_seen.append(id(client))
        if ref.startswith("NOPE"):
            raise ReferenceLookupError(f"no match for {ref}")
        if ref.startswith("BOOM"):
            return "https://portal/app?keyVal=BOOM"   # discover() will raise
        if ref.startswith("EMPTY"):
            return "https://portal/app?keyVal=EMPTY"   # discover() returns []
        return f"https://portal/app?keyVal={ref.replace('/', '')}"

    def discover(self, client, url):
        if url.endswith("BOOM"):
            raise RuntimeError("portal exploded")
        if url.endswith("EMPTY"):
            return []
        return [DocMeta(title=f"Doc{i}", source_url=f"{url}&f={i}", index=i, total=2)
                for i in (1, 2)]


def fake_download_all(scraper, docs, out_dir, config, client=None, progress=None):
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    results = []
    for d in docs:
        results.append(FetchResult(d, f"{d.title}.pdf", "downloaded"))
        if progress:
            progress({"type": "file", "index": d.index, "total": d.total,
                      "title": d.title, "filename": f"{d.title}.pdf",
                      "status": "downloaded", "error": None})
    return results


def _run(refs, parent):
    """Run download_batch with the engine deps faked out. Returns (scraper, items, events)."""
    fake = FakeScraper()
    events = []
    B.get_scraper = lambda url, reg=None: fake
    B.make_client = lambda config, ua=None: DummyClient()   # dummy shared client
    B.user_agent_for = lambda scraper, config: "UA"
    B.download_all = fake_download_all
    items = B.download_batch(refs, "https://portal", parent, Config(),
                             progress=events.append)
    return fake, items, events


def test_batch_mixed_outcomes():
    refs = ["OK1/A", "NOPE/1", "EMPTY/1", "BOOM/1", "ok1/a"]  # last is a dup of first
    with tempfile.TemporaryDirectory() as d:
        parent = Path(d)
        fake, items, events = _run(refs, parent)

        eq(len(items), 4, "batch: duplicate reference de-duplicated")
        by_ref = {it.reference: it for it in items}
        eq(by_ref["OK1/A"].status, "ok", "batch: good reference -> ok")
        eq(by_ref["OK1/A"].downloaded, 2, "batch: ok reference downloaded 2 docs")
        eq(by_ref["NOPE/1"].status, "not_found", "batch: unresolved -> not_found")
        eq(by_ref["EMPTY/1"].status, "no_documents", "batch: resolved-but-empty -> no_documents")
        eq(by_ref["BOOM/1"].status, "failed", "batch: discovery error -> failed")

        # One bad reference never aborts the rest.
        ok(all(r in by_ref for r in ("OK1/A", "NOPE/1", "EMPTY/1", "BOOM/1")),
           "batch: every reference produced a result despite failures")

        # Subfolder per application, named from the reference (/ -> -).
        ok((parent / "OK1-A").is_dir(), "batch: per-application subfolder created")
        eq(by_ref["OK1/A"].folder, str(parent / "OK1-A"), "batch: item folder path")

        # One client shared across the whole batch.
        eq(len(set(fake.clients_seen)), 1, "batch: single client reused for all references")

        # Combined manifest.
        rows = list(csv.DictReader((parent / "batch_manifest.csv").open(encoding="utf-8")))
        eq(len(rows), 4, "batch: manifest has one row per reference")
        eq({r["reference"] for r in rows},
           {"OK1/A", "NOPE/1", "EMPTY/1", "BOOM/1"}, "batch: manifest references")

        # Progress stream shape.
        types = [e["type"] for e in events]
        eq(types[0], "batch-start", "batch: first event is batch-start")
        eq(types[-1], "done", "batch: last event is done")
        eq(sum(t == "item-done" for t in types), 4, "batch: one item-done per reference")
        summary = events[-1]["summary"]
        eq(summary["ok"], 1, "summary: ok count")
        eq(summary["not_found"], 1, "summary: not_found count")
        eq(summary["failed"], 1, "summary: failed count")
        eq(summary["downloaded"], 2, "summary: total files downloaded")


def test_clean_and_subdir_helpers():
    eq(B._clean_references([" a ", "", "a", "B", "b"]), ["a", "B"],
       "clean: trim, drop blanks, de-dupe case-insensitively, keep order")
    taken = set()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        a = B._subdir_for(p, "A/B", taken)
        b = B._subdir_for(p, "A-B", taken)  # sanitises to the same base -> disambiguated
        eq(a.name, "A-B", "subdir: first gets the clean name")
        eq(b.name, "A-B (2)", "subdir: colliding distinct reference gets a suffix")


if __name__ == "__main__":
    test_batch_mixed_outcomes()
    test_clean_and_subdir_helpers()
    print(f"OK — {checks} batch checks passed.")
