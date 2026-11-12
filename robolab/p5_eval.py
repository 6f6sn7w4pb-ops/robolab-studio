"""P5 closed-loop evaluation of a policy on planned P4 scenes.

The simulator, scene, controller and contact sampler are the ones that recorded the P4-D
dataset. At every 50 ms tick the policy sees an observation built exactly like a row of the
training view (two canonical 224x224 RGB images, the 8-D openpi-style state, the instruction),
returns a chunk of normalized 7-D OSC_POSE actions, and the actions are executed one per tick.
The episode verdict uses the same contact evidence (PartEvidence) as the dataset, applied to
whatever the policy did; nothing about the policy's intent is assumed.

Policies:
- teacher / wrong_part_teacher: privileged rule teachers that read simulator geometry. They are
  harness checks (an upper reference and a part-confusion control), not models.
- adapter:<name>: any registered VlaAdapter (Mock, or a lab-side inference service) through the
  existing robolab.observation.v1 bridge with JPEG images.
"""
from dataclasses import dataclass
import base64
import hashlib
import io
import json
import math
from pathlib import Path
import time

import numpy as np

from .contact_store import canonical_json

CONFIG_SCHEMA = 'robolab.p5_eval_config.v1'
EPISODE_SCHEMA = 'robolab.p5_eval_episode.v1'
SUMMARY_SCHEMA = 'robolab.p5_eval_summary.v1'
OBSERVATION_LAYOUT = 'p4_training_view_v1'
# v2: the pre-contact window ends one tick before the first sampled finger contact, because
#     contacts are sampled at the start of a tick and the touch can happen inside the previous tick.
METRICS_VERSION = 2
EVAL_SPLITS = ('test_instance', 'test_unseen')
SETTLE_TICKS = 12               # same rested-pose reference tick as the dataset metrics (0.6 s)
OBSTACLE_MOVED_M = .002
DISTURB_ROTATION_DEG = 1.
DISTURB_MOVE_M = .001
KEYFRAME_EVERY = 20
MAX_CHUNK = 32
RESULT_FILE = 'result.json'
TRACE_FILE = 'trace.jsonl'
TIMING_FILE = 'timing.json'
CAMERAS = ('agentview', 'robot0_eye_in_hand')
TARGET_JOINT = 'p4_stepped_bar_joint'
OBSTACLE_JOINT = 'p4_distractor_joint'


def other_part(part):
    return 'thin' if part == 'thick' else 'thick'


@dataclass
class EpisodeContext:
    """Handed to policy.reset(). Only privileged policies may touch engine/osc."""
    engine: object
    osc: object
    case: object
    instruction: str
    language: str


class EvalPolicy:
    policy_id = 'base'
    privileged = False
    is_vla = False

    def describe(self):
        return dict(policy_id=self.policy_id, privileged=self.privileged, is_vla=self.is_vla)

    def reset(self, context):
        pass

    def act(self, observation):
        raise NotImplementedError

    def close(self):
        pass


class TeacherPolicy(EvalPolicy):
    """The P4 rule teacher, queried one action per tick from the live simulator state."""
    privileged = True

    def __init__(self, aim='requested'):
        if aim not in ('requested', 'other'):
            raise ValueError('aim must be requested or other')
        self.aim = aim
        self.policy_id = 'teacher' if aim == 'requested' else 'wrong_part_teacher'

    def describe(self):
        return dict(super().describe(), aim=self.aim,
                    note='reads simulator part positions; harness reference, not a learned policy')

    def reset(self, context):
        from .p4_osc import OSC_PERIOD_S
        from .p4_parts import PartWaypoints

        part = context.case.part if self.aim == 'requested' else other_part(context.case.part)
        self.osc = context.osc
        self.commands = PartWaypoints(context.engine, part, align_yaw=True).commands(OSC_PERIOD_S)
        self.gripper = -1.

    def act(self, observation):
        command = next(self.commands, None)
        if command is None:
            # Past the scripted path: keep the pose and the last gripper command.
            return [[0.]*6+[self.gripper]]
        _, position, rotation, opening = command
        action = self.osc.action_toward(position, rotation, opening)
        self.gripper = float(action[6])
        return [action.tolist()]


