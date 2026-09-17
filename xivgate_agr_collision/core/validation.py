# SPDX-FileCopyrightText: 2026 XIVgate
# SPDX-License-Identifier: GPL-3.0-or-later
# Validation of generated collision objects.

from dataclasses import dataclass, field
import math
import re

import bmesh
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import decompose
from . import naming


@dataclass
class ValidationReport:
    valid: bool
    collider_count: int
    triangle_count: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _world_bmesh(ob):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bm.transform(ob.matrix_world)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    return bm


def _bvh_from_object(ob):
    vertices = [ob.matrix_world @ vertex.co for vertex in ob.data.vertices]
    ob.data.calc_loop_triangles()
    polygons = [tuple(triangle.vertices) for triangle in ob.data.loop_triangles]
    return BVHTree.FromPolygons(vertices, polygons, all_triangles=True, epsilon=1.0e-7)


def validate_colliders(
    colliders,
    expected_base=None,
    triangle_budget=None,
    checker_tolerances=None,
    model_polygons=None,
):
    errors = []
    warnings = []
    total_triangles = 0
    bvhs = []
    pieces = []

    for index, ob in enumerate(sorted(colliders, key=lambda item: item.name), 1):
        if expected_base is not None:
            expected_name = naming.collider_name(expected_base, index)
            if ob.name != expected_name:
                errors.append("{} should be named {}".format(ob.name, expected_name))
        if ob.data.materials:
            errors.append("{} has material slots".format(ob.name))
        if any(abs(value - 1.0) > 1.0e-6 for value in ob.scale):
            errors.append("{} has unapplied scale".format(ob.name))
        if any(abs(value) > 1.0e-6 for value in ob.rotation_euler):
            errors.append("{} has unapplied rotation".format(ob.name))

        bm = _world_bmesh(ob)
        try:
            boundary = sum(edge.is_boundary for edge in bm.edges)
            non_manifold = sum(not edge.is_manifold for edge in bm.edges)
            if boundary or non_manifold:
                errors.append(
                    "{} is not closed/manifold ({} boundary, {} non-manifold edges)".format(
                        ob.name,
                        boundary,
                        non_manifold,
                    )
                )
            total_triangles += len(bm.faces)

            hull = bmesh.new()
            try:
                hull_vertices = [hull.verts.new(vertex.co) for vertex in bm.verts]
                bmesh.ops.convex_hull(hull, input=hull_vertices, use_existing_faces=False)
                source_volume = abs(bm.calc_volume(signed=True))
                hull_volume = abs(hull.calc_volume(signed=True))
                if hull_volume > 1.0e-9:
                    diff_vol = abs(hull_volume - source_volume)
                    difference = diff_vol / hull_volume
                    if difference > 0.05 and diff_vol > 1.0e-3:
                        errors.append("{} is not convex".format(ob.name))
            finally:
                hull.free()
        finally:
            bm.free()
        bvhs.append((ob.name, _bvh_from_object(ob)))
        ob.data.calc_loop_triangles()
        world_vertices = np.asarray(
            [
                tuple(ob.matrix_world @ vertex.co)
                for vertex in ob.data.vertices
            ],
            dtype=np.float64,
        )
        triangle_faces = np.asarray(
            [
                tuple(triangle.vertices)
                for triangle in ob.data.loop_triangles
            ],
            dtype=np.int32,
        )
        pieces.append(
            (
                ob.name,
                decompose._analyse_piece(
                    decompose.Piece(
                        vertices=world_vertices,
                        faces=triangle_faces,
                    )
                ),
            )
        )

    for left_index in range(len(bvhs)):
        for right_index in range(left_index + 1, len(bvhs)):
            left_name, left_bvh = bvhs[left_index]
            right_name, right_bvh = bvhs[right_index]
            if decompose._pieces_overlap(
                pieces[left_index][1],
                pieces[right_index][1],
                clearance=1.0e-7,
            ):
                errors.append("{} intersects {}".format(left_name, right_name))
            elif left_bvh.overlap(right_bvh):
                warnings.append(
                    "{} touches {}; no positive-volume overlap was found".format(
                        left_name,
                        right_name,
                    )
                )

    if triangle_budget is not None and total_triangles > triangle_budget:
        errors.append(
            "Collision triangle count {} exceeds budget {}".format(
                total_triangles,
                triangle_budget,
            )
        )

    if checker_tolerances is not None:
        convex_tolerance, gap_tolerance = checker_tolerances
        checker = agr_checker_report(
            colliders, convex_tolerance, gap_tolerance, model_polygons
        )
        if not checker.passed:
            errors.append(
                "SINTEZ AGR Checker would reject the set: {} (convexity {:.1f} mm, "
                "gap {:.1f} mm); first: {}".format(
                    checker.summary(),
                    convex_tolerance * 1000.0,
                    gap_tolerance * 1000.0,
                    ", ".join(checker.failing_names[:3]),
                )
            )

    return ValidationReport(
        valid=not errors,
        collider_count=len(colliders),
        triangle_count=total_triangles,
        errors=errors,
        warnings=warnings,
    )


