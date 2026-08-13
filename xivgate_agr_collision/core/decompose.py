# SPDX-FileCopyrightText: 2026 XIVgate
# SPDX-License-Identifier: GPL-3.0-or-later
# Exact concave-edge BSP decomposition.
#
# Closed source components are cut along planes derived from their own
# concave edges. Exterior faces are never replaced by a sampled approximation:
# every final exterior polygon is inherited from the repaired source, while
# newly created polygons are internal cut caps. This preserves architectural
# corners, openings and steps.

from dataclasses import dataclass, field
import math
import random

import bmesh
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree


CONCAVE_EPSILON = math.radians(0.25)
PLANE_KEY_EPSILON = 1.0e-5
CONVEX_VOLUME_ABSOLUTE_EPSILON = 1.0e-7
CONVEX_VOLUME_RELATIVE_EPSILON = 1.0e-7


@dataclass(eq=False)
class Piece:
    vertices: np.ndarray
    faces: np.ndarray
    depth: int = 0
    closed: bool = False
    volume: float = 0.0
    concave_edges: int = 0
    concavity_volume: float = math.inf
    unsplittable: bool = False
    approximate_open_shell: bool = False
    allow_tolerance_hull: bool = False
    approximation_deviation: float = 0.0
    relaxed_splits: int = 0

    @property
    def convex(self):
        if not self.closed:
            return False
        if self.concave_edges == 0:
            return True

        # Plane cuts can leave a nearly coplanar diagonal with a tiny negative
        # signed angle. The convex-hull volume difference is a more stable
        # secondary test and prevents subdivision from chasing floating-point
        # noise into the Max Parts limit.
        volume_epsilon = max(
            CONVEX_VOLUME_ABSOLUTE_EPSILON,
            self.volume * CONVEX_VOLUME_RELATIVE_EPSILON,
        )
        return self.concavity_volume <= volume_epsilon


@dataclass
class DecompositionResult:
    hulls: list[tuple[np.ndarray, np.ndarray]]
    max_deviation: float
    total_triangles: int
    seed: int
    warnings: list[str] = field(default_factory=list)
    complete: bool = True
    remaining_invalid: int = 0


def _new_bmesh(vertices, faces):
    bm = bmesh.new()
    bm_vertices = [bm.verts.new(tuple(point)) for point in vertices]
    for triangle in faces:
        try:
            bm.faces.new([bm_vertices[int(index)] for index in triangle])
        except ValueError:
            pass
    if bm.faces:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        if (
            bm.edges
            and all(edge.is_manifold for edge in bm.edges)
            and float(bm.calc_volume(signed=True)) < 0.0
        ):
            bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    return bm


def _bmesh_arrays(bm, simplify=True):
    if simplify and bm.faces:
        bmesh.ops.dissolve_limit(
            bm,
            angle_limit=math.radians(0.05),
            use_dissolve_boundaries=True,
            verts=bm.verts[:],
            edges=bm.edges[:],
            delimit=set(),
        )
    if bm.faces:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        bmesh.ops.triangulate(bm, faces=bm.faces[:])

    used_vertices = [vertex for vertex in bm.verts if vertex.link_faces]
    if len(used_vertices) < 3 or not bm.faces:
        return (
            np.empty((0, 3), dtype=np.float64),
            np.empty((0, 3), dtype=np.int32),
        )
    vertex_indices = {vertex: index for index, vertex in enumerate(used_vertices)}
    vertices = np.asarray(
        [tuple(vertex.co) for vertex in used_vertices],
        dtype=np.float64,
    )
    faces = np.asarray(
        [
            [vertex_indices[vertex] for vertex in face.verts]
            for face in bm.faces
        ],
        dtype=np.int32,
    )
    return vertices, faces


def _analyse_piece(piece):
    bm = _new_bmesh(piece.vertices, piece.faces)
    try:
        if not bm.faces:
            piece.unsplittable = True
            return piece
        piece.closed = bool(bm.edges) and all(edge.is_manifold for edge in bm.edges)
        piece.volume = abs(float(bm.calc_volume(signed=True)))
        if piece.closed:
            piece.concave_edges = sum(
                edge.calc_face_angle_signed() < -CONCAVE_EPSILON
                for edge in bm.edges
                if edge.is_manifold
            )
            hull = _convex_hull(piece.vertices)
            if hull is None:
                piece.concavity_volume = math.inf
            else:
                hull_bm = _new_bmesh(*hull)
                try:
                    hull_volume = abs(float(hull_bm.calc_volume(signed=True)))
                finally:
                    hull_bm.free()
                piece.concavity_volume = max(0.0, hull_volume - piece.volume)
        else:
            piece.concave_edges = 0
            piece.concavity_volume = math.inf
        return piece
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
        left_root = find(int(left))
        right_root = find(int(right))
        if left_root != right_root:
            parent[right_root] = left_root

    for triangle in faces:
        union(triangle[0], triangle[1])
        union(triangle[1], triangle[2])

    groups = {}
    for face_index, triangle in enumerate(faces):
        groups.setdefault(find(int(triangle[0])), []).append(face_index)
    return [
        np.asarray(indices, dtype=np.int32)
        for indices in groups.values()
    ]


def _component_pieces(vertices, faces):
    pieces = []
    for face_indices in _component_face_groups(len(vertices), faces):
        component_faces = faces[face_indices]
        used = np.unique(component_faces.reshape(-1))
        remap = np.full(len(vertices), -1, dtype=np.int32)
        remap[used] = np.arange(len(used), dtype=np.int32)
        piece = Piece(
            vertices=vertices[used].copy(),
            faces=remap[component_faces],
        )
        pieces.append(_analyse_piece(piece))
    return pieces


def _canonical_plane(point, normal):
    normal = np.asarray(normal, dtype=np.float64)
    length = float(np.linalg.norm(normal))
    if length <= 1.0e-10:
        return None
    normal /= length
    offset = float(np.dot(point, normal))
    for value in normal:
        if abs(float(value)) <= 1.0e-10:
            continue
        if value < 0.0:
            normal = -normal
            offset = -offset
        break
    key = (
        *(int(round(float(value) / PLANE_KEY_EPSILON)) for value in normal),
        int(round(offset / PLANE_KEY_EPSILON)),
    )
    return key, normal, offset


