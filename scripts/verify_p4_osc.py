"""Run the actual OSC controller and replay its saved commands on synthetic scenes."""
import argparse
import os
from pathlib import Path
import sys
import time
import traceback

from verify_p4_parts import save_projection_preview


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'robolab.p4_osc_audit.v1'


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
    from robolab.engine import Engine
    from robolab.p4_parts import INSTRUCTIONS
    from robolab.p4_osc_recording import run_osc_episode, verify_osc_episode
    from robolab.p4_pilot_scene import blocked_control, pilot_scenes
    from robolab.p4_recording import publish_json

    scenes = {scene.scene_id: scene for scene in pilot_scenes()}
    same_scene = scenes['box_step_0']
    cases = [(same_scene, 'thick'), (same_scene, 'thin'), (scenes['ellipsoid_step_1'], 'thin'), (blocked_control(), 'thin')]
    result = dict(schema=SCHEMA, passed=False, is_vla=False, dataset_ready=False, status='recording', runs=[])
    output = out/'result.json'
    publish_json(output, result)
    engine = Engine()
    start = time.monotonic()
    paired_initial = []
    try:
        for scene, part in cases:
            control = scene.split == 'control'
            name = f'{scene.scene_id}_{part}'
            relative = Path('episodes')/name
            directory = out/relative
            run = dict(path=relative.as_posix(), scene_id=scene.scene_id, part=part, split='control' if control else 'osc_check',
                       files_verified=False, instruction=dict(en=INSTRUCTIONS[part][0], zh=INSTRUCTIONS[part][1]))
            try:
                manifest = run_osc_episode(engine, directory, scene, part)
                run.update(outcome=manifest['outcome'], rgb_pairs=manifest['rgb_pairs'])
                verified, frames, replay = verify_osc_episode(engine, directory, scene)
                if scene is same_scene:
                    paired_initial.append((verified['model_xml_sha256'], verified['initial']))
                representative = verified['representative_sample_index']
                if representative is not None:
                    save_projection_preview(directory, frames[representative])
                outcome = verified['outcome']
                expected = (not outcome['passed'] and outcome['finger_obstacle_frames'] > 0
                            if control else outcome['passed'] and outcome['held_parts'] == [part])
                run.update(files_verified=True, expected_outcome_verified=expected, replay=replay,
                           model_xml_sha256=verified['model_xml_sha256'])
                print(f'P4 OSC {name}: passed={outcome["passed"]} lift={outcome["max_lift_m"]:.4f} '
                      f'expected={expected} replay={replay}', flush=True)
            except Exception as error:
                run.update(error=str(error), traceback=traceback.format_exc())
                traceback.print_exc()
            result['runs'].append(run)
            publish_json(output, result)
        result['same_initial_scene_for_box_pair'] = len(paired_initial) == 2 and paired_initial[0] == paired_initial[1]
        result['passed'] = result['same_initial_scene_for_box_pair'] and all(
            run['files_verified'] and run['expected_outcome_verified'] for run in result['runs'])
        result['status'] = 'finalized' if all(run['files_verified'] for run in result['runs']) else 'verification_error'
    finally:
        engine.close()
        result['elapsed_wall_s'] = round(time.monotonic()-start, 3)
        publish_json(output, result)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
