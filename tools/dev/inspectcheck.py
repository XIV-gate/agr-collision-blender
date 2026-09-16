"""False positives on real hulls, true positives on synthetic defects."""
import sys, time, numpy as np, bpy, bmesh
from mathutils import Vector
argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
from xivgate_agr_collision.core import inspection as I

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

from mathutils import geometry
rng = np.random.default_rng(5)
worst = 0.0
for trial in range(200):
    tri = rng.normal(size=(3, 3))
    if trial % 4 == 0:
        tri[2] = tri[0] + (tri[1] - tri[0]) * rng.random() + rng.normal(size=3) * 1e-6
    pts = rng.normal(size=(50, 3)) * 2.0
    from xivgate_agr_collision.core import hull64 as H64; ours = H64.distance_to_triangles(pts, tri[None, 0], tri[None, 1], tri[None, 2])
    ref = np.array([(Vector(q) - geometry.closest_point_on_tri(Vector(q), *map(Vector, tri))).length for q in pts])
    worst = max(worst, float(np.abs(ours - ref).max()))
print("point-triangle distance vs mathutils, max abs difference: %.2e" % worst)

def mesh_object(name, vertices, faces, location=(0, 0, 0)):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(map(float, v)) for v in vertices], [], [tuple(int(i) for i in f) for f in faces])
    me.update()
    ob = bpy.data.objects.new(name, me)
    ob.location = location
    scene.collection.objects.link(ob)
    bpy.context.view_layer.update()
    return ob

# Real hulls, twice: in world coordinates, and relative to a pivot as UCX are.
hulls = np.load(argv[1])
n = len(hulls.files) // 2
pivot = np.array([-48.0, 73.0, 0.0])
for label, shift in (("world coords", np.zeros(3)), ("pivot-relative", pivot)):
    t = time.time()
    flagged = []
    for i in range(n):
        v, f = hulls["v%d" % i], hulls["f%d" % i]
        ob = mesh_object("H%d" % i, v - shift, f, tuple(shift))
        r = I.inspect_object(ob, 0.0)
        if r.concavity > 0.0 or not r.closed:
            flagged.append((r.name, r.concavity, r.closed))
        bpy.data.objects.remove(ob)
    print("%s: %d of %d flagged in %.1fs" % (label, len(flagged), n, time.time() - t))
    for row in sorted(flagged, key=lambda x: -x[1])[:8]:
        print("   ", row)

far = (100.0, 200.0, 30.0)
bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0)
cube_v = [tuple(v.co) for v in bm.verts]; cube_f = [[v.index for v in fc.verts] for fc in bm.faces]; bm.free()

def poked(depth):
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0)
    top = [fc for fc in bm.faces if fc.normal.z > 0.5]
    res = bmesh.ops.poke(bm, faces=top)
    center = [v for v in res["verts"]][0]
    center.co.z -= depth
    bm.verts.index_update()
    v = [tuple(x.co) for x in bm.verts]; f = [[x.index for x in fc.verts] for fc in bm.faces]; bm.free()
    return v, f

L_v = [(0,0,0),(2,0,0),(2,1,0),(1,1,0),(1,2,0),(0,2,0),(0,0,1),(2,0,1),(2,1,1),(1,1,1),(1,2,1),(0,2,1)]
L_f = [(0,5,4,3,2,1),(6,7,8,9,10,11),(0,1,7,6),(1,2,8,7),(2,3,9,8),(3,4,10,9),(4,5,11,10),(5,0,6,11)]

cases = [
    ("cube far from origin", cube_v, cube_f, far),
    ("dent 0.1 mm far", *poked(0.0001), far),
    ("dent 0.01 mm far", *poked(0.00001), far),
    ("bump 0.1 mm (still convex)", *poked(-0.0001), far),
    ("L block 1 m notch", L_v, L_f, far),
    ("small cube 5 cm", [tuple(np.array(p) * 0.05) for p in cube_v], cube_f, far),
    ("thin slab 1 cm", [tuple(np.array(p) * np.array([2.0, 2.0, 0.01])) for p in cube_v], cube_f, far),
]
bpy.ops.mesh.primitive_cylinder_add(vertices=128, radius=3.0, depth=0.2, location=(350.0, -420.0, 12.0))
cyl = bpy.context.object
print("%-28s %8s %12s %12s %10s" % ("case", "closed", "concavity", "volume", "thickness"))
for name, v, f, loc in cases:
    ob = mesh_object(name, v, f, loc)
    r = I.inspect_object(ob, 0.0)
    print("%-28s %8s %12.7f %12.7f %10.5f" % (name, r.closed, r.concavity, r.volume, r.thickness))
r = I.inspect_object(cyl, 0.0)
print("%-28s %8s %12.7f %12.7f %10.5f" % ("cylinder 128 far", r.closed, r.concavity, r.volume, r.thickness))
