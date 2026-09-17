"""Count reflex edges: the theoretical driver of convex piece count."""
import sys, numpy as np
argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
from xivgate_agr_collision.core import decompose as D

d = np.load(argv[1]); v, f = d["v"], d["f"]
D._feature_tolerance = 0.02
topo = D._topology(len(v), f)
normals, _areas = D._face_normals(v, f)
depths = D._edge_fold_depths(v, topo, normals)
for limit, label in ((0.0, "any"), (0.001, ">1mm"), (0.02, ">2cm (our tolerance)"), (0.05, ">5cm")):
    print("reflex edges %-22s %d" % (label, int((depths > limit).sum())))
print("manifold edges total %d" % len(topo.first))
