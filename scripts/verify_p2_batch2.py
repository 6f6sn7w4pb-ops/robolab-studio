"""Real MuJoCo P2 batch 2 checks. No model downloads, training or hardware."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--features-only', action='store_true')
    parser.add_argument('--matrix-only', action='store_true')
    parser.add_argument('--resume', action='store_true', help='Reuse finalized matching-source matrix episodes, and recheck them')
    args = parser.parse_args()
    out = ROOT/'outputs'/'p2_batch2'; out.mkdir(parents=True, exist_ok=True)
    os.environ.update(MUJOCO_GL='egl', PYOPENGL_PLATFORM='egl', PYTHONDONTWRITEBYTECODE='1',
                      HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', ROBOLAB_P2_OUTPUT_DIR=str(out/'episodes'))
    os.environ.pop('DISPLAY',None); os.environ.pop('WAYLAND_DISPLAY',None)
    sys.path.insert(0,str(ROOT))
    from robolab.config import LIBERO_ROOT
    sys.path.insert(0,str(LIBERO_ROOT))
    import numpy as np
    from robolab.engine import Engine
    from robolab.episode_store import inspect_episode, load_episode, list_episodes
    from verify_p2_grasp import validate_trajectory
    from PIL import Image

    e = Engine(); checks=[]; matrix=[]; started=time.monotonic()

    def check(name): checks.append(name); print('PASS '+name,flush=True)

    def rejects(action):
        q=e.data.qpos.copy(); target=e.robot.targets.copy()
        try: action()
        except ValueError: pass
        else: raise AssertionError('Expected rejected command')
        np.testing.assert_array_equal(q,e.data.qpos); np.testing.assert_array_equal(target,e.robot.targets)

    try:
        if not args.matrix_only:
            e.load_task()
            free=np.setdiff1d(np.arange(e.model.nq), np.r_[e.robot.qadr,e.robot.gq])
            original=e.data.qpos[free].copy()
            for axis in range(3):
                for direction in (-1,1):
                    position=e.data.site_xpos[e.robot.site].copy(); rotation=e.data.site_xmat[e.robot.site].copy()
                    delta=np.zeros(3); delta[axis]=direction*.005
                    result=e.jog_xyz(delta.tolist())
                    np.testing.assert_allclose(e.data.site_xpos[e.robot.site],position+delta,atol=.00025)
                    np.testing.assert_allclose(e.data.site_xmat[e.robot.site],rotation,atol=.003)
                    assert result['status']=='reached'
            np.testing.assert_array_equal(original,e.data.qpos[free])
            expected=e.data.qpos.copy(); e.reset(); np.testing.assert_array_equal(expected,e.data.qpos)
            check('6 world-axis edit jogs: 5mm, orientation held, object qpos unchanged; reset baseline preserved')
            for delta, frame in [([0,0,0],'world'),([.02,0,0],'world'),([.005,.005,0],'world'),([.005,0,0],'eef')]:
                rejects(lambda:e.jog_xyz(delta,frame))
            rejects(lambda:e.robot.solve_translation([100,0,0]))
            limits=e.robot.limits.copy()
            try:
                e.robot.limits[:]=np.c_[e.data.qpos[e.robot.qadr]-1e-6,e.data.qpos[e.robot.qadr]+1e-6]
                rejects(lambda:e.jog_xyz([.01,0,0]))
            finally: e.robot.limits[:]=limits
            check('Invalid frame/step, unreachable target, and verification-only locked joint bounds reject atomically')
            e.set_mode('simulate'); q=e.data.qpos.copy()
            e.jog_xyz([0,0,.005]); np.testing.assert_array_equal(q,e.data.qpos)
            rejects(lambda:e.jog_xyz([0,0,.005]))
            servo=e.robot.servo.copy()
            for _ in range(175):
                e.step()
                assert np.max(np.abs(e.robot.servo-servo)) <= .6*.02+1e-8
                servo=e.robot.servo.copy()
                if e.jog['status']!='pending': break
            assert e.jog['status']=='reached',e.jog
            check('Physical jog uses PD/slew limits, rejects pending motion; reached within 1mm')
            e.configure_policy('mock'); e.start_policy(2); e.pause()
            rejects(lambda:e.jog_xyz([0,0,.005])); e.stop_execution(); e.reset()
            e.start_grasp_test(); e.pause(); rejects(lambda:e.jog_xyz([0,0,.005]))
            e.step(); e.stop_execution()
            cancelled=e.last_episode; assert inspect_episode(cancelled['directory'])['classification']=='cancelled'
            metadata,rows=load_episode(cancelled['directory'])
            assert metadata['schema']=='robolab.p2_episode.v2'
            e.set_mode('edit'); e.add_object('box')
            saved=(e.data.qpos.copy(),e.data.qvel.copy(),e.data.ctrl.copy(),e.robot.targets.copy(),e.checkpoint[0].copy(),e.mode,e.running,float(e.data.time),e.project())
            packet=e.open_replay(cancelled['directory']); assert packet['supported']
            for index in (len(rows)-1,0):
                frame=e.replay_frame(index)
                np.testing.assert_allclose(e.replay.data.qpos,rows[index]['scene_state']['qpos'],atol=1e-12,rtol=0)
                np.testing.assert_array_equal(e.replay.data.qvel,rows[index]['scene_state']['qvel'])
                assert frame['sim_time']==rows[index]['sim_time']
            e.close_replay()
            for before,after in zip(saved[:5],(e.data.qpos,e.data.qvel,e.data.ctrl,e.robot.targets,e.checkpoint[0])):
                np.testing.assert_array_equal(before,after)
            assert saved[5:]==(e.mode,e.running,float(e.data.time),e.project())
            assert e.render().std()>10 and len(e.camera_images())==2
            check('Cancelled v2 replay uses separate scene/data; editor object, layout, targets, checkpoint and time unchanged; RGB regression passes')
            v1=next((p.parent for p in (ROOT/'outputs').glob('p2_verification/episodes/*/metadata.json')
                     if json.loads(p.read_text())['schema']=='robolab.p2_episode.v1'),None)
            assert v1 is not None,'Existing genuine v1 evidence missing'
            result=e.open_replay(str(v1)); assert not result['supported']; e.close_replay()
            check('Genuine existing v1 record opens read-only with state replay explicitly unavailable')
            (out/'features.json').write_text(json.dumps(dict(passed=True,checks=checks,cancelled=cancelled,v1=str(v1)),indent=2))

        if not args.features_only:
            for seed,init in ((0,0),(1,0),(0,1),(2,2),(3,3)):
                existing=None
                if args.resume:
                    for candidate in sorted((out/'episodes').glob('*/metadata.json'),reverse=True):
                        m=json.loads(candidate.read_text())
                        if (m['task']['seed']==seed and m['task']['init_index']==init and m['status'] in ('succeeded','failed')
                                and all(hashlib.sha256((ROOT/'robolab'/name).read_bytes()).hexdigest()==digest
                                        for name,digest in m['source']['sha256'].items())):
                            existing=m; directory=candidate.parent; break
                if existing is None:
                    e.load_task(seed=seed,init_index=init); initial=e.data.qpos.copy(); e.start_grasp_test()
                    try:
                        for _ in range(1400):
                            if not e.running: break
                            e.step()
                    except RuntimeError as error:
                        assert e.last_episode and e.last_episode['status']=='failed',str(error)
                    assert not e.running and e.last_episode
                    record=dict(e.last_episode, seed=seed, init_index=init, initial_qpos=initial.tolist())
                else:
                    if e.model is None: e.load_task(seed=seed,init_index=init)
                    record=dict(directory=str(directory),metadata=str(directory/'metadata.json'),trajectory=str(directory/'trajectory.jsonl'),
                                status=existing['status'],p2_passed=existing['p2_passed'],metrics=existing['metrics'],
                                official_task_success=existing['official_task_success'],sample_count=existing['sample_count'],
                                seed=seed,init_index=init,initial_qpos=existing['initial_qpos'],reused_finalized_episode=True)
                metadata,rows=load_episode(record['directory'])
                record['checks']=validate_trajectory(Path(record['trajectory']),metadata)
                assert inspect_episode(record['directory'])['classification']==('success' if record['p2_passed'] else 'failed')
                assert record['p2_passed']==(metadata['status']=='succeeded' and metadata['metrics']['finger_contact_verified'] and metadata['metrics']['lift_verified'] and metadata['official_task_success'] is True)
                e.open_replay(record['directory'])
                restoration_error=0.
                for index in (0,len(rows)//2,len(rows)-1):
                    frame=e.replay_frame(index)
                    restoration_error=max(restoration_error,float(np.max(np.abs(e.replay.data.qpos-rows[index]['scene_state']['qpos']))))
                    np.testing.assert_allclose(e.replay.data.qpos, rows[index]['scene_state']['qpos'],atol=1e-12,rtol=0)
                record['max_qpos_restoration_abs_error']=restoration_error
                if not matrix:
                    Image.fromarray(e.replay.renderer.render()).save(out/'reconstructed-final.png')
                e.close_replay(); matrix.append(record)
                print('MATRIX',seed,init,record['status'],'P2=',record['p2_passed'],flush=True)
                (out/'matrix.json').write_text(json.dumps(dict(verification_passed=False,runs=matrix,is_vla=False,
                    scope='Only LIBERO-Object task 0; small selected official states, not a generalization success rate',
                    elapsed_wall_s=round(time.monotonic()-started,3)),ensure_ascii=False,indent=2))
            np.testing.assert_array_equal(matrix[0]['initial_qpos'],matrix[1]['initial_qpos'])
            check('Changing seed with the same official initial state does not create a new object layout')
            summary=json.loads((out/'matrix.json').read_text()); summary['verification_passed']=True
            summary['same_official_init_different_seed_same_qpos']=True
            (out/'matrix.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
        return 0
    except Exception:
        traceback.print_exc()
        (out/'verification-failure.json').write_text(json.dumps(dict(checks=checks,runs=matrix,traceback=traceback.format_exc()),indent=2))
        return 1
    finally: e.close()


if __name__=='__main__': raise SystemExit(main())
