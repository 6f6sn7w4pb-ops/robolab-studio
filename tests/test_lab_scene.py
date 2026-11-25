"""Measured lab layout: table geometry, camera aiming and the wrist mount (NumPy only)."""
import json
import math
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

import numpy as np

from robolab.lab_scene import (D455_LENS_FROM_CENTRE_M, D455_RGB, D455_UNITS, LabLayout, camera_axes, d455_body_box,
                               lab_xml_hook, wrist_camera_pose)

XML = ("<mujoco><worldbody><body name='floor'/><body name='robot0_base' pos='-0.6 0 0'>"
       "<body name='robot0_right_hand'/></body><body name='p4_stepped_bar' pos='-0.1 -0.2 .001'/>"
       "<body name='milk_1_main' pos='0 0 .031'/></worldbody></mujoco>")
CALIBRATION = Path(__file__).resolve().parents[1]/'docs'/'calibration'/'d455_cameras.json'
FRONT = [('front', (0.5, 0., 1.3), (-0.09, 0., .74))]


class LabSceneTests(unittest.TestCase):
    def test_tables_and_heights_follow_the_measurements(self):
        layout = LabLayout()
        root = ET.fromstring(lab_xml_hook(layout, [('front', (0.5, 0., 1.3), (-0.09, 0., .74))],
                                          wrist=dict(name='wrist', tilt_deg=20.))(None, XML))
        bodies = {b.get('name'): b for b in root.iter('body')}
        self.assertEqual(bodies['robot0_base'].get('pos'), '-0.6 0 0.89')
        self.assertAlmostEqual(float(bodies['p4_stepped_bar'].get('pos').split()[2]), .741)
        geoms = {g.get('name'): g for g in root.iter('geom')}
        top = geoms['lab_object_table_top']
        x, y, z = map(float, top.get('pos').split())
        hx, hy, hz = map(float, top.get('size').split())
        self.assertAlmostEqual(x-hx, -0.6+.21)          # touches the robot table's front edge
        self.assertAlmostEqual(2*hx, .60)
        self.assertAlmostEqual(2*hy, 1.0)
        self.assertAlmostEqual(z+hz, .74)
        robot_top = geoms['lab_robot_table_top']
        rx, _, rz = map(float, robot_top.get('pos').split())
        rhx, rhy, rhz = map(float, robot_top.get('size').split())
        self.assertAlmostEqual(rx+rhx, -0.6+.21)
        self.assertAlmostEqual(2*rhy, 1.2)
        self.assertAlmostEqual(rz+rhz, .89)
        self.assertIn('wrist', [c.get('name') for c in root.iter('camera')])

    def test_camera_axes_look_at_the_target(self):
        axes = camera_axes((1., 0., 1.), (0., 0., 0.))
        right, up = axes[:3], axes[3:]
        forward = np.cross(up, right)                    # camera looks along -z = cross(y, x)
        self.assertAlmostEqual(float(np.dot(right, up)), 0.)
        np.testing.assert_allclose(forward, np.array([-1., 0., -1.])/math.sqrt(2), atol=1e-12)
        self.assertGreater(up[2], 0.)

    def test_wrist_tilt_turns_towards_the_hand_axis(self):
        position, _, forward, centring = wrist_camera_pose(offset=.08, tilt_deg=20.)
        self.assertEqual(position.tolist(), [.08, 0., 0.])
        self.assertLess(forward[0], 0.)                  # towards the axis (camera sits at +x)
        self.assertAlmostEqual(math.degrees(math.atan2(-forward[0], forward[2])), 20.)
        self.assertAlmostEqual(centring, math.degrees(math.atan(.08/.097)))

    def test_each_unit_keeps_its_own_measured_fovy(self):
        def fovys(**overrides):
            root = ET.fromstring(lab_xml_hook(LabLayout(), FRONT, wrist=dict(name='wrist', tilt_deg=20.),
                                              **overrides)(None, XML))
            return {c.get('name'): float(c.get('fovy')) for c in root.iter('camera')}

        self.assertEqual(fovys(), dict(front=65.0309, wrist=63.7889))
        self.assertEqual(fovys(external_fovy_deg=57., wrist_fovy_deg=60.), dict(front=57., wrist=60.))

    def test_units_match_the_calibration_file(self):
        cameras = json.loads(CALIBRATION.read_text(encoding='utf-8'))['cameras'].values()
        by_role = {camera['role']: camera for camera in cameras}
        self.assertEqual(set(by_role), set(D455_UNITS))
        for role, unit in D455_UNITS.items():
            camera = by_role[role]
            self.assertEqual(camera['serial'], unit['serial'])
            colour = next(c for c in camera['color'] if (c['width'], c['height']) == (1280, 800))
            self.assertAlmostEqual(unit['fovy_deg'], math.degrees(2*math.atan(400/colour['fy'])), places=3)
            self.assertAlmostEqual(unit['fovy_deg'], camera['sim_1280x800']['fovy_deg'], places=4)

    def test_d455_body_sits_behind_the_lens_and_off_centre(self):
        position, xyaxes, forward, _ = wrist_camera_pose(offset=.06, height=.02, tilt_deg=25.)
        centre, axes, half = d455_body_box(position, xyaxes, forward)
        np.testing.assert_allclose(2*half, D455_RGB['body_m'])
        np.testing.assert_allclose(axes@axes.T, np.eye(3), atol=1e-12)
        lens = position-centre
        self.assertAlmostEqual(float(lens@axes[0]), D455_LENS_FROM_CENTRE_M)    # towards the image's right
        self.assertAlmostEqual(float(lens@axes[1]), 0.)
        self.assertAlmostEqual(float(lens@forward), half[2]+.001)               # front face 1 mm behind the lens
        glass, _, _ = d455_body_box(position, xyaxes, forward, front_ahead_m=.005)
        self.assertAlmostEqual(float((glass-centre)@forward), .006)             # physical glass 5 mm ahead
        root = ET.fromstring(lab_xml_hook(LabLayout(), (), wrist=dict(name='wrist', offset=.06, height=.02,
                                                                    tilt_deg=25.))(None, XML))
        body = next(g for g in root.iter('geom') if g.get('name') == 'wrist_body')
        np.testing.assert_allclose([float(v) for v in body.get('pos').split()], centre, atol=1e-6)
        np.testing.assert_allclose([float(v) for v in body.get('size').split()], half, atol=1e-6)


if __name__ == '__main__':
    unittest.main()
