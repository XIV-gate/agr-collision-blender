# SPDX-FileCopyrightText: 2026 XIVgate
# SPDX-License-Identifier: GPL-3.0-or-later
# Collision debugger: strict per-object measurements of existing hulls.
#
# Validate answers "is this set acceptable for AGR"; its convexity test lets a
# hull through while its convex hull is within 5 % of its volume. The debugger
# answers a different question - "where exactly is this set not ideal" - so it
# measures every object on its own and reports the smallest real concavity,
# the volume and the true minimum thickness.

from dataclasses import dataclass
import math

import bmesh
import numpy as np

from . import hull64

# Blender stores mesh coordinates as float32. A coordinate carries a rounding
# error of about this fraction of its magnitude. Depths within that bound are
# rounding, not geometry, and are never reported.
FLOAT32_RELATIVE_ERROR = 1.2e-7
# Polygons with less area than this have no usable normal.
DEGENERATE_AREA = 1.0e-12


@dataclass
class HullReport:
    name: str
    closed: bool
    concavity: float
    volume: float
    thickness: float


def _world_mesh(ob):
    """World-space copy of an object's mesh, in float64.

    Returns (vertices, polygons, closed, coordinate_noise), where polygons are
    vertex index lists and the noise is the float32 rounding bound of the
    stored coordinates. The transform is applied with numpy in float64:
    letting bmesh do it would round world coordinates to float32 again, which
    a hundred metres from the origin is several micrometres of noise.
    """
    bm = bmesh.new()
    try:
        bm.from_mesh(ob.data)
        closed = bool(bm.faces) and all(edge.is_manifold for edge in bm.edges)
        bm.verts.index_update()
        local = np.asarray([tuple(vertex.co) for vertex in bm.verts], dtype=np.float64)
        polygons = [[vertex.index for vertex in face.verts] for face in bm.faces]
    finally:
        bm.free()
    local = local.reshape((-1, 3))
    matrix = np.asarray(ob.matrix_world, dtype=np.float64)
    linear = matrix[:3, :3]
    vertices = local @ linear.T + matrix[:3, 3]
    magnitude = float(np.abs(local).max()) if len(local) else 0.0
    scale = float(np.abs(linear).max()) if len(local) else 1.0
    noise = FLOAT32_RELATIVE_ERROR * magnitude * max(scale, 1.0)
    return vertices, polygons, closed, noise


def _polygon_normals(vertices, polygons):
    """Newell unit normals of the polygons that have any area."""
    normals = []
    for polygon in polygons:
        points = vertices[polygon]
        following = np.roll(points, -1, axis=0)
        normal = np.array([
            np.sum((points[:, 1] - following[:, 1]) * (points[:, 2] + following[:, 2])),
            np.sum((points[:, 2] - following[:, 2]) * (points[:, 0] + following[:, 0])),
            np.sum((points[:, 0] - following[:, 0]) * (points[:, 1] + following[:, 1])),
        ])
        length = float(np.linalg.norm(normal))
        if 0.5 * length > DEGENERATE_AREA:
            normals.append(normal / length)
    return np.asarray(normals).reshape((-1, 3))


def surface_samples(vertices, polygons):
    """Surface sample points and the vertices that polygons actually use.

    The samples are the vertices, the edge midpoints and the polygon centres.
    """
    used = sorted({index for polygon in polygons for index in polygon})
    samples = [vertices[used]]
    edges = {
        (
            min(polygon[k], polygon[(k + 1) % len(polygon)]),
            max(polygon[k], polygon[(k + 1) % len(polygon)]),
        )
        for polygon in polygons
        for k in range(len(polygon))
    }
    if edges:
        pairs = np.asarray(sorted(edges), dtype=np.int64)
        samples.append(0.5 * (vertices[pairs[:, 0]] + vertices[pairs[:, 1]]))
    if polygons:
        samples.append(
            np.asarray([vertices[polygon].mean(axis=0) for polygon in polygons])
        )
    return np.vstack(samples), vertices[used]


