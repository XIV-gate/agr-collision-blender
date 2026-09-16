import sys, numpy as np
argv = sys.argv[sys.argv.index("--")+1:]
sys.path.insert(0, argv[0])
from xivgate_agr_collision.core import decompose as D
H = np.load(argv[1])
pieces = [D._analyse_piece(D.Piece(vertices=H["v%d"%i].astype(np.float32).astype(np.float64), faces=H["f%d"%i])) for i in range(len(H.files)//2)]
bounds = [(p.vertices.min(0), p.vertices.max(0)) for p in pieces]
bad = []
for i in range(len(pieces)):
    for j in range(i+1, len(pieces)):
        (a0, a1), (b0, b1) = bounds[i], bounds[j]
        if np.any(a1 < b0) or np.any(b1 < a0): continue
        if D._pieces_overlap(pieces[i], pieces[j], clearance=1e-7):
            # depth: max over samples of i inside j
            pl = D._convex_planes(pieces[j]); N = np.array([n for _q, n in pl]); O = np.array([n @ q for q, n in pl])
            s = D._convex_test_samples(pieces[i]); depth = (O - s @ N.T).min(1).max()
            pl2 = D._convex_planes(pieces[i]); N2 = np.array([n for _q, n in pl2]); O2 = np.array([n @ q for q, n in pl2])
            s2 = D._convex_test_samples(pieces[j]); depth2 = (O2 - s2 @ N2.T).min(1).max()
            bad.append((i, j, max(depth, depth2), pieces[i].vertices.mean(0).round(2)))
print("overlapping pairs", len(bad))
for b in bad[:10]: print("  pair", b[0], b[1], "penetration %.5f m" % b[2], "near", b[3])
