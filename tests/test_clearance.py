"""Box-to-mesh clearance used by the wrist camera mount study (NumPy only)."""
import math
import unittest

import numpy as np

from robolab.clearance import (box_mesh, box_mesh_clearance, box_surface_points, point_box_distance,
                               point_triangle_distance)

EYE = np.eye(3)


def rotation_z(degrees):
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return np.array([[c, s, 0.], [-s, c, 0.], [0., 0., 1.]])


class ClearanceTests(unittest.TestCase):
    def test_point_box_distance_is_signed(self):
        half = np.array([.1, .2, .3])
        d = point_box_distance([[0., 0., 0.], [.15, 0., 0.], [.13, .24, 0.], [.09, 0., 0.]], np.zeros(3), EYE, half)
        np.testing.assert_allclose(d, [-.1, .05, .05, -.01], atol=1e-12)

    def test_point_triangle_distance_regions(self):
        tri = np.array([[[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]]])
        points = [[.2, .2, .5],      # above the face
                  [.5, -.3, 0.],     # beside an edge
                  [-.3, -.4, 0.],    # beyond a corner
                  [.5, .5, -.2]]     # under the hypotenuse
        np.testing.assert_allclose(point_triangle_distance(points, tri),
                                   [.5, .3, .5, .2], atol=1e-12)

    def test_box_mesh_is_closed_and_on_the_surface(self):
        half = np.array([.062, .0145, .013])
        vertices, faces = box_mesh(np.array([1., 2., 3.]), rotation_z(30.), half)
        self.assertEqual(faces.shape, (12, 3))
        edges = {tuple(sorted(e)) for f in faces for e in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0]))}
        self.assertEqual(len(edges), 18)                 # 12 box edges + 6 face diagonals: watertight
        points = box_surface_points(np.array([1., 2., 3.]), rotation_z(30.), half, .002)
        np.testing.assert_allclose(point_box_distance(points, np.array([1., 2., 3.]), rotation_z(30.), half),
                                   0., atol=1e-12)

    def test_parallel_boxes_face_to_face(self):
        half = np.array([.05, .02, .01])
        other = box_mesh(np.array([0., 0., .05]), EYE, np.array([.2, .2, .02]))
        self.assertAlmostEqual(box_mesh_clearance(np.zeros(3), EYE, half, *other), .02, places=9)

    def test_corner_of_a_rotated_box_against_a_large_face(self):
        # Box turned 45 degrees about z: its edge reaches x = half*sqrt(2) and faces a wall at x = .1,
        # whose vertices are all far away (only the triangle interiors are close).
        half = np.array([.03, .03, .01])
        wall = box_mesh(np.array([.3, 0., 0.]), EYE, np.array([.2, 1., 1.]))
        expected = .1-.03*math.sqrt(2)
        self.assertAlmostEqual(box_mesh_clearance(np.zeros(3), rotation_z(45.), half, *wall), expected, places=9)

    def test_enough_returns_a_lower_bound_for_far_meshes(self):
        half = np.array([.03, .03, .01])
        wall = box_mesh(np.array([.3, 0., 0.]), EYE, np.array([.2, 1., 1.]))
        exact = box_mesh_clearance(np.zeros(3), rotation_z(45.), half, *wall)
        self.assertAlmostEqual(box_mesh_clearance(np.zeros(3), rotation_z(45.), half, *wall, enough=.2), exact, places=9)
        small = box_mesh(np.array([.5, 0., 0.]), EYE, np.full(3, .01))
        bound = box_mesh_clearance(np.zeros(3), EYE, half, *small, enough=.2)
        self.assertGreaterEqual(bound, .2)
        self.assertLessEqual(bound, .5-.01-.03+1e-12)

    def test_overlap_is_negative(self):
        half = np.array([.05, .05, .05])
        other = box_mesh(np.array([.08, 0., 0.]), EYE, np.array([.04, .01, .01]))
        self.assertLess(box_mesh_clearance(np.zeros(3), EYE, half, *other), 0.)


if __name__ == '__main__':
    unittest.main()
