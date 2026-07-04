"""Build a compact, self-contained SVG coverage map of England's LPAs.

Reads the planning.data.gov.uk LPA dataset (the same one tools/seed_registry.py
uses), extracts each authority's boundary, simplifies it (Douglas-Peucker, pure
Python — no shapely/geo deps), projects it to SVG coordinates, and writes
``data/lpa_boundaries.json``: a viewBox plus one path per LPA, keyed by a
normalised name so the dashboard can join it to compatibility status.

No internet/tiles at runtime — the dashboard renders these paths as inline SVG,
which matters on the locked-down offline PC.

    python tools/build_map.py --input /path/to/local-planning-authority.csv
    python tools/build_map.py            # download the dataset if needed
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plangrab.engine.compat import normalise_name

OUT = Path(__file__).resolve().parent.parent / "data" / "lpa_boundaries.json"
WIDTH = 1000.0          # SVG viewBox width; height derived to preserve aspect
SIMPLIFY_TOL = 0.0025   # Douglas-Peucker tolerance in degrees (~250 m)
MIN_RING_PTS = 4        # drop slivers below this after simplification


def _rings(wkt: str) -> list[list[tuple[float, float]]]:
    """Every ring (outer + holes) of a POLYGON/MULTIPOLYGON WKT.

    We keep holes and rely on the dashboard rendering each LPA's path with
    ``fill-rule="evenodd"``, which makes inner rings punch holes correctly. This
    is robust to both POLYGON and MULTIPOLYGON nesting.
    """
    rings = []
    for grp in re.findall(r"\(([^()]+)\)", wkt):
        pts = [(float(a), float(b))
               for a, b in re.findall(r"(-?\d+\.?\d*) (-?\d+\.?\d*)", grp)]
        if pts:
            rings.append(pts)
    return rings


def _douglas_peucker(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    if len(pts) < 3:
        return pts
    # Find the point farthest from the line between the endpoints.
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2 - x1, y2 - y1
    denom = math.hypot(dx, dy) or 1e-12
    dmax, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        x0, y0 = pts[i]
        d = abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / denom
        if d > dmax:
            dmax, idx = d, i
    if dmax > tol:
        left = _douglas_peucker(pts[: idx + 1], tol)
        right = _douglas_peucker(pts[idx:], tol)
        return left[:-1] + right
    return [pts[0], pts[-1]]


def _simplify_ring(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker for a closed ring (first==last), split at the farthest
    vertex so the algorithm has a non-degenerate baseline."""
    if len(pts) < 4:
        return pts
    if pts[0] == pts[-1]:
        x0, y0 = pts[0]
        k = max(range(len(pts)), key=lambda i: (pts[i][0] - x0) ** 2 + (pts[i][1] - y0) ** 2)
        ring = _douglas_peucker(pts[: k + 1], tol)[:-1] + _douglas_peucker(pts[k:], tol)
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        return ring
    return _douglas_peucker(pts, tol)


def build(dataset: Path) -> dict:
    csv.field_size_limit(sys.maxsize)
    features = []
    all_pts: list[tuple[float, float]] = []
    with dataset.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("end-date") or not row.get("geometry"):
                continue
            name = re.sub(r"\s+LPA$", "", (row.get("name") or "").strip())
            if not name:
                continue
            rings = []
            for ring in _rings(row["geometry"]):
                simp = _simplify_ring(ring, SIMPLIFY_TOL)
                if len(simp) >= MIN_RING_PTS:
                    rings.append(simp)
                    all_pts.extend(simp)
            if rings:
                features.append({"name": name, "norm": normalise_name(name),
                                 "ref": (row.get("reference") or "").strip(), "rings": rings})

    # Equirectangular projection with longitude compressed by cos(mean latitude).
    lat0 = sum(p[1] for p in all_pts) / len(all_pts)
    k = math.cos(math.radians(lat0))
    xs = [p[0] * k for p in all_pts]
    ys = [p[1] for p in all_pts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    scale = WIDTH / (maxx - minx)
    height = round((maxy - miny) * scale, 1)

    def project(lon, lat):
        return (round((lon * k - minx) * scale, 1), round((maxy - lat) * scale, 1))

    out_features = []
    for feat in features:
        parts = []
        for ring in feat["rings"]:
            pts = [project(lon, lat) for lon, lat in ring]
            parts.append("M" + " ".join(f"{x},{y}" for x, y in pts) + "Z")
        out_features.append({"name": feat["name"], "norm": feat["norm"],
                             "ref": feat["ref"], "d": "".join(parts)})

    return {"viewBox": f"0 0 {WIDTH:.0f} {height:.0f}", "features": out_features}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Build the LPA coverage map boundaries.")
    p.add_argument("--input", help="local-planning-authority.csv (else download)")
    p.add_argument("--out", default=str(OUT))
    args = p.parse_args(argv)

    if args.input:
        dataset = Path(args.input)
    else:
        from tools.seed_registry import fetch_dataset
        dataset = fetch_dataset(Path(__file__).resolve().parent.parent / "data" / "_lpa_dataset.csv")

    data = build(dataset)
    Path(args.out).write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    size_kb = Path(args.out).stat().st_size / 1024
    print(f"Wrote {len(data['features'])} LPA boundaries -> {args.out} "
          f"({size_kb:.0f} KB, viewBox {data['viewBox']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
