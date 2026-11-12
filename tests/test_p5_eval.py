"""P5 evaluation metrics, aggregation and policy-bridge contracts (NumPy only; no simulator)."""
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

from robolab.p5_eval import (EPISODE_SCHEMA, METRICS_VERSION, OBSTACLE_MOVED_M, SETTLE_TICKS, SUMMARY_SCHEMA, build_summary,
                             compare_runs, episode_metrics, group_stats, policy_spec, summarize, wilson)

ROOT = Path(__file__).resolve().parents[1]
HAS_PIL = importlib.util.find_spec('PIL') is not None


@dataclass
class Case:
    part: str = 'thick'


def outcome(contacted=('thick',), held=('thick',), lifted=True, obstacle_frames=0, match=150):
    return dict(contacted_parts=list(contacted), held_parts=list(held), lifted=lifted,
                finger_obstacle_frames=obstacle_frames, first_match_sample_index=match if held else None,
                max_lift_m=.15 if lifted else 0.)


def rows(ticks=40, obstacle_shift=0., target_shift_at=None, contact_from=30):
    result = []
    for tick in range(ticks):
        obstacle = [.1, .2, .03]
        if tick >= SETTLE_TICKS+5:
            obstacle = [.1+obstacle_shift, .2, .03]
        target = [0., 0., .02, 1., 0., 0., 0.]
        if target_shift_at is not None and tick >= target_shift_at:
            target = [.003, 0., .02, 1., 0., 0., 0.]
        result.append(dict(tick=tick, action=[0.]*6+[-1.], obstacle_position_m=obstacle, target_pose=target,
                           finger_target_contacts=int(tick >= contact_from)))
    return result


def record(split='test_unseen', part='thick', phrasing='seen', **metrics):
    base = dict(success=False, part_hit=False, wrong_part_contacted=False, lifted=False, collision=False,
                target_disturbed_before_contact=False, any_part_contacted=False, time_to_success_s=None,
                failure_reasons=[], obstacle_max_displacement_m=0., metrics_version=METRICS_VERSION)
    base.update(metrics)
    return dict(case=dict(case_id=f'{split}-{part}-{phrasing}', split=split, family='box_step', part=part,
                          scene=dict(scene_id=f'{split}-x'), instruction=dict(phrasing=phrasing, en='e', zh='z')),
                metrics=base, error=None, outcome=dict(held_parts=[], contacted_parts=[], max_lift_m=0.),
                keyframes=[], policy=dict(policy_id='teacher', privileged=True, is_vla=False), reference=None)


