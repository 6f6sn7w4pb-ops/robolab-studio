"""P5-B scene sets, evaluation variants and run comparison (NumPy only; no simulator)."""
import colorsys
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from robolab.p4_dataset import plan_v1, shape_id
from robolab.p4_parts import TARGET
from robolab.p5_compare import compare, episodes_csv, load_run, markdown, mcnemar_exact, parse_run_argument
from robolab.p5_eval import SUMMARY_SCHEMA, conditions
from robolab.p5_scenes import EXTRA_SCENES, extra_cases, scene_set_cases
from robolab.p5_variation import (ANTI_ROLL_PAIR, APPEARANCE_FACTORS, anti_roll_xml, appearance_params,
                                  parse_appearance)

HAS_MATPLOTLIB = importlib.util.find_spec('matplotlib') is not None


class Scene:
    def __init__(self, family):
        self.family = family


MINIMAL_XML = (f"<mujoco><worldbody><body name='floor'><geom name='floor' type='plane' size='1 1 .1'/></body>"
               f"<body name='{TARGET}'><freejoint/><geom name='{TARGET}_thick' type='box' size='.1 .1 .1'/>"
               f"<geom name='{TARGET}_thin' type='box' size='.1 .1 .1'/></body></worldbody></mujoco>")


class SceneSetTests(unittest.TestCase):
    def test_extended_set_is_audited_against_the_whole_dataset_plan(self):
        cases, controls = plan_v1()
        everything, kept_controls, info = scene_set_cases('extended', cases, controls)
        extra = everything[len(cases):]
        self.assertEqual(len(extra), 2*sum(EXTRA_SCENES.values()))
        self.assertEqual(info['audit']['episodes_by_split'], dict(train=120, validation=16, test_instance=120,
                                                                  test_unseen=120))
        self.assertEqual(kept_controls, controls)
        self.assertTrue(all('-x' in case.scene.scene_id for case in extra))
        self.assertFalse({shape_id(case.scene) for case in extra} & {shape_id(case.scene) for case in cases})
        held = [case for case in extra if case.split == 'test_unseen' and case.phrasing == 'held_out']
        self.assertEqual(len(held), EXTRA_SCENES[('ellipsoid_step', 'test_unseen')])
        self.assertEqual([case.case_id for case in extra_cases()], [case.case_id for case in extra])   # deterministic

    def test_dataset_set_is_unchanged(self):
        cases, controls = plan_v1()
        same, _, info = scene_set_cases('dataset', cases, controls)
        self.assertEqual(same, cases)
        self.assertEqual(info['extra_cases'], 0)
        with self.assertRaises(ValueError):
            scene_set_cases('bigger', cases, controls)


class VariantTests(unittest.TestCase):
    def test_anti_roll_only_touches_cylinder_target_floor_contacts(self):
        self.assertEqual(anti_roll_xml(Scene('box_step'), MINIMAL_XML), MINIMAL_XML)
        root = ET.fromstring(anti_roll_xml(Scene('cylinder_step'), MINIMAL_XML))
        contact = root.find('contact')
        self.assertEqual(contact.find('exclude').attrib, dict(body1=TARGET, body2='floor'))
        pairs = contact.findall('pair')
        self.assertEqual(sorted(pair.get('geom1') for pair in pairs), [f'{TARGET}_thick', f'{TARGET}_thin'])
        self.assertTrue(all(pair.get('geom2') == 'floor' and pair.get('condim') == '6' for pair in pairs))
        self.assertEqual(pairs[0].get('friction').split()[:3], ['1', '1', '0.02'])
        self.assertEqual(ANTI_ROLL_PAIR['solref'], '0.02 1')
        with self.assertRaises(ValueError):
            anti_roll_xml(Scene('cylinder_step'), '<mujoco><worldbody/></mujoco>')

    def test_appearance_parsing(self):
        self.assertEqual(parse_appearance(''), ())
        self.assertEqual(parse_appearance('none'), ())
        self.assertEqual(parse_appearance('all'), APPEARANCE_FACTORS)
        self.assertEqual(parse_appearance('objects,lighting'), ('lighting', 'objects'))
        with self.assertRaises(ValueError):
            parse_appearance('lighting,fog')

    def test_appearance_is_seeded_per_scene_and_keeps_objects_distinct(self):
        first = appearance_params('box_step-test_instance-000', APPEARANCE_FACTORS)
        self.assertEqual(first, appearance_params('box_step-test_instance-000', APPEARANCE_FACTORS))
        self.assertNotEqual(first, appearance_params('box_step-test_instance-001', APPEARANCE_FACTORS))
        self.assertEqual(set(appearance_params('s', ('lighting',))), {'lighting'})
        self.assertEqual(appearance_params('s', ()), {})
        json.dumps(first, allow_nan=False)
        for index in range(200):
            objects = appearance_params(f'scene-{index}', ('objects',))['objects']
            hues = [colorsys.rgb_to_hsv(*objects[key][:3])[0] for key in ('target_rgba', 'obstacle_rgba')]
            gap = abs(hues[0]-hues[1]) % 1.
            self.assertGreaterEqual(min(gap, 1-gap), 1/6-.01)
            light = appearance_params(f'scene-{index}', ('lighting',))['lighting']['lights'][0]
            self.assertTrue(.55 <= light['intensity'] <= 1.15 and 3. <= light['height_m'] <= 5.)

    def test_old_configs_read_as_nominal_conditions(self):
        self.assertEqual(conditions({}), dict(scene_set='dataset', anti_roll=False, appearance=[], appearance_seed=None))


