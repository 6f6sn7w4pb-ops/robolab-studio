"""Frame conversions for simulation contact labels; no physics or model I/O."""
import numpy as np


def finite_array(value, shape, name):
    array = np.asarray(value, dtype=float)
    if array.shape != shape or not np.isfinite(array).all():
        raise ValueError('Invalid '+name)
    return array


def rotation_matrix(value):
    rotation = finite_array(value, (3, 3), 'rotation')
    if not np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-8, rtol=0) or abs(np.linalg.det(rotation)-1) > 1e-8:
        raise ValueError('Rotation must be orthonormal and right handed')
    return rotation


def rigid_transform(rotation, position):
    transform = np.eye(4)
    transform[:3, :3] = rotation_matrix(rotation)
    transform[:3, 3] = finite_array(position, (3,), 'position')
    return transform


def validate_transform(value):
    transform = finite_array(value, (4, 4), 'transform')
    rotation_matrix(transform[:3, :3])
    if not np.allclose(transform[3], [0, 0, 0, 1], atol=1e-12, rtol=0):
        raise ValueError('Invalid homogeneous transform bottom row')
    return transform


def point_to_local(transform_world_local, point_world):
    transform = validate_transform(transform_world_local)
    point = finite_array(point_world, (3,), 'point')
    return transform[:3, :3].T @ (point-transform[:3, 3])


def point_to_world(transform_world_local, point_local):
    transform = validate_transform(transform_world_local)
    return transform[:3, :3] @ finite_array(point_local, (3,), 'point') + transform[:3, 3]


def vector_to_local(transform_world_local, vector_world):
    transform = validate_transform(transform_world_local)
    return transform[:3, :3].T @ finite_array(vector_world, (3,), 'vector')


def contact_force_world(frame_rows, wrench_contact, other_is_geom2):
    """Canonical force on other, and normal pointing from finger to other.

    MuJoCo contact axes are ROWS in world coordinates. Only force is
    transformed here: torque about the contact point is not TCP torque.
    """
    if not isinstance(other_is_geom2, (bool, np.bool_)):
        raise ValueError('Contact role must be boolean')
    axes = rotation_matrix(frame_rows)
    wrench = finite_array(wrench_contact, (6,), 'contact wrench')
    sign = 1. if other_is_geom2 else -1.
    return sign * (axes.T @ wrench[:3]), sign * axes[0]
