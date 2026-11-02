"""Lightweight physical self-test and official LIBERO action runtime.

The scripted self-test uses simulator state and inverse kinematics. It is not a
VLA model, learned policy, demonstration replay or benchmark success-rate claim.
No object qpos is written while running the scripted trajectory.
"""
import os
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import mujoco

class LiberoRuntime:
    def __init__(self, engine):
        from libero.libero import benchmark, get_libero_path
        from libero.libero.envs.env_wrapper import ControlEnv
        task=benchmark.get_benchmark_dict()[engine.task['suite']]().get_task(engine.task['index'])
        self.env=ControlEnv(bddl_file_name=os.path.join(get_libero_path('bddl_files'),task.problem_folder,task.bddl_file),
                            has_renderer=False,has_offscreen_renderer=False,use_camera_obs=False,
                            renderer='mujoco',ignore_done=True)
        try:
            self.env.env.reset()
            self.sync_from(engine)
        except Exception:
            self.close(); raise

    def sync_from(self, engine):
        sim=self.env.sim
        sim.data.qpos[:]=engine.data.qpos[:sim.model.nq]
        sim.data.qvel[:]=engine.data.qvel[:sim.model.nv]
        sim.data._data.time=float(engine.data.time)
        sim.forward()

    def success(self, engine):
        self.sync_from(engine)
        return bool(self.env.check_success())

    def action(self, engine, action):
        if engine.objects:
            raise ValueError('VLA execution currently requires an unextended LIBERO scene')
        self.sync_from(engine)
        self.env.step(np.asarray(action,dtype=float))
        engine.data.qpos[:]=self.env.sim.data.qpos
        engine.data.qvel[:]=self.env.sim.data.qvel
        engine.data.ctrl[:]=self.env.sim.data.ctrl
        engine.data.time=float(self.env.sim.data._data.time)
        mujoco.mj_forward(engine.model,engine.data)
        engine.robot.targets[:]=engine.data.qpos[engine.robot.qadr]
        engine.robot.servo[:]=engine.robot.targets
        return bool(self.env.check_success())

    def close(self):
        if self.env:
            self.env.close(); self.env=None

class ScriptedPickPlace:
    names=('稳定物体','移动至目标上方','下降对齐','闭合夹爪','抬起验证','移动至篮子上方','下降放置','松开夹爪','抬手避让','检查任务结果')
    durations=(1.,3.,3.,1.,3.,5.,2.,1.,2.,1.)

    def __init__(self,engine):
        if engine.task['suite']!='libero_object' or engine.task['index']!=0:
            raise ValueError('规则抓取自检目前仅验证 LIBERO-Object 第一个汤罐任务；其他任务请接入 VLA 策略')
        if engine.objects:
            raise ValueError('请在未添加额外物体的默认场景中运行抓取自检')
        self.engine=engine
        self.runtime=LiberoRuntime(engine)
        self.scratch=mujoco.MjData(engine.model)
        self.jp=np.zeros((3,engine.model.nv)); self.jr=np.zeros_like(self.jp)
        self.rotation=np.array([[0.,1,0],[1,0,0],[0,0,-1]])
        self.index=0; self.started=float(engine.data.time); self.segment_started=self.started
        self.segment_from=engine.data.site_xpos[engine.robot.site].copy()
        self.obj=self.basket=None
        self.target=engine.model.body('alphabet_soup_1_main').id
        self.destination=engine.model.body('basket_1_main').id
        self.closed=False
        from .p2_recording import GraspEvidence,FingerContactProbe,P2EpisodeRecorder
        try:
            self.evidence=GraspEvidence()
            self.contact_probe=FingerContactProbe(engine,self.target)
            self.recorder=P2EpisodeRecorder(engine,self.target,self.contact_probe,self.evidence)
            self.recorder.capture(self.names[0])
        except Exception:
            self.runtime.close()
            raise

    def status(self):
        return {'kind':'scripted_self_test','status':'running','phase':self.names[min(self.index,9)],
                'progress':min(.99,max(0,(self.engine.data.time-self.started)/sum(self.durations))),
                'is_vla':False,'official_success':None,'record_dir':str(self.recorder.directory),
                'metrics':self.evidence.summary(),'sample_count':self.recorder.count}

    def after_step(self):
        contacts=self.contact_probe.read()
        self.evidence.observe(float(self.engine.data.time),float(self.engine.data.xpos[self.target,2]),
                              *contacts,lifting=self.index in (3,4))
        self.recorder.capture(self.names[min(self.index,9)],contacts)
        self.engine.execution=self.status()

    def _ik(self,position):
        e=self.engine; m,d,r=e.model,e.data,e.robot
        self.scratch.qpos[:]=d.qpos
        self.scratch.qpos[r.qadr]=r.targets
        for _ in range(12):
            mujoco.mj_forward(m,self.scratch)
            current=self.scratch.site_xmat[r.site].reshape(3,3)
            er=.5*np.cross(current.T,self.rotation.T).sum(axis=0)
            error=np.r_[position-self.scratch.site_xpos[r.site],er*.5]
            if np.linalg.norm(error)<.0005: break
            mujoco.mj_jacSite(m,self.scratch,self.jp,self.jr,r.site)
            jac=np.r_[self.jp[:,r.vadr],self.jr[:,r.vadr]*.5]
            delta=jac.T@np.linalg.solve(jac@jac.T+.0001*np.eye(6),error)
            self.scratch.qpos[r.qadr]=np.clip(self.scratch.qpos[r.qadr]+np.clip(delta,-.08,.08),r.limits[:,0]+.01,r.limits[:,1]-.01)
        r.targets[:]=self.scratch.qpos[r.qadr]

    def before_step(self):
        e=self.engine; elapsed=e.data.time-self.segment_started
        if elapsed>=self.durations[self.index]-1e-7:
            if self.index==0:
                self.obj=e.data.xpos[self.target].copy()
                self.basket=e.data.xpos[self.destination].copy()
                self.evidence.baseline_z=float(self.obj[2])
            if self.index==4 and not self.evidence.summary()['lift_verified']:
                raise RuntimeError('P2 提升未通过：未观测到持续双指接触与足够提升高度')
            self.index+=1
            if self.index>=len(self.names):
                success=self.runtime.success(e)
                e.execution={'kind':'scripted_self_test','status':'succeeded' if success else 'failed',
                             'phase':'LIBERO 判定成功' if success else 'LIBERO 判定未完成',
                             'progress':1.,'is_vla':False,'official_success':success}
                e.running=False; self.close(); return
            self.segment_started=float(e.data.time)
            self.segment_from=e.data.site_xpos[e.robot.site].copy()
            elapsed=0.
        if self.index==0:
            e.robot.opening=1
        else:
            goals=[None,self.obj+[0,0,.18],self.obj+[0,0,.02],self.obj+[0,0,.02],
                   self.obj+[0,0,.3],self.basket+[0,0,.35],self.basket+[0,0,.22],
                   self.basket+[0,0,.22],self.basket+[0,0,.4],self.basket+[0,0,.4]]
            alpha=min(1.,elapsed/max(.1,self.durations[self.index]-.6))
            alpha=alpha*alpha*(3-2*alpha)
            self._ik(self.segment_from+(goals[self.index]-self.segment_from)*alpha)
            e.robot.opening=0. if 3<=self.index<=6 else 1.
        e.execution=self.status()

    def close(self):
        if not self.closed:
            self.closed=True
            try: self.runtime.close()
            finally:
                self.engine.last_episode=self.recorder.finish(self.engine.execution)
                self.engine.execution.update(record_dir=str(self.recorder.directory),metrics=self.evidence.summary(),sample_count=self.recorder.count)

