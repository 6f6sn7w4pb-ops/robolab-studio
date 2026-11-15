"""Verify solver-pose visualization and record a small real Panda initial-state matrix."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'outputs'/'p3c_verification')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.environ.update(MUJOCO_GL='egl', PYOPENGL_PLATFORM='egl', HF_HUB_OFFLINE='1',
                      TRANSFORMERS_OFFLINE='1', ROBOLAB_P2_OUTPUT_DIR=str(out/'episodes'))
    os.environ.pop('DISPLAY', None)
    os.environ.pop('WAYLAND_DISPLAY', None)
    sys.path.insert(0, str(ROOT))
    from robolab.config import LIBERO_ROOT
    sys.path.insert(0, str(LIBERO_ROOT))
    import numpy as np
    from PIL import Image
    from robolab.contact_store import load_contact_episode
    from robolab.engine import Engine
    from robolab.episode_store import inspect_episode

    engine = Engine()
    result = {'passed': False, 'is_vla': False, 'runs': [], 'view': {}}
    started = time.monotonic()

    def live_snapshot():
        data = engine.data
        return (float(data.time), *(getattr(data, key).copy() for key in ('qpos', 'qvel', 'ctrl', 'qacc_warmstart')))

    def unchanged(snapshot):
        now = live_snapshot()
        assert snapshot[0] == now[0]
        for before, after in zip(snapshot[1:], now[1:]):
            np.testing.assert_array_equal(before, after)

    def render_live(sample):
        live = live_snapshot()
        engine.configure_contact_view(True, 'target', True)
        engine.render()
        view = engine.rendered_contacts
        assert view['solver_time_s'] == sample['solver_step_start_time_s']
        assert view['end_time_s'] == live[0] and view['drawn'] >= 2
        shadow = engine.contact_view_data
        assert shadow.time == view['solver_time_s'] and shadow.ncon == 0
        transform = np.asarray(sample['T_world_gripper'])
        np.testing.assert_allclose(shadow.site_xpos[engine.robot.site], transform[:3, 3], atol=1e-9, rtol=0)
        np.testing.assert_allclose(shadow.site_xmat[engine.robot.site].reshape(3, 3), transform[:3, :3], atol=1e-9, rtol=0)
        for recorded in sample['camera_calibration']:
            cam = engine.model.camera(recorded['name']).id
            reconstructed = shadow.cam_xmat[cam].reshape(3, 3) @ np.diag([1., -1., -1.])
            expected = np.asarray(recorded['T_world_camera'])
            np.testing.assert_allclose(shadow.cam_xpos[cam], expected[:3, 3], atol=1e-9, rtol=0)
            np.testing.assert_allclose(reconstructed, expected[:3, :3], atol=1e-9, rtol=0)
        overlay = []
        for enabled in (False, True):
            engine.configure_contact_view(enabled, 'target', True)
            timings = []
            for _ in range(12):
                tick = time.monotonic()
                engine.render()
                timings.append((time.monotonic()-tick)*1000)
            overlay.append(float(np.median(timings)))
        engine.configure_contact_view(True, 'obstacle', True)
        engine.render()
        assert engine.rendered_contacts['drawn'] == 0
        assert engine.rendered_contacts['total'] >= 2
        unchanged(live)
        result['view'].update(live_sample=view, viewport_render_median_ms=dict(off=overlay[0], on=overlay[1]),
                              site_and_camera_transforms_verified=True, live_physics_unchanged=True)
        engine.configure_contact_view(False)

    def render_history(directory, frame):
        live = live_snapshot()
        info = engine.open_replay(directory)
        assert info['supported'] and info['can_view_contacts'] and info['rendered_contacts'] is None
        first = engine.replay_frame(0, contact_view=True)
        assert first['contact_view'] and first['rendered_contacts'] is None
        index = frame['p2_trajectory_row_index']
        hashes = {}
        for camera in ('agentview', 'robot0_eye_in_hand', 'gripper'):
            packet = engine.replay_frame(index, contact_view=True, camera=camera, forces=True)
            assert packet['rendered_time_s'] == frame['solver_step_start_time_s']
            assert packet['sim_time'] == frame['state_after_step_time_s']
            assert packet['rendered_contacts']['sample_index'] == index-1
            assert packet['rendered_contacts']['drawn'] >= 2
            assert engine.replay.contact_data.ncon == 0
            jpeg = base64.b64decode(packet['jpeg'])
            image = Image.open(io.BytesIO(jpeg))
            assert image.size == (360, 360)
            image.save(out/f'contact-{camera}.png')
            hashes[camera] = hashlib.sha256(jpeg).hexdigest()
        assert len(set(hashes.values())) == 3
        p2 = engine.replay_frame(index)
        assert p2['rendered_time_s'] == packet['sim_time'] and p2['rendered_contacts'] is None
        assert Image.open(io.BytesIO(base64.b64decode(p2['jpeg']))).size == (540, 360)
        unchanged(live)
        engine.close_replay()
        result['view'].update(history_render_hashes=hashes, history_index=index,
                              history_solver_time_s=packet['rendered_time_s'], history_end_time_s=packet['sim_time'],
                              history_keeps_live_state=True, history_directory=str(directory))

    try:
        for seed, init in ((0, 0), (1, 0), (0, 1), (2, 2), (3, 3)):
            engine.load_task(seed=seed, init_index=init)
            initial = hashlib.sha256(engine.data.qpos.tobytes()).hexdigest()
            engine.start_grasp_test(record_contacts=True)
            live_checked = False
            failure = None
            tick_cost = []
            try:
                for _ in range(1400):
                    if not engine.running:
                        break
                    tick = time.monotonic()
                    engine.step()
                    tick_cost.append((time.monotonic()-tick)*1000)
                    if seed == init == 0 and not live_checked:
                        sample = engine.contact_sampler.latest
                        if sample and {c['finger'] for c in sample['contacts']
                                       if c['pair_role'] == 'finger_target' and c['load_bearing']} == {'finger_1', 'finger_2'}:
                            render_live(sample)
                            live_checked = True
            except RuntimeError as error:
                failure = str(error)
                assert not engine.running and engine.last_episode and engine.last_episode['status'] == 'failed'
            assert not engine.running and engine.last_episode
            directory = Path(engine.last_episode['directory'])
            record = inspect_episode(directory)
            manifest, frames = load_contact_episode(directory)
            active = [frame for frame in frames if
                      {c['finger'] for c in frame['contacts'] if c['pair_role'] == 'finger_target'
                       and c['load_bearing'] and c['surface_region'] == 'pad'} == {'finger_1', 'finger_2'}]
            run = dict(seed=seed, init=init, initial_qpos_sha256=initial, verdict=record['classification'],
                       p2_passed=record['p2_passed'], p3_frames=manifest['sample_count'], directory=str(directory),
                       bilateral_pad_frames=len(active), failure=failure, max_lift_m=record['metrics']['max_height_gain_m'],
                       step_median_ms=float(np.median(tick_cost)), step_p95_ms=float(np.percentile(tick_cost, 95)),
                       trace_bytes=(directory/'p3_contacts.jsonl').stat().st_size)
            result['runs'].append(run)
            print(f'P3 init={init} seed={seed}: {run["verdict"]}, {len(frames)} samples, {len(active)} bilateral pad frames', flush=True)
            if seed == init == 0:
                assert active and live_checked
                render_history(directory, active[len(active)//2])
        assert result['runs'][0]['initial_qpos_sha256'] == result['runs'][1]['initial_qpos_sha256']
        result['distinct_initial_qpos'] = len({run['initial_qpos_sha256'] for run in result['runs']})
        assert result['distinct_initial_qpos'] == 4
        engine.reset()
        engine.configure_contact_view(True)
        engine.step()
        engine.render()
        assert engine.rendered_contacts is not None
        engine.configure_policy('mock')
        engine.start_policy(2)
        end = time.monotonic()+30
        while engine.running and time.monotonic() < end:
            engine.step()
            time.sleep(.001)
        assert not engine.running and engine.execution['steps'] == 2 and engine.execution['status'] == 'completed'
        assert not engine.policy_status()['real_inference_verified']
        engine.render()
        assert engine.rendered_contacts is None and engine.contact_sampling_status()['availability'] == 'unsupported'
        images = engine.camera_images()
        assert set(images) == {'agentview', 'robot0_eye_in_hand'}
        assert all(image['width'] == image['height'] == 224 for image in images.values())
        engine.reset()
        engine.render()
        assert engine.rendered_contacts is None
        engine.step()
        engine.render()
        assert engine.rendered_contacts is not None
        result.update(passed=True, mock_osc_steps=2, dual_rgb_regression=True)
    except Exception:
        result['traceback'] = traceback.format_exc()
        traceback.print_exc()
    finally:
        engine.close()
        result['elapsed_wall_s'] = round(time.monotonic()-started, 3)
        (out/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
