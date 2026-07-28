# SPDX-FileCopyrightText: 2026 XIVgate
# SPDX-License-Identifier: GPL-3.0-or-later
# English user interface for the Blender 3D Viewport sidebar.

import bpy

from . import translations


class AGR_PT_collider(bpy.types.Panel):
    bl_label = "AGR Collision"
    bl_idname = "XIVGATE_PT_agr_collision"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AGR"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.xivgate_agr_collision

        source_box = layout.box()
        source_box.label(text="Source", icon="OBJECT_DATA")
        active = context.view_layer.objects.active
        source_box.label(
            text=active.name if active and active.type == "MESH" else "No active mesh"
        )
        source_box.operator("xivgate_agr_collision.analyze_selected", icon="VIEWZOOM")

        accuracy = layout.box()
        accuracy.label(text="Accuracy", icon="MODIFIER")
        accuracy.prop(settings, "tolerance")
        accuracy.prop(settings, "min_feature")
        accuracy.prop(settings, "gap")

        repair = layout.box()
        repair.label(text="Exact Geometry Repair", icon="MESH_DATA")
        repair.prop(settings, "fuse_sources")
        fuse_column = repair.column()
        fuse_column.enabled = settings.fuse_sources
        fuse_column.prop(settings, "fuse_distance")
        repair.prop(settings, "skip_thin")
        column = repair.column()
        column.enabled = settings.skip_thin
        column.prop(settings, "thin_threshold")

        search = layout.box()
        search.label(text="Exact Split Limits", icon="MOD_BOOLEAN")
        row = search.row(align=True)
        row.prop(settings, "attempts")
        row.prop(settings, "seed")
        search.prop(settings, "max_parts")
        search.prop(settings, "max_depth")

        output = layout.box()
        output.label(text="Output", icon="MESH_ICOSPHERE")
        output.prop(settings, "wire_display")
        output.prop(settings, "hide_sources")
        generate = output.operator("xivgate_agr_collision.generate", icon="MOD_BUILD")
        row = output.row(align=True)
        row.operator("xivgate_agr_collision.validate", icon="CHECKMARK")
        row.operator("xivgate_agr_collision.remove_generated", icon="TRASH")

        status = layout.box()
        status.label(text="Status", icon="INFO")
        status.label(text=settings.last_status)
        if settings.last_source:
            status.label(
                text=translations.iface("Source: {}").format(settings.last_source),
                translate=False,
            )
            status.label(
                text=translations.iface(
                    "Input / working: {:,} / {:,} tris"
                ).format(
                    settings.last_input_triangles,
                    settings.last_proxy_triangles,
                ),
                translate=False,
            )
            status.label(
                text=translations.iface("UCX: {} objects, {:,} tris").format(
                    settings.last_colliders,
                    settings.last_triangles,
                ),
                translate=False,
            )
            status.label(
                text=translations.iface("Max deviation: {:.3f} m").format(
                    settings.last_deviation
                ),
                translate=False,
            )


CLASSES = (AGR_PT_collider,)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
