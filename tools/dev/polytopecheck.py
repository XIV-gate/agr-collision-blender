"""Check hull64.planar_polytope (system Python, no Blender).

    python polytopecheck.py WT hulls.npz

Volume against qhull, closed and consistently oriented polygon surface,
Euler characteristic 2, and the face-plane convexity test SINTEZ AGR Checker
uses, evaluated after float32 rounding about a pivot.
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import ConvexHull

root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("hull64", root / "xivgate_agr_collision/core/hull64.py")
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


def surface_ok(vertices, polygons):
    directed = {}
    for polygon in polygons:
        for k in range(len(polygon)):
            edge = (polygon[k], polygon[(k + 1) % len(polygon)])
            if edge in directed:
                return False, "duplicate directed edge"
            directed[edge] = True
    for a, b in directed:
        if (b, a) not in directed:
            return False, "open edge"
    edges = len(directed) // 2
    euler = len(vertices) - edges + len(polygons)
    return euler == 2, "euler %d" % euler


def volume(vertices, polygons):
    tris = H.fan_triangles(polygons)
    p = vertices[tris]
    return np.einsum("tk,tk->t", p[:, 0], np.cross(p[:, 1], p[:, 2])).sum() / 6.0


def newell(points):
    following = np.roll(points, -1, axis=0)
    n = np.array([
        np.sum((points[:, 1] - following[:, 1]) * (points[:, 2] + following[:, 2])),
        np.sum((points[:, 2] - following[:, 2]) * (points[:, 0] + following[:, 0])),
        np.sum((points[:, 0] - following[:, 0]) * (points[:, 1] + following[:, 1])),
    ])
    return n


def sintez_bad_faces(vertices, polygons, tolerance):
    """Face-plane convexity test in the manner of SINTEZ AGR Checker."""
    bad = 0
    for polygon in polygons:
        pts = vertices[polygon]
        n = newell(pts)
        length = np.linalg.norm(n)
        if length == 0:
            continue
        n /= length
        area = 0.5 * length
        longest = max(np.linalg.norm(pts[(k + 1) % len(pts)] - pts[k]) for k in range(len(pts)))
        if longest <= 0 or 2 * area / longest < tolerance:
            continue
        center = pts.mean(axis=0)
        others = np.delete(vertices, polygon, axis=0)
        d = (others - center) @ n
        if (d > tolerance).any() and (d < -tolerance).any():
            bad += 1
    return bad


g = np.linspace(-1, 1, 5)
cube = np.array([(x, y, z) for x in g for y in g for z in g if max(abs(x), abs(y), abs(z)) == 1.0])
cube = cube + np.random.default_rng(0).normal(size=cube.shape) * 1e-8
v, polys = H.planar_polytope(cube, 1e-5)
print("cube with face/edge points: %d vertices, %d polygons, sizes %s, surface %s" % (
    len(v), len(polys), sorted(len(p) for p in polys), surface_ok(v, polys)))

rng = np.random.default_rng(2)
worst = 0.0
for trial in range(30):
    pts = rng.normal(size=(150, 3)) * [20, 4, 1] + [80, -40, 10]
    v, polys = H.planar_polytope(pts, 1e-9)
    ok, why = surface_ok(v, polys)
    rel = abs(volume(v, polys) - ConvexHull(pts).volume) / ConvexHull(pts).volume
    worst = max(worst, rel)
    if not ok or volume(v, polys) <= 0:
        print("cloud %d surface %s volume %s" % (trial, why, volume(v, polys)))
print("random clouds: worst relative volume error %.1e" % worst)

d = np.load(sys.argv[2])
n = len(d.files) // 2
pivot = np.array([-48.0, 73.0, 0.0])
tri_before = tri_after = 0
bad_tri = {0.01: 0, 0.001: 0, 0.0001: 0}
bad_poly = {0.01: 0, 0.001: 0, 0.0001: 0}
bad_fan = {0.01: 0, 0.001: 0, 0.0001: 0}
bad_wide = {0.01: 0, 0.001: 0, 0.0001: 0}
tri_wide = 0
worst = 0.0
for i in range(n):
    verts = d["v%d" % i].astype(np.float64)
    faces = d["f%d" % i].astype(np.int64)
    tri_before += len(faces)
    rounded = (verts - pivot).astype(np.float32).astype(np.float64) + pivot
    for tol in bad_tri:
        bad_tri[tol] += int(sintez_bad_faces(rounded, faces.tolist(), tol) > 0)
    v, polys = H.planar_polytope(verts, float(sys.argv[3]) if len(sys.argv) > 3 else 1e-5)
    ok, why = surface_ok(v, polys)
    if not ok:
        print("hull %d: %s" % (i, why))
    tri_after += len(H.fan_triangles(polys))
    hull_volume = ConvexHull(verts[np.unique(faces)]).volume
    worst = max(worst, abs(volume(v, polys) - hull_volume) / hull_volume)
    rv = (v - pivot).astype(np.float32).astype(np.float64) + pivot
    for tol in bad_poly:
        bad_poly[tol] += int(sintez_bad_faces(rv, polys, tol) > 0)
    fan = H.fan_triangles(polys).tolist()
    wide = H.wide_triangles(v, polys).tolist()
    tri_wide += len(wide)
    for tol in bad_fan:
        bad_fan[tol] += int(sintez_bad_faces(rv, fan, tol) > 0)
        bad_wide[tol] += int(sintez_bad_faces(rv, wide, tol) > 0)
print("tower hulls: worst relative volume vs exact hull %.1e" % worst)
print("triangles: %d before, %d after" % (tri_before, tri_after))
print("wide triangulation: %d triangles" % tri_wide)
print("hulls rejected by SINTEZ-style rule after float32 rounding:")
print("%-9s %12s %9s %12s %14s" % ("tolerance", "old output", "polygons", "fan triangles", "wide triangles"))
for tol in (0.01, 0.001, 0.0001):
    print("%-9s %12d %9d %12d %14d" % ("%.1f mm" % (tol * 1000), bad_tri[tol], bad_poly[tol], bad_fan[tol], bad_wide[tol]))
