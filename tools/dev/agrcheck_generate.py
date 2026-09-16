"""Generate through the real operator path and judge by SINTEZ AGR Checker rules.

    blender -b --factory-startup --python agrcheck_generate.py -- WT mesh.npz [PASSES] [GAP]
"""
import sys
import time

import bpy
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
passes = int(argv[2]) if len(argv) > 2 else 1
gap = float(argv[3]) if len(argv) > 3 else None

import xivgate_agr_collision as addon
from xivgate_agr_collision import operators
from xivgate_agr_collision.core import validation

bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
scene = bpy.context.scene
settings = scene.xivgate_agr_collision
settings.attempts = passes
if gap is not None:
    settings.gap = gap

data = np.load(argv[1])
mesh = bpy.data.meshes.new("Source")
mesh.from_pydata([tuple(map(float, v)) for v in data["v"]], [], [tuple(int(i) for i in f) for f in data["f"]])
mesh.update()
source = bpy.data.objects.new("Cube", mesh)
scene.collection.objects.link(source)

started = time.time()
result = operators.generate_for_objects(bpy.context, [source], base_name="Cube", settings=settings)
elapsed = time.time() - started
colliders = result["colliders"]
triangles = sum(len(p.vertices) - 2 for ob in colliders for p in ob.data.polygons)
print("passes %d gap %.4f: %d hulls, %d polygons, %d triangles, %.1fs, validate %s" % (
    passes, settings.gap, len(colliders), sum(len(ob.data.polygons) for ob in colliders),
    triangles, elapsed, result["validation"].valid))
for convex, gap_tolerance in ((0.01, 0.0002), (0.001, 0.0005), (0.0001, 0.0005), (0.0001, 0.0001)):
    report = validation.agr_checker_report(colliders, convex, gap_tolerance)
    print("  SINTEZ rules convex %.1f mm, gap %.1f mm: %s" % (
        convex * 1000, gap_tolerance * 1000, "PASS" if report.passed else report.summary()))
