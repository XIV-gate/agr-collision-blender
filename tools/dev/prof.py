import sys, cProfile, pstats, numpy as np
from types import SimpleNamespace
argv = sys.argv[sys.argv.index("--")+1:]
sys.path.insert(0, argv[0])
from xivgate_agr_collision.core import decompose as D
d = np.load(argv[1])
src = SimpleNamespace(vertices=d["v"], faces=d["f"])
settings = SimpleNamespace(gap=0.0002, thin_threshold=0.05)
cProfile.run("D._run_attempt(src, settings)", argv[2])
st = pstats.Stats(argv[2]); st.sort_stats("cumulative").print_stats(25)
