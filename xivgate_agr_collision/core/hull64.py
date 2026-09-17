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


def planar_polytope(points, merge_distance):
    """Convex hull of ``points`` as planar facets instead of raw triangles.

    Returns ``(vertices, polygons)`` with only the vertices the polygons use,
    or ``None`` when the points span no volume.

    A raw hull triangulation is full of long needle triangles whose plane is
    decided by micrometres of rounding. SINTEZ AGR Checker judges convexity
    against every face plane, and on the reference tower such needles made
    five hulls with a true concavity of 0.03-0.33 mm fail its 10 mm check,
    because a vertex ten metres away swings across a tilted needle. Merging
    the triangles of one plane into a facet lets it be triangulated again
    with wide triangles, see ``wide_triangles``.

    Triangles join a facet when all their corners lie within
    ``merge_distance`` of the facet's seed plane, seeded from the largest
    triangles first. A corner shared by only two facets is not a corner of
    the polytope at all, only a point on the edge between them, and is
    dropped from both.
    """
    points = np.asarray(points, dtype=np.float64).reshape((-1, 3))
    triangles = convex_hull(points)
    if len(triangles) == 0:
        return None
    corners = points[triangles]
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    areas = 0.5 * np.linalg.norm(normals, axis=1)
    units = np.divide(
        normals, (2.0 * areas)[:, None], out=np.zeros_like(normals), where=areas[:, None] > 0.0
    )
    edge_triangle = {}
    for index, (a, b, c) in enumerate(triangles.tolist()):
        for edge in ((a, b), (b, c), (c, a)):
            edge_triangle[edge] = index

    facet_of = np.full(len(triangles), -1, dtype=np.int64)
    facets = []
    for seed in np.argsort(-areas, kind="stable").tolist():
        if facet_of[seed] >= 0:
            continue
        unit = units[seed]
        offset = float(unit @ corners[seed, 0])
        members = [seed]
        facet_of[seed] = len(facets)
        stack = [seed]
        while stack:
            current = stack.pop()
            a, b, c = triangles[current].tolist()
            for u, v in ((a, b), (b, c), (c, a)):
                neighbour = edge_triangle.get((v, u))
                if neighbour is None or facet_of[neighbour] >= 0:
                    continue
                if float(units[neighbour] @ unit) <= 0.0:
                    continue
                if float(np.abs(corners[neighbour] @ unit - offset).max()) > merge_distance:
                    continue
                facet_of[neighbour] = len(facets)
                members.append(neighbour)
                stack.append(neighbour)
        facets.append(members)

    polygons = []
    for members in facets:
        member_set = set(members)
        following = {}
        valid = True
        for index in members:
            a, b, c = triangles[index].tolist()
            for u, v in ((a, b), (b, c), (c, a)):
                if facet_of[edge_triangle[(v, u)]] == facet_of[index]:
                    continue
                if u in following:
                    valid = False
                following[u] = v
        loop = []
        if valid and following:
            start = next(iter(following))
            current = start
            while True:
                loop.append(current)
                current = following.get(current)
                if current is None or len(loop) > len(following):
                    valid = False
                    break
                if current == start:
                    break
            valid = valid and len(loop) == len(following)
        if valid and len(loop) >= 3:
            polygons.append(loop)
        else:
            # A facet whose outline is not one simple loop is left as its
            # triangles rather than guessed at.
            polygons.extend(triangles[index].tolist() for index in sorted(member_set))

    incidence = {}
    for polygon in polygons:
        for vertex in polygon:
            incidence[vertex] = incidence.get(vertex, 0) + 1
    polygons = [
        [vertex for vertex in polygon if incidence[vertex] >= 3]
        for polygon in polygons
    ]
    polygons = [polygon for polygon in polygons if len(polygon) >= 3]

    used = sorted({vertex for polygon in polygons for vertex in polygon})
    remap = {old: new for new, old in enumerate(used)}
    return points[used], [[remap[vertex] for vertex in polygon] for polygon in polygons]


def facet_width(points, polygon):
    """Narrowest width of a convex planar polygon.

    For a convex polygon the minimum width is measured across some edge, so
    it is the smallest, over the edges, of the farthest vertex from that
    edge's line.
    """
    corners = points[polygon]
    width = np.inf
    for index in range(len(corners)):
        start = corners[index]
        direction = corners[(index + 1) % len(corners)] - start
        length = float(np.linalg.norm(direction))
        if length == 0.0:
            continue
        across = np.linalg.norm(np.cross(corners - start, direction / length), axis=1)
        width = min(width, float(across.max()))
    return 0.0 if width == np.inf else width