# SINTEZ AGR Checker defaults, in metres. Its scene settings hold them in mm.
AGR_CHECKER_CONVEX_TOLERANCE = 0.01
AGR_CHECKER_GAP_TOLERANCE = 0.0002


@dataclass
class AgrCheckerReport:
    open: list[str] = field(default_factory=list)
    non_manifold: list[str] = field(default_factory=list)
    non_convex: list[str] = field(default_factory=list)
    intersections: list[tuple[str, str]] = field(default_factory=list)
    gaps: list[tuple[str, str]] = field(default_factory=list)
    numbering: list[str] = field(default_factory=list)
    duplicate_numbers: list[str] = field(default_factory=list)
    with_uv: list[str] = field(default_factory=list)
    with_materials: list[str] = field(default_factory=list)
    non_triangles: list[str] = field(default_factory=list)
    polygon_count: int = 0
    polygon_limit: int = 0

    @property
    def over_polygon_limit(self):
        return bool(self.polygon_limit) and self.polygon_count > self.polygon_limit

    @property
    def passed(self):
        return not (
            self.open or self.non_manifold or self.non_convex
            or self.intersections or self.gaps or self.numbering
            or self.with_uv or self.with_materials or self.non_triangles
            or self.over_polygon_limit
        )

    @property
    def failing_names(self):
        names = set(self.open) | set(self.non_manifold) | set(self.non_convex)
        names |= set(self.duplicate_numbers) | set(self.with_uv)
        names |= set(self.with_materials) | set(self.non_triangles)
        for left, right in self.intersections + self.gaps:
            names.update((left, right))
        return sorted(names)

    def summary(self):
        parts = []
        for label, items in (
            ("open", self.open),
            ("non-manifold", self.non_manifold),
            ("non-convex", self.non_convex),
            ("intersecting pairs", self.intersections),
            ("pairs closer than the gap tolerance", self.gaps),
            ("numbering errors", self.numbering),
            ("with UV maps", self.with_uv),
            ("with materials", self.with_materials),
            ("with non-triangle polygons", self.non_triangles),
        ):
            if items:
                parts.append("{} {}".format(len(items), label))
        if self.over_polygon_limit:
            parts.append("{} UCX polygons above the limit of {}".format(
                self.polygon_count, self.polygon_limit))
        return ", ".join(parts)


def agr_checker_tolerances(scene):
    """Convexity and gap tolerances of SINTEZ AGR Checker, in metres.

    When the checker is installed its own scene settings are used, so a set
    is judged exactly as the checker will judge it; otherwise its defaults.
    """
    settings = getattr(scene, "agr_scene_properties", None)
    convex = getattr(settings, "ucx_convex_tolerance", None)
    gap = getattr(settings, "ucx_gap_tolerance", None)
    return (
        float(convex) / 1000.0 if convex is not None else AGR_CHECKER_CONVEX_TOLERANCE,
        float(gap) / 1000.0 if gap is not None else AGR_CHECKER_GAP_TOLERANCE,
    )


def _face_plane_convex(ob, tolerance):
    """The face-plane convexity rule SINTEZ AGR Checker applies to a UCX.

    For every face wider than the tolerance, the other vertices must not lie
    beyond the tolerance on both sides of its plane. Normals, centres and
    areas come from Blender's own mesh data, as in the checker.
    """
    mesh = ob.data
    coordinates = [vertex.co for vertex in mesh.vertices]
    for polygon in mesh.polygons:
        normal = polygon.normal
        if normal.length_squared < 0.5:
            continue
        longest = max(
            (coordinates[a] - coordinates[b]).length for a, b in polygon.edge_keys
        )
        if longest <= 0.0 or 2.0 * polygon.area / longest < tolerance:
            continue
        center = polygon.center
        own = set(polygon.vertices)
        positive = negative = False
        for index, co in enumerate(coordinates):
            if index in own:
                continue
            distance = normal.dot(co - center)
            if -tolerance < distance < tolerance:
                continue
            if distance > 0.0:
                positive = True
            else:
                negative = True
            if positive and negative:
                return False
    return True


def _checker_collision_data(ob):
    bm = bmesh.new()
    try:
        bm.from_mesh(ob.data)
        bm.transform(ob.matrix_world)
        bm.normal_update()
        vertices = [vertex.co.copy() for vertex in bm.verts]
        planes = [(face.calc_center_median(), face.normal.copy()) for face in bm.faces]
        bvh = BVHTree.FromBMesh(bm)
    finally:
        bm.free()
    if vertices:
        lower = Vector((min(v.x for v in vertices), min(v.y for v in vertices), min(v.z for v in vertices)))
        upper = Vector((max(v.x for v in vertices), max(v.y for v in vertices), max(v.z for v in vertices)))
    else:
        lower = upper = Vector((0.0, 0.0, 0.0))
    return {"bvh": bvh, "vertices": vertices, "planes": planes, "lower": lower, "upper": upper}


def _checker_nested(vertices, planes, tolerance=0.0001):
    if not vertices or not planes:
        return False
    return all(
        (vertex - center).dot(normal) <= tolerance
        for vertex in vertices
        for center, normal in planes
    )


