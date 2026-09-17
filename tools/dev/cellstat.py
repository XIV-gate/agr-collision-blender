"""Quality of a cell population: wafers, aspect ratios, where the volume is."""
import sys, numpy as np
argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
from xivgate_agr_collision.core import decompose as D

f = np.load(argv[1])
D._feature_tolerance = 0.02
rows = []
for i in range(len(f.files) // 2):
    v, t = f["v%d" % i], f["f%d" % i]
    if len(t) == 0:
        continue
    p = D.Piece(vertices=np.asarray(v, dtype=np.float64), faces=t)
    ext = np.sort(D._piece_extents(p))          # thin -> long
    vol = abs(D._signed_volume(p.vertices, np.asarray(D._orient_outward(p.vertices, t))))
    rows.append((vol, ext[0], ext[1], ext[2], len(t)))
rows = np.asarray(rows)
vol, thin, mid, long_, tris = rows.T
total = vol.sum()
print("cells %d | triangles %d | volume %.1f" % (len(rows), int(tris.sum()), total))
order = np.argsort(-vol)
cum = np.cumsum(vol[order]) / total
for share in (0.5, 0.8, 0.9, 0.99):
    print("  %.0f%% of volume in %d cells" % (share * 100, int(np.searchsorted(cum, share)) + 1))
print("thinnest extent: <1cm %d  <5cm %d  <10cm %d  <20cm %d"
      % ((thin < 0.01).sum(), (thin < 0.05).sum(), (thin < 0.10).sum(), (thin < 0.20).sum()))
aspect = long_ / np.maximum(thin, 1e-9)
print("aspect long/thin:  >50 %d   >200 %d   >1000 %d   max %.0f"
      % ((aspect > 50).sum(), (aspect > 200).sum(), (aspect > 1000).sum(), aspect.max()))
tiny = vol < 1e-4
print("near-zero volume (<0.0001 m3): %d cells, %d triangles" % (tiny.sum(), int(tris[tiny].sum())))
print("cells carrying <0.1%% of volume each: %d (%.2f%% of volume, %d triangles)"
      % ((vol < total * 1e-3).sum(), 100 * vol[vol < total * 1e-3].sum() / total,
         int(tris[vol < total * 1e-3].sum())))
print("triangles in the 20 biggest cells: %d" % int(tris[order[:20]].sum()))
