"""Integration tests against the actual installed MuJoCo and LIBERO, no mocks."""
import tempfile
import unittest
from pathlib import Path
import numpy as np
from robolab.engine import Engine

ROOT = Path(__file__).resolve().parents[1]

class RealEngineTests(unittest.TestCase):
    def test_full_editor_control_and_persistence(self):
        e = Engine()
        try:
            tasks = e.tasks()
            self.assertEqual(len(tasks), 10)
            scene = e.load_task()
            self.assertIn('alphabet soup', scene['task']['language'])
            self.assertEqual(len(e.robot.names), 7)
            before = e.render()
            self.assertGreater(float(before.std()), 10)
            e.screenshot(ROOT/'evidence'/'engine-initial.png')
            e.move_camera('orbit', .12, -.04)
            orbit = e.render()
            self.assertGreater(float(np.mean(np.abs(orbit.astype(float)-before))), 2)
            e.move_camera('pan', .02, .01)
            e.move_camera('zoom', dy=1)
            self.assertNotEqual(e.camera.distance, 1.45)
            e.move_camera('home')
            # Exercise ray picking on actual rendered geometry.
            hits = [e.pick(x, y) for x, y in [(.5,.5),(.45,.7),(.55,.65)]]
            self.assertTrue(any(hits))
            print('PASS orbit/pan/zoom/ray-pick, RGB render', before.shape, flush=True)
            q0 = e.data.qpos[e.robot.qadr].copy()
            e.set_joint(0, float(q0[0]+.1))
            self.assertAlmostEqual(e.data.qpos[e.robot.qadr[0]], q0[0]+.1)
            eef0 = e.robot.state()['eef_position']
            with self.assertRaises(ValueError): e.set_joint(0, 100)
            e.set_gripper(0)
            self.assertLess(np.max(np.abs(e.data.qpos[e.robot.gq])), 1e-8)
            e.set_gripper(1)
            self.assertGreater(np.max(np.abs(e.data.qpos[e.robot.gq])), .03)
            q_checkpoint = e.data.qpos.copy()
            e.play()
            with self.assertRaises(ValueError): e.add_object('box')
            target = float(e.robot.targets[0] + .12)
            e.set_joint(0, target)
            for _ in range(75): e.step()
            measured = e.data.qpos[e.robot.qadr].copy()
            self.assertLess(abs(measured[0]-target), .06)
            self.assertTrue(np.all(measured >= e.robot.limits[:,0]-.03))
            self.assertTrue(np.all(measured <= e.robot.limits[:,1]+.03))
            e.set_gripper(0)
            for _ in range(40): e.step()
            self.assertLess(np.max(np.abs(e.data.qpos[e.robot.gq])), .015)
            self.assertNotEqual(eef0, e.robot.state()['eef_position'])
            e.pause(); t = e.data.time
            self.assertFalse(e.running)
            e.step(); self.assertAlmostEqual(e.data.time-t, .02, places=5)
            e.reset(); np.testing.assert_allclose(e.data.qpos, q_checkpoint)
            self.assertEqual(e.data.time, 0)
            print('PASS all 7 limit-checked joints, PD motion, physical gripper, eef, step/reset', flush=True)
            for kind in ('box','sphere','cylinder'):
                obj = e.add_object(kind)
                self.assertGreater(e.model.body(obj['id']).mass[0], 0)
            object_id = e.objects[0]['id']
            e.update_object(object_id, {'position':[.15,-.12,.16], 'rotation':[15,20,30],
                                       'size':[.08,.06,.1], 'mass':.35, 'friction':[.7,.01,.001]})
            self.assertAlmostEqual(e.model.body(object_id).mass[0], .35)
            np.testing.assert_allclose(e.model.geom(object_id+'_geom').friction, [.7,.01,.001])
            np.testing.assert_allclose(e.model.geom(object_id+'_geom').size, [.04,.03,.05])
            e.update_object(object_id, {'collision':False})
            self.assertEqual(int(e.model.geom(object_id+'_geom').contype[0]), 0)
            e.update_object(object_id, {'collision':True})
            original = next(b for b in e.scene()['bodies'] if b['type']=='libero_object')
            pos = list(original['position']); pos[0] += .02
            e.set_placement(original['id'], pos, [0,0,15])
            np.testing.assert_allclose(e.body_info(original['id'])['position'], pos)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'scene.robolab.json'
                expected = e.project()
                e.save(path)
                e.delete_object(object_id)
                self.assertNotIn(object_id, [o['id'] for o in e.objects])
                e.load(path)
                self.assertIn(object_id, [o['id'] for o in e.objects])
                np.testing.assert_allclose(e.body_info(object_id)['position'], [.15,-.12,.16])
                np.testing.assert_allclose(e.robot.targets, expected['robot_qpos'])
                self.assertAlmostEqual(e.model.body(object_id).mass[0], .35)
                path.write_text('{"schema_version":999}')
                old_model = e.model
                with self.assertRaises(ValueError): e.load(path)
                self.assertIs(e.model, old_model)
            e.screenshot(ROOT/'evidence'/'engine-edited.png')
            z0 = e.body_info(object_id)['position'][2]
            e.play()
            for _ in range(30): e.step()
            z1 = e.body_info(object_id)['position'][2]
            self.assertLess(z1, z0)
            self.assertGreater(e.data.ncon, 0)
            e.reset()
            print('PASS primitives, physics properties, benchmark placement, JSON roundtrip, gravity/contact', flush=True)
            second = e.load_task(index=1)
            self.assertIn('cream cheese', second['task']['language'])
            self.assertEqual(len(e.objects), 0)
            print('PASS second LIBERO task and renderer resource replacement', flush=True)
        finally:
            e.close()

if __name__ == '__main__': unittest.main(verbosity=2)
