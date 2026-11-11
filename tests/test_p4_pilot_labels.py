"""Geometry and time-contract regressions for the P4 pilot."""
import math
import unittest

import numpy as np

from robolab.p4_pilot_labels import box_surface, check_motion, motion_rows


class PilotLabelTests(unittest.TestCase):
    def test_nearest_rotated_box_surface_outside(self):
        center = np.array([3., 2., 1.])
        rotation = np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
        point = center+rotation @ [2., -4., 0.]
        surface, distance = box_surface(point, center, rotation, np.array([1., 2., .5]))
        np.testing.assert_allclose(surface, center+rotation @ [1., -2., 0.])
        self.assertAlmostEqual(distance, math.sqrt(5.))

    def test_inside_box_reports_boundary_and_negative_distance(self):
        surface, distance = box_surface(np.array([0., 0., .3]), np.zeros(3), np.eye(3), np.array([1., 2., .5]))
        np.testing.assert_allclose(surface, [0., 0., .5])
        self.assertAlmostEqual(distance, -.2)

    def test_motion_world_rotation_and_last_row_without_future_pose(self):
        initial, final = np.eye(4), np.eye(4)
        initial[:3, :3] = [[1., 0., 0.], [0., 0., -1.], [0., 1., 0.]]
        rotation = np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
        final[:3, :3] = rotation @ initial[:3, :3]
        final[:3, 3] = [.01, -.02, .03]
        poses = [(0, .018, initial, 0.), (5, .118, final, 1.)]
        rows = list(motion_rows(poses))
        np.testing.assert_allclose(rows[0]['action'], [.01, -.02, .03, 0., 0., math.pi/2, 1])
        self.assertIsNone(rows[-1]['action'])
        check_motion(rows, poses)
        rows[0]['action'][0] *= -1
        with self.assertRaises(ValueError):
            check_motion(rows, poses)
