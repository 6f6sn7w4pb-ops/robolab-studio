"""P3-A actual native sampling, phase/geometry checks and on/off grasp control.

Uses existing simulation environment, process-local EGL and no model weights.
Exported JSONL is verification evidence, not the future P3 sidecar format.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick',action='store_true',help='Capability/single-step checks only; no full grasp claim')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'outputs'/'p3a_verification')
    args=parser.parse_args(); out=args.output_dir.resolve(); out.mkdir(parents=True,exist_ok=True)
    os.environ.update(MUJOCO_GL='egl',PYOPENGL_PLATFORM='egl',PYTHONDONTWRITEBYTECODE='1',
                      HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',ROBOLAB_P2_OUTPUT_DIR=str(out/'episodes'))
    os.environ.pop('DISPLAY',None); os.environ.pop('WAYLAND_DISPLAY',None)
    sys.dont_write_bytecode=True; sys.path.insert(0,str(ROOT))
    from robolab.config import LIBERO_ROOT
    sys.path.insert(0,str(LIBERO_ROOT))
    import copy
    import mujoco
    import numpy as np
    from PIL import Image,ImageDraw
    from robolab.engine import Engine
    from robolab.task_control import ScriptedPickPlace
    from robolab.contact_geometry import point_to_world

    e=Engine(); checks=[]; started=time.monotonic(); result={'passed':False,'scope':'quick' if args.quick else 'full_A_batch',
        'is_vla':False,'formal_p3_sidecar_implemented':False,'p3_gui_implemented':False}

    def passed(name): checks.append(name); print('PASS '+name,flush=True)

    def rejected(action,reason=None):
        try: action()
        except ValueError as error:
            if reason: assert reason in str(error),str(error)
        else: raise AssertionError('Expected rejected contact configuration')

    def validate(sample,check_shadow=False):
        assert sample['record_origin']=='native_euler_step'
        assert abs(sample['state_after_step_time_s']-sample['solver_step_start_time_s']-.002)<1e-9
        assert sample['state_after_step_time_s']==e.data.time
        assert sample['scene_contact_count']==e.data.ncon
        T=np.asarray(sample['T_world_gripper']); R=T[:3,:3]
        assert abs(np.linalg.det(R)-1)<1e-8
        worst_pad_violation=0.
        for c in sample['contacts']:
            native=e.data.contact[c['contact_index']]
            assert c['geom1_id']==native.geom1 and c['geom2_id']==native.geom2
            np.testing.assert_array_equal(c['position_world_m'],native.pos)
            np.testing.assert_allclose(point_to_world(T,c['position_gripper_m']),c['position_world_m'],atol=1e-9,rtol=0)
            np.testing.assert_allclose(R@c['normal_gripper_finger_to_other'],c['normal_world_finger_to_other'],atol=1e-9,rtol=0)
            np.testing.assert_allclose(R@c['force_on_other_gripper_N'],c['force_on_other_world_N'],atol=1e-8,rtol=0)
            assert abs(np.linalg.norm(c['normal_world_finger_to_other'])-1)<1e-8
            raw=np.zeros(6); mujoco.mj_contactForce(e.model,e.data,c['contact_index'],raw)
            np.testing.assert_array_equal(c['native_wrench_contact'],raw)
            assert c['constraint_active']==(native.efc_address>=0 and native.exclude==0)
            assert c['load_bearing']==(c['constraint_active'] and c['normal_force_N']>sample['force_epsilon_N'])
            if c['surface_region']=='pad':
                geom=e.model.geom(c['finger_geom_name']).id
                local=e.data.geom_xmat[geom].reshape(3,3).T@(np.asarray(c['position_world_m'])-e.data.geom_xpos[geom])
                violation=max(0.,float(np.max(np.abs(local)-e.model.geom_size[geom])))
                assert violation<=abs(c['distance_m'])+1e-4,(violation,c['distance_m'])
                worst_pad_violation=max(worst_pad_violation,violation)
        if check_shadow:
            live_q=e.data.qpos.copy(); live_warm=e.data.qacc_warmstart.copy(); live_ctrl=e.data.ctrl.copy()
            shadow=copy.copy(e.data)
            shadow.qpos[:]=sample['state_at_solver']['qpos']; shadow.qvel[:]=sample['state_at_solver']['qvel']
            shadow.ctrl[:]=sample['state_at_solver']['ctrl_native']; shadow.time=sample['solver_step_start_time_s']
            mujoco.mj_forward(e.model,shadow)
            np.testing.assert_allclose(shadow.site_xpos[e.robot.site],T[:3,3],atol=1e-9,rtol=0)
            np.testing.assert_allclose(shadow.site_xmat[e.robot.site].reshape(3,3),R,atol=1e-9,rtol=0)
            np.testing.assert_array_equal(e.data.qpos,live_q); np.testing.assert_array_equal(e.data.qacc_warmstart,live_warm)
            np.testing.assert_array_equal(e.data.ctrl,live_ctrl)
        return worst_pad_violation

    def diagnostic_image(sample,path):
        old=e.camera_state()
        target=e.model.body('alphabet_soup_1_main').id
        e.camera.lookat[:]=e.data.xpos[target]; e.camera.distance=.24; e.camera.azimuth=135; e.camera.elevation=-25
        e.renderer._gl_context.make_current(); e.renderer.update_scene(e.data,camera=e.camera,scene_option=e.options)
        scene=e.renderer.scene
        def geom(kind,size,pos,rotation,rgba):
            item=scene.geoms[scene.ngeom]; mujoco.mjv_initGeom(item,kind,np.asarray(size),np.asarray(pos),np.asarray(rotation).ravel(),np.asarray(rgba,dtype=np.float32)); scene.ngeom+=1
            return item
        colors=([1.,.2,.15,.55],[.1,.7,1.,.55])
        for index,pad in enumerate(e.contact_sampler.profile.pads):
            geom(mujoco.mjtGeom.mjGEOM_BOX,e.model.geom_size[pad],e.data.geom_xpos[pad],e.data.geom_xmat[pad],colors[index])
        for c in sample['contacts']:
            if c['pair_role']!='finger_target':continue
            pos=np.asarray(c['position_world_m']); normal=np.asarray(c['normal_world_finger_to_other'])
            rgba=colors[0 if c['finger']=='finger_1' else 1][:3]+[1.]
            geom(mujoco.mjtGeom.mjGEOM_SPHERE,[.0015,0,0],pos,np.eye(3),rgba)
            arrow=geom(mujoco.mjtGeom.mjGEOM_ARROW,[0,0,0],pos,np.eye(3),rgba)
            mujoco.mjv_makeConnector(arrow,mujoco.mjtGeom.mjGEOM_ARROW,.0007,*pos,*(pos+.014*normal))
        image=Image.fromarray(e.renderer.render())
        draw=ImageDraw.Draw(image); draw.rectangle((0,0,960,40),fill=(20,27,38))
        draw.text((8,5),f"P3-A native solver t={sample['solver_step_start_time_s']:.3f}s / not VLA",fill='white')
        draw.text((8,22),'Red: finger 1 pad | Blue: finger 2 pad | dots: solver midpoints | arrows: normals',fill='white')
        image.save(path)
        for key,value in old.items():
            if key=='lookat':e.camera.lookat[:]=value
            else:setattr(e.camera,key,value)

    try:
        e.load_task(seed=0,init_index=0); e.reset()
        assert e.contact_sample() is None and e.contact_sampling_status()['availability']=='disabled'
        e.configure_contacts(); assert e.contact_sampling_status()['availability']=='not_sampled'
        profile=e.contact_sampler.profile
        (out/'profile.json').write_text(json.dumps(profile.description,ensure_ascii=False,indent=2))
        target_geom=next(iter(profile.target_geoms)); pad=profile.pads[0]
        assert profile.classify(pad,target_geom)[0][3:]==('finger_target','pad')
        assert profile.classify(target_geom,pad)[0][3:]==('finger_target','pad')
        finger_geom=e.model.geom('gripper0_finger1_collision').id
        assert profile.classify(finger_geom,target_geom)[0][4]=='other_finger'
        environment=next(i for i in range(e.model.ngeom) if i not in profile.robot_geoms|profile.dynamic_geoms|profile.target_geoms)
        assert profile.classify(pad,environment)[0][3]=='finger_environment'
        other_dynamic=next(i for i in profile.dynamic_geoms if i not in profile.target_geoms|profile.robot_geoms)
        assert profile.classify(pad,other_dynamic)[0][3]=='finger_distractor'
        assert len(profile.classify(profile.pads[0],profile.pads[1]))==1
        passed('Actual Panda site/pad profile and target/environment/distractor identity mapping verified')
        rejected(lambda:e.configure_contacts(enabled=1))
        rejected(lambda:e.configure_contacts(target_body='missing_target'),'contact_target_missing')
        rejected(lambda:e.configure_contacts(force_epsilon_N=True))
        original=e.model.opt.integrator
        try:
            e.model.opt.integrator=mujoco.mjtIntegrator.mjINT_RK4
            rejected(lambda:e.configure_contacts(),'contact_integrator_not_supported')
        finally:e.model.opt.integrator=original
        try:
            mujoco.set_mjcb_control(lambda model,data:None)
            rejected(lambda:e.configure_contacts(),'contact_control_callback_not_supported')
        finally:mujoco.set_mjcb_control(None)
        passed('Invalid parameters, target, unverified integrator and control callback rejected without changing environment')
        e.step(); sample=e.contact_sample(); validate(sample,True)
        assert sample['episode_id'] is None and sample['p2_trajectory_row_index'] is None
        assert sample['contacts']==[],sample['contacts']
        e.pause(); count=e.contact_sampling_status()['sample_count']; frozen=e.data.time
        for _ in range(3):
            packet=e.contact_sample(); packet['contacts'].append({'mutation':'test only'})
        assert e.data.time==frozen and e.contact_sampling_status()['sample_count']==count and e.contact_sample()['contacts']==[]
        e.reset(); assert e.contact_sample() is None and e.contact_sampling_status()['availability']=='stale'
        passed('Native 20ms step has 18ms solver source, empty contact distinct from missing/stale, copies do not mutate sampler')

        if not args.quick:
            e.configure_contacts(False); e.start_grasp_test(); baseline_durations=[]
            for _ in range(1400):
                if not e.running:break
                tick=time.monotonic();e.step();baseline_durations.append(time.monotonic()-tick)
            assert not e.running and e.last_episode['p2_passed'],e.execution
            baseline=dict(e.last_episode); baseline_q=e.data.qpos.copy()
            baseline_meta=json.loads(Path(baseline['metadata']).read_text())
            passed('Sampling OFF: default task executes the real successful 22s scripted grasp')

            e.reset(); e.configure_contacts(); e.start_grasp_test(); e.pause()
            rejected(lambda:e.configure_contacts(False),'请先结束策略执行')
            enabled_durations=[]; n=0; seen=set(); loaded_pad_fingers=set(); peak_force={k:0. for k in ('finger_1','finger_2')}
            max_pad_violation=0.; picture=False; first_time=last_time=None
            trace=out/'native_contact_samples.jsonl'
            with trace.open('w') as stream:
                for iteration in range(1400):
                    before_time=float(e.data.time)
                    tick=time.monotonic(); e.step(); enabled_durations.append(time.monotonic()-tick)
                    if e.data.time>before_time:
                        sample=e.contact_sample(); assert sample['sample_index']==n
                        assert sample['p2_trajectory_row_index']==n+1
                        max_pad_violation=max(max_pad_violation,validate(sample,n in (0,499,999)))
                        if first_time is None:first_time=sample['solver_step_start_time_s']
                        last_time=sample['solver_step_start_time_s']
                        for c in sample['contacts']:
                            seen.add((c['finger'],c['pair_role'],c['surface_region']))
                            if c['pair_role']=='finger_target' and c['surface_region']=='pad' and c['load_bearing']:
                                loaded_pad_fingers.add(c['finger']);peak_force[c['finger']]=max(peak_force[c['finger']],c['normal_force_N'])
                        stream.write(json.dumps(sample,allow_nan=False)+'\n'); n+=1
                        if not picture and e.execution.get('metrics',{}).get('lift_verified'):
                            diagnostic_image(sample,out/'panda-pad-contact-lift.png'); picture=True
                    if iteration==0:
                        assert not e.running and n==1 and abs(e.data.time-.02)<1e-9
                        e.play()
                    if not e.running:break
            assert n==1100 and loaded_pad_fingers=={'finger_1','finger_2'} and picture,(n,loaded_pad_fingers)
            assert e.last_episode['p2_passed'],e.execution
            enabled=dict(e.last_episode); enabled_q=e.data.qpos.copy()
            max_difference=float(np.max(np.abs(enabled_q-baseline_q))); assert max_difference<=1e-9,max_difference
            enabled_meta=json.loads(Path(enabled['metadata']).read_text())
            for key in ('status','p2_passed','sample_count','metrics','official_task_success','final_qpos'):
                assert enabled_meta[key]==baseline_meta[key],key
            # All native commands remain identical, not just the final pose.
            off_rows=[json.loads(line) for line in Path(baseline['trajectory']).read_text().splitlines()]
            on_rows=[json.loads(line) for line in Path(enabled['trajectory']).read_text().splitlines()]
            for off,on in zip(off_rows,on_rows):
                for field in ('actuator_control_native','joint_servo_rad','joint_target_rad','joint_position_rad'):
                    assert off[field]==on[field],field
            passed('Sampling ON: 1100 real samples + 1101 P2 rows; both pads load the target; commands and final physics identical to OFF')
            result.update(baseline=baseline,enabled=enabled,contact_samples=n,first_solver_time_s=first_time,last_solver_time_s=last_time,
                default_final_qpos_max_abs_difference=max_difference,all_control_commands_equal=True,
                observed_contact_roles=[list(item) for item in sorted(seen)],peak_per_contact_normal_force_N=peak_force,
                max_pad_midpoint_outside_box_m=max_pad_violation,trace_sha256=hashlib.sha256(trace.read_bytes()).hexdigest(),
                step_wall_s={name:{'median':float(np.median(values)),'p95':float(np.percentile(values,95))}
                             for name,values in [('off',baseline_durations),('on',enabled_durations)]})

            e.reset(); e.configure_contacts(); negative_samples=0;negative_loaded=0
            with patch.object(ScriptedPickPlace,'_ik',return_value=None):
                e.start_grasp_test()
                e.execution_runner.recorder.metadata['verification_only_fault_injection']='disable_scripted_ik'
                e.execution_runner.recorder._write_metadata()
                try:
                    for _ in range(1400):
                        if not e.running:break
                        previous=e.data.time; e.step()
                        if e.data.time>previous:
                            negative_samples+=1
                            negative_loaded+=sum(c['load_bearing'] and c['pair_role']=='finger_target' for c in e.contact_sample()['contacts'])
                except RuntimeError as error: assert 'P2' in str(error)
            assert e.last_episode['status']=='failed' and not e.last_episode['p2_passed']
            assert negative_samples>0 and negative_loaded==0
            result['physical_failure_control']=dict(e.last_episode,sample_count_p3=negative_samples,load_bearing_target_contacts=negative_loaded,
                                                   fault_injection='disable_scripted_ik')
            passed('Physical failure control: disabled approach IK fails the grasp; no false load-bearing target contact')

            e.reset(); e.configure_policy('mock'); e.start_policy(2); e.pause()
            assert e.contact_sample() is None and e.contact_sampling_status()['availability']=='unsupported'
            e.play(); deadline=time.monotonic()+20
            while e.running and time.monotonic()<deadline:e.step();time.sleep(.001)
            assert not e.running and e.execution['status']=='completed' and e.execution['steps']==2
            assert not e.policy_status()['real_inference_verified']
            assert e.contact_sample() is None and e.contact_sampling_status()['availability']=='unsupported'
            e.reset(); e.step(); assert e.contact_sampling_status()['availability']=='available'
            passed('Mock official OSC still executes; original contact source correctly unsupported; native sampling recovers after reset')
            e.set_mode('edit'); assert e.contact_sample() is None
            e.set_joint(0,float(e.robot.targets[0]+.001)); assert e.contact_sample() is None
            e.step(); assert e.contact_sampling_status()['availability']=='available'
            e.set_mode('edit'); e.add_object('box')
            assert not e.contact_sampling_status()['enabled'] and e.contact_sample() is None
            passed('Pose editing and model rebuild invalidate the previous contact sample and profile safely')

        result.update(passed=True,checks=checks)
        return 0
    except Exception:
        traceback.print_exc();result['traceback']=traceback.format_exc();result['checks']=checks
        return 1
    finally:
        e.close();result['elapsed_wall_s']=round(time.monotonic()-started,3)
        result['source_sha256']={name:hashlib.sha256((ROOT/'robolab'/name).read_bytes()).hexdigest()
                                 for name in ('engine.py','adapters.py','task_control.py','contact_geometry.py','p3_contact.py')}
        (out/('quick-result.json' if args.quick else 'result.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print('P3-A result:',out,'passed=',result['passed'],flush=True)


if __name__=='__main__':raise SystemExit(main())
