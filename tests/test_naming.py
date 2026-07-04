"""Fast, offline checks for the naming logic — the easiest place for subtle bugs.

Run:  python tests/test_naming.py
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plangrab.engine.models import DocMeta
from plangrab.engine.naming import dedupe, render_filename, sanitise

TEMPLATE = "{index:03d} of {total:03d} - {title} - {plan_number} - {date}"
checks = 0


def eq(got, want, label):
    global checks
    checks += 1
    assert got == want, f"{label}:\n  got : {got!r}\n  want: {want!r}"


def doc(**kw):
    base = dict(title="T", source_url="u", index=1, total=1)
    base.update(kw)
    return DocMeta(**base)


# Width padding follows total, and empty plan_number segment is dropped cleanly.
eq(
    render_filename(doc(title="Site Plan", index=7, total=142, date=date(2025, 1, 1)),
                    ".pdf", TEMPLATE),
    "007 of 142 - Site Plan - 01 Jan 2025.pdf",
    "padding + dropped empty plan_number segment",
)

# A present plan_number appears; padding width tracks a small total.
eq(
    render_filename(doc(title="Elevations", index=2, total=9, plan_number="A-101",
                        date=date(2025, 12, 31)), ".PDF", TEMPLATE),
    "2 of 9 - Elevations - A-101 - 31 Dec 2025.pdf",
    "plan_number present + ext lowercased",
)

# Missing date AND plan_number: only the index/total/title segments remain.
eq(
    render_filename(doc(title="Photo", index=5, total=38), ".jpeg", TEMPLATE),
    "05 of 38 - Photo - Photo.jpeg".replace(" - Photo.jpeg", ".jpeg"),  # see below
    "missing date and plan_number",
)
# (clarity) the above expectation spelled out:
eq(
    render_filename(doc(title="Photo", index=5, total=38), ".jpeg", TEMPLATE),
    "05 of 38 - Photo.jpeg",
    "missing date and plan_number -> trailing segments dropped",
)

# Illegal Windows characters are stripped; a title's own ' - ' is preserved.
eq(
    sanitise('Plan: A/B "draft" <v2>'),
    "Plan AB draft v2",
    "illegal chars stripped",
)
eq(
    render_filename(doc(title="SGC MAP - APPLICATION SITE PLAN", index=3, total=7,
                        date=date(2025, 11, 5)), ".pdf", TEMPLATE),
    "3 of 7 - SGC MAP - APPLICATION SITE PLAN - 05 Nov 2025.pdf",
    "internal ' - ' in title preserved",
)

# De-duplication is case-insensitive and suffixes before the extension.
taken = set()
eq(dedupe("a.pdf", taken), "a.pdf", "dedupe first")
# Case-insensitive: 'A.PDF' collides with 'a.pdf' and takes the (2) slot…
eq(dedupe("A.PDF", taken), "A (2).PDF", "dedupe case-insensitive")
# …so the next 'a.pdf' skips the consumed (2) and becomes (3).
eq(dedupe("a.pdf", taken), "a (3).pdf", "dedupe shares namespace case-insensitively")

# Over-long titles are capped (no crash, trimmed cleanly).
long_name = render_filename(doc(title="X" * 400, index=1, total=1), ".pdf", TEMPLATE)
assert len(Path(long_name).stem) <= 150, "length cap"
assert long_name.endswith(".pdf"), "length cap keeps extension"
checks += 2

print(f"OK — {checks} naming checks passed.")
