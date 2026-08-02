# SPDX-FileCopyrightText: 2026 XIVgate
# SPDX-License-Identifier: GPL-3.0-or-later
# Source-mesh collection and hidden proxy generation.
#
# The visual model is never modified. Evaluated meshes are copied to world
# space, fused and repaired without resampling. The working copy exists only
# in memory and preserves the source vertices, planes and architectural corners.

from dataclasses import dataclass, field

import bmesh
import bpy
import numpy as np

from . import naming


@dataclass
class SourceData:
    name: str
    object_names: list[str]
    raw_vertices: np.ndarray
    raw_faces: np.ndarray
    vertices: np.ndarray
    faces: np.ndarray
    skipped_components: list[dict] = field(default_factory=list)
    proxy_mode: str = "SURFACE"
    capped_boundaries: int = 0
    oriented_closed_shells: int = 0

    @property
    def raw_triangles(self):
        return int(len(self.raw_faces))

    @property
    def proxy_triangles(self):
        return int(len(self.faces))


def selected_source_objects(context):
    """Return selected mesh sources, excluding generated UCX objects."""
    objects = [
        ob
        for ob in context.selected_objects
        if ob.type == "MESH" and not naming.is_any_collider(ob)
    ]
    active = context.view_layer.objects.active
    if active in objects:
        objects.remove(active)
        objects.insert(0, active)
    return objects


def _collect_evaluated_meshes(context, objects):
    depsgraph = context.evaluated_depsgraph_get()
    vertices = []
    faces = []
    vertex_offset = 0

    for ob in objects:
        ob_eval = ob.evaluated_get(depsgraph)
        mesh = ob_eval.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
        try:
            mesh.calc_loop_triangles()
            world = ob_eval.matrix_world
            object_vertices = [tuple(world @ vertex.co) for vertex in mesh.vertices]
            vertices.extend(object_vertices)
            faces.extend(
                (
                    triangle.vertices[0] + vertex_offset,
                    triangle.vertices[1] + vertex_offset,
                    triangle.vertices[2] + vertex_offset,
                )
                for triangle in mesh.loop_triangles
            )
            vertex_offset += len(object_vertices)
        finally:
            ob_eval.to_mesh_clear()

    return (
        np.asarray(vertices, dtype=np.float64).reshape((-1, 3)),
        np.asarray(faces, dtype=np.int32).reshape((-1, 3)),
    )


def _remove_degenerate_and_duplicate_faces(vertices, faces, snap_size=0.0):
    if len(vertices) == 0 or len(faces) == 0:
        return vertices[:0], faces[:0]

    points = vertices.copy()
    if snap_size > 0.0:
        points = np.round(points / snap_size) * snap_size

    # Merge coincident vertices after the optional exact coordinate snap.
    rounded = np.round(points, decimals=8)
    unique_vertices, inverse = np.unique(rounded, axis=0, return_inverse=True)
    remapped = inverse[faces]

    keep = (
        (remapped[:, 0] != remapped[:, 1])
        & (remapped[:, 1] != remapped[:, 2])
        & (remapped[:, 2] != remapped[:, 0])
    )
    remapped = remapped[keep]
    if len(remapped) == 0:
        return unique_vertices, remapped.astype(np.int32)

    a = unique_vertices[remapped[:, 1]] - unique_vertices[remapped[:, 0]]
    b = unique_vertices[remapped[:, 2]] - unique_vertices[remapped[:, 0]]
    keep = np.linalg.norm(np.cross(a, b), axis=1) > 1.0e-10
    remapped = remapped[keep]

    # Winding is deliberately ignored when detecting duplicate triangles.
    canonical = np.sort(remapped, axis=1)
    _, unique_face_indices = np.unique(canonical, axis=0, return_index=True)
    remapped = remapped[np.sort(unique_face_indices)]
    canonical = np.sort(remapped, axis=1)
    face_order = np.lexsort(
        (canonical[:, 2], canonical[:, 1], canonical[:, 0])
    )
    remapped = remapped[face_order]

    used = np.unique(remapped.reshape(-1))
    compact_map = np.full(len(unique_vertices), -1, dtype=np.int32)
    compact_map[used] = np.arange(len(used), dtype=np.int32)
    return unique_vertices[used], compact_map[remapped]


