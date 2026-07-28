# SPDX-FileCopyrightText: 2026 XIVgate
# SPDX-License-Identifier: GPL-3.0-or-later
# Validation of generated collision objects.

from dataclasses import dataclass, field
import math

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
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    return bm


def _bvh_from_object(ob):
    vertices = [ob.matrix_world @ vertex.co for vertex in ob.data.vertices]
    ob.data.calc_loop_triangles()
    polygons = [tuple(triangle.vertices) for triangle in ob.data.loop_triangles]
    return BVHTree.FromPolygons(vertices, polygons, all_triangles=True, epsilon=1.0e-7)


def validate_colliders(colliders, expected_base=None, triangle_budget=None):
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
                    difference = abs(hull_volume - source_volume) / hull_volume
                    if difference > 1.0e-4:
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

    return ValidationReport(
        valid=not errors,
        collider_count=len(colliders),
        triangle_count=total_triangles,
        errors=errors,
        warnings=warnings,
    )


def agr_triangle_budget(source_triangles):
    if source_triangles < 50_000:
        return 15_000
    return min(100_000, int(math.ceil(source_triangles * 0.05)))
