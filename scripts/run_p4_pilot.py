"""Generate and verify the 24-episode P4 pilot and one physical obstacle control."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'robolab.p4_pilot_index.v1'
SPLITS = ('train', 'validation', 'test_unseen')


def check_split(runs):
    indexed = [r for r in runs if r['split'] != 'control']
    groups = defaultdict(list)
    for run in indexed:
        groups[run['scene_id']].append(run)
    if len(indexed) != 24 or len(groups) != 12:
        raise ValueError('P4 pilot must have 12 paired scenes')
    for group in groups.values():
        if len(group) != 2 or {run['part'] for run in group} != {'thick', 'thin'}:
            raise ValueError('P4 pilot scene lacks both paired instructions')
        for key in ('scene_sha256', 'initial_state_sha256', 'shape_id', 'split', 'family'):
            if group[0][key] != group[1][key]:
                raise ValueError('P4 pilot paired episodes differ: '+key)
    representative = [pair[0] for pair in groups.values()]
    if len({run['scene_sha256'] for run in representative}) != len(groups):
        raise ValueError('P4 pilot contains duplicate scene XML across groups')
    if len({run['shape_id'] for run in representative}) != len(groups):
        raise ValueError('P4 pilot contains a duplicate shape instance across groups')
    family_by_split = {split: sorted({r['family'] for r in indexed if r['split'] == split}) for split in SPLITS}
    if set(family_by_split['test_unseen']) & (set(family_by_split['train']) | set(family_by_split['validation'])):
        raise ValueError('P4 pilot has a shape-family leak into unseen evaluation')
    if len(family_by_split['test_unseen']) != 1 or len(family_by_split['train']) != 2:
        raise ValueError('P4 pilot family coverage mismatch')
    for language in ('en', 'zh'):
        known = {r['instruction'][language] for r in indexed if r['split'] != 'test_unseen'}
        held_out = {r['instruction'][language] for r in indexed if r['held_out_phrasing']}
        if len(held_out) != 2 or known & held_out:
            raise ValueError('P4 held-out prompts overlap development prompts')
    return family_by_split


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.environ.update(MUJOCO_GL='egl', PYOPENGL_PLATFORM='egl', HF_HUB_OFFLINE='1',
                      TRANSFORMERS_OFFLINE='1', PYTHONDONTWRITEBYTECODE='1')
    os.environ.pop('DISPLAY', None)
    os.environ.pop('WAYLAND_DISPLAY', None)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    from robolab.config import LIBERO_ROOT
    sys.path.insert(0, str(LIBERO_ROOT))
    from robolab.contact_store import canonical_json
    from robolab.engine import Engine
    from robolab.p4_parts import PARTS
    from robolab.p4_pilot_scene import blocked_control, pilot_scenes
    from robolab.p4_recording import publish_json, run_part_episode, verify_part_episode

    def digest(value):
        return hashlib.sha256(canonical_json(value)).hexdigest()

    stopped = False

    def cancel(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, cancel)
    signal.signal(signal.SIGTERM, cancel)
    summary = dict(schema=SCHEMA, status='recording', passed=False, dataset_ready=False, is_vla=False,
                   planned_attempts=25, runs=[])
    manifest_path = out/'result.json'
    publish_json(manifest_path, summary)
    engine = Engine()
    started = time.monotonic()
    cases = [(scene, part) for scene in pilot_scenes() for part in PARTS] + [(blocked_control(), 'thin')]
    try:
        for index, (scene, part) in enumerate(cases, 1):
            if stopped:
                break
            directory = out/'episodes'/f'{scene.scene_id}_{part}'
            run = dict(path=directory.relative_to(out).as_posix(), scene_id=scene.scene_id,
                       family=scene.family, split=scene.split, part=part, held_out_phrasing=scene.held_out_phrasing)
            try:
                manifest = run_part_episode(engine, directory, part, pilot=scene, cancelled=lambda: stopped)
                run.update(status=manifest['status'], outcome=manifest['outcome'],
                           contact_samples=manifest['contact_samples'], rgb_pairs=manifest['rgb_pairs'],
                           instruction=manifest['instruction'], files_verified=False)
                if manifest['status'] == 'finalized':
                    _, frames = verify_part_episode(directory)
                    counts = Counter(frame['nearest_obstacle']['geom'] for frame in frames)
                    nearest = min(abs(frame['nearest_obstacle']['signed_distance_m']) for frame in frames)
                    run.update(files_verified=True, scene_sha256=manifest['model_xml_sha256'],
                               initial_state_sha256=digest([manifest['initial_qpos'], manifest['initial_qvel']]),
                               shape_id=digest([scene.family, manifest['variant']]),
                               nearest_geom_counts=dict(counts), min_tcp_surface_distance_m=nearest,
                               motion_intervals=len(frames)-1)
            except Exception as error:
                run.update(status='error', files_verified=False, error=str(error), traceback=traceback.format_exc())
                traceback.print_exc()
            summary['runs'].append(run)
            publish_json(manifest_path, summary)
            verdict = run.get('outcome', {}).get('passed', False)
            print(f"P4 pilot {index}/{len(cases)} {scene.scene_id} {part}: "
                  f"verified={run['files_verified']} passed={verdict}", flush=True)
        if stopped:
            summary['status'] = 'cancelled'
        elif len(summary['runs']) != len(cases) or not all(r['files_verified'] for r in summary['runs']):
            summary['status'] = 'verification_error'
        else:
            runs = summary['runs']
            summary['family_by_split'] = check_split(runs)
            summary['splits'] = {}
            summary['successful_candidates_by_split'] = {}
            for split in SPLITS:
                entries = [r for r in runs if r['split'] == split]
                successful = [r for r in entries if r['outcome']['passed']]
                summary['splits'][split] = dict(attempts=len(entries), successes=len(successful),
                                                failures=len(entries)-len(successful))
                summary['successful_candidates_by_split'][split] = [r['path'] for r in successful]
            control = runs[-1]['outcome']
            summary['physical_obstacle_control_passed'] = not control['passed'] and control['finger_obstacle_frames'] > 0
            summary['totals'] = dict(attempts=24, successes=sum(item['successes'] for item in summary['splits'].values()),
                                     controls=1, contact_samples=sum(r['contact_samples'] for r in runs),
                                     rgb_pairs=sum(r['rgb_pairs'] for r in runs),
                                     motion_intervals=sum(r['motion_intervals'] for r in runs))
            summary['passed'] = summary['physical_obstacle_control_passed']
            summary['status'] = 'finalized'
    except Exception:
        summary['status'] = 'verification_error'
        summary['traceback'] = traceback.format_exc()
        traceback.print_exc()
    finally:
        engine.close()
        summary['elapsed_wall_s'] = round(time.monotonic()-started, 3)
        publish_json(manifest_path, summary)
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