def concavity_depth(vertices, polygons, noise, tolerance=0.0):
    """How far the surface sinks below its own convex hull, in metres.

    Every vertex, edge midpoint and polygon centre lies inside or on the
    convex hull of the vertices, so its distance to the hull surface is zero
    exactly when the surface there is part of the hull. A dent shows up at
    its vertices; a fold between hull vertices shows up at the midpoints and
    centres of the folded faces.

    Nothing here depends on the orientation of a face. A plane-based test was
    tried first and reported concavities of tens of metres on convex
    generated hulls: a ten metre sliver face 22 micrometres high has no
    meaningful plane, and its tilt swung a vertex 28 m away across it. The
    hull is the float64 one from hull64; bmesh's float32 hull merges nearly
    coplanar points and made the points it dropped look like dents.

    Depths within twice the float32 rounding of the stored coordinates are
    rounding, not geometry. Everything else above ``tolerance`` counts.
    """
    if not polygons:
        return 0.0
    samples, points = surface_samples(vertices, polygons)
    if len(points) < 4:
        return 0.0
    triangles = hull64.convex_hull(points)
    if len(triangles) == 0:
        return 0.0
    depth = hull64.distance_to_triangles(
        samples,
        points[triangles[:, 0]],
        points[triangles[:, 1]],
        points[triangles[:, 2]],
    )
    deepest = float(depth.max()) if len(depth) else 0.0
    return deepest if deepest > tolerance + 2.0 * noise else 0.0


def minimum_thickness(vertices):
    """Exact minimum width of the object's convex hull, in metres.

    The narrowest direction of a convex polyhedron is always the normal of a
    face or the cross product of two edges, so the minimum over those
    directions is exact rather than an estimate. A badly oriented sliver
    normal can only report a wider width than the true one, never a narrower,
    so it cannot distort the minimum. The generator's thin-part warning checks
    face normals only, so this value can be a little smaller.
    """
    if len(vertices) < 4:
        return 0.0
    triangles = hull64.convex_hull(vertices)
    if len(triangles) == 0:
        return 0.0
    edges = hull64.hull_edges(triangles)
    points = vertices[np.unique(triangles)]
    directions = [_polygon_normals(vertices, triangles.tolist())]
    if len(edges):
        vectors = vertices[edges[:, 1]] - vertices[edges[:, 0]]
        lengths = np.linalg.norm(vectors, axis=1)
        keep = lengths > 1.0e-9
        vectors = vectors[keep] / lengths[keep, None]
        first, second = np.triu_indices(len(vectors), k=1)
        crosses = np.cross(vectors[first], vectors[second])
        norms = np.linalg.norm(crosses, axis=1)
        keep = norms > 1.0e-9
        directions.append(crosses[keep] / norms[keep, None])
    directions = np.vstack([block for block in directions if len(block)] or [np.empty((0, 3))])
    if len(directions) == 0:
        return 0.0
    thickness = math.inf
    # Chunked so a hull with many edges does not build one huge matrix.
    for start in range(0, len(directions), 4096):
        projection = points @ directions[start:start + 4096].T
        widths = projection.max(axis=0) - projection.min(axis=0)
        thickness = min(thickness, float(widths.min()))
    return thickness


def mesh_volume(vertices, polygons):
    """Enclosed volume by the divergence theorem over fan triangles."""
    total = 0.0
    for polygon in polygons:
        origin = vertices[polygon[0]]
        for index in range(1, len(polygon) - 1):
            total += float(
                origin @ np.cross(vertices[polygon[index]], vertices[polygon[index + 1]])
            )
    return abs(total) / 6.0


def inspect_object(ob, tolerance=0.0, measure=("concavity", "volume", "thickness")):
    """Measure one mesh object in world space."""
    vertices, polygons, closed, noise = _world_mesh(ob)
    concavity = 0.0
    volume = 0.0
    thickness = 0.0
    if "concavity" in measure:
        concavity = concavity_depth(vertices, polygons, noise, tolerance)
    if "volume" in measure:
        volume = mesh_volume(vertices, polygons)
    if "thickness" in measure:
        thickness = minimum_thickness(vertices)
    return HullReport(
        name=ob.name,
        closed=closed,
        concavity=concavity,
        volume=volume,
        thickness=thickness,
    )
