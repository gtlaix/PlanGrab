"""Offline tests for the coverage-map geometry: WKT ring parsing, closed-ring
Douglas-Peucker simplification, and the end-to-end build. Run: python tests/test_build_map.py
"""
import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import build_map as bm

checks = 0


def eq(got, want, label):
    global checks
    checks += 1
    assert got == want, f"{label}:\n  got : {got!r}\n  want: {want!r}"


def test_rings_parse_polygon_and_multipolygon():
    poly = "POLYGON ((0.0 0.0, 1.0 0.0, 1.0 1.0, 0.0 0.0))"
    rings = bm._rings(poly)
    eq(len(rings), 1, "POLYGON: one ring")
    eq(rings[0][0], (0.0, 0.0), "POLYGON: first point")
    eq(len(rings[0]), 4, "POLYGON: point count")

    multi = "MULTIPOLYGON (((0 0, 2 0, 2 2, 0 0)), ((5 5, 6 5, 6 6, 5 5)))"
    eq(len(bm._rings(multi)), 2, "MULTIPOLYGON: two outer rings")


def test_simplify_closed_ring_not_collapsed():
    # A square with a redundant midpoint on one edge; DP should drop the midpoint
    # but keep the 4 corners and stay closed (the bug we fixed: closed rings
    # collapsing to 2 points).
    ring = [(0, 0), (1, 0), (2, 0), (2, 2), (0, 2), (0, 0)]  # (2,0)? midpoint at (1,0)
    simp = bm._simplify_ring(ring, tol=0.01)
    eq(simp[0] == simp[-1], True, "simplify: ring stays closed")
    assert len(simp) >= 4, f"simplify: keeps corners, got {len(simp)} points"
    checks_inc()
    # the collinear midpoint (1,0) should be gone
    eq((1, 0) in simp, False, "simplify: collinear midpoint dropped")


def checks_inc():
    global checks
    checks += 1


def test_douglas_peucker_straight_line():
    line = [(0, 0), (1, 0.0001), (2, 0), (3, 0)]
    eq(bm._douglas_peucker(line, tol=0.01), [(0, 0), (3, 0)], "DP: straight line -> endpoints")


def test_build_end_to_end():
    rows = [
        {"name": "Testshire LPA", "reference": "E60000001", "end-date": "", "geometry":
            "MULTIPOLYGON (((0 0, 10 0, 10 10, 0 10, 0 0)))"},
        {"name": "Oldgone LPA", "reference": "E60000002", "end-date": "2020-01-01", "geometry":
            "MULTIPOLYGON (((0 0, 1 0, 1 1, 0 0)))"},  # ended -> excluded
    ]
    with tempfile.TemporaryDirectory() as d:
        ds = Path(d) / "lpa.csv"
        with ds.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["name", "reference", "end-date", "geometry"])
            w.writeheader()
            w.writerows(rows)
        data = bm.build(ds)
    eq(len(data["features"]), 1, "build: excludes ended LPAs")
    feat = data["features"][0]
    eq(feat["name"], "Testshire", "build: strips ' LPA' suffix")
    eq(feat["norm"], "testshire", "build: normalised name")
    eq(feat["d"].startswith("M"), True, "build: SVG path starts with moveto")
    eq(data["viewBox"].startswith("0 0 1000"), True, "build: viewBox width 1000")


if __name__ == "__main__":
    test_rings_parse_polygon_and_multipolygon()
    test_simplify_closed_ring_not_collapsed()
    test_douglas_peucker_straight_line()
    test_build_end_to_end()
    print(f"OK — {checks} build_map checks passed.")
