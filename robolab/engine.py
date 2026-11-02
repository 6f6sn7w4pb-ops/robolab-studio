"""Actual MuJoCo simulation, LIBERO task import, transactional scene editing.

LIBERO supplies task MJCF and its official initial state. The editor then owns
an independent in-memory MjModel; no source assets or benchmark files are written.
Simulation uses the explicit joint PD adapter, not a VLA policy or LIBERO reward.
"""
import copy
import math
import os
import sys
import time
import base64
import io
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image

from .adapters import create_robot_adapter
from .schema import SCHEMA_VERSION, SUITES, number, vector, validate_object, validate_project, read_project, write_project

def euler_quat(degrees):
    x, y, z = np.radians(degrees) / 2
    cx, cy, cz, sx, sy, sz = math.cos(x), math.cos(y), math.cos(z), math.sin(x), math.sin(y), math.sin(z)
    return np.array([cx*cy*cz+sx*sy*sz, sx*cy*cz-cx*sy*sz, cx*sy*cz+sx*cy*sz, cx*cy*sz-sx*sy*cz])

def quat_euler(q):
    w, x, y, z = q / np.linalg.norm(q)
    return np.degrees([math.atan2(2*(w*x+y*z), 1-2*(x*x+y*y)),
                       math.asin(np.clip(2*(w*y-z*x), -1, 1)),
                       math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z))]).tolist()

def joint_width(kind):
    return (7, 6) if kind == 0 else (4, 3) if kind == 1 else (1, 1)

def dispose_renderer(renderer):
    if renderer is None:
        return
    # MuJoCo 2.3.7 Renderer has no public close(). Keep compatibility isolated.
    if hasattr(renderer, 'close'):
        renderer.close()
    else:
        renderer._gl_context.make_current()
        renderer._mjr_context.free()
        renderer._gl_context.free()

