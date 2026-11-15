"""Training view of a verified P4 OSC dataset (LIBERO/robomimic-style HDF5).

One file per split holds only teacher demonstrations whose outcome matched expectation.
Raw episodes stay the provenance record; every exported array is re-derived and compared.

Conventions (also written into file attributes):
- Images: 224x224 RGB uint8, top-left origin, rendered at the same solver input state as the
  contact labels ("canonical"). openpi's LIBERO example rotates raw robosuite frames by 180
  degrees; raw frames are bottom-left origin, so that convention equals canonical[:, ::-1].
  A horizontal flip changes the pixel geometry, so geometry modules must use canonical
  images with the stored K / T_world_camera, or transform K consistently.
- state (8): robosuite robot0_eef_pos (grip site, world, m), axis-angle of robot0_eef_quat
  (right_hand body, robosuite quat2axisangle), robot0_gripper_qpos (2 finger joints, m).
  This is the layout used by openpi's LIBERO example.
- actions (7): the exact normalized OSC_POSE command sent in that 50 ms tick,
  [dx, dy, dz, rx, ry, rz, gripper] in [-1, 1]; gripper -1 open, +1 close.
"""
import hashlib
import json
from pathlib import Path

import numpy as np

EXPORT_SCHEMA = 'robolab.p4_training_view.v1'
STATS_SCHEMA = 'robolab.p4_norm_stats.v1'
STATE_NAMES = ('eef_pos_x', 'eef_pos_y', 'eef_pos_z', 'eef_axis_angle_x', 'eef_axis_angle_y',
               'eef_axis_angle_z', 'gripper_qpos_finger1', 'gripper_qpos_finger2')
ACTION_NAMES = ('dx', 'dy', 'dz', 'rx', 'ry', 'rz', 'gripper')
PART_IDS = {'thick': 0, 'thin': 1}
ROLE_IDS = {'finger_target': 0, 'finger_environment': 1, 'finger_distractor': 2, 'finger_robot_other': 3}
REGION_IDS = {'pad': 0, 'other_finger': 1}
SURFACE_IDS = {'floor': 0, 'p4_distractor_box': 1}
CAMERA_KEYS = {'agentview': 'agentview_rgb', 'robot0_eye_in_hand': 'eye_in_hand_rgb'}
LABEL_FORCE_N = .2           # same threshold as the P4 part verdict
IMAGE_SHAPE = (224, 224, 3)
FINGER_JOINTS = ('gripper0_finger_joint1', 'gripper0_finger_joint2')
ARM_JOINTS = tuple(f'robot0_joint{i}' for i in range(1, 8))
HAND_BODY = 'robot0_right_hand'
GRIP_SITE = 'gripper0_grip_site'
SPLIT_FILE = 'p4_osc_{split}.hdf5'


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def finger_labels(contacts):
    """Aggregate raw finger contacts into fixed-size per-finger targets.

    Only load-bearing finger-target contacts at or above LABEL_FORCE_N count. Position and
    normal are normal-force weighted means in the gripper frame; the normal is re-normalized.
    Absent contacts give NaN geometry and zero force, never a fabricated point.
    """
    present = np.zeros(2, bool)
    pad = np.zeros(2, bool)
    position = np.full((2, 3), np.nan)
    normal = np.full((2, 3), np.nan)
    force = np.zeros(2)
    part = np.full(2, -1, np.int8)
    obstacle = np.zeros(2, bool)
    weights = [[] for _ in range(2)]
    for contact in contacts:
        finger = 0 if contact['finger'] == 'finger_1' else 1
        if not contact['load_bearing'] or contact['normal_force_N'] < LABEL_FORCE_N:
            continue
        role = contact['pair_role']
        if role in ('finger_environment', 'finger_distractor'):
            obstacle[finger] = True
        if role != 'finger_target':
            continue
        weights[finger].append(contact)
    for finger, rows in enumerate(weights):
        if not rows:
            continue
        w = np.array([row['normal_force_N'] for row in rows])
        present[finger] = True
        pad[finger] = any(row['surface_region'] == 'pad' for row in rows)
        position[finger] = (w[:, None]*np.array([row['position_gripper_m'] for row in rows])).sum(0)/w.sum()
        vector = (w[:, None]*np.array([row['normal_gripper_finger_to_other'] for row in rows])).sum(0)
        normal[finger] = vector/np.linalg.norm(vector)
        force[finger] = w.sum()
        parts = {PART_IDS[row['other_geom_name'].rsplit('_', 1)[1]] for row in rows}
        part[finger] = parts.pop() if len(parts) == 1 else 2
    return dict(present=present, pad=pad, position_gripper_m=position, normal_gripper=normal,
                normal_force_N=force, part=part, obstacle_contact=obstacle)