def encode_jpeg(rgb, quality=95):
    from PIL import Image
    buffer = io.BytesIO()
    Image.fromarray(rgb).save(buffer, 'JPEG', quality=quality)
    return base64.b64encode(buffer.getvalue()).decode('ascii')


def bridge_observation(observation, observation_id):
    """robolab.observation.v1 for VlaAdapter / HTTP services. Extra keys describe the layout."""
    return {'schema': 'robolab.observation.v1', 'observation_id': observation_id,
            'instruction': observation['instruction'], 'sim_time': observation['time_s'],
            'images': {name: {'encoding': 'jpeg_base64', 'width': 224, 'height': 224, 'origin': 'top_left',
                              'data': encode_jpeg(observation['images'][name])} for name in CAMERAS},
            'proprio': [float(x) for x in observation['state']], 'joints': [float(x) for x in observation['joints']],
            'calibration': observation['calibration'], 'state_layout': OBSERVATION_LAYOUT,
            'image_convention': 'canonical_top_left', 'action_space': observation['action_space'],
            'episode_tick': observation['tick']}


class AdapterPolicy(EvalPolicy):
    """Wrap a registered VlaAdapter (MockPolicy, OpenVLA-OFT service, future pi05 adapter)."""
    def __init__(self, name, **configuration):
        from .policy import create_vla_adapter
        self.name = name
        self.policy_id = 'adapter:'+name
        self.adapter = create_vla_adapter(name, **configuration)
        self.status = self.adapter.load()
        self.is_vla = bool(self.status.get('real_model')) and bool(self.status.get('model_loaded'))
        self.sequence = 0
        self.last_response = {}

    def describe(self):
        return dict(super().describe(), adapter=self.name, adapter_status=self.status,
                    action_space=self.adapter.action_space)

    def act(self, observation):
        self.sequence += 1
        result = self.adapter.predict(bridge_observation(observation, self.sequence))
        self.last_response = {key: result.get(key) for key in ('is_mock', 'real_inference', 'source')}
        return result['actions']

    def close(self):
        self.adapter.close()


def make_policy(spec):
    kind = spec['kind']
    if kind == 'teacher':
        return TeacherPolicy('requested')
    if kind == 'wrong_part_teacher':
        return TeacherPolicy('other')
    if kind == 'adapter':
        configuration = {}
        if spec.get('endpoint'):
            configuration.update(endpoint=spec['endpoint'], timeout=spec.get('timeout', 10))
        return AdapterPolicy(spec['adapter'], **configuration)
    raise ValueError('Unknown policy kind: '+str(kind))


def policy_spec(text, endpoint='', timeout=10.):
    if text in ('teacher', 'wrong_part_teacher'):
        return dict(kind=text)
    if text.startswith('adapter:') and len(text) > 8:
        return dict(kind='adapter', adapter=text[8:], endpoint=endpoint, timeout=timeout)
    raise ValueError('policy must be teacher, wrong_part_teacher or adapter:<name>')


class Observer:
    """Build training-view observations from the live (pre-action) simulator state."""
    def __init__(self, engine, instruction, language):
        import mujoco
        from .p4_dataset_export import StateReader
        from .p4_osc import ACTION_SPACE

        self.mujoco = mujoco
        self.engine = engine
        self.instruction = instruction
        self.language = language
        self.action_space = ACTION_SPACE
        self.state = StateReader(engine.base_xml)
        self.shadow = mujoco.MjData(engine.model)

    def __call__(self, tick, time_s):
        data, shadow, model = self.engine.data, self.shadow, self.engine.model
        # Same steps as the dataset's solver-pose restore: the state the next action is applied to.
        shadow.time = data.time
        shadow.qpos[:] = data.qpos
        shadow.qvel[:] = data.qvel
        shadow.ctrl[:] = data.ctrl
        self.mujoco.mj_kinematics(model, shadow)
        self.mujoco.mj_comPos(model, shadow)
        self.mujoco.mj_camlight(model, shadow)
        state, joints, _, _ = self.state(data.qpos)
        return dict(tick=tick, time_s=time_s, instruction=self.instruction, language=self.language,
                    images=self.engine.camera_rgb(shadow), state=state, joints=joints,
                    calibration=self.engine.camera_calibration(shadow), action_space=self.action_space)