def _principal_extents(points):
    """Return orientation-independent extents from largest to smallest."""
    points = np.asarray(points, dtype=np.float64)
    if len(points) < 3:
        return np.zeros(3, dtype=np.float64)
    centered = points - points.mean(axis=0)
    try:
        _, axes = np.linalg.eigh(centered.T @ centered)
        projected = centered @ axes
        extents = projected.max(axis=0) - projected.min(axis=0)
    except np.linalg.LinAlgError:
        extents = points.max(axis=0) - points.min(axis=0)
    return np.sort(extents)[::-1]


def _significant_concave_edges(bm, tolerance):
    """Ignore connected reflex features whose narrow dimension is in tolerance."""
    bm.edges.index_update()
    remaining = {
        edge
        for edge in bm.edges
        if (
            edge.is_manifold
            and edge.calc_face_angle_signed() < -CONCAVE_EPSILON
        )
    }
    significant = []
    while remaining:
        seed = min(remaining, key=lambda edge: edge.index)
        remaining.remove(seed)
        stack = [seed]
        group = [seed]
        while stack:
            edge = stack.pop()
            for vertex in edge.verts:
                for neighbour in vertex.link_edges:
                    if neighbour in remaining:
                        remaining.remove(neighbour)
                        stack.append(neighbour)
                        group.append(neighbour)

        points = np.asarray(
            [
                tuple(vertex.co)
                for edge in group
                for vertex in edge.verts
            ],
            dtype=np.float64,
        )
        extents = _principal_extents(
            np.unique(np.round(points, decimals=8), axis=0)
        )
        extent_epsilon = max(tolerance * 1.0e-4, 1.0e-7)
        nonzero = extents[extents > extent_epsilon]
        narrow_feature = (
            len(group) >= 4
            and len(nonzero) >= 2
            and float(nonzero.min()) <= tolerance
        )
        if not narrow_feature:
            significant.extend(group)
    return significant


def _candidate_planes(piece, rng, tolerance):
    bm = _new_bmesh(piece.vertices, piece.faces)
    try:
        candidates = {}
        significant_edges = _significant_concave_edges(bm, tolerance)
        if not significant_edges:
            # The dense hull check is authoritative. If a nominally narrow
            # feature still cannot be bridged inside tolerance, retain an
            # exact fallback instead of leaving the component unsplittable.
            significant_edges = [
                edge
                for edge in bm.edges
                if (
                    edge.is_manifold
                    and edge.calc_face_angle_signed() < -CONCAVE_EPSILON
                )
            ]
        if len(significant_edges) >= 3:
            # Reflex-edge midpoints describe the centre of an architectural
            # opening much better than the vertex centroid of its surrounding
            # wall, especially for asymmetrical rings and arches.
            component_center = np.mean(
                [
                    np.asarray(
                        tuple((edge.verts[0].co + edge.verts[1].co) * 0.5),
                        dtype=np.float64,
                    )
                    for edge in significant_edges
                ],
                axis=0,
            )
        else:
            component_center = np.asarray(
                piece.vertices.mean(axis=0),
                dtype=np.float64,
            )
        for edge in significant_edges:
            midpoint = np.asarray(
                tuple((edge.verts[0].co + edge.verts[1].co) * 0.5),
                dtype=np.float64,
            )
            normals = [
                np.asarray(tuple(face.normal), dtype=np.float64)
                for face in edge.link_faces
            ]
            if len(normals) == 2:
                normals.extend(
                    (
                        normals[0] + normals[1],
                        normals[0] - normals[1],
                    )
                )
            edge_direction = np.asarray(
                tuple(edge.verts[1].co - edge.verts[0].co),
                dtype=np.float64,
            )
            edge_direction /= max(
                float(np.linalg.norm(edge_direction)),
                1.0e-12,
            )
            center_direction = component_center - midpoint
            # For ring, arch and frame-like parts this creates a radial plane
            # through the opening centre and the full reflex edge. Paired
            # front/back corners then converge on one intentional cut instead
            # of producing a fan of small wedges.
            normals.append(
                np.cross(edge_direction, center_direction)
            )
            # Project world axes onto the space perpendicular to the reflex
            # edge. Every candidate plane therefore contains the complete
            # edge instead of merely passing through its midpoint.
            for axis in np.eye(3, dtype=np.float64):
                normals.append(
                    axis - edge_direction * float(np.dot(axis, edge_direction))
                )
            for normal in normals:
                canonical = _canonical_plane(midpoint, normal)
                if canonical is None:
                    continue
                key, canonical_normal, offset = canonical
                candidates[key] = (
                    canonical_normal * offset,
                    canonical_normal,
                )
        ordered = [candidates[key] for key in sorted(candidates)]
        rng.shuffle(ordered)
        return ordered
    finally:
        bm.free()


def _cut_half(piece, plane_point, plane_normal, keep_positive, gap):
    bm = _new_bmesh(piece.vertices, piece.faces)
    try:
        half_gap = gap * 0.5
        shifted_point = (
            np.asarray(plane_point, dtype=np.float64)
            + np.asarray(plane_normal, dtype=np.float64)
            * (half_gap if keep_positive else -half_gap)
        )
        result = bmesh.ops.bisect_plane(
            bm,
            geom=bm.verts[:] + bm.edges[:] + bm.faces[:],
            dist=1.0e-7,
            plane_co=Vector(tuple(shifted_point)),
            plane_no=Vector(tuple(plane_normal)),
            use_snap_center=False,
            clear_inner=keep_positive,
            clear_outer=not keep_positive,
        )
        # The operator's geom_cut list can omit pre-existing edges that already
        # lie on the cutting plane. Because every input piece is closed, every
        # boundary edge after clearing a half belongs to a cut contour.
        cut_edges = [
            edge
            for edge in bm.edges
            if edge.is_valid and edge.is_boundary
        ]
        if cut_edges:
            try:
                bmesh.ops.holes_fill(
                    bm,
                    edges=cut_edges,
                    sides=0,
                )
            except RuntimeError:
                pass

            remaining_boundary = [
                edge
                for edge in bm.edges
                if edge.is_valid and edge.is_boundary
            ]
            if remaining_boundary:
                try:
                    prepared = bmesh.ops.edgenet_prepare(
                        bm,
                        edges=remaining_boundary,
                    )
                    prepared_edges = list(prepared.get("edges", ()))
                    bmesh.ops.edgenet_fill(
                        bm,
                        edges=list(
                            set(remaining_boundary + prepared_edges)
                        ),
                        sides=0,
                    )
                except RuntimeError:
                    pass

        vertices, faces = _bmesh_arrays(bm, simplify=True)
        child = Piece(
            vertices=vertices,
            faces=faces,
            depth=piece.depth + 1,
            allow_tolerance_hull=piece.allow_tolerance_hull,
            approximation_deviation=piece.approximation_deviation,
            relaxed_splits=piece.relaxed_splits,
        )
        return _analyse_piece(child)
    finally:
        bm.free()


