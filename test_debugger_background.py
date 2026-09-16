"""Background checks for the AGR Collision debugger.

Run with:
    blender -b --factory-startup --python-exit-code 1 --python test_debugger_background.py
"""

from pathlib import Path
import sys

import bmesh
import bpy

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import xivgate_agr_collision as addon
from xivgate_agr_collision import operators


bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
scene = bpy.context.scene
settings = scene.xivgate_agr_collision
FAR = (140.0, -260.0, 35.0)


def mesh_object(name, build, collection, location=FAR):
    bm = bmesh.new()
    try:
        build(bm)
        mesh = bpy.data.meshes.new(name)
        bm.to_mesh(mesh)
    finally:
        bm.free()
    ob = bpy.data.objects.new(name, mesh)
    ob.location = location
    collection.objects.link(ob)
    return ob


def cube(size=(1.0, 1.0, 1.0)):
    def build(bm):
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    return build


def dented_cube(depth):
    def build(bm):
        bmesh.ops.create_cube(bm, size=1.0)
        bm.faces.ensure_lookup_table()
        top = [face for face in bm.faces if face.normal.z > 0.5]
        result = bmesh.ops.poke(bm, faces=top)
        result["verts"][0].co.z -= depth
    return build


def l_block(bm):
    points = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]
    bottom = [bm.verts.new((x, y, 0.0)) for x, y in points]
    top = [bm.verts.new((x, y, 1.0)) for x, y in points]
    bm.faces.new(list(reversed(bottom)))
    bm.faces.new(top)
    for index in range(len(points)):
        following = (index + 1) % len(points)
        bm.faces.new((bottom[index], bottom[following], top[following], top[index]))


def open_cube(bm):
    bmesh.ops.create_cube(bm, size=1.0)
    bm.faces.ensure_lookup_table()
    bm.faces.remove([face for face in bm.faces if face.normal.z > 0.5][0])


def cylinder(bm):
    bmesh.ops.create_cone(
        bm, cap_ends=True, segments=128, radius1=3.0, radius2=3.0, depth=0.2
    )


check = bpy.data.collections.new("DBG_CHECK")
scene.collection.children.link(check)
nested = bpy.data.collections.new("DBG_NESTED")
check.children.link(nested)

convex = mesh_object("UCX_Dbg_001", cube(), check)
dent = mesh_object("UCX_Dbg_002", dented_cube(0.0001), check)
notch = mesh_object("UCX_Dbg_003", l_block, check)
small = mesh_object("UCX_Dbg_004", cube((0.05, 0.05, 0.05)), check)
slab = mesh_object("UCX_Dbg_005", cube((2.0, 2.0, 0.01)), check)
opened = mesh_object("UCX_Dbg_006", open_cube, nested)
round_part = mesh_object("UCX_Dbg_007", cylinder, check, (-380.0, 510.0, 12.0))
hidden = mesh_object("UCX_Dbg_008", l_block, check)
helper = mesh_object("Helper_Concave", l_block, check)
bpy.context.view_layer.update()
hidden.hide_set(True)


def selected_names():
    return {ob.name for ob in bpy.context.view_layer.objects if ob.select_get()}


assert not bpy.ops.xivgate_agr_collision.debug_concave.poll()
settings.debug_collection = check
assert len(operators.debug_targets(settings)) == 8

settings.debug_concavity_tolerance = 0.0
assert bpy.ops.xivgate_agr_collision.debug_concave() == {"FINISHED"}
assert selected_names() == {"UCX_Dbg_002", "UCX_Dbg_003", "UCX_Dbg_006"}, selected_names()
assert bpy.context.view_layer.objects.active.name == "UCX_Dbg_006"
concave_status = settings.debug_status
assert "1 hidden" in concave_status, concave_status

settings.debug_concavity_tolerance = 0.001
assert bpy.ops.xivgate_agr_collision.debug_concave() == {"FINISHED"}
assert selected_names() == {"UCX_Dbg_003", "UCX_Dbg_006"}, selected_names()

settings.debug_colliders_only = False
assert bpy.ops.xivgate_agr_collision.debug_concave() == {"FINISHED"}
assert "Helper_Concave" in selected_names()
settings.debug_colliders_only = True

settings.debug_min_volume = 0.001
assert bpy.ops.xivgate_agr_collision.debug_small() == {"FINISHED"}
assert "UCX_Dbg_004" in selected_names()
assert not {"UCX_Dbg_001", "UCX_Dbg_002", "UCX_Dbg_003", "UCX_Dbg_007"} & selected_names()

settings.debug_min_thickness = 0.02
assert bpy.ops.xivgate_agr_collision.debug_thin() == {"FINISHED"}
assert selected_names() == {"UCX_Dbg_005"}, selected_names()
assert bpy.context.view_layer.objects.active.name == "UCX_Dbg_005"

icons = set(bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys())
for icon in ("OUTLINER_COLLECTION", "MOD_SMOOTH", "SNAP_VOLUME", "MOD_SOLIDIFY", "RESTRICT_SELECT_OFF"):
    assert icon in icons, icon

pair = bpy.data.collections.new("DBG_PAIR")
scene.collection.children.link(pair)
mesh_object("UCX_Pair_001", cube(), pair, (0.0, 0.0, 0.0))
mesh_object("UCX_Pair_002", cube(), pair, (0.5, 0.0, 0.0))
mesh_object("UCX_Pair_003", cube(), pair, (5.0, 0.0, 0.0))
bpy.context.view_layer.update()
settings.debug_collection = pair
assert bpy.ops.xivgate_agr_collision.debug_agr_checker() == {"FINISHED"}
assert selected_names() == {"UCX_Pair_001", "UCX_Pair_002"}, selected_names()
checker_status = settings.debug_status
assert "intersecting" in checker_status, checker_status

bpy.ops.mesh.primitive_cube_add(size=2.0, location=(10.0, 20.0, 3.0))
source = bpy.context.object
source.name = "SM_DebugSource"
generated = operators.generate_for_objects(bpy.context, [source], base_name=source.name)
assert settings.debug_collection == generated["collection"]
assert bpy.ops.xivgate_agr_collision.debug_concave() == {"FINISHED"}
assert not selected_names(), selected_names()
assert bpy.ops.xivgate_agr_collision.debug_agr_checker() == {"FINISHED"}
assert not selected_names(), selected_names()

print(
    "AGR_DEBUGGER_RESULT",
    {
        "concave_status": concave_status,
        "auto_collection": settings.debug_collection.name,
        "generated_status": settings.debug_status,
    },
)
