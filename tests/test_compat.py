"""Offline tests for compat status I/O, age calc, and the dashboard merge.
Run: python tests/test_compat.py
"""
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plangrab.engine import compat
from plangrab.engine.registry import LpaRecord, Registry

checks = 0


def eq(got, want, label):
    global checks
    checks += 1
    assert got == want, f"{label}:\n  got : {got!r}\n  want: {want!r}"


def test_status_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "compat_status.json"
        data = {"E06000023": {"status": "ok", "doc_count": 7, "last_checked": "2026-06-28", "message": ""}}
        compat.save_status(data, p)
        eq(compat.load_status(p), data, "status save/load round-trip")
        eq(compat.load_status(Path(d) / "missing.json"), {}, "missing status file -> {}")


def test_days_since():
    five = (date.today() - timedelta(days=5)).isoformat()
    eq(compat.days_since(five), 5, "days_since counts days")
    eq(compat.days_since(None), None, "days_since None")
    eq(compat.days_since("not-a-date"), None, "days_since bad format")


def test_merge_for_dashboard():
    reg = Registry([
        LpaRecord(lpa_name="Bristol City Council", gss_code="E06000023", system="idox",
                  domains=["pa.bristol.gov.uk"], example_application_url="https://x"),
        LpaRecord(lpa_name="New Council", gss_code="", system="idox", domains=["pa.new.gov.uk"]),
    ])
    status = {"E06000023": {"status": "ok", "doc_count": 7, "last_checked": "2026-06-28"}}
    rows = compat.merge_for_dashboard(reg, status)
    eq(len(rows), 2, "merge: one row per registry record")
    eq(rows[0]["status"], "ok", "merge: status pulled in by gss key")
    eq(rows[0]["doc_count"], 7, "merge: doc_count")
    # New Council has no status entry and no gss code -> defaults to unchecked.
    eq(rows[1]["status"], compat.UNCHECKED, "merge: missing status -> unchecked")
    eq(rows[1]["doc_count"], None, "merge: missing doc_count -> None")


if __name__ == "__main__":
    test_status_roundtrip()
    test_days_since()
    test_merge_for_dashboard()
    print(f"OK — {checks} compat checks passed.")
