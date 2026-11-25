"""Clearance between a box (the D455 body) and triangle meshes (NumPy only).

A box is (centre (3,), axes (3, 3), half (3,)): the rows of ``axes`` are its unit edge
directions and ``half`` its half extents, all in one frame and in metres. A mesh is
(vertices (N, 3), triangles (M, 3) vertex indices) in the same frame; a box can be turned into
one with ``box_mesh``.

``box_mesh_clearance`` returns the smallest distance between the box and the mesh surface. It
combines the exact distance from every mesh vertex to the box with the exact distance from
points sampled on the box surface (``spacing`` apart) to every nearby triangle, so it can
overestimate the true distance by at most about ``spacing``. A mesh vertex inside the box gives
a negative value (overlap).
"""
import numpy as np


def point_box_distance(points, centre, axes, half):
    """Signed distance from points (N, 3) to the box: negative inside."""
    local = (np.asarray(points, float)-centre)@np.asarray(axes, float).T
    q = np.abs(local)-half
    return np.linalg.norm(np.maximum(q, 0.), axis=-1)+np.minimum(q.max(-1), 0.)


def box_mesh(centre, axes, half):
    """The box's 8 corners and 12 surface triangles."""
    signs = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], float)
    vertices = np.asarray(centre, float)+(signs*half)@np.asarray(axes, float)
    faces = []
    for axis in range(3):
        for side in (-1, 1):
            quad = [i for i in range(8) if signs[i, axis] == side]          # 4 corners of one face
            a, b, c, d = sorted(quad, key=lambda i: tuple(signs[i]))
            faces += [(a, b, d), (a, d, c)]
    return vertices, np.array(faces, int)


def box_surface_points(centre, axes, half, spacing):
    """Grid points on all six faces of the box, at most ``spacing`` apart along each edge."""
    local = []
    for k in range(3):
        i, j = (a for a in range(3) if a != k)
        u = np.linspace(-half[i], half[i], max(2, int(np.ceil(2*half[i]/spacing))+1))
        v = np.linspace(-half[j], half[j], max(2, int(np.ceil(2*half[j]/spacing))+1))
        grid_u, grid_v = np.meshgrid(u, v, indexing='ij')
        for side in (-1., 1.):
            face = np.zeros((grid_u.size, 3))
            face[:, i], face[:, j], face[:, k] = grid_u.ravel(), grid_v.ravel(), side*half[k]
            local.append(face)
    return np.asarray(centre, float)+np.concatenate(local)@np.asarray(axes, float)


def _segment_distance(p, a, b):
    ab = b-a
    t = np.clip(((p-a)*ab).sum(-1)/np.maximum((ab*ab).sum(-1), 1e-30), 0., 1.)
    return np.linalg.norm(p-(a+t[..., None]*ab), axis=-1)


def point_triangle_distance(points, triangles, chunk=1024):
    """Smallest distance from each point (N, 3) to any triangle in (M, 3, 3)."""
    points = np.asarray(points, float)
    tri = np.asarray(triangles, float)
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    normal = np.cross(b-a, c-a)
    area2 = np.linalg.norm(normal, axis=-1)
    unit = normal/np.maximum(area2, 1e-30)[:, None]
    flat = area2 > 1e-12
    out = np.empty(len(points))
    for start in range(0, len(points), chunk):
        p = points[start:start+chunk, None, :]
        edges = np.minimum(np.minimum(_segment_distance(p, a, b), _segment_distance(p, b, c)),
                           _segment_distance(p, c, a))
        height = ((p-a)*unit).sum(-1)
        foot = p-height[..., None]*unit
        inside = flat
        for u, v in ((a, b), (b, c), (c, a)):           # the foot is on the inner side of every edge
            inside = inside & ((np.cross(v-u, foot-u)*normal).sum(-1) >= 0.)
        out[start:start+chunk] = np.where(inside, np.abs(height), edges).min(-1)
    return out


def box_mesh_clearance(centre, axes, half, vertices, triangles, spacing=.002, enough=None):
    """Smallest distance between the box and the mesh; negative if a mesh vertex is inside.

    With ``enough``, a mesh that is certainly farther than ``enough`` returns a lower bound
    (>= enough) instead of the exact distance, which is much cheaper for far-away meshes."""
    vertices = np.asarray(vertices, float)
    triangles = np.asarray(triangles, int)
    to_box = point_box_distance(vertices, centre, axes, half)
    best = float(to_box.min())
    if best <= 0.:
        return best
    tri = vertices[triangles]
    longest = np.linalg.norm(tri-np.roll(tri, 1, axis=1), axis=-1).max(-1)
    # Every point of a triangle lies within its longest edge of one of its vertices.
    lower = np.maximum(to_box[triangles].min(-1)-longest, 0.)
    if enough is not None and lower.min() >= enough:
        return float(lower.min())
    for step in (4*spacing, spacing):                                # coarse, then fine
        keep = lower <= best                                         # the only triangles that can be closer
        near = tri[keep]
        points = box_surface_points(centre, axes, half, step)
        low, high = near.reshape(-1, 3).min(0)-best, near.reshape(-1, 3).max(0)+best
        points = points[np.all((points >= low) & (points <= high), axis=1)]
        if len(points):
            best = min(best, float(point_triangle_distance(points, near).min()))
    return best