class Engine:
    width, height = 960, 640

    def __init__(self):
        self.model = self.data = self.renderer = self.robot = None
        self.objects = []
        self.base_xml = ''
        self.task = None
        self.running = False
        self.mode = 'edit'
        self.selected = None
        self.camera = mujoco.MjvCamera()
        self.camera.type = mujoco.mjtCamera.mjCAMERA_FREE
        self.camera.lookat[:] = [0, 0, .85]
        self.camera.distance, self.camera.azimuth, self.camera.elevation = 1.7, 135, -35
        self.options = mujoco.MjvOption()
        self.options.geomgroup[0] = 0  # Collision meshes, including green Panda, are hidden.
        self.options.sitegroup[:] = 0
        self.axes = True
        self.initial = None
        self.robot_meta = None
        self.robot_adapter = 'panda_joint_pd'
        self.checkpoint = None
        self.home_camera = self.camera_state()
        self.spawn_position = [0, -.1, .15]
        self.execution_runner = None
        self.execution = {'kind':None,'status':'idle','phase':'场景已就绪；尚未执行策略','is_vla':False,'official_success':None}
        self.vla = None
        self.model_inspection = None
        self.observation_sequence = 0
        self.last_episode = None

    @staticmethod
    def tasks(suite_name='libero_object'):
        if suite_name not in SUITES:
            raise ValueError('Unsupported benchmark suite')
        from libero.libero import benchmark
        suite = benchmark.get_benchmark_dict()[suite_name]()
        return [{'index': i, 'language': suite.get_task(i).language} for i in range(suite.n_tasks)]

    def load_task(self, suite_name='libero_object', index=0, seed=0, init_index=0):
        from libero.libero import benchmark, get_libero_path
        from libero.libero.envs.env_wrapper import ControlEnv
        if suite_name not in SUITES:
            raise ValueError('Unsupported benchmark suite')
        if any(isinstance(x, bool) or not isinstance(x, int) for x in (index, seed, init_index)):
            raise ValueError('Task parameters must be integers')
        if not 0 <= seed < 2**31:
            raise ValueError('Invalid seed')
        suite = benchmark.get_benchmark_dict()[suite_name]()
        if not 0 <= index < suite.n_tasks:
            raise ValueError('Task index out of range')
        task = suite.get_task(index)
        states = suite.get_task_init_states(index)
        if not 0 <= init_index < len(states):
            raise ValueError('Initial state index out of range')
        env = ControlEnv(bddl_file_name=os.path.join(get_libero_path('bddl_files'), task.problem_folder, task.bddl_file),
                         has_renderer=False, has_offscreen_renderer=False, use_camera_obs=False,
                         renderer='mujoco', ignore_done=True)
        try:
            # ControlEnv.reset has an unbounded retry in the installed LIBERO.
            # Call the underlying environment once so initialization errors surface.
            env.seed(seed)
            env.env.reset()
            env.set_init_state(states[init_index])
            native = env.sim.model._model
            robot = env.robots[0]
            meta = (list(robot.robot_model.joints), list(robot.gripper.joints), native.site(robot.eef_site_id).name)
            base_xml = env.sim.model.get_xml()
            qpos, qvel = env.sim.data.qpos.copy(), env.sim.data.qvel.copy()
        finally:
            env.close()
        candidate = Engine()
        try:
            candidate.base_xml, candidate.robot_meta = base_xml, meta
            candidate.task = {'suite': suite_name, 'index': index, 'seed': seed, 'init_index': init_index,
                              'language': task.language}
            candidate.initial = (qpos, qvel)
            candidate._rebuild()
            centers = [candidate.data.xpos[candidate.model.jnt_bodyid[j]].copy()
                       for j in range(candidate.model.njnt) if candidate.model.jnt_type[j] == 0]
            center = np.mean(centers, axis=0) if centers else np.array([0., 0., 0.])
            points = np.array([candidate.data.xpos[candidate.model.jnt_bodyid[j]]
                               for j in range(candidate.model.njnt)])
            low, high = points.min(axis=0), points.max(axis=0)
            candidate.camera.lookat[:] = (low+high)/2 + [0, 0, .04]
            candidate.camera.distance = max(1.45, float(np.linalg.norm(high-low))*1.65)
            candidate.home_camera = candidate.camera_state()
            candidate.spawn_position = (center + [.12, -.12, .15]).tolist()
            candidate.robot.targets[:] = candidate.data.qpos[candidate.robot.qadr]
            candidate.robot.servo[:] = candidate.robot.targets
            candidate._set_checkpoint()
            # Rendering is part of the transaction; a failed GL context keeps old scene.
            candidate.render()
        except Exception:
            candidate.close()
            raise
        old_vla, old_inspection = self.vla, self.model_inspection
        self.close(preserve_vla=True)
        self.__dict__.update(candidate.__dict__)
        self.vla, self.model_inspection = old_vla, old_inspection
        candidate.renderer = None
        return self.scene()

    def _require(self):
        if self.model is None:
            raise ValueError('Load a task first')

    def _editable(self):
        self._require()
        if self.mode != 'edit' or self.running:
            raise ValueError('Switch to paused Edit mode to change the scene')

    def _joint_snapshot(self):
        if self.model is None:
            return {}
        result = {}
        for i in range(self.model.njnt):
            nq, nv = joint_width(int(self.model.jnt_type[i]))
            qa, va = self.model.jnt_qposadr[i], self.model.jnt_dofadr[i]
            result[self.model.joint(i).name] = (self.data.qpos[qa:qa+nq].copy(), self.data.qvel[va:va+nv].copy())
        return result

    def _rebuild(self, reset_ids=()):
        started = time.monotonic()
        root = ET.fromstring(self.base_xml)
        world = root.find('worldbody')
        for obj in self.objects:
            size = obj['size']
            geom_size = [v/2 for v in size] if obj['kind'] == 'box' else [size[0]/2] if obj['kind'] == 'sphere' else [size[0]/2, size[2]/2]
            body = ET.SubElement(world, 'body', name=obj['id'], pos=' '.join(map(str, obj['position'])),
                                 quat=' '.join(map(str, euler_quat(obj['rotation']))))
            ET.SubElement(body, 'freejoint', name=obj['id']+'_joint')
            ET.SubElement(body, 'geom', name=obj['id']+'_geom', type=obj['kind'], size=' '.join(map(str, geom_size)),
                          mass=str(obj['mass']), friction=' '.join(map(str, obj['friction'])),
                          rgba=' '.join(map(str, obj['color'])), group='1',
                          contype='1' if obj['collision'] else '0', conaffinity='1' if obj['collision'] else '0')
        xml = ET.tostring(root, encoding='unicode')
        model = mujoco.MjModel.from_xml_string(xml)
        compiled = time.monotonic()
        data = mujoco.MjData(model)
        saved = self._joint_snapshot()
        if not saved:
            data.qpos[:len(self.initial[0])] = self.initial[0]
            data.qvel[:len(self.initial[1])] = self.initial[1]
        else:
            data.time = self.data.time
            for i in range(model.njnt):
                name = model.joint(i).name
                if name in saved and name.removesuffix('_joint') not in reset_ids:
                    nq, nv = joint_width(int(model.jnt_type[i]))
                    qa, va = model.jnt_qposadr[i], model.jnt_dofadr[i]
                    data.qpos[qa:qa+nq], data.qvel[va:va+nv] = saved[name]
        mujoco.mj_forward(model, data)
        adapter = create_robot_adapter(self.robot_adapter, model, data, self.robot_meta)
        if self.robot is not None:
            adapter.targets[:] = self.robot.targets
            adapter.servo[:] = self.robot.servo
            adapter.opening = self.robot.opening
        model.vis.global_.offwidth, model.vis.global_.offheight = self.width, self.height
        if self.renderer:
            # Primitive edits never modify this task's mesh/texture/skin assets.
            # Retain their GPU uploads instead of rebuilding a GL context for
            # every property change. This compatibility bridge is for 2.3.7.
            old = self.model
            if (old.nmesh, old.ntex, old.nskin) != (model.nmesh, model.ntex, model.nskin):
                raise ValueError('Editor changed immutable rendering assets')
            renderer = self.renderer
            new_scene = mujoco.MjvScene(model, maxgeom=10000)
            renderer._model = model
            renderer._scene = new_scene
        else:
            renderer = mujoco.Renderer(model, height=self.height, width=self.width)
        self.model, self.data, self.robot, self.renderer = model, data, adapter, renderer
        self.renderer._gl_context.make_current()
        print(f'[RoboLab timing] compile={compiled-started:.3f}s renderer={time.monotonic()-compiled:.3f}s', file=sys.stderr, flush=True)

    def _set_checkpoint(self):
        self.checkpoint = (self.data.qpos.copy(), self.robot.targets.copy(), self.robot.opening)

    def set_mode(self, mode):
        self._require()
        if mode not in ('edit', 'simulate'):
            raise ValueError('Unknown mode')
        self.running = False
        if mode == 'edit':
            self.cancel_execution()
            self.data.qvel[:] = 0
            self.robot.targets[:] = np.clip(self.data.qpos[self.robot.qadr], self.robot.limits[:, 0], self.robot.limits[:, 1])
            self.robot.servo[:] = self.robot.targets
            self._sync_objects()
            self._set_checkpoint()
        self.mode = mode

    def play(self):
        self._require()
        self.mode, self.running = 'simulate', True

    def pause(self):
        self._require()
        self.running = False

    def reset(self):
        self._require()
        self.cancel_execution()
        self.running, self.mode = False, 'edit'
        mujoco.mj_resetData(self.model, self.data)
        q, targets, opening = self.checkpoint
        self.data.qpos[:] = q
        self.robot.targets[:], self.robot.servo[:], self.robot.opening = targets, targets, opening
        mujoco.mj_forward(self.model, self.data)

    def step(self, seconds=.02):
        self._require()
        self.mode = 'simulate'
        if self.execution_runner and not self.execution_runner.closed:
            try:
                if hasattr(self.execution_runner,'tick'):
                    self.execution_runner.tick()
                    return
                self.execution_runner.before_step()
                if self.execution_runner.closed: return
            except Exception as error:
                self.running=False
                self.execution.update(status='failed',phase=str(error))
                self.execution_runner.close()
                raise
        try:
            dt = float(self.model.opt.timestep)
            for _ in range(max(1, round(seconds/dt))):
                self.robot.apply_control(dt)
                mujoco.mj_step(self.model, self.data)
            if not np.isfinite(self.data.qpos).all() or np.max(np.abs(self.data.qvel)) > 1e4:
                raise RuntimeError('Unstable physics; paused. Reset the layout before continuing.')
            if self.execution_runner and not self.execution_runner.closed and hasattr(self.execution_runner,'after_step'):
                self.execution_runner.after_step()
        except Exception as error:
            self.running=False
            if self.execution_runner and not self.execution_runner.closed:
                self.execution.update(status='failed',phase=str(error))
                self.execution_runner.close()
            raise

    def set_joint(self, index, radians):
        self._require()
        if self.execution_runner and not self.execution_runner.closed:
            raise ValueError('请先结束当前策略执行，再手动调整机械臂')
        radians = number(radians, -7, 7, 'Joint target')
        self.robot.set_target(index, radians)
        if self.mode == 'edit':
            self.robot.edit_pose()
            self._set_checkpoint()

    def set_gripper(self, opening):
        self._require()
        if self.execution_runner and not self.execution_runner.closed:
            raise ValueError('请先结束当前策略执行，再手动调整夹爪')
        self.robot.opening = number(opening, 0, 1, 'Gripper opening')
        if self.mode == 'edit':
            self.robot.edit_pose()
            self._set_checkpoint()

    def add_object(self, kind='box'):
        self._editable()
        if len(self.objects) >= 100:
            raise ValueError('Maximum 100 editor objects')
        obj = validate_object({'id': 'rl_'+uuid.uuid4().hex[:8], 'kind': kind, 'position': list(self.spawn_position),
                               'rotation': [0, 0, 0], 'size': [.06, .06, .06], 'mass': .1,
                               'friction': [1, .005, .0001], 'collision': True, 'color': [.12, .62, .84, 1]})
        old = copy.deepcopy(self.objects)
        self._sync_objects()
        self.objects.append(obj)
        try:
            self._rebuild()
        except Exception:
            self.objects = old
            raise
        self.selected = obj['id']
        self._set_checkpoint()
        return obj

    def update_object(self, object_id, changes):
        self._editable()
        old = copy.deepcopy(self.objects)
        obj = next((o for o in self.objects if o['id'] == object_id), None)
        if obj is None:
            raise ValueError('Full property editing is available for editor primitives')
        if set(changes) - {'position', 'rotation', 'size', 'mass', 'friction', 'collision', 'color'}:
            raise ValueError('Unsupported property')
        self._sync_objects()
        index = self.objects.index(obj)
        self.objects[index] = validate_object(dict(obj, **changes))
        try:
            self._rebuild(reset_ids=(object_id,))
        except Exception:
            self.objects = old
            raise
        self._set_checkpoint()

    def delete_object(self, object_id):
        self._editable()
        if not any(o['id'] == object_id for o in self.objects):
            raise ValueError('Only editor primitives can be deleted; benchmark assets are protected')
        old = copy.deepcopy(self.objects)
        self._sync_objects()
        self.objects = [o for o in self.objects if o['id'] != object_id]
        try:
            self._rebuild()
        except Exception:
            self.objects = old
            raise
        self.selected = None
        self._set_checkpoint()

    def set_placement(self, object_id, position, rotation):
        self._editable()
        info = self.body_info(object_id)
        if info['type'] != 'libero_object':
            raise ValueError('Expected a movable benchmark object')
        pos = vector(position, 3, -10, 10, 'Position')
        rot = vector(rotation, 3, -360, 360, 'Rotation')
        jid = self.model.joint(info['joint']).id
        qa, va = self.model.jnt_qposadr[jid], self.model.jnt_dofadr[jid]
        self.data.qpos[qa:qa+7] = pos + euler_quat(rot).tolist()
        self.data.qvel[va:va+6] = 0
        mujoco.mj_forward(self.model, self.data)
        self._set_checkpoint()

    def _sync_objects(self):
        for obj in self.objects:
            if self.model is None or mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, obj['id']) < 0:
                continue
            body = self.model.body(obj['id']).id
            obj['position'] = self.data.xpos[body].tolist()
            obj['rotation'] = quat_euler(self.data.xquat[body])

    def body_info(self, object_id):
        self._require()
        for obj in self.objects:
            if obj['id'] == object_id:
                result = copy.deepcopy(obj)
                bid = self.model.body(object_id).id
                result.update(type='primitive', position=self.data.xpos[bid].tolist(), rotation=quat_euler(self.data.xquat[bid]))
                return result
        bid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, object_id)
        if bid < 0:
            raise ValueError('Unknown scene body')
        for jid in range(self.model.njnt):
            if self.model.jnt_bodyid[jid] == bid and self.model.jnt_type[jid] == mujoco.mjtJoint.mjJNT_FREE:
                return {'id': object_id, 'type': 'libero_object', 'joint': self.model.joint(jid).name,
                        'position': self.data.xpos[bid].tolist(), 'rotation': quat_euler(self.data.xquat[bid])}
        return {'id': object_id, 'type': 'readonly', 'position': self.data.xpos[bid].tolist()}

    def scene(self):
        self._require()
        bodies = []
        for i in range(1, self.model.nbody):
            name = self.model.body(i).name
            info = self.body_info(name)
            info['parent'] = self.model.body(int(self.model.body_parentid[i])).name or 'world'
            bodies.append(info)
        return {'task': self.task, 'bodies': bodies, 'robot': self.robot.state(),
                'cameras': [self.model.camera(i).name for i in range(self.model.ncam)], 'state': self.state()}

    def state(self):
        if self.model is None:
            return {'loaded': False, 'running': False, 'mode': 'edit', 'time': 0}
        return {'loaded': True, 'running': self.running, 'mode': self.mode, 'time': float(self.data.time),
                'contacts': int(self.data.ncon), 'selected': self.selected, 'robot': self.robot.state(),
                'camera': self.camera_state(), 'execution':self.execution,
                'policy':self.policy_status(),'last_episode':self.last_episode}

    def policy_status(self):
        from .policy import ModelStatus
        if not self.vla: return ModelStatus().as_dict()
        return dict(self.vla.status(),adapter_id=getattr(self.vla,'adapter_id',self.vla.status()['family']))

    @property
    def control_period(self):
        return .05 if self.execution_runner and hasattr(self.execution_runner,'tick') and not self.execution_runner.closed else .02

    def cancel_execution(self):
        if self.execution_runner:
            if not self.execution_runner.closed:
                self.execution.update(status='cancelled',phase='执行已取消')
                self.execution_runner.close()
            self.execution_runner=None

    def configure_policy(self,kind='mock',checkpoint='',endpoint=''):
        from .policy import create_vla_adapter
        if self.execution_runner and not self.execution_runner.closed:
            raise ValueError('请先停止当前策略执行')
        candidate=create_vla_adapter(kind,**({} if kind=='mock' else {'checkpoint':checkpoint,'endpoint':endpoint}))
        if self.vla: self.vla.close()
        self.vla=candidate
        self.vla.adapter_id=kind
        self.model_inspection=getattr(candidate,'inspection',None)
        return self.policy_status()

    def load_policy(self):
        if not self.vla: raise ValueError('请先配置策略适配器')
        return self.vla.load()

    def start_policy(self,max_steps=20):
        self._require()
        from .task_control import PolicyExecution
        if isinstance(max_steps,bool) or not isinstance(max_steps,int) or not 1<=max_steps<=1000: raise ValueError('策略步数应在 1–1000')
        if not self.vla: raise ValueError('请先配置 Mock 或 VLA 适配器')
        self.cancel_execution()
        self.execution_runner=PolicyExecution(self,max_steps)
        self.execution=self.execution_runner.status('准备观察输入')
        self.mode,self.running='simulate',True
        return self.execution

    def start_grasp_test(self):
        self._require()
        from .task_control import ScriptedPickPlace
        self.cancel_execution()
        self.running=False
        self.last_episode=None
        self.execution={'kind':'scripted_self_test','status':'starting','phase':'正在初始化 P2 记录','is_vla':False,'official_success':None}
        try: self.execution_runner=ScriptedPickPlace(self)
        except Exception as error:
            self.execution.update(status='failed',phase=str(error))
            raise
        self.execution=self.execution_runner.status()
        self.mode,self.running='simulate',True
        return self.execution

    def stop_execution(self):
        self._require()
        self.running=False
        self.cancel_execution()
        self.robot.targets[:]=self.data.qpos[self.robot.qadr]
        self.robot.servo[:]=self.robot.targets
        return self.state()

    def camera_images(self):
        """当前物理状态的同步双 RGB；不创建或执行策略。"""
        self._require()
        images={}
        renderer=self.renderer
        old=(renderer._width,renderer._height,renderer._rect)
        try:
            # Square viewport, not a distorted resize of the editor's 3:2 view.
            renderer._width=renderer._height=224
            renderer._rect=mujoco.MjrRect(0,0,224,224)
            for name in ('agentview','robot0_eye_in_hand'):
                renderer.update_scene(self.data,camera=name,scene_option=self.options)
                # Canonical model-independent RGB: top-left origin, OpenCV axes.
                # Training-specific flips/crops belong in the model adapter.
                rgb=renderer.render()
                buf=io.BytesIO(); Image.fromarray(rgb).save(buf,'JPEG',quality=90)
                images[name]={'encoding':'jpeg_base64','width':224,'height':224,'origin':'top_left','data':base64.b64encode(buf.getvalue()).decode('ascii')}
        finally:
            renderer._width,renderer._height,renderer._rect=old
        return images

    def policy_observation(self):
        images=self.camera_images()
        state=self.robot.state(); quat=np.asarray(state['eef_quaternion_wxyz'])
        if quat[0]<0: quat=-quat
        angle=2*math.acos(float(np.clip(quat[0],-1,1)))
        axis=quat[1:]/max(1e-9,float(np.linalg.norm(quat[1:])))
        self.observation_sequence+=1
        return {'schema':'robolab.observation.v1','observation_id':self.observation_sequence,
                'instruction':self.task['language'],'sim_time':float(self.data.time),'images':images,
                'proprio':state['eef_position']+(axis*angle).tolist()+state['gripper_qpos'],
                'joints':state['qpos'],'calibration':self.camera_calibration()}

    def camera_calibration(self):
        result={}
        for name in ('agentview','robot0_eye_in_hand'):
            i=self.model.camera(name).id
            f=112/math.tan(math.radians(float(self.model.cam_fovy[i]))/2)
            rotation=self.data.cam_xmat[i].reshape(3,3)@np.diag([1.,-1.,-1.])
            transform=np.eye(4); transform[:3,:3]=rotation; transform[:3,3]=self.data.cam_xpos[i]
            result[name]={'K':[[f,0,111.5],[0,f,111.5],[0,0,1]],'T_world_camera':transform.tolist(),
                          'image_rotation_deg':0,'units':'m','convention':'x-right,y-down,z-forward'}
        return result

    def camera_state(self):
        return {'lookat': self.camera.lookat.tolist(), 'distance': float(self.camera.distance),
                'azimuth': float(self.camera.azimuth), 'elevation': float(self.camera.elevation)}

    def move_camera(self, action, dx=0, dy=0):
        self._require()
        dx, dy = number(dx, -10, 10, 'Camera dx'), number(dy, -10, 10, 'Camera dy')
        if action == 'orbit':
            self.camera.azimuth -= dx*180
            self.camera.elevation = np.clip(self.camera.elevation-dy*180, -89, 89)
        elif action == 'pan':
            mujoco.mjv_moveCamera(self.model, mujoco.mjtMouse.mjMOUSE_MOVE_V, dx, dy, self.renderer.scene, self.camera)
        elif action == 'zoom':
            self.camera.distance = np.clip(self.camera.distance*math.exp(-dy*.15), .15, 10)
        elif action == 'home':
            for key, value in self.home_camera.items():
                if key == 'lookat': self.camera.lookat[:] = value
                else: setattr(self.camera, key, value)
        elif action == 'focus':
            if self.selected:
                self.camera.lookat[:] = self.data.xpos[self.model.body(self.selected).id]
                self.camera.distance = .55
        else:
            raise ValueError('Unknown camera operation')

    def select(self, object_id):
        self.body_info(object_id)
        self.selected = object_id
        return self.body_info(object_id)

    def pick(self, x, y):
        self._require()
        x, y = number(x, 0, 1, 'Pick x'), number(y, 0, 1, 'Pick y')
        self.renderer.update_scene(self.data, camera=self.camera, scene_option=self.options)
        point, geom, skin = np.zeros(3), np.zeros(1, dtype=np.int32), np.zeros(1, dtype=np.int32)
        bid = mujoco.mjv_select(self.model, self.data, self.options, self.width/self.height, x, 1-y,
                                self.renderer.scene, point, geom, skin)
        if bid <= 0:
            self.selected = None
            return None
        return self.select(self.model.body(bid).name)

    def render(self):
        self._require()
        self.renderer.update_scene(self.data, camera=self.camera, scene_option=self.options)
        if self.axes:
            self._axes([0, 0, .005], .13)
            if self.selected:
                bid = self.model.body(self.selected).id
                self._axes(self.data.xpos[bid], .09)
        return self.renderer.render()

    def _axes(self, origin, length):
        scene = self.renderer.scene
        for axis in range(3):
            if scene.ngeom >= scene.maxgeom:
                return
            geom = scene.geoms[scene.ngeom]
            rgba = np.zeros(4, dtype=np.float32); rgba[axis], rgba[3] = .95, 1
            mujoco.mjv_initGeom(geom, mujoco.mjtGeom.mjGEOM_ARROW, np.zeros(3), np.asarray(origin), np.eye(3).ravel(), rgba)
            end = np.array(origin, dtype=float); end[axis] += length
            mujoco.mjv_makeConnector(geom, mujoco.mjtGeom.mjGEOM_ARROW, .004, *origin, *end)
            scene.ngeom += 1

    def project(self):
        self._require()
        self._sync_objects()
        placements = {}
        for i in range(self.model.njnt):
            if self.model.jnt_type[i] == 0 and not self.model.joint(i).name.startswith('rl_'):
                qa = self.model.jnt_qposadr[i]
                placements[self.model.joint(i).name] = self.data.qpos[qa:qa+7].tolist()
        return validate_project({'schema_version': SCHEMA_VERSION, 'task': self.task, 'objects': self.objects,
                                 'placements': placements, 'camera': self.camera_state(),
                                 'robot_qpos': self.data.qpos[self.robot.qadr].tolist(), 'gripper': self.robot.opening,
                                 'adapters': {'robot': 'panda_joint_pd', 'camera': 'mujoco_orbit', 'policy': None}})

    def save(self, path):
        return write_project(path, self.project())

    def load(self, path):
        project = read_project(path)
        if project.get('adapters', {}).get('robot', 'panda_joint_pd') != self.robot_adapter:
            raise ValueError('Project requires a different robot adapter; install/configure it before loading')
        candidate = Engine()
        try:
            t = project['task']
            candidate.load_task(t['suite'], t['index'], t['seed'], t['init_index'])
            candidate.objects = copy.deepcopy(project['objects'])
            candidate._rebuild()
            if len(project['robot_qpos']) != len(candidate.robot.names):
                raise ValueError('Project robot joint count does not match loaded model')
            for name, pose in project['placements'].items():
                jid = mujoco.mj_name2id(candidate.model, mujoco.mjtObj.mjOBJ_JOINT, name)
                if jid < 0 or candidate.model.jnt_type[jid] != 0:
                    raise ValueError(f'Project references missing free joint {name}')
                qa = candidate.model.jnt_qposadr[jid]
                candidate.data.qpos[qa:qa+7] = pose
            for i, q in enumerate(project['robot_qpos']):
                candidate.robot.set_target(i, q)
            candidate.robot.opening = project['gripper']
            candidate.robot.edit_pose()
            candidate.data.qvel[:] = 0
            for key, value in project['camera'].items():
                if key == 'lookat': candidate.camera.lookat[:] = value
                else: setattr(candidate.camera, key, value)
            candidate._set_checkpoint()
            candidate.render()
        except Exception:
            candidate.close()
            if self.renderer:
                self.renderer._gl_context.make_current()
            raise
        old_vla, old_inspection = self.vla, self.model_inspection
        self.close(preserve_vla=True)
        self.__dict__.update(candidate.__dict__)
        self.vla, self.model_inspection = old_vla, old_inspection
        candidate.renderer = None
        return self.scene()

    def screenshot(self, path):
        self._require()
        path = Path(path).expanduser().resolve()
        if path.suffix.lower() != '.png':
            raise ValueError('Screenshot must use .png extension')
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(self.render()).save(path)
        return str(path)

    def close(self,preserve_vla=False):
        self.running = False
        self.cancel_execution()
        if self.vla and not preserve_vla:
            self.vla.close(); self.vla=None
        dispose_renderer(self.renderer)
        self.renderer = None
