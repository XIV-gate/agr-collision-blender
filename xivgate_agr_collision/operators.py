# SPDX-FileCopyrightText: 2026 XIVgate
# SPDX-License-Identifier: GPL-3.0-or-later
# Blender operators for analysis, generation, validation and cleanup.

import ctypes
import datetime
import json
import math
import os
import sys
import threading
import time
import traceback

import bpy
from mathutils import Vector

from .core import decompose
from .core import inspection
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


def active_collision_set(context):
    """Return the base name and colliders owned by the active mesh."""
    if context is None or context.scene is None or context.view_layer is None:
        return None, []
    active = context.view_layer.objects.active
    if active is None or active.type != "MESH":
        return None, []

    if naming.is_any_collider(active):
        base = active.get(naming.SOURCE_PROP)
        if not base:
            parsed = naming.parse(active.name)
            base = parsed[0] if parsed else None
    else:
        base = active.name
    if not base:
        return None, []
    return str(base), _generated_colliders(context.scene, str(base))


def active_wire_display(context):
    """Report the actual wire state of the active collision set."""
    _base, colliders = active_collision_set(context)
    return bool(colliders) and all(
        collider.display_type == "WIRE" for collider in colliders
    )


def apply_wire_display(scene, enabled, colliders=None):
    """Apply the viewport style to one explicit generated collider set."""
    if colliders is None:
        colliders = []
    display_type = "WIRE" if enabled else "SOLID"
    for collider in colliders:
        if collider.name in scene.objects:
            collider.display_type = display_type


def _source_objects_for_colliders(scene, colliders=None):
    if colliders is None:
        colliders = [
            ob
            for ob in scene.objects
            if ob.type == "MESH" and ob.get("agr_generated")
    ]
    names = []
    for collider in colliders:
        if "agr_source_objects_json" in collider:
            stored = str(collider.get("agr_source_objects_json", ""))
            try:
                decoded = json.loads(stored)
            except (json.JSONDecodeError, TypeError):
                decoded = []
            if not isinstance(decoded, list):
                decoded = []
        else:
            # Compatibility with the comma-separated property written by
            # AGR Collision <= 1.2.6. It must never be guessed as JSON because
            # valid Blender object names can themselves look like JSON.
            stored = str(collider.get("agr_source_objects", ""))
            decoded = stored.split(", ") if stored else []
        names.extend(
            name for name in decoded
            if isinstance(name, str) and name
        )

    result = []
    seen = set()
    for name in names:
        source_object = scene.objects.get(name)
        if source_object is None or source_object.as_pointer() in seen:
            continue
        seen.add(source_object.as_pointer())
        result.append(source_object)
    return result


def apply_source_visibility(scene, hidden, view_layer=None, colliders=None):
    """Apply the reversible source-visibility setting to generated sets."""
    for source_object in _source_objects_for_colliders(scene, colliders):
        source_object.hide_set(bool(hidden), view_layer=view_layer)


def active_sources_hidden(context):
    """Report the actual source visibility of the active collision set."""
    _base, colliders = active_collision_set(context)
    sources = _source_objects_for_colliders(context.scene, colliders)
    return bool(sources) and all(
        source_object.hide_get(view_layer=context.view_layer)
        for source_object in sources
    )


