# SPDX-FileCopyrightText: 2026 XIVgate
# SPDX-License-Identifier: GPL-3.0-or-later
# Exact convex decomposition by planar cuts along reflex edges.
#
# A closed source component is split only by planes that contain one of its
# reflex (concave) edges and continue a neighbouring source facet. Every cut
# is exact: both halves share one triangulated cap and the parent volume is
# conserved to floating-point precision. A convex hull never replaces a
# concave region, so openings, niches and steps of the source are preserved.
# Only parts thinner than the thin-part threshold are ignored.

from dataclasses import dataclass, field
import itertools
import math

import bmesh
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import hull64


MAX_PARTS = 999
# Leaves may exceed the final limit before exact neighbours are merged again.
LEAF_LIMIT = MAX_PARTS * 4
# Vertices this close to a cutting plane lie on it: half of the minimum AGR
# gap, and above the float32 noise of long source faces.
PLANE_EPSILON = 1.0e-4
# Triangles join one planar facet only when all their vertices lie this close
# to its plane, so a facet used as a cutting plane is always classified on it.
FACET_EPSILON = 5.0e-5
# A facet plane is a support plane of its piece when no vertex lies further
# than this in front of it. Float32 source coordinates tilt long facade faces
# by up to about this much.
REFLEX_EPSILON = 1.0e-3
CONVEX_DEPTH_EPSILON = 1.0e-3
# Depth a piece may deviate from convexity and still count as convex. A run
# raises it to the automatic feature tolerance; numeric support-plane and
# containment tests keep REFLEX_EPSILON.
_feature_tolerance = CONVEX_DEPTH_EPSILON
# Facets smaller than this carry no reliable plane orientation.
MIN_FACET_SIZE = 1.0e-2
# Coordinate noise used to widen the support tolerance of small facets.
COORDINATE_NOISE = 2.0e-5
# A merge may add at most this hull volume per square metre of hull surface.
MERGE_FILM_EPSILON = 3.0e-5
# A candidate plane must leave at least this much material on both sides.
GRAZING_EPSILON = 1.0e-3
VOLUME_RELATIVE_EPSILON = 1.0e-7
VOLUME_ABSOLUTE_EPSILON = 1.0e-9
# Default thickness below which a part of the source is ignored.
THIN_PART_DEFAULT = 0.05
MAX_CUT_ATTEMPTS = 32
# Cuts actually tried per split; the one leaving the least concavity wins.
CUT_LOOKAHEAD = 12
# Alternative cuts tried per node during the refinement search.
REFINEMENT_TRIES = 3
# How far down its ranked planes the variant search walks one node.
SEARCH_MAX_SKIP = 24
# Largest share of the model's cutting work one re-cut node may carry.
SEARCH_WORK_SHARE = 0.25
# Real variants such a node may try before it is retired.
SEARCH_HEAVY_VARIANTS = 1
# Output hulls merge hull triangles into one planar polygon when their
# corners lie this close to a common plane.
OUTPUT_MERGE_DISTANCE = 1.0e-5
# Up to this many face planes the gap inset intersects every plane triple;
# above it only planes of neighbouring faces.
INSET_GLOBAL_PLANES = 48
# Automatic depth tolerance for shallow steps, as a share of the thin-part
# threshold: 0.4 of 5 cm lets a hull bridge steps and recesses up to 2 cm.
FEATURE_TOLERANCE_RATIO = 0.4


@dataclass(eq=False)
class Piece:
    vertices: np.ndarray
    faces: np.ndarray
    depth: int = 0
    closed: bool = False
    volume: float = 0.0
    concave_edges: int = 0
    concavity_depth: float = math.inf
    unsplittable: bool = False

    @property
    def convex(self):
        return (
            self.closed
            and self.concave_edges == 0
            and self.concavity_depth <= _feature_tolerance
        )


@dataclass
class DecompositionResult:
    hulls: list[tuple[np.ndarray, np.ndarray]]
    max_deviation: float
    total_triangles: int
    seed: int
    warnings: list[str] = field(default_factory=list)
    complete: bool = True
    remaining_invalid: int = 0
    ignored_parts: list = field(default_factory=list)
    failed_parts: list = field(default_factory=list)
    feature_tolerance: float = 0.0
    # Planar polygon faces of each hull, as vertex index lists, in the order
    # of ``hulls``; the triangles in ``hulls`` are their fans.
    polygons: list = field(default_factory=list)


# --------------------------------------------------------------------------
# Mesh topology
# --------------------------------------------------------------------------

@dataclass
class _Topology:
    start: np.ndarray        # directed edge start vertex, 3 per face
    end: np.ndarray          # directed edge end vertex
    opposite: np.ndarray     # vertex opposite the directed edge in its face
    face: np.ndarray         # face owning the directed edge
    first: np.ndarray        # directed edge index of each manifold edge
    second: np.ndarray       # its twin with reversed direction
    boundary_edges: int
    nonmanifold_edges: int
    misoriented_edges: int

    @property
    def closed(self):
        return (
            len(self.first) > 0
            and self.boundary_edges == 0
            and self.nonmanifold_edges == 0
            and self.misoriented_edges == 0
        )


def _topology(vertex_count, faces):
    faces = np.asarray(faces, dtype=np.int64).reshape((-1, 3))
    start = faces[:, [0, 1, 2]].reshape(-1)
    end = faces[:, [1, 2, 0]].reshape(-1)
    opposite = faces[:, [2, 0, 1]].reshape(-1)
    face = np.repeat(np.arange(len(faces)), 3)
    if len(start) == 0:
        empty = np.empty(0, dtype=np.int64)
        return _Topology(start, end, opposite, face, empty, empty, 0, 0, 0)
    low = np.minimum(start, end)
    high = np.maximum(start, end)
    key = low * max(int(vertex_count), 1) + high
    order = np.argsort(key, kind="stable")
    sorted_key = key[order]
    group_start = np.flatnonzero(
        np.r_[True, sorted_key[1:] != sorted_key[:-1]]
    )
    counts = np.diff(np.r_[group_start, len(sorted_key)])
    pairs = group_start[counts == 2]
    first = order[pairs]
    second = order[pairs + 1]
    misoriented = int(np.count_nonzero(start[first] != end[second]))
    return _Topology(
        start=start,
        end=end,
        opposite=opposite,
        face=face,
        first=first,
        second=second,
        boundary_edges=int(np.count_nonzero(counts == 1)),
        nonmanifold_edges=int(np.count_nonzero(counts > 2)),
        misoriented_edges=misoriented,
    )


def _face_normals(vertices, faces):
    a = vertices[faces[:, 0]]
    cross = np.cross(vertices[faces[:, 1]] - a, vertices[faces[:, 2]] - a)
    area2 = np.linalg.norm(cross, axis=1)
    normals = np.zeros_like(cross)
    valid = area2 > 1.0e-15
    normals[valid] = cross[valid] / area2[valid, None]
    return normals, area2 * 0.5