class MetricTests(unittest.TestCase):
    def test_wilson_interval(self):
        self.assertEqual(wilson(0, 0), (None, None))
        low, high = wilson(64, 64)
        self.assertAlmostEqual(high, 1.)
        self.assertAlmostEqual(low, 0.94339, places=4)
        low, high = wilson(0, 64)
        self.assertEqual(low, 0.)
        self.assertAlmostEqual(high, 1-0.94339, places=4)
        low, high = wilson(5, 10)
        self.assertAlmostEqual(low, 0.23659, places=4)
        self.assertAlmostEqual(high, 0.76341, places=4)

    def test_requested_part_held_is_success(self):
        metrics = episode_metrics(Case(), outcome(), rows())
        self.assertTrue(metrics['success'] and metrics['part_hit'] and not metrics['collision'])
        self.assertEqual(metrics['failure_reasons'], [])
        self.assertEqual(metrics['time_to_success_s'], 7.55)
        self.assertEqual(metrics['first_finger_target_contact_tick'], 30)

    def test_other_part_is_a_part_miss_even_when_lifted(self):
        metrics = episode_metrics(Case('thin'), outcome(), rows())
        self.assertFalse(metrics['success'] or metrics['part_hit'])
        self.assertTrue(metrics['wrong_part_contacted'] and metrics['lifted'])
        self.assertEqual(metrics['failure_reasons'], ['requested_part_not_contacted', 'other_part_contacted',
                                                      'requested_part_not_held', 'other_part_held'])

    def test_obstacle_push_is_a_collision_measured_from_rested_pose(self):
        metrics = episode_metrics(Case(), outcome(), rows(obstacle_shift=OBSTACLE_MOVED_M))
        self.assertTrue(metrics['collision'])
        self.assertFalse(metrics['success'])
        self.assertIn('obstacle_moved', metrics['failure_reasons'])
        settle_only = rows()
        for row in settle_only[:SETTLE_TICKS-1]:
            row['obstacle_position_m'] = [.1, .2, .031]      # the initial drop before the rested tick
        self.assertFalse(episode_metrics(Case(), outcome(), settle_only)['collision'])

    def test_finger_obstacle_contact_is_a_collision(self):
        metrics = episode_metrics(Case(), outcome(obstacle_frames=3), rows())
        self.assertTrue(metrics['collision'])
        self.assertFalse(metrics['success'])

    def test_pre_contact_disturbance_is_diagnostic_only(self):
        metrics = episode_metrics(Case(), outcome(), rows(target_shift_at=20))
        self.assertTrue(metrics['target_disturbed_before_contact'])
        self.assertTrue(metrics['success'])
        later = episode_metrics(Case(), outcome(), rows(target_shift_at=35))     # after first contact
        self.assertFalse(later['target_disturbed_before_contact'])
        # Contact is sampled at the start of tick 30; the touch itself happened during tick 29.
        touching = episode_metrics(Case(), outcome(), rows(target_shift_at=29))
        self.assertFalse(touching['target_disturbed_before_contact'])
        self.assertTrue(episode_metrics(Case(), outcome(), rows(target_shift_at=28))['target_disturbed_before_contact'])

    def test_policy_error_fails_with_reason(self):
        metrics = episode_metrics(Case(), outcome(contacted=(), held=(), lifted=False), rows(ticks=5),
                                  dict(kind='policy_error', tick=5, message='x'))
        self.assertFalse(metrics['success'])
        self.assertEqual(metrics['failure_reasons'][0], 'policy_error')

    def test_group_rates_and_part_accuracy(self):
        group = [record(success=True, part_hit=True, any_part_contacted=True, lifted=True, time_to_success_s=8.),
                 record(part_hit=False, wrong_part_contacted=True, any_part_contacted=True, lifted=True,
                        failure_reasons=['other_part_held']),
                 record(collision=True, failure_reasons=['finger_obstacle_contact'])]
        stats = group_stats(group)
        self.assertEqual(stats['episodes'], 3)
        self.assertEqual((stats['success']['count'], stats['collision']['count'], stats['lifted']['count']), (1, 1, 2))
        self.assertEqual((stats['part_accuracy_given_contact']['count'], stats['part_accuracy_given_contact']['of']),
                         (1, 2))
        self.assertEqual(stats['mean_time_to_success_s'], 8.)
        self.assertEqual(stats['failure_reasons'], {'finger_obstacle_contact': 1, 'other_part_held': 1})
        summary = summarize(group+[record(split='test_instance', phrasing='held_out')])
        self.assertEqual(set(summary['by_split']), {'test_instance', 'test_unseen'})
        self.assertIn('test_instance/held_out', summary['by_split_phrasing'])

    def test_policy_spec(self):
        self.assertEqual(policy_spec('teacher'), dict(kind='teacher'))
        self.assertEqual(policy_spec('adapter:mock')['adapter'], 'mock')
        for bad in ('', 'adapter:', 'random'):
            with self.assertRaises(ValueError):
                policy_spec(bad)

    def test_worker_budget_respects_memory(self):
        sys.path.insert(0, str(ROOT/'scripts'))
        try:
            from run_p5_eval import worker_budget
        finally:
            sys.path.pop(0)
        self.assertEqual(worker_budget(2, None), 2)
        self.assertEqual(worker_budget(4, 7.0), 2)
        self.assertEqual(worker_budget(2, 2.0), 1)


class SummaryFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_episode(self, subdir, case_id, payload, trace=b'{"tick":0}\n'):
        directory = self.root/subdir/case_id
        (directory/'keyframes').mkdir(parents=True)
        (directory/'result.json').write_text(json.dumps(payload))
        (directory/'trace.jsonl').write_bytes(trace)
        (directory/'keyframes'/'0000_agentview.png').write_bytes(b'png')
        return directory

    def test_summary_counts_missing_and_controls_separately(self):
        config = dict(case_ids=['a', 'b', 'c'], policy=dict(policy_id='teacher'), policy_spec=dict(kind='teacher'),
                      dataset='d', plan_sha256='p', splits=['test_unseen', 'control'], language='en', horizon=222,
                      execute_steps=0)
        (self.root/'eval_config.json').write_text(json.dumps(config))
        good = dict(record(success=True), schema=EPISODE_SCHEMA)
        good['case']['case_id'] = 'a'
        control = dict(record(split='control', collision=True), schema=EPISODE_SCHEMA)
        control['case']['case_id'] = 'b'
        self.write_episode('episodes', 'a', good)
        self.write_episode('episodes', 'b', control)
        summary = build_summary(self.root)
        self.assertEqual((summary['schema'], summary['status']), (SUMMARY_SCHEMA, 'incomplete'))
        self.assertEqual(summary['missing'], ['c'])
        self.assertEqual(summary['results']['overall']['episodes'], 1)
        self.assertEqual(summary['controls']['collision']['count'], 1)
        self.assertFalse(summary['is_vla'])
        self.assertFalse(summary['harness_checks']['all_planned_finished'])

    def test_older_metrics_are_recomputed_from_the_trace(self):
        config = dict(case_ids=['a'], policy=dict(policy_id='teacher'), policy_spec=dict(kind='teacher'), dataset='d',
                      plan_sha256='p', splits=['test_unseen'], language='en', horizon=40, execute_steps=0)
        (self.root/'eval_config.json').write_text(json.dumps(config))
        old = dict(record(success=False), schema=EPISODE_SCHEMA)
        old['metrics']['metrics_version'] = 0
        old['outcome'] = outcome()
        old['case']['case_id'] = 'a'
        trace = b''.join(json.dumps(row).encode()+b'\n' for row in rows())
        self.write_episode('episodes', 'a', old, trace=trace)
        run = build_summary(self.root)['runs'][0]
        self.assertTrue(run['metrics']['success'] and run['metrics']['recomputed_from_files'])
        self.assertEqual(run['metrics']['metrics_version'], METRICS_VERSION)

    def test_compare_runs_detects_any_byte_change(self):
        payload = dict(record(), schema=EPISODE_SCHEMA)
        first = self.write_episode('episodes', 'a', payload)
        second = self.write_episode('recheck', 'a', payload)
        self.assertTrue(compare_runs(first, second)['identical'])
        (second/'keyframes'/'0000_agentview.png').write_bytes(b'PNG')
        self.assertEqual(compare_runs(first, second)['differing'], ['keyframes/0000_agentview.png'])


@unittest.skipUnless(HAS_PIL, 'bridge test needs Pillow (backend venv)')
class BridgeTests(unittest.TestCase):
    def test_bridge_observation_is_accepted_by_mock_adapter(self):
        from robolab.p5_eval import bridge_observation
        from robolab.policy import MockPolicy, validate_actions

        observation = dict(tick=3, time_s=.15, instruction='Pick up the object by the thin end.', language='en',
                           images={name: np.zeros((224, 224, 3), np.uint8) for name in ('agentview', 'robot0_eye_in_hand')},
                           state=np.arange(8.), joints=np.zeros(7), calibration={}, action_space='osc')
        bridged = bridge_observation(observation, 7)
        self.assertEqual((bridged['observation_id'], bridged['episode_tick'], len(bridged['proprio'])), (7, 3, 8))
        self.assertEqual(bridged['state_layout'], 'p4_training_view_v1')
        json.dumps(bridged, allow_nan=False)
        result = MockPolicy().predict(bridged)
        self.assertEqual(len(validate_actions(result['actions'])), 4)
        self.assertTrue(result['is_mock'])


if __name__ == '__main__':
    unittest.main()
