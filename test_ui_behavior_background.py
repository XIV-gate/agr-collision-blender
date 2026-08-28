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


def _activate(obj):
    for selected in list(bpy.context.selected_objects):
        selected.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _make_source(name, location):
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=location)
    source = bpy.context.object
    source.name = name
    return source


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

    first_result = operators.generate_for_objects(
        bpy.context,
        [source],
        base_name=source.name,
        settings=settings,
    )
    first_colliders = first_result["colliders"]
    assert first_colliders
    assert all(obj.display_type == "WIRE" for obj in first_colliders)
    assert json.loads(first_colliders[0]["agr_source_objects_json"]) == [
        "Facade, North"
    ]

    second_source = _make_source("Annex", (4.0, 0.0, 0.0))
    second_result = operators.generate_for_objects(
        bpy.context,
        [second_source],
        base_name=second_source.name,
        settings=settings,
    )
    second_colliders = second_result["colliders"]
    assert second_colliders
    assert all(obj.display_type == "WIRE" for obj in second_colliders)

    # Viewport toggles must read and change only the collision set owned by
    # the active source. The other generated set must remain untouched.
    _activate(source)
    assert operators.active_wire_display(bpy.context) is True
    assert bpy.ops.xivgate_agr_collision.toggle_wire_display() == {"FINISHED"}
    assert all(obj.display_type == "SOLID" for obj in first_colliders)
    assert all(obj.display_type == "WIRE" for obj in second_colliders)
    assert not operators.active_wire_display(bpy.context)

    _activate(second_source)
    assert operators.active_wire_display(bpy.context)
    assert settings.wire_display is False
    settings.show_progress_console = False
    assert bpy.ops.xivgate_agr_collision.generate() == {"FINISHED"}
    second_colliders = operators._generated_colliders(
        bpy.context.scene,
        second_source.name,
    )
    assert all(obj.display_type == "WIRE" for obj in second_colliders)
    assert all(obj.display_type == "SOLID" for obj in first_colliders)

    _activate(source)
    assert bpy.ops.xivgate_agr_collision.toggle_wire_display() == {"FINISHED"}
    assert all(obj.display_type == "WIRE" for obj in first_colliders)
    assert all(obj.display_type == "WIRE" for obj in second_colliders)

    assert operators.active_sources_hidden(bpy.context) is False
    assert bpy.ops.xivgate_agr_collision.toggle_source_visibility() == {
        "FINISHED"
    }
    assert source.hide_get()
    assert not second_source.hide_get()

    # Regenerating another visible set must preserve its own source state,
    # even though the last toggled set changed the scene-level default.
    _activate(second_source)
    assert settings.hide_sources is True
    assert bpy.ops.xivgate_agr_collision.generate() == {"FINISHED"}
    second_colliders = operators._generated_colliders(
        bpy.context.scene,
        second_source.name,
    )
    assert source.hide_get()
    assert not second_source.hide_get()

    # A generated collider resolves back to the same set, allowing a hidden
    # source to be restored without affecting any other source.
    _activate(first_colliders[0])
    assert operators.active_sources_hidden(bpy.context)
    assert bpy.ops.xivgate_agr_collision.toggle_source_visibility() == {
        "FINISHED"
    }
    assert not source.hide_get()
    assert not second_source.hide_get()

    for selected in list(bpy.context.selected_objects):
        selected.select_set(False)
    bpy.context.view_layer.objects.active = None
    assert not bpy.ops.xivgate_agr_collision.toggle_wire_display.poll()
    assert not bpy.ops.xivgate_agr_collision.toggle_source_visibility.poll()

    # Colliders from <= 1.2.6 stored one or more names as a comma-separated
    # string. A legacy object name may itself also be valid JSON syntax.
    source.name = "123"
    for collider in first_colliders:
        if "agr_source_objects_json" in collider:
            del collider["agr_source_objects_json"]
        collider["agr_source_objects"] = source.name
    operators.apply_source_visibility(
        bpy.context.scene,
        True,
        view_layer=bpy.context.view_layer,
        colliders=first_colliders,
    )
    assert source.hide_get()
    operators.apply_source_visibility(
        bpy.context.scene,
        False,
        view_layer=bpy.context.view_layer,
        colliders=first_colliders,
    )
    assert not source.hide_get()

    source.name = '["foo"]'
    for collider in first_colliders:
        collider["agr_source_objects"] = source.name
    operators.apply_source_visibility(
        bpy.context.scene,
        True,
        view_layer=bpy.context.view_layer,
        colliders=first_colliders,
    )
    assert source.hide_get()
    operators.apply_source_visibility(
        bpy.context.scene,
        False,
        view_layer=bpy.context.view_layer,
        colliders=first_colliders,
    )
    assert not source.hide_get()

    print(
        "AGR_UI_BEHAVIOR_RESULT",
        {
            "colliders": len(first_colliders) + len(second_colliders),
            "selection_required": True,
            "active_set_isolation": True,
            "active_collider_resolution": True,
            "legacy_source_links": True,
        },
    )
finally:
    addon.unregister()
