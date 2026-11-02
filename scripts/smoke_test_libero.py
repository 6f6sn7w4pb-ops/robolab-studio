"""P1：LIBERO 双 RGB / EGL 离屏 reset-step-PNG-GIF 验证，不执行抓取或 VLA。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
import uuid

ROOT=Path(__file__).resolve().parents[1]
CAMERAS={'external':'agentview','wrist':'robot0_eye_in_hand'}
EVENT_PREFIX='ROBOLAB_P1_EVENT='

def notify(enabled,event,**payload):
    if enabled:
        print(EVENT_PREFIX+json.dumps(dict(event=event,**payload),ensure_ascii=False),flush=True)

class Tee:
    def __init__(self,stream,file): self.stream,self.file=stream,file
    def write(self,text): self.stream.write(text); self.file.write(text); self.file.flush(); return len(text)
    def flush(self): self.stream.flush(); self.file.flush()

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def git_head(path):
    import subprocess
    r=subprocess.run(['git','-C',str(path),'rev-parse','HEAD'],capture_output=True,text=True)
    return r.stdout.strip() if r.returncode==0 else None

def close_environment(env):
    """在 EGL display 的 atexit 清理前释放 robosuite 1.4 的渲染资源。"""
    context=getattr(env.sim,'_render_context_offscreen',None)
    if context is not None:
        # MjrContext.free() and EGLGLContext.free() are idempotent in this
        # installed version. env.close() alone can leave references alive
        # until after eglTerminate(), causing ignored destructor exceptions.
        context.gl_ctx.make_current()
        context.con.free()
        context.gl_ctx.free()
    env.close()

def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--libero-root',type=Path,default=Path(os.getenv('LIBERO_ROOT',os.getenv('ROBOLAB_LIBERO_ROOT',str(Path.home()/'projects'/'LIBERO')))))
    p.add_argument('--output-dir',type=Path,default=Path(os.getenv('OUTPUT_DIR',os.getenv('ROBOLAB_OUTPUT_DIR',str(ROOT/'outputs'/'smoke_test')))))
    p.add_argument('--task-id',type=int,default=0)
    p.add_argument('--seed',type=int,default=0)
    p.add_argument('--init-index',type=int,default=0)
    p.add_argument('--steps',type=int,default=20)
    p.add_argument('--size',type=int,default=256)
    p.add_argument('--json-events',action='store_true',help='为桌面 GUI 输出进度事件；仍保留完整文本日志')
    args=p.parse_args(argv)
    if not 1<=args.steps<=200: p.error('--steps 应在 1–200，P1 不执行长任务')
    if not 64<=args.size<=512: p.error('--size 应在 64–512')
    if not 0<=args.seed<2**31: p.error('--seed 应在 [0, 2**31)')
    return args

def main(argv=None):
    args=parse_args(argv)
    output_root=args.output_dir.expanduser().resolve()
    run_dir=output_root/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:6])
    run_dir.mkdir(parents=True,exist_ok=False)
    log=(run_dir/'run.log').open('w',encoding='utf-8')
    original_out,original_error=sys.stdout,sys.stderr
    sys.stdout,sys.stderr=Tee(original_out,log),Tee(original_error,log)
    started=time.monotonic(); env=None; result=1
    report={'passed':False,'run_dir':str(run_dir),'checked_at_utc':datetime.now(timezone.utc).isoformat(),
            'claims':{'scene_initialized':False,'dual_rgb_verified':False,'step_verified':False,'grasp_tested':False,'contact_collection_tested':False,'vla_inference_tested':False},
            'artifacts':{},'requested_steps':args.steps,'seed':args.seed,'init_index':args.init_index}
    try:
        notify(args.json_events,'created',run_dir=str(run_dir))
        if any(name in sys.modules for name in ('mujoco','robosuite','OpenGL')):
            raise RuntimeError('请在独立 Python 进程执行此脚本，确保 EGL 在首次导入前配置')
        os.environ['MUJOCO_GL']='egl'; os.environ['PYOPENGL_PLATFORM']='egl'
        os.environ['PYTHONDONTWRITEBYTECODE']='1'; sys.dont_write_bytecode=True
        os.environ['HF_HUB_OFFLINE']='1'; os.environ['TRANSFORMERS_OFFLINE']='1'
        # Prove this entry point has no dependency on an X11/Wayland window.
        os.environ.pop('DISPLAY',None); os.environ.pop('WAYLAND_DISPLAY',None)
        libero_root=args.libero_root.expanduser().resolve()
        if not (libero_root/'libero'/'libero'/'envs'/'env_wrapper.py').is_file():
            raise FileNotFoundError(f'LIBERO 源码路径不正确：{libero_root}；可用 --libero-root 或 LIBERO_ROOT 配置')
        config=Path(os.getenv('LIBERO_CONFIG_PATH',str(Path.home()/'.libero')))/'config.yaml'
        if not config.is_file(): raise FileNotFoundError(f'缺少已有 LIBERO 配置：{config}；本测试不自动创建或改写用户配置')
        sys.path.insert(0,str(libero_root))
        import importlib.metadata as md
        import numpy as np
        import mujoco
        from PIL import Image,ImageDraw
        import robosuite.macros as macros
        from libero.libero import benchmark,get_libero_path
        from libero.libero.envs import OffScreenRenderEnv
        report['runtime']={'python':sys.version,'executable':sys.executable,
                           'versions':{name:md.version(name) for name in ('mujoco','robosuite','libero','torch','numpy','Pillow')},
                           'MUJOCO_GL':os.environ.get('MUJOCO_GL'),'PYOPENGL_PLATFORM':os.environ.get('PYOPENGL_PLATFORM'),
                           'DISPLAY':os.getenv('DISPLAY'),'WAYLAND_DISPLAY':os.getenv('WAYLAND_DISPLAY')}
        report['source']={'libero_root':str(libero_root),'libero_commit':git_head(libero_root),
                          'project_commit':git_head(ROOT),'script_sha256':sha(__file__),'libero_config_sha256':sha(config)}
        suite=benchmark.get_benchmark_dict()['libero_object']()
        if not 0<=args.task_id<suite.n_tasks: raise ValueError('任务索引超出 LIBERO-Object 范围')
        task=suite.get_task(args.task_id)
        bddl=Path(get_libero_path('bddl_files'))/task.problem_folder/task.bddl_file
        states_file=Path(get_libero_path('init_states'))/task.problem_folder/task.init_states_file
        if not bddl.is_file() or not states_file.is_file(): raise FileNotFoundError('缺少 BDDL 或官方初始状态文件')
        report['task']={'suite':'libero_object','id':args.task_id,'name':task.name,'language':task.language,
                        'bddl_file':str(bddl),'bddl_sha256':sha(bddl),'initial_states_file':str(states_file),'initial_states_sha256':sha(states_file)}
        print('【P1】创建现有 LIBERO-Object 场景：',task.language,flush=True)
        notify(args.json_events,'phase',text='正在创建官方 LIBERO 场景与 EGL 相机…')
        env=OffScreenRenderEnv(bddl_file_name=str(bddl),robots=['Panda'],controller='OSC_POSE',
                               camera_names=list(CAMERAS.values()),camera_heights=args.size,camera_widths=args.size,
                               use_camera_obs=True,camera_depths=False,renderer='mujoco',ignore_done=True)
        env.seed(args.seed)
        notify(args.json_events,'phase',text='正在重置场景并加载官方初始状态…')
        # Installed ControlEnv.reset uses finally: continue and can suppress errors.
        # Reuse its underlying reset once; preserve source and surface any exception.
        env.env.reset()
        states=suite.get_task_init_states(args.task_id)
        if not 0<=args.init_index<len(states): raise ValueError('官方初始状态索引超出范围')
        obs=env.set_init_state(states[args.init_index])
        report['claims']['scene_initialized']=True
        print('【P1】观测键：',list(obs),flush=True)
        low,high=env.env.action_spec
        robot=env.robots[0]; controller=robot.controller
        report['action']={'dimension':int(env.env.action_dim),'low':low.tolist(),'high':high.tolist(),
                          'controller_class':type(controller).__name__,'controller_name':controller.name,
                          'control_freq_hz':env.env.control_freq,'physics_timestep':env.sim.model.opt.timestep,
                          'use_delta':bool(controller.use_delta),
                          'controller_input_min':np.asarray(controller.input_min).tolist(),'controller_input_max':np.asarray(controller.input_max).tolist(),
                          'controller_output_min':np.asarray(controller.output_min).tolist(),'controller_output_max':np.asarray(controller.output_max).tolist(),
                          'components':['delta_x','delta_y','delta_z','delta_rx','delta_ry','delta_rz','gripper'],
                          'meaning':'归一化的末端位姿增量 6 维 + 夹爪 1 维；不是七个关节角。OSC 增量基于世界坐标。',
                          'gripper_convention':'-1 打开，+1 闭合；本测试固定 -1，不执行抓取'}
        if int(env.env.action_dim)!=7 or controller.name!='OSC_POSE': raise RuntimeError('实际控制器或动作维度与 P1 样例不符，请先核对')
        action=np.zeros(env.env.action_dim); action[-1]=-1
        report['action']['test_action']=action.tolist()
        print('【P1】动作维度/控制器：',report['action']['dimension'],controller.name,'控制频率',env.env.control_freq,flush=True)
        report['observation_schema']={key:{'shape':list(np.asarray(value).shape),'dtype':str(np.asarray(value).dtype)} for key,value in obs.items()}
        native_model,native_data=env.sim.model._model,env.sim.data._data
        context=env.sim._render_context_offscreen
        report['render_context']={'class':type(context).__name__,'gl_context_module':type(context.gl_ctx).__module__}
        del context
        report['robot']={'simulation_model':type(robot.robot_model).__name__,'joint_names':list(robot.robot_model.joints),
                          'eef_site':native_model.site(robot.eef_site_id).name,'eef_quaternion_order':'xyzw in LIBERO observations',
                          'physical_hardware_reference':'Franka Emika Panda (FER); P1 does not connect hardware'}
        report['cameras']={}
        for role,name in CAMERAS.items():
            i=native_model.camera(name).id
            focal=args.size/2/np.tan(np.radians(native_model.cam_fovy[i])/2)
            rotation=native_data.cam_xmat[i].reshape(3,3)@np.diag([1.,-1.,-1.])
            transform=np.eye(4); transform[:3,:3]=rotation; transform[:3,3]=native_data.cam_xpos[i]
            report['cameras'][role]={'name':name,'observation_key':name+'_image','width':args.size,'height':args.size,
                                      'rgb':True,'depth_enabled':False,'source_image_convention':macros.IMAGE_CONVENTION,
                                      'saved_image_convention':'opencv_top_left_rgb','fovy_degrees':float(native_model.cam_fovy[i]),
                                      'K':[[float(focal),0,(args.size-1)/2],[0,float(focal),(args.size-1)/2],[0,0,1]],
                                      'T_world_camera_initial':transform.tolist(),'axes':'x-right,y-down,z-forward','units':'m'}
            print('【P1】相机：',name,report['observation_schema'][name+'_image'],flush=True)
        frames=[]; samples=[]
        def capture(observation,index):
            images={}
            for role,name in CAMERAS.items():
                pixels=np.asarray(observation[name+'_image'])
                if pixels.shape!=(args.size,args.size,3) or pixels.dtype!=np.uint8:
                    raise RuntimeError(f'{name} 图像 shape/dtype 不正确：{pixels.shape}/{pixels.dtype}')
                if not np.isfinite(pixels).all() or float(pixels.std())<1:
                    raise RuntimeError(f'{name} 返回空白或无效图像')
                if macros.IMAGE_CONVENTION not in ('opengl','opencv'): raise ValueError('未知相机图像坐标约定')
                pixels=pixels[::-1] if macros.IMAGE_CONVENTION=='opengl' else pixels
                images[role]=np.ascontiguousarray(pixels)
            if np.array_equal(images['external'],images['wrist']): raise RuntimeError('两路相机图像完全相同，请核对相机选择')
            canvas=Image.new('RGB',(args.size*2,args.size+24),(25,32,42))
            canvas.paste(Image.fromarray(images['external']),(0,24)); canvas.paste(Image.fromarray(images['wrist']),(args.size,24))
            draw=ImageDraw.Draw(canvas)
            draw.text((5,5),f"External / agentview  t={native_data.time:.2f}s",fill='white')
            draw.text((args.size+5,5),'Wrist / robot0_eye_in_hand',fill='white')
            frames.append(canvas)
            samples.append({'step':index,'simulation_time':float(native_data.time),
                            'external_sha256':hashlib.sha256(images['external'].tobytes()).hexdigest(),
                            'wrist_sha256':hashlib.sha256(images['wrist'].tobytes()).hexdigest()})
            return images,canvas
        report['initial_qpos']=native_data.qpos.tolist(); initial_time=float(native_data.time)
        images,canvas=capture(obs,0)
        for role,image in images.items(): Image.fromarray(image).save(run_dir/(role+'_initial.png'))
        for index in range(1,args.steps+1):
            obs,reward,done,info=env.step(action.copy())
            images,canvas=capture(obs,index)
            notify(args.json_events,'step',completed=index,total=args.steps,sim_time=float(native_data.time))
        report['final_qpos']=native_data.qpos.tolist(); report['final_qvel']=native_data.qvel.tolist()
        report['elapsed_simulation_seconds']=float(native_data.time-initial_time)
        expected=args.steps/env.env.control_freq
        if not np.isclose(report['elapsed_simulation_seconds'],expected,atol=1e-7): raise RuntimeError('实际仿真时间没有按控制频率推进')
        report['claims']['step_verified']=True; report['claims']['dual_rgb_verified']=True
        notify(args.json_events,'phase',text='正在保存图像、GIF 并验证资源释放…')
        for role,image in images.items(): Image.fromarray(image).save(run_dir/(role+'.png'))
        canvas.save(run_dir/'side_by_side.png')
        frames[0].save(run_dir/'simulation.gif',save_all=True,append_images=frames[1:],duration=round(1000/env.env.control_freq),loop=0,optimize=False)
        with Image.open(run_dir/'simulation.gif') as gif:
            report['gif']={'captured_frames':len(frames),'encoded_frames':gif.n_frames,'size':list(gif.size),'fps':env.env.control_freq}
            if gif.n_frames<2: raise RuntimeError('GIF 未包含多个有效帧')
        report['frame_samples']=samples
        report['last_reward']=float(reward); report['last_done']=bool(done)
        report['final_robot_state']={key:np.asarray(obs[key]).tolist() for key in ('robot0_joint_pos','robot0_eef_pos','robot0_eef_quat','robot0_gripper_qpos')}
        report['artifacts']={name:{'path':str(run_dir/name),'sha256':sha(run_dir/name)} for name in ('external.png','wrist.png','side_by_side.png','simulation.gif','external_initial.png','wrist_initial.png')}
        close_environment(env); env=None
        report['resources_released']=True; report['passed']=True; result=0
        print('【P1 通过】双路有效 RGB、reset/step、PNG/GIF 导出完成。没有执行抓取或 VLA 推理。',flush=True)
    except Exception as error:
        report['error']={'type':type(error).__name__,'message':str(error)}
        traceback.print_exc()
        print('【P1 未通过】请查看本次 run.log；不会将旧图片标记为本次结果。',flush=True)
    finally:
        if env is not None:
            try: close_environment(env); report['resources_released']=True
            except Exception as error: report['close_error']=repr(error); report['passed']=False; result=1
        report['elapsed_wall_seconds']=round(time.monotonic()-started,3)
        (run_dir/'metadata.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        (output_root/'latest.json').write_text(json.dumps({'passed':report['passed'],'run_dir':str(run_dir),'metadata':str(run_dir/'metadata.json')},ensure_ascii=False,indent=2),encoding='utf-8')
        notify(args.json_events,'complete',passed=report['passed'],run_dir=str(run_dir))
        print('结果目录：',run_dir,flush=True)
        sys.stdout,sys.stderr=original_out,original_error
        log.close()
    return result

if __name__=='__main__': raise SystemExit(main())