class PolicyExecution:
    """Observation -> replaceable policy -> official LIBERO OSC -> physics."""
    def __init__(self,engine,max_steps=20):
        if engine.objects: raise ValueError('当前策略执行请使用未添加额外刚体的 LIBERO 场景')
        self.engine=engine; self.adapter=engine.vla
        self.is_mock=not self.adapter.status()['real_model']
        if not self.is_mock and not self.adapter.status()['model_loaded']:
            raise ValueError('请先连接已真实加载模型的推理服务，或选择 Mock Policy 进行轻量自检')
        self.runtime=LiberoRuntime(engine)
        self.max_steps=max_steps; self.steps=0; self.requests=0
        self.actions=deque(); self.future=None
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='vla-http')
        self.closed=False

    def tick(self):
        e=self.engine
        if not self.actions:
            if self.future is None:
                obs=e.policy_observation(); self.requests+=1
                self.future=self.pool.submit(self.adapter.predict,obs)
                e.execution=self.status('等待策略动作' if self.is_mock else '等待真实模型推理')
                return
            if not self.future.done(): return
            result=self.future.result(); self.future=None
            from .policy import validate_actions
            self.actions.extend(validate_actions(result['actions']))
        action=self.actions.popleft()
        success=self.runtime.action(e,action)
        self.steps+=1
        e.execution=self.status('执行 Mock 自检动作（非 VLA）' if self.is_mock else '执行 VLA 动作')
        e.execution['latest_action']=action
        e.execution['official_success']=success
        if success or self.steps>=self.max_steps:
            e.execution['status']='completed' if self.is_mock else 'succeeded' if success else 'limit_reached'
            e.execution['phase']='Mock 接口自检完成（非 VLA，不代表抓取成功）' if self.is_mock else 'LIBERO 判定成功' if success else '已达到步数上限，任务未成功'
            e.running=False; self.close()

    def status(self,phase):
        return {'kind':'mock' if self.is_mock else 'vla','status':'running','phase':phase,
                'is_vla':not self.is_mock,'progress':self.steps/self.max_steps,
                'steps':self.steps,'requests':self.requests,'official_success':None}

    def close(self):
        if not self.closed:
            if self.future: self.future.cancel()
            self.pool.shutdown(wait=False,cancel_futures=True)
            self.runtime.close(); self.closed=True