class Reference:
    """Recorded dataset episode (raw trace + exported training view) to compare tick by tick."""
    def __init__(self, dataset_root, case_id, export_index):
        import h5py

        directory = dataset_root/'episodes'/case_id
        self.trace = [json.loads(line) for line in (directory/'actions_and_contacts.jsonl').read_bytes().splitlines()]
        self.outcome = json.loads((directory/'manifest.json').read_bytes())['outcome']
        self.images = self.state = None
        if case_id in export_index:
            path, demo = export_index[case_id]
            with h5py.File(path, 'r') as handle:
                group = handle['data'][demo]
                self.state = group['obs/state'][()]
                self.images = {'agentview': group['obs/agentview_rgb'][()],
                               'robot0_eye_in_hand': group['obs/eye_in_hand_rgb'][()]}
        self.first = dict(action=None, qpos=None, image=None, state=None)
        self.compared = dict(action=0, qpos=0, image=0, state=0)

    def _note(self, key, tick, equal):
        self.compared[key] += 1
        if not equal and self.first[key] is None:
            self.first[key] = tick

    def check(self, tick, observation, action, qpos_after):
        if tick >= len(self.trace):
            return
        row = self.trace[tick]
        self._note('action', tick, list(action) == row['action_normalized_7d'])
        self._note('qpos', tick, np.array_equal(qpos_after, row['qpos_after']))
        if self.images is not None:
            self._note('image', tick, all(np.array_equal(observation['images'][name], self.images[name][tick])
                                          for name in CAMERAS))
            self._note('state', tick, np.array_equal(observation['state'], self.state[tick]))

    def report(self, outcome, ticks):
        complete = ticks == len(self.trace) and self.compared['action'] == len(self.trace)
        identical = complete and all(value is None for value in self.first.values())
        # Physics only (actions, qpos, verdict): what an appearance variant must leave unchanged.
        physics = complete and self.first['action'] is None and self.first['qpos'] is None
        return dict(reference_ticks=len(self.trace), compared=self.compared, first_divergence_tick=self.first,
                    exported_view_compared=self.images is not None,
                    outcome_equal=outcome == self.outcome, identical=identical and outcome == self.outcome,
                    physics_identical=physics and outcome == self.outcome)


def export_case_index(dataset_root):
    """case_id -> (hdf5 path, demo group) over the exported training view, if present."""
    import h5py

    index = {}
    for path in sorted((dataset_root/'export').glob('p4_osc_*.hdf5')):
        with h5py.File(path, 'r') as handle:
            for name in handle['data']:
                index[handle['data'][name].attrs['case_id']] = (path, name)
    return index


def _quat_to_mat(quat):
    """Same formula as MuJoCo's mju_quat2Mat (w, x, y, z), so metrics need NumPy only."""
    w, x, y, z = (float(v) for v in quat)
    if (w, x, y, z) == (1., 0., 0., 0.):
        return np.eye(3)
    ww, xx, yy, zz = w*w, x*x, y*y, z*z
    wx, wy, wz, xy, xz, yz = w*x, w*y, w*z, x*y, x*z, y*z
    return np.array([[ww+xx-yy-zz, 2*(xy-wz), 2*(xz+wy)],
                     [2*(xy+wz), ww-xx+yy-zz, 2*(yz-wx)],
                     [2*(xz-wy), 2*(yz+wx), ww-xx-yy+zz]])


def _rotation_deg(model_quat_ref, quat):
    cosine = (np.trace(_quat_to_mat(model_quat_ref).T @ _quat_to_mat(quat))-1)/2
    return float(np.degrees(np.arccos(np.clip(cosine, -1., 1.))))


