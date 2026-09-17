"""Where do micro-concavities enter the output? Measured after each late stage."""
import sys, numpy as np
from types import SimpleNamespace
argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
from xivgate_agr_collision.core import decompose as D, hull64 as H

def depth(piece):
    v = np.asarray(piece.vertices, dtype=np.float64); f = np.asarray(piece.faces, dtype=np.int64)
    if len(f) == 0: return 0.0
    used = np.unique(f); pts = v[used]
    tris = H.convex_hull(pts)
    if len(tris) == 0: return 0.0
    edges = np.unique(np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1), axis=0)
    s = np.vstack([pts, 0.5 * (v[edges[:, 0]] + v[edges[:, 1]]), v[f].mean(axis=1)])
    return float(H.distance_to_triangles(s, *[pts[tris[:, k]] for k in range(3)]).max())

def stats(label, pieces):
    d = np.array([depth(p) for p in pieces])
    print("%-34s n=%3d  >1um %3d  >10um %3d  >0.1mm %3d  max %.4f mm" % (label, len(d), (d > 1e-6).sum(), (d > 1e-5).sum(), (d > 1e-4).sum(), d.max() * 1000 if len(d) else 0))

orig_inset = D._inset_convex_piece
orig_sep = D._separate_touching_pieces
calls = {"sep": 0}
def sep(pieces, *a, **k):
    calls["sep"] += 1
    stats("before separate #%d" % calls["sep"], pieces)
    out = orig_sep(pieces, *a, **k)
    stats("after separate #%d" % calls["sep"], out)
    return out
def inset_all(piece, distance):
    return orig_inset(piece, distance)
D._separate_touching_pieces = sep
orig_absorb = D._absorb_thin_pieces
def absorb(*a, **k):
    out = orig_absorb(*a, **k)
    stats("after merge+absorb (kept)", out)
    return out
D._absorb_thin_pieces = absorb
orig_merge = D._merge_within_tolerance
def merge(pieces, *a, **k):
    stats("cut leaves (before merge)", pieces)
    out = orig_merge(pieces, *a, **k)
    stats("after merge", out)
    return out
D._merge_within_tolerance = merge

d = np.load(argv[1])
r = D.decompose(SimpleNamespace(vertices=d["v"], faces=d["f"]), SimpleNamespace(gap=float(argv[2]) if len(argv) > 2 else 0.0002, thin_threshold=0.05, attempts=1))
stats("final output hulls", [D.Piece(vertices=v, faces=f) for v, f in r.hulls])
