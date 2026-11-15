"""Isolated, read-only rendering of recorded states. Never call mj_step here."""
import base64
import hashlib
import io
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image

from .episode_store import inspect_episode, load_episode
from .contact_store import load_contacts_for_parent
from .contact_overlay import CAMERAS, draw_contacts, inspection_camera, restore_solver_pose, reveal_grasp, validate_options


class StateReplay:
    def __init__(self, directory):
        self.renderer = None
        self.directory = Path(directory).expanduser().resolve()
        self.record = inspect_episode(self.directory)
        self.contact_frames = None
        self.model = self.data = self.contact_data = None
        if self.record['classification'] in ('corrupt', 'incomplete'):
            raise ValueError('记录不完整或损坏，不能打开状态回放')
        self.metadata, self.rows = load_episode(self.directory)
        contact_record = self.record.get('contact_recording', {})
        self.can_view_contacts = contact_record.get('status') == 'available' and contact_record['sample_count'] > 0
        self.supported = self.metadata['schema'] == 'robolab.p2_episode.v2'
        if not self.supported: return
        view = self.metadata['state_view']
        if view.get('semantics') != 'sampled_scene_state_v1': raise ValueError('Unsupported state-view semantics')
        payload = (self.directory/'model.xml').read_bytes()
        if hashlib.sha256(payload).hexdigest() != view['model_file_sha256']:
            raise ValueError('P2 model XML SHA256 mismatch')
        if self.metadata['versions'].get('mujoco') != mujoco.__version__:
            raise ValueError('MuJoCo version differs from recorded state view')
        for name, digest in view['asset_sha256'].items():
            if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest:
                raise ValueError('Recorded local asset hash mismatch: '+name)
        self.model = mujoco.MjModel.from_xml_string(payload.decode('utf-8'))
        if any(int(getattr(self.model, key)) != size for key, size in view['dimensions'].items()):
            raise ValueError('Recorded model dimensions mismatch')
        self.data = mujoco.MjData(self.model)
        fields = ('qpos','qvel','act','ctrl','mocap_pos','mocap_quat','userdata','qacc_warmstart','qfrc_applied','xfrc_applied')
        if view['fields'] != list(fields): raise ValueError('Unsupported state fields')
        # Validate every frame before creating a GL context or returning a view.
        for row in self.rows:
            for name in fields:
                values = np.asarray(row['scene_state'][name], dtype=float)
                if values.size == 0 and getattr(self.data, name).size == 0:
                    values = values.reshape(getattr(self.data, name).shape)
                if values.shape != getattr(self.data, name).shape or not np.isfinite(values).all():
                    raise ValueError('Invalid recorded scene state: '+name)
        self.fields = fields
        self.options = mujoco.MjvOption(); self.options.geomgroup[0] = 0; self.options.sitegroup[:] = 0
        self.renderer = mujoco.Renderer(self.model, height=360, width=540)

    def frame(self, index, *, contact_view=False, contact_filter='target', forces=False, camera='agentview'):
        if type(contact_view) is not bool or camera not in CAMERAS:
            raise ValueError('无效的 P3 接触显示设置')
        validate_options(contact_filter, forces)
        if contact_view and not self.can_view_contacts:
            raise ValueError('此记录没有可查看的 P3 接触帧')
        result = {'record': self.record, 'supported': self.supported, 'count': len(self.rows),
                  'can_view_contacts': self.can_view_contacts}
        if not self.supported: return result
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(self.rows):
            raise ValueError('Replay index out of range')
        row = self.rows[index]
        sample = None
        pose = self.data
        if contact_view and index > 0:
            if self.contact_frames is None:
                _, self.contact_frames = load_contacts_for_parent(self.directory, self.metadata, self.rows)
                self.contact_data = mujoco.MjData(self.model)
            sample = self.contact_frames[index-1]
            pose = self.contact_data
            restore_solver_pose(self.model, pose, sample)
        else:
            self.data.time = row['sim_time']
            for name in self.fields:
                destination = getattr(self.data, name)
                destination[:] = np.asarray(row['scene_state'][name]).reshape(destination.shape)
            # Original P2 sampled state view. No policy, verdict, or physics step.
            mujoco.mj_forward(self.model, self.data)
        self.renderer._gl_context.make_current()
        chosen_camera = ('agentview' if not contact_view or camera == 'gripper' else camera)
        close_up = sample is not None and camera == 'gripper'
        if close_up:
            chosen_camera = inspection_camera(sample)
        self.renderer.update_scene(pose, camera=chosen_camera, scene_option=self.options)
        if close_up:
            reveal_grasp(self.model, self.renderer.scene, sample)
        overlay = draw_contacts(self.renderer.scene, sample, contact_filter, forces) if sample else None
        # Square contact views preserve the aspect ratio of the stored camera calibration.
        viewport = (self.renderer._width, self.renderer._height, self.renderer._rect)
        try:
            if contact_view:
                self.renderer._width = self.renderer._height = 360
                self.renderer._rect = mujoco.MjrRect(0, 0, 360, 360)
            rgb = self.renderer.render()
        finally:
            self.renderer._width, self.renderer._height, self.renderer._rect = viewport
        buf = io.BytesIO(); Image.fromarray(rgb).save(buf, 'JPEG', quality=85)
        result.update(index=index, sim_time=row['sim_time'], phase=row['phase'],
                      times=[r['sim_time'] for r in self.rows] if index == 0 else None,
                      contact_view=contact_view, rendered_contacts=overlay, rendered_time_s=float(pose.time),
                      view_camera='gripper' if close_up else chosen_camera,
                      jpeg=base64.b64encode(buf.getvalue()).decode('ascii'))
        return result

    def close(self):
        from .engine import dispose_renderer
        dispose_renderer(self.renderer)
        self.renderer = None
        self.contact_frames = None
        self.contact_data = None
