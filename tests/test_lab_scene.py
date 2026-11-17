"""Measured lab layout: table geometry, camera aiming and the wrist mount (NumPy only)."""
import math
import unittest
import xml.etree.ElementTree as ET

import numpy as np

from robolab.lab_scene import LabLayout, camera_axes, lab_xml_hook, wrist_camera_pose

XML = ("<mujoco><worldbody><body name='floor'/><body name='robot0_base' pos='-0.6 0 0'>"
       "<body name='robot0_right_hand'/></body><body name='p4_stepped_bar' pos='-0.1 -0.2 .001'/>"
       "<body name='milk_1_main' pos='0 0 .031'/></worldbody></mujoco>")


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


if __name__ == '__main__':
    unittest.main()