def episode_metrics(case, outcome, rows, error=None):
    """Policy-agnostic verdict from the per-tick trace and the dataset's contact evidence."""
    ticks = len(rows)
    rested = rows[min(SETTLE_TICKS, ticks)-1] if ticks else None
    obstacle_moved = 0.
    disturb_angle = disturb_move = 0.
    first_contact = next((row['tick'] for row in rows if row['finger_target_contacts']), None)
    if rested is not None:
        reference_obstacle = np.asarray(rested['obstacle_position_m'])
        reference_target = rested['target_pose']
        for row in rows[SETTLE_TICKS:]:
            obstacle_moved = max(obstacle_moved, float(np.linalg.norm(np.asarray(row['obstacle_position_m'])
                                                                       - reference_obstacle)))
            if first_contact is not None and row['tick'] >= first_contact-1:
                continue
            disturb_move = max(disturb_move, float(np.linalg.norm(np.asarray(row['target_pose'][:3])
                                                                   - reference_target[:3])))
            disturb_angle = max(disturb_angle, _rotation_deg(reference_target[3:], row['target_pose'][3:]))
    part = case.part
    other = other_part(part)
    part_hit = part in outcome['contacted_parts']
    wrong_part = other in outcome['contacted_parts']
    collision = outcome['finger_obstacle_frames'] > 0 or obstacle_moved >= OBSTACLE_MOVED_M
    success = (error is None and part in outcome['held_parts'] and other not in outcome['held_parts']
               and outcome['finger_obstacle_frames'] == 0 and obstacle_moved < OBSTACLE_MOVED_M)
    reasons = []
    if error is not None:
        reasons.append(error['kind'])
    if not part_hit:
        reasons.append('requested_part_not_contacted')
    if wrong_part:
        reasons.append('other_part_contacted')
    if not outcome['lifted']:
        reasons.append('not_lifted')
    if part not in outcome['held_parts']:
        reasons.append('requested_part_not_held')
    if other in outcome['held_parts']:
        reasons.append('other_part_held')
    if outcome['finger_obstacle_frames']:
        reasons.append('finger_obstacle_contact')
    if obstacle_moved >= OBSTACLE_MOVED_M:
        reasons.append('obstacle_moved')
    match = outcome['first_match_sample_index']
    actions = np.asarray([row['action'] for row in rows], float) if rows else np.zeros((0, 7))
    return dict(metrics_version=METRICS_VERSION, ticks=ticks, success=success, part_hit=part_hit,
                wrong_part_contacted=wrong_part, lifted=outcome['lifted'], collision=collision,
                any_part_contacted=bool(outcome['contacted_parts']),
                finger_obstacle_frames=outcome['finger_obstacle_frames'], obstacle_max_displacement_m=obstacle_moved,
                first_finger_target_contact_tick=first_contact,
                target_disturbed_before_contact=(disturb_angle >= DISTURB_ROTATION_DEG or disturb_move >= DISTURB_MOVE_M),
                pre_contact_target_rotation_deg=disturb_angle, pre_contact_target_displacement_m=disturb_move,
                time_to_success_s=None if match is None or not success else round((match+1)*.05, 6),
                saturated_action_ticks=int(np.any(np.abs(actions[:, :6]) >= 1., axis=1).sum()) if rows else 0,
                failure_reasons=[] if success else reasons)