def _split_score(children, parent):
    unresolved_concavity_volumes = [
        child.concavity_volume
        for child in children
        if not child.convex
    ]
    total_concavity_volume = sum(unresolved_concavity_volumes)
    total_concavity = sum(child.concave_edges for child in children)
    maximum_concavity = max(
        (child.concave_edges for child in children),
        default=0,
    )
    smaller_fraction = min(
        (child.volume for child in children),
        default=0.0,
    ) / max(parent.volume, 1.0e-12)
    # On ordinary architectural solids, removing reflex edges directly keeps
    # doors, steps and facade corners stable. Closed sweep components are
    # separated into logical sectors before reaching this generic fallback.
    return (
        total_concavity,
        maximum_concavity,
        total_concavity_volume / max(parent.volume, 1.0e-12),
        len(children),
        -smaller_fraction,
        sum(len(child.faces) for child in children),
    )


def _split_volume_allowance(piece, gap):
    """Return the maximum numerical and intentional gap volume per split."""
    extents = np.maximum(
        piece.vertices.max(axis=0) - piece.vertices.min(axis=0),
        1.0e-8,
    )
    maximum_section_area = max(
        extents[0] * extents[1],
        extents[1] * extents[2],
        extents[0] * extents[2],
    )
    return max(
        CONVEX_VOLUME_ABSOLUTE_EPSILON,
        gap * maximum_section_area * 3.0,
        piece.volume * 1.0e-6,
    )


def _normalize_gap_scale_concavity(piece, gap):
    """Replace only gap-scale reflex noise with its numerically stable hull."""
    if (
        piece.convex
        or not piece.closed
        or piece.concavity_volume > _split_volume_allowance(piece, gap)
    ):
        return piece
    hull = _as_convex_hull_piece(piece)
    return hull if hull is not None else piece


def _cut_half_components(
    piece,
    plane_point,
    plane_normal,
    keep_positive,
    gap,
):
    combined = _cut_half(
        piece,
        plane_point,
        plane_normal,
        keep_positive,
        gap,
    )
    if not combined.closed or len(combined.faces) == 0:
        return []
    components = _component_pieces(combined.vertices, combined.faces)
    for child in components:
        child.depth = combined.depth
        child.relaxed_splits = piece.relaxed_splits
    return components


def _merge_split_children(children, gap):
    """Collapse locally redundant split children before candidate scoring."""
    children = list(children)
    while True:
        best = None
        bounds = [
            (child.vertices.min(axis=0), child.vertices.max(axis=0))
            for child in children
        ]
        for left_index in range(len(children)):
            for right_index in range(left_index + 1, len(children)):
                left = children[left_index]
                right = children[right_index]
                left_minimum, left_maximum = bounds[left_index]
                right_minimum, right_maximum = bounds[right_index]
                proximity = gap * 4.0 + 1.0e-7
                if np.any(
                    (left_maximum + proximity < right_minimum)
                    | (right_maximum + proximity < left_minimum)
                ):
                    continue
                hull = _convex_hull(
                    np.vstack((left.vertices, right.vertices))
                )
                if hull is None:
                    continue
                merged = _analyse_piece(
                    Piece(
                        vertices=hull[0],
                        faces=hull[1],
                        depth=max(left.depth, right.depth),
                    )
                )
                points = np.vstack((left.vertices, right.vertices))
                extents = np.maximum(
                    points.max(axis=0) - points.min(axis=0),
                    1.0e-8,
                )
                maximum_face_area = max(
                    extents[0] * extents[1],
                    extents[1] * extents[2],
                    extents[0] * extents[2],
                )
                allowance = max(
                    1.0e-7,
                    gap * maximum_face_area * 2.5,
                    (left.volume + right.volume) * 1.0e-6,
                )
                added_volume = max(
                    0.0,
                    merged.volume - left.volume - right.volume,
                )
                if added_volume > allowance:
                    continue
                candidate = (
                    added_volume / allowance,
                    left_index,
                    right_index,
                    merged,
                )
                if best is None or candidate[0] < best[0]:
                    best = candidate
        if best is None:
            return children
        _, left_index, right_index, merged = best
        children = [
            child
            for index, child in enumerate(children)
            if index not in {left_index, right_index}
        ]
        children.append(merged)


def _best_exact_split(piece, gap, tolerance, rng):
    candidates = []
    minimum_volume = max(piece.volume * 1.0e-5, 1.0e-9)
    for plane_point, plane_normal in _candidate_planes(
        piece,
        rng,
        tolerance,
    ):
        negative = _cut_half_components(
            piece,
            plane_point,
            plane_normal,
            keep_positive=False,
            gap=gap,
        )
        positive = _cut_half_components(
            piece,
            plane_point,
            plane_normal,
            keep_positive=True,
            gap=gap,
        )
        children = [
            _normalize_gap_scale_concavity(child, gap)
            for child in negative + positive
        ]
        children = _merge_split_children(children, gap)
        if len(children) < 2:
            continue
        if any(
            not child.closed or child.volume <= minimum_volume
            for child in children
        ):
            continue
        # Reject any candidate that silently loses an open half after the
        # bisect/cap operation. This is essential for large connected models:
        # a valid-looking set of convex remnants must never replace only part
        # of the parent volume.
        child_volume = sum(child.volume for child in children)
        if abs(child_volume - piece.volume) > _split_volume_allowance(
            piece,
            gap,
        ):
            continue
        child_concavity = sum(
            child.concave_edges
            for child in children
            if not child.convex
        )
        child_concavity_volume = sum(
            child.concavity_volume
            for child in children
            if not child.convex
        )
        concavity_allowance = _split_volume_allowance(piece, gap)
        if child_concavity_volume > (
            piece.concavity_volume + concavity_allowance
        ):
            continue
        if (
            child_concavity_volume
            >= piece.concavity_volume - concavity_allowance
            and child_concavity > piece.concave_edges
        ):
            continue
        score = _split_score(children, piece)
        candidates.append((score, children))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return tuple(candidates[0][1])


