"""Export original solver labels and simulated RGB rendered at that solver pose."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image

from .contact_geometry import point_to_local
from .contact_overlay import restore_solver_pose
from .contact_store import canonical_json
from .p4_parts import (CONTROL_PERIOD_S, DISTRACTOR, GEOM_PART, INSTRUCTIONS, MIN_FORCE_N,
                       REQUIRED_CONTACT_S, REQUIRED_HOLD_S, REQUIRED_LIFT_M, TARGET, PartEvidence,
                       PartTeacher, SteppedBar)
from .p4_pilot_scene import TEST_PROMPTS
from .p4_pilot_labels import MOTION_FILE, MOTION_SPACE, check_motion, motion_rows, nearest_obstacle


SCHEMA = 'robolab.p4_part_grasp.v1'
PILOT_SCHEMA = 'robolab.p4_part_grasp.v2'
CAMERAS = ('agentview', 'robot0_eye_in_hand')
IMAGE_STRIDE = 5


def sha256(path):
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def publish_json(path, value):
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_bytes(canonical_json(value)+b'\n')
    temporary.replace(path)


def project_point(calibration, point):
    transform = np.asarray(calibration['T_world_camera'])
    cam_point = transform[:3, :3].T @ (np.asarray(point)-transform[:3, 3])
    if cam_point[2] <= 0:
        return {'uv_px': None, 'in_frame': False, 'depth_m': float(cam_point[2])}
    uv = (np.asarray(calibration['K']) @ cam_point)[:2]/cam_point[2]
    return {'uv_px': uv.tolist(), 'depth_m': float(cam_point[2]),
            'in_frame': bool(-.5 <= uv[0] < 223.5 and -.5 <= uv[1] < 223.5)}


def verify_rgb_pair(directory, frame, sample):
    if (frame['sample_index'] != sample['sample_index']
            or frame['solver_time_s'] != sample['solver_step_start_time_s']
            or set(frame['images']) != set(CAMERAS)):
        raise ValueError('P4 frame and original sample mismatch')
    for stored in sample['camera_calibration']:
        camera = stored['name']
        image = frame['images'][camera]
        expected_file = f"rgb/{sample['sample_index']:06d}_{camera}.png"
        if (image['file'] != expected_file or image['sample_time_s'] != frame['solver_time_s']
                or any(image[key] != stored[key] for key in ('K', 'T_world_camera'))):
            raise ValueError('P4 RGB and original calibration mismatch')
        if sha256(directory/expected_file) != image['sha256']:
            raise ValueError('P4 RGB checksum mismatch')
        with Image.open(directory/expected_file) as rgb:
            if rgb.mode != 'RGB' or rgb.size != (224, 224) or rgb.format != 'PNG':
                raise ValueError('P4 invalid RGB file')
            rgb.load()


def verify_obstacle_label(model, shadow, frame, sample):
    restore_solver_pose(model, shadow, sample)
    obstacle = nearest_obstacle(model, shadow, sample['T_world_gripper'])
    stored = frame['nearest_obstacle']
    if (obstacle['geom'] != stored['geom']
            or any(not np.allclose(obstacle[key], stored[key], atol=1e-9, rtol=0)
                   for key in ('signed_distance_m', 'point_world_m', 'point_gripper_m'))):
        raise ValueError('P4 nearest obstacle label does not match solver pose')


def capture_pair(engine, shadow, sample, observed, requested, directory, *, include_obstacle=False):
    restore_solver_pose(engine.model, shadow, sample)
    reference = np.asarray(sample['T_world_gripper'])
    site = engine.robot.site
    if (not np.allclose(shadow.site_xpos[site], reference[:3, 3], atol=1e-9, rtol=0)
            or not np.allclose(shadow.site_xmat[site].reshape(3, 3), reference[:3, :3], atol=1e-9, rtol=0)):
        raise ValueError('P4 solver gripper pose mismatch')
    calibration = engine.camera_calibration(shadow)
    for camera in sample['camera_calibration']:
        current = calibration[camera['name']]
        for field in ('T_world_camera', 'K'):
            if not np.allclose(current[field], camera[field], atol=1e-9, rtol=0):
                raise ValueError('P4 solver camera calibration mismatch')
            current[field] = camera[field]
    rgb = engine.camera_rgb(shadow)
    images = {}
    index, time = sample['sample_index'], sample['solver_step_start_time_s']
    for camera in CAMERAS:
        relative = Path('rgb')/f'{index:06d}_{camera}.png'
        Image.fromarray(rgb[camera]).save(directory/relative)
        images[camera] = dict(calibration[camera], file=relative.as_posix(), sha256=sha256(directory/relative),
                              sample_time_s=time, width=224, height=224, origin='top_left', encoding='png_rgb')

    centers = {part: shadow.geom_xpos[engine.model.geom(geom).id].tolist() for geom, part in GEOM_PART.items()}
    points = [{'contact_index': c['contact_index'], 'finger': c['finger'],
               'part': GEOM_PART[c['other_geom_name']], 'position_world_m': c['position_world_m']}
              for c in sample['contacts'] if c['pair_role'] == 'finger_target' and c['surface_region'] == 'pad'
              and c['load_bearing'] and c['normal_force_N'] >= MIN_FORCE_N]
    for point in points:
        point['projections'] = {name: project_point(calibration[name], point['position_world_m']) for name in CAMERAS}
    center = centers[requested]
    result = dict(sample_index=index, solver_time_s=time, images=images, actual_part=observed['actual_part'],
                requested_center_world_m=center, requested_center_gripper_m=point_to_local(reference, center).tolist(),
                requested_center_projections={name: project_point(calibration[name], center) for name in CAMERAS},
                projected_pad_contacts=points)
    if include_obstacle:
        result['nearest_obstacle'] = nearest_obstacle(engine.model, shadow, reference)
    return result


def run_part_episode(engine, directory, requested, *, aimed_part=None, bar=SteppedBar(), pilot=None,
                     cancelled=lambda: False):
    """Capture one labelled episode. A wrong-part teacher override is verification-only."""
    aim = requested if aimed_part is None else aimed_part
    if requested not in INSTRUCTIONS or aim not in INSTRUCTIONS:
        raise ValueError('Unknown P4 part')
    directory.mkdir(parents=True, exist_ok=False)
    (directory/'rgb').mkdir()
    engine.load_task(seed=0, init_index=0)
    if pilot is None:
        bar.install(engine)
    else:
        pilot.install(engine)
        bar = pilot.bar
    engine.configure_contacts(target_body=TARGET)
    (directory/'model.xml').write_text(engine.base_xml, encoding='utf-8')
    joint = int(engine.model.joint(TARGET+'_joint').qposadr[0])
    profile = engine.contact_sampler.profile
    baseline = float(engine.data.qpos[joint+2])
    evidence = PartEvidence(requested, baseline)
    manifest_path = directory/'manifest.json'
    prompt = (TEST_PROMPTS if pilot and pilot.held_out_phrasing else INSTRUCTIONS)[requested]
    manifest = dict(
        schema=PILOT_SCHEMA if pilot else SCHEMA, status='recording', task='synthetic_two_part_grasp', is_vla=False,
        seed=0, base_libero_init_index=0, variant=asdict(bar), target_body=TARGET, distractors=[DISTRACTOR],
        instruction={'part': requested, 'en': prompt[0], 'zh': prompt[1]},
        teacher={'kind': 'simulator_geometry_rule_native_pd', 'aimed_part': aim, 'verification_override': aim != requested},
        initial_qpos=engine.data.qpos.tolist(), initial_qvel=engine.data.qvel.tolist(),
        model_xml_sha256=sha256(directory/'model.xml'), mujoco_version=mujoco.__version__,
        contact_profile=profile.description, contact_profile_sha256=profile.sha256, part_by_geom=GEOM_PART,
        thresholds={'pad_normal_force_N': MIN_FORCE_N, 'part_contact_s': REQUIRED_CONTACT_S,
                    'lift_m': REQUIRED_LIFT_M, 'lift_hold_s': REQUIRED_HOLD_S, 'baseline_z_m': baseline},
        contact_period_s=CONTROL_PERIOD_S, rgb_period_s=CONTROL_PERIOD_S*IMAGE_STRIDE,
        rgb_pose_source='same_native_solver_input_as_contact_label',
        projection_semantics='pinhole_geometric_no_occlusion_check', action_space='native_panda_joint_pd',
        training_dataset_ready=False)
    if pilot:
        manifest.update(pilot_scene=asdict(pilot), joint_names=engine.robot.names,
                        motion_space=MOTION_SPACE, obstacle_semantics='nearest_floor_or_box_surface_from_tcp')
    publish_json(manifest_path, manifest)
    trace_path, frame_path = directory/'contact_trace.jsonl', directory/'frames.jsonl'
    shadow = mujoco.MjData(engine.model)
    count = pairs = 0
    representative = None
    poses = []
    try:
        with trace_path.open('xb') as trace, frame_path.open('xb') as frames:
            for phase in PartTeacher(engine, aim, align_yaw=pilot is not None).segments():
                if cancelled():
                    break
                sample = engine.contact_sampler.latest
                if sample is None or sample['sample_index'] != count:
                    raise ValueError('Missing or out-of-order native P4 sample')
                target_z = sample['state_at_solver']['qpos'][joint+2]
                observed = evidence.observe(sample, target_z)
                action = dict(joint_targets_rad=engine.robot.targets.tolist(), gripper_open_fraction=engine.robot.opening)
                trace.write(canonical_json(dict(phase=phase, sample=sample, observed=observed, action=action))+b'\n')
                count += 1
                if sample['sample_index'] % IMAGE_STRIDE == 0:
                    frame = capture_pair(engine, shadow, sample, observed, requested, directory,
                                         include_obstacle=pilot is not None)
                    frames.write(canonical_json(frame)+b'\n')
                    pairs += 1
                    if pilot:
                        poses.append((sample['sample_index'], sample['solver_step_start_time_s'],
                                      sample['T_world_gripper'], action['gripper_open_fraction']))
                    if frame['actual_part'] is not None and observed['lift_m'] >= REQUIRED_LIFT_M:
                        representative = frame['sample_index']
        if pilot:
            motion_path = directory/MOTION_FILE
            with motion_path.open('xb') as stream:
                for row in motion_rows(poses):
                    stream.write(canonical_json(row)+b'\n')
            manifest['observed_motion_sha256'] = sha256(motion_path)
    except Exception as error:
        manifest.update(status='capture_error', error=str(error), contact_samples=count, rgb_pairs=pairs)
        publish_json(manifest_path, manifest)
        raise
    manifest.update(status='cancelled' if cancelled() else 'finalized', outcome=evidence.result(),
                    contact_samples=count, rgb_pairs=pairs, representative_sample_index=representative,
                    contact_trace_sha256=sha256(trace_path), frames_sha256=sha256(frame_path))
    publish_json(manifest_path, manifest)
    return manifest


def verify_part_episode(directory):
    """Reopen finalized records and independently check links, evidence and RGB files."""
    metadata = json.loads((directory/'manifest.json').read_bytes())
    if metadata['schema'] not in (SCHEMA, PILOT_SCHEMA) or metadata['status'] != 'finalized':
        raise ValueError('P4 episode is not finalized')
    trace_path, frame_path = directory/'contact_trace.jsonl', directory/'frames.jsonl'
    for path, field in ((trace_path, 'contact_trace_sha256'), (frame_path, 'frames_sha256'),
                        (directory/'model.xml', 'model_xml_sha256')):
        if sha256(path) != metadata[field]:
            raise ValueError('P4 checksum mismatch: '+path.name)
    trace = [json.loads(line) for line in trace_path.read_bytes().splitlines()]
    frames = [json.loads(line) for line in frame_path.read_bytes().splitlines()]
    if len(trace) != metadata['contact_samples'] or len(frames) != metadata['rgb_pairs']:
        raise ValueError('P4 exported count mismatch')
    evidence = PartEvidence(metadata['instruction']['part'], metadata['thresholds']['baseline_z_m'])
    model = mujoco.MjModel.from_xml_path(str(directory/'model.xml'))
    pilot = metadata['schema'] == PILOT_SCHEMA
    if pilot:
        shadow = mujoco.MjData(model)
        limits = np.array([model.jnt_range[model.joint(name).id] for name in metadata['joint_names']])
        ctrl_limited = model.actuator_ctrllimited.astype(bool)
        ctrl_limits = model.actuator_ctrlrange[ctrl_limited]
        poses = []
    joint = int(model.joint(TARGET+'_joint').qposadr[0])
    for index, row in enumerate(trace):
        sample = row['sample']
        if sample['sample_index'] != index or evidence.observe(sample, sample['state_at_solver']['qpos'][joint+2]) != row['observed']:
            raise ValueError('P4 recorded evidence does not match original samples')
        if pilot:
            targets = np.asarray(row['action']['joint_targets_rad'])
            control = np.asarray(sample['state_at_solver']['ctrl_native'])[ctrl_limited]
            if (targets.shape != (7,) or limits.shape != (7, 2)
                    or not np.all((targets >= limits[:, 0]) & (targets <= limits[:, 1]))
                    or not np.all((control >= ctrl_limits[:, 0]-1e-9) & (control <= ctrl_limits[:, 1]+1e-9))
                    or row['action']['gripper_open_fraction'] not in (0., 1.)):
                raise ValueError('P4 native action violates the recorded joint or actuator contract')
    if evidence.result() != metadata['outcome']:
        raise ValueError('P4 result does not match original samples')
    if [frame['sample_index'] for frame in frames] != list(range(0, len(trace), IMAGE_STRIDE)):
        raise ValueError('P4 RGB sample schedule mismatch')
    for frame in frames:
        index = frame['sample_index']
        row = trace[index]
        sample = row['sample']
        time = sample['solver_step_start_time_s']
        if frame['actual_part'] != row['observed']['actual_part']:
            raise ValueError('P4 frame and original sample mismatch')
        if pilot:
            verify_obstacle_label(model, shadow, frame, sample)
            poses.append((index, time, sample['T_world_gripper'], row['action']['gripper_open_fraction']))
        verify_rgb_pair(directory, frame, sample)
    if pilot:
        motion_path = directory/MOTION_FILE
        if sha256(motion_path) != metadata['observed_motion_sha256'] or metadata['motion_space'] != MOTION_SPACE:
            raise ValueError('P4 observed motion contract or checksum mismatch')
        check_motion([json.loads(line) for line in motion_path.read_bytes().splitlines()], poses)
    return metadata, frames