def run_eval_episode(engine, case, policy, directory, *, language='en', horizon=None, execute_steps=0,
                     keyframe_every=KEYFRAME_EVERY, reference=None, anti_roll=False, appearance=()):
    """Run one closed-loop episode and write result.json, trace.jsonl, timing.json, keyframes/."""
    from PIL import Image

    from .p4_osc import OSC_PERIOD_S
    from .p4_osc_recording import TICKS
    from .p4_parts import PartEvidence, TARGET
    from .p5_variation import load_eval_scene
    from .policy import validate_actions

    horizon = horizon or TICKS
    if horizon <= SETTLE_TICKS:
        raise ValueError('horizon must exceed the settle reference tick')
    directory.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    osc, variant = load_eval_scene(engine, case.scene, anti_roll=anti_roll, appearance=appearance)
    model = engine.model
    instruction = case.instruction_en if language == 'en' else case.instruction_zh
    observe = Observer(engine, instruction, language)
    qadr_z = int(model.joint(TARGET+'_joint').qposadr[0])+2
    target = int(model.joint(TARGET_JOINT).qposadr[0])
    obstacle = int(model.joint(OBSTACLE_JOINT).qposadr[0])
    evidence = PartEvidence(case.part, float(engine.data.qpos[qadr_z]), sample_period_s=OSC_PERIOD_S)
    initial_sha = hashlib.sha256(canonical_json(engine.data.qpos.tolist())).hexdigest()
    policy.reset(EpisodeContext(engine, osc, case, instruction, language))
    rows, queue, queries, latencies = [], [], 0, []
    error = None
    keyframes = []
    if keyframe_every:
        (directory/'keyframes').mkdir()
    with (directory/TRACE_FILE).open('xb') as trace:
        for tick in range(horizon):
            time_s = round(tick*OSC_PERIOD_S, 9)
            observation = observe(tick, time_s)
            queried = not queue
            if queried:
                began = time.perf_counter()
                try:
                    chunk = validate_actions(policy.act(observation))
                    if len(chunk) > MAX_CHUNK:
                        raise ValueError('action chunk too long')
                    if any(abs(x) > 1. for row in chunk for x in row):
                        raise ValueError('action outside [-1, 1]')
                except Exception as exception:
                    error = dict(kind='policy_error', tick=tick, message=str(exception)[:500])
                    break
                latencies.append(time.perf_counter()-began)
                queries += 1
                queue = chunk[:execute_steps] if execute_steps else chunk
            action = queue.pop(0)
            try:
                sample = osc.step(action)
            except Exception as exception:
                error = dict(kind='simulation_error', tick=tick, message=str(exception)[:500])
                break
            observed = evidence.observe(sample, sample['state_at_solver']['qpos'][qadr_z])
            qpos = engine.data.qpos
            row = dict(tick=tick, queried=queried, action=action, state=observation['state'].tolist(),
                       target_pose=qpos[target:target+7].tolist(), obstacle_position_m=qpos[obstacle:obstacle+3].tolist(),
                       actual_part=observed['actual_part'], per_finger_parts=observed['per_finger_parts'],
                       lift_m=observed['lift_m'], obstacle_contacts=observed['obstacle_contacts'],
                       finger_target_contacts=sum(c['pair_role'] == 'finger_target' and c['load_bearing']
                                                  for c in sample['contacts']))
            trace.write(canonical_json(row)+b'\n')
            rows.append(row)
            if reference is not None:
                reference.check(tick, observation, action, qpos)
            if keyframe_every and (tick % keyframe_every == 0 or tick == horizon-1):
                names = {}
                for camera in CAMERAS:
                    relative = f'keyframes/{tick:04d}_{camera}.png'
                    Image.fromarray(observation['images'][camera]).save(directory/relative)
                    names[camera] = relative
                keyframes.append(dict(tick=tick, actual_part=observed['actual_part'], lift_m=observed['lift_m'],
                                      files=names))
    outcome = evidence.result()
    metrics = episode_metrics(case, outcome, rows, error)
    result = dict(schema=EPISODE_SCHEMA, status='finished', case=case.describe(), policy=policy.describe(),
                  language=language, instruction=instruction, horizon=horizon, execute_steps=execute_steps,
                  observation_layout=OBSERVATION_LAYOUT, action_space=observe.action_space, scene_variant=variant,
                  initial_qpos_sha256=initial_sha, outcome=outcome, metrics=metrics, error=error,
                  policy_queries=queries, keyframes=keyframes,
                  reference=None if reference is None else reference.report(outcome, len(rows)))
    (directory/RESULT_FILE).write_bytes(canonical_json(result)+b'\n')
    timing = dict(wall_s=round(time.monotonic()-started, 3), policy_queries=queries,
                  mean_policy_latency_ms=round(1000*float(np.mean(latencies)), 3) if latencies else None,
                  max_policy_latency_ms=round(1000*float(np.max(latencies)), 3) if latencies else None)
    (directory/TIMING_FILE).write_bytes(canonical_json(timing)+b'\n')
    return result


