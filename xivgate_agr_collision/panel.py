# SPDX-FileCopyrightText: 2026 XIVgate
# SPDX-License-Identifier: GPL-3.0-or-later
# English user interface for the Blender 3D Viewport sidebar.

import bpy

from . import translations


def _configure_properties(layout):
    layout.use_property_split = True
    layout.use_property_decorate = False


def _status_icon(status):
    upper = str(status).upper()
    if "FAIL" in upper or "ERROR" in upper:
        return "ERROR"
    if "VALID" in upper or "GENERATED" in upper:
        return "CHECKMARK"
    return "INFO"


class AGR_PT_collider(bpy.types.Panel):
    bl_label = "AGR Collision"
    bl_idname = "XIVGATE_PT_agr_collision"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AGR"
    bl_order = 20

    def draw(self, context):
        layout = self.layout
        settings = context.scene.xivgate_agr_collision
        _configure_properties(layout)

        status = layout.box()
        status.label(
            text=settings.last_status,
            icon=_status_icon(settings.last_status))

        source = layout.box()
        source.label(text="Source", icon="OBJECT_DATA")
        active = context.view_layer.objects.active
        source.label(
            text=(
                active.name
                if active and active.type == "MESH" else "No active mesh"))
        source.operator(
            "xivgate_agr_collision.analyze_selected",
            text="Analyze Selected",
            icon="VIEWZOOM")

        quality = layout.box()
        quality.label(text="Collision Quality", icon="MODIFIER")
        quality.prop(settings, "tolerance")
        quality.prop(settings, "gap")
        if settings.destructive_preprocess:
            quality.label(
                text="Topology-changing preprocess is enabled",
                icon="ERROR",
            )
        else:
            quality.label(
                text="Lossless components; validated replacement only",
                icon="LOCKED",
            )

        actions = layout.box()
        actions.label(text="Generate & Validate", icon="MESH_ICOSPHERE")
        actions.operator(
            "xivgate_agr_collision.generate",
            text="Generate / Regenerate",
            icon="MOD_BUILD")
        row = actions.row(align=True)
        row.operator(
            "xivgate_agr_collision.validate",
            text="Validate",
            icon="CHECKMARK")

        if settings.last_source:
            result = layout.box()
            result.label(text="Last Result", icon="INFO")
            result.label(
                text=translations.iface("Source: {}").format(
                    settings.last_source),
                translate=False)
            result.label(
                text=translations.iface(
                    "Input / working: {:,} / {:,} tris").format(
                    settings.last_input_triangles,
                    settings.last_proxy_triangles),
                translate=False)
            result.label(
                text=translations.iface(
                    "UCX: {} objects, {:,} tris").format(
                    settings.last_colliders,
                    settings.last_triangles),
                translate=False)
            result.label(
                text=translations.iface(
                    "Max deviation: {:.3f} m").format(
                    settings.last_deviation),
                translate=False)


class AGR_PT_collider_advanced(bpy.types.Panel):
    bl_label = "Advanced Collision Settings"
    bl_idname = "XIVGATE_PT_agr_collision_advanced"
    bl_parent_id = "XIVGATE_PT_agr_collision"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_order = 1
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.xivgate_agr_collision
        _configure_properties(layout)

        repair = layout.box()
        repair.label(text="Geometry Preprocessing", icon="MESH_DATA")
        repair.prop(settings, "destructive_preprocess")
        if not settings.destructive_preprocess:
            repair.label(
                text="Lossless: no component removal or broad source fusion",
                icon="LOCKED",
            )
        controls = repair.column()
        controls.enabled = settings.destructive_preprocess
        controls.prop(settings, "fuse_sources")
        fuse = controls.column()
        fuse.enabled = settings.fuse_sources
        fuse.prop(settings, "fuse_distance")
        controls.prop(settings, "min_feature")
        controls.prop(settings, "skip_thin")
        thin = controls.column()
        thin.enabled = settings.skip_thin
        thin.prop(settings, "thin_threshold")

        search = layout.box()
        search.label(text="Convex Search Limits", icon="MOD_BOOLEAN")
        row = search.row(align=True)
        row.prop(settings, "attempts")
        row.prop(settings, "seed")
        row = search.row(align=True)
        row.prop(settings, "max_parts")
        row.prop(settings, "max_depth")

        display = layout.box()
        display.label(text="Viewport Output", icon="HIDE_OFF")
        display.prop(settings, "show_progress_console")
        display.prop(settings, "wire_display")
        display.prop(settings, "hide_sources")

        cleanup = layout.box()
        cleanup.label(text="Manual Cleanup", icon="TRASH")
        cleanup.operator(
            "xivgate_agr_collision.remove_generated",
            text="Remove Generated",
            icon="TRASH")


CLASSES = (
    AGR_PT_collider,
    AGR_PT_collider_advanced,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
