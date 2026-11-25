"""Measured lab layout: robot table, object table and RealSense D455 cameras around the Panda.

The P4 scenes put the Panda and the objects on one floor plane. In the lab the Panda stands on
its own table and the objects lie on a lower table in front of it. ``lab_xml_hook`` rewrites a
P4 scene (PilotScene.install's xml_hook) into that layout; robot, controller and objects are
unchanged, only their heights and the surrounding furniture and cameras differ.

World frame: the scene's own (x forward from the robot, z up, metres). The robot base keeps its
x/y; the floor stays at z = 0. All lengths are metres.
"""
from dataclasses import dataclass
import math
import xml.etree.ElementTree as ET

import numpy as np

from .p4_parts import TARGET
from .p4_pilot_scene import OBSTACLE

ROBOT_BASE = 'robot0_base'
HAND_BODY = 'robot0_right_hand'
OBSTACLE_BODY = 'milk_1_main'

# RealSense D455 colour sensor (OV9782, global shutter): nominal retail values (90 x 65 deg at
# 1280 x 800; some listings say 86 x 57 deg). The 2026-11-17 camera study
# (scripts/study_lab_cameras.py) ran with these; the measured units are D455_UNITS below.
D455_RGB = dict(width=1280, height=800, vfov_deg=65., hfov_deg=90., alt_vfov_deg=57.,
                body_m=(.124, .029, .026))      # length, height, depth

# The lab's two D455 units, read from the cameras on 2026-11-25 (docs/D455_CAMERA_PARAMS.md,
# docs/calibration/d455_cameras.json; roles assigned by the user the same day). fovy is MuJoCo's
# vertical field of view of the 1280 x 800 colour stream, 2 atan(800 / (2 fy)). The two units'
# focal lengths differ by 2.4 %, so each simulated camera uses its own unit's value.
D455_UNITS = {
    'wrist': dict(label='camera1', serial='246322302827', fovy_deg=63.7889),
    'external': dict(label='camera2', serial='141322252821', fovy_deg=65.0309),
}

# The RGB lens is not at the middle of the 124 mm body. The cameras' own extrinsics put it
# 58.9 mm from the left infrared imager towards the right one, and the imagers 95.0 mm apart;
# with the imagers symmetric about the body centre (an assumption, to be checked with
# calipers), the lens sits 11.4 mm from the centre towards the image's right.
D455_LENS_FROM_CENTRE_M = .0114


@dataclass(frozen=True)
class LabLayout:
    """Measurements from the lab (user, 2026-11-17). Object table abuts the robot table's front
    long edge, centred on the robot. The robot table width (0.60) was not measured."""
    robot_table_height: float = .89
    robot_table_length: float = 1.20       # along y
    robot_table_width: float = .60         # along x
    base_to_front_edge: float = .21        # robot base centre to the long edge it faces
    base_to_short_edge: float = .60        # robot base centre to one short edge
    object_table_height: float = .74
    object_table_length: float = 1.00      # along y
    object_table_width: float = .60        # along x
    gap: float = 0.                        # between the two tables
    table_rgba: tuple = (.93, .91, .86, 1.)   # white with a slight beige tint
    top_thickness: float = .03
    leg_size: float = .04

    def front_edge_x(self, base_x):
        return base_x+self.base_to_front_edge

    def object_table_x(self, base_x):
        near = self.front_edge_x(base_x)+self.gap
        return near, near+self.object_table_width

    def workspace_center(self, base_x):
        near, far = self.object_table_x(base_x)
        return np.array([(near+far)/2, 0., self.object_table_height])


def camera_axes(position, target, up=(0., 0., 1.)):
    """MuJoCo camera xyaxes (x right, y up, looking along -z) aimed from position at target."""
    forward = np.asarray(target, float)-np.asarray(position, float)
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, up)
    if np.linalg.norm(right) < 1e-9:
        right = np.cross(forward, (1., 0., 0.))
    right /= np.linalg.norm(right)
    upward = np.cross(right, forward)
    return np.r_[right, upward]


def _fmt(values):
    return ' '.join(f'{float(v):.6g}' for v in values)


def _table(world, name, x0, x1, y0, y1, height, layout, collide):
    rgba = _fmt(layout.table_rgba)
    top = layout.top_thickness
    ET.SubElement(world, 'geom', name=f'{name}_top', type='box', group='1', rgba=rgba,
                  pos=_fmt(((x0+x1)/2, (y0+y1)/2, height-top/2)),
                  size=_fmt(((x1-x0)/2, (y1-y0)/2, top/2)),
                  **({} if collide else dict(contype='0', conaffinity='0')))
    leg = layout.leg_size/2
    for i, (x, y) in enumerate(((x0+leg, y0+leg), (x0+leg, y1-leg), (x1-leg, y0+leg), (x1-leg, y1-leg))):
        ET.SubElement(world, 'geom', name=f'{name}_leg{i}', type='box', group='1', rgba=rgba,
                      contype='0', conaffinity='0', pos=_fmt((x, y, (height-top)/2)),
                      size=_fmt((leg, leg, (height-top)/2)))


