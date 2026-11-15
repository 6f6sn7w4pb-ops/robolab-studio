"""Plan, split and leakage contracts for the first P4 OSC dataset (NumPy only, no simulator)."""
from dataclasses import replace
import math
import unittest

from robolab.p4_dataset import (DATASET_SEED, HELD_OUT_PROMPTS, SEEN_PROMPTS, audit_plan, obstacle_clearance,
                                plan_document, plan_pilot, plan_v1, rests_stably, uniform)
from robolab.p4_dataset_build import _quadrant
from robolab.p4_parts import SteppedBar


class PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document, cls.cases, cls.controls = plan_document('v1')

    def test_plan_is_deterministic_and_seeded(self):
        again, _, _ = plan_document('v1')
        self.assertEqual(again['plan_sha256'], self.document['plan_sha256'])
        other, cases, _ = plan_document('v1', seed=DATASET_SEED+1)
        self.assertNotEqual(other['plan_sha256'], self.document['plan_sha256'])
        self.assertEqual(len(cases), 200)

    def test_uniform_is_bounded_and_independent_of_other_fields(self):
        values = [uniform(7, 'scene', 'x', -1., 2., attempt) for attempt in range(500)]
        self.assertTrue(all(-1. <= value < 2. for value in values))
        self.assertEqual(uniform(7, 'scene', 'x', 0., 1.), uniform(7, 'scene', 'x', 0., 1.))
        self.assertNotEqual(uniform(7, 'scene', 'x', 0., 1.), uniform(7, 'scene', 'y', 0., 1.))

    def test_first_version_counts_and_family_hold_out(self):
        audit = self.document['audit']
        self.assertEqual((audit['scenes'], audit['episodes'], audit['controls']), (100, 200, 4))
        self.assertEqual(audit['episodes_by_split'],
                         {'train': 120, 'validation': 16, 'test_instance': 24, 'test_unseen': 40})
        self.assertEqual(audit['families_by_split']['test_unseen'], ['ellipsoid_step'])
        for split in ('train', 'validation', 'test_instance'):
            self.assertEqual(audit['families_by_split'][split], ['box_step', 'cylinder_step'])

    def test_requested_part_direction_is_not_a_shortcut(self):
        """Each part must point into every 90-degree sector in training, so image side cannot stand in for the part."""
        for part in ('thick', 'thin'):
            sectors = {_quadrant(case.scene.yaw_deg, case.part) for case in self.cases
                       if case.split == 'train' and case.part == part}
            self.assertEqual(sectors, {0, 1, 2, 3})

    def test_prompts_pair_thick_and_thin_with_the_same_template(self):
        pools = {kind: {part: [pair for pair in pool[part]] for part in pool}
                 for kind, pool in (('seen', SEEN_PROMPTS), ('held_out', HELD_OUT_PROMPTS))}
        by_scene = {}
        for case in self.cases:
            by_scene.setdefault(case.scene.scene_id, {})[case.part] = case
        for pair in by_scene.values():
            thick, thin = pair['thick'], pair['thin']
            pool = pools[thick.phrasing]
            self.assertEqual(pool['thick'].index((thick.instruction_en, thick.instruction_zh)),
                             pool['thin'].index((thin.instruction_en, thin.instruction_zh)))

    def test_obstacles_clear_the_bar_and_finger_sweep_except_controls(self):
        for case in self.cases[::2]:
            self.assertGreaterEqual(obstacle_clearance(case.scene), .005, case.case_id)
        for control in self.controls:
            self.assertLess(obstacle_clearance(control.scene), 0, control.case_id)
            self.assertEqual((control.split, control.expected), ('control', 'blocked_failure'))

    def test_dimensions_stay_inside_the_scene_envelope(self):
        for case in self.cases[::2]:
            bar = case.scene.bar
            self.assertTrue(.06 <= bar.segment_length_m <= .09)
            self.assertTrue(.026 <= bar.thin_width_m < bar.thick_width_m <= .064)
            self.assertGreaterEqual(bar.thick_width_m-bar.thin_width_m, .014-1e-9)
            self.assertTrue(.032 <= bar.thin_height_m < bar.thick_height_m <= .055)
            self.assertTrue(0 <= case.scene.yaw_deg < 360)

    def test_ellipsoid_lobes_rest_in_stable_equilibrium(self):
        """Taller-than-wide ellipsoid lobes balance on their crown and roll over (first attempt)."""
        ellipsoids = [case.scene for case in self.cases+self.controls if case.scene.family == 'ellipsoid_step']
        self.assertEqual(len(ellipsoids), 41)
        for scene in ellipsoids:
            self.assertTrue(rests_stably(scene.family, scene.bar), scene.scene_id)
            self.assertLessEqual(scene.bar.thick_height_m/scene.bar.thick_width_m, .92+1e-3)
            self.assertLessEqual(scene.bar.thin_height_m/scene.bar.thin_width_m, .92+1e-3)
        tall = SteppedBar(thick_width_m=.0497, thick_height_m=.0539, thin_width_m=.0338, thin_height_m=.0442)
        self.assertFalse(rests_stably('ellipsoid_step', tall))
        self.assertTrue(rests_stably('box_step', tall))

    def test_pilot_plan_reuses_the_p4b_scenes_and_prompts(self):
        cases, controls = plan_pilot()
        self.assertEqual(len(cases), 24)
        held = [case for case in cases if case.phrasing == 'held_out']
        self.assertEqual(len(held), 4)
        self.assertTrue(all(case.split == 'test_unseen' for case in held))
        self.assertEqual(controls[0].case_id, 'blocked_approach_thin')
        audit_plan(cases, controls)


