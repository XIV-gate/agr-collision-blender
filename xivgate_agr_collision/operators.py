# SPDX-FileCopyrightText: 2026 XIVgate
# SPDX-License-Identifier: GPL-3.0-or-later
# Blender operators for analysis, generation, validation and cleanup.

import ctypes
import datetime
import os
import sys
import threading
import time
import traceback

import bpy

from .core import decompose
from .core import naming
from .core import source
from .core import validation
from . import translations


def _console_is_visible():
    if os.name != "nt":
        return False
    try:
        handle = ctypes.windll.kernel32.GetConsoleWindow()
        return bool(handle and ctypes.windll.user32.IsWindowVisible(handle))
    except Exception:
        return False


def _open_progress_console():
    if bpy.app.background or os.name != "nt" or _console_is_visible():
        return False
    try:
        if bpy.ops.wm.console_toggle.poll():
            bpy.ops.wm.console_toggle()
            ctypes.windll.kernel32.SetConsoleTitleW(
                "AGR Collision - progress")
            return True
    except Exception:
        traceback.print_exc()
    return False


def _close_progress_console_later(opened_by_us):
    if not opened_by_us:
        return

    def close_console():
        try:
            if _console_is_visible() and bpy.ops.wm.console_toggle.poll():
                bpy.ops.wm.console_toggle()
        except Exception:
            traceback.print_exc()
        return None

    bpy.app.timers.register(close_console, first_interval=1.0)


def _console_progress(percent, message):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    print("[AGR Collision {}] {:>3}%  {}".format(
        timestamp, int(percent), message), flush=True)
    sys.stdout.flush()


class _ProgressSession:
    """Keep console feedback alive while synchronous decomposition runs."""

    def __init__(self, context, enabled, label):
        self.context = context
        self.enabled = bool(enabled)
        self.label = str(label)
        self.percent = 0.0
        self.message = "Starting"
        self.started = time.monotonic()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        try:
            self.context.window_manager.progress_begin(0, 100)
        except Exception:
            pass
        if self.enabled:
            _console_progress(0, "{} - starting".format(self.label))
            self._thread = threading.Thread(
                target=self._heartbeat,
                name="AGR-Collision-Progress",
                daemon=True,
            )
            self._thread.start()
        return self.update

    def update(self, percent, message):
        with self._lock:
            self.percent = max(0.0, min(100.0, float(percent)))
            self.message = str(message)
            current = self.percent
            current_message = self.message
        try:
            self.context.window_manager.progress_update(current)
            if self.context.workspace:
                self.context.workspace.status_text_set(
                    "AGR Collision {:>3}% - {}".format(
                        int(current), current_message))
        except Exception:
            pass
        if self.enabled:
            _console_progress(current, current_message)

    def _heartbeat(self):
        while not self._stop.wait(5.0):
            with self._lock:
                percent = self.percent
                message = self.message
            elapsed = int(time.monotonic() - self.started)
            _console_progress(
                percent,
                "{} - still working ({}s elapsed)".format(
                    message, elapsed),
            )

    def __exit__(self, exc_type, exc, _traceback):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.25)
        try:
            self.context.window_manager.progress_end()
            if self.context.workspace:
                self.context.workspace.status_text_set(None)
        except Exception:
            pass
        if self.enabled:
            _console_progress(
                100,
                "{} - {} after {:.1f}s".format(
                    self.label,
                    "failed" if exc_type else "finished",
                    time.monotonic() - self.started,
                ),
            )


def _safe_collection_name(base):
    value = "AGR_COLLISION__{}".format(base)
    return value[:63]


def _source_base_from_context(context):
    settings = context.scene.xivgate_agr_collision
    active = context.view_layer.objects.active
    if active and active.type == "MESH" and not naming.is_any_collider(active):
        return active.name
    return settings.last_source or None


def _generated_colliders(scene, base):
    return [
        ob
        for ob in scene.objects
        if ob.type == "MESH" and naming.is_collider_of(ob, base)
    ]