def wrist_camera_pose(offset=.06, height=0., tilt_deg=20., aim_depth=.097):
    """Pose in the hand frame of a D455 beside the Franka hand.

    Hand frame (robosuite): +z towards the fingertips, fingers open along y, the camera sits on
    the +x side. ``tilt_deg`` turns the optical axis from +z towards the hand axis; 0 looks
    straight along the fingers. ``aim_depth`` is only reported: the tilt that centres the
    grasp point is atan(offset / (aim_depth - height)).
    """
    tilt = math.radians(tilt_deg)
    forward = np.array([-math.sin(tilt), 0., math.cos(tilt)])     # towards the axis and fingers
    right = np.array([0., 1., 0.])                               # along the finger opening
    up = np.cross(right, forward)
    return np.array([offset, 0., height]), np.r_[right, up], forward, math.degrees(math.atan2(offset, aim_depth-height))


def d455_body_box(position, xyaxes, forward, lens_from_centre=D455_LENS_FROM_CENTRE_M, front_ahead_m=-.001):
    """The D455 body as a box around its RGB lens: (centre, axes, half_sizes), in the frame of
    ``position``. axes rows are the box's length (image right), height (image up) and depth
    (backwards from the lens) directions. ``front_ahead_m`` is how far the front face is ahead of
    the lens: the default keeps it 1 mm behind, so the rendered camera does not see its own body;
    clearance checks use the physical front glass a few millimetres ahead of the optical centre."""
    length, height, depth = D455_RGB['body_m']
    right, up = np.asarray(xyaxes[:3], float), np.asarray(xyaxes[3:], float)
    forward = np.asarray(forward, float)
    centre = np.asarray(position, float)+forward*(front_ahead_m-depth/2)-right*lens_from_centre
    return centre, np.stack([right, up, -forward]), np.array([length/2, height/2, depth/2])


def lab_xml_hook(layout=LabLayout(), cameras=(), wrist=None, d455_body=True, external_fovy_deg=None,
                 wrist_fovy_deg=None):
    """xml_hook for PilotScene.install. cameras: [(name, position, target)] in world metres, all
    rendered as the external unit; wrist: dict(name=..., offset=..., height=..., tilt_deg=...) or
    None. Each D455 unit gets its own measured fovy (D455_UNITS) unless overridden."""
    fovy = D455_UNITS['external']['fovy_deg'] if external_fovy_deg is None else external_fovy_deg
    wrist_fovy = D455_UNITS['wrist']['fovy_deg'] if wrist_fovy_deg is None else wrist_fovy_deg

    def hook(scene, xml_text):
        root = ET.fromstring(xml_text)
        world = root.find('worldbody')
        bodies = {body.get('name'): body for body in root.iter('body')}
        base = bodies[ROBOT_BASE]
        bx, by, _ = map(float, base.get('pos').split())
        base.set('pos', _fmt((bx, by, layout.robot_table_height)))
        for name in (TARGET, OBSTACLE_BODY):
            x, y, z = map(float, bodies[name].get('pos').split())
            bodies[name].set('pos', _fmt((x, y, z+layout.object_table_height)))
        front = layout.front_edge_x(bx)
        y_shift = layout.base_to_short_edge-layout.robot_table_length/2
        _table(world, 'lab_robot_table', front-layout.robot_table_width, front,
               by-layout.robot_table_length/2-y_shift, by+layout.robot_table_length/2-y_shift,
               layout.robot_table_height, layout, collide=True)
        near, far = layout.object_table_x(bx)
        _table(world, 'lab_object_table', near, far, by-layout.object_table_length/2, by+layout.object_table_length/2,
               layout.object_table_height, layout, collide=True)
        for name, position, target in cameras:
            ET.SubElement(world, 'camera', name=name, mode='fixed', pos=_fmt(position),
                          xyaxes=_fmt(camera_axes(position, target)), fovy=f'{fovy:.6g}')
        if wrist:
            hand = bodies[HAND_BODY]
            position, xyaxes, forward, _ = wrist_camera_pose(wrist.get('offset', .06), wrist.get('height', 0.),
                                                             wrist['tilt_deg'])
            if d455_body:
                centre, _, half = d455_body_box(position, xyaxes, forward)
                ET.SubElement(hand, 'geom', name=wrist['name']+'_body', type='box', group='1',
                              contype='0', conaffinity='0', rgba='.12 .12 .13 1', pos=_fmt(centre),
                              xyaxes=_fmt(xyaxes), size=_fmt(half))
            ET.SubElement(hand, 'camera', name=wrist['name'], mode='fixed', pos=_fmt(position),
                          xyaxes=_fmt(xyaxes), fovy=f'{wrist_fovy:.6g}')
        return ET.tostring(root, encoding='unicode')

    return hook