_NUMBER_PATTERN = re.compile(r"_(\d{3})$")


def agr_checker_polygon_limit(model_polygons):
    """UCX polygon limit SINTEZ AGR Checker derives from the model's polygons.

    15 000 below 50 000 model polygons, otherwise 5 % of them, compared
    strictly - so the largest allowed count is the floor of that 5 %.
    """
    if model_polygons < 50_000:
        return 15_000
    return int(math.floor(model_polygons * 0.05))


def _numbering_errors(colliders, report):
    """Numbers after the last underscore must run 1..N without gaps or repeats.

    Names without a trailing three-digit number are not part of the series,
    as in the checker, where their format is a naming-mask matter.
    """
    numbers = {}
    for name in sorted({ob.name for ob in colliders}):
        match = _NUMBER_PATTERN.search(name)
        if match:
            numbers.setdefault(int(match.group(1)), []).append(name)
    if not numbers:
        return
    for number, names in sorted(numbers.items()):
        if len(names) > 1:
            report.numbering.append(
                "number {:03d} repeats ({})".format(number, ", ".join(names)))
            report.duplicate_numbers.extend(names)
    ordered = sorted(numbers)
    if ordered[0] != 1:
        report.numbering.append(
            "numbering starts at {:03d}, not 001".format(ordered[0]))
    for number in range(ordered[0], ordered[-1]):
        if number not in numbers:
            report.numbering.append("number {:03d} is missing".format(number))


def agr_checker_report(
    colliders, convex_tolerance=None, gap_tolerance=None, model_polygons=None
):
    """Judge a UCX set by the rules SINTEZ AGR Checker applies to it.

    Closure by Euler characteristic, non-manifold edges, the face-plane
    convexity rule, and for every pair with touching bounds: triangle
    overlap, one hull nested in another, and any vertex closer than the gap
    tolerance to the other hull. Also the checker's rules for collision
    objects beyond geometry: continuous 001..N numbering, no UV maps, no
    materials, triangles only, and - when ``model_polygons`` is known - the
    UCX polygon limit. Reimplemented from the checker's behaviour so a
    generated set can be refused before the checker sees it.
    """
    if convex_tolerance is None:
        convex_tolerance = AGR_CHECKER_CONVEX_TOLERANCE
    if gap_tolerance is None:
        gap_tolerance = AGR_CHECKER_GAP_TOLERANCE
    report = AgrCheckerReport()
    colliders = sorted(colliders, key=lambda item: item.name)
    _numbering_errors(colliders, report)
    for ob in colliders:
        mesh = ob.data
        report.polygon_count += len(mesh.polygons)
        if len(mesh.uv_layers):
            report.with_uv.append(ob.name)
        if len(mesh.materials):
            report.with_materials.append(ob.name)
        if any(len(polygon.vertices) != 3 for polygon in mesh.polygons):
            report.non_triangles.append(ob.name)
        if len(mesh.vertices) - len(mesh.edges) + len(mesh.polygons) != 2:
            report.open.append(ob.name)
            continue
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            non_manifold = any(not edge.is_manifold for edge in bm.edges)
        finally:
            bm.free()
        if non_manifold:
            report.non_manifold.append(ob.name)
            continue
        if not _face_plane_convex(ob, convex_tolerance):
            report.non_convex.append(ob.name)

    data = {ob: _checker_collision_data(ob) for ob in colliders}
    for index, left in enumerate(colliders):
        first = data[left]
        for right in colliders[index + 1:]:
            second = data[right]
            if (
                first["lower"].x > second["upper"].x + gap_tolerance
                or first["upper"].x < second["lower"].x - gap_tolerance
                or first["lower"].y > second["upper"].y + gap_tolerance
                or first["upper"].y < second["lower"].y - gap_tolerance
                or first["lower"].z > second["upper"].z + gap_tolerance
                or first["upper"].z < second["lower"].z - gap_tolerance
            ):
                continue
            pair = (left.name, right.name)
            if first["bvh"].overlap(second["bvh"]):
                report.intersections.append(pair)
                continue
            if _checker_nested(second["vertices"], first["planes"]) or _checker_nested(
                first["vertices"], second["planes"]
            ):
                report.intersections.append(pair)
                continue
            if any(
                second["bvh"].find_nearest(vertex, gap_tolerance)[0] is not None
                for vertex in first["vertices"]
            ) or any(
                first["bvh"].find_nearest(vertex, gap_tolerance)[0] is not None
                for vertex in second["vertices"]
            ):
                report.gaps.append(pair)
    if model_polygons:
        report.polygon_limit = agr_checker_polygon_limit(model_polygons)
    return report


def agr_triangle_budget(source_triangles):
    """UCX triangle budget: 15 000 below 50 000 source triangles, else 5 %.

    The requirements round 5 % up and cap it at 100 000; SINTEZ AGR Checker
    compares against the exact 5 %, so a count one above it fails there. The
    floor passes both.
    """
    if source_triangles < 50_000:
        return 15_000
    return min(100_000, int(math.floor(source_triangles * 0.05)))