def _removal_loss(vertices, polygons, corner):
    """Upper bound on how far the hull retreats when ``corner`` is removed.

    The new hull contains the hull of the corner's neighbours and the
    polytope's centre, so the distance to that smaller hull can only
    overstate the loss.
    """
    ring = sorted({other for polygon in polygons if corner in polygon for other in polygon} - {corner})
    local = np.vstack((vertices[ring], vertices.mean(axis=0)[None, :]))
    triangles = convex_hull(local)
    if len(triangles) == 0:
        return np.inf
    normals = np.cross(
        local[triangles[:, 1]] - local[triangles[:, 0]],
        local[triangles[:, 2]] - local[triangles[:, 0]],
    )
    probe = vertices[corner]
    if bool(np.all(normals @ probe - np.einsum("tk,tk->t", normals, local[triangles[:, 0]]) <= 0.0)):
        return 0.0
    return float(distance_to_triangles(
        probe[None, :], local[triangles[:, 0]], local[triangles[:, 1]], local[triangles[:, 2]]
    )[0])


def simplify_needles(points, merge_distance, needle_aspect, max_loss, max_removals=64):
    """Remove the corners that force needle triangles into the hull.

    Returns ``(vertices, polygons, triangles)`` or ``None``.

    SINTEZ AGR Checker tests every triangle's plane against all other
    vertices. A triangle much narrower than its hull is long has a plane
    decided by float32 rounding, and far vertices land millimetres across
    it. On the reference tower almost all such needles sat inside wide
    facets, forced by corners that lie nearly on the line between their
    neighbours; no triangulation of such a facet avoids them. Removing the
    corner does, and a corner is only removed when the hull retreats from
    it by at most ``max_loss``. Removal only shrinks the hull, so no gap or
    overlap can get worse.

    A triangle is a needle when the hull's diameter exceeds its width
    ``needle_aspect`` times.
    """
    result = planar_polytope(points, merge_distance)
    for _removal in range(max_removals + 1):
        if result is None:
            return None
        vertices, polygons = result
        triangles = wide_triangles(vertices, polygons)
        if len(vertices) <= 4 or _removal == max_removals:
            return vertices, polygons, triangles
        diameter = float(np.linalg.norm(np.ptp(vertices, axis=0)))
        needles = [
            triangle for triangle in triangles.tolist()
            if _triangle_width(*vertices[triangle]) * needle_aspect < diameter
        ]
        if not needles:
            return vertices, polygons, triangles
        candidates = sorted({corner for triangle in needles for corner in triangle})
        losses = [(_removal_loss(vertices, polygons, corner), corner) for corner in candidates]
        best_loss, best_corner = min(losses)
        if best_loss > max_loss:
            return vertices, polygons, triangles
        simplified = planar_polytope(np.delete(vertices, best_corner, axis=0), merge_distance)
        if simplified is None:
            return vertices, polygons, triangles
        result = simplified
    return None


def _triangle_width(a, b, c):
    """Height of a triangle over its longest edge."""
    longest = max(np.linalg.norm(b - a), np.linalg.norm(c - b), np.linalg.norm(a - c))
    if longest == 0.0:
        return 0.0
    return float(np.linalg.norm(np.cross(b - a, c - a))) / longest


def wide_triangles(points, polygons):
    """Triangulate convex polygons so their narrowest triangle is widest.

    SINTEZ AGR Checker tests every triangle's plane against all other
    vertices. A narrow triangle's plane is set by micrometres of float32
    rounding, and a vertex metres away lands millimetres across it, so the
    triangulation that keeps each polygon's narrowest triangle as wide as
    possible is the one least likely to be misjudged. A fan from one corner
    is the opposite: on a long strip every triangle is a needle.

    Exact interval dynamic programming over each convex polygon.
    """
    rows = []
    for polygon in polygons:
        count = len(polygon)
        if count == 3:
            rows.append(tuple(polygon))
            continue
        corners = points[polygon]
        best = [[np.inf] * count for _ in range(count)]
        split = [[-1] * count for _ in range(count)]
        for span in range(2, count):
            for first in range(count - span):
                last = first + span
                best_value = -1.0
                best_split = first + 1
                for middle in range(first + 1, last):
                    value = min(
                        best[first][middle],
                        best[middle][last],
                        _triangle_width(corners[first], corners[middle], corners[last]),
                    )
                    if value > best_value:
                        best_value = value
                        best_split = middle
                best[first][last] = best_value
                split[first][last] = best_split
        stack = [(0, count - 1)]
        while stack:
            first, last = stack.pop()
            if last - first < 2:
                continue
            middle = split[first][last]
            rows.append((polygon[first], polygon[middle], polygon[last]))
            stack.append((first, middle))
            stack.append((middle, last))
    return np.asarray(rows, dtype=np.int64).reshape((-1, 3))


def fan_triangles(polygons):
    """Triangles of convex polygons, as an (N, 3) index array."""
    rows = [
        (polygon[0], polygon[corner], polygon[corner + 1])
        for polygon in polygons
        for corner in range(1, len(polygon) - 1)
    ]
    return np.asarray(rows, dtype=np.int64).reshape((-1, 3))


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
