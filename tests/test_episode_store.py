"""Integrity and classification boundaries independent of Qt/MuJoCo."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from robolab.episode_store import inspect_episode, list_episodes, load_episode


class EpisodeStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.directory = self.root/'episode'; self.directory.mkdir()
        self.rows = [dict(index=i,sim_time=i*.02,joint_position_rad=[0.]*7,
                          eef_position_world_m=[0.]*3,target_position_world_m=[0.]*3) for i in range(2)]
        self.metadata = dict(schema='robolab.p2_episode.v1', status='succeeded', sample_count=2,
                             task={}, p2_passed=True, official_task_success=True,
                             metrics=dict(finger_contact_verified=True,lift_verified=True))
        self.save()

    def tearDown(self): self.tmp.cleanup()

    def save(self):
        payload = ''.join(json.dumps(row)+'\n' for row in self.rows).encode()
        (self.directory/'trajectory.jsonl').write_bytes(payload)
        self.metadata['trajectory_sha256'] = hashlib.sha256(payload).hexdigest()
        (self.directory/'metadata.json').write_text(json.dumps(self.metadata))

    def test_all_verdicts_and_incomplete_are_distinct(self):
        for status, expected in [('succeeded','success'),('failed','failed'),('cancelled','cancelled'),('recording','incomplete')]:
            self.metadata['status'] = status; self.save()
            self.assertEqual(inspect_episode(self.directory)['classification'],expected)
        (self.directory/'metadata.json').unlink()
        self.assertEqual(inspect_episode(self.directory)['classification'],'incomplete')

    def test_success_requires_all_original_evidence(self):
        for field in ('finger_contact_verified','lift_verified'):
            self.metadata['metrics'][field]=False; self.save()
            self.assertEqual(inspect_episode(self.directory)['classification'],'failed')
            self.metadata['metrics'][field]=True
        self.metadata['official_task_success']=False; self.save()
        self.assertEqual(inspect_episode(self.directory)['classification'],'failed')

    def test_hash_count_and_monotonic_time(self):
        with (self.directory/'trajectory.jsonl').open('a') as stream: stream.write('{}\n')
        self.assertEqual(inspect_episode(self.directory)['classification'],'corrupt')
        self.rows[1]['sim_time']=0; self.save()
        self.assertEqual(inspect_episode(self.directory)['classification'],'corrupt')
        self.rows[1]['sim_time']=.02; self.metadata['sample_count']=3; self.save()
        self.assertEqual(inspect_episode(self.directory)['classification'],'corrupt')

    def test_unknown_schema_and_invalid_json_are_isolated(self):
        self.metadata['schema']='future'; self.save()
        self.assertEqual(inspect_episode(self.directory)['classification'],'corrupt')
        (self.directory/'metadata.json').write_text('{')
        self.assertEqual(list_episodes(self.root)['records'][0]['classification'],'corrupt')

    def test_v1_read_does_not_claim_state_view(self):
        self.assertFalse(inspect_episode(self.directory)['state_view_supported'])
        metadata, rows=load_episode(self.directory)
        self.assertEqual(len(rows),2)
        self.assertEqual(metadata['schema'],'robolab.p2_episode.v1')

    def test_nonfinite_sample_is_corrupt_even_with_matching_hash(self):
        self.rows[0]['joint_position_rad'][0]=float('nan'); self.save()
        self.assertEqual(inspect_episode(self.directory)['classification'],'corrupt')

    def test_malformed_metadata_is_corrupt(self):
        self.metadata['task']=[]; self.save()
        self.assertEqual(inspect_episode(self.directory)['classification'],'corrupt')

    def test_v2_requires_model_hash_and_scene_state(self):
        self.metadata['schema']='robolab.p2_episode.v2'
        xml=b'<mujoco/>'
        (self.directory/'model.xml').write_bytes(xml)
        self.metadata['state_view']=dict(semantics='sampled_scene_state_v1',model_file_sha256=hashlib.sha256(xml).hexdigest(),
                                          dimensions=dict(nq=7,nv=7,na=0,nu=7,nmocap=0,nuserdata=0))
        self.save()
        self.assertEqual(inspect_episode(self.directory)['classification'],'corrupt')
        for row in self.rows:
            row['scene_state']=dict(qpos=[0.]*7,qvel=[0.]*7,act=[],ctrl=[0.]*7,mocap_pos=[],mocap_quat=[],
                                    userdata=[],qacc_warmstart=[0.]*7,qfrc_applied=[0.]*7,xfrc_applied=[[0.]*6])
        self.save()
        self.assertEqual(inspect_episode(self.directory)['classification'],'success')
        (self.directory/'model.xml').write_bytes(b'changed')
        self.assertEqual(inspect_episode(self.directory)['classification'],'corrupt')


if __name__=='__main__': unittest.main()