def _face_island_map(bm):
    bm.edges.index_update()
    bm.faces.index_update()
    islands = []
    face_to_island = {}
    unseen = set(bm.faces)
    while unseen:
        seed = min(unseen, key=lambda face: face.index)
        unseen.remove(seed)
        stack = [seed]
        faces = [seed]
        while stack:
            face = stack.pop()
            for edge in sorted(face.edges, key=lambda item: item.index):
                for neighbour in sorted(
                    edge.link_faces,
                    key=lambda item: item.index,
                ):
                    if neighbour in unseen:
                        unseen.remove(neighbour)
                        stack.append(neighbour)
                        faces.append(neighbour)
        vertices = {vertex for face in faces for vertex in face.verts}
        coordinates = np.asarray([tuple(vertex.co) for vertex in vertices], dtype=np.float64)
        island_index = len(islands)
        for face in faces:
            face_to_island[face] = island_index
        islands.append(
            {
                "minimum": coordinates.min(axis=0),
                "maximum": coordinates.max(axis=0),
            }
        )
    return islands, face_to_island


def _boundary_groups(bm):
    bm.verts.index_update()
    bm.edges.index_update()
    remaining = {edge for edge in bm.edges if edge.is_boundary}
    groups = []
    while remaining:
        seed = min(remaining, key=lambda edge: edge.index)
        remaining.remove(seed)
        stack = [seed]
        edges = [seed]
        while stack:
            edge = stack.pop()
            for vertex in sorted(
                edge.verts,
                key=lambda item: item.index,
            ):
                for neighbour in sorted(
                    vertex.link_edges,
                    key=lambda item: item.index,
                ):
                    if neighbour in remaining and neighbour.is_boundary:
                        remaining.remove(neighbour)
                        stack.append(neighbour)
                        edges.append(neighbour)
        groups.append(edges)
    return groups


def _orient_closed_shells_outward(bm):
    """Recalculate outward normals only for closed manifold face islands."""
    bm.edges.index_update()
    bm.faces.index_update()
    unseen = set(bm.faces)
    oriented = 0
    while unseen:
        seed = min(unseen, key=lambda face: face.index)
        unseen.remove(seed)
        stack = [seed]
        faces = [seed]
        while stack:
            face = stack.pop()
            for edge in sorted(face.edges, key=lambda item: item.index):
                for neighbour in sorted(
                    edge.link_faces,
                    key=lambda item: item.index,
                ):
                    if neighbour in unseen:
                        unseen.remove(neighbour)
                        stack.append(neighbour)
                        faces.append(neighbour)

        edges = {edge for face in faces for edge in face.edges}
        if edges and all(len(edge.link_faces) == 2 for edge in edges):
            bmesh.ops.recalc_face_normals(bm, faces=faces)
            signed_volume = 0.0
            for face in faces:
                origin = face.verts[0].co
                for index in range(1, len(face.verts) - 1):
                    signed_volume += float(
                        origin.dot(
                            face.verts[index].co.cross(
                                face.verts[index + 1].co
                            )
                        )
                    ) / 6.0
            if signed_volume < 0.0:
                bmesh.ops.reverse_faces(bm, faces=faces)
            oriented += 1
    return oriented


