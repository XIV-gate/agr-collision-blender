"""Log every variant of the search: node size, depth, cost, gain."""
import sys, time, numpy as np
from types import SimpleNamespace
argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
from xivgate_agr_collision.core import decompose as D

log = []
original = D._refine_tree

def logged(roots, thin_limit, passes):
    attempts = 0
    start = time.time()
    while attempts < passes:
        limit = D.SEARCH_WORK_SHARE * sum(r_.work for r_ in roots)
        candidates = []
        for root in roots:
            candidates += [n for n in root.internal_nodes() if not n.exhausted and n.count > 2 and n.work <= limit]
        if not candidates:
            break
        node = max(candidates, key=D._search_priority)
        tried = node.__dict__.setdefault("tried", {node.cut_key})
        skip = node.__dict__.get("next_skip", node.skip) + 1
        node.next_skip = skip
        if skip > D.SEARCH_MAX_SKIP:
            node.exhausted = True; continue
        t = time.time()
        key, children = D._split_piece_keyed(node.piece, thin_limit, skip=skip)
        split_time = time.time() - t
        if children is None:
            node.exhausted = True; continue
        if key in tried:
            continue
        tried.add(key); attempts += 1
        depth, walker = 0, node
        while walker.parent is not None:
            depth += 1; walker = walker.parent
        t = time.time()
        variant = D._grow_node(D._Node(piece=node.piece, skip=skip), key, children, thin_limit, [D.LEAF_LIMIT])
        grow_time = time.time() - t
        gain = 0
        if not variant.invalid and variant.score < node.score:
            gain = node.count - variant.count
            variant.parent = node.parent; variant.tried = tried; variant.next_skip = skip
            if node.parent is None:
                roots[roots.index(node)] = variant
            else:
                node.parent.children[node.parent.children.index(node)] = variant
        log.append((attempts, node.count, len(node.piece.faces), depth, skip, split_time, grow_time, gain, time.time() - start))
    return roots, 0, attempts

D._refine_tree = logged
d = np.load(argv[1])
r = D._run_attempt(SimpleNamespace(vertices=d["v"], faces=d["f"]),
                   SimpleNamespace(gap=0.0002, thin_threshold=0.05, attempts=int(argv[2]) if len(argv) > 2 else 100))
L = np.asarray(log)
print("hulls", len(r.hulls), "variants", len(L))
print("idx count faces depth skip split_s grow_s gain elapsed (costliest 15)")
for row in L[np.argsort(-(L[:, 5] + L[:, 6]))][:15]:
    print("%4d %5d %5d %5d %4d %7.2f %7.2f %4d %7.1f" % tuple(row))
cost = L[:, 5] + L[:, 6]
print("total split %.1fs grow %.1fs" % (L[:, 5].sum(), L[:, 6].sum()))
for label, mask in (("gain>0", L[:, 7] > 0), ("gain=0", L[:, 7] == 0)):
    print("%s: %d variants, %.1fs" % (label, mask.sum(), cost[mask].sum()))
for lo, hi in ((0, 2), (2, 5), (5, 10), (10, 99)):
    m = (L[:, 3] >= lo) & (L[:, 3] < hi)
    print("depth %d-%d: %d variants %.1fs gain %d" % (lo, hi - 1, m.sum(), cost[m].sum(), L[m, 7].sum()))
for lim in (100, 300, 500, 800, 1200, 2000, 5000):
    m = L[:, 0] <= lim
    if m.sum() == 0 or lim > len(L) + 300:
        break
    print("first %d variants: %.1fs gain %d" % (lim, cost[m].sum(), L[m, 7].sum()))
for lo, hi in ((1, 3), (3, 6), (6, 12), (12, 25)):
    m = (L[:, 4] >= lo) & (L[:, 4] < hi)
    print("skip %d-%d: %d variants %.1fs gain %d" % (lo, hi - 1, m.sum(), cost[m].sum(), L[m, 7].sum()))
