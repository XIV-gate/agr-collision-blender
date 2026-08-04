# SPDX-FileCopyrightText: 2026 XIVgate
# SPDX-License-Identifier: GPL-3.0-or-later
"""Background test for the selection-independent AGR Collision API."""

import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector


sys.path.insert(0, str(Path(__file__).resolve().parent))

import xivgate_agr_collision
from xivgate_agr_collision import operators
from xivgate_agr_collision.core import decompose


xivgate_agr_collision.register()
settings = bpy.context.scene.xivgate_agr_collision
assert not settings.destructive_preprocess
assert not settings.fuse_sources
assert not settings.skip_thin
bpy.ops.mesh.primitive_cube_add(size=2.0, location=(1.0, 2.0, 3.0))
proxy = bpy.context.object
proxy.name = "Tower Collision Proxy"
source_pointer = proxy.data.as_pointer()

destination = bpy.data.collections.new("PREPARED_HIGH_ITEM")
bpy.context.scene.collection.children.link(destination)
progress_events = []
result = operators.generate_for_objects(
    bpy.context,
    [proxy],
    base_name="SM_TestTower_Main",
    destination_collection=destination,
    origin_world=(7.0, -4.0, 0.5),
    progress=lambda percent, message: progress_events.append(
        (float(percent), str(message))),
)
colliders = result["colliders"]

assert colliders
assert all(obj.name.startswith("UCX_SM_TestTower_Main_") for obj in colliders)
assert all(obj in destination.objects[:] for obj in colliders)
assert all(
    (obj.matrix_world.translation - Vector((7.0, -4.0, 0.5))).length < 1.0e-7
    for obj in colliders
)
assert result["origin_world"] == [7.0, -4.0, 0.5]
first_points = np.asarray([
    tuple(obj.matrix_world @ vertex.co)
    for obj in colliders
    for vertex in obj.data.vertices
])
expected_first_points = np.concatenate([
    vertices for vertices, _faces in result["decomposition"].hulls
])
assert np.allclose(first_points, expected_first_points, atol=1.0e-5)
assert proxy.data.as_pointer() == source_pointer
assert not proxy.hide_get()
assert result["validation"].valid
assert result["decomposition"].total_triangles <= result["budget"]
assert progress_events
assert progress_events[0][0] == 5.0
assert progress_events[-1][0] == 98.0
assert any("decomposition" in message.lower()
           for _percent, message in progress_events)

# Regeneration is a transaction: a candidate that fails validation must not
# delete or rename the previously valid UCX set.
old_collider_state = {
    obj.as_pointer(): (obj.name, obj.data.name)
    for obj in colliders
}
original_validator = operators.validation.validate_colliders
try:
    operators.validation.validate_colliders = (
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("intentional replacement validation failure")))
    try:
        operators.generate_for_objects(
            bpy.context,
            [proxy],
            base_name="SM_TestTower_Main",
            destination_collection=destination,
        )
    except RuntimeError as exc:
        assert "replacement validation failure" in str(exc)
    else:
        raise AssertionError("Expected replacement validation failure")
finally:
    operators.validation.validate_colliders = original_validator

assert {
    obj.as_pointer(): (obj.name, obj.data.name)
    for obj in colliders
} == old_collider_state
assert all(obj in destination.objects[:] for obj in colliders)

before_collections = {collection.as_pointer() for collection in bpy.data.collections}
bpy.ops.mesh.primitive_cube_add(size=1.0, location=(5.0, 0.0, 0.0))
failing_proxy = bpy.context.object
original_validator = operators.validation.validate_colliders
try:
    operators.validation.validate_colliders = (
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("intentional validation failure")))
    try:
        operators.generate_for_objects(
            bpy.context,
            [failing_proxy],
            base_name="SM_FailCleanup_Main",
        )
    except RuntimeError as exc:
        assert "intentional validation failure" in str(exc)
    else:
        raise AssertionError("Expected intentional validation failure")
finally:
    operators.validation.validate_colliders = original_validator

assert {
    collection.as_pointer()
    for collection in bpy.data.collections
} == before_collections
assert not [
    obj for obj in bpy.data.objects
    if obj.name.startswith("UCX_SM_FailCleanup_Main_")
]

