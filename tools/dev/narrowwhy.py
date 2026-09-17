"""Needle triangles in the exact float64 output: which facets hold them and why.

    blender -b --python narrowwhy.py -- WT mesh.npz
"""
import sys
from types import SimpleNamespace
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
from xivgate_agr_collision.core import decompose as D, hull64 as H

data = np.load(argv[1])
captured = []
original = D._polytope_piece
def capture(vertices, depth=0, simplify=False):
    piece = original(vertices, depth, simplify)
    if simplify and piece is not None:
        captured.append(piece)
    return piece
D._polytope_piece = capture
D.decompose(SimpleNamespace(vertices=data["v"], faces=data["f"]), SimpleNamespace(gap=0.001, thin_threshold=0.05, attempts=1))

needles = []
for hull_index, piece in enumerate(captured):
    v = piece.vertices
    owner = {}
    for polygon in piece.polygons:
        for t in H.wide_triangles(v, [polygon]).tolist():
            owner[tuple(sorted(t))] = polygon
    for t in piece.faces.tolist():
        width = H._triangle_width(*v[t])
        if width < 0.01:
            polygon = owner[tuple(sorted(t))]
            needles.append((width, hull_index, len(polygon), H.facet_width(v, polygon),
                            float(np.linalg.norm(np.ptp(v[polygon], axis=0)))))
needles.sort()
print("hulls %d, triangles %d, triangles narrower than 10 mm: %d" % (len(captured), sum(len(p.faces) for p in captured), len(needles)))
kinds = {"narrow facet (<20 mm)": 0, "wide facet": 0}
for width, hull_index, corners, facet_w, extent in needles:
    kinds["narrow facet (<20 mm)" if facet_w < 0.02 else "wide facet"] += 1
print(kinds)
for row in needles[:12]:
    print("  tri %.3f mm | hull %d | facet %d corners, width %.2f mm, extent %.2f m" % (row[0] * 1000, row[1], row[2], row[3] * 1000, row[4]))
