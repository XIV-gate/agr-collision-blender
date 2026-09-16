"""Check core/hull64.py against scipy qhull (system Python, no Blender).

    python hull64check.py WT hulls.npz

Random clouds, a coplanar surface grid, the given hulls in float64, and the
same hulls after float32 storage relative to a pivot, as Blender holds them.
"""
import importlib.util
import sys
import time
from pathlib import Path

import numpy as np
from scipy.spatial import ConvexHull

root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location(
    "hull64", root / "xivgate_agr_collision/core/hull64.py")
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


def volume(points, tris):
    p = points[tris]
    return abs(np.einsum("tk,tk->t", p[:, 0], np.cross(p[:, 1], p[:, 2])).sum()) / 6.0


def outside(points, tris):
    p = points[tris]
    n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    ln = np.linalg.norm(n, axis=1)
    ok = ln > 0
    n = n[ok] / ln[ok, None]
    o = np.einsum("tk,tk->t", n, p[ok, 0])
    return float((points @ n.T - o).max())


def closed(tris):
    e = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    fwd = {tuple(x) for x in e.tolist()}
    return len(fwd) == len(e) and all((b, a) in fwd for a, b in fwd)


def depth(v, f, tris_of):
    used = np.unique(f)
    pts = v[used]
    edges = np.unique(np.sort(np.concatenate(
        [f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1), axis=0)
    samples = np.vstack([pts, 0.5 * (v[edges[:, 0]] + v[edges[:, 1]]), v[f].mean(axis=1)])
    tris = tris_of(pts)
    return pts, tris, H.distance_to_triangles(samples, *[pts[tris[:, k]] for k in range(3)]).max()


rng = np.random.default_rng(1)
clouds = [("gaussian", rng.normal(size=(200, 3)) * [30, 5, 1] + [100, -60, 20]) for _ in range(20)]
g = np.linspace(-1, 1, 9)
grid = np.array([(x, y, z) for x in g for y in g for z in g if max(abs(x), abs(y), abs(z)) == 1.0])
clouds.append(("surface grid", grid * [20, 3, 0.5] + [-48, 73, 5]))
clouds.append(("grid jitter 1e-9", grid + rng.normal(size=grid.shape) * 1e-9))
clouds.append(("grid jitter 1e-5", grid * 30 + rng.normal(size=grid.shape) * 1e-5))
worst_vol = worst_out = 0.0
for name, pts in clouds:
    tris = H.convex_hull(pts)
    ref = ConvexHull(pts)
    rel = abs(volume(pts, tris) - ref.volume) / ref.volume
    worst_vol = max(worst_vol, rel)
    worst_out = max(worst_out, outside(pts, tris))
    if not closed(tris) or rel > 1e-9:
        print("MISMATCH", name, "closed", closed(tris), "rel vol", rel)
print("clouds: worst relative volume error %.1e, worst outside %.1e m" % (worst_vol, worst_out))

d = np.load(sys.argv[2])
n = len(d.files) // 2
pivot = np.array([-48.0, 73.0, 0.0])
for label, transform in (
    ("float64", lambda v: v),
    ("float32 about pivot", lambda v: (v - pivot).astype(np.float32).astype(np.float64) + pivot),
    ("float32 world", lambda v: v.astype(np.float32).astype(np.float64)),
):
    t = time.time()
    worst = worst_vol = worst_out = 0.0
    depths = []
    for i in range(n):
        v = transform(d["v%d" % i].astype(np.float64))
        f = d["f%d" % i].astype(np.int64)
        pts, tris, ours = depth(v, f, H.convex_hull)
        _pts, ref_tris, theirs = depth(v, f, lambda p: ConvexHull(p).simplices)
        worst = max(worst, abs(ours - theirs))
        worst_vol = max(worst_vol, abs(volume(pts, tris) - ConvexHull(pts).volume) / ConvexHull(pts).volume)
        worst_out = max(worst_out, outside(pts, tris))
        if not closed(tris):
            print("  hull %d not closed" % i)
        depths.append(ours)
    depths = np.array(depths)
    print("%-20s %.1fs  max|depth-qhull| %.2e m  worst rel vol %.1e  max outside %.2e m  "
          "depth>1e-5: %d  >1e-4: %d  >1e-3: %d"
          % (label, time.time() - t, worst, worst_vol, worst_out,
             (depths > 1e-5).sum(), (depths > 1e-4).sum(), (depths > 1e-3).sum()))
