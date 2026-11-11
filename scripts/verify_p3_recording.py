"""Verify P3 sidecars with native scripted episodes and separate reader fixtures."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'outputs'/'p3b_verification')
    parser.add_argument('--inspect', type=Path, help='Inspect one existing episode without running simulation')
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    from robolab.episode_store import inspect_episode

    if args.inspect:
        print(json.dumps(inspect_episode(args.inspect), ensure_ascii=False, indent=2))
        return 0

    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.environ.update(MUJOCO_GL='egl', PYOPENGL_PLATFORM='egl', HF_HUB_OFFLINE='1',
                      TRANSFORMERS_OFFLINE='1', ROBOLAB_P2_OUTPUT_DIR=str(out/'episodes'))
    os.environ.pop('DISPLAY', None)
    os.environ.pop('WAYLAND_DISPLAY', None)
    from robolab.config import LIBERO_ROOT
    sys.path.insert(0, str(LIBERO_ROOT))
    from robolab.contact_store import MANIFEST, TRACE, canonical_json, load_contact_episode
    from robolab.engine import Engine
    from robolab.task_control import ScriptedPickPlace
    from robolab.worker import dispatch

    result = {'passed': False, 'is_vla': False, 'episodes': {}, 'fixtures': {}, 'legacy': {}}
    started = time.monotonic()
    engine = Engine()

    def check_record(name, expected_p2, expected_p3, count=None):
        directory = Path(engine.last_episode['directory'])
        record = inspect_episode(directory)
        assert (record['classification'], record['contact_recording']['status']) == (expected_p2, expected_p3), record
        result['episodes'][name] = record
        if expected_p3 == 'available':
            manifest, frames = load_contact_episode(directory)
            assert len(frames) == count and manifest['sample_count'] == count
            assert manifest['parent']['episode_status'] == record['original_status']
            assert manifest['original_rgb_recorded'] is False
            record['trace_bytes'] = (directory/TRACE).stat().st_size
            record['time_range_s'] = [manifest['first_solver_time_s'], manifest['last_solver_time_s']]
        else:
            frames = []
        print(f'PASS {name}: {expected_p2} / {expected_p3}', flush=True)
        return directory, frames

    def start(record_contacts):
        engine.reset()
        dispatch(engine, 'contact_configure', {'enabled': record_contacts})
        dispatch(engine, 'grasp_test', {'record_contacts': record_contacts})

    def run():
        for _ in range(1400):
            if not engine.running:
                return
            engine.step()
        raise AssertionError('Scripted episode did not finish within step limit')

    def expected_error(operate, message):
        try:
            operate()
        except (RuntimeError, ValueError) as error:
            assert message in str(error), str(error)
        else:
            raise AssertionError('Expected failure: '+message)

    def fixture(name, source, mutate, expected_p2, expected_p3, reason):
        directory = out/'reader_fixtures'/name/source.name
        shutil.copytree(source, directory)
        mutate(directory)
        record = inspect_episode(directory)
        contacts = record['contact_recording']
        assert (record['classification'], contacts['status'], contacts['reason']) == (expected_p2, expected_p3, reason), record
        result['fixtures'][name] = record
        print(f'PASS reader fixture {name}: {expected_p2} / {expected_p3}', flush=True)

    def change_manifest(directory, **fields):
        path = directory/MANIFEST
        meta = json.loads(path.read_bytes())
        meta.update(fields)
        path.write_bytes(canonical_json(meta)+b'\n')

    def change_row(directory, field, value, *, rehash):
        path = directory/TRACE
        lines = path.read_bytes().splitlines(keepends=True)
        row = json.loads(lines[0])
        row[field] = value
        lines[0] = canonical_json(row)+b'\n'
        payload = b''.join(lines)
        path.write_bytes(payload)
        if rehash:
            change_manifest(directory, trace_sha256=hashlib.sha256(payload).hexdigest())

    try:
        engine.load_task(seed=0, init_index=0)
        dispatch(engine, 'contact_configure', {'target_body': 'basket_1_main'})
        expected_error(lambda: dispatch(engine, 'grasp_test', {'record_contacts': True}), '启用接触采样')
        expected_error(lambda: dispatch(engine, 'grasp_test', {'record_contacts': 1}), '布尔')
        start(False)
        run()
        baseline, _ = check_record('baseline_no_sidecar', 'success', 'not_collected')

        start(True)
        run()
        success, frames = check_record('default_success', 'success', 'available', 1100)
        fingers = {c['finger'] for frame in frames for c in frame['contacts']
                   if c['pair_role'] == 'finger_target' and c['surface_region'] == 'pad' and c['load_bearing']}
        assert fingers == {'finger_1', 'finger_2'} and not frames[0]['contacts']
        assert (baseline/'trajectory.jsonl').read_bytes() == (success/'trajectory.jsonl').read_bytes()
        result['default_p2_trajectory_byte_identical'] = True
        result['target_pad_fingers'] = sorted(fingers)
        del frames

        with patch.object(ScriptedPickPlace, '_ik', return_value=None):
            start(True)
            parent = engine.execution_runner.recorder
            parent.metadata['verification_only_fault_injection'] = 'disable_scripted_ik'
            parent._write_metadata()
            expected_error(run, 'P2 提升未通过')
        _, frames = check_record('physical_failure_disable_ik', 'failed', 'available', 550)
        assert not any(c['pair_role'] == 'finger_target' and c['load_bearing'] for frame in frames for c in frame['contacts'])

        start(True)
        engine.stop_execution()
        check_record('immediate_cancel', 'cancelled', 'available', 0)
        start(True)
        engine.pause()
        for _ in range(3):
            dispatch(engine, 'step', {})
        cancel_dir = Path(engine.execution['record_dir'])
        active = inspect_episode(cancel_dir)
        assert active['classification'] == active['contact_recording']['status'] == 'incomplete'
        engine.stop_execution()
        cancelled, frames = check_record('cancel_after_three_steps', 'cancelled', 'available', 3)
        assert all(not frame['contacts'] for frame in frames)

        start(True)
        engine.step()
        # Deliberate I/O failure in this verification process only.
        engine.execution_runner.contact_recorder.stream.close()
        expected_error(engine.step, '写入失败')
        assert not engine.running
        check_record('injected_write_error', 'failed', 'failed')

        start(True)
        writer = engine.execution_runner.contact_recorder
        publish = writer._publish

        def fail_final_manifest():
            if writer.metadata['recording_status'] == 'finalized':
                raise OSError('verification-only final manifest error')
            publish()

        with patch.object(writer, '_publish', side_effect=fail_final_manifest):
            engine.stop_execution()
        check_record('injected_final_manifest_error', 'cancelled', 'failed')

        fixture('interrupted', cancelled, lambda d: change_manifest(d, recording_status='recording'),
                'cancelled', 'incomplete', 'p3_unfinalized')
        fixture('missing_trace', cancelled, lambda d: (d/TRACE).unlink(),
                'cancelled', 'incomplete', 'p3_trace_missing')

        def remove_sidecar(directory):
            (directory/TRACE).unlink()
            (directory/MANIFEST).unlink()

        fixture('missing_sidecar', cancelled, remove_sidecar, 'cancelled', 'incomplete', 'p3_manifest_missing')
        fixture('truncated', cancelled, lambda d: (d/TRACE).write_bytes((d/TRACE).read_bytes()[:-5]),
                'cancelled', 'incomplete', 'p3_trace_truncated')
        fixture('changed_bytes', cancelled, lambda d: change_row(d, 'scene_contact_count', 999, rehash=False),
                'cancelled', 'corrupt', 'p3_invalid_record')
        fixture('wrong_row_with_valid_hash', cancelled, lambda d: change_row(d, 'p2_trajectory_row_index', 2, rehash=True),
                'cancelled', 'corrupt', 'p3_invalid_record')
        fixture('future_schema', cancelled, lambda d: change_manifest(d, schema='robolab.p3_contact_episode.v999'),
                'cancelled', 'unsupported', 'p3_schema_unsupported')

        def change_parent_hash(directory):
            parent = json.loads((directory/MANIFEST).read_bytes())['parent']
            change_manifest(directory, parent=dict(parent, trajectory_sha256='0'*64))

        fixture('different_parent', cancelled, change_parent_hash, 'cancelled', 'corrupt', 'p3_invalid_record')
        for root in (ROOT/'outputs'/'p2_verification', ROOT/'outputs'/'p2_batch2'/'episodes'):
            for path in sorted(root.glob('**/metadata.json')):
                record = inspect_episode(path.parent)
                if record.get('classification') == 'success' and record['schema'] not in result['legacy']:
                    assert record['contact_recording']['status'] == 'not_collected'
                    result['legacy'][record['schema']] = record
                    print('PASS legacy: '+record['schema'], flush=True)
                    break
        result['passed'] = True
        return 0
    except Exception:
        result['traceback'] = traceback.format_exc()
        traceback.print_exc()
        return 1
    finally:
        engine.close()
        result['elapsed_wall_s'] = round(time.monotonic()-started, 3)
        (out/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
