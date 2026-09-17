"""Verbose rejection reason for specific merge pairs, plus the corner split trace."""
import sys
import numpy as np
from types import SimpleNamespace
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
from xivgate_agr_collision.core import decompose as D

d = np.load(argv[1])
corner = np.array([float(x) for x in argv[2].split(",")])

splits = []
orig_split = D._split_piece_keyed


def split(piece, thin_limit, skip=0):
    low, high = piece.vertices.min(0), piece.vertices.max(0)
    key, children = orig_split(piece, thin_limit, skip)
    if np.all(corner >= low - 0.05) and np.all(corner <= high + 0.05):
        kids = [] if children is None else [
            (round(float(c.volume), 4), np.round(np.ptp(c.vertices, axis=0), 2).tolist())
            for c in children
        ]
        splits.append((round(float(piece.volume), 3),
                       np.round(np.ptp(piece.vertices, axis=0), 2).tolist(), kids))
    return key, children


D._split_piece_keyed = split
captured = {}
orig_merge = D._merge_within_tolerance


def merge(pieces, bvh, origin, tol, thin_limit=0.0):
    out = orig_merge(pieces, bvh, origin, tol, thin_limit)
    captured.update(pieces=out, bvh=bvh, origin=origin, tol=tol, thin=thin_limit)
    return out


D._merge_within_tolerance = merge
D.decompose(SimpleNamespace(vertices=d["v"], faces=d["f"]),
            SimpleNamespace(gap=0.0002, thin_threshold=0.05))

print("corner split trace (parent volume, parent size, children):")
for row in splits:
    print("   %.3f %s -> %s" % row)

pieces = captured["pieces"]
origin = captured["origin"]
tol = captured["tol"]
thin = captured["thin"]
bvh = captured["bvh"]


def analyse(i, j):
    a, b = pieces[i], pieces[j]
    limit = thin if min(D._minimum_width(a), D._minimum_width(b)) < thin else tol
    hull = D._convex_hull(np.vstack((a.vertices, b.vertices)), simplify=False)
    samples, normals = D._surface_samples(*hull)
    planes = [D._convex_planes(p) for p in (a, b)]
    inside = np.zeros(len(samples), dtype=bool)
    for pl in planes:
        N = np.asarray([n for _q, n in pl]).reshape((-1, 3))
        O = np.asarray([float(n @ q) for q, n in pl])
        inside |= np.all(samples @ N.T - O <= D.REFLEX_EPSILON, axis=1)
    bvhs = [D._surface_bvh(p.vertices - origin, p.faces) for p in (a, b)]
    deep = []
    covered = 0
    for point, normal in zip(samples[~inside] - origin, normals[~inside]):
        local = Vector(tuple(point))
        direction = Vector(tuple(-normal))
        depth = min((float(h[3]) for h in (
            bvhs[0].ray_cast(local, direction, 10.0),
            bvhs[1].ray_cast(local, direction, 10.0)) if h[0] is not None), default=np.inf)
        if depth == np.inf:
            depth = min((float(h[3]) for h in (
                bvhs[0].find_nearest(local), bvhs[1].find_nearest(local)) if h[0] is not None),
                default=np.inf)
        deep.append(depth)
        if D.PLANE_EPSILON < depth <= limit and D._inside_source(bvh, point):
            covered += 1
    deep = np.array(deep)
    print("pair %d+%d: limit %.3f, bridged samples %d, depth median %.4f p90 %.4f max %.4f, over limit %d, covering another piece %d"
          % (i, j, limit, len(deep), np.median(deep) if len(deep) else 0,
             np.percentile(deep, 90) if len(deep) else 0, deep.max() if len(deep) else 0,
             int((deep > limit).sum()), covered))


flat = [i for i, p in enumerate(pieces)
        if D._minimum_width(p) < 0.06 and float(np.ptp(p.vertices, axis=0).max()) > 1.0]
print("flat plates:", flat[:6])
for index in flat[:2]:
    piece = pieces[index]
    low, high = piece.vertices.min(0) - 0.05, piece.vertices.max(0) + 0.05
    for other in range(len(pieces)):
        if other == index:
            continue
        o = pieces[other]
        if np.any(o.vertices.max(0) < low) or np.any(o.vertices.min(0) > high):
            continue
        if o.volume < 0.05:
            continue
        analyse(index, other)
