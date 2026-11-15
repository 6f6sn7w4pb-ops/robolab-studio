"""P2 规则抓取证据与轨迹；只读取物理状态，不写物体位姿。"""
from datetime import datetime, timezone
import hashlib
import importlib.metadata as md
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET

from .config import ROOT, LIBERO_ROOT


class GraspEvidence:
    """按控制采样时刻检查连续双指接触与提升；不是接触力/接触点数据集。"""
    def __init__(self, lift_height=.10, hold_seconds=.50, contact_seconds=.20):
        self.lift_height, self.hold_seconds, self.contact_seconds = lift_height, hold_seconds, contact_seconds
        self.baseline_z = None
        self.previous_time = None
        self.contact_streak = self.lift_streak = 0.
        self.max_contact_streak = self.max_lift_streak = self.max_height = 0.
        self.previous_contact = self.previous_lift = False

    def observe(self, sim_time, target_z, left, right, lifting):
        if not math.isfinite(sim_time) or not math.isfinite(target_z): raise ValueError('Non-finite P2 observation')
        delta = 0. if self.previous_time is None else sim_time-self.previous_time
        if delta < -1e-8: raise ValueError('P2 simulation time moved backwards')
        self.previous_time = sim_time
        gain = 0. if self.baseline_z is None else target_z-self.baseline_z
        if self.baseline_z is not None: self.max_height = max(self.max_height, gain)
        touching = bool(left and right and lifting)
        lifted = touching and gain>=self.lift_height
        self.contact_streak = self.contact_streak+delta if touching and self.previous_contact else 0.
        self.lift_streak = self.lift_streak+delta if lifted and self.previous_lift else 0.
        self.previous_contact, self.previous_lift = touching, lifted
        self.max_contact_streak = max(self.max_contact_streak, self.contact_streak)
        self.max_lift_streak = max(self.max_lift_streak, self.lift_streak)

    def summary(self):
        return {'baseline_target_z_m': self.baseline_z, 'required_lift_m': self.lift_height,
                'required_contact_s': self.contact_seconds, 'required_stable_lift_s': self.hold_seconds,
                'max_height_gain_m': self.max_height,
                'max_bilateral_contact_s': self.max_contact_streak,
                'max_stable_lift_s': self.max_lift_streak,
                'finger_contact_verified': self.max_contact_streak+1e-8>=self.contact_seconds,
                'lift_verified': self.max_lift_streak+1e-8>=self.hold_seconds}


class FingerContactProbe:
    def __init__(self, engine, target_body):
        self.engine, self.target_body = engine, target_body
        model = engine.model
        if len(engine.robot.gids)!=2: raise ValueError('P2 validation requires the known two-finger Panda gripper')

        def geoms_below(body):
            descendants = set()
            for candidate in range(model.nbody):
                current = candidate
                while current and current!=body: current = int(model.body_parentid[current])
                if current==body: descendants.add(candidate)
            return {i for i in range(model.ngeom) if int(model.geom_bodyid[i]) in descendants}

        self.target_geoms = geoms_below(target_body)
        self.finger_geoms = [geoms_below(int(model.jnt_bodyid[joint])) for joint in engine.robot.gids]
        if not self.target_geoms or any(not geoms for geoms in self.finger_geoms):
            raise ValueError('Cannot identify P2 target/finger collision geometry')
        self.mapping = {'target': [model.geom(i).name for i in sorted(self.target_geoms)],
                        'finger_1': [model.geom(i).name for i in sorted(self.finger_geoms[0])],
                        'finger_2': [model.geom(i).name for i in sorted(self.finger_geoms[1])]}

    def read(self):
        touching = [False, False]
        for contact in self.engine.data.contact:
            if float(contact.dist)>0: continue
            first, second = int(contact.geom1), int(contact.geom2)
            if first in self.target_geoms: other=second
            elif second in self.target_geoms: other=first
            else: continue
            for index, geoms in enumerate(self.finger_geoms):
                if other in geoms: touching[index]=True
        return touching


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_head(path):
    result = subprocess.run(['git','-C',str(path),'rev-parse','HEAD'],capture_output=True,text=True,timeout=10)
    return result.stdout.strip() if result.returncode==0 else None


