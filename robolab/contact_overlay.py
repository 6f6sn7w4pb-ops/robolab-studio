"""Draw native contact samples on their own kinematic pose, without a solver call."""
import math

import mujoco
import numpy as np


FILTERS = ('target', 'obstacle', 'pads', 'all')
CAMERAS = ('agentview', 'robot0_eye_in_hand', 'gripper')
IDENTITY = np.eye(3).ravel()
FINGER_COLORS = {'finger_1': (1., .44, .08, 1.), 'finger_2': (.12, .68, 1., 1.)}
FORCE_COLOR = (.88, .22, .98, 1.)
MAX_CONTACTS_DRAWN = 64


def validate_options(contact_filter, forces):
    if contact_filter not in FILTERS or type(forces) is not bool:
        raise ValueError('无效的 P3 接触显示设置')


def restore_solver_pose(model, data, frame):
    state = frame['state_at_solver']
    data.time = frame['solver_step_start_time_s']
    data.qpos[:] = state['qpos']
    data.qvel[:] = state['qvel']
    data.ctrl[:] = state['ctrl_native']
    mujoco.mj_kinematics(model, data)
    mujoco.mj_comPos(model, data)
    mujoco.mj_camlight(model, data)


def _matching(contact, contact_filter):
    return (contact_filter == 'all'
            or contact_filter == 'target' and contact['pair_role'] == 'finger_target'
            or contact_filter == 'obstacle' and contact['pair_role'] in ('finger_environment', 'finger_distractor')
            or contact_filter == 'pads' and contact['surface_region'] == 'pad')


def _geom(scene, kind, position, size, rgba):
    geom = scene.geoms[scene.ngeom]
    mujoco.mjv_initGeom(geom, kind, np.asarray(size), position, IDENTITY, np.asarray(rgba, dtype=np.float32))
    scene.ngeom += 1
    return geom


def _arrow(scene, start, end, width, rgba):
    kind = mujoco.mjtGeom.mjGEOM_ARROW
    geom = _geom(scene, kind, start, (0., 0., 0.), rgba)
    mujoco.mjv_connector(geom, kind, width, start, end)


def draw_contacts(scene, frame, contact_filter='target', forces=False):
    """World-space decorations only. Force vectors are original solver values."""
    contacts = [c for c in frame['contacts'] if _matching(c, contact_filter)]
    transform = np.asarray(frame['T_world_gripper'])
    origin = transform[:3, 3].copy()
    if scene.maxgeom-scene.ngeom >= 3:
        for axis, color in enumerate(((1., .1, .1, 1.), (.1, 1., .1, 1.), (.15, .3, 1., 1.))):
            _arrow(scene, origin, origin+.035*transform[:3, axis], .0015, color)
    drawn = 0
    for contact in contacts[:MAX_CONTACTS_DRAWN]:
        if scene.maxgeom-scene.ngeom < 2+int(forces):
            break
        point = np.asarray(contact['position_world_m'])
        normal = np.asarray(contact['normal_world_finger_to_other'])
        color = FINGER_COLORS[contact['finger']]
        _geom(scene, mujoco.mjtGeom.mjGEOM_SPHERE, point, (.003, 0., 0.), color)
        _arrow(scene, point, point+.025*normal, .0015, color)
        if forces and contact['load_bearing']:
            force = np.asarray(contact['force_on_other_world_N'])
            magnitude = math.hypot(*force)
            if magnitude > 0:
                _arrow(scene, point, point+(force/magnitude)*min(.07, .003*magnitude), .0011, FORCE_COLOR)
        drawn += 1
    return dict(solver_time_s=frame['solver_step_start_time_s'],
                end_time_s=frame['state_after_step_time_s'], sample_index=frame['sample_index'],
                total=len(frame['contacts']), matching=len(contacts), drawn=drawn,
                max_normal_force_N=max((c['normal_force_N'] for c in contacts), default=0.),
                source='native_euler_step')


def inspection_camera(frame):
    camera = mujoco.MjvCamera()
    camera.type = mujoco.mjtCamera.mjCAMERA_FREE
    camera.lookat[:] = np.asarray(frame['T_world_gripper'])[:3, 3]
    camera.distance, camera.azimuth, camera.elevation = .30, 135, -25
    return camera


def reveal_grasp(model, scene, frame):
    """Make only the close-up's gripper and target visual geometry translucent."""
    from .p3_contact import geoms_below

    site = model.site(frame['reference_site']).id
    gripper = int(model.body_parentid[model.site_bodyid[site]])
    target = model.body(frame['target_body']).id
    hidden = geoms_below(model, gripper) | geoms_below(model, target)
    for index in range(scene.ngeom):
        geom = scene.geoms[index]
        if geom.objtype == mujoco.mjtObj.mjOBJ_GEOM and geom.objid in hidden:
            geom.rgba[3] = min(.24, geom.rgba[3])
            geom.transparent = 1
