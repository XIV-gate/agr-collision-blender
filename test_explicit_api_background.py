# SPDX-FileCopyrightText: 2026 XIVgate
# SPDX-License-Identifier: GPL-3.0-or-later
"""Background test for the selection-independent AGR Collision API."""

import sys
from pathlib import Path

import bpy


sys.path.insert(0, str(Path(__file__).resolve().parent))

import xivgate_agr_collision
from xivgate_agr_collision import operators


xivgate_agr_collision.register()
bpy.ops.mesh.primitive_cube_add(size=2.0, location=(1.0, 2.0, 3.0))
proxy = bpy.context.object
proxy.name = "Tower Collision Proxy"
source_pointer = proxy.data.as_pointer()

destination = bpy.data.collections.new("PREPARED_HIGH_ITEM")
bpy.context.scene.collection.children.link(destination)
result = operators.generate_for_objects(
    bpy.context,
    [proxy],
    base_name="SM_TestTower_Main",
    destination_collection=destination,
)
colliders = result["colliders"]

assert colliders
assert all(obj.name.startswith("UCX_SM_TestTower_Main_") for obj in colliders)
assert all(obj in destination.objects[:] for obj in colliders)
assert proxy.data.as_pointer() == source_pointer
assert not proxy.hide_get()
assert result["validation"].valid
assert result["decomposition"].total_triangles <= result["budget"]

print(
    "AGR_TEST_RESULT",
    {
        "colliders": [obj.name for obj in colliders],
        "triangles": result["decomposition"].total_triangles,
        "budget": result["budget"],
        "destination": destination.name,
        "source_unchanged": True,
    },
)