class P2EpisodeRecorder:
    def __init__(self, engine, target_body, probe, evidence, *, record_contacts=False):
        self.engine, self.target_body, self.probe, self.evidence = engine, target_body, probe, evidence
        output_root = Path(os.getenv('ROBOLAB_P2_OUTPUT_DIR',str(ROOT/'outputs'/'p2_grasp'))).expanduser().resolve()
        self.directory = output_root/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:6])
        self.directory.mkdir(parents=True,exist_ok=False)
        self.trajectory = (self.directory/'trajectory.jsonl').open('x',encoding='utf-8')
        self.started_wall = time.monotonic(); self.start_time = float(engine.data.time)
        self.count = 0; self.closed = False; self.result = None
        # v2 is a sampled full-scene state view, not deterministic simulation
        # re-execution. Original RGB and solver internals are not recorded.
        (self.directory/'model.xml').write_text(engine.base_xml, encoding='utf-8')
        asset_hashes = {}
        for node in ET.fromstring(engine.base_xml).findall('.//asset/*'):
            if node.get('file'):
                asset = Path(node.get('file')).expanduser()
                if not asset.is_absolute() or not asset.is_file():
                    self.trajectory.close(); self.closed = True
                    raise ValueError('State view requires existing absolute local asset paths')
                asset_hashes[str(asset)] = sha(asset)
        versions = {}
        for name in ('mujoco','robosuite','libero','numpy'):
            try: versions[name]=md.version(name)
            except md.PackageNotFoundError: versions[name]=None
        self.metadata = {'schema':'robolab.p2_episode.v2','status':'recording','is_vla':False,
            'created_at_utc':datetime.now(timezone.utc).isoformat(),'task':dict(engine.task),
            'directory':str(self.directory),'robot_model':'LIBERO Panda','joint_names':engine.robot.names,
            'control':{'adapter':engine.robot_adapter,'action':'joint_targets_rad_and_gripper_open_fraction',
                       'control_period_s':.02,'physics_timestep_s':float(engine.model.opt.timestep),
                       'joint_target_slew_limit_rad_s':.6,'joint_limits_rad':engine.robot.limits.tolist(),
                       'actuator_names':[engine.model.actuator(i).name for i in range(engine.model.nu)],
                       'actuator_ctrlrange_native':engine.model.actuator_ctrlrange.tolist(),
                       'actuator_units_note':'Panda arm motors: torque N m; finger position servos: m'},
            'coordinates':{'positions':'world frame, m','quaternion_order':'wxyz'},
            'initial_qpos':engine.data.qpos.tolist(),'initial_qvel':engine.data.qvel.tolist(),
            'model_xml_sha256':hashlib.sha256(engine.base_xml.encode()).hexdigest(),
            'geometry_mapping':probe.mapping,'versions':versions,'python':platform.python_version(),
            'source':{'libero_commit':git_head(LIBERO_ROOT),'project_commit':git_head(ROOT),
                      'sha256':{name:sha(ROOT/'robolab'/name) for name in ('task_control.py','engine.py','adapters.py','p2_recording.py')}},
            'state_view':{'semantics':'sampled_scene_state_v1', 'model_file':'model.xml',
                'model_file_sha256':sha(self.directory/'model.xml'),
                'dimensions':{key:int(getattr(engine.model,key)) for key in ('nq','nv','na','nu','nmocap','nuserdata')},
                'fields':['qpos','qvel','act','ctrl','mocap_pos','mocap_quat','userdata','qacc_warmstart','qfrc_applied','xfrc_applied'],
                'original_rgb_recorded':False, 'solver_snapshot_recorded':False,
                'asset_sha256':asset_hashes,
                'asset_paths':'existing local LIBERO assets; not a portable asset bundle'},
            'claims':{'real_vla_inference':False,'contact_points_exported':False,'full_episode_replay_implemented':False,
                      'sampled_scene_state_view':True}}
        if record_contacts:
            from .contact_store import EXPECTED
            self.metadata[EXPECTED] = True
        try: self._write_metadata()
        except Exception:
            self.trajectory.close(); self.closed=True
            raise

    def _write_metadata(self):
        path = self.directory/'metadata.json'
        temporary = self.directory/'metadata.json.tmp'
        temporary.write_text(json.dumps(self.metadata,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        temporary.replace(path)

    def capture(self, phase, finger_contacts=None):
        e = self.engine
        state = e.robot.state()
        row = {'index':self.count,'sim_time':float(e.data.time),'phase':phase,
               'joint_position_rad':state['qpos'],'joint_velocity_rad_s':e.data.qvel[e.robot.vadr].tolist(),
               'joint_target_rad':state['targets'],'joint_servo_rad':e.robot.servo.tolist(),
               'gripper_open_fraction':float(e.robot.opening),'gripper_position_m':state['gripper_qpos'],
               'actuator_control_native':e.data.ctrl.tolist(),
               'eef_position_world_m':state['eef_position'],'eef_quaternion_wxyz':state['eef_quaternion_wxyz'],
               'target_position_world_m':e.data.xpos[self.target_body].tolist(),
               'target_quaternion_wxyz':e.data.xquat[self.target_body].tolist(),
               'finger_target_contact':self.probe.read() if finger_contacts is None else finger_contacts}
        row['scene_state'] = {name:getattr(e.data,name).tolist() for name in self.metadata['state_view']['fields']}
        self.trajectory.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
        self.count += 1
        if self.count%50==0: self.trajectory.flush()

    def finish(self, execution):
        if self.closed: return self.result
        self.trajectory.flush(); self.trajectory.close(); self.closed=True
        metrics = self.evidence.summary()
        status = execution.get('status','cancelled')
        passed = status=='succeeded' and metrics['finger_contact_verified'] and metrics['lift_verified'] and execution.get('official_success') is True
        self.metadata.update(status=status,phase=execution.get('phase'),p2_passed=passed,
            official_task_success=execution.get('official_success'),metrics=metrics,sample_count=self.count,
            elapsed_simulation_s=float(self.engine.data.time)-self.start_time,
            elapsed_wall_s=round(time.monotonic()-self.started_wall,3),
            trajectory_sha256=sha(self.directory/'trajectory.jsonl'),final_qpos=self.engine.data.qpos.tolist())
        self.result={'directory':str(self.directory),'metadata':str(self.directory/'metadata.json'),
                     'trajectory':str(self.directory/'trajectory.jsonl'),'status':status,'p2_passed':passed,
                     'official_task_success':execution.get('official_success'),'metrics':metrics,'sample_count':self.count}
        self._write_metadata()
        return self.result
