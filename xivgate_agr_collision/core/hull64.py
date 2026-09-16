# SPDX-FileCopyrightText: 2026 XIVgate
# SPDX-License-Identifier: GPL-3.0-or-later
# Double precision convex hull and point-to-surface distance.
#
# bmesh.ops.convex_hull works in float32 and merges nearly coplanar points, so
# a point it drops lies up to a fraction of a millimetre outside the hull it
# returns. Measuring concavity against that hull reports the dropped point as
# a dent. The hull here is built in float64 from the exact input points, and
# the module has no Blender dependency so it can be checked against qhull.

import numpy as np

# Coplanarity tolerance as a fraction of the point cloud's size.
HULL_RELATIVE_EPSILON = 1.0e-11


def _unit_normal(points, a, b, c):
    normal = np.cross(points[b] - points[a], points[c] - points[a])
    length = float(np.linalg.norm(normal))
    if length == 0.0:
        return normal, 0.0
    return normal / length, length


def convex_hull(points):
    """Outward triangles of the convex hull, as indices into ``points``.

    Quickhull. Every face keeps the points in front of it, and the next point
    added is always the farthest one in front of a face, which makes it a
    true vertex of the final hull. Inserting points in any other order was
    tried first: on nearly coplanar architectural hulls whose coordinates had
    been rounded to float32 it folded the hull over itself, doubling its
    volume and leaving points two metres outside it.

    The faces a new point sees are grown from its own face across shared
    edges, so the horizon is always one loop. Returns an empty array for
    fewer than four points or a flat set.
    """
    points = np.asarray(points, dtype=np.float64).reshape((-1, 3))
    empty = np.empty((0, 3), dtype=np.int64)
    if len(points) < 4:
        return empty
    centered = points - points.mean(axis=0)
    scale = float(np.abs(centered).max())
    if scale == 0.0:
        return empty
    epsilon = scale * HULL_RELATIVE_EPSILON

    axis = int(np.argmax(centered.max(axis=0) - centered.min(axis=0)))
    first = int(np.argmin(centered[:, axis]))
    second = int(np.argmax(centered[:, axis]))
    direction = centered[second] - centered[first]
    if float(np.linalg.norm(direction)) <= epsilon:
        return empty
    offsets = centered - centered[first]
    across = np.linalg.norm(np.cross(offsets, direction), axis=1) / np.linalg.norm(direction)
    third = int(np.argmax(across))
    if across[third] <= epsilon:
        return empty
    normal, _length = _unit_normal(centered, first, second, third)
    height = offsets @ normal
    fourth = int(np.argmax(np.abs(height)))
    if abs(height[fourth]) <= epsilon:
        return empty

    # face id -> [a, b, c, unit normal, offset, indices of points in front]
    faces = {}
    edge_face = {}
    counter = [0]

    def add_face(a, b, c):
        unit, _length = _unit_normal(centered, a, b, c)
        face_id = counter[0]
        counter[0] += 1
        faces[face_id] = [a, b, c, unit, float(unit @ centered[a]), []]
        for edge in ((a, b), (b, c), (c, a)):
            edge_face[edge] = face_id
        return face_id

    def remove_face(face_id):
        a, b, c = faces[face_id][:3]
        for edge in ((a, b), (b, c), (c, a)):
            if edge_face.get(edge) == face_id:
                del edge_face[edge]
        del faces[face_id]

    inside = centered[[first, second, third, fourth]].mean(axis=0)
    for a, b, c in (
        (first, second, third),
        (first, second, fourth),
        (first, third, fourth),
        (second, third, fourth),
    ):
        unit, _length = _unit_normal(centered, a, b, c)
        if float(unit @ (inside - centered[a])) > 0.0:
            b, c = c, b
        add_face(a, b, c)

    def assign(candidates, face_ids):
        if not candidates or not face_ids:
            return
        indices = np.asarray(candidates, dtype=np.int64)
        normals = np.asarray([faces[face][3] for face in face_ids])
        plane_offsets = np.asarray([faces[face][4] for face in face_ids])
        distance = centered[indices] @ normals.T - plane_offsets
        best = distance.argmax(axis=1)
        reach = distance[np.arange(len(indices)), best]
        for index, position, value in zip(indices.tolist(), best.tolist(), reach.tolist()):
            if value > epsilon:
                faces[face_ids[position]][5].append(index)

    seed = {first, second, third, fourth}
    assign([index for index in range(len(points)) if index not in seed], list(faces))

    while True:
        face_id = next((face for face, data in faces.items() if data[5]), None)
        if face_id is None:
            break
        data = faces[face_id]
        outside = np.asarray(data[5], dtype=np.int64)
        eye = int(outside[np.argmax(centered[outside] @ data[3] - data[4])])
        eye_point = centered[eye]

        visible = {face_id}
        stack = [face_id]
        while stack:
            current = stack.pop()
            a, b, c = faces[current][:3]
            for u, v in ((a, b), (b, c), (c, a)):
                neighbour = edge_face.get((v, u))
                if neighbour is None or neighbour in visible:
                    continue
                other = faces[neighbour]
                if float(other[3] @ eye_point - other[4]) > epsilon:
                    visible.add(neighbour)
                    stack.append(neighbour)

        horizon = []
        orphans = []
        for current in visible:
            a, b, c = faces[current][:3]
            orphans.extend(faces[current][5])
            for u, v in ((a, b), (b, c), (c, a)):
                if edge_face.get((v, u)) not in visible:
                    horizon.append((u, v))
        for current in visible:
            remove_face(current)
        created = [add_face(u, v, eye) for u, v in horizon]
        assign([index for index in orphans if index != eye], created)

    return np.asarray(
        [data[:3] for data in faces.values()], dtype=np.int64
    ).reshape((-1, 3))


