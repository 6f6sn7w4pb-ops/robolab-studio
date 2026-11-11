"""P3-A native Euler solver samples. Read-only, opt-in, no file writer/model.

The input state is copied BEFORE the tail physics step. Contacts, force and
derived poses are copied immediately AFTER that step. No mj_forward is called
on live or copied data to invent an original solver force.
"""
import copy
import hashlib
import json
import math

import mujoco
import numpy as np

from .contact_geometry import (rigid_transform, point_to_local, vector_to_local,
                               contact_force_world, finite_array)


class ContactSamplingUnsupported(ValueError):
    pass


def geoms_below(model, body):
    result = set()
    for geom in range(model.ngeom):
        ancestor = int(model.geom_bodyid[geom])
        while ancestor and ancestor != body:
            ancestor = int(model.body_parentid[ancestor])
        if ancestor == body: result.add(geom)
    return result


class PandaContactProfile:
    profile_id = 'libero_panda_parallel_jaw_pad.v1'
    reference_name = 'gripper0_grip_site'
    pad_names = ('gripper0_finger1_pad_collision', 'gripper0_finger2_pad_collision')

    def __init__(self, engine, target_body):
        model, robot = engine.model, engine.robot
        if engine.robot_adapter != 'panda_joint_pd' or len(robot.gids) != 2:
            raise ContactSamplingUnsupported('contact_robot_not_supported')
        self.site = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, self.reference_name)
        if self.site < 0 or self.site != robot.site:
            raise ContactSamplingUnsupported('contact_reference_missing')
        target = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, target_body)
        if target <= 0: raise ContactSamplingUnsupported('contact_target_missing')
        self.target_body = target_body
        self.target_geoms = geoms_below(model, target)
        self.finger_geoms = [geoms_below(model, int(model.jnt_bodyid[jid])) for jid in robot.gids]
        self.pads = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name) for name in self.pad_names]
        if not self.target_geoms or any(not group for group in self.finger_geoms):
            raise ContactSamplingUnsupported('contact_geometry_missing')
        if self.finger_geoms[0] & self.finger_geoms[1] or self.target_geoms & set.union(*self.finger_geoms):
            raise ContactSamplingUnsupported('contact_geometry_overlap')
        for index, pad in enumerate(self.pads):
            if pad < 0 or pad not in self.finger_geoms[index] or model.geom_type[pad] != mujoco.mjtGeom.mjGEOM_BOX:
                raise ContactSamplingUnsupported('contact_pad_missing')
        robot_root = int(model.jnt_bodyid[robot.jids[0]])
        while model.body_parentid[robot_root]: robot_root = int(model.body_parentid[robot_root])
        self.robot_geoms = geoms_below(model, robot_root)
        self.dynamic_geoms = set()
        for joint in range(model.njnt):
            if model.jnt_type[joint] == mujoco.mjtJoint.mjJNT_FREE:
                self.dynamic_geoms.update(geoms_below(model, int(model.jnt_bodyid[joint])))
        self.description = {'profile_id': self.profile_id, 'reference_site': self.reference_name,
            'target_body': target_body, 'position_role': 'solver_midpoint',
            'pad_region_definition': 'entire_named_pad_collision_box_not_a_surface_patch',
            'target_geoms': [model.geom(i).name for i in sorted(self.target_geoms)], 'fingers': []}
        for index, pad in enumerate(self.pads):
            body = int(model.geom_bodyid[pad])
            self.description['fingers'].append({'finger': f'finger_{index+1}',
                'body': model.body(int(model.jnt_bodyid[robot.gids[index]])).name,
                'pad_geom': model.geom(pad).name, 'pad_body': model.body(body).name,
                'pad_half_size_m': model.geom_size[pad].tolist(),
                'pad_position_in_body_m': model.geom_pos[pad].tolist(),
                'pad_quaternion_in_body_wxyz': model.geom_quat[pad].tolist(),
                'all_geoms': [model.geom(i).name for i in sorted(self.finger_geoms[index])]})
        canonical = json.dumps(self.description, sort_keys=True, allow_nan=False, separators=(',', ':'))
        self.sha256 = hashlib.sha256(canonical.encode()).hexdigest()

    def classify(self, geom1, geom2):
        hits = []
        for finger, group in enumerate(self.finger_geoms):
            for own, other in ((geom1, geom2), (geom2, geom1)):
                if own in group:
                    role = ('finger_target' if other in self.target_geoms else
                            'finger_robot_other' if other in self.robot_geoms else
                            'finger_distractor' if other in self.dynamic_geoms else 'finger_environment')
                    hits.append((finger, own, other, role, 'pad' if own == self.pads[finger] else 'other_finger'))
        # A finger/finger or same-finger pair has two possible roles. Keep one
        # deterministic representation of this physical contact, not two rows.
        return hits[:1]