def _remove_objects(objects):
    for ob in list(objects):
        mesh = ob.data if ob.type == "MESH" else None
        bpy.data.objects.remove(ob, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _create_collider_collection(
        context, source_data, result, settings, destination_collection=None):
    old_colliders = _generated_colliders(context.scene, source_data.name)
    old_collections = {
        owner
        for ob in old_colliders
        for owner in ob.users_collection
    }
    owns_collection = destination_collection is None
    if owns_collection:
        temporary_name = "AGR_COLLISION_TMP"
        collection = bpy.data.collections.new(temporary_name)
        context.scene.collection.children.link(collection)
    else:
        collection = destination_collection
    created = []
    desired_names = []

    try:
        for index, (vertices, faces) in enumerate(result.hulls, 1):
            name = naming.collider_name(source_data.name, index)
            if len(name.encode("utf-8")) > naming.BLENDER_NAME_LIMIT:
                raise ValueError(
                    "Source name is too long for Blender UCX naming: {}".format(
                        source_data.name
                    )
                )
            mesh = bpy.data.meshes.new("AGR_TMP_COLLIDER_MESH_{:03d}".format(index))
            mesh.from_pydata(vertices.tolist(), [], faces.tolist())
            mesh.materials.clear()
            mesh.update(calc_edges=True)

            ob = bpy.data.objects.new("AGR_TMP_COLLIDER_{:03d}".format(index), mesh)
            collection.objects.link(ob)
            ob[naming.SOURCE_PROP] = source_data.name
            ob["agr_generated"] = True
            ob["agr_source_objects"] = ", ".join(source_data.object_names)
            ob["agr_tolerance"] = settings.tolerance
            ob["agr_seed"] = result.seed
            ob.display_type = "WIRE" if settings.wire_display else "SOLID"
            ob.color = (0.12, 0.65, 1.0, 1.0)
            created.append(ob)
            desired_names.append(name)
    except Exception:
        _remove_objects(created)
        if owns_collection and bpy.data.collections.get(collection.name) is collection:
            bpy.data.collections.remove(collection)
        raise

    old_names = []
    for index, ob in enumerate(old_colliders, 1):
        old_names.append((ob, ob.name, ob.data.name))
        ob.name = "__AGR_COLLISION_BACKUP_{:03d}".format(index)
        ob.data.name = "__AGR_COLLISION_BACKUP_MESH_{:03d}".format(index)
    for ob, desired_name in zip(created, desired_names):
        ob.data.name = desired_name
        ob.name = desired_name
    return collection, created, {
        "old_colliders": old_colliders,
        "old_collections": old_collections,
        "old_names": old_names,
        "owns_collection": owns_collection,
    }


def _rollback_collider_swap(collection, created, transaction):
    _remove_objects(created)
    for ob, object_name, mesh_name in transaction["old_names"]:
        if ob.name in bpy.data.objects:
            ob.name = object_name
            ob.data.name = mesh_name
    if (
            transaction["owns_collection"]
            and bpy.data.collections.get(collection.name) is collection):
        bpy.data.collections.remove(collection)


def _commit_collider_swap(collection, source_name, transaction):
    _remove_objects(transaction["old_colliders"])
    for old_collection in transaction["old_collections"]:
        if (
            old_collection.users == 0
            or (
                not old_collection.objects
                and old_collection.name.startswith("AGR_COLLISION__")
            )
        ):
            bpy.data.collections.remove(old_collection)
    if transaction["owns_collection"]:
        collection.name = _safe_collection_name(source_name)


def generate_for_objects(
        context, objects, base_name, destination_collection=None,
        settings=None, progress=None):
    """Generate one UCX set for an explicit proxy list.

    This is the stable integration entry point used by AGR Prepare. It avoids
    mutating viewport selection and lets the caller place UCX beside the
    prepared render mesh in the same generated collection.
    """
    settings = settings or context.scene.xivgate_agr_collision
    progress = progress or (lambda _percent, _message: None)
    progress(5, "Collecting collision source geometry")
    source_data = source.collect_objects(
        context, settings, objects, name=base_name)
    progress(
        20,
        "Searching convex decomposition for {:,} source triangles".format(
            source_data.raw_triangles),
    )
    result = decompose.decompose(source_data, settings)
    progress(75, "Checking decomposition completeness and triangle budget")
    if not result.complete:
        raise RuntimeError(result.warnings[-1])
    if not result.hulls:
        raise RuntimeError("The decomposition did not produce any hulls")

    budget = validation.agr_triangle_budget(source_data.raw_triangles)
    if result.total_triangles > budget:
        raise RuntimeError(
            "Generated {:,} triangles, exceeding the AGR budget of {:,}".format(
                result.total_triangles,
                budget,
            )
        )

    collection, colliders, transaction = _create_collider_collection(
        context,
        source_data,
        result,
        settings,
        destination_collection=destination_collection,
    )
    try:
        progress(88, "Validating convexity, closure, intersections and names")
        report = validation.validate_colliders(
            colliders,
            expected_base=source_data.name,
            triangle_budget=budget,
        )
        if not report.valid:
            raise RuntimeError(report.errors[0])
    except Exception:
        _rollback_collider_swap(collection, colliders, transaction)
        raise
    _commit_collider_swap(collection, source_data.name, transaction)
    progress(98, "Committing validated UCX set atomically")

    settings.last_source = source_data.name
    settings.last_colliders = len(result.hulls)
    settings.last_triangles = result.total_triangles
    settings.last_deviation = result.max_deviation
    settings.last_input_triangles = source_data.raw_triangles
    settings.last_proxy_triangles = source_data.proxy_triangles
    return {
        "collection": collection,
        "colliders": colliders,
        "source": source_data,
        "decomposition": result,
        "validation": report,
        "budget": budget,
    }


class AGR_OT_analyze_selected(bpy.types.Operator):
    bl_idname = "xivgate_agr_collision.analyze_selected"
    bl_label = "Analyze Selected"
    bl_description = "Analyze selected objects through the same hidden preprocessing used by generation"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return bool(source.selected_source_objects(context))

    def execute(self, context):
        settings = context.scene.xivgate_agr_collision
        started = time.perf_counter()
        try:
            source_data = source.collect_source(context, settings)
        except Exception as exc:
            settings.last_status = translations.iface("Analysis failed")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        elapsed = time.perf_counter() - started
        settings.last_source = source_data.name
        settings.last_input_triangles = source_data.raw_triangles
        settings.last_proxy_triangles = source_data.proxy_triangles
        settings.last_status = translations.iface("Analyzed in {:.2f}s").format(
            elapsed
        )
        message = (
            "{} source object(s), {:,} input tris, {:,} proxy tris, "
            "{} capped boundary group(s), {} outward closed shell(s), "
            "{} skipped component(s)"
        ).format(
            len(source_data.object_names),
            source_data.raw_triangles,
            source_data.proxy_triangles,
            source_data.capped_boundaries,
            source_data.oriented_closed_shells,
            len(source_data.skipped_components),
        )
        self.report({"INFO"}, message)
        return {"FINISHED"}


class AGR_OT_generate(bpy.types.Operator):
    bl_idname = "xivgate_agr_collision.generate"
    bl_label = "Generate / Regenerate"
    bl_description = "Build a new convex UCX set and atomically replace the previous generated set"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(source.selected_source_objects(context))

    def execute(self, context):
        settings = context.scene.xivgate_agr_collision
        settings.last_status = translations.iface("Building proxy...")
        started = time.perf_counter()
        opened_console = (
            _open_progress_console()
            if settings.show_progress_console else False)

        try:
            with _ProgressSession(
                    context, settings.show_progress_console,
                    "Generate / Regenerate") as progress:
                settings.last_status = translations.iface(
                    "Searching convex decomposition..."
                )
                progress(2, "Reading selected proxy objects")
                selected = source.selected_source_objects(context)
                active = context.view_layer.objects.active
                base_name = (
                    active.name if active in selected else selected[0].name)
                generated = generate_for_objects(
                    context,
                    selected,
                    base_name=base_name,
                    settings=settings,
                    progress=progress,
                )
                progress(100, "Validated UCX set is ready")
            source_data = generated["source"]
            result = generated["decomposition"]

            if settings.hide_sources:
                for name in source_data.object_names:
                    ob = bpy.data.objects.get(name)
                    if ob is not None:
                        ob.hide_set(True)

            elapsed = time.perf_counter() - started
            if result.warnings:
                settings.last_status = translations.iface(
                    "Generated with warnings in {:.2f}s"
                ).format(elapsed)
                self.report({"WARNING"}, result.warnings[0])
            else:
                settings.last_status = translations.iface(
                    "Valid result in {:.2f}s"
                ).format(elapsed)
                self.report(
                    {"INFO"},
                    "Generated {} UCX hulls, {} triangles".format(
                        len(result.hulls),
                        result.total_triangles,
                    ),
                )
            return {"FINISHED"}
        except Exception as exc:
            settings.last_status = translations.iface("Generation failed")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            _close_progress_console_later(opened_console)


class AGR_OT_validate(bpy.types.Operator):
    bl_idname = "xivgate_agr_collision.validate"
    bl_label = "Validate Colliders"
    bl_description = "Validate naming, convexity, closure, intersections and AGR triangle budget"
    bl_options = {"REGISTER"}

    def execute(self, context):
        settings = context.scene.xivgate_agr_collision
        base = _source_base_from_context(context)
        if not base:
            self.report({"ERROR"}, "Select the source object or generate colliders first")
            return {"CANCELLED"}
        colliders = _generated_colliders(context.scene, base)
        if not colliders:
            self.report({"ERROR"}, "No generated colliders found for {}".format(base))
            return {"CANCELLED"}

        source_triangles = settings.last_input_triangles
        budget = validation.agr_triangle_budget(source_triangles) if source_triangles else None
        report = validation.validate_colliders(
            colliders,
            expected_base=base,
            triangle_budget=budget,
        )
        settings.last_colliders = report.collider_count
        settings.last_triangles = report.triangle_count
        if report.valid:
            settings.last_status = translations.iface("Validation passed")
            self.report(
                {"INFO"},
                "Valid: {} hulls, {} triangles".format(
                    report.collider_count,
                    report.triangle_count,
                ),
            )
            return {"FINISHED"}

        settings.last_status = translations.iface("Validation failed")
        self.report({"ERROR"}, report.errors[0])
        return {"CANCELLED"}


class AGR_OT_remove_generated(bpy.types.Operator):
    bl_idname = "xivgate_agr_collision.remove_generated"
    bl_label = "Remove Generated"
    bl_description = "Remove only generated colliders associated with the active source"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.xivgate_agr_collision
        base = _source_base_from_context(context)
        if not base:
            self.report({"ERROR"}, "Select the source object or generate colliders first")
            return {"CANCELLED"}
        colliders = _generated_colliders(context.scene, base)
        count = len(colliders)
        collections = {
            collection
            for ob in colliders
            for collection in ob.users_collection
        }
        _remove_objects(colliders)
        for collection in collections:
            if not collection.objects and collection.name.startswith("AGR_COLLISION__"):
                bpy.data.collections.remove(collection)
        settings.last_status = translations.iface(
            "Removed {} collider(s)"
        ).format(count)
        settings.last_colliders = 0
        settings.last_triangles = 0
        self.report({"INFO"}, settings.last_status)
        return {"FINISHED"}


CLASSES = (
    AGR_OT_analyze_selected,
    AGR_OT_generate,
    AGR_OT_validate,
    AGR_OT_remove_generated,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
