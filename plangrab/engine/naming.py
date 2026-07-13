"""Turn a :class:`DocMeta` into a safe, readable filename.

The template is a configurable format string (see ``config.toml``). It is split on
" - " into *segments*; any segment whose referenced field is empty is dropped
whole, so missing metadata never leaves a dangling " -  - ". ``index``/``total``
are zero-padded to the width of ``total``. The real file extension is supplied
separately by the downloader (from Content-Disposition / Content-Type / URL) and
always appended — the template never decides the extension.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import DocMeta

# Characters illegal in Windows filenames, plus control chars.
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_PLACEHOLDER = re.compile(r"\{(\w+)(?::[^}]*)?\}")

# Windows MAX_PATH is ~260; leave generous headroom for the output directory.
MAX_STEM_LEN = 150


def render_filename(
    doc: "DocMeta",
    ext: str,
    template: str,
    date_format: str = "%d %b %Y",
) -> str:
    """Render ``doc`` to ``"<stem>.<ext>"`` using ``template``."""
    width = len(str(doc.total)) if doc.total else 3
    values = {
        "index": f"{doc.index:0{width}d}" if doc.index else "",
        "total": f"{doc.total:0{width}d}" if doc.total else "",
        "title": doc.title or "",
        "plan_number": doc.plan_number or "",
        "doc_type": doc.doc_type or "",
        "doc_id": doc.doc_id or "",
        "date": doc.date.strftime(date_format) if doc.date else "",
    }

    rendered_segments: list[str] = []
    for segment in template.split(" - "):
        fields = _PLACEHOLDER.findall(segment)
        # Drop the segment if any field it references is empty.
        if fields and any(not values.get(f, "") for f in fields):
            continue
        text = _PLACEHOLDER.sub(lambda m: values.get(m.group(1), ""), segment)
        text = text.strip()
        if text:
            rendered_segments.append(text)

    stem = sanitise(" - ".join(rendered_segments)) or "document"
    ext = ext.lstrip(".").lower()
    if len(stem) > MAX_STEM_LEN:
        stem = stem[:MAX_STEM_LEN].rstrip(" .-")
    return f"{stem}.{ext}" if ext else stem


def sanitise(name: str) -> str:
    """Make ``name`` a safe Windows/macOS filename component (no extension)."""
    name = _ILLEGAL.sub("", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.rstrip(". ")  # Windows forbids trailing dot/space
    return name


def reference_folder(reference: str, index: int, total: int) -> str:
    """Folder name for one application in a batch, e.g. ``01. 87.03602.L``.

    The reference's slashes become dots (a slash is a path separator, so it can't
    live in a folder name), then the usual filename sanitiser strips anything
    else illegal. The 1-based ``index`` is zero-padded to at least two digits
    (to the width of ``total`` for larger batches) so the folders sort in the
    order the references were given.
    """
    width = max(2, len(str(total))) if total else 2
    safe_ref = sanitise(reference.replace("/", ".")) or "application"
    return f"{index:0{width}d}. {safe_ref}"


def dedupe(name: str, taken: set[str]) -> str:
    """Return ``name`` or ``name (2)`` etc. so it is unique within ``taken``.

    Comparison is case-insensitive because Windows filesystems are.
    """
    if name.lower() not in taken:
        taken.add(name.lower())
        return name

    stem, dot, ext = name.rpartition(".")
    base = stem if dot else name
    suffix = f".{ext}" if dot else ""
    n = 2
    while True:
        candidate = f"{base} ({n}){suffix}"
        if candidate.lower() not in taken:
            taken.add(candidate.lower())
            return candidate
        n += 1
