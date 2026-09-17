"""Why were nearly-convex neighbour pairs not merged?"""
import sys
import numpy as np
from types import SimpleNamespace
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
from xivgate_agr_collision.core import decompose as D

d = np.load(argv[1])
captured = {}
orig = D._merge_within_tolerance


def capture(pieces, bvh, origin, tol):
    captured["in"] = list(pieces)
    out = orig(pieces, bvh, origin, tol)
    captured["out"] = list(out)
    captured["bvh"] = bvh
    captured["origin"] = origin
    captured["tol"] = tol
    return out


D._merge_within_tolerance = capture
D.decompose(SimpleNamespace(vertices=d["v"], faces=d["f"]),
            SimpleNamespace(gap=0.0002, thin_threshold=0.05))
pieces = captured["out"]
origin = captured["origin"]
tol = captured["tol"]
source_bvh = captured["bvh"]
print("pieces after merge:", len(pieces), "tolerance", tol)

info = []
for piece in pieces:
    planes = D._convex_planes(piece)
    info.append((
        np.asarray([n for _q, n in planes]).reshape((-1, 3)),
        np.asarray([float(n @ q) for q, n in planes]),
        D._surface_bvh(piece.vertices - origin, piece.faces),
        piece.vertices.min(0), piece.vertices.max(0),
    ))

def why(i, j):
    a, b = pieces[i], pieces[j]
    hull = D._convex_hull(np.vstack((a.vertices, b.vertices)), simplify=False)
    if hull is None:
        return "no hull", 0.0
    added = D._signed_volume(*hull) - a.volume - b.volume
    area_a = float(D._face_normals(a.vertices, a.faces)[1].sum())
    area_b = float(D._face_normals(b.vertices, b.faces)[1].sum())
    if added > tol * min(area_a, area_b):
        return "added volume %.3f m3 > film %.3f" % (added, tol * min(area_a, area_b)), added
    samples, normals = D._surface_samples(*hull)
    inside = np.zeros(len(samples), dtype=bool)
    for k in (i, j):
        N, O = info[k][0], info[k][1]
        if len(N):
            inside |= np.all(samples @ N.T - O <= D.REFLEX_EPSILON, axis=1)
    deep = 0.0
    covered = 0
    for point, normal in zip(samples[~inside] - origin, normals[~inside]):
        local = Vector(tuple(point))
        direction = Vector(tuple(-normal))
        depth = min((float(hit[3]) for hit in (
            info[i][2].ray_cast(local, direction, tol),
            info[j][2].ray_cast(local, direction, tol)) if hit[0] is not None), default=np.inf)
        if depth > tol:
            deep += 1
        elif depth > D.PLANE_EPSILON and D._inside_source(source_bvh, point):
            covered += 1
    if deep:
        return "%d/%d hull samples deeper than tolerance" % (deep, int((~inside).sum())), added
    if covered:
        return "%d hull samples would cover another piece" % covered, added
    return "MERGEABLE?", added

reasons = {}
pairs = 0
for i in range(len(pieces)):
    for j in range(i + 1, len(pieces)):
        if np.any(info[i][4] < info[j][3] - tol) or np.any(info[j][4] < info[i][3] - tol):
            continue
        a, b = pieces[i], pieces[j]
        hull = D._convex_hull(np.vstack((a.vertices, b.vertices)), simplify=False)
        if hull is None:
            continue
        added = D._signed_volume(*hull) - a.volume - b.volume
        if added > 0.02 * (a.volume + b.volume):
            continue        # union is far from convex: a real cut
        pairs += 1
        reason, value = why(i, j)
        key = reason.split(" ")[0] if "samples" not in reason else " ".join(reason.split(" ")[1:])
        reasons.setdefault(key, []).append((round(value, 4), i, j))
print("near-convex touching pairs:", pairs)
for key, items in sorted(reasons.items(), key=lambda kv: -len(kv[1])):
    print("  %-45s %4d   e.g. %s" % (key, len(items), items[:3]))
