"""How often does splitting fall back to bisector planes, and why?"""
import sys
import numpy as np
from types import SimpleNamespace

argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
from xivgate_agr_collision.core import decompose as D

d = np.load(argv[1])
stats = {"primary": 0, "fallback": 0, "none": 0, "primary_tried": 0, "fallback_pieces": []}
orig_cut = D._cut_piece
orig_split = D._split_piece_keyed
orig_fallback = D._fallback_planes
state = {"stage": "primary", "fails": 0}


def cut(piece, point, normal):
    result = orig_cut(piece, point, normal)
    if result is None:
        state["fails"] += 1
    return result


def fallback(piece, limit=8):
    state["stage"] = "fallback"
    return orig_fallback(piece, limit)


def split(piece, thin_limit, skip=0):
    state["stage"] = "primary"
    state["fails"] = 0
    key, children = orig_split(piece, thin_limit, skip)
    if children is None:
        stats["none"] += 1
    elif state["stage"] == "fallback":
        stats["fallback"] += 1
        stats["fallback_pieces"].append(
            (float(piece.volume), np.round(piece.vertices.mean(0), 2).tolist(), state["fails"])
        )
    else:
        stats["primary"] += 1
    stats["primary_tried"] += state["fails"]
    return key, children


D._cut_piece = cut
D._split_piece_keyed = split
D._fallback_planes = fallback
result = D.decompose(
    SimpleNamespace(vertices=d["v"], faces=d["f"]),
    SimpleNamespace(gap=0.0002, thin_threshold=0.05),
)
print("splits by facet planes:", stats["primary"], "| by fallback planes:", stats["fallback"], "| failed:", stats["none"])
print("total rejected cut attempts:", stats["primary_tried"])
rows = sorted(stats["fallback_pieces"], key=lambda r: -r[0])
print("pieces that needed the fallback (volume, centre, rejected attempts):")
for row in rows[:12]:
    print("   %.4f m3 at %s after %d rejected cuts" % (row[0], row[1], row[2]))
