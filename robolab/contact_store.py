"""Read original P3 contact records without importing the simulator."""
import hashlib
import json
import math
from pathlib import Path

from .episode_store import MAX_BYTES, SCHEMAS, load_episode


MANIFEST = 'p3_contact_metadata.json'
TRACE = 'p3_contacts.jsonl'
MANIFEST_SCHEMA = 'robolab.p3_contact_episode.v1'
FRAME_SCHEMA = 'robolab.p3_contact_frame.v1'
EXPECTED = 'p3_contact_recording_expected'
FINAL_P2 = ('succeeded', 'failed', 'cancelled')
MAX_FRAME_BYTES = 1_000_000
MAX_SAMPLES = 100_000
TIME_TOLERANCE = 1e-9


def canonical_json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':')).encode('utf-8')


def _read_json(payload):
    def reject(token):
        raise ValueError('Non-finite P3 JSON constant: '+token)

    return json.loads(payload, parse_constant=reject)


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _index(value, minimum=0):
    return type(value) is int and value >= minimum


def _vector(value, size):
    return isinstance(value, list) and len(value) == size and all(_number(x) for x in value)


def _matrix(value, size):
    return isinstance(value, list) and len(value) == size and all(_vector(row, size) for row in value)


def _transform(value):
    return _matrix(value, 4) and value[-1] == [0, 0, 0, 1]


def validate_alignment(frame, index, episode_id, end_time, dt, previous_start):
    """Both writer and reader bind sample i to integrated P2 row i+1."""
    _require(frame['episode_id'] == episode_id and _index(frame['sample_index'])
             and frame['sample_index'] == index and _index(frame['p2_trajectory_row_index'], 1)
             and frame['p2_trajectory_row_index'] == index+1, 'P3 parent row or sample index mismatch')
    start, end, frame_dt = (frame[key] for key in
        ('solver_step_start_time_s', 'state_after_step_time_s', 'physics_dt_s'))
    _require(all(_number(value) for value in (start, end, frame_dt)) and start >= 0 and start > previous_start
             and end > start and frame_dt == dt and abs(end-start-dt) <= TIME_TOLERANCE
             and abs(end-end_time) <= TIME_TOLERANCE, 'P3 solver or parent time mismatch')
    return start


def _validate_contact(contact, previous, scene_count, epsilon):
    index = contact['contact_index']
    _require(_index(index) and previous < index < scene_count, 'Invalid P3 contact index')
    _require(all(_index(contact[key]) for key in ('geom1_id', 'geom2_id', 'exclude'))
             and type(contact['efc_address']) is int
             and type(contact['contact_dimension']) is int and contact['contact_dimension'] in (1, 3, 4, 6),
             'Invalid P3 contact geometry/constraint IDs')
    names = tuple(contact[key] for key in ('geom1_name', 'geom2_name', 'finger_geom_name', 'other_geom_name'))
    _require(all(isinstance(value, str) for value in names) and names[2:] in (names[:2], names[:2][::-1])
             and contact['finger'] in ('finger_1', 'finger_2')
             and contact['surface_region'] in ('pad', 'other_finger')
             and contact['position_role'] == 'solver_midpoint'
             and contact['pair_role'] in ('finger_target', 'finger_environment', 'finger_robot_other', 'finger_distractor'),
             'Invalid P3 contact identity')
    for field in ('position_world_m', 'position_gripper_m', 'normal_world_finger_to_other',
                  'normal_gripper_finger_to_other', 'force_on_other_world_N', 'force_on_other_gripper_N'):
        _require(_vector(contact[field], 3), 'Invalid P3 contact vector: '+field)
    _require(_matrix(contact['native_contact_frame_rows'], 3) and _vector(contact['native_wrench_contact'], 6),
             'Invalid P3 native contact wrench or frame')
    normal_force = contact['normal_force_N']
    active = contact['efc_address'] >= 0 and contact['exclude'] == 0
    _require(_number(normal_force) and normal_force >= 0 and _number(contact['distance_m'])
             and contact['constraint_active'] is active and contact['load_bearing'] is (active and normal_force > epsilon),
             'Invalid P3 force or constraint flags')
    return index


