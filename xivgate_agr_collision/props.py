# SPDX-FileCopyrightText: 2026 XIVgate
# SPDX-License-Identifier: GPL-3.0-or-later
# Scene-level settings for AGR Collision.

import bpy
from bpy.props import (
    BoolProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)


class AGRCollisionSettings(bpy.types.PropertyGroup):
    tolerance: FloatProperty(
        name="Tolerance",
        description="Maximum allowed collision deviation; 0.10 m is the strict universal AGR limit",
        default=0.10,
        min=0.01,
        max=1.0,
        precision=3,
        subtype="DISTANCE",
        unit="LENGTH",
    )
    min_feature: FloatProperty(
        name="Min Feature",
        description="Separate details smaller than this size may be removed during preprocessing",
        default=0.10,
        min=0.01,
        max=1.0,
        precision=3,
        subtype="DISTANCE",
        unit="LENGTH",
    )
    fuse_sources: BoolProperty(
        name="Fuse Selected Geometry",
        description=(
            "Merge nearby vertices in the combined hidden proxy before volume repair; "
            "this can reconnect walls and other parts split across source objects"
        ),
        default=True,
    )
    fuse_distance: FloatProperty(
        name="Fuse Distance",
        description="Maximum distance used to merge nearby proxy vertices",
        default=0.02,
        min=0.0001,
        max=0.10,
        precision=3,
        subtype="DISTANCE",
        unit="LENGTH",
    )
    skip_thin: BoolProperty(
        name="Skip Separate Thin Parts",
        description="Ignore separate thin components such as canopies and fences; the largest component is never removed",
        default=True,
    )
    thin_threshold: FloatProperty(
        name="Thin Threshold",
        description="Maximum thickness of separate components that may be ignored",
        default=0.05,
        min=0.005,
        max=0.25,
        precision=3,
        subtype="DISTANCE",
        unit="LENGTH",
    )
    gap: FloatProperty(
        name="Gap",
        description="Air gap between neighbouring hulls; 0.0002 m is the AGR minimum",
        default=0.0002,
        min=0.0002,
        max=0.01,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
    )

    attempts: IntProperty(
        name="Optimization Passes",
        description="Try deterministic tie variants and keep the smallest complete result; each pass runs the full search",
        default=1,
        min=1,
        max=24,
    )
    seed: IntProperty(
        name="Seed",
        description="Base seed for deterministic split tie variants",
        default=0,
        min=0,
    )
    max_parts: IntProperty(
        name="Max Parts",
        description="Hard maximum number of UCX hulls",
        default=128,
        min=1,
        max=999,
    )
    max_depth: IntProperty(
        name="Search Depth",
        description="Maximum number of recursive separating planes",
        default=24,
        min=1,
        max=24,
    )

    wire_display: BoolProperty(
        name="Wire Display",
        description="Display generated colliders as wireframe objects",
        default=True,
    )
    hide_sources: BoolProperty(
        name="Hide Sources After Generation",
        description="Hide selected visual sources after a successful generation",
        default=False,
    )

    last_status: StringProperty(
        name="Status",
        default="Ready",
    )
    last_source: StringProperty(
        name="Last Source",
        default="",
    )
    last_colliders: IntProperty(
        name="Last Colliders",
        default=0,
        min=0,
    )
    last_triangles: IntProperty(
        name="Last Triangles",
        default=0,
        min=0,
    )
    last_deviation: FloatProperty(
        name="Last Deviation",
        default=0.0,
        min=0.0,
        subtype="DISTANCE",
        unit="LENGTH",
    )
    last_input_triangles: IntProperty(
        name="Last Input Triangles",
        default=0,
        min=0,
    )
    last_proxy_triangles: IntProperty(
        name="Last Proxy Triangles",
        default=0,
        min=0,
    )


def register():
    bpy.utils.register_class(AGRCollisionSettings)
    bpy.types.Scene.xivgate_agr_collision = PointerProperty(
        type=AGRCollisionSettings)


def unregister():
    if hasattr(bpy.types.Scene, "xivgate_agr_collision"):
        del bpy.types.Scene.xivgate_agr_collision
    bpy.utils.unregister_class(AGRCollisionSettings)
