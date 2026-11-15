"""Tiny real MuJoCo scenes: force convention and inactive margin candidates."""
import unittest
import numpy as np
import mujoco
from robolab.contact_geometry import contact_force_world


class ContactSolverTests(unittest.TestCase):
    def test_force_on_load_is_up_for_both_geom_orders(self):
        for reverse in (False,True):
            support='<body name="support"><geom name="support_geom" type="sphere" size=".05"/></body>'
            load='<body name="load" pos="0 0 .1"><freejoint/><geom name="load_geom" type="sphere" size=".05" mass="1"/></body>'
            model=mujoco.MjModel.from_xml_string('<mujoco><option timestep=".001"/><worldbody>'+((load+support) if reverse else (support+load))+'</worldbody></mujoco>')
            data=mujoco.MjData(model)
            for _ in range(2500): mujoco.mj_step(model,data)
            self.assertEqual(data.ncon,1)
            contact=data.contact[0]; wrench=np.zeros(6)
            mujoco.mj_contactForce(model,data,0,wrench)
            force,normal=contact_force_world(contact.frame.reshape(3,3),wrench,contact.geom2==model.geom('load_geom').id)
            np.testing.assert_allclose(force,[0,0,9.81],atol=.01,rtol=0)
            np.testing.assert_allclose(normal,[0,0,1],atol=1e-12,rtol=0)

    def test_margin_candidate_is_not_an_active_load(self):
        model=mujoco.MjModel.from_xml_string('<mujoco><worldbody><geom type="plane" size="1 1 .1" margin=".01" gap=".005"/><body pos="0 0 .057"><freejoint/><geom type="sphere" size=".05" mass="1"/></body></worldbody></mujoco>')
        data=mujoco.MjData(model); mujoco.mj_forward(model,data)
        self.assertEqual(data.ncon,1)
        contact=data.contact[0]; wrench=np.zeros(6)
        mujoco.mj_contactForce(model,data,0,wrench)
        self.assertGreater(contact.dist,0); self.assertLess(contact.efc_address,0)
        self.assertEqual(contact.exclude,1); np.testing.assert_array_equal(wrench,np.zeros(6))


if __name__=='__main__': unittest.main()