def _validate_frame(frame, metadata):
    profile = metadata['profile']
    _require(frame['schema'] == FRAME_SCHEMA and frame['record_origin'] == 'native_euler_step'
             and frame['sampling_mode'] == 'control_tail_substep' and frame['integrator'] == 'Euler'
             and frame['original_rgb_recorded'] is False, 'Unsupported P3 frame provenance')
    epsilon = frame['force_epsilon_N']
    _require(frame['target_body'] == profile['target_body'] and frame['reference_site'] == profile['reference_site']
             and frame['profile_sha256'] == metadata['profile_sha256']
             and _number(epsilon) and epsilon == metadata['force_epsilon_N'], 'P3 profile mismatch')
    state = frame['state_at_solver']
    dims = metadata['dimensions']
    _require(state['phase'] == 'solver_input_state' and _transform(frame['T_world_gripper'])
             and all(_vector(state[key], dims[dim])
                     for key, dim in (('qpos', 'nq'), ('qvel', 'nv'), ('ctrl_native', 'nu'))),
             'Invalid P3 solver-input state')
    cameras = frame['camera_calibration']
    _require(isinstance(cameras, list) and len(cameras) == 2
             and {c['name'] for c in cameras} == {'agentview', 'robot0_eye_in_hand'}, 'Invalid P3 camera list')
    for camera in cameras:
        _require(camera['calibration_only'] is True and camera['convention'] == 'x-right,y-down,z-forward'
                 and camera['units'] == 'm' and camera['image_rotation_deg'] == 0
                 and camera['image_width'] == camera['image_height'] == 224
                 and _matrix(camera['K'], 3) and _transform(camera['T_world_camera']),
                 'Invalid P3 camera calibration')
    scene_count, contacts = frame['scene_contact_count'], frame['contacts']
    _require(_index(scene_count) and isinstance(contacts, list) and len(contacts) <= scene_count,
             'Invalid P3 contact count')
    previous = -1
    for contact in contacts:
        previous = _validate_contact(contact, previous, scene_count, epsilon)


def _validate_manifest(metadata, parent, rows, directory):
    binding = metadata['parent']
    view = parent['state_view']
    dims = view['dimensions']
    _require(binding == dict(schema=parent['schema'], trajectory_sha256=parent['trajectory_sha256'],
                             model_xml_sha256=view['model_file_sha256'], sample_count=len(rows),
                             episode_status=parent['status'])
             and metadata['episode_id'] == directory.name, 'P3 parent episode binding mismatch')
    count = metadata['sample_count']
    _require(_index(count) and count == len(rows)-1 and count <= MAX_SAMPLES and metadata['trace_file'] == TRACE,
             'P3 expected one contact sample for each noninitial P2 row')
    _require(metadata['frame_schema'] == FRAME_SCHEMA
             and metadata['sampler_schema'] == 'robolab.p3_contact_sample.v1', 'Invalid P3 frame or sampler schema')
    _require(metadata['dimensions'] == {key: dims[key] for key in ('nq', 'nv', 'nu')}
             and metadata['mujoco_version'] == parent['versions']['mujoco'] == '2.3.7'
             and metadata['physics_dt_s'] == parent['control']['physics_timestep_s'],
             'P3 recorded model dimensions/version/timestep mismatch')
    profile = metadata['profile']
    _require(isinstance(profile['target_body'], str) and isinstance(profile['reference_site'], str)
             and hashlib.sha256(canonical_json(profile)).hexdigest() == metadata['profile_sha256'],
             'P3 profile SHA256 mismatch')
    source = metadata['source_sha256']
    _require(set(source) == {'p3_contact.py', 'contact_geometry.py', 'p3_recording.py'}
             and all(_digest(value) for value in source.values()), 'Invalid P3 source digests')
    _require(_number(metadata['force_epsilon_N']) and metadata['force_epsilon_N'] >= 0
             and _number(metadata['physics_dt_s']) and metadata['physics_dt_s'] > 0
             and metadata['original_rgb_recorded'] is False, 'Invalid P3 sampling configuration')
    return count


