"""Blender background regression test for live viewport settings."""

import json
from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import xivgate_agr_collision as addon
from xivgate_agr_collision import operators


def _unregister_loaded_test_copy():
    if hasattr(bpy.types.Scene, "xivgate_agr_collision"):
        try:
            addon.unregister()
        except Exception:
            pass


_unregister_loaded_test_copy()
addon.register()

try:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    bpy.ops.mesh.primitive_cube_add(size=2.0)
    source = bpy.context.object
    source.name = "Facade, North"

    settings = bpy.context.scene.xivgate_agr_collision
    settings.last_source = "Cube"

    # Analyze and Generate intentionally require a current mesh selection,
    # even when the panel still displays the last remembered source.
    assert operators.AGR_OT_analyze_selected.poll(bpy.context)
    assert operators.AGR_OT_generate.poll(bpy.context)
    source.select_set(False)
    bpy.context.view_layer.objects.active = None
    assert settings.last_source == "Cube"
    assert not operators.AGR_OT_analyze_selected.poll(bpy.context)
    assert not operators.AGR_OT_generate.poll(bpy.context)
    source.select_set(True)
    bpy.context.view_layer.objects.active = source

    settings.attempts = 1
    settings.wire_display = True
    settings.hide_sources = False

    result = operators.generate_for_objects(
        bpy.context,
        [source],
        base_name=source.name,
        settings=settings,
    )
    colliders = result["colliders"]
    assert colliders
    assert all(obj.display_type == "WIRE" for obj in colliders)
    assert json.loads(colliders[0]["agr_source_objects_json"]) == [
        "Facade, North"
    ]

    # Changing the UI setting must update the already-generated result rather
    # than only affecting the next generation.
    settings.wire_display = False
    assert all(obj.display_type == "SOLID" for obj in colliders)
    settings.wire_display = True
    assert all(obj.display_type == "WIRE" for obj in colliders)

    # The visibility setting must also be reversible for the source set owned
    # by the generated colliders.
    settings.hide_sources = True
    assert source.hide_get()
    settings.hide_sources = False
    assert not source.hide_get()

    # Colliders from <= 1.2.6 stored one or more names as a comma-separated
    # string. A legacy object name may itself also be valid JSON syntax.
    source.name = "123"
    for collider in colliders:
        if "agr_source_objects_json" in collider:
            del collider["agr_source_objects_json"]
        collider["agr_source_objects"] = source.name
    settings.hide_sources = True
    assert source.hide_get()
    settings.hide_sources = False
    assert not source.hide_get()

    source.name = '["foo"]'
    for collider in colliders:
        collider["agr_source_objects"] = source.name
    settings.hide_sources = True
    assert source.hide_get()
    settings.hide_sources = False
    assert not source.hide_get()

    print(
        "AGR_UI_BEHAVIOR_RESULT",
        {
            "colliders": len(colliders),
            "selection_required": True,
            "wire_reactive": True,
            "source_visibility_reversible": True,
            "legacy_source_links": True,
        },
    )
finally:
    addon.unregister()
