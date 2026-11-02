"""Real cameras and physics; mock model only. No VLA weights are loaded."""
import base64
import json
import time
import unittest
from pathlib import Path
import numpy as np
from robolab.engine import Engine
from robolab.policy import validate_observation,scan_checkpoints

ROOT=Path(__file__).resolve().parents[1]

class PipelineTests(unittest.TestCase):
    def test_mock_physics_and_scripted_grasp(self):
        e=Engine(); evidence=ROOT/'evidence'; evidence.mkdir(exist_ok=True)
        try:
            e.load_task()
            scan=scan_checkpoints()
            (evidence/'weight-scan.json').write_text(json.dumps(scan,indent=2,ensure_ascii=False),encoding='utf-8')
            print('SCAN complete candidates',scan['complete_candidates'],flush=True)
            e.configure_policy('mock'); observation=e.policy_observation()
            validate_observation(observation)
            self.assertEqual(len(observation['proprio']),8)
            self.assertEqual(len(observation['images']),2)
            for name,value in observation['images'].items():
                (evidence/f'policy-{name}.jpg').write_bytes(base64.b64decode(value['data']))
                self.assertEqual(value['width'],224)
                self.assertEqual(len(observation['calibration'][name]['T_world_camera']),4)
                self.assertEqual(observation['calibration'][name]['image_rotation_deg'],0)
                self.assertGreater(observation['calibration'][name]['K'][0][0],0)
            before=e.data.qpos[e.robot.qadr].copy()
            start=e.data.time
            e.start_policy(8)
            deadline=time.monotonic()+30
            while e.running and time.monotonic()<deadline:
                e.step(); time.sleep(.001)
            self.assertFalse(e.running)
            self.assertEqual(e.execution['status'],'completed')
            self.assertEqual(e.execution['steps'],8)
            self.assertGreater(np.linalg.norm(e.data.qpos[e.robot.qadr]-before),.001)
            self.assertAlmostEqual(e.data.time-start,.4,places=4)
            self.assertFalse(e.policy_status()['model_loaded'])
            self.assertFalse(e.policy_status()['real_inference_verified'])
            mock_result=dict(e.execution)
            print('PASS real 2-camera observation -> Mock actions -> official OSC -> physical qpos motion',flush=True)
            e.reset(); e.start_grasp_test(); phases=[]
            for _ in range(1400):
                if not e.running: break
                e.step()
                phase=e.execution['phase']
                if not phases or phases[-1]!=phase: phases.append(phase)
            self.assertFalse(e.running)
            self.assertEqual(e.execution['status'],'succeeded')
            self.assertTrue(e.execution['official_success'])
            self.assertFalse(e.execution['is_vla'])
            e.screenshot(evidence/'grasp-self-test-success.png')
            report={'mock':mock_result,'model_status':e.policy_status(),'scripted_grasp':e.execution,
                    'scripted_phases':phases,'real_vla_tested':False,'weights_downloaded':False}
            (evidence/'vla-pipeline-result.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
            print('PASS physical scripted grasp, official LIBERO success=True; not a VLA result',flush=True)
        finally: e.close()

if __name__=='__main__': unittest.main(verbosity=2)
