"""Simulation adapters and contracts for future devices; no physical device I/O."""
from dataclasses import dataclass
from typing import Protocol
import numpy as np
import mujoco

class RobotAdapter(Protocol):
    def set_target(self, index: int, radians: float) -> None: ...
    def apply_control(self, dt: float) -> None: ...
    def state(self) -> dict: ...

class CameraAdapter(Protocol):
    def render(self) -> np.ndarray: ...

class PolicyAdapter(Protocol):
    """Future local/remote inference. No policy implementation is included in P0."""
    def predict(self, observations: dict, instruction: str) -> np.ndarray: ...
    def close(self) -> None: ...

class HardwareRobot(Protocol):
    """Future device driver contract. Not connected or implemented in P0."""
    def connect(self, configuration: dict) -> None: ...
    def joint_spec(self) -> list[dict]: ...
    def observe(self) -> dict: ...
    def command_joints(self, targets: list[float]) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...

class HardwareGripper(Protocol):
    def connect(self, configuration: dict) -> None: ...
    def observe(self) -> dict: ...
    def command_opening(self, metres: float) -> None: ...
    def close(self) -> None: ...

class HardwareCamera(Protocol):
    def connect(self, configuration: dict) -> None: ...
    def capture(self) -> dict: ...
    def calibration(self) -> dict: ...
    def close(self) -> None: ...

@dataclass(frozen=True)
class ObjectAsset:
    asset_id: str
    visual_path: str | None = None
    collision_path: str | None = None
    units: str = 'm'

class PandaJointAdapter:
    """Torque PD + bias compensation for the installed Panda model.

    Joint references are discovered from names captured from LIBERO. Servo
    targets are slew-limited and motor effort is clamped to model ctrlrange.
    Edit poses are separate from physical simulation commands.
    """
    def __init__(self, model, data, joint_names, gripper_names, eef_site):
        self.model, self.data = model, data
        self.names = list(joint_names)
        self.jids = [model.joint(n).id for n in joint_names]
        self.qadr = model.jnt_qposadr[self.jids]
        self.vadr = model.jnt_dofadr[self.jids]
        self.limits = model.jnt_range[self.jids].copy()
        self.targets = data.qpos[self.qadr].copy()
        self.servo = self.targets.copy()
        self.gripper_names = list(gripper_names)
        self.gids = [model.joint(n).id for n in gripper_names]
        self.gq = model.jnt_qposadr[self.gids]
        self.gv = model.jnt_dofadr[self.gids]
        self.gl = model.jnt_range[self.gids].copy()
        self.opening = 1.0
        self.site = model.site(eef_site).id
        self.arm_motors = [self._motor(j) for j in self.jids]
        self.grip_motors = [self._motor(j) for j in self.gids]
        # This adapter is deliberately limited to torque motors with unit gear.
        for aid in self.arm_motors + self.grip_motors:
            if not np.isclose(model.actuator_gear[aid, 0], 1):
                raise ValueError('Panda adapter requires unit actuator gear')

    def _motor(self, jid):
        ids = np.flatnonzero(self.model.actuator_trnid[:, 0] == jid)
        if not len(ids):
            raise ValueError(f'Missing actuator for joint {jid}')
        return int(ids[0])

    def set_target(self, index, radians):
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(self.names):
            raise ValueError('Unknown robot joint')
        if not np.isfinite(radians) or not self.limits[index, 0] <= radians <= self.limits[index, 1]:
            raise ValueError('Joint target exceeds model limits')
        self.targets[index] = radians

    def edit_pose(self):
        self.data.qpos[self.qadr] = self.targets
        self.data.qvel[self.vadr] = 0
        self.servo[:] = self.targets
        self.edit_gripper()
        mujoco.mj_forward(self.model, self.data)

    def edit_gripper(self):
        # Finger 1 positive, finger 2 negative, discovered from signed ranges.
        self.data.qpos[self.gq] = np.where(self.gl[:, 1] > 0, self.gl[:, 1], self.gl[:, 0]) * self.opening
        self.data.qvel[self.gv] = 0

    def apply_control(self, dt):
        self.servo += np.clip(self.targets-self.servo, -.6*dt, .6*dt)
        tau = 180*(self.servo-self.data.qpos[self.qadr]) - 28*self.data.qvel[self.vadr]
        tau += self.data.qfrc_bias[self.vadr]
        for aid, effort in zip(self.arm_motors, tau):
            self.data.ctrl[aid] = np.clip(effort, *self.model.actuator_ctrlrange[aid])
        desired = np.where(self.gl[:, 1] > 0, self.gl[:, 1], self.gl[:, 0]) * self.opening
        # Installed Panda gripper actuators are position servos (gain=100).
        for aid, q, v, goal in zip(self.grip_motors, self.gq, self.gv, desired):
            if self.model.actuator_biastype[aid] == mujoco.mjtBias.mjBIAS_AFFINE:
                command = goal
            else:
                command = 400*(goal-self.data.qpos[q])-8*self.data.qvel[v]
            self.data.ctrl[aid] = np.clip(command, *self.model.actuator_ctrlrange[aid])

    def state(self):
        quat = np.zeros(4)
        mujoco.mju_mat2Quat(quat, self.data.site_xmat[self.site])
        return {'names': self.names, 'qpos': self.data.qpos[self.qadr].tolist(),
                'targets': self.targets.tolist(), 'limits': self.limits.tolist(),
                'gripper_qpos': self.data.qpos[self.gq].tolist(), 'gripper_target': self.opening,
                'eef_position': self.data.site_xpos[self.site].tolist(), 'eef_quaternion_wxyz': quat.tolist()}

ROBOT_ADAPTERS = {'panda_joint_pd': PandaJointAdapter}

def register_robot_adapter(name, factory):
    """Register an alternate simulation robot adapter before creating an Engine."""
    if not isinstance(name, str) or not name or name in ROBOT_ADAPTERS or not callable(factory):
        raise ValueError('Adapter name must be new and factory must be callable')
    ROBOT_ADAPTERS[name] = factory

def create_robot_adapter(name, model, data, metadata):
    if name not in ROBOT_ADAPTERS:
        raise ValueError(f'Robot adapter is not installed: {name}')
    return ROBOT_ADAPTERS[name](model, data, *metadata)