# Two proxy objects can deliberately overlap.  Regeneration may split,
# subtract or inset the UCX hulls, but it must not silently discard either
# component.  Validate source-surface coverage and the combined outer bounds
# after the non-overlapping union is built.
bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 0.0, 0.0))
overlap_left = bpy.context.object
overlap_left.name = "Overlap Left"
bpy.ops.mesh.primitive_cube_add(size=2.0, location=(1.5, 0.0, 0.0))
overlap_right = bpy.context.object
overlap_right.name = "Overlap Right"
overlap_signatures = {
    obj.name: (
        obj.data.as_pointer(),
        len(obj.data.vertices),
        len(obj.data.polygons),
        tuple(round(value, 8) for row in obj.matrix_world for value in row),
    )
    for obj in (overlap_left, overlap_right)
}
overlap_destination = bpy.data.collections.new("OVERLAP_HIGH_ITEM")
bpy.context.scene.collection.children.link(overlap_destination)
overlap_result = operators.generate_for_objects(
    bpy.context,
    [overlap_left, overlap_right],
    base_name="SM_Overlap_Main",
    destination_collection=overlap_destination,
)
assert overlap_result["validation"].valid
assert not overlap_result["source"].skipped_components
overlap_pieces = [
    decompose._analyse_piece(
        decompose.Piece(vertices=vertices, faces=faces))
    for vertices, faces in overlap_result["decomposition"].hulls
]
coverage_deviation, uncovered = decompose._source_coverage_deviation(
    overlap_result["source"].vertices,
    overlap_result["source"].faces,
    overlap_pieces,
    tolerance=bpy.context.scene.xivgate_agr_collision.tolerance,
)
assert coverage_deviation <= bpy.context.scene.xivgate_agr_collision.tolerance
assert uncovered == 0
generated_points = np.asarray([
    tuple(obj.matrix_world @ vertex.co)
    for obj in overlap_result["colliders"]
    for vertex in obj.data.vertices
])
assert generated_points[:, 0].min() <= -1.0 + 0.101
assert generated_points[:, 0].max() >= 2.5 - 0.101
assert overlap_signatures == {
    obj.name: (
        obj.data.as_pointer(),
        len(obj.data.vertices),
        len(obj.data.polygons),
        tuple(round(value, 8) for row in obj.matrix_world for value in row),
    )
    for obj in (overlap_left, overlap_right)
}

# Close is not the same as overlapping.  A real air gap must survive
# regeneration: two source boxes separated by 10 mm may not be replaced by
# one broad hull that silently fills the gap.
bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 10.0, 0.0))
near_left = bpy.context.object
near_left.name = "Near Left"
bpy.ops.mesh.primitive_cube_add(size=2.0, location=(2.01, 10.0, 0.0))
near_right = bpy.context.object
near_right.name = "Near Right"
near_signatures = {
    obj.name: (
        obj.data.as_pointer(),
        len(obj.data.vertices),
        len(obj.data.polygons),
        tuple(round(value, 8) for row in obj.matrix_world for value in row),
    )
    for obj in (near_left, near_right)
}
near_destination = bpy.data.collections.new("NEAR_HIGH_ITEM")
bpy.context.scene.collection.children.link(near_destination)
near_result = operators.generate_for_objects(
    bpy.context,
    [near_left, near_right],
    base_name="SM_Near_Main",
    destination_collection=near_destination,
)
near_bounds = []
for obj in near_result["colliders"]:
    points = np.asarray([
        tuple(obj.matrix_world @ vertex.co)
        for vertex in obj.data.vertices
    ])
    near_bounds.append((float(points[:, 0].min()), float(points[:, 0].max())))
assert near_result["validation"].valid
assert not near_result["source"].skipped_components
assert len(near_result["colliders"]) >= 2
assert all(maximum - minimum < 2.005 for minimum, maximum in near_bounds), (
    "A generated hull bridged the intentional 10 mm gap: {}".format(
        near_bounds))
assert near_signatures == {
    obj.name: (
        obj.data.as_pointer(),
        len(obj.data.vertices),
        len(obj.data.polygons),
        tuple(round(value, 8) for row in obj.matrix_world for value in row),
    )
    for obj in (near_left, near_right)
}

# A small, detached component is still semantically meaningful.  It must not
# disappear just because it is a tiny fraction of the total source volume.
bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 5.0, 0.0))
coverage_main = bpy.context.object
coverage_main.name = "Coverage Main"
bpy.ops.mesh.primitive_cube_add(size=0.2, location=(2.0, 5.0, 0.0))
coverage_detail = bpy.context.object
coverage_detail.name = "Coverage Detail"
coverage_destination = bpy.data.collections.new("COVERAGE_HIGH_ITEM")
bpy.context.scene.collection.children.link(coverage_destination)
coverage_result = operators.generate_for_objects(
    bpy.context,
    [coverage_main, coverage_detail],
    base_name="SM_Coverage_Main",
    destination_collection=coverage_destination,
)
coverage_points = np.asarray([
    tuple(obj.matrix_world @ vertex.co)
    for obj in coverage_result["colliders"]
    for vertex in obj.data.vertices
])
assert coverage_result["validation"].valid
assert not coverage_result["source"].skipped_components
assert coverage_points[:, 0].max() >= 2.1 - 0.101
assert len(coverage_result["colliders"]) >= 2

print(
    "AGR_TEST_RESULT",
    {
        "colliders": [obj.name for obj in colliders],
        "triangles": result["decomposition"].total_triangles,
        "budget": result["budget"],
        "destination": destination.name,
        "source_unchanged": True,
        "failed_transaction_clean": True,
        "progress_events": len(progress_events),
        "overlap_colliders": len(overlap_result["colliders"]),
        "overlap_coverage_deviation": coverage_deviation,
        "near_gap_colliders": len(near_result["colliders"]),
        "near_gap_bounds": near_bounds,
        "detached_detail_colliders": len(coverage_result["colliders"]),
    },
)
