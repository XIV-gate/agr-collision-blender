"""Workbench renders of a mesh set with random per-object colors from several views."""
import sys, math, random, numpy as np, bpy
from mathutils import Vector
argv = sys.argv[sys.argv.index("--")+1:]
npz, out, mode = argv[0], argv[1], argv[2]   # mode: source | hulls
extra = argv[3] if len(argv) > 3 else None
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
def add(v, f, name, color):
    me = bpy.data.meshes.new(name); me.from_pydata([tuple(p) for p in v], [], [tuple(int(k) for k in t) for t in f])
    ob = bpy.data.objects.new(name, me); ob.color = color; scene.collection.objects.link(ob); return ob
rng = random.Random(3)
H = np.load(npz)
pts = []
if mode == "source":
    add(H["v"], H["f"], "src", (0.8, 0.8, 0.8, 1)); pts.append(H["v"])
else:
    n = len(H.files)//2
    for i in range(n):
        v, f = H["v%d"%i], H["f%d"%i]
        if len(f) == 0: continue
        add(v, f, "h%d"%i, (rng.random()*0.8+0.2, rng.random()*0.8+0.2, rng.random()*0.8+0.2, 1)); pts.append(v)
    if extra:
        E = np.load(extra)
        for i in range(len(E.files)//2):
            v, f = E["v%d"%i], E["f%d"%i]
            if len(f): add(v, f, "e%d"%i, (1, 0, 0, 1))
P = np.vstack(pts); c = (P.min(0) + P.max(0)) / 2; r = np.linalg.norm(P.max(0) - P.min(0)) / 2
scene.render.engine = 'BLENDER_WORKBENCH'
scene.display.shading.light = 'STUDIO'
scene.display.shading.color_type = 'OBJECT'
scene.display.shading.show_object_outline = True
scene.render.resolution_x = 900; scene.render.resolution_y = 1100
cam_data = bpy.data.cameras.new("cam"); cam = bpy.data.objects.new("cam", cam_data); scene.collection.objects.link(cam); scene.camera = cam
cam_data.type = 'ORTHO'; cam_data.ortho_scale = r * 2.1
views = {"a": (1, -1, 0.6), "b": (-1, 1, 0.6), "c": (1, 1, -0.5)}
for key, d in views.items():
    d = Vector(d).normalized()
    cam.location = Vector(c) + d * r * 4
    cam.rotation_euler = (-d).to_track_quat('-Z', 'Y').to_euler()
    cam_data.clip_end = r * 10
    scene.render.filepath = "%s_%s.png" % (out, key)
    bpy.ops.render.render(write_still=True)
print("rendered")