def _check(directory, parent, rows=None, *, collect=False):
    path = directory/MANIFEST
    trace = directory/TRACE
    if not path.exists():
        if trace.exists() or parent.get(EXPECTED) is True or (directory/(MANIFEST+'.tmp')).exists():
            return {'status': 'incomplete', 'reason': 'p3_manifest_missing'}, None, []
        return {'status': 'not_collected'}, None, []
    with path.open('rb') as stream:
        payload = stream.read(MAX_FRAME_BYTES+1)
    _require(len(payload) <= MAX_FRAME_BYTES, 'P3 manifest exceeds reader limit')
    metadata = _read_json(payload)
    _require(isinstance(metadata, dict) and isinstance(metadata.get('schema'), str), 'Invalid P3 manifest')
    if metadata['schema'] != MANIFEST_SCHEMA:
        return {'status': 'unsupported', 'reason': 'p3_schema_unsupported'}, metadata, []
    status = metadata['recording_status']
    if status == 'failed':
        return {'status': 'failed', 'reason': 'p3_write_failed'}, metadata, []
    if status == 'recording' or status == 'finalized' and not metadata.get('trace_sha256'):
        return {'status': 'incomplete', 'reason': 'p3_unfinalized'}, metadata, []
    _require(status == 'finalized' and _digest(metadata['trace_sha256']), 'Invalid P3 completion state')
    if parent['schema'] != SCHEMAS[1]:
        return {'status': 'unsupported', 'reason': 'p3_parent_unsupported'}, metadata, []
    if parent['status'] not in FINAL_P2 or rows is None:
        return {'status': 'incomplete', 'reason': 'p3_parent_unfinalized'}, metadata, []
    if not trace.is_file():
        return {'status': 'incomplete', 'reason': 'p3_trace_missing'}, metadata, []
    count = _validate_manifest(metadata, parent, rows, directory)
    frames = []
    digest = hashlib.sha256()
    first = last = None
    total = 0
    with trace.open('rb') as stream:
        for index in range(count):
            line = stream.readline(MAX_FRAME_BYTES+1)
            if not line or len(line) <= MAX_FRAME_BYTES and not line.endswith(b'\n'):
                return {'status': 'incomplete', 'reason': 'p3_trace_truncated'}, metadata, []
            total += len(line)
            _require(len(line) <= MAX_FRAME_BYTES and total <= MAX_BYTES, 'P3 trace exceeds reader limit')
            digest.update(line)
            frame = _read_json(line)
            last = validate_alignment(frame, index, directory.name, rows[index+1]['sim_time'],
                                      metadata['physics_dt_s'], rows[index]['sim_time']-TIME_TOLERANCE
                                      if index == 0 else last)
            _require(last >= rows[index]['sim_time']-TIME_TOLERANCE, 'P3 solver time precedes previous P2 state')
            _validate_frame(frame, metadata)
            if index == 0:
                first = last
            if collect:
                frames.append(frame)
        _require(not stream.read(1) and digest.hexdigest() == metadata['trace_sha256'],
                 'P3 trace SHA256 or sample count mismatch')
    _require(metadata['first_solver_time_s'] == first and metadata['last_solver_time_s'] == last,
             'P3 manifest time range mismatch')
    return {'status': 'available', 'sample_count': count, 'trace_sha256': digest.hexdigest()}, metadata, frames


def inspect_contacts(directory, parent, rows=None):
    """Return P3 capability separately from the original P2 classification."""
    try:
        return _check(directory, parent, rows)[0]
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as error:
        return {'status': 'corrupt', 'reason': 'p3_invalid_record', 'detail': str(error)}


def load_contact_episode(directory):
    """Return a verified manifest and original frames, including valid zero-frame cancellations."""
    directory = Path(directory).expanduser().resolve()
    parent, rows = load_episode(directory)
    return load_contacts_for_parent(directory, parent, rows)


def load_contacts_for_parent(directory, parent, rows):
    """Verify against an already validated, in-memory P2 episode."""
    status, metadata, frames = _check(directory, parent, rows, collect=True)
    if status['status'] != 'available':
        raise ValueError(status.get('reason', 'p3_not_collected'))
    return metadata, frames