def _selective_cap_boundaries(vertices, faces, tolerance, fuse_distance):
    """Cap obvious cut ends while preserving bounded facade openings."""
    bm = bmesh.new()
    try:
        bm_vertices = [bm.verts.new(tuple(point)) for point in vertices]
        bm.verts.ensure_lookup_table()
        for triangle in faces:
            try:
                bm.faces.new([bm_vertices[int(index)] for index in triangle])
            except ValueError:
                pass
        bmesh.ops.remove_doubles(
            bm,
            verts=bm.verts[:],
            dist=max(float(fuse_distance), 0.0001),
        )
        islands, face_to_island = _face_island_map(bm)
        capped = 0

        for boundary in _boundary_groups(bm):
            boundary_vertices = {vertex for edge in boundary for vertex in edge.verts}
            points = np.asarray(
                [tuple(vertex.co) for vertex in boundary_vertices],
                dtype=np.float64,
            )
            minimum = points.min(axis=0)
            maximum = points.max(axis=0)
            extents = maximum - minimum

            adjacent_faces = [
                face
                for edge in boundary
                for face in edge.link_faces
                if face in face_to_island
            ]
            if not adjacent_faces:
                continue
            island = islands[face_to_island[adjacent_faces[0]]]
            island_extents = np.maximum(
                island["maximum"] - island["minimum"],
                1.0e-8,
            )

            flat_axis = int(np.argmin(extents))
            plane_epsilon = max(tolerance * 0.25, island_extents.max() * 1.0e-5)
            is_planar = extents[flat_axis] <= plane_epsilon
            at_minimum = (
                abs(minimum[flat_axis] - island["minimum"][flat_axis])
                <= plane_epsilon
            )
            at_maximum = (
                abs(maximum[flat_axis] - island["maximum"][flat_axis])
                <= plane_epsilon
            )
            other_axes = [axis for axis in range(3) if axis != flat_axis]
            coverage = (
                extents[other_axes[0]]
                * extents[other_axes[1]]
                / (
                    island_extents[other_axes[0]]
                    * island_extents[other_axes[1]]
                )
            )
            nonzero_extents = extents[extents > plane_epsilon]
            narrow_opening = (
                len(nonzero_extents) > 0
                and float(nonzero_extents.min()) <= tolerance
            )
            small_opening = float(extents.max()) <= tolerance
            plane_normal = np.zeros(3, dtype=np.float64)
            plane_normal[flat_axis] = 1.0
            adjacent_alignments = [
                abs(
                    float(
                        np.dot(
                            np.asarray(tuple(face.normal), dtype=np.float64),
                            plane_normal,
                        )
                    )
                )
                for face in adjacent_faces
            ]
            # A sliced solid has side faces perpendicular to its missing cap.
            # This identifies internal cut ends without filling facade holes,
            # whose neighbouring faces are parallel to the opening plane.
            extruded_cut_end = (
                is_planar
                and bool(adjacent_alignments)
                and max(adjacent_alignments) <= 0.25
            )
            obvious_cut_end = (
                is_planar
                and (
                    (
                        (at_minimum or at_maximum)
                        and coverage >= 0.20
                    )
                    or extruded_cut_end
                )
            )
            if not (small_opening or narrow_opening or obvious_cut_end):
                continue

            try:
                result = bmesh.ops.holes_fill(
                    bm,
                    edges=boundary,
                    sides=0,
                )
                if any(
                    isinstance(item, bmesh.types.BMFace)
                    for item in result.get("faces", ())
                ):
                    capped += 1
            except RuntimeError:
                pass

        oriented_closed_shells = _orient_closed_shells_outward(bm)
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        bm.verts.ensure_lookup_table()
        repaired_vertices = np.asarray(
            [tuple(vertex.co) for vertex in bm.verts],
            dtype=np.float64,
        )
        repaired_faces = np.asarray(
            [[vertex.index for vertex in face.verts] for face in bm.faces],
            dtype=np.int32,
        )
        return repaired_vertices, repaired_faces, capped, oriented_closed_shells
    finally:
        bm.free()


def _component_face_groups(vertex_count, faces):
    parent = np.arange(vertex_count, dtype=np.int32)

    def find(index):
        root = index
        while parent[root] != root:
            root = int(parent[root])
        while parent[index] != index:
            next_index = int(parent[index])
            parent[index] = root
            index = next_index
        return root

    def union(left, right):
        root_left = find(int(left))
        root_right = find(int(right))
        if root_left != root_right:
            parent[root_right] = root_left

    for triangle in faces:
        union(triangle[0], triangle[1])
        union(triangle[1], triangle[2])

    groups = {}
    for face_index, triangle in enumerate(faces):
        root = find(int(triangle[0]))
        groups.setdefault(root, []).append(face_index)
    return [np.asarray(indices, dtype=np.int32) for indices in groups.values()]


