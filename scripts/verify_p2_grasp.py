"""P2：重复真实规则抓取、轨迹完整性与未抓住的物理对照；无 VLA。"""
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
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


def validate_trajectory(path, metadata):
    import numpy as np
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows)==metadata['sample_count'] and len(rows)>1
    assert hashlib.sha256(path.read_bytes()).hexdigest()==metadata['trajectory_sha256']
    times=np.array([row['sim_time'] for row in rows])
    assert np.all(np.diff(times)>0)
    servo=np.array([row['joint_servo_rad'] for row in rows])
    maximum=float(np.max(np.abs(np.diff(servo,axis=0))/np.diff(times)[:,None]))
    assert maximum<=.600001,maximum
    controls=np.array([row['actuator_control_native'] for row in rows])
    bounds=np.array(metadata['control']['actuator_ctrlrange_native'])
    assert np.isfinite(controls).all() and np.all(controls>=bounds[:,0]-1e-8) and np.all(controls<=bounds[:,1]+1e-8)
    assert all(len(row['joint_position_rad'])==7 and len(row['finger_target_contact'])==2 for row in rows)
    return {'samples':len(rows),'strictly_increasing_time':True,'max_servo_speed_rad_s':maximum,'actuator_bounds_verified':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'outputs'/'p2_verification')
    args=parser.parse_args()
    if not 2<=args.repeats<=5:parser.error('--repeats must be 2–5')
    out=args.output_dir.expanduser().resolve();out.mkdir(parents=True,exist_ok=True)
    os.environ.update(MUJOCO_GL='egl',PYOPENGL_PLATFORM='egl',PYTHONDONTWRITEBYTECODE='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',ROBOLAB_P2_OUTPUT_DIR=str(out/'episodes'))
    os.environ.pop('DISPLAY',None);os.environ.pop('WAYLAND_DISPLAY',None)
    sys.dont_write_bytecode=True;sys.path.insert(0,str(ROOT))
    from robolab.config import LIBERO_ROOT
    sys.path.insert(0,str(LIBERO_ROOT))
    from robolab.engine import Engine
    from robolab.task_control import ScriptedPickPlace
    from PIL import Image,ImageDraw
    e=Engine();results=[];negative=None;frames=[];start=time.monotonic();passed=False;lift_saved=False
    try:
        e.load_task(seed=0,init_index=0)
        for repeat in range(args.repeats):
            e.reset();e.start_grasp_test()
            for step in range(1400):
                if not e.running:break
                e.step()
                if repeat==0 and step%10==0:
                    images=e.camera_images();canvas=Image.new('RGB',(448,248),(25,32,42))
                    for x,name in [(0,'agentview'),(224,'robot0_eye_in_hand')]:
                        canvas.paste(Image.open(io.BytesIO(base64.b64decode(images[name]['data']))),(x,24))
                    ImageDraw.Draw(canvas).text((4,5),f'P2 scripted / not VLA   t={e.data.time:.2f}s',fill='white')
                    frames.append(canvas)
                if repeat==0 and e.execution.get('metrics',{}).get('lift_verified') and not lift_saved:
                    e.screenshot(out/'lift.png')
                    lift_saved=True
            assert not e.running and e.last_episode and e.last_episode['p2_passed'],e.execution
            metadata=json.loads(Path(e.last_episode['metadata']).read_text())
            checks=validate_trajectory(Path(e.last_episode['trajectory']),metadata)
            results.append(dict(e.last_episode,checks=checks,final_qpos=metadata['final_qpos']))
            print('P2 重复',repeat+1,'通过：',json.dumps(metadata['metrics'],ensure_ascii=False),flush=True)
        e.screenshot(out/'placed.png')
        frames[0].save(out/'scripted_grasp.gif',save_all=True,append_images=frames[1:],duration=200,loop=0,optimize=False)
        e.reset()
        # Fault injection only in this verification process: the gripper moves,
        # but IK cannot approach the object. Never label this as a successful run.
        with patch.object(ScriptedPickPlace,'_ik',return_value=None):
            e.start_grasp_test()
            e.execution_runner.recorder.metadata['verification_only_fault_injection']='disable_scripted_ik'
            e.execution_runner.recorder._write_metadata()
            try:
                for _ in range(1400):
                    if not e.running:break
                    e.step()
            except RuntimeError as error:
                assert 'P2' in str(error),str(error)
            assert not e.running and e.last_episode['status']=='failed' and not e.last_episode['p2_passed']
            assert not e.last_episode['metrics']['lift_verified']
            negative=dict(e.last_episode,fault_injection='disable_scripted_ik')
            negative['checks']=validate_trajectory(Path(negative['trajectory']),json.loads(Path(negative['metadata']).read_text()))
            print('P2 未抓住的物理对照：正确判为失败',flush=True)
        import numpy as np
        maximum=max(float(np.max(np.abs(np.asarray(item['final_qpos'])-np.asarray(results[0]['final_qpos'])))) for item in results)
        assert maximum<=1e-9,maximum
        passed=True
    except Exception:
        traceback.print_exc()
    finally:
        e.close()
        summary={'passed':passed,'repeats':results,'negative_control':negative,
                 'elapsed_wall_s':round(time.monotonic()-start,3),'same_seed_final_qpos_max_abs_difference':maximum if passed else None,
                 'is_vla':False,'real_vla_tested':False,'contact_point_dataset_tested':False}
        (out/'result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
        print('P2 报告:',out/'result.json','passed=',passed,flush=True)
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