# ----------------------------------------------------------------------------- aggregation

def wilson(successes, total, z=1.959964):
    """95 % Wilson score interval; (None, None) for an empty group."""
    if total == 0:
        return None, None
    p = successes/total
    denominator = 1+z*z/total
    center = (p+z*z/(2*total))/denominator
    half = z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/denominator
    return max(0., center-half), min(1., center+half)


RATE_KEYS = ('success', 'part_hit', 'wrong_part_contacted', 'lifted', 'collision', 'target_disturbed_before_contact')


def group_stats(rows):
    total = len(rows)
    stats = dict(episodes=total, errors=sum(row['error'] is not None for row in rows))
    for key in RATE_KEYS:
        count = sum(bool(row['metrics'][key]) for row in rows)
        low, high = wilson(count, total)
        stats[key] = dict(count=count, rate=count/total if total else None, ci95=[low, high])
    contacted = [row for row in rows if row['metrics']['any_part_contacted']]
    hits = sum(row['metrics']['part_hit'] and not row['metrics']['wrong_part_contacted'] for row in contacted)
    low, high = wilson(hits, len(contacted))
    stats['part_accuracy_given_contact'] = dict(count=hits, of=len(contacted),
                                                rate=hits/len(contacted) if contacted else None, ci95=[low, high])
    times = [row['metrics']['time_to_success_s'] for row in rows if row['metrics']['time_to_success_s'] is not None]
    stats['mean_time_to_success_s'] = float(np.mean(times)) if times else None
    reasons = {}
    for row in rows:
        for reason in row['metrics']['failure_reasons']:
            reasons[reason] = reasons.get(reason, 0)+1
    stats['failure_reasons'] = dict(sorted(reasons.items()))
    return stats


def summarize(rows):
    def by(key):
        groups = {}
        for row in rows:
            groups.setdefault(key(row), []).append(row)
        return {name: group_stats(group) for name, group in sorted(groups.items())}

    return dict(overall=group_stats(rows),
                by_split=by(lambda row: row['case']['split']),
                by_family=by(lambda row: row['case']['family']),
                by_part=by(lambda row: row['case']['part']),
                by_phrasing=by(lambda row: row['case']['instruction']['phrasing']),
                by_split_phrasing=by(lambda row: row['case']['split']+'/'+row['case']['instruction']['phrasing']))


def read_json(path):
    return json.loads(Path(path).read_bytes())


def publish_json(path, value):
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_bytes(canonical_json(value)+b'\n')
    temporary.replace(path)


def select_cases(dataset_root, splits, scene_set='dataset'):
    """(plan, chosen cases, scene-set info); 'extended' adds the audited extra test scenes."""
    from .p4_dataset_build import load_plan, ordered_cases
    from .p5_scenes import scene_set_cases
    plan, cases, controls = load_plan(dataset_root)
    cases, controls, info = scene_set_cases(scene_set, cases, controls)
    chosen = [case for case in ordered_cases(cases, controls) if case.split in splits]
    return plan, chosen, info


def recompute_metrics(directory, result):
    """Current-version metrics from the stored trace and verdict (no simulation)."""
    from types import SimpleNamespace

    rows = [json.loads(line) for line in (directory/TRACE_FILE).read_bytes().splitlines()]
    metrics = episode_metrics(SimpleNamespace(part=result['case']['part']), result['outcome'], rows, result['error'])
    metrics['recomputed_from_files'] = True
    return metrics


def reference_physics_identical(reference):
    """Results written before P5-B lack the key; derive it from the stored first-divergence ticks."""
    if 'physics_identical' in reference:
        return bool(reference['physics_identical'])
    first = reference['first_divergence_tick']
    return bool(reference['identical'] or (first['action'] is None and first['qpos'] is None
                                           and reference['outcome_equal']
                                           and reference['compared']['action'] == reference['reference_ticks']))


def conditions(config):
    """Scene conditions of a run; configs written before these options existed are nominal."""
    return dict(scene_set=config.get('scene_set', 'dataset'), anti_roll=bool(config.get('anti_roll', False)),
                appearance=list(config.get('appearance', [])), appearance_seed=config.get('appearance_seed'))


