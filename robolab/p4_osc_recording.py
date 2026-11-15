"""Store applied OSC actions separately from measured motion and PD records."""
from dataclasses import asdict
import json
import math

import mujoco
import numpy as np

from .contact_store import canonical_json
from .p4_osc import ACTION_SPACE, OSC_PERIOD_S, SAMPLING_MODE, SyntheticOsc
from .p4_parts import INSTRUCTIONS, REQUIRED_LIFT_M, TARGET, PartEvidence, PartWaypoints
from .p4_recording import capture_pair, publish_json, sha256, verify_obstacle_label, verify_rgb_pair


SCHEMA = 'robolab.p4_osc_grasp.v1'
TRACE = 'actions_and_contacts.jsonl'
FRAMES = 'frames.jsonl'
TICKS = sum(round(duration/OSC_PERIOD_S) for _, duration in PartWaypoints.phases)


def _load_scene(engine, scene):
    engine.load_task(seed=0, init_index=0)
    scene.install(engine)
    return SyntheticOsc(engine)


def _append(stream, row):
    stream.write(canonical_json(row)+b'\n')


def _goal(osc):
    return dict(position_m=osc.controller.goal_pos.tolist(), rotation=osc.controller.goal_ori.tolist())


def _initial(engine):
    data = engine.data
    return dict(qpos=data.qpos.tolist(), qvel=data.qvel.tolist(), ctrl=data.ctrl.tolist())


def run_osc_episode(engine, directory, scene, part, instruction=None):
    """Record one teacher episode; ``instruction`` overrides the default prompt for dataset plans."""
    if instruction is None:
        instruction = dict(part=part, en=INSTRUCTIONS[part][0], zh=INSTRUCTIONS[part][1])
    elif instruction.get('part') != part:
        raise ValueError('Instruction part differs from the requested part')
    directory.mkdir(parents=True, exist_ok=False)
    (directory/'rgb').mkdir()
    osc = _load_scene(engine, scene)
    (directory/'model.xml').write_text(engine.base_xml, encoding='utf-8')
    scene_hash = sha256(directory/'model.xml')
    qadr_z = int(engine.model.joint(TARGET+'_joint').qposadr[0])+2
    baseline_z = float(engine.data.qpos[qadr_z])
    evidence = PartEvidence(part, baseline_z, sample_period_s=OSC_PERIOD_S)
    manifest_path = directory/'manifest.json'
    manifest = dict(schema=SCHEMA, status='recording', scene=asdict(scene), model_xml_sha256=scene_hash,
                    is_vla=False, training_dataset_ready=False, action_space=ACTION_SPACE,
                    command_order=['dx', 'dy', 'dz', 'rx', 'ry', 'rz', 'gripper'],
                    input_bounds=[-1, 1], arm_scale_m_rad=osc.scale.tolist(), controller_config=osc.config,
                    control_period_s=OSC_PERIOD_S, physics_substeps=osc.substeps,
                    physics_dt_s=float(engine.model.opt.timestep), sampling_mode=SAMPLING_MODE,
                    mujoco_version=mujoco.__version__, robosuite_version='1.4.1',
                    instruction=dict(instruction), initial=_initial(engine), baseline_z_m=baseline_z)
    publish_json(manifest_path, manifest)
    shadow = mujoco.MjData(engine.model)
    count = 0
    representative = None
    try:
        with (directory/TRACE).open('xb') as trace, (directory/FRAMES).open('xb') as images:
            for phase, position, rotation, opening in PartWaypoints(engine, part, align_yaw=True).commands(OSC_PERIOD_S):
                action = osc.action_toward(position, rotation, opening)
                orientation_before = osc.controller.goal_ori.tolist()
                sample = osc.step(action)
                observed = evidence.observe(sample, sample['state_at_solver']['qpos'][qadr_z])
                timing = dict(action_time_s=sample['solver_step_start_time_s'],
                              control_end_time_s=float(engine.data.time))
                command = action.tolist()
                _append(trace, dict(index=count, phase=phase, action_normalized_7d=command,
                                    goal=_goal(osc), previous_goal_rotation=orientation_before,
                                    sample=sample, observed=observed, qpos_after=engine.data.qpos.tolist(),
                                    qvel_after=engine.data.qvel.tolist(), **timing))
                frame = capture_pair(engine, shadow, sample, observed, part, directory, include_obstacle=True)
                frame.update(action_normalized_7d=command, **timing)
                _append(images, frame)
                count += 1
                if observed['actual_part'] and observed['lift_m'] >= REQUIRED_LIFT_M:
                    representative = frame['sample_index']
    except Exception as error:
        manifest.update(status='capture_error', error=str(error), sample_count=count)
        publish_json(manifest_path, manifest)
        raise
    manifest.update(status='finalized', sample_count=count, rgb_pairs=count, outcome=evidence.result(),
                    representative_sample_index=representative, trace_sha256=sha256(directory/TRACE),
                    frames_sha256=sha256(directory/FRAMES))
    publish_json(manifest_path, manifest)
    return manifest