def _remove_objects(objects):
    for ob in list(objects):
        mesh = ob.data if ob.type == "MESH" else None
        bpy.data.objects.remove(ob, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _create_collider_collection(
        context, source_data, result, settings, destination_collection=None,
        origin_world=None):
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
    pivot = Vector(
        origin_world if origin_world is not None else (0.0, 0.0, 0.0))

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
            mesh.from_pydata([
                (
                    float(vertex[0]) - pivot.x,
                    float(vertex[1]) - pivot.y,
                    float(vertex[2]) - pivot.z,
                )
                for vertex in vertices
            ], [], faces.tolist())
            mesh.materials.clear()
            mesh.update(calc_edges=True)

            ob = bpy.data.objects.new("AGR_TMP_COLLIDER_{:03d}".format(index), mesh)
            collection.objects.link(ob)
            ob.matrix_world.translation = pivot
            ob[naming.SOURCE_PROP] = source_data.name
            ob["agr_generated"] = True
            ob["agr_source_objects_json"] = json.dumps(
                source_data.object_names,
                ensure_ascii=False,
            )
            ob["agr_feature_tolerance"] = result.feature_tolerance
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
        settings=None, progress=None, origin_world=None):
    """Generate one UCX set for an explicit proxy list.

    This is the stable integration entry point used by AGR Prepare. It avoids
    mutating viewport selection and lets the caller place UCX beside the
    prepared render mesh in the same generated collection.
    """
    settings = settings or context.scene.xivgate_agr_collision
    progress = progress or (lambda _percent, _message: None)
    pivot = Vector(
        origin_world
        if origin_world is not None
        else objects[0].matrix_world.translation)
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
        origin_world=pivot,
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
    scene_settings = getattr(context.scene, "xivgate_agr_collision", None)
    if scene_settings is not None:
        # The debugger checks the set that was generated last unless the
        # user points it somewhere else.
        scene_settings.debug_collection = collection

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
        "origin_world": list(pivot),
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
                existing_colliders = _generated_colliders(
                    context.scene,
                    base_name,
                )
                if existing_colliders:
                    settings.wire_display = all(
                        collider.display_type == "WIRE"
                        for collider in existing_colliders
                    )
                    existing_sources = _source_objects_for_colliders(
                        context.scene,
                        existing_colliders,
                    )
                    if existing_sources:
                        settings.hide_sources = all(
                            source_object.hide_get(
                                view_layer=context.view_layer,
                            )
                            for source_object in existing_sources
                        )
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
                apply_source_visibility(
                    context.scene,
                    True,
                    view_layer=context.view_layer,
                    colliders=generated["colliders"],
                )

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


class AGR_OT_toggle_wire_display(bpy.types.Operator):
    bl_idname = "xivgate_agr_collision.toggle_wire_display"
    bl_label = "Wire Display"
    bl_description = "Toggle wire display for the active AGR collision set"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        _base, colliders = active_collision_set(context)
        return bool(colliders)

    def execute(self, context):
        _base, colliders = active_collision_set(context)
        enabled = not active_wire_display(context)
        apply_wire_display(context.scene, enabled, colliders=colliders)
        context.scene.xivgate_agr_collision.wire_display = enabled
        return {"FINISHED"}


class AGR_OT_toggle_source_visibility(bpy.types.Operator):
    bl_idname = "xivgate_agr_collision.toggle_source_visibility"
    bl_label = "Hide Sources After Generation"
    bl_description = "Hide or restore source objects for the active AGR collision set"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        _base, colliders = active_collision_set(context)
        return bool(
            colliders
            and _source_objects_for_colliders(context.scene, colliders)
        )

    def execute(self, context):
        _base, colliders = active_collision_set(context)
        hidden = not active_sources_hidden(context)
        apply_source_visibility(
            context.scene,
            hidden,
            view_layer=context.view_layer,
            colliders=colliders,
        )
        context.scene.xivgate_agr_collision.hide_sources = hidden
        return {"FINISHED"}


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


def debug_targets(settings):
    """Mesh objects of the debugger collection, including nested collections."""
    collection = settings.debug_collection
    if collection is None:
        return []
    return sorted(
        (
            ob for ob in collection.all_objects
            if ob.type == "MESH"
            and (not settings.debug_colliders_only or naming.is_any_collider(ob))
        ),
        key=lambda ob: ob.name,
    )


def _select_only(context, objects):
    """Replace the selection with ``objects``; the first becomes active.

    Objects hidden in the viewport or outside the view layer cannot be
    selected; they are counted and left alone rather than unhidden.
    """
    view_layer = context.view_layer
    for ob in view_layer.objects:
        if ob.select_get(view_layer=view_layer):
            ob.select_set(False, view_layer=view_layer)
    selected = []
    unreachable = 0
    for ob in objects:
        if (
            view_layer.objects.get(ob.name) is not ob
            or not ob.visible_get(view_layer=view_layer)
        ):
            unreachable += 1
            continue
        ob.select_set(True, view_layer=view_layer)
        selected.append(ob)
    if selected:
        view_layer.objects.active = selected[0]
    return selected, unreachable


