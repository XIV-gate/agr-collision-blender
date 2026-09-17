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
    destructive_preprocess: BoolProperty(
        name="Allow Topology-Changing Preprocess",
        description=(
            "Explicitly allow source fusion and removal of separate small or "
            "thin components; keep disabled for lossless collision generation"
        ),
        default=False,
    )
    min_feature: FloatProperty(
        name="Min Feature",
        description=(
            "When topology-changing preprocessing is explicitly enabled, "
            "separate details smaller than this size may be removed"
        ),
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
        default=False,
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
        default=False,
    )
    thin_threshold: FloatProperty(
        name="Thin Threshold",
        description="Parts of the model thinner than this receive no collision",
        default=0.05,
        min=0.005,
        max=0.25,
        precision=3,
        subtype="DISTANCE",
        unit="LENGTH",
    )
    gap: FloatProperty(
        name="Gap",
        description=(
            "Air gap between neighbouring hulls; AGR recommends 0.2-10 mm, and "
            "1 mm clears SINTEZ AGR Checker at any gap tolerance it allows"
        ),
        default=0.001,
        min=0.0002,
        max=0.01,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
    )

    attempts: IntProperty(
        name="Optimization Passes",
        description=(
            "Re-cut the parts that produced the most pieces and keep "
            "variants with fewer pieces; every variant stays exact, so more "
            "passes only trade generation time for a simpler collision. "
            "1 is a single fast pass; around 100 gives the smallest sets on "
            "complex buildings in a few minutes"
        ),
        default=1,
        min=1,
        max=999,
    )

    debug_collection: PointerProperty(
        name="Check Collection",
        description=(
            "Collection the collision debugger checks; the last generated "
            "collision set is filled in automatically"
        ),
        type=bpy.types.Collection,
    )
    debug_colliders_only: BoolProperty(
        name="UCX Objects Only",
        description="Check only UCX collision objects and skip other meshes in the collection",
        default=True,
    )
    debug_concavity_tolerance: FloatProperty(
        name="Concavity Tolerance",
        description=(
            "Select objects whose surface sinks deeper than this below their "
            "own convex hull; 0 finds every concavity above float32 rounding"
        ),
        default=0.0,
        min=0.0,
        soft_max=0.01,
        precision=6,
        step=0.001,
        subtype="DISTANCE",
        unit="LENGTH",
    )
    debug_min_volume: FloatProperty(
        name="Volume Below",
        description="Select objects whose enclosed volume is smaller than this",
        default=0.001,
        min=0.0,
        soft_max=1.0,
        precision=6,
        step=0.01,
        unit="VOLUME",
    )
    debug_min_thickness: FloatProperty(
        name="Thickness Below",
        description="Select objects whose exact minimum thickness is smaller than this",
        default=0.05,
        min=0.0,
        soft_max=1.0,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
    )
    debug_status: StringProperty(
        name="Debugger Status",
        default="",
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
    show_progress_console: BoolProperty(
        name="Open Progress Console During Generation",
        description=(
            "Open a temporary console while collision generation is running "
            "and print progress heartbeats"
        ),
        default=True,
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
