"""Build a closed test house: hollow walls, door, windows, arch; save npz."""
import bpy, bmesh, numpy as np, sys, math
out = sys.argv[sys.argv.index("--")+1]
bpy.ops.wm.read_factory_settings(use_empty=True)
def box(name, size, loc):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc); ob = bpy.context.object; ob.scale = size; ob.name = name
    bpy.ops.object.transform_apply(scale=True); return ob
house = box("house", (10, 8, 6), (0, 0, 3))
cutters = [
    box("room", (9.5, 7.5, 5.5), (0, 0, 3)),                 # hollow interior (walls/floor/roof 0.25 m)
    box("door", (1.0, 1.0, 2.1), (-2.5, -4.0, 1.3)),         # door through front wall
    box("win1", (1.2, 1.0, 1.2), (2.0, -4.0, 3.2)),          # window front
    box("win2", (1.0, 1.2, 1.0), (5.0, 1.5, 3.5)),           # window side
    box("niche", (1.5, 0.12, 2.0), (-2.0, 4.0 - 0.06 + 0.0, 3.0)),  # 12 cm deep niche on back wall outside
]
bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=1.0, depth=1.0, location=(2.0, 4.0, 2.2), rotation=(math.pi/2, 0, 0))
arch_top = bpy.context.object
cutters.append(arch_top)
cutters.append(box("arch_low", (2.0, 1.0, 2.2), (2.0, 4.0, 1.1)))
for c in cutters:
    mod = house.modifiers.new("b", "BOOLEAN"); mod.operation = "DIFFERENCE"; mod.object = c; mod.solver = "EXACT"
    bpy.context.view_layer.objects.active = house; bpy.ops.object.modifier_apply(modifier=mod.name)
bm = bmesh.new(); bm.from_mesh(house.data); bmesh.ops.triangulate(bm, faces=bm.faces[:])
v = np.array([tuple(x.co) for x in bm.verts]); f = np.array([[x.index for x in fc.verts] for fc in bm.faces])
print("house V", len(v), "F", len(f), "nonmanifold", sum(1 for e in bm.edges if not e.is_manifold), "vol", bm.calc_volume())
np.savez(out, v=v, f=f)