def _convex_hull(vertices):
    unique = np.unique(np.round(vertices, decimals=8), axis=0)
    if len(unique) < 4:
        return None
    bm = bmesh.new()
    try:
        bm_vertices = [bm.verts.new(tuple(point)) for point in unique]
        bmesh.ops.convex_hull(
            bm,
            input=bm_vertices,
            use_existing_faces=False,
        )
        hull_vertices, hull_faces = _bmesh_arrays(bm, simplify=True)
        if len(hull_faces) < 4:
            return None
        return hull_vertices, hull_faces
    finally:
        bm.free()


def _as_convex_hull_piece(piece):
    hull = _convex_hull(piece.vertices)
    if hull is None:
        return None
    vertices, faces = hull
    return _analyse_piece(
        Piece(
            vertices=vertices,
            faces=faces,
            depth=piece.depth,
            approximate_open_shell=piece.approximate_open_shell,
            allow_tolerance_hull=piece.allow_tolerance_hull,
            approximation_deviation=piece.approximation_deviation,
        )
    )


def _surface_deviation(
    hull_vertices,
    hull_faces,
    source_vertices,
    source_faces,
    sample_spacing=None,
    stop_at=None,
):
    source_bvh = BVHTree.FromPolygons(
        [Vector(tuple(point)) for point in source_vertices],
        [tuple(int(index) for index in triangle) for triangle in source_faces],
        all_triangles=True,
        epsilon=0.0,
    )
    worst = 0.0
    for triangle in hull_faces:
        a, b, c = hull_vertices[triangle]
        if sample_spacing is None:
            samples = (a, b, c, (a + b + c) / 3.0)
        else:
            maximum_edge = max(
                float(np.linalg.norm(a - b)),
                float(np.linalg.norm(b - c)),
                float(np.linalg.norm(c - a)),
            )
            divisions = min(
                64,
                max(2, int(math.ceil(maximum_edge / sample_spacing))),
            )
            samples = (
                a + (b - a) * (left / divisions)
                + (c - a) * (right / divisions)
                for left in range(divisions + 1)
                for right in range(divisions + 1 - left)
            )
        for point in samples:
            hit = source_bvh.find_nearest(Vector(tuple(point)))
            if hit[0] is not None:
                worst = max(worst, float(hit[3]))
                if stop_at is not None and worst > stop_at:
                    return worst
    return worst


def _approximate_convex_piece(piece, tolerance):
    """Connect existing points when one hull stays inside the user tolerance."""
    if piece.convex or not piece.closed:
        return piece, 0.0
    bm = _new_bmesh(piece.vertices, piece.faces)
    try:
        significant_edges = _significant_concave_edges(bm, tolerance)
    finally:
        bm.free()
    if significant_edges and not piece.allow_tolerance_hull:
        # Exterior steps and broad corners must remain exact even when the
        # diagonal of one small step happens to fit inside the tolerance.
        # Hull approximation is reserved for enclosed narrow recesses.
        return None, math.inf
    hull = _convex_hull(piece.vertices)
    if hull is None:
        return None, math.inf
    hull_vertices, hull_faces = hull

    # The cheap pass rejects clearly concave parts before dense sampling.
    deviation = _surface_deviation(
        hull_vertices,
        hull_faces,
        piece.vertices,
        piece.faces,
        stop_at=tolerance,
    )
    if deviation > tolerance:
        return None, deviation

    # A tolerance-spaced lattice catches large doors and windows while narrow
    # slots below tolerance are intentionally bridged by the hull.
    deviation = _surface_deviation(
        hull_vertices,
        hull_faces,
        piece.vertices,
        piece.faces,
        sample_spacing=max(tolerance * 0.5, 0.005),
        stop_at=tolerance,
    )
    if deviation > tolerance:
        return None, deviation

    approximated = _analyse_piece(
        Piece(
            vertices=hull_vertices,
            faces=hull_faces,
            depth=piece.depth,
            approximate_open_shell=piece.approximate_open_shell,
            allow_tolerance_hull=piece.allow_tolerance_hull,
            approximation_deviation=max(
                piece.approximation_deviation,
                deviation,
            ),
        )
    )
    return approximated, deviation


def _close_small_open_piece(piece, tolerance):
    hull = _convex_hull(piece.vertices)
    if hull is None:
        return None, math.inf
    hull_vertices, hull_faces = hull
    deviation = _surface_deviation(
        hull_vertices,
        hull_faces,
        piece.vertices,
        piece.faces,
    )
    if deviation > tolerance:
        return None, deviation
    closed = Piece(
        vertices=hull_vertices,
        faces=hull_faces,
        depth=piece.depth,
        approximate_open_shell=True,
    )
    return _analyse_piece(closed), deviation


def _ordered_cycle(edges):
    adjacency = {}
    for edge in edges:
        left, right = (vertex.index for vertex in edge.verts)
        adjacency.setdefault(left, []).append(right)
        adjacency.setdefault(right, []).append(left)
    if len(adjacency) < 3 or any(
        len(neighbours) != 2
        for neighbours in adjacency.values()
    ):
        return None

    start = min(adjacency)
    order = [start]
    previous = None
    current = start
    while len(order) < len(adjacency):
        candidates = [
            neighbour
            for neighbour in adjacency[current]
            if neighbour != previous and neighbour not in order
        ]
        if not candidates:
            return None
        previous, current = current, min(candidates)
        order.append(current)
    if start not in adjacency[current]:
        return None
    return order


