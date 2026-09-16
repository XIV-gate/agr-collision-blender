import sys, random, numpy as np, bpy
from mathutils import Vector
argv = sys.argv[sys.argv.index("--")+1:]
npz, out, mode = argv[0], argv[1], argv[2]
center = Vector([float(x) for x in argv[3].split(",")]); size = float(argv[4]); direction = Vector([float(x) for x in argv[5].split(",")]).normalized()
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
rng = random.Random(3)
H = np.load(npz)
def add(v, f, name, color):
    me = bpy.data.meshes.new(name); me.from_pydata([tuple(p) for p in v], [], [tuple(int(k) for k in t) for t in f])
    ob = bpy.data.objects.new(name, me); ob.color = color; scene.collection.objects.link(ob)
if mode == "source":
    add(H["v"], H["f"], "src", (0.8,0.8,0.8,1))
else:
    for i in range(len(H.files)//2):
        if len(H["f%d"%i]): add(H["v%d"%i], H["f%d"%i], "h%d"%i, (rng.random()*0.8+0.2, rng.random()*0.8+0.2, rng.random()*0.8+0.2, 1))
scene.render.engine = 'BLENDER_WORKBENCH'; scene.display.shading.light = 'STUDIO'
scene.display.shading.color_type = 'OBJECT'; scene.display.shading.show_object_outline = True
scene.render.resolution_x = 1000; scene.render.resolution_y = 800
cd = bpy.data.cameras.new("c"); cam = bpy.data.objects.new("c", cd); scene.collection.objects.link(cam); scene.camera = cam
cd.type = 'ORTHO'; cd.ortho_scale = size; cd.clip_end = 1000
cam.location = center + direction * 200; cam.rotation_euler = (-direction).to_track_quat('-Z', 'Y').to_euler()
scene.render.filepath = out; bpy.ops.render.render(write_still=True); print("rendered")
