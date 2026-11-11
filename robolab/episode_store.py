"""Read-only P2 inventory and integrity checks; standard library only.

An unfinalized recording is incomplete, even if its last frame looks successful.
The original verdict is never recomputed from replayed/rendered state.
"""
import hashlib
import json
import math
import os
from pathlib import Path

from .config import ROOT

SCHEMAS = ('robolab.p2_episode.v1', 'robolab.p2_episode.v2')
MAX_BYTES = 128_000_000


def output_root():
    return Path(os.getenv('ROBOLAB_P2_OUTPUT_DIR', str(ROOT/'outputs'/'p2_grasp'))).expanduser().resolve()


def read_json(path):
    if path.stat().st_size > MAX_BYTES: raise ValueError('P2 file exceeds 128 MB reader limit')
    def reject(value): raise ValueError('Non-finite JSON value: '+value)
    return json.loads(path.read_text(encoding='utf-8'), parse_constant=reject)


def load_episode(directory):
    directory = Path(directory).expanduser().resolve()
    metadata = read_json(directory/'metadata.json')
    if not isinstance(metadata, dict) or metadata.get('schema') not in SCHEMAS:
        raise ValueError('Unsupported P2 schema')
    if not isinstance(metadata.get('task', {}), dict) or not isinstance(metadata.get('metrics', {}), dict):
        raise ValueError('Invalid P2 metadata objects')
    if not isinstance(metadata.get('task', {}).get('language', ''), str): raise ValueError('Invalid task language')
    path = directory/'trajectory.jsonl'
    if path.stat().st_size > MAX_BYTES: raise ValueError('P2 trajectory exceeds 128 MB reader limit')
    payload = path.read_bytes()
    rows = [json.loads(line) for line in payload.splitlines()]
    if len(rows) > 100_000: raise ValueError('P2 sample count exceeds reader limit')
    final = metadata.get('status') in ('succeeded', 'failed', 'cancelled')
    if final:
        if isinstance(metadata.get('sample_count'), bool) or not isinstance(metadata.get('sample_count'), int):
            raise ValueError('Invalid P2 sample count')
        if not rows or metadata.get('sample_count') != len(rows): raise ValueError('P2 sample count mismatch')
        if metadata.get('trajectory_sha256') != hashlib.sha256(payload).hexdigest():
            raise ValueError('P2 trajectory SHA256 mismatch')
    previous = -math.inf
    for index, row in enumerate(rows):
        t = row.get('sim_time')
        if (isinstance(row.get('index'), bool) or not isinstance(row.get('index'), int) or row.get('index') != index or isinstance(t, bool) or not isinstance(t, (int, float))
                or not math.isfinite(t) or t <= previous):
            raise ValueError('P2 sample index/time invalid')
        previous = t
        for field, size in (('joint_position_rad', 7), ('eef_position_world_m', 3), ('target_position_world_m', 3)):
            values = row.get(field)
            if not isinstance(values, list) or len(values) != size or any(
                    isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
                raise ValueError('P2 invalid sample field: '+field)
    if metadata['schema'] == SCHEMAS[1]:
        view = metadata.get('state_view', {})
        if view.get('semantics') != 'sampled_scene_state_v1': raise ValueError('Unsupported state-view semantics')
        model_path = directory/'model.xml'
        if model_path.stat().st_size > MAX_BYTES: raise ValueError('P2 model exceeds reader limit')
        if hashlib.sha256(model_path.read_bytes()).hexdigest() != view.get('model_file_sha256'):
            raise ValueError('P2 model XML SHA256 mismatch')
        dims = view['dimensions']
        if any(isinstance(dims.get(k), bool) or not isinstance(dims.get(k), int) or dims[k]<0
               for k in ('nq','nv','na','nu','nmocap','nuserdata')):
            raise ValueError('Invalid state dimensions')
        sizes = dict(qpos=dims['nq'],qvel=dims['nv'],act=dims['na'],ctrl=dims['nu'],
                     mocap_pos=3*dims['nmocap'],mocap_quat=4*dims['nmocap'],userdata=dims['nuserdata'],
                     qacc_warmstart=dims['nv'],qfrc_applied=dims['nv'])
        def flat(values):
            if not isinstance(values,list): raise ValueError('Invalid scene state array')
            return [entry for v in values for entry in (flat(v) if isinstance(v,list) else [v])]
        for row in rows:
            state = row.get('scene_state', {})
            for name, size in sizes.items():
                values = flat(state.get(name))
                if len(values)!=size or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in values):
                    raise ValueError('Invalid scene state array: '+name)
            forces = flat(state.get('xfrc_applied'))
            if not forces or len(forces)%6 or any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) for v in forces):
                raise ValueError('Invalid applied force array')
    return metadata, rows


def inspect_episode(directory):
    from .contact_store import inspect_contacts

    directory = Path(directory).resolve()
    result = {'directory': str(directory), 'id': directory.name, 'classification': 'incomplete', 'sample_count': 0}
    try:
        if not (directory/'metadata.json').is_file():
            result['reason'] = 'missing_metadata'
            return result
        m = read_json(directory/'metadata.json')
        if not isinstance(m, dict) or m.get('schema') not in SCHEMAS: raise ValueError('Unsupported P2 schema')
        if not isinstance(m.get('task', {}), dict) or not isinstance(m.get('metrics', {}), dict):
            raise ValueError('Invalid P2 metadata objects')
        if not isinstance(m.get('task', {}).get('language', ''), str): raise ValueError('Invalid task language')
        result.update(task=m.get('task', {}), schema=m['schema'], created_at_utc=m.get('created_at_utc', ''),
                      original_status=m.get('status'), sample_count=m.get('sample_count', 0), metrics=m.get('metrics', {}),
                      official_task_success=m.get('official_task_success'), p2_passed=False)
        if m.get('status') == 'recording':
            result['reason'] = 'unfinalized'
            result['contact_recording'] = inspect_contacts(directory, m)
            return result
        if m.get('status') not in ('succeeded', 'failed', 'cancelled'): raise ValueError('Unknown P2 completion status')
        m, rows = load_episode(directory)
        metrics = m.get('metrics', {})
        passed = (m['status'] == 'succeeded' and m.get('p2_passed') is True
                  and m.get('official_task_success') is True and metrics.get('finger_contact_verified') is True
                  and metrics.get('lift_verified') is True)
        result.update(classification='success' if passed else 'cancelled' if m['status'] == 'cancelled' else 'failed',
                      sample_count=len(rows), p2_passed=passed, integrity_verified=True,
                      state_view_supported=m['schema'] == SCHEMAS[1],
                      contact_recording=inspect_contacts(directory, m, rows))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        result.update(classification='corrupt', reason=str(error), state_view_supported=False)
    return result


def list_episodes(root=None):
    root = Path(root).expanduser().resolve() if root else output_root()
    if not root.exists(): return {'root': str(root), 'records': [], 'truncated': False}
    folders = sorted((p for p in root.iterdir() if p.is_dir() and not p.is_symlink()
                      and any((p/name).exists() for name in ('metadata.json','trajectory.jsonl','model.xml'))), reverse=True)
    return {'root': str(root), 'records': [inspect_episode(p) for p in folders[:200]], 'truncated': len(folders)>200}
