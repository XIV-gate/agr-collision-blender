import sys, time, numpy as np
from types import SimpleNamespace
argv = sys.argv[sys.argv.index("--")+1:]
sys.path.insert(0, argv[0])
from xivgate_agr_collision.core import decompose as D
d = np.load(argv[1])
t = time.time()
r = D.decompose(SimpleNamespace(vertices=d["v"], faces=d["f"]), SimpleNamespace(gap=float(argv[4]) if len(argv) > 4 else 0.001, thin_threshold=0.05, attempts=int(argv[3]) if len(argv) > 3 else 1))
print("TIME %.1fs complete %s hulls %d tris %d maxdev %.3f tol %.3f" % (time.time()-t, r.complete, len(r.hulls), r.total_triangles, r.max_deviation, r.feature_tolerance))
for w in r.warnings: print("WARN", w)
def save(path, items):
    np.savez(path, **{"v%d"%i: v for i,(v,f) in enumerate(items)}, **{"f%d"%i: f for i,(v,f) in enumerate(items)})
save(argv[2] + "_hulls.npz", r.hulls); save(argv[2] + "_thin.npz", r.ignored_parts); save(argv[2] + "_invalid.npz", r.failed_parts)