def _repeated_cross_section_cycles(bm, tolerance):
    """Detect a closed sweep made from repeated equal cross-section loops."""
    bm.verts.index_update()
    bm.edges.index_update()
    if len(bm.verts) < 12 or len(bm.verts) - len(bm.edges) + len(bm.faces) != 0:
        return None

    lengths = sorted(
        ((float(edge.calc_length()), edge) for edge in bm.edges),
        key=lambda item: item[0],
    )
    median_length = lengths[len(lengths) // 2][0]
    length_epsilon = max(
        tolerance * 0.02,
        median_length * 1.0e-4,
        1.0e-6,
    )
    clusters = []
    for length, edge in lengths:
        if (
            not clusters
            or abs(length - clusters[-1]["mean"]) > length_epsilon
        ):
            clusters.append(
                {
                    "mean": length,
                    "edges": [edge],
                }
            )
        else:
            cluster = clusters[-1]
            cluster["edges"].append(edge)
            cluster["mean"] = sum(
                float(item.calc_length())
                for item in cluster["edges"]
            ) / len(cluster["edges"])

    for cluster in sorted(
        clusters,
        key=lambda item: len(item["edges"]),
        reverse=True,
    ):
        if len(cluster["edges"]) < 9:
            continue
        remaining = set(cluster["edges"])
        cycles = []
        while remaining:
            seed = min(remaining, key=lambda edge: edge.index)
            remaining.remove(seed)
            stack = [seed]
            component = [seed]
            while stack:
                edge = stack.pop()
                for vertex in edge.verts:
                    for neighbour in vertex.link_edges:
                        if neighbour in remaining:
                            remaining.remove(neighbour)
                            stack.append(neighbour)
                            component.append(neighbour)
            cycle = _ordered_cycle(component)
            if cycle is None:
                cycles = []
                break
            cycles.append(cycle)

        if len(cycles) < 3:
            continue
        cycle_size = len(cycles[0])
        if cycle_size < 3 or any(
            len(cycle) != cycle_size
            for cycle in cycles
        ):
            continue
        covered_vertices = {
            vertex_index
            for cycle in cycles
            for vertex_index in cycle
        }
        if (
            len(covered_vertices) != len(bm.verts)
            or sum(len(cycle) for cycle in cycles) != len(bm.verts)
        ):
            continue

        cycle_sets = [set(cycle) for cycle in cycles]
        neighbours = {index: set() for index in range(len(cycles))}
        pairs = []
        for left_index in range(len(cycles)):
            for right_index in range(left_index + 1, len(cycles)):
                left = cycle_sets[left_index]
                right = cycle_sets[right_index]
                connecting_edges = sum(
                    (
                        edge.verts[0].index in left
                        and edge.verts[1].index in right
                    )
                    or (
                        edge.verts[1].index in left
                        and edge.verts[0].index in right
                    )
                    for edge in bm.edges
                )
                if connecting_edges >= cycle_size:
                    neighbours[left_index].add(right_index)
                    neighbours[right_index].add(left_index)
                    pairs.append((left_index, right_index))
        if (
            len(pairs) == len(cycles)
            and all(len(items) == 2 for items in neighbours.values())
        ):
            return cycles, pairs
    return None


def _ring_sector_piece(piece, left_cycle, right_cycle):
    left = set(left_cycle)
    right = set(right_cycle)
    combined = left | right
    side_faces = [
        triangle
        for triangle in piece.faces
        if (
            set(int(index) for index in triangle).issubset(combined)
            and any(int(index) in left for index in triangle)
            and any(int(index) in right for index in triangle)
        )
    ]
    if len(side_faces) < len(left_cycle) * 2:
        return None

    used = sorted(combined)
    remap = {old: new for new, old in enumerate(used)}
    remapped_faces = np.asarray(
        [
            [remap[int(index)] for index in triangle]
            for triangle in side_faces
        ],
        dtype=np.int32,
    )
    bm = _new_bmesh(piece.vertices[used], remapped_faces)
    try:
        boundary_edges = [edge for edge in bm.edges if edge.is_boundary]
        if not boundary_edges:
            return None
        try:
            bmesh.ops.holes_fill(
                bm,
                edges=boundary_edges,
                sides=0,
            )
        except RuntimeError:
            return None
        vertices, faces = _bmesh_arrays(bm)
    finally:
        bm.free()
    sector = _analyse_piece(
        Piece(
            vertices=vertices,
            faces=faces,
            depth=piece.depth,
            allow_tolerance_hull=True,
        )
    )
    return sector if sector.closed else None


def _ring_hull_external_deviation(
    hull,
    source_piece,
    left_points,
    right_points,
    tolerance,
    stop_at=None,
):
    hull_vertices, hull_faces = hull
    source_bvh = _piece_bvh(source_piece)
    worst = 0.0
    for triangle in hull_faces:
        a, b, c = hull_vertices[triangle]
        ring_labels = []
        for point in (a, b, c):
            distances = (
                float(np.min(np.linalg.norm(left_points - point, axis=1))),
                float(np.min(np.linalg.norm(right_points - point, axis=1))),
            )
            ring_labels.append(int(np.argmin(distances)))
        # End caps are intentional internal partition faces.
        if len(set(ring_labels)) == 1:
            continue

        maximum_edge = max(
            float(np.linalg.norm(a - b)),
            float(np.linalg.norm(b - c)),
            float(np.linalg.norm(c - a)),
        )
        divisions = min(
            64,
            max(2, int(math.ceil(maximum_edge / max(tolerance * 0.5, 0.005)))),
        )
        for left in range(divisions + 1):
            for right in range(divisions + 1 - left):
                point = (
                    a
                    + (b - a) * (left / divisions)
                    + (c - a) * (right / divisions)
                )
                hit = source_bvh.find_nearest(Vector(tuple(point)))
                if hit[0] is not None:
                    worst = max(worst, float(hit[3]))
                    if stop_at is not None and worst > stop_at:
                        return worst
    return worst


def _ring_sweep_decomposition(piece, tolerance, gap):
    """Return logical tube sectors for a repeated closed sweep, if detected."""
    if not piece.closed or piece.convex:
        return None
    bm = _new_bmesh(piece.vertices, piece.faces)
    try:
        detected = _repeated_cross_section_cycles(bm, tolerance)
    finally:
        bm.free()
    if detected is None:
        return None
    cycles, pairs = detected

    sectors = []
    for left_index, right_index in pairs:
        left_cycle = cycles[left_index]
        right_cycle = cycles[right_index]
        sector = _ring_sector_piece(
            piece,
            left_cycle,
            right_cycle,
        )
        if sector is None:
            return None
        hull = _convex_hull(sector.vertices)
        if hull is None:
            return None
        deviation = _ring_hull_external_deviation(
            hull,
            piece,
            piece.vertices[left_cycle],
            piece.vertices[right_cycle],
            tolerance,
            stop_at=tolerance * 1.10,
        )
        if deviation > tolerance:
            # A twisted quad can make the point hull bow outside the source.
            # Search for the smallest conservative inset instead of creating
            # four tiny BSP wedges. Under-coverage is capped by the same user
            # tolerance and is verified later by source coverage sampling.
            center = hull[0].mean(axis=0)
            original_hull = hull
            for inset_distance in np.linspace(
                max(gap * 2.0, tolerance * 0.05),
                tolerance,
                12,
            ):
                directions = center - original_hull[0]
                lengths = np.linalg.norm(directions, axis=1)
                inset_vertices = original_hull[0] + directions * (
                    inset_distance / np.maximum(lengths, 1.0e-12)
                )[:, None]
                inset_hull = _convex_hull(inset_vertices)
                if inset_hull is None:
                    continue
                inset_deviation = _ring_hull_external_deviation(
                    inset_hull,
                    piece,
                    piece.vertices[left_cycle],
                    piece.vertices[right_cycle],
                    tolerance,
                    stop_at=tolerance,
                )
                if inset_deviation <= tolerance:
                    hull = inset_hull
                    deviation = max(inset_deviation, inset_distance)
                    break
        if deviation <= tolerance:
            sector = _analyse_piece(
                Piece(
                    vertices=hull[0],
                    faces=hull[1],
                    depth=piece.depth,
                    allow_tolerance_hull=True,
                    approximation_deviation=deviation,
                )
            )
        sectors.append(sector)

    if abs(sum(sector.volume for sector in sectors) - piece.volume) > max(
        _split_volume_allowance(piece, gap),
        piece.volume * 1.0e-5,
    ):
        # Hull sectors may contain a little extra volume; validate the exact
        # sector partition instead of rejecting a sound topology detection.
        exact_volume = 0.0
        for left_index, right_index in pairs:
            exact_sector = _ring_sector_piece(
                piece,
                cycles[left_index],
                cycles[right_index],
            )
            if exact_sector is None:
                return None
            exact_volume += exact_sector.volume
        if abs(exact_volume - piece.volume) > _split_volume_allowance(piece, gap):
            return None
    return sectors


def _convex_planes(piece):
    """Return unique outward face planes of a closed convex piece."""
    bm = _new_bmesh(piece.vertices, piece.faces)
    try:
        planes = {}
        for face in bm.faces:
            point = np.asarray(tuple(face.verts[0].co), dtype=np.float64)
            normal = np.asarray(tuple(face.normal), dtype=np.float64)
            canonical = _canonical_plane(point, normal)
            if canonical is None:
                continue
            key, canonical_normal, offset = canonical
            # Canonicalization is only used for deduplication. Preserve the
            # original outward direction for half-space classification.
            planes[key] = (point, normal / max(np.linalg.norm(normal), 1.0e-12))
        return list(planes.values())
    finally:
        bm.free()


def _piece_bvh(piece):
    return BVHTree.FromPolygons(
        [Vector(tuple(point)) for point in piece.vertices],
        [tuple(int(index) for index in triangle) for triangle in piece.faces],
        all_triangles=True,
        epsilon=1.0e-8,
    )


def _point_inside_convex(point, planes, epsilon=1.0e-7):
    point = np.asarray(point, dtype=np.float64)
    return all(
        float(np.dot(point - plane_point, plane_normal)) <= epsilon
        for plane_point, plane_normal in planes
    )


def _convex_test_samples(piece):
    centroids = piece.vertices[piece.faces].mean(axis=1)
    edge_indices = set()
    for triangle in piece.faces:
        for left, right in (
            (triangle[0], triangle[1]),
            (triangle[1], triangle[2]),
            (triangle[2], triangle[0]),
        ):
            edge_indices.add(tuple(sorted((int(left), int(right)))))
    edge_samples = np.asarray(
        [
            piece.vertices[left] * (1.0 - fraction)
            + piece.vertices[right] * fraction
            for left, right in sorted(edge_indices)
            for fraction in (0.25, 0.5, 0.75)
        ],
        dtype=np.float64,
    )
    return np.vstack((piece.vertices, edge_samples, centroids))


def _inset_convex_piece(piece, distance):
    """Move every support plane inward by one fixed world-space distance.

    Uniform scaling is not a geometric inset.  On a long, thin hull its scale
    factor is dictated by the thinnest support distance, so a 0.1 mm clearance
    can shorten a 55 m prism by several centimetres.  Reconstructing the
    polyhedron from the shifted half-spaces keeps the clearance independent of
    aspect ratio and preserves the ends of tall architectural collision parts.
    """
    if distance <= 0.0:
        return piece
    planes = _convex_planes(piece)
    if len(planes) < 4:
        return piece

    normals = np.asarray(
        [normal for _point, normal in planes],
        dtype=np.float64,
    )
    offsets = np.asarray(
        [float(np.dot(point, normal)) - distance for point, normal in planes],
        dtype=np.float64,
    )
    epsilon = max(distance * 1.0e-5, 1.0e-9)
    intersections = []
    for left in range(len(planes) - 2):
        for middle in range(left + 1, len(planes) - 1):
            for right in range(middle + 1, len(planes)):
                matrix = normals[[left, middle, right]]
                determinant = float(np.linalg.det(matrix))
                if abs(determinant) <= 1.0e-10:
                    continue
                point = np.linalg.solve(
                    matrix,
                    offsets[[left, middle, right]],
                )
                if np.all(normals @ point <= offsets + epsilon):
                    intersections.append(point)

    if len(intersections) < 4:
        return piece
    hull = _convex_hull(np.asarray(intersections, dtype=np.float64))
    if hull is None:
        return piece
    return _analyse_piece(
        Piece(
            vertices=hull[0],
            faces=hull[1],
            depth=piece.depth,
            approximate_open_shell=piece.approximate_open_shell,
            allow_tolerance_hull=piece.allow_tolerance_hull,
            approximation_deviation=max(
                piece.approximation_deviation,
                distance,
            ),
        )
    )


def _pieces_overlap(subject, cutter, clearance=1.0e-6):
    """Return true only for positive-volume overlap, not shared boundaries."""
    cutter_planes = _convex_planes(cutter)
    subject_planes = _convex_planes(subject)
    return (
        any(
            _point_inside_convex(
                point,
                cutter_planes,
                epsilon=-clearance,
            )
            for point in _convex_test_samples(subject)
        )
        or any(
            _point_inside_convex(
                point,
                subject_planes,
                epsilon=-clearance,
            )
            for point in _convex_test_samples(cutter)
        )
    )


def _subtract_convex_fragments(subject, cutter, settings):
    """Keep every significant convex fragment of subject outside cutter."""
    if not _pieces_overlap(subject, cutter):
        return [subject]

    pending_inside = [subject]
    outside_fragments = []
    epsilon = 1.0e-7
    for plane_point, plane_normal in _convex_planes(cutter):
        next_inside = []
        for fragment in pending_inside:
            distances = (
                fragment.vertices - np.asarray(plane_point, dtype=np.float64)
            ) @ np.asarray(plane_normal, dtype=np.float64)
            if float(distances.min()) >= settings.gap - epsilon:
                outside_fragments.append(fragment)
                continue
            if float(distances.max()) <= epsilon:
                next_inside.append(fragment)
                continue

            outside = _cut_half(
                fragment,
                plane_point,
                plane_normal,
                keep_positive=True,
                gap=settings.gap * 2.0,
            )
            inside = _cut_half(
                fragment,
                plane_point,
                plane_normal,
                keep_positive=False,
                gap=0.0,
            )
            for candidate, target in (
                (outside, outside_fragments),
                (inside, next_inside),
            ):
                if not candidate.closed or candidate.volume <= 1.0e-9:
                    continue
                hull = _as_convex_hull_piece(candidate)
                if hull is None:
                    continue
                target.append(hull)

        pending_inside = next_inside
        if not pending_inside:
            break

    # Anything still inside every cutter plane is the overlap and is removed.
    return outside_fragments


def _piece_extents(piece):
    points = piece.vertices
    if len(points) < 2:
        return np.zeros(3, dtype=np.float64)
    centered = points - points.mean(axis=0)
    try:
        _, axes = np.linalg.eigh(centered.T @ centered)
        projected = centered @ axes
        return np.sort(projected.max(axis=0) - projected.min(axis=0))[::-1]
    except np.linalg.LinAlgError:
        return np.sort(points.max(axis=0) - points.min(axis=0))[::-1]


def _is_ignorable_fragment(piece, settings):
    extents = _piece_extents(piece)
    # A support-plane inset may collapse an extremely thin leftover into a
    # plane.  Such a fragment is not a convex volume and would export as an
    # invalid UCX object with duplicate vertices.
    collapse_epsilon = max(
        1.0e-6,
        float(settings.gap) * 0.5 + 1.0e-7,
    )
    if float(extents[-1]) <= collapse_epsilon:
        return True
    if float(extents[0]) <= settings.min_feature:
        return True
    return bool(
        settings.skip_thin
        and float(extents[-1]) <= settings.thin_threshold
    )


def _piece_sort_key(piece):
    center = piece.vertices.mean(axis=0)
    return (
        -piece.volume,
        float(center[0]),
        float(center[1]),
        float(center[2]),
    )


def _merge_gap_allowance(left, right, settings):
    points = np.vstack((left.vertices, right.vertices))
    extents = np.maximum(points.max(axis=0) - points.min(axis=0), 1.0e-8)
    face_areas = (
        extents[0] * extents[1],
        extents[1] * extents[2],
        extents[0] * extents[2],
    )
    return max(
        1.0e-7,
        settings.gap * max(face_areas) * 2.5,
        (left.volume + right.volume) * 1.0e-6,
    )


def _merge_convex_neighbours(pieces, settings):
    """Greedily remove redundant cuts whose union is still convex."""
    pieces = list(pieces)
    while True:
        candidates = []
        bounds = [
            (piece.vertices.min(axis=0), piece.vertices.max(axis=0))
            for piece in pieces
        ]
        for left_index in range(len(pieces)):
            for right_index in range(left_index + 1, len(pieces)):
                left = pieces[left_index]
                right = pieces[right_index]
                left_minimum, left_maximum = bounds[left_index]
                right_minimum, right_maximum = bounds[right_index]
                proximity = settings.gap * 4.0 + 1.0e-7
                if np.any(
                    (left_maximum + proximity < right_minimum)
                    | (right_maximum + proximity < left_minimum)
                ):
                    continue
                merged = _convex_hull(
                    np.vstack((left.vertices, right.vertices))
                )
                if merged is None:
                    continue
                merged_piece = _analyse_piece(
                    Piece(vertices=merged[0], faces=merged[1])
                )
                added_volume = max(
                    0.0,
                    merged_piece.volume - left.volume - right.volume,
                )
                allowance = _merge_gap_allowance(left, right, settings)
                if added_volume > allowance:
                    continue

                intersects_other = any(
                    index not in {left_index, right_index}
                    and _pieces_overlap(merged_piece, other)
                    for index, other in enumerate(pieces)
                )
                if intersects_other:
                    continue
                candidates.append(
                    (
                        added_volume / allowance,
                        left_index,
                        right_index,
                        merged_piece,
                    )
                )

        if not candidates:
            return pieces
        _, left_index, right_index, merged_piece = min(
            candidates,
            key=lambda item: item[0],
        )
        pieces = [
            piece
            for index, piece in enumerate(pieces)
            if index not in {left_index, right_index}
        ]
        pieces.append(merged_piece)


def _resolve_component_overlaps(pieces, settings):
    """Build a non-overlapping convex union while keeping large pieces whole."""
    accepted = []
    normalized = []
    for piece in pieces:
        hull = _as_convex_hull_piece(piece)
        if hull is not None:
            normalized.append(hull)

    for piece in sorted(normalized, key=_piece_sort_key):
        fragments = [piece]
        for cutter in accepted:
            next_fragments = []
            for fragment in fragments:
                next_fragments.extend(
                    _subtract_convex_fragments(
                        fragment,
                        cutter,
                        settings,
                    )
                )
            fragments = next_fragments
            if not fragments:
                break
        accepted.extend(fragments)
        if len(accepted) > settings.max_parts:
            return accepted, False
    return accepted, True


def _source_coverage_deviation(
    source_vertices,
    source_faces,
    convex_pieces,
    tolerance=0.0,
    sample_limit=25000,
):
    """Measure source-surface samples against convex output half-spaces."""
    if not convex_pieces:
        return math.inf, 0

    centroids = source_vertices[source_faces].mean(axis=1)
    samples = np.vstack((source_vertices, centroids))
    if len(samples) > sample_limit:
        indices = np.linspace(
            0,
            len(samples) - 1,
            sample_limit,
            dtype=np.int32,
        )
        samples = samples[indices]

    piece_planes = []
    for piece in convex_pieces:
        planes = _convex_planes(piece)
        if not planes:
            continue
        plane_points = np.asarray(
            [plane_point for plane_point, _ in planes],
            dtype=np.float64,
        )
        plane_normals = np.asarray(
            [plane_normal for _, plane_normal in planes],
            dtype=np.float64,
        )
        piece_planes.append((plane_points, plane_normals))

    worst = 0.0
    uncovered = 0
    for point in samples:
        nearest_violation = math.inf
        for plane_points, plane_normals in piece_planes:
            violation = float(
                np.max(
                    np.einsum(
                        "ij,ij->i",
                        point - plane_points,
                        plane_normals,
                    )
                )
            )
            nearest_violation = min(nearest_violation, violation)
            if nearest_violation <= 0.0:
                break
        if nearest_violation > tolerance:
            uncovered += 1
        if nearest_violation > 0.0:
            worst = max(worst, nearest_violation)
    return worst, uncovered


def _run_attempt(source, settings, seed):
    rng = random.Random(seed)
    leaves = _component_pieces(
        source.vertices,
        source.faces,
    )
    topology_aware_leaves = []
    for piece in leaves:
        sweep = _ring_sweep_decomposition(
            piece,
            settings.tolerance,
            settings.gap,
        )
        if sweep is None:
            topology_aware_leaves.append(piece)
        else:
            topology_aware_leaves.extend(sweep)
    leaves = topology_aware_leaves
    leaves = [
        _normalize_gap_scale_concavity(piece, settings.gap)
        for piece in leaves
    ]
    warnings = []
    maximum_open_deviation = 0.0
    maximum_approximation_deviation = max(
        (
            piece.approximation_deviation
            for piece in leaves
        ),
        default=0.0,
    )

    for index, piece in enumerate(list(leaves)):
        if piece.closed:
            continue
        closed, deviation = _close_small_open_piece(piece, settings.tolerance)
        maximum_open_deviation = max(maximum_open_deviation, deviation)
        if closed is None:
            piece.unsplittable = True
            warnings.append(
                "Open component {} cannot be closed within the {:.3f} m tolerance".format(
                    index + 1,
                    settings.tolerance,
                )
            )
        else:
            leaves[index] = closed
            warnings.append(
                "Open component {} was conservatively closed ({:.3f} m deviation)".format(
                    index + 1,
                    deviation,
                )
            )

    while len(leaves) < settings.max_parts:
        pending = [
            piece
            for piece in leaves
            if piece.closed
            and not piece.convex
            and not piece.unsplittable
            and piece.depth < settings.max_depth
        ]
        if not pending:
            break
        piece = max(
            pending,
            key=lambda item: (item.concave_edges, item.volume),
        )
        approximated, deviation = _approximate_convex_piece(
            piece,
            settings.tolerance,
        )
        if approximated is not None:
            leaves.remove(piece)
            leaves.append(approximated)
            maximum_approximation_deviation = max(
                maximum_approximation_deviation,
                deviation,
            )
            continue

        split = _best_exact_split(
            piece,
            settings.gap,
            settings.tolerance,
            rng,
        )
        if split is None:
            piece.unsplittable = True
            continue
        leaves.remove(piece)
        leaves.extend(split)

    invalid = [piece for piece in leaves if not piece.convex]
    complete = not invalid
    if invalid:
        warnings.append(
            "{} component(s) remain open or concave; increase Max Parts/Search Depth "
            "or repair the source".format(len(invalid))
        )

    convex_pieces = [piece for piece in leaves if piece.convex]
    if complete:
        convex_pieces, overlap_complete = _resolve_component_overlaps(
            convex_pieces,
            settings,
        )
        if not overlap_complete:
            complete = False
            warnings.append(
                "Overlap trimming exceeded Max Parts; raise the limit or simplify the source"
            )
        else:
            convex_pieces = _merge_convex_neighbours(
                convex_pieces,
                settings,
            )
            # A successful merge can expose a positive-volume intersection
            # that did not exist between either original child and a third
            # piece. Run one final subtraction pass and do not merge again.
            convex_pieces, final_overlap_complete = (
                _resolve_component_overlaps(
                    convex_pieces,
                    settings,
                )
            )
            if not final_overlap_complete:
                complete = False
                warnings.append(
                    "Final overlap trimming exceeded Max Parts"
                )
            convex_pieces = [
                _inset_convex_piece(
                    piece,
                    settings.gap * 0.5 + 1.0e-7,
                )
                for piece in convex_pieces
            ]
            convex_pieces = [
                piece for piece in convex_pieces
                if not _is_ignorable_fragment(piece, settings)
            ]
            # Blender stores generated mesh coordinates as float32. Resolve
            # once more after the final inset so conversion-level contacts
            # cannot turn into a small positive-volume penetration.
            convex_pieces, inset_overlap_complete = (
                _resolve_component_overlaps(
                    convex_pieces,
                    settings,
                )
            )
            convex_pieces = [
                piece for piece in convex_pieces
                if not _is_ignorable_fragment(piece, settings)
            ]
            if not inset_overlap_complete:
                complete = False
                warnings.append(
                    "Post-gap overlap trimming exceeded Max Parts"
                )
            coverage_deviation, uncovered_samples = (
                _source_coverage_deviation(
                    source.vertices,
                    source.faces,
                    convex_pieces,
                    tolerance=settings.tolerance,
                )
            )
            if coverage_deviation > settings.tolerance:
                warnings.append(
                    "{} proxy surface sample(s) exceed tolerance by up to "
                    "{:.3f} m; these may be internal overlapping layers".format(
                        uncovered_samples,
                        coverage_deviation,
                    )
                )
                # Coverage is a safety invariant, not a quality score.  A
                # small detached architectural component must not disappear
                # merely because it contributes less than a percentage of a
                # large proxy.  Two samples are tolerated for numerical edge
                # cases; anything more outside the user tolerance rejects the
                # whole candidate and leaves the previous UCX set untouched.
                severe_coverage_loss = uncovered_samples > 2
                if severe_coverage_loss:
                    complete = False

    hulls = [(piece.vertices, piece.faces) for piece in convex_pieces]
    return DecompositionResult(
        hulls=hulls,
        max_deviation=max(
            maximum_open_deviation,
            maximum_approximation_deviation,
        ),
        total_triangles=sum(len(faces) for _, faces in hulls),
        seed=seed,
        warnings=warnings,
        complete=complete,
        remaining_invalid=len(invalid),
    )


def decompose(source, settings):
    attempts = [
        _run_attempt(source, settings, settings.seed + attempt_index)
        for attempt_index in range(settings.attempts)
    ]

    def score(result):
        return (
            0 if result.complete else 1,
            result.remaining_invalid,
            len(result.hulls),
            result.total_triangles,
        )

    return min(attempts, key=score)
