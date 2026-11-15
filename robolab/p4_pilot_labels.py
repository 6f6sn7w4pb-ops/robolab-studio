"""Pilot labels with explicit surface, frame and action semantics."""
import math

import mujoco
import numpy as np

from .contact_geometry import point_to_local
from .p4_pilot_scene import OBSTACLE

MOTION_FILE = 'observed_motion.jsonl'
MOTION_SPACE = 'observed_world_delta_pose_7d_v1'


def box_surface(point, center, rotation, half_size):
    local = rotation.T @ (point-center)
    nearest = np.clip(local, -half_size, half_size)
    if np.any(np.abs(local) > half_size):
        signed_distance = np.linalg.norm(local-nearest)
    else:
        face = int(np.argmin(half_size-np.abs(local)))
        nearest[face] = math.copysign(half_size[face], local[face])
        signed_distance = -np.linalg.norm(local-nearest)
    return center+rotation@nearest, float(signed_distance)


def nearest_obstacle(model, data, world_gripper):
    """Nearest of two known surfaces from the TCP point, not a swept-volume clearance."""
    point = np.asarray(world_gripper)[:3, 3]
    floor = model.geom('floor').id
    box = model.geom(OBSTACLE).id
    if (model.geom_type[floor] != mujoco.mjtGeom.mjGEOM_PLANE
            or model.geom_type[box] != mujoco.mjtGeom.mjGEOM_BOX):
        raise ValueError('Unsupported P4 pilot obstacle geometry')
    normal = data.geom_xmat[floor].reshape(3, 3)[:, 2]
    signed = float((point-data.geom_xpos[floor]) @ normal)
    candidates = [('floor', point-signed*normal, signed)]
    surface, signed = box_surface(point, data.geom_xpos[box],
                                  data.geom_xmat[box].reshape(3, 3), model.geom_size[box])
    candidates.append((OBSTACLE, surface, signed))
    name, surface, signed = min(candidates, key=lambda item: abs(item[2]))
    return {'geom': name, 'point_world_m': surface.tolist(),
            'point_gripper_m': point_to_local(world_gripper, surface).tolist(), 'signed_distance_m': signed}


def motion_rows(poses):
    """World translation and left rotation from one sampled image pair to the next."""
    for index, (sample, time, pose, opening) in enumerate(poses):
        row = dict(sample_index=sample, time_s=time, next_sample_index=None, dt_s=None, action=None)
        if index+1 < len(poses):
            next_sample, next_time, next_pose, _ = poses[index+1]
            first, last = np.asarray(pose), np.asarray(next_pose)
            rotation = last[:3, :3] @ first[:3, :3].T
            quat, rotation_vector = np.zeros(4), np.zeros(3)
            mujoco.mju_mat2Quat(quat, rotation.ravel())
            if quat[0] < 0:
                quat *= -1
            mujoco.mju_quat2Vel(rotation_vector, quat, 1.)
            row.update(next_sample_index=next_sample, dt_s=next_time-time,
                       action=[*(last[:3, 3]-first[:3, 3]).tolist(), *rotation_vector.tolist(),
                               1 if opening == 0. else -1])
        yield row


def check_motion(rows, poses):
    if rows != list(motion_rows(poses)):
        raise ValueError('P4 observed motion values or sample links do not match the raw poses')
    for row, (_, time, pose, _), (_, next_time, next_pose, _) in zip(rows, poses, poses[1:]):
        delta, final = np.asarray(row['action']), np.asarray(next_pose)
        initial = np.asarray(pose)
        angle = float(np.linalg.norm(delta[3:6]))
        rotation = np.eye(3).ravel()
        if angle > 1e-12:
            quat = np.zeros(4)
            mujoco.mju_axisAngle2Quat(quat, delta[3:6]/angle, angle)
            mujoco.mju_quat2Mat(rotation, quat)
        if (not math.isclose(next_time-time, .1, rel_tol=0, abs_tol=1e-8)
                or not np.allclose(initial[:3, 3]+delta[:3], final[:3, 3], atol=1e-9, rtol=0)
                or not np.allclose(rotation.reshape(3, 3)@initial[:3, :3], final[:3, :3], atol=1e-8, rtol=0)):
            raise ValueError('P4 observed 7D motion did not reconstruct the next solver pose')