class LeakageAuditTests(unittest.TestCase):
    def setUp(self):
        self.cases, self.controls = plan_v1()

    def _swap(self, predicate, **changes):
        cases = list(self.cases)
        for index, case in enumerate(cases):
            if predicate(case):
                scene_changes = changes.get('scene', {})
                cases[index] = replace(case, scene=replace(case.scene, **scene_changes),
                                       **{k: v for k, v in changes.items() if k != 'scene'})
        return cases

    def test_held_out_family_in_training_is_rejected(self):
        scene_id = next(case.scene.scene_id for case in self.cases if case.split == 'test_unseen')
        cases = self._swap(lambda case: case.scene.scene_id == scene_id, scene={'split': 'train'})
        with self.assertRaisesRegex(ValueError, 'family'):
            audit_plan(cases, self.controls)

    def test_held_out_phrase_in_development_is_rejected(self):
        scene_id = next(case.scene.scene_id for case in self.cases if case.split == 'train')
        en, zh = HELD_OUT_PROMPTS['thick'][0]
        cases = self._swap(lambda case: case.scene.scene_id == scene_id and case.part == 'thick',
                           instruction_en=en, instruction_zh=zh)
        with self.assertRaisesRegex(ValueError, 'phrasing'):
            audit_plan(cases, self.controls)

    def test_duplicate_shape_instance_is_rejected(self):
        first = next(case.scene for case in self.cases if case.split == 'train')
        other = next(case.scene.scene_id for case in self.cases if case.split == 'validation'
                     and case.scene.family == first.family)
        cases = self._swap(lambda case: case.scene.scene_id == other, scene={'bar': first.bar})
        with self.assertRaisesRegex(ValueError, 'shape instance'):
            audit_plan(cases, self.controls)

    def test_missing_pair_and_ambiguous_prompt_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'paired'):
            audit_plan(self.cases[1:], self.controls)
        en, zh = SEEN_PROMPTS['thin'][0]
        scene_id = self.cases[0].scene.scene_id
        cases = self._swap(lambda case: case.scene.scene_id == scene_id and case.part == 'thick',
                           instruction_en=en, instruction_zh=zh)
        with self.assertRaisesRegex(ValueError, 'both parts'):
            audit_plan(cases, self.controls)

    def test_control_cannot_be_indexed(self):
        control = self.controls[0]
        indexed = replace(control, expected='success', scene=replace(control.scene, split='train'))
        with self.assertRaises(ValueError):
            audit_plan(self.cases+[indexed], self.controls)


class SectorTests(unittest.TestCase):
    def test_sector_follows_part_direction(self):
        self.assertEqual(_quadrant(0., 'thin'), 0)
        self.assertEqual(_quadrant(0., 'thick'), 2)
        self.assertEqual(_quadrant(271., 'thin'), 3)
        self.assertEqual(_quadrant(271., 'thick'), 1)
        self.assertTrue(math.isclose(uniform(1, 'a', 'b', 0., 0.), 0.))


if __name__ == '__main__':
    unittest.main()
