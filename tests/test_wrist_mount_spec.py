"""Numbers for the wrist D455 bracket (NumPy only)."""
import importlib.util
import math
from pathlib import Path
import unittest

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1]/'scripts'/'wrist_mount_spec.py'
_spec = importlib.util.spec_from_file_location('wrist_mount_spec', SCRIPT)
mount = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mount)


class WristMountSpecTests(unittest.TestCase):
    def test_default_tilt_aims_at_the_grasp_point(self):
        numbers = mount.spec(65., 10.)
        self.assertAlmostEqual(numbers['inputs']['tilt_deg'], round(math.degrees(math.atan2(65., 87.)), 2))
        self.assertLess(numbers['optical_axis_miss_real_grasp_point_deg'], .01)
        self.assertEqual(numbers['lens_real']['H'], [65., 0., 16.])            # 6 mm further from the flange

    def test_flange_frame_is_the_hand_frame_turned_back_45_degrees(self):
        numbers = mount.spec(60., 20.)
        h, f = np.array(numbers['lens_real']['H']), np.array(numbers['lens_real']['F'])
        self.assertAlmostEqual(float(np.hypot(*f[:2])), float(np.hypot(*h[:2])), places=1)
        self.assertAlmostEqual(math.degrees(math.atan2(f[1], f[0])), -45., places=1)
        self.assertAlmostEqual(float(f[2]), float(h[2]))

    def test_body_sits_on_the_far_side_of_the_lens(self):
        numbers = mount.spec(65., 10.)
        extent = numbers['body']['extent_real_H']
        self.assertGreater(extent['min'][0], 31.5)                             # outside the hand housing's width
        self.assertAlmostEqual(extent['max'][1]-extent['min'][1], 124., places=0)


if __name__ == '__main__':
    unittest.main()