def raw_contact_rows(contacts):
    for contact in contacts:
        other = contact['other_geom_name']
        yield dict(finger=0 if contact['finger'] == 'finger_1' else 1, role=ROLE_IDS[contact['pair_role']],
                   region=REGION_IDS[contact['surface_region']],
                   other_part=PART_IDS.get(other.rsplit('_', 1)[1], -1) if contact['pair_role'] == 'finger_target' else -1,
                   load_bearing=bool(contact['load_bearing']), constraint_active=bool(contact['constraint_active']),
                   position_gripper_m=contact['position_gripper_m'],
                   normal_gripper=contact['normal_gripper_finger_to_other'],
                   force_gripper_N=contact['force_on_other_gripper_N'], normal_force_N=contact['normal_force_N'])


class StateReader:
    """robosuite-compatible proprioception from a recorded qpos vector."""
    def __init__(self, model_xml):
        import mujoco
        from robosuite.utils.transform_utils import convert_quat, quat2axisangle
        self.mujoco = mujoco
        self.convert_quat, self.quat2axisangle = convert_quat, quat2axisangle
        self.model = mujoco.MjModel.from_xml_string(model_xml)
        self.data = mujoco.MjData(self.model)
        self.site = self.model.site(GRIP_SITE).id
        self.hand = self.model.body(HAND_BODY).id
        self.fingers = [int(self.model.joint(name).qposadr[0]) for name in FINGER_JOINTS]
        self.arm = [int(self.model.joint(name).qposadr[0]) for name in ARM_JOINTS]

    def __call__(self, qpos):
        self.data.qpos[:] = qpos
        self.mujoco.mj_kinematics(self.model, self.data)
        quat = self.convert_quat(self.data.xquat[self.hand].copy(), to='xyzw')
        position = self.data.site_xpos[self.site].copy()
        state = np.r_[position, self.quat2axisangle(quat), np.asarray(qpos)[self.fingers]]
        return state, np.asarray(qpos)[self.arm], quat, self.data.xmat[self.hand].reshape(3, 3).copy()


def _read_episode(directory):
    from PIL import Image

    manifest = json.loads((directory/'manifest.json').read_bytes())
    trace = [json.loads(line) for line in (directory/'actions_and_contacts.jsonl').read_bytes().splitlines()]
    frames = [json.loads(line) for line in (directory/'frames.jsonl').read_bytes().splitlines()]
    images = {key: np.empty((len(frames),)+IMAGE_SHAPE, np.uint8) for key in CAMERA_KEYS.values()}
    for index, frame in enumerate(frames):
        for camera, key in CAMERA_KEYS.items():
            record = frame['images'][camera]
            path = directory/record['file']
            if _sha256(path) != record['sha256']:
                raise ValueError(f'{directory.name}: {record["file"]} changed since recording')
            with Image.open(path) as image:
                images[key][index] = np.asarray(image.convert('RGB'))
    return manifest, trace, frames, images