def _principal_extents(points):
    if len(points) < 3:
        return np.zeros(3, dtype=np.float64)
    centered = points - points.mean(axis=0)
    covariance = centered.T @ centered
    try:
        _, axes = np.linalg.eigh(covariance)
        projected = centered @ axes
        extents = projected.max(axis=0) - projected.min(axis=0)
    except np.linalg.LinAlgError:
        extents = points.max(axis=0) - points.min(axis=0)
    return np.sort(extents)[::-1]


def _filter_components(vertices, faces, min_feature, skip_thin, thin_threshold):
    groups = _component_face_groups(len(vertices), faces)
    if len(groups) <= 1:
        return vertices, faces, []

    component_rows = []
    for face_indices in groups:
        component_faces = faces[face_indices]
        vertex_indices = np.unique(component_faces.reshape(-1))
        points = vertices[vertex_indices]
        extents = _principal_extents(points)
        component_rows.append(
            {
                "faces": face_indices,
                "triangle_count": int(len(face_indices)),
                "extents": extents,
            }
        )

    # The largest component is always preserved. This prevents zero-thickness
    # facade shells from being mistaken for optional thin detail.
    largest = max(component_rows, key=lambda row: row["triangle_count"])
    keep_indices = []
    skipped = []
    for row in component_rows:
        extents = row["extents"]
        reason = None
        if row is not largest and extents[0] < min_feature:
            reason = "smaller than Min Feature"
        elif (
            row is not largest
            and skip_thin
            and extents[-1] <= thin_threshold
        ):
            reason = "separate thin component"

        if reason is None:
            keep_indices.extend(row["faces"].tolist())
        else:
            skipped.append(
                {
                    "reason": reason,
                    "triangles": row["triangle_count"],
                    "extents": [float(value) for value in extents],
                }
            )

    selected_faces = faces[np.asarray(sorted(keep_indices), dtype=np.int32)]
    return (*_remove_degenerate_and_duplicate_faces(vertices, selected_faces), skipped)


def collect_objects(context, settings, objects, name=None):
    """Collect an explicit source set without depending on viewport selection."""
    unique = []
    seen = set()
    for ob in objects:
        if (
                ob is None
                or ob.type != "MESH"
                or naming.is_any_collider(ob)
                or ob.as_pointer() in seen):
            continue
        seen.add(ob.as_pointer())
        unique.append(ob)
    objects = unique
    if not objects:
        raise ValueError("Select at least one non-collider mesh object")

    active = context.view_layer.objects.active
    if active not in objects:
        active = objects[0]
    source_name = str(name or active.name)

    raw_vertices, raw_faces = _collect_evaluated_meshes(context, objects)
    raw_vertices, raw_faces = _remove_degenerate_and_duplicate_faces(
        raw_vertices,
        raw_faces,
    )
    if len(raw_faces) == 0:
        raise ValueError("The selected objects do not contain valid triangles")

    (
        repaired_vertices,
        repaired_faces,
        capped_boundaries,
        oriented_closed_shells,
    ) = _selective_cap_boundaries(
        raw_vertices,
        raw_faces,
        settings.tolerance,
        (
            settings.fuse_distance
            if settings.destructive_preprocess and settings.fuse_sources
            else 0.0001
        ),
    )
    proxy_vertices, proxy_faces = _remove_degenerate_and_duplicate_faces(
        repaired_vertices,
        repaired_faces,
        snap_size=0.0,
    )
    proxy_vertices, proxy_faces, skipped = _filter_components(
        proxy_vertices,
        proxy_faces,
        settings.min_feature if settings.destructive_preprocess else 0.0,
        settings.skip_thin if settings.destructive_preprocess else False,
        settings.thin_threshold,
    )
    if len(proxy_faces) == 0:
        raise ValueError("Preprocessing removed all source triangles")

    return SourceData(
        name=source_name,
        object_names=[ob.name for ob in objects],
        raw_vertices=raw_vertices,
        raw_faces=raw_faces,
        vertices=proxy_vertices,
        faces=proxy_faces,
        skipped_components=skipped,
        proxy_mode="EXACT_FUSED",
        capped_boundaries=capped_boundaries,
        oriented_closed_shells=oriented_closed_shells,
    )


def collect_source(context, settings):
    return collect_objects(
        context,
        settings,
        selected_source_objects(context),
    )