def summary(label_rates, *, ids=None, status='finalized', conditions_value=None):
    ids = ids or [f'case_{i}' for i in range(len(label_rates))]
    runs = []
    for case_id, (success, part_hit) in zip(ids, label_rates):
        split = 'test_unseen' if case_id.endswith(('0', '2', '4', '6', '8')) else 'test_instance'
        runs.append(dict(case_id=case_id, split=split, family='box_step', part='thick', phrasing='seen',
                         instruction=dict(en='e', zh='z'), error=None,
                         metrics=dict(success=success, part_hit=part_hit, wrong_part_contacted=False, lifted=success,
                                      collision=False, target_disturbed_before_contact=False, any_part_contacted=part_hit,
                                      time_to_success_s=8. if success else None,
                                      failure_reasons=[] if success else ['not_lifted'])))
    return dict(schema=SUMMARY_SCHEMA, status=status, policy=dict(policy_id='teacher'), is_vla=False,
                finished=len(runs), planned=len(runs), harness_checks={}, conditions=conditions_value,
                plan_sha256='p', horizon=222, language='en', execute_steps=0, runs=runs)


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, value):
        directory = self.root/name
        directory.mkdir()
        (directory/'summary.json').write_text(json.dumps(value))
        return directory

    def test_mcnemar_exact(self):
        self.assertEqual(mcnemar_exact(0, 0), 1.)
        self.assertAlmostEqual(mcnemar_exact(6, 0), 2/64)
        self.assertAlmostEqual(mcnemar_exact(5, 1), 2*7/64)
        self.assertEqual(mcnemar_exact(3, 3), 1.)

    def test_runs_are_compared_on_shared_episodes(self):
        a = self.write('a', summary([(True, True)]*6+[(False, True)]*2))
        b = self.write('b', summary([(True, True)]*2+[(False, False)]*4+[(False, True)]*3,
                                    conditions_value=dict(scene_set='extended', anti_roll=True, appearance=['lighting'])))
        runs = [load_run(a), load_run(b/'summary.json', 'bee')]
        result = compare(runs)
        self.assertEqual(result['shared_episodes'], 8)
        self.assertTrue(any('outside the shared set' in text for text in result['warnings']))
        overall = [run['shared']['overall'] for run in result['runs']]
        self.assertEqual((overall[0]['success']['count'], overall[1]['success']['count']), (6, 2))
        success = result['pairs'][0]['tests'][0]
        self.assertEqual((success['both'], success['only_first'], success['only_second'], success['neither']), (2, 4, 0, 2))
        self.assertAlmostEqual(success['rate_difference'], .5)
        self.assertEqual(result['runs'][1]['conditions_text'], 'extended, anti-roll, appearance:lighting')
        text = markdown(result)
        self.assertIn('McNemar', text)
        self.assertIn('| bee |', text)
        rows = episodes_csv(result, runs).splitlines()
        self.assertEqual(len(rows), 9)
        self.assertTrue(rows[1].startswith('case_0,test_unseen,box_step,thick,seen,1,1,1,0,1,1,1,0'))

    def test_rejects_mismatched_or_disjoint_runs(self):
        a = load_run(self.write('a', summary([(True, True)]*2)))
        with self.assertRaises(ValueError):
            compare([a])
        with self.assertRaises(ValueError):
            compare([a, load_run(self.write('b', summary([(True, True)]*2, ids=['x1', 'x3'])))])
        changed = summary([(True, True)]*2)
        changed['runs'][0]['part'] = 'thin'
        with self.assertRaises(ValueError):
            compare([a, load_run(self.write('c', changed))])
        with self.assertRaises(ValueError):
            load_run(self.write('d', dict(schema='other')))

    def test_run_argument_labels(self):
        self.assertEqual(parse_run_argument('teacher=outputs/x'), ('teacher', 'outputs/x'))
        self.assertEqual(parse_run_argument('outputs/x'), (None, 'outputs/x'))

    @unittest.skipUnless(HAS_MATPLOTLIB, 'figures need matplotlib (backend venv)')
    def test_written_report_has_figures(self):
        from robolab.p5_compare import write_comparison
        runs = [load_run(self.write('a', summary([(True, True)]*4))), load_run(self.write('b', summary([(False, True)]*4)))]
        result = write_comparison(runs, self.root/'out')
        self.assertEqual(result['figures'], ['figures/rates_overall.png', 'figures/success_by_split.png'])
        for name in ('comparison.json', 'comparison.md', 'episodes.csv', *result['figures']):
            self.assertTrue((self.root/'out'/name).stat().st_size > 0)


if __name__ == '__main__':
    unittest.main()
