"""Run and verify the lightweight P4 part-grasp prototype."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]


def save_projection_preview(directory, frame):
    from PIL import Image, ImageDraw

    canvas = Image.new('RGB', (448, 250), '#18283e')
    labels = ('Desk', 'Wrist')
    for offset, camera in enumerate(('agentview', 'robot0_eye_in_hand')):
        with Image.open(directory/frame['images'][camera]['file']) as image:
            rgb = image.convert('RGB')
        draw = ImageDraw.Draw(rgb)
        center = frame['requested_center_projections'][camera]
        if center['in_frame']:
            x, y = center['uv_px']
            draw.line((x-5, y, x+5, y), fill='white', width=2)
            draw.line((x, y-5, x, y+5), fill='white', width=2)
        for contact in frame['projected_pad_contacts']:
            projection = contact['projections'][camera]
            if projection['in_frame']:
                x, y = projection['uv_px']
                color = '#ffa044' if contact['finger'] == 'finger_1' else '#47b7ff'
                draw.ellipse((x-3, y-3, x+3, y+3), outline=color, width=2)
        canvas.paste(rgb, (224*offset, 0))
    draw = ImageDraw.Draw(canvas)
    for i, name in enumerate(labels):
        draw.text((224*i+5, 226), f"{name}  t={frame['solver_time_s']:.3f} s", fill='white')
        draw.text((224*i+5, 238), 'Projected labels (occlusion unknown)', fill='white')
    canvas.save(directory/'projection-preview.png')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--part', choices=('pair', 'thick', 'thin'), default='pair')
    parser.add_argument('--accept-cancel-stdin', action='store_true', help=argparse.SUPPRESS)
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
    from robolab.p4_recording import run_part_episode, verify_part_episode

    stop = threading.Event()

    def cancel(*_):
        stop.set()

    def read_cancel():
        if sys.stdin.readline().strip() == 'cancel':
            stop.set()

    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    if args.accept_cancel_stdin:
        threading.Thread(target=read_cancel, daemon=True).start()
    started = time.monotonic()
    result = dict(passed=False, status='running', is_vla=False, dataset_ready=False, runs=[])
    engine = Engine()
    cases = [('thick', 'thick', None), ('thin', 'thin', None), ('wrong_part_control', 'thick', 'thin')]
    if args.part != 'pair':
        cases = [(args.part, args.part, None)]
    try:
        signatures = []
        for name, requested, aimed in cases:
            if stop.is_set():
                break
            print(f'P4 run: {name}', flush=True)
            directory = out/name
            manifest = run_part_episode(engine, directory, requested, aimed_part=aimed, cancelled=stop.is_set)
            outcome = manifest['outcome']
            run = dict(name=name, status=manifest['status'], instruction=manifest['instruction'],
                       verification_override=manifest['teacher']['verification_override'],
                       contact_samples=manifest['contact_samples'], rgb_pairs=manifest['rgb_pairs'],
                       outcome=outcome)
            result['runs'].append(run)
            if stop.is_set():
                break
            manifest, frames = verify_part_episode(directory)
            signatures.append((manifest['model_xml_sha256'], manifest['initial_qpos'], manifest['initial_qvel']))
            frame_index = manifest['representative_sample_index']
            if frame_index is not None:
                frame = next(row for row in frames if row['sample_index'] == frame_index)
                if not frame['projected_pad_contacts']:
                    raise ValueError('Representative frame has no projected pad contacts')
                save_projection_preview(directory, frame)
                run['preview_frame'] = frame_index
                run['preview_solver_time_s'] = frame['solver_time_s']
            run['files_verified'] = True
            run['model_xml_sha256'] = manifest['model_xml_sha256']
            if aimed is None:
                run['expected_outcome_verified'] = outcome['passed'] and outcome['held_parts'] == [requested]
            else:
                run['expected_outcome_verified'] = (outcome['lifted'] and not outcome['part_matched']
                    and not outcome['passed'] and outcome['held_parts'] == [aimed])
            print(f"RESULT {name}: held={outcome['held_parts']} lift={outcome['max_lift_m']:.4f} m "
                  f"passed={outcome['passed']} pairs={len(frames)}", flush=True)
        if stop.is_set():
            result['status'] = 'cancelled'
        else:
            if len(signatures) > 1:
                result['same_scene_and_initial_state'] = all(signature == signatures[0] for signature in signatures)
            result['passed'] = (result.get('same_scene_and_initial_state', True)
                                and all(run['expected_outcome_verified'] for run in result['runs']))
            result['status'] = 'finalized'
    except Exception:
        result['traceback'] = traceback.format_exc()
        result['status'] = 'capture_error'
        traceback.print_exc()
    finally:
        engine.close()
        result['elapsed_wall_s'] = round(time.monotonic()-started, 3)
        (out/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"P4 result: {out} / {result['status']}", flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