def hull_edges(triangles):
    """Unique undirected edges of a triangle list."""
    if len(triangles) == 0:
        return np.empty((0, 2), dtype=np.int64)
    pairs = np.concatenate(
        (triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]), axis=0
    )
    return np.unique(np.sort(pairs, axis=1), axis=0)


def _segment_distance(points, a, b):
    ab = b - a
    length = np.einsum("tk,tk->t", ab, ab)
    t = np.einsum("ptk,tk->pt", points - a[None], ab)
    t = np.divide(t, length[None], out=np.zeros_like(t), where=length[None] > 0.0)
    t = np.clip(t, 0.0, 1.0)
    closest = a[None] + t[..., None] * ab[None]
    return np.linalg.norm(points - closest, axis=2)


def distance_to_triangles(points, a, b, c, chunk=128):
    """Distance from every point to the nearest triangle, exactly.

    The nearest point of a triangle is on one of its edges, or inside it when
    the point projects inside. Edges are clamped segments, which stay exact
    however thin the triangle is; the face term is only used for triangles
    with a well defined normal.
    """
    points = np.asarray(points, dtype=np.float64).reshape((-1, 3))
    nearest = np.empty(len(points))
    normal = np.cross(b - a, c - a)
    length = np.linalg.norm(normal, axis=1)
    reference = np.maximum(
        np.einsum("tk,tk->t", b - a, b - a) + np.einsum("tk,tk->t", c - a, c - a),
        1.0e-300,
    )
    usable = length > 1.0e-12 * reference
    unit = np.divide(
        normal, length[:, None], out=np.zeros_like(normal), where=usable[:, None]
    )
    for start in range(0, len(points), chunk):
        p = points[start:start + chunk, None, :]
        distance = np.minimum(
            np.minimum(_segment_distance(p, a, b), _segment_distance(p, b, c)),
            _segment_distance(p, c, a),
        )
        height = np.einsum("ptk,tk->pt", p - a[None], unit)
        projected = p - height[..., None] * unit[None]
        inside = np.broadcast_to(usable[None], height.shape).copy()
        for u, v in ((a, b), (b, c), (c, a)):
            side = np.einsum(
                "ptk,tk->pt", np.cross((v - u)[None], projected - u[None]), unit
            )
            inside &= side >= 0.0
        distance = np.where(inside, np.minimum(distance, np.abs(height)), distance)
        nearest[start:start + chunk] = distance.min(axis=1)
    return nearest