def build_summary(root, *, recheck=None, elapsed_wall_s=None, status=None):
    config = read_json(root/'eval_config.json')
    rows, missing = [], []
    for case_id in config['case_ids']:
        path = root/'episodes'/case_id/RESULT_FILE
        if path.is_file():
            row = read_json(path)
            row['path'] = f'episodes/{case_id}'
            if row['metrics'].get('metrics_version') != METRICS_VERSION:
                row['metrics'] = recompute_metrics(root/'episodes'/case_id, row)
            rows.append(row)
        else:
            missing.append(case_id)
    evaluated = [row for row in rows if row['case']['split'] != 'control']
    controls = [row for row in rows if row['case']['split'] == 'control']
    # Anti-roll changes the target-floor contact on purpose, so those episodes cannot match the
    # recorded demonstration; they are counted separately instead of failing the identity check.
    compared_rows = [row for row in rows if row.get('reference')]
    reference_rows = [row for row in compared_rows if not (row.get('scene_variant') or {}).get('anti_roll')]
    runs = [dict(path=row['path'], case_id=row['case']['case_id'], scene_id=row['case']['scene']['scene_id'],
                 split=row['case']['split'], family=row['case']['family'], part=row['case']['part'],
                 phrasing=row['case']['instruction']['phrasing'], instruction=dict(en=row['case']['instruction']['en'],
                                                                                   zh=row['case']['instruction']['zh']),
                 metrics=row['metrics'], outcome=dict(held_parts=row['outcome']['held_parts'],
                                                     contacted_parts=row['outcome']['contacted_parts'],
                                                     max_lift_m=row['outcome']['max_lift_m']),
                 error=row['error'], keyframes=row['keyframes'],
                 reference_identical=None if not row.get('reference') else row['reference']['identical'])
            for row in rows]
    complete = not missing
    summary = dict(schema=SUMMARY_SCHEMA, status=status or ('finalized' if complete else 'incomplete'),
                   policy=rows[0]['policy'] if rows else config['policy'], policy_spec=config['policy_spec'],
                   is_vla=bool(rows) and all(row['policy'].get('is_vla') is True for row in rows),
                   dataset=config['dataset'], plan_sha256=config['plan_sha256'], splits=config['splits'],
                   language=config['language'], horizon=config['horizon'], execute_steps=config['execute_steps'],
                   conditions=conditions(config), planned=len(config['case_ids']), finished=len(rows), missing=missing,
                   results=summarize(evaluated) if evaluated else None,
                   controls=summarize(controls)['overall'] if controls else None,
                   reference=dict(compared=len(reference_rows),
                                  identical=sum(row['reference']['identical'] for row in reference_rows),
                                  physics_identical=sum(reference_physics_identical(row['reference'])
                                                        for row in reference_rows),
                                  excluded_physics_variant=len(compared_rows)-len(reference_rows)
                                  ) if compared_rows else None,
                   recheck=recheck, elapsed_wall_s=elapsed_wall_s, runs=runs)
    summary['harness_checks'] = dict(
        all_planned_finished=complete,
        recheck_identical=None if recheck is None else all(item['identical'] for item in recheck),
        reference_identical=None if not reference_rows else summary['reference']['identical'] == len(reference_rows),
        reference_physics_identical=None if not reference_rows
        else summary['reference']['physics_identical'] == len(reference_rows))
    return summary


def compare_runs(first, second):
    """Byte comparison of two evaluation episodes (result, trace and keyframes; not timing)."""
    differing = [name for name in (RESULT_FILE, TRACE_FILE)
                 if (first/name).read_bytes() != (second/name).read_bytes()]
    frames = sorted(path.name for path in (first/'keyframes').glob('*.png')) if (first/'keyframes').is_dir() else []
    other = sorted(path.name for path in (second/'keyframes').glob('*.png')) if (second/'keyframes').is_dir() else []
    if frames != other:
        differing.append('keyframes/(file list)')
    else:
        differing += ['keyframes/'+name for name in frames
                      if (first/'keyframes'/name).read_bytes() != (second/'keyframes'/name).read_bytes()]
    return dict(case_id=first.name, identical=not differing, differing=differing[:20], keyframes_compared=len(frames))


