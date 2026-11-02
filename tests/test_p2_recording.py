"""P2 判定的边界与反例；不加载机器人或模型。"""
import unittest
from robolab.p2_recording import GraspEvidence


class EvidenceTests(unittest.TestCase):
    def sequence(self, evidence, height, contact, count=40):
        for index in range(count): evidence.observe(index*.02,height,*contact,lifting=True)

    def test_height_without_finger_contact_does_not_pass(self):
        evidence=GraspEvidence();evidence.baseline_z=0.
        self.sequence(evidence,.3,(False,False))
        self.assertFalse(evidence.summary()['lift_verified'])
        self.assertFalse(evidence.summary()['finger_contact_verified'])

    def test_contact_without_lift_does_not_pass(self):
        evidence=GraspEvidence();evidence.baseline_z=0.
        self.sequence(evidence,.03,(True,True))
        self.assertTrue(evidence.summary()['finger_contact_verified'])
        self.assertFalse(evidence.summary()['lift_verified'])

    def test_short_or_interrupted_lift_does_not_pass(self):
        evidence=GraspEvidence();evidence.baseline_z=0.
        for index in range(100):
            evidence.observe(index*.02,.2,index%20!=0,True,lifting=True)
        self.assertFalse(evidence.summary()['lift_verified'])
        self.assertLess(evidence.summary()['max_stable_lift_s'],.5)

    def test_duration_uses_first_and_last_valid_samples(self):
        evidence=GraspEvidence();evidence.baseline_z=0.
        self.sequence(evidence,.2,(True,True),count=26)
        self.assertTrue(evidence.summary()['lift_verified'])
        self.assertAlmostEqual(evidence.summary()['max_stable_lift_s'],.5)
        with self.assertRaises(ValueError):evidence.observe(-1,.2,True,True,lifting=True)


if __name__=='__main__':unittest.main()
