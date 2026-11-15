"""Export a verified P4 OSC dataset to per-split HDF5 training views and check them independently.

  export_p4_dataset.py --dataset outputs/p4d_dataset_v1 [--out DIR]

Only demonstrations whose outcome matched the plan are exported. Normalization statistics
come from the train split alone. Every exported array is compared with the raw episode
through a second code path (OpenCV image decode, direct qpos addressing, re-aggregation).
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SCHEMA = 'robolab.p4_training_export.v1'


def _setup():
    os.environ.update(MUJOCO_GL='egl', PYOPENGL_PLATFORM='egl', PYTHONDONTWRITEBYTECODE='1')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))


def _aggregate_from_raw(demo, steps):
    """Second implementation of the per-finger labels, from the exported raw contact arrays."""
    import numpy as np
    from robolab.p4_dataset_export import LABEL_FORCE_N, ROLE_IDS

    raw = {key: demo['contacts_raw/'+key][()] for key in (
        'offsets', 'finger', 'role', 'other_part', 'load_bearing', 'normal_force_N', 'position_gripper_m', 'normal_gripper',
        'region')}
    present = np.zeros((steps, 2), bool)
    position = np.full((steps, 2, 3), np.nan)
    normal = np.full((steps, 2, 3), np.nan)
    force = np.zeros((steps, 2))
    part = np.full((steps, 2), -1, np.int8)
    pad = np.zeros((steps, 2), bool)
    obstacle = np.zeros((steps, 2), bool)
    target, environment, distractor = ROLE_IDS['finger_target'], ROLE_IDS['finger_environment'], ROLE_IDS['finger_distractor']
    for step in range(steps):
        rows = slice(raw['offsets'][step], raw['offsets'][step+1])
        strong = raw['load_bearing'][rows] & (raw['normal_force_N'][rows] >= LABEL_FORCE_N)
        for finger in (0, 1):
            mine = strong & (raw['finger'][rows] == finger)
            obstacle[step, finger] = bool(np.any(mine & np.isin(raw['role'][rows], (environment, distractor))))
            chosen = mine & (raw['role'][rows] == target)
            if not chosen.any():
                continue
            weight = raw['normal_force_N'][rows][chosen]
            present[step, finger] = True
            pad[step, finger] = bool(np.any(raw['region'][rows][chosen] == 0))
            position[step, finger] = weight @ raw['position_gripper_m'][rows][chosen]/weight.sum()
            vector = weight @ raw['normal_gripper'][rows][chosen]
            normal[step, finger] = vector/np.linalg.norm(vector)
            force[step, finger] = weight.sum()
            parts = set(raw['other_part'][rows][chosen].tolist())
            part[step, finger] = parts.pop() if len(parts) == 1 else 2
    return dict(present=present, pad=pad, position_gripper_m=position, normal_gripper=normal,
                normal_force_N=force, part=part, obstacle=obstacle)


def verify_split(path, rows, root, seen_cases):
    import cv2
    import h5py
    import mujoco
    import numpy as np
    from robolab.p4_dataset_export import ARM_JOINTS, EXPORT_SCHEMA, FINGER_JOINTS, GRIP_SITE, HAND_BODY

    report = dict(file=path.name, demos=0, steps=0, images_compared=0, max_state_position_error_m=0.,
                  max_hand_rotation_error=0., label_mismatches=0)
    with h5py.File(path, 'r') as handle:
        if handle.attrs['schema'] != EXPORT_SCHEMA:
            raise ValueError('Unexpected export schema')
        names = sorted(handle['data'], key=lambda name: int(name.split('_')[1]))
        if len(names) != len(rows) or handle['data'].attrs['num_demos'] != len(rows):
            raise ValueError(f'{path.name}: demo count differs from the index')
        for name, row in zip(names, rows):
            demo = handle['data'][name]
            if demo.attrs['case_id'] != row['case_id'] or row['case_id'] in seen_cases:
                raise ValueError(f'{path.name}: unexpected or duplicate case {demo.attrs["case_id"]}')
            seen_cases.add(row['case_id'])
            directory = root/row['path']
            trace = [json.loads(line) for line in (directory/'actions_and_contacts.jsonl').read_bytes().splitlines()]
            frames = [json.loads(line) for line in (directory/'frames.jsonl').read_bytes().splitlines()]
            steps = len(trace)
            model = mujoco.MjModel.from_xml_path(str(directory/'model.xml'))
            data = mujoco.MjData(model)
            fingers = [int(model.joint(joint).qposadr[0]) for joint in FINGER_JOINTS]
            arm = [int(model.joint(joint).qposadr[0]) for joint in ARM_JOINTS]
            state = demo['obs/state'][()]
            if state.shape != (steps, 8) or demo['actions'].shape != (steps, 7) or demo.attrs['num_samples'] != steps:
                raise ValueError(f'{row["case_id"]}: exported shapes differ')
            if not np.array_equal(demo['actions'][()], np.asarray([r['action_normalized_7d'] for r in trace])):
                raise ValueError(f'{row["case_id"]}: actions differ from the applied commands')
            for step, (record, frame) in enumerate(zip(trace, frames)):
                sample = record['sample']
                qpos = np.asarray(sample['state_at_solver']['qpos'])
                pose = np.asarray(sample['T_world_gripper'])
                error = float(np.max(np.abs(state[step, :3]-pose[:3, 3])))
                if error > 1e-12 or not np.array_equal(state[step, 6:], qpos[fingers]):
                    raise ValueError(f'{row["case_id"]}: state position or gripper differs at {step}')
                if not np.array_equal(demo['obs/joint_states'][step], qpos[arm]):
                    raise ValueError(f'{row["case_id"]}: joint states differ at {step}')
                if not np.array_equal(demo['states'][step], np.r_[qpos, sample['state_at_solver']['qvel']]):
                    raise ValueError(f'{row["case_id"]}: simulator state differs at {step}')
                data.qpos[:] = qpos
                mujoco.mj_kinematics(model, data)
                if not np.allclose(data.site_xpos[model.site(GRIP_SITE).id], state[step, :3], atol=1e-12, rtol=0):
                    raise ValueError(f'{row["case_id"]}: grip site differs at {step}')
                vector = state[step, 3:6]
                angle = float(np.linalg.norm(vector))
                quat, rotation = np.array([1., 0, 0, 0]), np.zeros(9)
                if angle > 0:
                    mujoco.mju_axisAngle2Quat(quat, vector/angle, angle)
                mujoco.mju_quat2Mat(rotation, quat)
                rotation_error = float(np.max(np.abs(rotation.reshape(3, 3)-data.xmat[model.body(HAND_BODY).id].reshape(3, 3))))
                report['max_state_position_error_m'] = max(report['max_state_position_error_m'], error)
                report['max_hand_rotation_error'] = max(report['max_hand_rotation_error'], rotation_error)
                if rotation_error > 1e-6:
                    raise ValueError(f'{row["case_id"]}: axis-angle does not reproduce the hand orientation at {step}')
                for camera, key, group in (('agentview', 'agentview_rgb', 'agentview'),
                                           ('robot0_eye_in_hand', 'eye_in_hand_rgb', 'eye_in_hand')):
                    record_image = frame['images'][camera]
                    decoded = cv2.imread(str(directory/record_image['file']), cv2.IMREAD_COLOR)[:, :, ::-1]
                    if not np.array_equal(demo['obs/'+key][step], decoded):
                        raise ValueError(f'{row["case_id"]}: {key} differs from the PNG at {step}')
                    if (not np.array_equal(demo[f'camera/{group}/T_world_camera'][step], record_image['T_world_camera'])
                            or not np.array_equal(demo[f'camera/{group}/K'][()], record_image['K'])):
                        raise ValueError(f'{row["case_id"]}: {camera} calibration differs at {step}')
                    report['images_compared'] += 1
                counts = demo['contacts_raw/offsets'][step+1]-demo['contacts_raw/offsets'][step]
                if counts != len(sample['contacts']):
                    raise ValueError(f'{row["case_id"]}: raw contact count differs at {step}')
                if not np.array_equal(demo['labels/requested_center_gripper_m'][step], frame['requested_center_gripper_m']):
                    raise ValueError(f'{row["case_id"]}: requested part center differs at {step}')
            again = _aggregate_from_raw(demo, steps)
            for key, exported in (('present', 'labels/finger_contact/present'), ('pad', 'labels/finger_contact/pad'),
                                  ('position_gripper_m', 'labels/finger_contact/position_gripper_m'),
                                  ('normal_gripper', 'labels/finger_contact/normal_gripper'),
                                  ('normal_force_N', 'labels/finger_contact/normal_force_N'),
                                  ('part', 'labels/finger_contact/part'), ('obstacle', 'labels/finger_obstacle_contact')):
                if not np.allclose(again[key], demo[exported][()], atol=1e-12, rtol=0, equal_nan=True):
                    report['label_mismatches'] += 1
                    raise ValueError(f'{row["case_id"]}: {key} labels differ between aggregation paths')
            report['demos'] += 1
            report['steps'] += steps
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    _setup()
    from robolab.p4_dataset import SPLITS
    from robolab.p4_dataset_build import publish_json, sha256_file
    from robolab.p4_dataset_export import (ACTION_NAMES, SPLIT_FILE, STATE_NAMES, STATS_SCHEMA, iter_policy_frames,
                                           split_stats, write_split)

    root = args.dataset.resolve()
    out = (args.out or root/'export').resolve()
    index_path = root/'result.json'
    index = json.loads(index_path.read_bytes())
    gate = index['gate']
    if not (gate['all_planned_episodes_verified'] and gate['exact_replay'] and gate['split_audit_clean']):
        raise SystemExit('Dataset index is not fully verified; refusing to export')
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    index_sha = sha256_file(index_path)
    by_path = {row['path']: row for row in index['runs']}
    files, reports, seen = {}, {}, set()
    for split in SPLITS:
        rows = [by_path[path] for path in index['splits'][split]['successful_paths']]
        if not rows:
            continue
        path = out/SPLIT_FILE.format(split=split)
        steps = write_split(path, split, rows, root, index_sha, index['plan_sha256'], log=lambda line: None)
        reports[split] = verify_split(path, rows, root, seen)
        if reports[split]['steps'] != steps:
            raise ValueError('Export step count changed during verification')
        files[split] = dict(file=path.name, sha256=sha256_file(path), bytes=path.stat().st_size,
                            demos=len(rows), steps=steps)
        print(json.dumps(dict(event='split_exported', split=split, **files[split])), flush=True)
    train = out/SPLIT_FILE.format(split='train')
    stats = dict(schema=STATS_SCHEMA, computed_from='train split only', file=train.name, state_names=STATE_NAMES,
                 action_names=ACTION_NAMES, note='numpy over exported frames; re-run the training framework\'s own '
                 'normalization step after its transforms', norm_stats=split_stats(train))
    publish_json(out/'norm_stats.json', stats)
    if split_stats(train) != stats['norm_stats']:
        raise ValueError('Normalization statistics are not reproducible')
    frame = next(iter_policy_frames(train, image_convention='libero_rotated_180'))
    canonical = next(iter_policy_frames(train))
    import numpy as np
    loader = dict(keys=sorted(frame), image_shape=list(frame['image'].shape), state_dtype=str(frame['state'].dtype),
                  actions_dtype=str(frame['actions'].dtype), task=frame['task'],
                  rotated_equals_horizontal_flip=bool(np.array_equal(frame['image'], canonical['image'][:, ::-1])))
    manifest = dict(schema=MANIFEST_SCHEMA, source_index=str(index_path), source_index_sha256=index_sha,
                    plan_sha256=index['plan_sha256'], files=files, verification=reports,
                    norm_stats_sha256=sha256_file(out/'norm_stats.json'), loader_check=loader,
                    totals=dict(demos=sum(item['demos'] for item in files.values()),
                                steps=sum(item['steps'] for item in files.values()),
                                bytes=sum(item['bytes'] for item in files.values())),
                    lerobot_written=False, elapsed_wall_s=round(time.monotonic()-started, 3), passed=True)
    publish_json(out/'export_manifest.json', manifest)
    print(json.dumps(dict(event='finished', passed=True, totals=manifest['totals'], loader=loader)), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