def run_worker(root, case_ids, *, subdir='episodes', stop=lambda: False, log=print):
    import shutil
    import traceback

    from .engine import Engine

    config = read_json(root/'eval_config.json')
    dataset = Path(config['dataset'])
    scene = conditions(config)
    _, cases, _ = select_cases(dataset, set(config['splits']), scene['scene_set'])
    lookup = {case.case_id: case for case in cases}
    export_index = export_case_index(dataset) if config['reference'] else {}
    appearance = tuple(scene['appearance'])
    policy = make_policy(config['policy_spec'])
    engine = Engine()
    try:
        for case_id in case_ids:
            if stop():
                log(json.dumps(dict(event='stopped', case_id=case_id)))
                break
            directory = root/subdir/case_id
            if (directory/RESULT_FILE).is_file():
                log(json.dumps(dict(event='skip_finished', case_id=case_id)))
                continue
            if directory.exists():
                archive = root/'discarded'
                archive.mkdir(exist_ok=True)
                serial = sum(1 for _ in archive.glob(f'{subdir}-{case_id}-*'))
                shutil.move(str(directory), archive/f'{subdir}-{case_id}-{serial}')
            # Extra scenes have no recorded demonstration to compare with.
            recorded = (dataset/'episodes'/case_id/'manifest.json').is_file()
            reference = Reference(dataset, case_id, export_index) if config['reference'] and recorded else None
            try:
                result = run_eval_episode(engine, lookup[case_id], policy, directory, language=config['language'],
                                          horizon=config['horizon'], execute_steps=config['execute_steps'],
                                          keyframe_every=config['keyframe_every'], reference=reference,
                                          anti_roll=scene['anti_roll'], appearance=appearance)
            except Exception as error:
                log(json.dumps(dict(event='episode_error', case_id=case_id, error=str(error),
                                    traceback=traceback.format_exc())))
                continue
            metrics = result['metrics']
            log(json.dumps(dict(event='episode', case_id=case_id, success=metrics['success'],
                                part_hit=metrics['part_hit'], collision=metrics['collision'],
                                reasons=metrics['failure_reasons'],
                                reference_identical=None if reference is None else result['reference']['identical'],
                                physics_identical=None if reference is None
                                else result['reference']['physics_identical'])))
    finally:
        policy.close()
        engine.close()


def config_document(policy_spec_value, policy_description, dataset_root, plan, cases, *, splits, language, horizon,
                    execute_steps, keyframe_every, reference, commit, scene_set='dataset', scene_set_sha256=None,
                    anti_roll=False, appearance=()):
    from .p5_variation import APPEARANCE_SEED
    return dict(schema=CONFIG_SCHEMA, policy_spec=policy_spec_value, policy=policy_description,
                dataset=str(dataset_root), plan_sha256=plan['plan_sha256'], scene_set=scene_set,
                scene_set_sha256=scene_set_sha256, anti_roll=anti_roll, appearance=list(appearance),
                appearance_seed=APPEARANCE_SEED if appearance else None, splits=list(splits), language=language,
                horizon=horizon, execute_steps=execute_steps, keyframe_every=keyframe_every, reference=reference,
                case_ids=[case.case_id for case in cases], observation_layout=OBSERVATION_LAYOUT,
                metrics_version=METRICS_VERSION, code_commit=commit, settle_reference_tick=SETTLE_TICKS - 1,
                thresholds=dict(obstacle_moved_m=OBSTACLE_MOVED_M, disturb_rotation_deg=DISTURB_ROTATION_DEG,
                                disturb_move_m=DISTURB_MOVE_M))


def describe_without_loading(spec):
    """Policy description for the config file, without starting a service connection."""
    if spec['kind'] == 'adapter':
        return dict(policy_id='adapter:'+spec['adapter'], privileged=False, is_vla=None,
                    endpoint=spec.get('endpoint') or None)
    return make_policy(spec).describe()

