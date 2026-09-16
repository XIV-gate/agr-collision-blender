"""Audit every accepted hull absorption against a dense winding test."""
import sys
import numpy as np
from types import SimpleNamespace

argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
from xivgate_agr_collision.core import decompose as D

d = np.load(argv[1])
sv, sf = d["v"], d["f"]
A, B, C = sv[sf[:, 0]], sv[sf[:, 1]], sv[sf[:, 2]]


def winding(points):
    out = []
    for start in range(0, len(points), 300):
        q = points[start:start + 300][:, None, :]
        x, y, z = A[None] - q, B[None] - q, C[None] - q
        lx, ly, lz = (np.sqrt((u * u).sum(2)) for u in (x, y, z))
        det = (x * np.cross(y, z)).sum(2)
        den = lx * ly * lz + (x * y).sum(2) * lz + (y * z).sum(2) * lx + (z * x).sum(2) * ly
        out.append(np.arctan2(det, den).sum(1) / (2 * np.pi))
    return np.concatenate(out) if out else np.zeros(0)


records = []
orig = D._absorbable_hull


def absorb(piece, source_bvh, origin, tolerance, thin_limit):
    result = orig(piece, source_bvh, origin, tolerance, thin_limit)
    if result is not None:
        added = result.volume - piece.volume
        # dense audit: random points inside the hull, outside the piece
        low, high = result.vertices.min(0), result.vertices.max(0)
        rng = np.random.default_rng(0)
        pts = rng.uniform(low, high, size=(4000, 3))
        planes = D._convex_planes(result)
        N = np.asarray([n for _q, n in planes]).reshape((-1, 3))
        O = np.asarray([float(n @ q) for q, n in planes])
        inside_hull = np.all(pts @ N.T - O <= 0, axis=1)
        pts = pts[inside_hull]
        if len(pts):
            outside_source = winding(pts) < 0.5
            box = float(np.prod(high - low))
            excess = outside_source.mean() * box * len(pts) / max(inside_hull.sum(), 1) if False else outside_source.sum() / 4000.0 * box
            records.append((excess, float(piece.volume), float(added),
                            np.round(piece.vertices.mean(0), 1).tolist(),
                            np.round(np.ptp(piece.vertices, axis=0), 2).tolist()))
    return result


D._absorbable_hull = absorb
result = D.decompose(SimpleNamespace(vertices=sv, faces=sf),
                     SimpleNamespace(gap=0.0002, thin_threshold=0.05))
records.sort(reverse=True)
print("absorbed pieces:", len(records), "| total audited excess %.3f m3" % sum(r[0] for r in records))
print("worst (audited excess outside source, piece volume, hull added, centre, size):")
for row in records[:10]:
    print("   %.4f m3 | vol %.3f | added %.4f | %s | %s" % row)
