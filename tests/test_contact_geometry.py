"""P3 frame/sign boundaries using numpy only; no robot or renderer."""
import unittest
import numpy as np
from robolab.contact_geometry import (rigid_transform, point_to_local, point_to_world,
    vector_to_local, contact_force_world, validate_transform)


class ContactGeometryTests(unittest.TestCase):
    def test_translation_applies_to_points_only(self):
        transform=rigid_transform(np.eye(3),[1.,2.,3.])
        np.testing.assert_allclose(point_to_local(transform,[1.,2.,4.]),[0.,0.,1.])
        np.testing.assert_allclose(vector_to_local(transform,[1.,2.,4.]),[1.,2.,4.])

    def test_ninety_degree_rotation(self):
        transform=rigid_transform([[0,-1,0],[1,0,0],[0,0,1]],[2,3,4])
        np.testing.assert_allclose(point_to_world(transform,[1,0,0]),[2,4,4])
        np.testing.assert_allclose(vector_to_local(transform,[1,0,0]),[0,-1,0])

    def test_random_rigid_roundtrips_and_norms(self):
        rng=np.random.default_rng(123)
        for _ in range(60):
            rotation,_=np.linalg.qr(rng.normal(size=(3,3)))
            if np.linalg.det(rotation)<0: rotation[:,0]*=-1
            transform=rigid_transform(rotation,rng.normal(size=3))
            point=rng.normal(size=3); normal=rng.normal(size=3); normal/=np.linalg.norm(normal)
            np.testing.assert_allclose(point_to_world(transform,point_to_local(transform,point)),point,atol=1e-12,rtol=0)
            self.assertAlmostEqual(np.linalg.norm(vector_to_local(transform,normal)),1.,places=12)

    def test_contact_axis_rows_and_geom_role_sign(self):
        axes=[[0,0,1],[1,0,0],[0,1,0]]
        force,normal=contact_force_world(axes,[3,4,5,6,7,8],True)
        np.testing.assert_array_equal(force,[4,5,3]); np.testing.assert_array_equal(normal,[0,0,1])
        reaction,opposite=contact_force_world(axes,[3,4,5,6,7,8],False)
        np.testing.assert_array_equal(reaction,-force); np.testing.assert_array_equal(opposite,-normal)

    def test_invalid_reflection_scaled_rotation_nonfinite_and_shape(self):
        for rotation in (np.diag([-1,1,1]),np.diag([2,1,1]),np.full((3,3),np.nan)):
            with self.assertRaises(ValueError): rigid_transform(rotation,[0,0,0])
        bad=np.eye(4); bad[3,0]=1
        with self.assertRaises(ValueError): validate_transform(bad)
        with self.assertRaises(ValueError): point_to_local(np.eye(4),[1,2])
        with self.assertRaises(ValueError): contact_force_world(np.eye(3),[0]*5,True)


if __name__=='__main__': unittest.main()