def episode_arrays(directory):
    """All exported arrays of one raw episode, derived only from its files."""
    manifest, trace, frames, images = _read_episode(directory)
    reader = StateReader((directory/'model.xml').read_text(encoding='utf-8'))
    T = len(trace)
    out = dict(images=images)
    states, joints, poses, labels, raw = [], [], [], [], []
    for row, frame in zip(trace, frames):
        sample = row['sample']
        qpos = sample['state_at_solver']['qpos']
        state, arm, _, _ = reader(qpos)
        states.append(state)
        joints.append(arm)
        poses.append(np.asarray(sample['T_world_gripper']))
        labels.append(finger_labels(sample['contacts']))
        raw.append(list(raw_contact_rows(sample['contacts'])))
    out['state'] = np.asarray(states)
    out['joint_states'] = np.asarray(joints)
    out['actions'] = np.asarray([row['action_normalized_7d'] for row in trace], dtype=np.float64)
    out['sim_states'] = np.asarray([np.r_[row['sample']['state_at_solver']['qpos'], row['sample']['state_at_solver']['qvel']]
                                    for row in trace])
    out['time_s'] = np.asarray([row['action_time_s'] for row in trace])
    out['T_world_gripper'] = np.asarray(poses)
    for key in ('present', 'pad', 'position_gripper_m', 'normal_gripper', 'normal_force_N', 'part', 'obstacle_contact'):
        out['finger_'+key] = np.asarray([label[key] for label in labels])
    out['requested_center_gripper_m'] = np.asarray([frame['requested_center_gripper_m'] for frame in frames])
    out['actual_part'] = np.asarray([PART_IDS.get(frame['actual_part'], -1) for frame in frames], np.int8)
    out['lift_m'] = np.asarray([row['observed']['lift_m'] for row in trace])
    out['nearest_point_gripper_m'] = np.asarray([frame['nearest_obstacle']['point_gripper_m'] for frame in frames])
    out['nearest_signed_distance_m'] = np.asarray([frame['nearest_obstacle']['signed_distance_m'] for frame in frames])
    out['nearest_surface'] = np.asarray([SURFACE_IDS[frame['nearest_obstacle']['geom']] for frame in frames], np.int8)
    for camera in CAMERA_KEYS:
        out[f'camera_{camera}_T'] = np.asarray([frame['images'][camera]['T_world_camera'] for frame in frames])
        out[f'camera_{camera}_K'] = np.asarray(frames[0]['images'][camera]['K'])
    offsets = np.cumsum([0]+[len(items) for items in raw])
    flat = [item for items in raw for item in items]
    out['contacts_offsets'] = offsets.astype(np.int64)
    for key, dtype in (('finger', np.int8), ('role', np.int8), ('region', np.int8), ('other_part', np.int8),
                       ('load_bearing', bool), ('constraint_active', bool), ('normal_force_N', np.float64)):
        out['contacts_'+key] = np.asarray([item[key] for item in flat], dtype=dtype)
    for key in ('position_gripper_m', 'normal_gripper', 'force_gripper_N'):
        out['contacts_'+key] = np.asarray([item[key] for item in flat], dtype=np.float64).reshape(-1, 3)
    if not (len(frames) == T == len(out['state'])) or any(frame['sample_index'] != i for i, frame in enumerate(frames)):
        raise ValueError(f'{directory.name}: frame/action count or order mismatch')
    return manifest, out


_DEMO_LAYOUT = {
    'obs/agentview_rgb': 'images:agentview_rgb', 'obs/eye_in_hand_rgb': 'images:eye_in_hand_rgb',
    'obs/state': 'state', 'obs/joint_states': 'joint_states', 'actions': 'actions', 'states': 'sim_states',
    'time_s': 'time_s', 'labels/T_world_gripper': 'T_world_gripper',
    'labels/finger_contact/present': 'finger_present', 'labels/finger_contact/pad': 'finger_pad',
    'labels/finger_contact/position_gripper_m': 'finger_position_gripper_m',
    'labels/finger_contact/normal_gripper': 'finger_normal_gripper',
    'labels/finger_contact/normal_force_N': 'finger_normal_force_N', 'labels/finger_contact/part': 'finger_part',
    'labels/finger_obstacle_contact': 'finger_obstacle_contact',
    'labels/requested_center_gripper_m': 'requested_center_gripper_m', 'labels/actual_part': 'actual_part',
    'labels/lift_m': 'lift_m', 'labels/nearest_obstacle/point_gripper_m': 'nearest_point_gripper_m',
    'labels/nearest_obstacle/signed_distance_m': 'nearest_signed_distance_m',
    'labels/nearest_obstacle/surface': 'nearest_surface',
    'camera/agentview/K': 'camera_agentview_K', 'camera/agentview/T_world_camera': 'camera_agentview_T',
    'camera/eye_in_hand/K': 'camera_robot0_eye_in_hand_K',
    'camera/eye_in_hand/T_world_camera': 'camera_robot0_eye_in_hand_T',
    'contacts_raw/offsets': 'contacts_offsets', 'contacts_raw/finger': 'contacts_finger',
    'contacts_raw/role': 'contacts_role', 'contacts_raw/region': 'contacts_region',
    'contacts_raw/other_part': 'contacts_other_part', 'contacts_raw/load_bearing': 'contacts_load_bearing',
    'contacts_raw/constraint_active': 'contacts_constraint_active',
    'contacts_raw/position_gripper_m': 'contacts_position_gripper_m',
    'contacts_raw/normal_gripper': 'contacts_normal_gripper', 'contacts_raw/force_gripper_N': 'contacts_force_gripper_N',
    'contacts_raw/normal_force_N': 'contacts_normal_force_N',
}


def _array(arrays, source):
    if source.startswith('images:'):
        return arrays['images'][source.split(':', 1)[1]]
    return arrays[source]