class ContactSampler:
    def __init__(self, engine, target_body='alphabet_soup_1_main', force_epsilon_N=1e-6):
        if isinstance(force_epsilon_N, bool) or not isinstance(force_epsilon_N, (int, float)) or not math.isfinite(force_epsilon_N) or force_epsilon_N < 0:
            raise ValueError('Invalid contact force epsilon')
        self.engine = engine
        self.model = engine.model
        self.check_native_capability()
        self.profile = PandaContactProfile(engine, target_body)
        self.force_epsilon_N = float(force_epsilon_N)
        self.sequence = 0
        self.latest = None
        self.availability = 'not_sampled'
        self.reason = 'awaiting_native_physics_step'

    def check_native_capability(self):
        model = self.engine.model
        if model is not self.model: raise ContactSamplingUnsupported('contact_model_changed')
        if mujoco.__version__ != '2.3.7': raise ContactSamplingUnsupported('contact_version_not_verified')
        if model.opt.integrator != mujoco.mjtIntegrator.mjINT_EULER:
            raise ContactSamplingUnsupported('contact_integrator_not_supported')
        if model.na or model.nmocap: raise ContactSamplingUnsupported('contact_actuator_or_mocap_not_supported')
        if mujoco.get_mjcb_control() is not None: raise ContactSamplingUnsupported('contact_control_callback_not_supported')
        flags = int(mujoco.mjtDisableBit.mjDSBL_CONTACT) | int(mujoco.mjtDisableBit.mjDSBL_CONSTRAINT)
        if int(model.opt.disableflags) & flags: raise ContactSamplingUnsupported('contact_solver_disabled')
        if not math.isfinite(float(model.opt.timestep)) or model.opt.timestep <= 0:
            raise ContactSamplingUnsupported('contact_timestep_invalid')

    def invalidate(self, reason, availability='stale', reset_sequence=False):
        self.latest = None
        self.availability, self.reason = availability, reason
        if reset_sequence: self.sequence = 0

    def prepare(self):
        self.check_native_capability()
        data = self.engine.data
        qpos = finite_array(data.qpos, (self.model.nq,), 'solver qpos').copy()
        qvel = finite_array(data.qvel, (self.model.nv,), 'solver qvel').copy()
        ctrl = finite_array(data.ctrl, (self.model.nu,), 'solver control').copy()
        return {'time': float(data.time), 'dt': float(self.model.opt.timestep), 'qpos': qpos, 'qvel': qvel, 'ctrl': ctrl}

    def capture(self, before, *, sampling_mode='control_tail_substep'):
        data, model = self.engine.data, self.model
        if not math.isclose(float(data.time)-before['time'], before['dt'], rel_tol=0, abs_tol=1e-9):
            raise RuntimeError('Contact solver sampling time mismatch')
        site = self.profile.site
        transform = rigid_transform(data.site_xmat[site].reshape(3, 3), data.site_xpos[site])
        rows = []
        for index, contact in enumerate(data.contact):
            first, second = int(contact.geom1), int(contact.geom2)
            hits = self.profile.classify(first, second)
            if not hits: continue
            wrench = np.zeros(6)
            mujoco.mj_contactForce(model, data, index, wrench)
            axes = contact.frame.reshape(3, 3).copy()
            position = finite_array(contact.pos, (3,), 'contact point').copy()
            for finger, own, other, role, region in hits:
                force, normal = contact_force_world(axes, wrench, other == second)
                normal_force = float(np.dot(force, normal))
                if normal_force < -1e-8: raise RuntimeError('Negative normal contact force')
                active = int(contact.efc_address) >= 0 and int(contact.exclude) == 0
                if not active and np.linalg.norm(wrench) > 1e-12:
                    raise RuntimeError('Inactive contact has a nonzero solver force')
                normal_force = max(0., normal_force)
                rows.append({'contact_index': index, 'geom1_id': first, 'geom2_id': second,
                    'geom1_name': model.geom(first).name, 'geom2_name': model.geom(second).name,
                    'finger': f'finger_{finger+1}', 'finger_geom_name': model.geom(own).name,
                    'other_geom_name': model.geom(other).name, 'pair_role': role, 'surface_region': region,
                    'position_role': 'solver_midpoint', 'position_world_m': position.tolist(),
                    'position_gripper_m': point_to_local(transform, position).tolist(),
                    'normal_world_finger_to_other': normal.tolist(),
                    'normal_gripper_finger_to_other': vector_to_local(transform, normal).tolist(),
                    'native_contact_frame_rows': axes.tolist(), 'native_wrench_contact': wrench.tolist(),
                    'force_on_other_world_N': force.tolist(),
                    'force_on_other_gripper_N': vector_to_local(transform, force).tolist(),
                    'normal_force_N': normal_force, 'distance_m': float(contact.dist),
                    'exclude': int(contact.exclude), 'efc_address': int(contact.efc_address),
                    'contact_dimension': int(contact.dim), 'constraint_active': active,
                    'load_bearing': bool(active and normal_force > self.force_epsilon_N)})
        cameras = []
        for name, calibration in self.engine.camera_calibration().items():
            cameras.append(dict(calibration, name=name, image_width=224, image_height=224, calibration_only=True))
        runner = self.engine.execution_runner
        recorder = getattr(runner, 'recorder', None) if runner and not runner.closed else None
        self.latest = {'schema': 'robolab.p3_contact_sample.v1', 'record_origin': 'native_euler_step',
            'sample_index': self.sequence, 'episode_id': recorder.directory.name if recorder else None,
            'p2_trajectory_row_index': recorder.count if recorder else None,
            'sampling_mode': sampling_mode, 'integrator': 'Euler',
            'solver_step_start_time_s': before['time'], 'state_after_step_time_s': float(data.time),
            'physics_dt_s': before['dt'], 'force_epsilon_N': self.force_epsilon_N,
            'reference_site': self.profile.reference_name, 'T_world_gripper': transform.tolist(),
            'state_at_solver': {'phase': 'solver_input_state', 'qpos': before['qpos'].tolist(),
                                'qvel': before['qvel'].tolist(), 'ctrl_native': before['ctrl'].tolist()},
            'target_body': self.profile.target_body, 'profile_sha256': self.profile.sha256,
            'camera_calibration': cameras, 'original_rgb_recorded': False,
            'scene_contact_count': int(data.ncon), 'contacts': rows}
        # Catch non-finite values in all output fields, including camera transforms.
        json.dumps(self.latest, allow_nan=False)
        self.sequence += 1
        self.availability, self.reason = 'available', None

    def sample(self):
        return copy.deepcopy(self.latest)

    def status(self):
        frame = self.latest
        counts = {finger: {'target_pad_contacts': 0, 'target_normal_force_N': 0.} for finger in ('finger_1', 'finger_2')}
        if frame:
            for contact in frame['contacts']:
                if contact['pair_role'] == 'finger_target':
                    counts[contact['finger']]['target_normal_force_N'] += contact['normal_force_N']
                    if contact['surface_region'] == 'pad': counts[contact['finger']]['target_pad_contacts'] += 1
        return {'enabled': True, 'availability': self.availability, 'reason': self.reason,
                'source': 'native_euler_step', 'sample_count': self.sequence,
                'solver_time_s': frame['solver_step_start_time_s'] if frame else None,
                'state_after_step_time_s': frame['state_after_step_time_s'] if frame else None,
                'captured_contact_count': len(frame['contacts']) if frame else None,
                'target_body': self.profile.target_body, 'reference_site': self.profile.reference_name,
                'profile_sha256': self.profile.sha256, 'fingers': counts}
