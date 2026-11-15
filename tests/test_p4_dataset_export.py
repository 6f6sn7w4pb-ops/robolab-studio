"""Label aggregation, statistics and loader contracts of the P4 training view."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np

from robolab.p4_dataset_export import (EXPORT_SCHEMA, LABEL_FORCE_N, PART_IDS, ROLE_IDS, finger_labels,
                                       iter_policy_frames, norm_stats, raw_contact_rows)


def contact(finger='finger_1', role='finger_target', part='thick', force=1., position=(0., 0., 0.),
            normal=(1., 0., 0.), region='pad', load=True):
    other = f'p4_stepped_bar_{part}' if role == 'finger_target' else 'p4_distractor_box'
    return dict(finger=finger, pair_role=role, other_geom_name=other, normal_force_N=force, load_bearing=load,
                constraint_active=load, surface_region=region, position_gripper_m=list(position),
                normal_gripper_finger_to_other=list(normal), force_on_other_gripper_N=[force, 0., 0.])


class FingerLabelTests(unittest.TestCase):
    def test_empty_frame_has_no_fabricated_contact(self):
        label = finger_labels([])
        self.assertFalse(label['present'].any())
        self.assertTrue(np.isnan(label['position_gripper_m']).all())
        self.assertTrue(np.isnan(label['normal_gripper']).all())
        np.testing.assert_array_equal(label['normal_force_N'], [0., 0.])
        np.testing.assert_array_equal(label['part'], [-1, -1])

    def test_force_weighted_position_and_renormalized_normal(self):
        label = finger_labels([
            contact(force=1., position=(0., 0., 0.), normal=(1., 0., 0.)),
            contact(force=3., position=(.04, 0., 0.), normal=(0., 1., 0.)),
        ])
        np.testing.assert_allclose(label['position_gripper_m'][0], [.03, 0., 0.])
        np.testing.assert_allclose(label['normal_gripper'][0], np.array([1., 3., 0.])/np.sqrt(10.))
        self.assertEqual(label['normal_force_N'][0], 4.)
        self.assertEqual(label['part'][0], PART_IDS['thick'])
        self.assertFalse(label['present'][1])

    def test_weak_and_inactive_contacts_are_ignored(self):
        label = finger_labels([contact(force=LABEL_FORCE_N/2), contact(finger='finger_2', load=False)])
        self.assertFalse(label['present'].any())

    def test_mixed_parts_and_obstacles(self):
        label = finger_labels([contact(part='thick'), contact(part='thin'),
                               contact(finger='finger_2', role='finger_environment'),
                               contact(finger='finger_2', part='thin', region='other_finger')])
        self.assertEqual(label['part'][0], 2)
        self.assertEqual(label['part'][1], PART_IDS['thin'])
        self.assertFalse(label['pad'][1])
        np.testing.assert_array_equal(label['obstacle_contact'], [False, True])

    def test_raw_rows_keep_every_contact_with_ids(self):
        rows = list(raw_contact_rows([contact(part='thin', force=.01), contact(role='finger_distractor')]))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['other_part'], PART_IDS['thin'])
        self.assertEqual(rows[1]['other_part'], -1)
        self.assertEqual(rows[1]['role'], ROLE_IDS['finger_distractor'])


class StatsTests(unittest.TestCase):
    def test_quantiles_and_moments(self):
        values = np.arange(101, dtype=float)[:, None]*[1., -2.]
        stats = norm_stats(values)
        np.testing.assert_allclose(stats['q01'], [1., -198.])   # second column spans -200..0 in steps of 2
        np.testing.assert_allclose(stats['q99'], [99., -2.])
        np.testing.assert_allclose(stats['mean'], [50., -100.])
        self.assertEqual(stats['count'], 101)


@unittest.skipUnless(importlib.util.find_spec('h5py'), 'h5py is not installed')
class LoaderTests(unittest.TestCase):
    def test_policy_frames_and_image_conventions(self):
        import h5py
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'view.hdf5'
            image = np.zeros((2, 224, 224, 3), np.uint8)
            image[:, :, 0] = 255           # left column red in canonical orientation
            with h5py.File(path, 'w') as handle:
                handle.attrs['schema'] = EXPORT_SCHEMA
                demo = handle.create_group('data/demo_0')
                demo['obs/agentview_rgb'] = image
                demo['obs/eye_in_hand_rgb'] = image
                demo['obs/state'] = np.ones((2, 8))
                demo['actions'] = np.full((2, 7), .5)
                demo.attrs.update(case_id='c', instruction_en='Pick up the object by the thin end.',
                                  instruction_zh='抓住物体的细端并抬起。')
            canonical = list(iter_policy_frames(path))
            rotated = list(iter_policy_frames(path, language='zh', image_convention='libero_rotated_180'))
            self.assertEqual(len(canonical), 2)
            self.assertEqual(canonical[0]['state'].dtype, np.float32)
            self.assertEqual(canonical[0]['actions'].shape, (7,))
            self.assertEqual(canonical[0]['image'][0, 0, 0], 255)
            self.assertEqual(rotated[0]['image'][0, -1, 0], 255)
            self.assertEqual(rotated[1]['task'], '抓住物体的细端并抬起。')
            with self.assertRaises(ValueError):
                next(iter_policy_frames(path, image_convention='bgr'))


if __name__ == '__main__':
    unittest.main()
