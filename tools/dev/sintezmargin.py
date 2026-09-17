"""How close is a generated set to failing SINTEZ convexity, and why.

For every hull: the largest checker tolerance at which it would still be
rejected, i.e. max over faces of min(face width, smaller of the two
distances beyond the face plane). A set passes a tolerance T when that
value is below T for every hull.
"""
import sys
import numpy as np
import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
import xivgate_agr_collision as addon
from xivgate_agr_collision import operators

from xivgate_agr_collision.core import decompose as D
for item in argv[3:] if len(argv) > 3 else []:
    key, value = item.split("=")
    setattr(D, key, float(value))
print("needle aspect %.0f, max loss %.4f m" % (D.NEEDLE_ASPECT, D.NEEDLE_MAX_LOSS))
bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
scene = bpy.context.scene
settings = scene.xivgate_agr_collision
data = np.load(argv[1])
mesh = bpy.data.meshes.new("Source")
mesh.from_pydata([tuple(map(float, v)) for v in data["v"]], [], [tuple(int(i) for i in f) for f in data["f"]])
mesh.update()
source = bpy.data.objects.new("Cube", mesh)
scene.collection.objects.link(source)
centre = data["v"].mean(axis=0) if len(argv) > 2 and argv[2] == "centre" else None
import time; started = time.time()
result = operators.generate_for_objects(bpy.context, [source], base_name="Cube", settings=settings,
                                        origin_world=tuple(centre) if centre is not None else None)

margins = []
causes = []
for ob in result["colliders"]:
    me = ob.data
    co = np.array([tuple(v.co) for v in me.vertices], dtype=np.float64)
    worst = 0.0
    cause = None
    for p in me.polygons:
        n = np.array(tuple(p.normal))
        if n @ n < 0.5:
            continue
        idx = list(p.vertices)
        pts = co[idx]
        longest = max(np.linalg.norm(pts[(k + 1) % len(pts)] - pts[k]) for k in range(len(pts)))
        width = 2 * p.area / longest if longest > 0 else 0
        others = np.delete(co, idx, axis=0)
        d = (others - np.array(tuple(p.center))) @ n
        side = min(d.max(), -d.min())
        value = min(width, side)
        if value > worst:
            worst = value
            cause = (width, longest, side)
    margins.append(worst)
    causes.append(cause)
margins = np.array(margins)
print("hulls %d, triangles %d, %.1fs, origin %s" % (len(margins), sum(len(o.data.polygons) for o in result["colliders"]), time.time() - started, "model centre" if centre is not None else "world origin"))
for tol in (0.01, 0.005, 0.002, 0.001, 0.0001):
    print("  rejected at %.1f mm tolerance: %d" % (tol * 1000, int((margins > tol).sum())))
print("  largest rejecting tolerance: %.3f mm" % (margins.max() * 1000))
order = np.argsort(-margins)[:5]
for i in order:
    w, L, s = causes[i]
    print("   hull %d: fails below %.3f mm; face width %.2f mm, length %.2f m, beyond plane %.3f mm" % (i, margins[i] * 1000, w * 1000, L, s * 1000))
