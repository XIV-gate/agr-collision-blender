"""Final hull count against search budget and heavy-node threshold."""
import sys, time, numpy as np
from types import SimpleNamespace
argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
from xivgate_agr_collision.core import decompose as D

d = np.load(argv[1])
src = SimpleNamespace(vertices=d["v"], faces=d["f"])
print("%-6s %8s %9s %7s %8s %8s" % ("share", "passes", "variants", "hulls", "tris", "time"))
for share in (0.25,):
    for attempts in (34, 100):
        D.SEARCH_WORK_SHARE = share
        t = time.time()
        r = D._run_attempt(src, SimpleNamespace(gap=0.0002, thin_threshold=0.05, attempts=attempts))
        print("%-6.2f %8d %9d %7d %8d %7.1fs" % (share, attempts, (attempts - 1) * D.REFINEMENT_TRIES,
              len(r.hulls), r.total_triangles, time.time() - t), flush=True)