def _verify_goal(row, scale):
    sample_pose = np.asarray(row['sample']['T_world_gripper'])
    delta = np.asarray(row['action_normalized_7d'][:6])*scale
    rotation_vector = delta[3:]
    angle = float(np.linalg.norm(rotation_vector))
    if angle == 0:
        expected = row['previous_goal_rotation']
    else:
        quat, rotation = np.zeros(4), np.zeros(9)
        mujoco.mju_axisAngle2Quat(quat, rotation_vector/angle, angle)
        mujoco.mju_quat2Mat(rotation, quat)
        expected = rotation.reshape(3, 3) @ sample_pose[:3, :3]
    translation_error = float(np.max(np.abs(np.asarray(row['goal']['position_m'])-sample_pose[:3, 3]-delta[:3])))
    rotation_error = float(np.max(np.abs(np.asarray(row['goal']['rotation'])-expected)))
    # robosuite 1.4.1 quat2mat casts to float32; MuJoCo's independent check uses doubles.
    if translation_error > 1e-9 or rotation_error > 1e-7:
        raise ValueError(f'OSC goal differs at tick {row["index"]}: {translation_error=}, {rotation_error=}')
    return rotation_error


def verify_osc_episode(engine, directory, scene):
    """Verify files then replay saved commands through a fresh actual controller."""
    manifest = json.loads((directory/'manifest.json').read_bytes())
    if (manifest['schema'] != SCHEMA or manifest['status'] != 'finalized'
            or manifest['action_space'] != ACTION_SPACE or canonical_json(manifest['scene']) != canonical_json(asdict(scene))):
        raise ValueError('Unexpected P4 OSC manifest')
    for filename, key in ((TRACE, 'trace_sha256'), (FRAMES, 'frames_sha256'), ('model.xml', 'model_xml_sha256')):
        if sha256(directory/filename) != manifest[key]:
            raise ValueError('P4 OSC checksum mismatch: '+filename)
    trace = [json.loads(line) for line in (directory/TRACE).read_bytes().splitlines()]
    frames = [json.loads(line) for line in (directory/FRAMES).read_bytes().splitlines()]
    if not len(trace) == len(frames) == manifest['sample_count'] == manifest['rgb_pairs'] == TICKS:
        raise ValueError('P4 OSC control and image counts differ')
    osc = _load_scene(engine, scene)
    if (canonical_json(_initial(engine)) != canonical_json(manifest['initial'])
            or engine.base_xml != (directory/'model.xml').read_text()
            or osc.config != manifest['controller_config']):
        raise ValueError('P4 OSC replay scene, initial state or controller config changed')
    evidence = PartEvidence(manifest['instruction']['part'], manifest['baseline_z_m'], sample_period_s=OSC_PERIOD_S)
    qadr_z = int(engine.model.joint(TARGET+'_joint').qposadr[0])+2
    for invalid in ([0.]*6, [float('nan')]+[0.]*6, [1.000001]+[0.]*6, [True]+[0.]*6):
        try:
            osc.step(invalid)
        except ValueError:
            pass
        else:
            raise ValueError('Invalid OSC action was accepted')
    if engine.data.time != 0. or _initial(engine) != manifest['initial']:
        raise ValueError('Rejected OSC actions changed the simulation')
    shadow = mujoco.MjData(engine.model)
    limited = engine.model.actuator_ctrllimited.astype(bool)
    limits = engine.model.actuator_ctrlrange[limited]
    max_qpos = 0.
    max_rotation_error = 0.
    for i, (row, frame) in enumerate(zip(trace, frames)):
        sample = row['sample']
        time_s = row['action_time_s']
        if (i != row['index'] or i != sample['sample_index'] or sample['sampling_mode'] != SAMPLING_MODE
                or sample['solver_step_start_time_s'] != time_s or frame['action_time_s'] != time_s
                or not math.isclose(time_s, i*OSC_PERIOD_S, abs_tol=1e-8, rel_tol=0)
                or not math.isclose(sample['state_after_step_time_s']-time_s, manifest['physics_dt_s'], abs_tol=1e-9, rel_tol=0)
                or not math.isclose(row['control_end_time_s']-time_s, OSC_PERIOD_S, abs_tol=1e-8, rel_tol=0)
                or row['control_end_time_s'] != frame['control_end_time_s']
                or row['action_normalized_7d'] != frame['action_normalized_7d']):
            raise ValueError('P4 OSC image/action/contact time alignment failed')
        verify_rgb_pair(directory, frame, sample)
        verify_obstacle_label(engine.model, shadow, frame, sample)
        max_rotation_error = max(max_rotation_error, _verify_goal(row, osc.scale))
        ctrl = np.asarray(sample['state_at_solver']['ctrl_native'])[limited]
        if not np.all((ctrl >= limits[:, 0]-1e-9) & (ctrl <= limits[:, 1]+1e-9)):
            raise ValueError('P4 OSC recorded control exceeds model actuator limits')
        replay = osc.step(row['action_normalized_7d'])
        max_qpos = max(max_qpos, float(np.max(np.abs(engine.data.qpos-row['qpos_after']))))
        if (replay != sample or not np.array_equal(engine.data.qpos, row['qpos_after'])
                or not np.array_equal(engine.data.qvel, row['qvel_after']) or _goal(osc) != row['goal']):
            raise ValueError(f'P4 OSC replay differs at tick {i}')
        observed = evidence.observe(replay, replay['state_at_solver']['qpos'][qadr_z])
        if observed != row['observed'] or frame['actual_part'] != observed['actual_part']:
            raise ValueError('P4 OSC contact verdict differs in replay')
    if evidence.result() != manifest['outcome']:
        raise ValueError('P4 OSC episode verdict differs in replay')
    return manifest, frames, dict(ticks=len(trace), max_qpos_difference=max_qpos,
                                  max_goal_rotation_matrix_error=max_rotation_error, contact_samples_equal=True)