def file_attributes(split, index_sha256, plan_sha256):
    return dict(schema=EXPORT_SCHEMA, split=split, source_index_sha256=index_sha256, plan_sha256=plan_sha256,
                control_frequency_hz=20, image_convention='canonical_top_left_rgb',
                libero_rotated_180_equivalent='image[:, ::-1]', state_names=json.dumps(STATE_NAMES),
                action_names=json.dumps(ACTION_NAMES), action_space='robosuite_1_4_1_panda_osc_pose_normalized_7d_v1',
                part_ids=json.dumps(PART_IDS), contact_role_ids=json.dumps(ROLE_IDS),
                contact_region_ids=json.dumps(REGION_IDS), nearest_surface_ids=json.dumps(SURFACE_IDS),
                label_force_threshold_N=LABEL_FORCE_N, is_vla=False, teacher='simulator_geometry_rule_osc')


def write_split(path, split, rows, root, index_sha256, plan_sha256, *, log=print):
    import h5py

    temporary = path.with_name(path.name+'.tmp')
    total = 0
    with h5py.File(temporary, 'w') as handle:
        for key, value in file_attributes(split, index_sha256, plan_sha256).items():
            handle.attrs[key] = value
        data = handle.create_group('data')
        for number, row in enumerate(rows):
            manifest, arrays = episode_arrays(root/row['path'])
            demo = data.create_group(f'demo_{number}')
            for target, source in _DEMO_LAYOUT.items():
                value = _array(arrays, source)
                options = dict(chunks=(1,)+IMAGE_SHAPE, compression='gzip', compression_opts=4) if value.ndim == 4 else {}
                demo.create_dataset(target, data=value, **options)
            steps = len(arrays['actions'])
            demo.create_dataset('dones', data=np.r_[np.zeros(steps-1, np.uint8), 1])
            demo.attrs.update(num_samples=steps, case_id=row['case_id'], scene_id=row['scene_id'],
                              family=row['family'], split=split, requested_part=row['part'],
                              requested_part_id=PART_IDS[row['part']], phrasing=row['phrasing'],
                              instruction_en=manifest['instruction']['en'], instruction_zh=manifest['instruction']['zh'],
                              source_path=row['path'], source_manifest_sha256=row['manifest_sha256'],
                              model_xml_sha256=manifest['model_xml_sha256'],
                              outcome=json.dumps(manifest['outcome'], sort_keys=True))
            total += steps
            log(json.dumps(dict(event='exported', split=split, demo=number, case_id=row['case_id'], steps=steps)))
        data.attrs.update(total=total, num_demos=len(rows))
    temporary.replace(path)
    return total


def norm_stats(values):
    values = np.asarray(values, dtype=np.float64)
    return dict(mean=values.mean(0).tolist(), std=values.std(0).tolist(), min=values.min(0).tolist(),
                max=values.max(0).tolist(), q01=np.quantile(values, .01, axis=0).tolist(),
                q99=np.quantile(values, .99, axis=0).tolist(), count=int(len(values)))


def split_stats(path):
    import h5py

    with h5py.File(path, 'r') as handle:
        demos = [handle['data'][name] for name in sorted(handle['data'], key=lambda name: int(name.split('_')[1]))]
        state = np.concatenate([demo['obs/state'][()] for demo in demos])
        actions = np.concatenate([demo['actions'][()] for demo in demos])
    return dict(state=norm_stats(state), actions=norm_stats(actions))


def iter_policy_frames(path, *, language='en', image_convention='canonical'):
    """Yield frames shaped like openpi's LIBERO LeRobot features: image, wrist_image, state, actions, task."""
    import h5py

    if image_convention not in ('canonical', 'libero_rotated_180'):
        raise ValueError('image_convention must be canonical or libero_rotated_180')
    flip = image_convention == 'libero_rotated_180'
    with h5py.File(path, 'r') as handle:
        if handle.attrs['schema'] != EXPORT_SCHEMA:
            raise ValueError('Not a P4 training view')
        for name in sorted(handle['data'], key=lambda name: int(name.split('_')[1])):
            demo = handle['data'][name]
            task = demo.attrs['instruction_'+language]
            agent, wrist = demo['obs/agentview_rgb'], demo['obs/eye_in_hand_rgb']
            state = demo['obs/state'][()].astype(np.float32)
            actions = demo['actions'][()].astype(np.float32)
            for step in range(len(actions)):
                image, wrist_image = agent[step], wrist[step]
                if flip:
                    image, wrist_image = image[:, ::-1], wrist_image[:, ::-1]
                yield dict(image=np.ascontiguousarray(image), wrist_image=np.ascontiguousarray(wrist_image),
                           state=state[step], actions=actions[step], task=task,
                           episode=demo.attrs['case_id'], frame_index=step)
