"""How much of the cut search actually pays for itself?"""
import sys, time, numpy as np
from types import SimpleNamespace
argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
from xivgate_agr_collision.core import decompose as D

d = np.load(argv[1])
src = SimpleNamespace(vertices=d["v"], faces=d["f"])
settings = SimpleNamespace(gap=0.0002, thin_threshold=0.05)
print("%-10s %-9s %7s %8s %8s %8s" % ("lookahead", "attempts", "hulls", "tris", "thin", "time"))
for lookahead, attempts in ((12, 32), (8, 24), (6, 16), (4, 12), (3, 8), (2, 6), (1, 4)):
    D.CUT_LOOKAHEAD = lookahead
    D.MAX_CUT_ATTEMPTS = attempts
    t = time.time()
    r = D._run_attempt(src, settings)
    thin = sum(1 for w in r.warnings if "thinner" in w)
    print("%-10d %-9d %7d %8d %8s %7.1fs"
          % (lookahead, attempts, len(r.hulls), r.total_triangles,
             "yes" if thin else "no", time.time() - t), flush=True)