class _DebugOperator:
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = context.scene.xivgate_agr_collision
        return settings.debug_collection is not None and context.mode == "OBJECT"

    def finish(self, context, found, total, summary):
        settings = context.scene.xivgate_agr_collision
        selected, unreachable = _select_only(context, [ob for ob, _value in found])
        if not total:
            status = translations.iface("No mesh objects to check")
        elif not found:
            status = summary["none"].format(total)
        else:
            worst_ob, worst_value = found[0]
            status = summary["found"].format(
                len(found), total, summary["value"](worst_value), worst_ob.name
            )
            if unreachable:
                status += translations.iface(" ({} hidden, not selected)").format(
                    unreachable
                )
        settings.debug_status = status
        self.report({"INFO"}, status)
        for ob, value in found[:50]:
            print("AGR debugger: {:<40} {}".format(ob.name, summary["value"](value)))
        return {"FINISHED"}


def _millimetres(value):
    return translations.iface("{:.4f} mm").format(value * 1000.0)


def _cubic_metres(value):
    return translations.iface("{:.6f} m3").format(value)


class AGR_OT_debug_concave(_DebugOperator, bpy.types.Operator):
    bl_idname = "xivgate_agr_collision.debug_concave"
    bl_label = "Select Concave Hulls"
    bl_description = (
        "Select objects whose surface sinks below their own convex hull, "
        "including open meshes; the deepest one becomes active"
    )

    def execute(self, context):
        settings = context.scene.xivgate_agr_collision
        targets = debug_targets(settings)
        found = []
        for ob in targets:
            report = inspection.inspect_object(
                ob, settings.debug_concavity_tolerance, measure=("concavity",)
            )
            if not report.closed:
                # An open mesh has no inside; it can never be a valid hull.
                found.append((ob, math.inf))
            elif report.concavity > 0.0:
                found.append((ob, report.concavity))
        found.sort(key=lambda item: -item[1])
        return self.finish(context, found, len(targets), {
            "none": translations.iface("No concave objects among {}"),
            "found": translations.iface("Concave: {} of {}; deepest {} in {}"),
            "value": lambda value: (
                translations.iface("open mesh") if value == math.inf else _millimetres(value)
            ),
        })


class AGR_OT_debug_small(_DebugOperator, bpy.types.Operator):
    bl_idname = "xivgate_agr_collision.debug_small"
    bl_label = "Select Small Parts"
    bl_description = (
        "Select objects whose enclosed volume is below the threshold; the "
        "smallest one becomes active"
    )

    def execute(self, context):
        settings = context.scene.xivgate_agr_collision
        targets = debug_targets(settings)
        found = []
        for ob in targets:
            report = inspection.inspect_object(ob, measure=("volume",))
            if report.volume < settings.debug_min_volume:
                found.append((ob, report.volume))
        found.sort(key=lambda item: item[1])
        return self.finish(context, found, len(targets), {
            "none": translations.iface("No small objects among {}"),
            "found": translations.iface("Small: {} of {}; smallest {} in {}"),
            "value": _cubic_metres,
        })


class AGR_OT_debug_thin(_DebugOperator, bpy.types.Operator):
    bl_idname = "xivgate_agr_collision.debug_thin"
    bl_label = "Select Thin Parts"
    bl_description = (
        "Select objects whose exact minimum thickness is below the threshold; "
        "the thinnest one becomes active"
    )

    def execute(self, context):
        settings = context.scene.xivgate_agr_collision
        targets = debug_targets(settings)
        found = []
        for ob in targets:
            report = inspection.inspect_object(ob, measure=("thickness",))
            if report.thickness < settings.debug_min_thickness:
                found.append((ob, report.thickness))
        found.sort(key=lambda item: item[1])
        return self.finish(context, found, len(targets), {
            "none": translations.iface("No thin objects among {}"),
            "found": translations.iface("Thin: {} of {}; thinnest {} in {}"),
            "value": _millimetres,
        })


CLASSES = (
    AGR_OT_analyze_selected,
    AGR_OT_generate,
    AGR_OT_toggle_wire_display,
    AGR_OT_toggle_source_visibility,
    AGR_OT_validate,
    AGR_OT_remove_generated,
    AGR_OT_debug_concave,
    AGR_OT_debug_small,
    AGR_OT_debug_thin,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
