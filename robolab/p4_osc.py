"""Execute normalized OSC_POSE actions through the installed robosuite Panda."""
from importlib.metadata import version
import math

import mujoco
import numpy as np

from .p4_parts import TARGET

OSC_PERIOD_S = .05
ACTION_SPACE = 'robosuite_1_4_1_panda_osc_pose_normalized_7d_v1'
SAMPLING_MODE = 'policy_head_substep'


def _axis_angle(rotation):
    quat, vector = np.zeros(4), np.zeros(3)
    mujoco.mju_mat2Quat(quat, np.asarray(rotation).ravel())
    if quat[0] < 0:
        quat *= -1
    mujoco.mju_quat2Vel(vector, quat, 1.)
    return vector


class SyntheticOsc:
    """Use SingleArm.control and the normal substep loop on an isolated P4 scene."""
    def __init__(self, engine):
        from robosuite import macros
        from robosuite.controllers import load_controller_config
        from robosuite.robots.single_arm import SingleArm
        from robosuite.utils.binding_utils import MjSim

        if version('robosuite') != '1.4.1' or engine.task['source'] != 'synthetic_part_task':
            raise ValueError('P4 OSC requires the verified synthetic Panda and robosuite 1.4.1')
        dt = float(engine.model.opt.timestep)
        self.substeps = round(OSC_PERIOD_S/dt)
        if (not math.isclose(dt, macros.SIMULATION_TIMESTEP, abs_tol=1e-12, rel_tol=0)
                or not math.isclose(self.substeps*dt, OSC_PERIOD_S, abs_tol=1e-12, rel_tol=0)):
            raise ValueError('Unexpected OSC model/policy timebase')
        self.engine = engine
        self.sim = MjSim(engine.model)
        data = self.sim.data._data
        data.qpos[:] = engine.data.qpos
        data.qvel[:] = engine.data.qvel
        self.sim.forward()
        self.config = load_controller_config(default_controller='OSC_POSE')
        self.robot = SingleArm('Panda', controller_config=self.config, control_freq=20,
                               initial_qpos=data.qpos[engine.robot.qadr].copy())
        self.robot.load_model()
        self.robot.reset_sim(self.sim)
        self.robot.setup_references()
        self.robot.reset(deterministic=True)
        controller = self.robot.controller
        lo, hi = self.robot.action_limits
        if (self.robot.eef_site_id != engine.robot.site or self.robot.action_dim != 7
                or not np.all(lo == -1.) or not np.all(hi == 1.)
                or controller.control_dim != 6 or controller.impedance_mode != 'fixed'
                or not controller.use_delta or not controller.use_ori
                or not np.all(controller.output_min == -controller.output_max)):
            raise ValueError('Unexpected OSC controller or action contract')
        self.scale = controller.output_max.copy()
        self.controller = controller
        self.pose = mujoco.MjData(engine.model)
        engine.data = engine.robot.data = data
        engine.configure_contacts(target_body=TARGET)

    def action_toward(self, position, rotation, opening):
        site = self.engine.robot.site
        arm = np.zeros(6)
        if position is not None:
            self.pose.qpos[:] = self.engine.data.qpos
            mujoco.mj_kinematics(self.engine.model, self.pose)
            current = self.pose.site_xmat[site].reshape(3, 3)
            arm[:3] = position-self.pose.site_xpos[site]
            arm[3:] = _axis_angle(rotation @ current.T)
        return np.r_[np.clip(arm/self.scale, -1., 1.), -1. if opening else 1.]

    def step(self, action):
        from .policy import validate_actions
        values = action.tolist() if isinstance(action, np.ndarray) else action
        action = np.asarray(validate_actions([values])[0])
        if np.any(np.abs(action) > 1.):
            raise ValueError('Invalid normalized 7D OSC action')
        for substep in range(self.substeps):
            self.sim.forward()
            self.robot.control(action, policy_step=substep == 0)
            if substep == 0:
                before = self.engine.contact_sampler.prepare()
            self.sim.step()
            if substep == 0:
                self.engine.contact_sampler.capture(before, sampling_mode=SAMPLING_MODE)
        if not np.isfinite(self.engine.data.qpos).all() or not np.isfinite(self.engine.data.qvel).all():
            raise RuntimeError('Non-finite OSC dynamics')
        return self.engine.contact_sampler.latest