def _signed_volume(vertices, faces):
    if len(faces) == 0:
        return 0.0
    a = vertices[faces[:, 0]]
    b = vertices[faces[:, 1]]
    c = vertices[faces[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def _edge_fold_depths(vertices, topology, normals):
    """Signed fold of every manifold edge; positive values are reflex.

    Each neighbour's far vertex is measured against the other face plane and
    the smaller value is kept. A sliver triangle has an unreliable normal, but
    its own far vertex is always close to the shared edge, so the minimum stays
    geometrically meaningful.
    """
    first = topology.first
    second = topology.second
    origin = vertices[topology.start[first]]
    far_first = vertices[topology.opposite[first]]
    far_second = vertices[topology.opposite[second]]
    depth_a = np.einsum(
        "ij,ij->i",
        normals[topology.face[first]],
        far_second - origin,
    )
    depth_b = np.einsum(
        "ij,ij->i",
        normals[topology.face[second]],
        far_first - origin,
    )
    return np.minimum(depth_a, depth_b)


def _compact(vertices, faces):
    faces = np.asarray(faces, dtype=np.int64).reshape((-1, 3))
    if len(faces) == 0:
        return (
            np.empty((0, 3), dtype=np.float64),
            np.empty((0, 3), dtype=np.int32),
        )
    used = np.unique(faces.reshape(-1))
    remap = np.full(len(vertices), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return (
        np.asarray(vertices, dtype=np.float64)[used].copy(),
        remap[faces].astype(np.int32),
    )


def _new_bmesh(vertices, faces):
    bm = bmesh.new()
    bm_vertices = [bm.verts.new(tuple(point)) for point in vertices]
    for triangle in faces:
        try:
            bm.faces.new([bm_vertices[int(index)] for index in triangle])
        except ValueError:
            pass
    return bm


def _orient_outward(vertices, faces):
    """Return faces with consistent outward winding, or None if impossible."""
    topology = _topology(len(vertices), faces)
    if topology.boundary_edges or topology.nonmanifold_edges:
        return None
    if topology.misoriented_edges:
        bm = _new_bmesh(vertices, faces)
        try:
            if len(bm.faces) != len(faces):
                return None
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
            bm.verts.index_update()
            faces = np.asarray(
                [[vertex.index for vertex in face.verts] for face in bm.faces],
                dtype=np.int32,
            )
        finally:
            bm.free()
    if _signed_volume(vertices, faces) < 0.0:
        faces = faces[:, ::-1].copy()
    return faces


# --------------------------------------------------------------------------
# Convex hulls and piece analysis
# --------------------------------------------------------------------------

def _bmesh_triangles(bm):
    used = [vertex for vertex in bm.verts if vertex.link_faces]
    if len(used) < 4 or not bm.faces:
        return None
    index = {vertex: position for position, vertex in enumerate(used)}
    vertices = np.asarray([tuple(vertex.co) for vertex in used], dtype=np.float64)
    faces = np.asarray(
        [[index[vertex] for vertex in face.verts] for face in bm.faces],
        dtype=np.int32,
    )
    return vertices, faces


def _convex_hull(vertices, simplify=True):
    unique = np.unique(np.round(np.asarray(vertices, dtype=np.float64), 9), axis=0)
    if len(unique) < 4:
        return None
    # BMesh stores float32 coordinates; hull in local space to keep precision.
    origin = unique.mean(axis=0)
    bm = bmesh.new()
    try:
        bm_vertices = [bm.verts.new(tuple(point - origin)) for point in unique]
        result = bmesh.ops.convex_hull(
            bm,
            input=bm_vertices,
            use_existing_faces=False,
        )
        unused = [
            vertex
            for vertex in result.get("geom_unused", ())
            if isinstance(vertex, bmesh.types.BMVert)
        ]
        interior = [
            vertex
            for vertex in result.get("geom_interior", ())
            if isinstance(vertex, bmesh.types.BMVert)
        ]
        stray = list({vertex for vertex in unused + interior if not vertex.link_faces})
        if stray:
            bmesh.ops.delete(bm, geom=stray, context="VERTS")
        if not bm.faces:
            return None
        if simplify:
            bmesh.ops.dissolve_limit(
                bm,
                angle_limit=1.0e-5,
                use_dissolve_boundaries=False,
                verts=bm.verts[:],
                edges=bm.edges[:],
                delimit=set(),
            )
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        arrays = _bmesh_triangles(bm)
        if arrays is None:
            return None
        hull_vertices, hull_faces = arrays
        hull_vertices = hull_vertices + origin
        hull_faces = _orient_outward(hull_vertices, hull_faces)
        if hull_faces is None or len(hull_faces) < 4:
            return None
        return hull_vertices, hull_faces
    finally:
        bm.free()


def _surface_bvh(vertices, faces):
    return BVHTree.FromPolygons(
        [Vector(tuple(point)) for point in vertices],
        [tuple(int(index) for index in triangle) for triangle in faces],
        all_triangles=True,
        epsilon=0.0,
    )


def _analyse_piece(piece):
    vertices = np.asarray(piece.vertices, dtype=np.float64)
    piece.vertices = vertices
    if len(piece.faces) == 0 or len(vertices) < 4:
        piece.closed = False
        piece.volume = 0.0
        piece.concave_edges = 0
        piece.concavity_depth = math.inf
        piece.unsplittable = True
        return piece
    faces = _orient_outward(vertices, piece.faces)
    if faces is None:
        piece.faces = np.asarray(piece.faces, dtype=np.int32)
        piece.closed = False
        piece.volume = abs(_signed_volume(vertices, piece.faces))
        piece.concave_edges = 0
        piece.concavity_depth = math.inf
        return piece
    piece.faces = np.asarray(faces, dtype=np.int32)
    piece.closed = True
    piece.volume = _signed_volume(vertices, piece.faces)
    # A closed polyhedron is convex exactly when every facet plane supports
    # it. This test cannot be fooled by sliver triangles hiding a crease.
    topology = _topology(len(vertices), piece.faces)
    normals, offsets, areas, tolerances, centroids = _piece_planes(
        vertices, piece.faces, topology
    )
    if len(normals) == 0:
        piece.concave_edges = 0
        piece.concavity_depth = 0.0
        piece.violated_planes = None
        return piece
    piece.facet_planes = (normals, offsets, areas, tolerances, centroids)
    violation = (vertices @ normals.T - offsets).max(axis=0)
    violated = violation > tolerances
    piece.concave_edges = int(np.count_nonzero(violated))
    piece.concavity_depth = (
        float(violation[violated].max()) if piece.concave_edges else 0.0
    )
    piece.violated_planes = (
        normals[violated],
        offsets[violated],
        areas[violated],
        tolerances[violated],
        centroids[violated],
    )
    if piece.concave_edges:
        return piece

    # Facets fragmented into slivers by earlier cuts may not form a reliable
    # plane. Every vertex of a convex piece lies on its hull, so a vertex
    # below the hull reveals such a hidden recess.
    hull = _convex_hull(vertices, simplify=False)
    if hull is None:
        return piece
    hull_planes = _convex_planes(Piece(vertices=hull[0], faces=hull[1]))
    if not hull_planes:
        return piece
    hull_normals = np.asarray([normal for _point, normal in hull_planes])
    hull_offsets = np.asarray([float(normal @ point) for point, normal in hull_planes])
    depth = (hull_offsets[None, :] - vertices @ hull_normals.T).min(axis=1)
    deep = np.flatnonzero(depth > _feature_tolerance)
    if len(deep) == 0:
        return piece
    piece.concavity_depth = float(depth[deep].max())
    deep = deep[np.argsort(-depth[deep])][:32]
    # Facets that run through the deepest vertices describe the recess; their
    # planes are the architectural cuts, unlike single sliver triangles.
    through = np.abs(
        vertices[deep] @ normals.T - offsets
    ).min(axis=0) <= REFLEX_EPSILON * 2.0
    if through.any():
        piece.concave_edges = int(np.count_nonzero(through))
        piece.violated_planes = (
            normals[through],
            offsets[through],
            areas[through],
            tolerances[through],
            centroids[through],
        )
        return piece

    incident = np.flatnonzero(np.isin(piece.faces, deep).any(axis=1))
    face_normals, face_areas = _face_normals(vertices, piece.faces[incident])
    found = {}
    for index, face in enumerate(incident):
        if face_areas[index] <= 1.0e-10:
            continue
        unit = face_normals[index]
        offset = float(unit @ vertices[piece.faces[face, 0]])
        key = (*np.round(unit * 1.0e3).astype(np.int64).tolist(), int(round(offset * 1.0e3)))
        if key in found:
            found[key][2] += float(face_areas[index])
        else:
            found[key] = [
                unit,
                offset,
                float(face_areas[index]),
                vertices[piece.faces[face]].mean(axis=0),
            ]
    if not found:
        return piece
    rows = list(found.values())
    piece.concave_edges = len(rows)
    piece.violated_planes = (
        np.asarray([row[0] for row in rows]),
        np.asarray([row[1] for row in rows]),
        np.asarray([row[2] for row in rows]),
        np.full(len(rows), _feature_tolerance),
        np.asarray([row[3] for row in rows]),
    )
    return piece


def _piece_planes(vertices, faces, topology=None):
    """Planar facets of a closed piece: normals, offsets, areas, tolerances.

    Facets grow from the largest triangles, whose normals are reliable.
    Neighbours join by vertex distance to the seed plane, so sliver triangles
    left along cut lines belong to the facet they lie in instead of hiding it.
    """
    faces = np.asarray(faces, dtype=np.int64)
    if topology is None:
        topology = _topology(len(vertices), faces)
    normals, areas = _face_normals(vertices, faces)
    corners = vertices[faces]
    longest = np.max(
        np.stack((
            np.linalg.norm(corners[:, 1] - corners[:, 0], axis=1),
            np.linalg.norm(corners[:, 2] - corners[:, 1], axis=1),
            np.linalg.norm(corners[:, 0] - corners[:, 2], axis=1),
        )),
        axis=0,
    )
    heights = 2.0 * areas / np.maximum(longest, 1.0e-12)
    # Face adjacency as one sorted array pair, so the region growing below
    # only slices it instead of walking Python lists.
    left_faces = topology.face[topology.first]
    right_faces = topology.face[topology.second]
    owners = np.concatenate((left_faces, right_faces))
    others = np.concatenate((right_faces, left_faces))
    order = np.argsort(owners, kind="stable")
    owners = owners[order]
    others = others[order]
    starts = np.searchsorted(owners, np.arange(len(faces) + 1))
    # The walk below reads neighbours one face at a time, so the adjacency is
    # handed to it as plain lists; slicing a numpy array per face cost more
    # than the lookup itself.
    others_list = others.tolist()
    starts_list = starts.tolist()
    neighbours = [
        others_list[starts_list[face]:starts_list[face + 1]]
        for face in range(len(faces))
    ]

    diameter = max(float(np.ptp(vertices, axis=0).max()), MIN_FACET_SIZE)
    assigned = np.zeros(len(faces), dtype=bool)
    rows = []
    for seed in np.argsort(-areas, kind="stable"):
        if assigned[seed]:
            continue
        if heights[seed] < MIN_FACET_SIZE:
            continue
        unit = normals[seed]
        offset = float(unit @ corners[seed, 0])
        # Every face is tested against this plane once, vectorised, instead
        # of three numpy calls per face inside the walk below. On small
        # fragments the call overhead of those tiny operations dominated the
        # arithmetic, and this function is a third of the whole run.
        on_plane = np.all(
            np.abs(corners @ unit - offset) <= FACET_EPSILON, axis=1
        ).tolist()
        members = [int(seed)]
        assigned[seed] = True
        stack = [int(seed)]
        while stack:
            face = stack.pop()
            for other in neighbours[face]:
                if assigned[other] or not on_plane[other]:
                    continue
                assigned[other] = True
                members.append(other)
                stack.append(other)
        members = np.asarray(members)
        weighted = (normals[members] * areas[members, None]).sum(axis=0)
        norm = float(np.linalg.norm(weighted))
        if norm > 1.0e-15:
            unit = weighted / norm
        points = vertices[np.unique(faces[members].reshape(-1))]
        offset = float(np.median(points @ unit))
        size = max(float(np.ptp(points, axis=0).max()), float(heights[seed]))
        tolerance = _feature_tolerance + min(
            COORDINATE_NOISE * diameter / size, REFLEX_EPSILON
        )
        rows.append((
            unit,
            offset,
            float(areas[members].sum()),
            tolerance,
            points.mean(axis=0),
        ))
    if not rows:
        return (
            np.empty((0, 3)), np.empty(0), np.empty(0), np.empty(0),
            np.empty((0, 3)),
        )
    return (
        np.asarray([row[0] for row in rows]),
        np.asarray([row[1] for row in rows]),
        np.asarray([row[2] for row in rows]),
        np.asarray([row[3] for row in rows]),
        np.asarray([row[4] for row in rows]),
    )


def _component_pieces(vertices, faces):
    """Split a triangle soup into edge-connected components."""
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64).reshape((-1, 3))
    if len(faces) == 0:
        return []
    topology = _topology(len(vertices), faces)
    parent = np.arange(len(faces))

    def find(index):
        root = index
        while parent[root] != root:
            root = parent[root]
        while parent[index] != root:
            parent[index], index = root, parent[index]
        return root

    # Faces sharing any edge belong together, including open or non-manifold
    # edges, so a damaged component is reported as one unit.
    low = np.minimum(topology.start, topology.end)
    high = np.maximum(topology.start, topology.end)
    key = low * len(vertices) + high
    order = np.argsort(key, kind="stable")
    sorted_key = key[order]
    for position in range(1, len(order)):
        if sorted_key[position] == sorted_key[position - 1]:
            left = find(int(topology.face[order[position - 1]]))
            right = find(int(topology.face[order[position]]))
            if left != right:
                parent[right] = left
    roots = np.asarray([find(index) for index in range(len(faces))])
    pieces = []
    for root in np.unique(roots):
        component_vertices, component_faces = _compact(
            vertices,
            faces[roots == root],
        )
        pieces.append(
            _analyse_piece(
                Piece(vertices=component_vertices, faces=component_faces)
            )
        )
    return pieces


# --------------------------------------------------------------------------
# Exact plane cut
# --------------------------------------------------------------------------

def _plane_basis(normal):
    helper = (
        np.asarray((1.0, 0.0, 0.0))
        if abs(normal[0]) < 0.9
        else np.asarray((0.0, 1.0, 0.0))
    )
    u = np.cross(normal, helper)
    u /= np.linalg.norm(u)
    w = np.cross(normal, u)
    return u, w


def _trace_cap_loops(edges, points_2d):
    """Chain directed cap edges into closed loops keeping the region on the left."""
    outgoing = {}
    for start, end in edges:
        outgoing.setdefault(start, []).append(end)
    remaining = {(start, end) for start, end in edges}
    loops = []
    while remaining:
        start, end = min(remaining)
        remaining.remove((start, end))
        loop = [start]
        previous, current = start, end
        guard = 0
        while current != start:
            loop.append(current)
            candidates = [
                nxt for nxt in outgoing.get(current, ())
                if (current, nxt) in remaining
            ]
            if not candidates:
                return None
            if len(candidates) > 1:
                # At a pinch vertex take the sharpest left turn so that two
                # regions touching in one point become two simple loops.
                incoming = points_2d[current] - points_2d[previous]
                base = math.atan2(incoming[1], incoming[0])

                def turn(nxt):
                    direction = points_2d[nxt] - points_2d[current]
                    angle = math.atan2(direction[1], direction[0]) - base
                    return (angle + math.pi) % (2.0 * math.pi)

                nxt = max(candidates, key=turn)
            else:
                nxt = candidates[0]
            remaining.remove((current, nxt))
            previous, current = current, nxt
            guard += 1
            if guard > len(edges) + 1:
                return None
        if len(loop) >= 3:
            loops.append(loop)
    return loops


def _ring_area(ring):
    x = ring[:, 0]; y = ring[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))

def _point_in_ring(point, ring):
    x, y = point
    inside = False
    xs = ring[:, 0]; ys = ring[:, 1]
    xn = np.roll(xs, -1); yn = np.roll(ys, -1)
    crossing = (ys > y) != (yn > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xi = xs + (y - ys) * (xn - xs) / (yn - ys)
    return bool(np.count_nonzero(crossing & (x < xi)) % 2)

def _triangulate_loops(loops, coords):
    """Triangulate planar loops with holes in float64 by ear clipping.

    The region lies left of every loop: outer loops run counter-clockwise and
    holes clockwise. Holes are bridged into their outer loop first. Output
    triangles reference the original vertex ids and never add vertices.
    """
    rings = [np.asarray([coords[i] for i in loop], dtype=np.float64) for loop in loops]
    areas = [_ring_area(r) for r in rings]
    outers = [k for k, a in enumerate(areas) if a > 0.0]
    holes = [k for k, a in enumerate(areas) if a <= 0.0]
    assignment = {k: [] for k in outers}
    for h in holes:
        probe = rings[h][int(np.argmax(rings[h][:, 0]))]
        containing = [k for k in outers if _point_in_ring(probe, rings[k])]
        if not containing:
            return None
        best = min(containing, key=lambda k: areas[k])
        assignment[best].append(h)
    triangles = []
    for k in outers:
        polygon = list(loops[k])
        for h in sorted(assignment[k], key=lambda h: -float(rings[h][:, 0].max())):
            polygon = _bridge_hole(polygon, list(loops[h]), coords)
            if polygon is None:
                return None
        result = _ear_clip(polygon, coords)
        if result is None:
            return None
        triangles.extend(result)
    return triangles

def _bridge_hole(polygon, hole, coords):
    hx = np.asarray([coords[i] for i in hole])
    m = int(np.argmax(hx[:, 0]))
    mx, my = hx[m]
    pts = np.asarray([coords[i] for i in polygon])
    nxt = np.roll(pts, -1, axis=0)
    best = None
    for e in range(len(polygon)):
        (x0, y0), (x1, y1) = pts[e], nxt[e]
        if (y0 > my) == (y1 > my) and not (y0 == my or y1 == my):
            continue
        if y0 == y1:
            continue
        t = (my - y0) / (y1 - y0)
        if t < 0.0 or t > 1.0:
            continue
        xi = x0 + t * (x1 - x0)
        if xi < mx:
            continue
        if best is None or xi < best[0]:
            best = (xi, e)
    if best is None:
        return None
    xi, e = best
    # endpoint of the hit edge with larger x is the candidate
    cand = e if pts[e][0] >= nxt[e][0] else (e + 1) % len(polygon)
    px, py = pts[cand]
    # reflex vertices inside triangle (M, I, P) take precedence: smallest angle to ray
    tri = np.asarray([(mx, my), (xi, my), (px, py)])
    chosen = cand
    best_angle = None
    for v in range(len(polygon)):
        if v == cand:
            continue
        qx, qy = pts[v]
        if qx < mx:
            continue
        if _inside_triangle((qx, qy), tri, strict=False):
            angle = abs(math.atan2(qy - my, qx - mx))
            dist = math.hypot(qx - mx, qy - my)
            key = (angle, dist)
            if best_angle is None or key < best_angle:
                best_angle = key
                chosen = v
    rotated_hole = hole[m:] + hole[:m]
    return polygon[:chosen + 1] + rotated_hole + [rotated_hole[0], polygon[chosen]] + polygon[chosen + 1:]

def _inside_triangle(q, tri, strict=True):
    (ax, ay), (bx, by), (cx, cy) = tri
    d1 = (bx - ax) * (q[1] - ay) - (by - ay) * (q[0] - ax)
    d2 = (cx - bx) * (q[1] - by) - (cy - by) * (q[0] - bx)
    d3 = (ax - cx) * (q[1] - cy) - (ay - cy) * (q[0] - cx)
    if strict:
        return (d1 > 0 and d2 > 0 and d3 > 0) or (d1 < 0 and d2 < 0 and d3 < 0)
    return (d1 >= 0 and d2 >= 0 and d3 >= 0) or (d1 <= 0 and d2 <= 0 and d3 <= 0)

def _ear_clip(polygon, coords):
    ids = list(polygon)
    pts = np.asarray([coords[i] for i in ids], dtype=np.float64)
    n = len(ids)
    prev = list(range(-1, n - 1)); prev[0] = n - 1
    nxt = list(range(1, n + 1)); nxt[-1] = 0
    alive = np.ones(n, dtype=bool)
    scale = max(float(np.ptp(pts, axis=0).max()), 1e-9)
    eps = scale * scale * 1e-14
    triangles = []
    remaining = n
    current = 0
    stall = 0
    allow_flat = False
    while remaining > 3:
        p, c, q = prev[current], current, nxt[current]
        a, b, d = pts[p], pts[c], pts[q]
        cross = (b[0] - a[0]) * (d[1] - a[1]) - (b[1] - a[1]) * (d[0] - a[0])
        is_ear = False
        if cross > eps or (allow_flat and cross >= -eps):
            others = np.flatnonzero(alive)
            others = others[(others != p) & (others != c) & (others != q)]
            if len(others):
                o = pts[others]
                # ignore vertices coincident with the triangle corners (bridge duplicates)
                same = (
                    np.all(np.abs(o - a) <= 1e-12, axis=1)
                    | np.all(np.abs(o - b) <= 1e-12, axis=1)
                    | np.all(np.abs(o - d) <= 1e-12, axis=1)
                )
                o = o[~same]
                if cross > eps and len(o):
                    d1 = (b[0] - a[0]) * (o[:, 1] - a[1]) - (b[1] - a[1]) * (o[:, 0] - a[0])
                    d2 = (d[0] - b[0]) * (o[:, 1] - b[1]) - (d[1] - b[1]) * (o[:, 0] - b[0])
                    d3 = (a[0] - d[0]) * (o[:, 1] - d[1]) - (a[1] - d[1]) * (o[:, 0] - d[0])
                    inside = (d1 >= -eps) & (d2 >= -eps) & (d3 >= -eps)
                    is_ear = not np.any(inside)
                else:
                    is_ear = True
            else:
                is_ear = True
        if is_ear:
            triangles.append((ids[p], ids[c], ids[q]))
            alive[c] = False
            nxt[p] = q
            prev[q] = p
            remaining -= 1
            current = q
            stall = 0
            allow_flat = False
        else:
            current = q
            stall += 1
            if stall > remaining:
                if allow_flat:
                    return None
                allow_flat = True
                stall = 0
    last = np.flatnonzero(alive)
    c = int(last[0])
    triangles.append((ids[prev[c]], ids[c], ids[nxt[c]]))
    return triangles


def _cut_piece(piece, plane_point, plane_normal):
    """Split a closed piece by a plane.

    Returns (negative_components, positive_components) or None when the plane
    misses the piece or the cut cannot be closed exactly.
    """
    normal = np.asarray(plane_normal, dtype=np.float64)
    length = float(np.linalg.norm(normal))
    if length <= 1.0e-12:
        return None
    normal = normal / length
    offset = float(np.dot(normal, plane_point))
    vertices = piece.vertices
    faces = piece.faces.astype(np.int64)
    distance = vertices @ normal - offset
    side = np.zeros(len(vertices), dtype=np.int8)
    side[distance > PLANE_EPSILON] = 1
    side[distance < -PLANE_EPSILON] = -1
    if not (side > 0).any() or not (side < 0).any():
        return None
    # Snap the on-plane band exactly onto the plane. Both halves then share
    # one flat cap, so the clearance between them is the requested gap and
    # not the leftover tilt of the band.
    on_plane = side == 0
    if on_plane.any():
        vertices = vertices.copy()
        vertices[on_plane] -= np.outer(distance[on_plane], normal)
        distance = distance.copy()
        distance[on_plane] = 0.0

    face_side = side[faces]
    any_positive = (face_side > 0).any(axis=1)
    any_negative = (face_side < 0).any(axis=1)
    positive_mask = any_positive & ~any_negative
    negative_mask = any_negative & ~any_positive
    coplanar_mask = ~any_positive & ~any_negative
    crossing = np.flatnonzero(any_positive & any_negative)

    negative_faces = [faces[negative_mask]]
    positive_faces = [faces[positive_mask]]
    if coplanar_mask.any():
        normals, _areas = _face_normals(vertices, faces[coplanar_mask])
        facing = normals @ normal
        coplanar = faces[coplanar_mask]
        # A face on the plane bounds the half on the opposite side of its
        # outward normal.
        negative_faces.append(coplanar[facing >= 0.0])
        positive_faces.append(coplanar[facing < 0.0])

    new_points = []
    edge_points = {}

    def crossing_vertex(left, right):
        key = (left, right) if left < right else (right, left)
        index = edge_points.get(key)
        if index is None:
            t = distance[left] / (distance[left] - distance[right])
            new_points.append(vertices[left] + (vertices[right] - vertices[left]) * t)
            index = len(vertices) + len(new_points) - 1
            edge_points[key] = index
        return index

    extra_negative = []
    extra_positive = []
    for face_index in crossing:
        triangle = faces[face_index]
        signs = side[triangle]
        zero = np.flatnonzero(signs == 0)
        if len(zero) == 1:
            k = int(zero[0])
            z, p, q = triangle[k], triangle[(k + 1) % 3], triangle[(k + 2) % 3]
            x = crossing_vertex(int(p), int(q))
            first = (z, p, x)
            second = (z, x, q)
            (extra_positive if side[p] > 0 else extra_negative).append(first)
            (extra_positive if side[q] > 0 else extra_negative).append(second)
        else:
            total = int(signs.sum())
            lone_sign = -1 if total > 0 else 1
            k = int(np.flatnonzero(signs == lone_sign)[0])
            lone, a, b = triangle[k], triangle[(k + 1) % 3], triangle[(k + 2) % 3]
            x1 = crossing_vertex(int(lone), int(a))
            x2 = crossing_vertex(int(b), int(lone))
            lone_faces = [(lone, x1, x2)]
            other_faces = [(x1, a, b), (x1, b, x2)]
            if lone_sign > 0:
                extra_positive.extend(lone_faces)
                extra_negative.extend(other_faces)
            else:
                extra_negative.extend(lone_faces)
                extra_positive.extend(other_faces)

    if new_points:
        all_vertices = np.vstack((vertices, np.asarray(new_points)))
    else:
        all_vertices = vertices
    all_side = np.r_[side, np.zeros(len(new_points), dtype=np.int8)]
    all_distance = np.r_[distance, np.zeros(len(new_points))]
    if extra_negative:
        negative_faces.append(np.asarray(extra_negative, dtype=np.int64))
    if extra_positive:
        positive_faces.append(np.asarray(extra_positive, dtype=np.int64))
    negative = np.vstack(negative_faces) if negative_faces else np.empty((0, 3))
    positive = np.vstack(positive_faces) if positive_faces else np.empty((0, 3))
    if len(negative) == 0 or len(positive) == 0:
        return None

    # Sliver triangles can place two section points a few micrometres apart.
    # Weld them so that the cap triangulation sees one point.
    weld = _weld_plane_vertices(all_vertices, all_side, np.vstack((negative, positive)))
    if weld is not None:
        negative = weld[negative]
        positive = weld[positive]
        negative = negative[
            (negative[:, 0] != negative[:, 1])
            & (negative[:, 1] != negative[:, 2])
            & (negative[:, 2] != negative[:, 0])
        ]
        positive = positive[
            (positive[:, 0] != positive[:, 1])
            & (positive[:, 1] != positive[:, 2])
            & (positive[:, 2] != positive[:, 0])
        ]
        if len(negative) == 0 or len(positive) == 0:
            return None

    # The open border of the negative half lies on the plane. Its reversed
    # edges form the cap boundary with the cap region on the left.
    topology = _topology(len(all_vertices), negative)
    low = np.minimum(topology.start, topology.end)
    high = np.maximum(topology.start, topology.end)
    key = low * len(all_vertices) + high
    unique_keys, inverse, counts = np.unique(
        key,
        return_inverse=True,
        return_counts=True,
    )
    if np.any(counts > 2):
        return None
    border = np.flatnonzero(counts[inverse] == 1)
    if len(border) < 3:
        return None
    border_start = topology.start[border]
    border_end = topology.end[border]
    if np.any(all_side[border_start] != 0) or np.any(all_side[border_end] != 0):
        return None

    u, w = _plane_basis(normal)
    cap_vertices = np.unique(np.r_[border_start, border_end])
    # Local coordinates keep float32 mathutils vectors precise.
    cap_origin = all_vertices[cap_vertices].mean(axis=0)
    points_2d = {
        int(index): np.asarray(
            (
                float(np.dot(all_vertices[index] - cap_origin, u)),
                float(np.dot(all_vertices[index] - cap_origin, w)),
            )
        )
        for index in cap_vertices
    }
    cap_edges = [
        (int(end), int(start))
        for start, end in zip(border_start, border_end)
    ]
    loops = _trace_cap_loops(cap_edges, points_2d)
    if not loops:
        return None
    triangles = _triangulate_loops(loops, points_2d)
    if not triangles:
        return None
    cap = np.asarray(triangles, dtype=np.int64)
    signed = 0.0
    for a, b, c in cap:
        pa, pb, pc = points_2d[int(a)], points_2d[int(b)], points_2d[int(c)]
        signed += (
            (pb[0] - pa[0]) * (pc[1] - pa[1])
            - (pc[0] - pa[0]) * (pb[1] - pa[1])
        )
    # The negative cap faces along +normal: counter-clockwise in (u, w).
    if signed < 0.0:
        cap = cap[:, ::-1]

    loop_area = 0.0
    for loop in loops:
        ring = np.asarray([points_2d[index] for index in loop])
        x, y = ring[:, 0], ring[:, 1]
        loop_area += 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    cap_area_2d = 0.0
    for a, b, c in cap:
        pa, pb, pc = points_2d[int(a)], points_2d[int(b)], points_2d[int(c)]
        cap_area_2d += 0.5 * ((pb[0] - pa[0]) * (pc[1] - pa[1]) - (pc[0] - pa[0]) * (pb[1] - pa[1]))
    # Both halves share the cap, so volume conservation cannot reveal a cap
    # that covers empty space. The exact section area can.
    if (
        loop_area <= 0.0
        or abs(cap_area_2d - loop_area) > 1.0e-6 * max(loop_area, 1.0) + 1.0e-7
    ):
        return None
    # Every cap point must lie inside the parent solid. A cap that covers
    # empty space conserves volume (both halves share it) and would pass the
    # checks below, but its winding number with respect to the parent is 0.
    cap_corners = all_vertices[cap]
    cap_areas = 0.5 * np.linalg.norm(
        np.cross(cap_corners[:, 1] - cap_corners[:, 0], cap_corners[:, 2] - cap_corners[:, 0]),
        axis=1,
    )
    probe = np.argsort(-cap_areas)[:64]
    probe = probe[cap_areas[probe] > 1.0e-8]
    if len(probe):
        winding = _winding_numbers(
            piece.vertices - cap_origin,
            piece.faces.astype(np.int64),
            cap_corners[probe].mean(axis=1) - cap_origin,
        )
        if np.any(winding < 0.5):
            return None
    # A face lying on the plane must face away from its half. A face facing
    # into its own half means a tilted source face straddled the on-plane band
    # and would leave a sheet over empty space.
    for half_faces, direction in ((negative, 1.0), (positive, -1.0)):
        near = np.all(np.abs(all_distance[half_faces]) <= PLANE_EPSILON * 5.0, axis=1)
        if near.any():
            half_normals, half_areas = _face_normals(all_vertices, half_faces[near])
            if np.any((half_normals @ normal) * direction < -0.5):
                return None
    negative_closed = np.vstack((negative, cap))
    positive_closed = np.vstack((positive, cap[:, ::-1]))
    halves = []
    total_volume = 0.0
    for half_faces in (negative_closed, positive_closed):
        half_topology = _topology(len(all_vertices), half_faces)
        if not half_topology.closed:
            return None
        components = _split_components(all_vertices, half_faces, half_topology)
        halves.append(components)
        total_volume += sum(component.volume for component in components)
    # Welding moves section points by at most PLANE_EPSILON inside the plane,
    # so the conserved volume may only drift by that band over the section.
    cap_area = float(_face_normals(all_vertices, cap)[1].sum())
    allowance = (
        VOLUME_ABSOLUTE_EPSILON
        + abs(piece.volume) * VOLUME_RELATIVE_EPSILON
        + 2.0 * PLANE_EPSILON * cap_area
    )
    if abs(total_volume - piece.volume) > allowance:
        return None
    for components in halves:
        for component in components:
            component.depth = piece.depth + 1
    return halves[0], halves[1]


def _weld_plane_vertices(vertices, side, faces, distance=PLANE_EPSILON):
    """Return an index map merging on-plane vertices closer than distance."""
    used = np.unique(faces.reshape(-1))
    candidates = used[side[used] == 0]
    if len(candidates) < 2:
        return None
    points = vertices[candidates]
    cells = np.floor(points / distance).astype(np.int64)
    grid = {}
    for position, cell in enumerate(map(tuple, cells)):
        grid.setdefault(cell, []).append(position)
    remap = np.arange(len(vertices))
    merged = False
    neighbours = [
        (dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
    ]
    for position, cell in enumerate(map(tuple, cells)):
        index = candidates[position]
        if remap[index] != index:
            continue
        for dx, dy, dz in neighbours:
            for other in grid.get((cell[0] + dx, cell[1] + dy, cell[2] + dz), ()):
                target = candidates[other]
                if other <= position or remap[target] != target:
                    continue
                delta = points[other] - points[position]
                if float(delta @ delta) <= distance * distance:
                    remap[target] = index
                    merged = True
    return remap if merged else None


def _split_components(vertices, faces, topology):
    parent = np.arange(len(faces))

    def find(index):
        root = index
        while parent[root] != root:
            root = parent[root]
        while parent[index] != root:
            parent[index], index = root, parent[index]
        return root

    for left, right in zip(topology.face[topology.first], topology.face[topology.second]):
        a, b = find(int(left)), find(int(right))
        if a != b:
            parent[b] = a
    roots = np.asarray([find(index) for index in range(len(faces))])
    components = []
    for root in np.unique(roots):
        component_vertices, component_faces = _compact(vertices, faces[roots == root])
        volume = _signed_volume(component_vertices, component_faces)
        if volume <= VOLUME_ABSOLUTE_EPSILON:
            # A zero-volume flap between touching regions carries no solid.
            continue
        components.append(
            _analyse_piece(Piece(vertices=component_vertices, faces=component_faces))
        )
    return components


# --------------------------------------------------------------------------
# Choice of the cutting plane
# --------------------------------------------------------------------------

def _section_area(vertices, faces, normal, offset):
    """Area of the solid cross-section by a plane, without cutting.

    Every triangle is clipped to the negative half-space in winding order.
    Clipped edges lying on the plane bound the negative half there; interior
    ones cancel, and Green theorem turns the remaining border into area.
    """
    distance = vertices @ normal - offset
    side = np.zeros(len(vertices), dtype=np.int8)
    side[distance > PLANE_EPSILON] = 1
    side[distance < -PLANE_EPSILON] = -1
    face_side = side[faces]
    touching = (face_side == 0).any(axis=1) | (
        (face_side > 0).any(axis=1) & (face_side < 0).any(axis=1)
    )
    coplanar = ~face_side.any(axis=1)
    if coplanar.any():
        corners = vertices[faces[coplanar]]
        facing = np.cross(
            corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]
        ) @ normal
        # A face on the plane bounds the negative half only when facing +normal.
        touching[np.flatnonzero(coplanar)[facing < 0.0]] = False
    selected = faces[touching]
    if len(selected) == 0:
        return 0.0
    corner_points = vertices[selected]
    corner_side = side[selected]
    corner_distance = distance[selected]
    count = len(selected)
    points = np.zeros((count, 6, 3))
    valid = np.zeros((count, 6), dtype=bool)
    on = np.zeros((count, 6), dtype=bool)
    for corner in range(3):
        following = (corner + 1) % 3
        points[:, corner * 2] = corner_points[:, corner]
        valid[:, corner * 2] = corner_side[:, corner] <= 0
        on[:, corner * 2] = corner_side[:, corner] == 0
        crossing = corner_side[:, corner] * corner_side[:, following] < 0
        denominator = corner_distance[:, corner] - corner_distance[:, following]
        t = np.divide(
            corner_distance[:, corner],
            denominator,
            out=np.zeros(count),
            where=crossing,
        )
        points[:, corner * 2 + 1] = corner_points[:, corner] + (
            corner_points[:, following] - corner_points[:, corner]
        ) * t[:, None]
        valid[:, corner * 2 + 1] = crossing
        on[:, corner * 2 + 1] = crossing
    total = 0.0
    slots = np.arange(6)
    rows = np.arange(count)
    for slot in range(6):
        # Next valid slot of every clipped polygon, cyclically.
        order = (slot + 1 + slots) % 6
        following_valid = valid[:, order]
        nxt = order[np.argmax(following_valid, axis=1)]
        use = valid[:, slot] & on[:, slot] & following_valid.any(axis=1)
        use &= on[rows, nxt]
        if not use.any():
            continue
        start = points[use, slot]
        end = points[rows[use], nxt[use]]
        total += float((np.cross(start, end) @ normal).sum())
    return abs(total) * 0.5


def _rank_planes(piece, normals, offsets, areas, tolerances, centroids, thin_limit):
    """Order cutting planes by concavity removed per unit of section area.

    Each violated facet plane has a set of vertices in front of it. A cut
    removes those that end up in the other child than the facet. Cutting a
    pilaster off along its wall, or the tower off the grooves of its plinth,
    removes many such violations through a modest section; a plane through
    the side of a single groove slices the building for little gain.
    """
    if len(normals) == 0:
        return []
    vertices = piece.vertices
    faces = piece.faces.astype(np.int64)
    distance = vertices @ normals.T - offsets
    side = np.zeros(distance.shape, dtype=np.int8)
    side[distance > PLANE_EPSILON] = 1
    side[distance < -PLANE_EPSILON] = -1
    cuts = (
        (distance.max(axis=0) > GRAZING_EPSILON)
        & (-distance.min(axis=0) > GRAZING_EPSILON)
    )
    unique = {}
    for index in np.flatnonzero(cuts):
        unique.setdefault(tuple(side[:, index].tolist()), int(index))
    candidates = list(unique.values())
    if not candidates:
        return []

    # A plane running closer than the thin limit to a parallel facet of the
    # same piece slices a slab out of solid material instead of cutting a
    # feature off; such planes go last.
    slab = np.zeros(len(candidates), dtype=bool)
    facets = getattr(piece, "facet_planes", None)
    if facets is not None and len(facets[0]):
        facet_normals, facet_offsets = facets[0], facets[1]
        alignment = normals[candidates] @ facet_normals.T
        separation = np.abs(
            np.sign(alignment) * facet_offsets[None, :] - offsets[candidates][:, None]
        )
        slab = np.any(
            (np.abs(alignment) > 0.999)
            & (separation > GRAZING_EPSILON)
            & (separation < thin_limit),
            axis=1,
        )


    front = (distance > tolerances[None, :]).astype(np.float32)
    positive = (side[:, candidates] > 0).astype(np.float32)
    negative = (side[:, candidates] < 0).astype(np.float32)
    in_negative = front.T @ negative
    in_positive = front.T @ positive
    facet_distance = centroids @ normals[candidates].T - offsets[candidates]
    facet_side = np.sign(facet_distance)
    on_cut = np.abs(facet_distance) <= PLANE_EPSILON * 5.0
    alignment = normals @ normals[candidates].T
    facet_side[on_cut] = np.where(alignment[on_cut] > 0.0, -1.0, 1.0)
    removed = np.where(facet_side > 0, in_negative, in_positive).sum(axis=0)

    sections = np.asarray([
        _section_area(vertices, faces, normals[index], offsets[index])
        for index in candidates
    ])
    score = removed / (sections + 1.0e-2)
    order = sorted(
        (
            position
            for position in range(len(candidates))
            if sections[position] > 1.0e-6 and removed[position] > 0.0
        ),
        key=lambda position: (
            bool(slab[position]),
            -round(float(score[position]), 9),
            round(float(sections[position]), 6),
            tuple(np.round(normals[candidates[position]], 6)),
        ),
    )
    return [
        (normals[candidates[position]], offsets[candidates[position]])
        for position in order
    ]


def _fallback_planes(piece, limit=8):
    """Face, bisector and axis planes through the most folded edges."""
    vertices = piece.vertices
    faces = piece.faces.astype(np.int64)
    topology = _topology(len(vertices), faces)
    normals, _areas = _face_normals(vertices, faces)
    depths = _edge_fold_depths(vertices, topology, normals)
    found = {}
    for edge in np.argsort(-depths)[:limit]:
        if depths[edge] <= 0.0:
            break
        first = topology.first[edge]
        second = topology.second[edge]
        a = vertices[topology.start[first]]
        b = vertices[topology.end[first]]
        direction = b - a
        length = float(np.linalg.norm(direction))
        if length <= 1.0e-9:
            continue
        direction /= length
        n1 = normals[topology.face[first]]
        n2 = normals[topology.face[second]]
        for raw in (n1, n2, n1 + n2, n1 - n2, *np.eye(3)):
            unit = raw - direction * float(raw @ direction)
            unit_length = float(np.linalg.norm(unit))
            if unit_length <= 1.0e-6:
                continue
            unit = unit / unit_length
            offset = float(unit @ ((a + b) * 0.5))
            key = (
                *np.round(unit * 1.0e4).astype(np.int64).tolist(),
                int(round(offset * 1.0e4)),
            )
            found.setdefault(key, (unit, offset, (a + b) * 0.5))
    if not found:
        return (
            np.empty((0, 3)), np.empty(0), np.empty(0), np.empty(0),
            np.empty((0, 3)),
        )
    rows = list(found.values())
    return (
        np.asarray([row[0] for row in rows]),
        np.asarray([row[1] for row in rows]),
        np.zeros(len(rows)),
        np.full(len(rows), _feature_tolerance),
        np.asarray([row[2] for row in rows]),
    )


def _split_piece_keyed(piece, thin_limit, skip=0):
    """Cut a piece and name the cut that was taken.

    With skip > 0 the best ranked planes are passed over, which yields a
    different, equally exact variant of the subtree. The name is the stage
    the plane came from and its position in that stage's ranking, so the
    search can tell two variants that took the same cut apart from two that
    did not.

    Rankings and cuts are kept on the piece. The variant search asks for the
    same piece again with the window moved by one plane; the window shares
    all but one plane with the previous one, and without the cache every one
    of them was ranked and cut again.
    """
    violated = getattr(piece, "violated_planes", None)
    if violated is None:
        return None, None
    stages = (
        lambda: violated,
        lambda: getattr(piece, "facet_planes", None),
        lambda: _fallback_planes(piece),
    )
    rankings = piece.__dict__.setdefault("_rankings", {})
    cuts = piece.__dict__.setdefault("_cuts", {})
    for stage_index, stage in enumerate(stages):
        ranking_key = (stage_index, thin_limit)
        if ranking_key in rankings:
            ranked = rankings[ranking_key]
        else:
            planes = stage()
            ranked = None if planes is None else _rank_planes(
                piece, *planes, thin_limit
            )
            rankings[ranking_key] = ranked
        if ranked is None:
            continue
        options = []
        for position in range(skip, min(skip + MAX_CUT_ATTEMPTS, len(ranked))):
            cut_key = (stage_index, position)
            if cut_key in cuts:
                children = cuts[cut_key]
            else:
                normal, offset = ranked[position]
                result = _cut_piece(piece, normal * offset, normal)
                children = None if result is None else result[0] + result[1]
                cuts[cut_key] = children
            if children is None:
                continue
            if len(children) >= 2 or (
                len(children) == 1
                and children[0].concave_edges < piece.concave_edges
            ):
                remaining = sum(child.concave_edges for child in children)
                slivers = sum(
                    1 for child in children
                    if _cached_extent(child) < thin_limit
                )
                options.append((slivers, remaining, len(options), children, cut_key))
                if slivers == 0 and remaining == 0:
                    # Children convex and none of them thin. The ranking key
                    # is (slivers, remaining, order), so nothing tried later
                    # can beat this and the selection below would pick it
                    # anyway; stopping here only skips work.
                    break
                if len(options) >= CUT_LOOKAHEAD:
                    break
        if options:
            best = min(options, key=lambda item: item[:3])
            return best[4], best[3]
    return None, None


def _cached_extent(piece):
    extent = piece.__dict__.get("_last_extent")
    if extent is None:
        extent = float(_piece_extents(piece)[-1])
        piece._last_extent = extent
    return extent


# --------------------------------------------------------------------------
# Convex post-processing
# --------------------------------------------------------------------------

def _hull_piece(vertices, faces=None, depth=0):
    hull = _convex_hull(vertices)
    if hull is None:
        return None
    return _analyse_piece(Piece(vertices=hull[0], faces=hull[1], depth=depth))


def _convex_planes(piece):
    """Return outward support planes bounding the convex hull of a piece.

    Every plane is placed at the furthest vertex along its normal, so it
    supports the piece by construction. Dropping planes that a sliver normal
    makes look non-supporting would open the half-space set and report
    neighbours as intersecting when they only touch.
    """
    vertices = np.asarray(piece.vertices, dtype=np.float64)
    faces = np.asarray(piece.faces, dtype=np.int64)
    if len(faces) == 0 or len(vertices) == 0:
        return []
    normals, areas = _face_normals(vertices, faces)
    order = np.argsort(-areas)
    order = order[areas[order] > 1.0e-12]
    if len(order) == 0:
        return []
    kept = normals[order]
    # One projection of every vertex against every candidate normal replaces
    # a projection per face; this runs for each overlap test, tens of
    # thousands of times per decomposition.
    projection = vertices @ kept.T
    support = projection.argmax(axis=0)
    keys = np.column_stack((
        np.round(kept * 1.0e4).astype(np.int64),
        np.round(projection[support, np.arange(len(order))] * 1.0e4).astype(np.int64),
    ))
    # First occurrence wins, in descending area order, as the dict did.
    _unique, first = np.unique(keys, axis=0, return_index=True)
    first = np.sort(first)
    return [
        (vertices[support[index]], kept[index]) for index in first.tolist()
    ]


def _plane_arrays(planes):
    """Pack (point, normal) planes into normal and offset arrays."""
    if not planes:
        return np.empty((0, 3)), np.empty(0)
    normals = np.asarray([normal for _point, normal in planes])
    offsets = np.asarray([float(normal @ point) for point, normal in planes])
    return normals, offsets


def _points_inside_convex(points, planes, epsilon=1.0e-7):
    """Boolean mask of points behind every plane."""
    normals, offsets = _plane_arrays(planes)
    if len(normals) == 0:
        return np.zeros(len(points), dtype=bool)
    return np.all(np.asarray(points) @ normals.T - offsets <= epsilon, axis=1)


def _point_inside_convex(point, planes, epsilon=1.0e-7):
    return bool(_points_inside_convex(np.asarray(point)[None, :], planes, epsilon)[0])


def _convex_test_samples(piece):
    vertices = piece.vertices
    faces = np.asarray(piece.faces, dtype=np.int64)
    centroids = vertices[faces].mean(axis=1)
    # The samples are only ever asked whether any of them is inside another
    # hull, so their order carries no meaning and the edge set can be built
    # with numpy instead of a Python set over every triangle.
    pairs = np.sort(
        np.concatenate((faces[:, :2], faces[:, 1:], faces[:, ::2]), axis=0),
        axis=1,
    )
    pairs = np.unique(pairs, axis=0)
    left = vertices[pairs[:, 0]]
    right = vertices[pairs[:, 1]]
    edge_samples = np.concatenate(
        [left * (1.0 - fraction) + right * fraction
         for fraction in (0.25, 0.5, 0.75)],
        axis=0,
    )
    return np.vstack((vertices, edge_samples, centroids))


def _pieces_overlap(subject, cutter, clearance=1.0e-6):
    """Return true only for positive-volume overlap, not shared boundaries."""
    if len(subject.vertices) == 0 or len(cutter.vertices) == 0:
        return False
    s_min = subject.vertices.min(axis=0)
    s_max = subject.vertices.max(axis=0)
    c_min = cutter.vertices.min(axis=0)
    c_max = cutter.vertices.max(axis=0)
    if np.any(s_max < c_min + clearance) or np.any(c_max < s_min + clearance):
        return False
    return bool(
        _points_inside_convex(
            _convex_test_samples(subject), _convex_planes(cutter), -clearance
        ).any()
        or _points_inside_convex(
            _convex_test_samples(cutter), _convex_planes(subject), -clearance
        ).any()
    )


def _subtract_convex_fragments(subject, cutter):
    """Keep every convex fragment of subject outside cutter."""
    if not _pieces_overlap(subject, cutter):
        return [subject]
    inside = [subject]
    outside = []
    for plane_point, plane_normal in _convex_planes(cutter):
        next_inside = []
        for fragment in inside:
            result = _cut_piece(fragment, plane_point, plane_normal)
            if result is None:
                distances = fragment.vertices @ plane_normal - float(
                    plane_normal @ plane_point
                )
                if float(distances.min()) >= -PLANE_EPSILON:
                    outside.append(fragment)
                else:
                    next_inside.append(fragment)
                continue
            negative, positive = result
            outside.extend(positive)
            next_inside.extend(negative)
        inside = next_inside
        if not inside:
            break
    return outside


def _bridge_is_shallow(hull, parts, source_bvh, origin, limit):
    """True when a hull only adds shallow empty space over the given parts.

    Every hull sample outside all parts must sit within the limit above one
    of them and in empty space, so the hull can never fill an opening or
    cover the material of another part.
    """
    samples, sample_normals = _surface_samples(*hull)
    inside = np.zeros(len(samples), dtype=bool)
    bvhs = []
    for part in parts:
        normals, offsets = _plane_arrays(_convex_planes(part))
        if len(normals):
            inside |= np.all(samples @ normals.T - offsets <= REFLEX_EPSILON, axis=1)
        bvhs.append(_surface_bvh(part.vertices - origin, part.faces))
    for point, normal in zip(samples[~inside] - origin, sample_normals[~inside]):
        local = Vector(tuple(point))
        direction = Vector(tuple(-normal))
        depth = min(
            (
                float(hit[3])
                for hit in (bvh.ray_cast(local, direction, limit) for bvh in bvhs)
                if hit[0] is not None
            ),
            default=math.inf,
        )
        if depth == math.inf:
            # The ray grazes along a seam; fall back to the nearest surface.
            depth = min(
                (
                    float(hit[3])
                    for hit in (bvh.find_nearest(local) for bvh in bvhs)
                    if hit[0] is not None
                ),
                default=math.inf,
            )
        if depth > limit:
            return False
        if depth > PLANE_EPSILON and _inside_source(source_bvh, point):
            return False
    return True


def _absorb_thin_pieces(pieces, thin_limit, tolerance, source_bvh, origin):
    """Fold parts thinner than the threshold into a neighbouring part.

    A sliver left between two parts cannot merge on its own: its hull would
    reach over the part on the far side. Clipping that hull along the plane
    that already separates the two keeps the union convex, swallows the
    sliver and touches nothing else.
    """
    pieces = [piece for piece in pieces]
    for _round in range(3):
        widths = [_minimum_width(piece) for piece in pieces]
        order = sorted(
            (index for index, width in enumerate(widths) if width < thin_limit),
            key=lambda index: pieces[index].volume,
        )
        if not order:
            break
        absorbed = []
        for index in order:
            thin = pieces[index]
            if thin is None:
                continue
            low = thin.vertices.min(axis=0) - thin_limit
            high = thin.vertices.max(axis=0) + thin_limit
            neighbours = [
                other for other, piece in enumerate(pieces)
                if piece is not None and other != index
                and np.all(piece.vertices.min(axis=0) <= high)
                and np.all(piece.vertices.max(axis=0) >= low)
            ]
            neighbours.sort(key=lambda other: -pieces[other].volume)
            for other in neighbours:
                host = pieces[other]
                hull = _convex_hull(
                    np.vstack((thin.vertices, host.vertices)), simplify=False
                )
                if hull is None or not _bridge_is_shallow(
                    hull, (thin, host), source_bvh, origin, thin_limit
                ):
                    continue
                merged = _hull_piece(np.vstack((thin.vertices, host.vertices)))
                if merged is None or not merged.closed:
                    continue
                merged = _clip_against_others(
                    merged, pieces, {index, other}, (thin, host), tolerance
                )
                if merged is None:
                    continue
                pieces[other] = merged
                pieces[index] = None
                absorbed.append(index)
                break
        if not absorbed:
            break
        pieces = [piece for piece in pieces if piece is not None]
    return [piece for piece in pieces if piece is not None]


def _clip_against_others(merged, pieces, skip, keep, tolerance):
    """Clip a merged hull off every other piece it would cover.

    Returns the clipped hull, or None when clipping would eat into the two
    pieces it is supposed to contain.
    """
    for index, other in enumerate(pieces):
        if other is None or index in skip:
            continue
        if not _pieces_overlap(merged, other, clearance=1.0e-6):
            continue
        separated = False
        for point, normal in _convex_planes(other):
            offset = float(normal @ point)
            if all(
                float((piece.vertices @ normal).max()) <= offset + PLANE_EPSILON
                for piece in keep
            ):
                result = _cut_piece(merged, normal * offset, normal)
                if result is None:
                    continue
                negative, _positive = result
                if not negative:
                    continue
                clipped = _hull_piece(
                    np.vstack([piece.vertices for piece in negative])
                )
                if clipped is None or not clipped.closed:
                    continue
                merged = clipped
                separated = True
                break
        if not separated:
            return None
    # the clipped hull must still hold both pieces
    planes = _convex_planes(merged)
    normals, offsets = _plane_arrays(planes)
    if len(normals) == 0:
        return None
    for piece in keep:
        if float((piece.vertices @ normals.T - offsets).max()) > tolerance:
            return None
    # and it must not overlap anything else after the clipping
    for index, other in enumerate(pieces):
        if other is None or index in skip:
            continue
        if _pieces_overlap(merged, other, clearance=1.0e-6):
            return None
    return merged


def _separate_touching_pieces(pieces, limit=REFLEX_EPSILON, max_rounds=4):
    """Remove sub-millimetre interpenetration between convex neighbours.

    Tolerant hulls, the on-plane band and float32 rounding can leave two
    neighbours sharing a sliver thinner than a cut can resolve. The smaller
    piece of such a pair is inset by the measured penetration, which keeps
    the deviation far below the gap while clearing the intersection.
    """
    pieces = list(pieces)
    for _round in range(max_rounds):
        bounds = [
            (piece.vertices.min(axis=0), piece.vertices.max(axis=0))
            for piece in pieces
        ]
        changed = False
        for left in range(len(pieces)):
            for right in range(left + 1, len(pieces)):
                (a_low, a_high), (b_low, b_high) = bounds[left], bounds[right]
                if np.any(a_high < b_low) or np.any(b_high < a_low):
                    continue
                a, b = pieces[left], pieces[right]
                if a is None or b is None:
                    continue
                if not _pieces_overlap(a, b, clearance=1.0e-7):
                    continue
                penetration = 0.0
                for owner, other in ((a, b), (b, a)):
                    planes = _convex_planes(owner)
                    if not planes:
                        continue
                    normals = np.asarray([normal for _point, normal in planes])
                    offsets = np.asarray(
                        [float(normal @ point) for point, normal in planes]
                    )
                    depth = (
                        offsets[None, :] - _convex_test_samples(other) @ normals.T
                    ).min(axis=1)
                    penetration = max(penetration, float(depth.max()))
                if penetration <= 0.0 or penetration > limit:
                    continue
                index = left if a.volume <= b.volume else right
                trimmed = _inset_convex_piece(pieces[index], penetration + 1.0e-5)
                if len(trimmed.faces) == 0:
                    continue
                pieces[index] = trimmed
                bounds[index] = (
                    trimmed.vertices.min(axis=0),
                    trimmed.vertices.max(axis=0),
                )
                changed = True
        if not changed:
            break
    return pieces


def _resolve_component_overlaps(pieces, limit=LEAF_LIMIT):
    """Build a non-overlapping union when source components interpenetrate."""
    accepted = []
    for piece in sorted(pieces, key=_piece_sort_key):
        fragments = [piece]
        for cutter in accepted:
            next_fragments = []
            for fragment in fragments:
                next_fragments.extend(_subtract_convex_fragments(fragment, cutter))
            fragments = next_fragments
            if not fragments:
                break
        accepted.extend(fragments)
        if len(accepted) > limit:
            return accepted, False
    return accepted, True


def _piece_sort_key(piece):
    center = piece.vertices.mean(axis=0)
    return (-piece.volume, float(center[0]), float(center[1]), float(center[2]))


def _merge_convex_neighbours(pieces):
    """Greedily join touching convex pieces whose union is exactly convex."""
    pieces = {index: piece for index, piece in enumerate(pieces)}
    bounds = {
        index: (piece.vertices.min(axis=0), piece.vertices.max(axis=0))
        for index, piece in pieces.items()
    }
    next_index = len(pieces)
    proximity = PLANE_EPSILON * 4.0
    candidates = {}

    planes = {}

    def support(index):
        if index not in planes:
            found = _convex_planes(pieces[index])
            planes[index] = (
                np.asarray([normal for _point, normal in found]).reshape((-1, 3)),
                np.asarray([float(normal @ point) for point, normal in found]),
            )
        return planes[index]

    def share_contact_plane(left, right):
        normals, offsets = support(left)
        if len(normals) == 0:
            return True
        distance = pieces[right].vertices @ normals.T - offsets
        behind = distance.min(axis=0) >= -REFLEX_EPSILON
        touching = np.abs(distance).min(axis=0) <= REFLEX_EPSILON
        return bool(np.any(behind & touching))

    def evaluate(left, right):
        if not (
            share_contact_plane(left, right) or share_contact_plane(right, left)
        ):
            return
        a = pieces[left]
        b = pieces[right]
        hull = _convex_hull(np.vstack((a.vertices, b.vertices)), simplify=False)
        if hull is None:
            return
        hull_volume = _signed_volume(*hull)
        smaller_area = min(
            float(_face_normals(a.vertices, a.faces)[1].sum()),
            float(_face_normals(b.vertices, b.faces)[1].sum()),
        )
        added = hull_volume - a.volume - b.volume
        if added > MERGE_FILM_EPSILON * smaller_area + VOLUME_ABSOLUTE_EPSILON:
            return
        # Every part of the merged hull surface must belong to one of the two
        # pieces; otherwise the hull bridges empty space between them.
        hull_vertices, hull_faces = hull
        corners = hull_vertices[hull_faces]
        centers = corners.mean(axis=1)
        samples = np.vstack((centers, ((corners + centers[:, None, :]) * 0.5).reshape(-1, 3)))
        inside = np.zeros(len(samples), dtype=bool)
        for index in (left, right):
            normals, offsets = support(index)
            if len(normals) == 0:
                continue
            inside |= np.all(samples @ normals.T - offsets <= REFLEX_EPSILON, axis=1)
        if np.all(inside):
            candidates[(left, right)] = hull_volume

    def touching(index):
        low, high = bounds[index]
        return [
            other
            for other, (other_low, other_high) in bounds.items()
            if other != index
            and np.all(low <= other_high + proximity)
            and np.all(other_low <= high + proximity)
        ]

    for index in list(pieces):
        for other in touching(index):
            if other > index:
                evaluate(index, other)

    while candidates:
        # Merge the largest resulting hull first; ties resolve by index.
        (left, right), _volume = max(
            candidates.items(), key=lambda item: (item[1], -item[0][0], -item[0][1])
        )
        merged = _hull_piece(
            np.vstack((pieces[left].vertices, pieces[right].vertices))
        )
        for key in [key for key in candidates if left in key or right in key]:
            del candidates[key]
        if merged is None:
            continue
        del pieces[left], pieces[right], bounds[left], bounds[right]
        planes.pop(left, None)
        planes.pop(right, None)
        pieces[next_index] = merged
        bounds[next_index] = (merged.vertices.min(axis=0), merged.vertices.max(axis=0))
        for other in touching(next_index):
            evaluate(other, next_index)
        next_index += 1
    return [pieces[index] for index in sorted(pieces)]


def _surface_samples(vertices, faces, divisions=4):
    """Grid samples on every triangle with the triangle's outward normal."""
    corners = vertices[faces]
    normals, areas = _face_normals(vertices, faces)
    keep = areas > 1.0e-8
    corners = corners[keep]
    normals = normals[keep]
    samples = []
    sample_normals = []
    for a in range(divisions + 1):
        for b in range(divisions + 1 - a):
            wa = a / divisions
            wb = b / divisions
            samples.append(
                corners[:, 0] * (1.0 - wa - wb)
                + corners[:, 1] * wa
                + corners[:, 2] * wb
            )
            sample_normals.append(normals)
    return np.vstack(samples), np.vstack(sample_normals)


def _merge_within_tolerance(pieces, source_bvh, origin, tolerance, thin_limit=0.0):
    """Join neighbouring convex pieces whose hull only adds shallow empty space.

    Exact cuts leave pieces that were separated by planes needed elsewhere.
    Two pieces merge into their hull when every part of the hull outside both
    pieces lies in empty space outside the source and no deeper than the
    feature tolerance, so the hull never covers another piece or an opening.
    A slab thinner than the thin-part threshold is an artefact of a cut along
    a nearly parallel plane, not a feature, so absorbing it may bridge up to
    that threshold. Cheapest merges run first.
    """
    pieces = dict(enumerate(pieces))
    cache = {}

    def details(index):
        if index not in cache:
            piece = pieces[index]
            planes = _convex_planes(piece)
            cache[index] = (
                np.asarray([normal for _point, normal in planes]).reshape((-1, 3)),
                np.asarray([float(normal @ point) for point, normal in planes]),
                float(_face_normals(piece.vertices, piece.faces)[1].sum()),
                _surface_bvh(piece.vertices - origin, piece.faces),
                piece.vertices.min(axis=0),
                piece.vertices.max(axis=0),
            )
        return cache[index]

    def neighbours(index):
        low, high = details(index)[4:6]
        return [
            other
            for other in pieces
            if other != index
            and np.all(low <= details(other)[5] + tolerance)
            and np.all(details(other)[4] <= high + tolerance)
        ]

    def cost(left, right):
        left_normals, left_offsets, left_area, left_bvh = details(left)[:4]
        right_normals, right_offsets, right_area, right_bvh = details(right)[:4]
        a = pieces[left]
        b = pieces[right]
        depth_limit = tolerance
        if thin_limit > tolerance and min(
            _minimum_width(a), _minimum_width(b)
        ) < thin_limit:
            depth_limit = thin_limit
        # Only pieces that actually touch may be bridged: an air gap between
        # separate objects must survive as a gap.
        contact = min(
            (
                float(hit[3])
                for hit in (
                    right_bvh.find_nearest(Vector(tuple(point - origin)))
                    for point in a.vertices
                )
                if hit[0] is not None
            ),
            default=math.inf,
        )
        if contact > PLANE_EPSILON * 10.0:
            return None
        hull = _convex_hull(np.vstack((a.vertices, b.vertices)), simplify=False)
        if hull is None:
            return None
        added = _signed_volume(*hull) - a.volume - b.volume
        if added > tolerance * min(left_area, right_area) + VOLUME_ABSOLUTE_EPSILON:
            return None
        samples, sample_normals = _surface_samples(*hull)
        inside = np.zeros(len(samples), dtype=bool)
        for normals, offsets in (
            (left_normals, left_offsets),
            (right_normals, right_offsets),
        ):
            if len(normals):
                inside |= np.all(samples @ normals.T - offsets <= REFLEX_EPSILON, axis=1)
        for point, normal in zip(samples[~inside] - origin, sample_normals[~inside]):
            local = Vector(tuple(point))
            # Depth of the bridged space measured into the hull: a shallow
            # step is hit within the tolerance, a slot or an air gap is not.
            direction = Vector(tuple(-normal))
            depth = min(
                (
                    float(hit[3])
                    for hit in (
                        left_bvh.ray_cast(local, direction, depth_limit),
                        right_bvh.ray_cast(local, direction, depth_limit),
                    )
                    if hit[0] is not None
                ),
                default=math.inf,
            )
            if depth == math.inf:
                # The ray grazes along the seam of two pieces of one wall;
                # fall back to the distance to the nearest of the two.
                depth = min(
                    (
                        float(hit[3])
                        for hit in (
                            left_bvh.find_nearest(local),
                            right_bvh.find_nearest(local),
                        )
                        if hit[0] is not None
                    ),
                    default=math.inf,
                )
            if depth > depth_limit:
                return None
            if depth > PLANE_EPSILON and _inside_source(source_bvh, point):
                # The hull would cover material of another piece.
                return None
        return added

    queue = []
    for index in list(pieces):
        for other in neighbours(index):
            if other > index:
                value = cost(index, other)
                if value is not None:
                    queue.append((value, index, other))
    next_index = len(pieces)
    while queue:
        queue.sort(key=lambda item: item[0])
        value, left, right = queue.pop(0)
        if left not in pieces or right not in pieces:
            continue
        merged = _hull_piece(
            np.vstack((pieces[left].vertices, pieces[right].vertices))
        )
        if merged is None or not merged.closed:
            continue
        for index in (left, right):
            del pieces[index]
            cache.pop(index, None)
        queue = [item for item in queue if left not in item[1:] and right not in item[1:]]
        pieces[next_index] = merged
        for other in neighbours(next_index):
            value = cost(other, next_index)
            if value is not None:
                queue.append((value, other, next_index))
        next_index += 1
    return [pieces[index] for index in sorted(pieces)]


def _minimum_width(piece):
    planes = _convex_planes(piece)
    if not planes or len(piece.vertices) < 4:
        return 0.0
    widths = [
        float(np.ptp(piece.vertices @ normal)) for _point, normal in planes
    ]
    return min(widths)


def _hull_width(piece):
    """Minimum width of the convex hull of any piece."""
    hull = _convex_hull(piece.vertices, simplify=False)
    if hull is None:
        return 0.0
    return _minimum_width(Piece(vertices=hull[0], faces=hull[1]))


def _principal_extents(points):
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


def _piece_extents(piece):
    return _principal_extents(piece.vertices)


def _is_ignorable_fragment(piece, settings):
    extents = _piece_extents(piece)
    collapse_epsilon = max(1.0e-6, float(settings.gap) * 0.5 + 1.0e-7)
    return float(extents[-1]) <= collapse_epsilon


def _reliable_hull_planes(vertices, faces):
    """Group hull triangles by plane into area-weighted support planes."""
    normals, areas = _face_normals(vertices, faces)
    groups = []
    for face in np.argsort(-areas):
        if areas[face] <= 1.0e-12:
            break
        normal = normals[face]
        offset = float(normal @ vertices[faces[face, 0]])
        for group in groups:
            if float(group[0] @ normal) > 1.0 - 1.0e-7 and abs(group[1] - offset) <= 1.0e-5:
                group[2] += normal * areas[face]
                group[3].append(face)
                break
        else:
            groups.append([normal, offset, normal * areas[face], [face]])
    planes = []
    incident = [[] for _ in range(len(vertices))]
    for weighted_normal, _offset, weighted, members in groups:
        length = float(np.linalg.norm(weighted))
        if length <= 1.0e-9:
            continue
        unit = weighted / length
        corners = np.unique(faces[members].reshape(-1))
        # The highest projection makes every plane a support plane by
        # definition, so shifting it inward can never grow the hull.
        offset = float((vertices @ unit).max())
        index = len(planes)
        planes.append((unit, offset))
        for corner in corners:
            incident[int(corner)].append(index)
    return planes, incident


def _polytope_piece(vertices, depth=0):
    """Exact convex polytope of the points, with planar polygon faces.

    The piece keeps fan triangles in ``faces`` for everything that measures
    it, and the polygons themselves in ``polygons`` for the output mesh.
    """
    result = hull64.planar_polytope(vertices, OUTPUT_MERGE_DISTANCE)
    if result is None:
        return None
    points, polygons = result
    faces = hull64.fan_triangles(polygons).astype(np.int32)
    piece = Piece(vertices=points, faces=faces, depth=depth)
    piece.polygons = polygons
    piece.closed = True
    piece.volume = abs(_signed_volume(points, faces))
    return piece


def _polytope_planes(piece):
    """Outward unit normals and supporting offsets of a polytope's polygons."""
    vertices = piece.vertices
    normals = []
    for polygon in piece.polygons:
        points = vertices[polygon]
        following = np.roll(points, -1, axis=0)
        normal = np.array([
            np.sum((points[:, 1] - following[:, 1]) * (points[:, 2] + following[:, 2])),
            np.sum((points[:, 2] - following[:, 2]) * (points[:, 0] + following[:, 0])),
            np.sum((points[:, 0] - following[:, 0]) * (points[:, 1] + following[:, 1])),
        ])
        length = float(np.linalg.norm(normal))
        if length > 0.0:
            normals.append(normal / length)
    normals = np.asarray(normals).reshape((-1, 3))
    # Supporting offsets: every vertex lies behind every plane by construction.
    offsets = (vertices @ normals.T).max(axis=0) if len(normals) else np.empty(0)
    return normals, offsets


def _inset_polytope(piece, distance):
    """Shift every face plane of a convex polytope inward by ``distance``.

    The result is the intersection of the shifted half-spaces, rebuilt from
    their exact vertices. Every point of it lies at least ``distance`` inside
    the original, so two pieces that did not overlap end at least twice that
    distance apart - the gap SINTEZ AGR Checker measures is then guaranteed
    rather than approximately reached. Returns an empty piece when the shift
    swallows the whole polytope.
    """
    empty = Piece(
        vertices=np.empty((0, 3), dtype=np.float64),
        faces=np.empty((0, 3), dtype=np.int32),
        depth=piece.depth,
    )
    if distance <= 0.0:
        return piece
    if getattr(piece, "polygons", None) is None:
        rebuilt = _polytope_piece(piece.vertices, piece.depth)
        if rebuilt is None:
            return empty
        piece = rebuilt
    normals, offsets = _polytope_planes(piece)
    if len(normals) < 4:
        return empty
    shifted = offsets - distance
    scale = max(float(np.abs(piece.vertices).max()), 1.0)
    count = len(normals)
    if count <= INSET_GLOBAL_PLANES:
        triples = np.asarray(list(itertools.combinations(range(count), 3)), dtype=np.int64)
    else:
        # Only planes near each other can meet in a vertex of the inset:
        # those of the polygons around a vertex and of their neighbours.
        vertex_polygons = {}
        for index, polygon in enumerate(piece.polygons):
            for vertex in polygon:
                vertex_polygons.setdefault(vertex, set()).add(index)
        neighbourhood = {}
        for vertex, owners in vertex_polygons.items():
            ring = set(owners)
            for owner in owners:
                for corner in piece.polygons[owner]:
                    ring |= vertex_polygons[corner]
            neighbourhood[vertex] = sorted(ring)
        rows = set()
        for ring in neighbourhood.values():
            rows.update(itertools.combinations(ring, 3))
        triples = np.asarray(sorted(rows), dtype=np.int64).reshape((-1, 3))
    if len(triples) == 0:
        return empty
    matrices = normals[triples]
    determinants = np.linalg.det(matrices)
    usable = np.abs(determinants) > 1.0e-10
    if not usable.any():
        return empty
    points = np.linalg.solve(matrices[usable], shifted[triples[usable]][..., None])[..., 0]
    inside = np.all(points @ normals.T - shifted <= scale * 1.0e-12, axis=1)
    points = points[inside]
    if len(points) < 4:
        return empty
    rebuilt = _polytope_piece(points, piece.depth)
    return rebuilt if rebuilt is not None else empty


def _polytope_edges(piece):
    pairs = {
        (min(polygon[k], polygon[(k + 1) % len(polygon)]),
         max(polygon[k], polygon[(k + 1) % len(polygon)]))
        for polygon in piece.polygons
        for k in range(len(polygon))
    }
    return np.asarray(sorted(pairs), dtype=np.int64).reshape((-1, 2))


def _clip_polytope(piece, normal, offset):
    """Keep the part of a convex polytope with ``normal . x <= offset``.

    The clipped polytope's corners are the kept corners plus the points where
    edges cross the plane, so the result is exact.
    """
    empty = Piece(
        vertices=np.empty((0, 3), dtype=np.float64),
        faces=np.empty((0, 3), dtype=np.int32),
        depth=piece.depth,
    )
    vertices = piece.vertices
    distance = vertices @ normal - offset
    if bool((distance <= 0.0).all()):
        return piece
    if bool((distance >= 0.0).all()):
        return empty
    edges = _polytope_edges(piece)
    start, end = distance[edges[:, 0]], distance[edges[:, 1]]
    crossing = (start < 0.0) != (end < 0.0)
    a, b = edges[crossing, 0], edges[crossing, 1]
    fraction = (distance[a] / (distance[a] - distance[b]))[:, None]
    points = np.vstack((vertices[distance <= 0.0], vertices[a] + (vertices[b] - vertices[a]) * fraction))
    rebuilt = _polytope_piece(points, piece.depth) if len(points) >= 4 else None
    return rebuilt if rebuilt is not None else empty


def _vertex_surface_gap(first, second):
    """Smallest distance from a corner of either polytope to the other one.

    SINTEZ AGR Checker measures the gap exactly this way - from each vertex
    to the nearest point of the other hull - so this is what has to clear
    the gap setting.
    """
    gaps = []
    for points, other in ((first.vertices, second), (second.vertices, first)):
        triangles = other.faces
        gaps.append(float(hull64.distance_to_triangles(
            points,
            other.vertices[triangles[:, 0]],
            other.vertices[triangles[:, 1]],
            other.vertices[triangles[:, 2]],
        ).min()))
    return min(gaps)


def _overlapping(first, second):
    """True when two convex polytopes share interior (separating axis test)."""
    axes = [_polytope_planes(first)[0], _polytope_planes(second)[0]]
    edges_first = _polytope_edges(first)
    edges_second = _polytope_edges(second)
    if len(edges_first) and len(edges_second):
        u = first.vertices[edges_first[:, 1]] - first.vertices[edges_first[:, 0]]
        v = second.vertices[edges_second[:, 1]] - second.vertices[edges_second[:, 0]]
        crosses = np.cross(u[:, None, :], v[None, :, :]).reshape((-1, 3))
        lengths = np.linalg.norm(crosses, axis=1)
        keep = lengths > 1.0e-12
        axes.append(crosses[keep] / lengths[keep, None])
    axes = np.vstack([block for block in axes if len(block)])
    first_projection = first.vertices @ axes.T
    second_projection = second.vertices @ axes.T
    separation = np.maximum(
        second_projection.min(axis=0) - first_projection.max(axis=0),
        first_projection.min(axis=0) - second_projection.max(axis=0),
    )
    return bool(separation.max() <= 0.0)


def _separate_polytopes(pieces, gap):
    """Make every pair of polytopes clear ``gap`` the way the checker measures.

    The exact inset already guarantees the gap between pieces that did not
    overlap before it; pieces that did are resolved here. The smaller piece
    of such a pair is clipped by a plane ``gap`` away from the larger one,
    along the axis that needs the thinnest slice. Clipping only ever shrinks
    a piece, so resolving one pair cannot break another, and one pass over
    the pairs is enough.
    """
    pieces = list(pieces)
    lower = [piece.vertices.min(axis=0) for piece in pieces]
    upper = [piece.vertices.max(axis=0) for piece in pieces]
    for index in range(len(pieces)):
        for other in range(index + 1, len(pieces)):
            first, second = pieces[index], pieces[other]
            if len(first.faces) == 0 or len(second.faces) == 0:
                continue
            if np.any(upper[index] + gap < lower[other]) or np.any(upper[other] + gap < lower[index]):
                continue
            if not _overlapping(first, second) and _vertex_surface_gap(first, second) >= gap:
                continue
            small, large = (index, other) if abs(first.volume) <= abs(second.volume) else (other, index)
            clipped = pieces[small]
            fixed = pieces[large]
            direction = fixed.vertices.mean(axis=0) - clipped.vertices.mean(axis=0)
            axes = [_polytope_planes(clipped)[0], _polytope_planes(fixed)[0]]
            if float(np.linalg.norm(direction)) > 0.0:
                axes.append((direction / np.linalg.norm(direction))[None, :])
            axes = np.vstack([block for block in axes if len(block)])
            # Orient every axis from the clipped piece towards the fixed one.
            axes = axes * np.where(axes @ direction < 0.0, -1.0, 1.0)[:, None]
            limit = (fixed.vertices @ axes.T).min(axis=0) - gap
            slice_depth = (clipped.vertices @ axes.T).max(axis=0) - limit
            best = int(np.argmin(slice_depth))
            pieces[small] = _clip_polytope(clipped, axes[best], float(limit[best]))
            if len(pieces[small].faces):
                lower[small] = pieces[small].vertices.min(axis=0)
                upper[small] = pieces[small].vertices.max(axis=0)
    return pieces


def _inset_convex_piece(piece, distance):
    """Move every support plane of a convex piece inward by one distance.

    Planes come from area-weighted groups of coplanar hull triangles, so a
    sliver triangle cannot contribute a wrong direction. Every vertex takes a
    damped least-squares step against its incident planes and is then
    projected behind all shifted planes, which guarantees the inset hull lies
    inside the original by at least the requested distance.
    """
    empty = Piece(
        vertices=np.empty((0, 3), dtype=np.float64),
        faces=np.empty((0, 3), dtype=np.int32),
        depth=piece.depth,
    )
    if distance <= 0.0:
        return piece
    hull = _convex_hull(piece.vertices, simplify=False)
    if hull is None:
        return empty
    vertices, faces = hull
    planes, incident = _reliable_hull_planes(vertices, faces)
    if len(planes) < 4:
        return empty
    plane_normals = np.asarray([normal for normal, _offset in planes])
    plane_offsets = np.asarray([offset for _normal, offset in planes]) - distance
    moved = vertices.copy()
    for index, members in enumerate(incident):
        if not members:
            continue
        matrix = plane_normals[members]
        system = matrix.T @ matrix + np.eye(3) * 1.0e-6
        step = np.linalg.solve(system, matrix.T @ np.full(len(members), -distance))
        step_length = float(np.linalg.norm(step))
        if step_length > distance * 20.0:
            step *= distance * 20.0 / step_length
        moved[index] = vertices[index] + step
    for _pass in range(16):
        worst = 0.0
        for normal, offset in zip(plane_normals, plane_offsets):
            excess = moved @ normal - offset
            outside = excess > 0.0
            if outside.any():
                moved[outside] -= np.outer(excess[outside], normal)
                worst = max(worst, float(excess[outside].max()))
        if worst <= 1.0e-9:
            break
    # Alternating projections converge slowly between nearly parallel planes.
    # Pull any remaining vertex towards the centre until it is inside.
    center = vertices.mean(axis=0)
    center_margin = plane_offsets - plane_normals @ center
    if np.any(center_margin <= 0.0):
        return empty
    excess = moved @ plane_normals.T - plane_offsets
    for index in np.flatnonzero((excess > 0.0).any(axis=1)):
        direction = moved[index] - center
        along = plane_normals @ direction
        limits = np.where(along > 1.0e-15, center_margin / np.maximum(along, 1.0e-15), np.inf)
        moved[index] = center + direction * min(1.0, float(limits.min()) * (1.0 - 1.0e-9))
    inset = _hull_piece(moved, depth=piece.depth)
    return inset if inset is not None else empty


# --------------------------------------------------------------------------
# Safety invariants against the source
# --------------------------------------------------------------------------

_RAY_DIRECTIONS = (
    Vector((0.5773, 0.5774, 0.5775)).normalized(),
    Vector((-0.3119, 0.7207, -0.6193)).normalized(),
    Vector((0.8329, -0.4105, 0.3710)).normalized(),
)


def _inside_source(bvh, point):
    """Signed ray-crossing count; robust for overlapping closed shells."""
    votes = 0
    for direction in _RAY_DIRECTIONS:
        origin = Vector(tuple(point))
        winding = 0
        for _guard in range(256):
            hit = bvh.ray_cast(origin, direction)
            if hit[0] is None:
                break
            winding += 1 if hit[1].dot(direction) > 0.0 else -1
            # BVH coordinates are float32: step clearly past every hit.
            origin = hit[0] + direction * 1.0e-3
        votes += 1 if winding > 0 else 0
    return votes >= 2


def _winding_numbers(vertices, faces, points, chunk=256):
    """Generalized winding number of points with respect to a closed mesh."""
    a = vertices[faces[:, 0]]
    b = vertices[faces[:, 1]]
    c = vertices[faces[:, 2]]
    result = np.zeros(len(points))
    for start in range(0, len(points), chunk):
        query = points[start:start + chunk][:, None, :]
        x = a[None] - query
        y = b[None] - query
        z = c[None] - query
        lx = np.sqrt((x * x).sum(axis=2))
        ly = np.sqrt((y * y).sum(axis=2))
        lz = np.sqrt((z * z).sum(axis=2))
        determinant = (x * np.cross(y, z)).sum(axis=2)
        denominator = (
            lx * ly * lz
            + (x * y).sum(axis=2) * lz
            + (y * z).sum(axis=2) * lx
            + (z * x).sum(axis=2) * ly
        )
        result[start:start + chunk] = (
            np.arctan2(determinant, denominator).sum(axis=1) / (2.0 * math.pi)
        )
    return result


def _source_excess(source_vertices, source_faces, pieces, probe=5.0e-3, tolerance=0.0):
    """Return hulls whose facets lie outside the source volume.

    Facet samples are moved inward along the facet normal by more than the
    convexity tolerance, so a facet on the source surface counts as inside
    while a hull bridging a door, niche or arch has samples in empty space.
    """
    origin = source_vertices.mean(axis=0)
    bvh = _surface_bvh(source_vertices - origin, source_faces)
    offending = []
    weights = [
        (a, b)
        for a in range(4)
        for b in range(4 - a)
    ]
    for index, piece in enumerate(pieces):
        vertices = piece.vertices
        faces = piece.faces
        normals, areas = _face_normals(vertices, faces)
        corners = vertices[faces]
        longest = np.max(
            np.stack((
                np.linalg.norm(corners[:, 1] - corners[:, 0], axis=1),
                np.linalg.norm(corners[:, 2] - corners[:, 1], axis=1),
                np.linalg.norm(corners[:, 0] - corners[:, 2], axis=1),
            )),
            axis=0,
        )
        reliable = 2.0 * areas / np.maximum(longest, 1.0e-12) >= MIN_FACET_SIZE
        if not reliable.any():
            continue
        planes = _convex_planes(piece)
        plane_normals = np.asarray([normal for _point, normal in planes]).reshape((-1, 3))
        plane_offsets = np.asarray([float(normal @ point) for point, normal in planes])
        samples = np.vstack([
            corners[reliable, 0] * (1.0 - (a + b) / 3.0)
            + corners[reliable, 1] * (a / 3.0)
            + corners[reliable, 2] * (b / 3.0)
            - normals[reliable] * probe
            for a, b in weights
        ])
        if len(plane_normals):
            samples = samples[
                np.all(samples @ plane_normals.T - plane_offsets <= 1.0e-6, axis=1)
            ]
        outside = 0
        for point in samples - origin:
            if _inside_source(bvh, point):
                continue
            hit = bvh.find_nearest(Vector(tuple(point)))
            if hit[0] is None or float(hit[3]) > tolerance:
                outside += 1
        if outside:
            offending.append((index, outside))
    return offending


def _source_coverage_deviation(
    source_vertices,
    source_faces,
    convex_pieces,
    tolerance=0.0,
    sample_limit=25000,
):
    """Measure source-surface samples against convex output colliders."""
    if not convex_pieces:
        return math.inf, 0
    centroids = source_vertices[source_faces].mean(axis=1)
    samples = np.vstack((source_vertices, centroids))
    if len(samples) > sample_limit:
        indices = np.linspace(0, len(samples) - 1, sample_limit, dtype=np.int32)
        samples = samples[indices]

    all_vertices = []
    all_faces = []
    offset = 0
    for piece in convex_pieces:
        all_vertices.append(piece.vertices)
        all_faces.append(piece.faces + offset)
        offset += len(piece.vertices)
    bvh = _surface_bvh(np.vstack(all_vertices), np.vstack(all_faces))
    bounds = np.asarray(
        [np.r_[piece.vertices.min(axis=0), piece.vertices.max(axis=0)] for piece in convex_pieces]
    )
    planes = [None] * len(convex_pieces)

    worst = 0.0
    uncovered = 0
    tolerance = float(tolerance)
    for point in samples:
        hit = bvh.find_nearest(Vector(tuple(point)))
        nearest = float(hit[3]) if hit[0] is not None else math.inf
        if nearest <= tolerance:
            worst = max(worst, nearest)
            continue
        inside = False
        candidates = np.flatnonzero(
            np.all(point >= bounds[:, :3] - 1.0e-6, axis=1)
            & np.all(point <= bounds[:, 3:] + 1.0e-6, axis=1)
        )
        for index in candidates:
            if planes[index] is None:
                planes[index] = _convex_planes(convex_pieces[index])
            if _point_inside_convex(point, planes[index], epsilon=1.0e-6):
                inside = True
                break
        if inside:
            continue
        uncovered += 1
        worst = max(worst, nearest)
    return worst, uncovered


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def _location(piece):
    center = piece.vertices.mean(axis=0) if len(piece.vertices) else np.zeros(3)
    return "({:.2f}, {:.2f}, {:.2f})".format(*center)


def _thin_limit(settings):
    return float(getattr(settings, "thin_threshold", THIN_PART_DEFAULT))


def _run_attempt(source, settings, seed=0):
    global _feature_tolerance
    thin_limit = _thin_limit(settings)
    tolerance = max(REFLEX_EPSILON, thin_limit * FEATURE_TOLERANCE_RATIO)
    saved = _feature_tolerance
    # The convexity tests of this run accept steps up to the tolerance.
    _feature_tolerance = tolerance
    try:
        return _decompose_with_tolerance(source, settings, seed, thin_limit, tolerance)
    finally:
        _feature_tolerance = saved


@dataclass(eq=False)
class _Node:
    """One cut of the decomposition tree, or one finished part."""
    piece: Piece
    children: list = field(default_factory=list)
    leaves: list = field(default_factory=list)
    thin: list = field(default_factory=list)
    invalid: list = field(default_factory=list)
    skip: int = 0
    exhausted: bool = False
    parent: object = None
    cut_key: object = None

    @property
    def count(self):
        return len(self.leaves) + sum(child.count for child in self.children)

    @property
    def work(self):
        """Triangles that re-cutting this subtree has to process again.

        A deterministic stand-in for the time a variant costs: every internal
        node analyses and cuts its piece, and that effort grows with the
        piece's triangles. Wall time would order the search differently on a
        faster machine, and the same model must cut the same way everywhere.
        """
        if not self.children:
            return 0
        return len(self.piece.faces) + sum(child.work for child in self.children)

    @property
    def thin_count(self):
        return len(self.thin) + sum(child.thin_count for child in self.children)

    @property
    def score(self):
        """Fewer parts first, then fewer parts thinner than the threshold."""
        return (self.count, self.thin_count)

    def collect(self):
        if not self.children:
            return list(self.leaves), list(self.thin), list(self.invalid)
        leaves, thin, invalid = list(self.leaves), list(self.thin), list(self.invalid)
        for child in self.children:
            child_leaves, child_thin, child_invalid = child.collect()
            leaves += child_leaves
            thin += child_thin
            invalid += child_invalid
        return leaves, thin, invalid

    def internal_nodes(self):
        nodes = [self] if self.children else []
        for child in self.children:
            nodes += child.internal_nodes()
        return nodes


def _decompose_node(piece, thin_limit, budget, skip=0):
    """Decompose one piece into a subtree of convex parts."""
    node = _Node(piece=piece, skip=skip)
    if piece.convex:
        # Replace the cut mesh by its clean hull: cut lines leave long
        # sliver triangles that carry no geometry of their own.
        hull = _hull_piece(piece.vertices, depth=piece.depth)
        node.leaves.append(hull if hull is not None and hull.closed else piece)
        return node
    if _hull_width(piece) < thin_limit:
        # Thinner than the thin-part threshold. Cutting it further would only
        # breed slivers, and deleting it would lose shape, so it enters the
        # merge pool as a hull for a neighbour to absorb.
        hull = _hull_piece(piece.vertices, depth=piece.depth)
        thin = hull if hull is not None and hull.closed else piece
        node.leaves.append(thin)
        node.thin.append(thin)
        return node
    if budget[0] <= 0:
        node.invalid.append(piece)
        return node
    key, children = _split_piece_keyed(piece, thin_limit, skip=skip)
    if children is None:
        node.invalid.append(piece)
        return node
    return _grow_node(node, key, children, thin_limit, budget)


def _grow_node(node, key, children, thin_limit, budget):
    """Attach the subtrees of an already chosen cut to its node."""
    node.cut_key = key
    budget[0] -= 1
    for child in children:
        child_node = _decompose_node(child, thin_limit, budget)
        child_node.parent = node
        node.children.append(child_node)
    return node


def _search_priority(node):
    """Parts per triangle of the node's own piece.

    Ordering by parts per unit of whole-subtree work was measured as well and
    is worse: it harvests the deep nodes first, after which the middle of the
    tree has little left to give, and the tower still ended at 248 parts after
    running the search to exhaustion.
    """
    return node.count / max(len(node.piece.faces), 1)


def _refine_tree(roots, thin_limit, passes):
    """Re-cut the parts that produced the most pieces and keep improvements.

    Every cut of the tree is exact, so any node can be cut again along a
    later ranked plane. A variant replaces the old subtree only when it ends
    in fewer parts, so the search can only improve the result.

    The node to re-cut is the one with the most parts per triangle. It is
    not retired after a few tries: it keeps walking down its ranked planes
    until they run out, and a node that has improved once is free to improve
    again. On the reference tower 297 variants now end at 259 parts in about
    six and a half minutes; retiring nodes after three tries stopped at 267
    after eleven.

    Moving the window by one plane often leaves the best cut in it unchanged,
    and such a variant would rebuild a subtree that has already been judged.
    Those are recognised by the name of their top cut and skipped without
    being counted as a variant.
    """
    improved = 0
    attempts = 0
    while attempts < passes:
        work_limit = SEARCH_WORK_SHARE * sum(root.work for root in roots)
        candidates = []
        for root in roots:
            candidates += [
                node for node in root.internal_nodes()
                if not node.exhausted and node.count > 2
            ]
        if not candidates:
            break
        node = max(candidates, key=_search_priority)
        tried = node.__dict__.setdefault("tried", {node.cut_key})
        skip = node.__dict__.get("next_skip", node.skip) + 1
        node.next_skip = skip
        if skip > SEARCH_MAX_SKIP:
            node.exhausted = True
            continue
        key, children = _split_piece_keyed(node.piece, thin_limit, skip=skip)
        if children is None:
            node.exhausted = True
            continue
        if key in tried:
            continue
        if node.work > work_limit and len(tried) > SEARCH_HEAVY_VARIANTS:
            # A node holding a large share of the model is close to a restart
            # of the whole decomposition. It gets one real variant: excluding
            # such nodes entirely cost the tower 247 -> 266 parts, because a
            # single accepted re-cut near the root reshapes everything below,
            # but its further variants took 353 s and gave nothing back.
            node.exhausted = True
            continue
        tried.add(key)
        attempts += 1
        budget = [LEAF_LIMIT]
        variant = _grow_node(
            _Node(piece=node.piece, skip=skip), key, children, thin_limit, budget
        )
        if variant.invalid or variant.score >= node.score:
            continue
        improved += node.count - variant.count
        variant.parent = node.parent
        variant.tried = tried
        variant.next_skip = skip
        if node.parent is None:
            roots[roots.index(node)] = variant
        else:
            node.parent.children[node.parent.children.index(node)] = variant
    return roots, improved, attempts


def _decompose_with_tolerance(source, settings, seed, thin_limit, tolerance):
    gap = float(settings.gap)
    warnings = []
    components = _component_pieces(source.vertices, source.faces)

    invalid = []
    pending = []
    for piece in components:
        if piece.closed:
            pending.append(piece)
            continue
        topology = _topology(len(piece.vertices), piece.faces)
        invalid.append(piece)
        warnings.append(
            "Source component near {} is not a closed solid ({} open, {} "
            "non-manifold edges); fix the model".format(
                _location(piece),
                topology.boundary_edges,
                topology.nonmanifold_edges,
            )
        )

    source_origin = source.vertices.mean(axis=0)
    source_bvh = _surface_bvh(source.vertices - source_origin, source.faces)
    budget = [LEAF_LIMIT]
    roots = [_decompose_node(piece, thin_limit, budget) for piece in pending]
    passes = max(0, int(getattr(settings, "attempts", 1)) - 1) * REFINEMENT_TRIES
    if passes:
        roots, improved, attempts = _refine_tree(roots, thin_limit, passes)
        if improved:
            warnings.append(
                "Search removed {} part(s) in {} variant(s)".format(improved, attempts)
            )
    convex = []
    for root in roots:
        root_leaves, _root_thin, root_invalid = root.collect()
        convex += root_leaves
        invalid += root_invalid
    for piece in invalid:
        warnings.append(
            "Concave part near {} ({:.3f} m3) could not be cut".format(
                _location(piece), piece.volume
            )
        )
    if budget[0] <= 0:
        warnings.append(
            "Decomposition needs more than {} parts".format(LEAF_LIMIT)
        )

    complete = not invalid
    if not complete:
        warnings.append(
            "{} part(s) could not be decomposed; see the messages above".format(
                len(invalid)
            )
        )

    convex = _merge_within_tolerance(
        convex, source_bvh, source_origin, tolerance, thin_limit
    )
    kept = _absorb_thin_pieces(
        convex, thin_limit, tolerance, source_bvh, source_origin
    )
    # Nothing is dropped: parts thinner than the threshold that no neighbour
    # could absorb stay in the collision and are only reported.
    thin_pieces = [
        piece for piece in kept if _minimum_width(piece) < thin_limit
    ]
    thin_volume = sum(piece.volume for piece in thin_pieces)
    thin_width = max((_minimum_width(piece) for piece in thin_pieces), default=0.0)
    if thin_pieces:
        warnings.append(
            "{} part(s) stayed thinner than {:.3f} m ({:.3f} m3)".format(
                len(thin_pieces), thin_limit, thin_volume
            )
        )

    if len(components) > 1:
        kept, overlap_complete = _resolve_component_overlaps(kept)
        if not overlap_complete:
            complete = False
            warnings.append("Overlap trimming exceeded the part limit")
        kept = _merge_convex_neighbours(kept)

    kept = _separate_touching_pieces(kept, limit=tolerance)

    if complete and len(components) == 1:
        # Bridged shallow steps add at most a tolerance-thick film.
        partition_volume = sum(piece.volume for piece in kept) + thin_volume
        expected = components[0].volume
        source_area = float(_face_normals(components[0].vertices, components[0].faces)[1].sum())
        if abs(partition_volume - expected) > tolerance * source_area + 1.0e-6:
            complete = False
            warnings.append(
                "Collision volume {:.4f} m3 differs from source {:.4f} m3".format(
                    partition_volume, expected
                )
            )

    # Exact polytopes before the gap: the float32 hulls the cutting stages
    # build are convex only to a fraction of a millimetre, and shifting their
    # planes would carry that error into the gap.
    kept = [
        _polytope_piece(piece.vertices, piece.depth) or piece for piece in kept
    ]
    hulls_before_gap = kept
    kept = [_inset_polytope(piece, gap * 0.5 + 1.0e-7) for piece in kept]
    kept = [
        piece for piece in kept
        if len(piece.faces) and not _is_ignorable_fragment(piece, settings)
    ]
    # Pieces that overlapped before the inset still overlap or sit closer
    # than the gap; clip them apart exactly, the way SINTEZ AGR Checker
    # measures intersections and gaps.
    kept = _separate_polytopes(kept, gap)
    kept = [
        piece for piece in kept
        if len(piece.faces) and not _is_ignorable_fragment(piece, settings)
    ]

    if complete and hulls_before_gap:
        excess = _source_excess(
            source.vertices,
            source.faces,
            hulls_before_gap,
            probe=thin_limit + 5.0e-3,
            tolerance=thin_limit,
        )
        if excess:
            complete = False
            warnings.append(
                "{} hull(s) extend outside the source volume".format(len(excess))
            )

    if len(kept) > MAX_PARTS:
        complete = False
        warnings.append(
            "Collision needs {} hulls, above the limit of {}".format(
                len(kept), MAX_PARTS
            )
        )

    # The output is rebuilt as exact polytopes with planar faces: separating
    # touching pieces cuts them again with triangulated caps, and needle
    # triangles are exactly what makes SINTEZ AGR Checker report a convex
    # hull as concave.
    output = []
    for piece in kept:
        polytope = _polytope_piece(piece.vertices, piece.depth)
        if polytope is not None:
            output.append(polytope)
    hulls = [(piece.vertices, piece.faces) for piece in output]
    return DecompositionResult(
        hulls=hulls,
        polygons=[piece.polygons for piece in output],
        max_deviation=tolerance,
        total_triangles=sum(len(faces) for _, faces in hulls),
        seed=seed,
        warnings=warnings,
        complete=complete,
        remaining_invalid=len(invalid),
        ignored_parts=[(piece.vertices, piece.faces) for piece in thin_pieces],
        failed_parts=[(piece.vertices, piece.faces) for piece in invalid],
        feature_tolerance=tolerance,
    )


def decompose(source, settings):
    return _run_attempt(source, settings, 0)
