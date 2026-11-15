"""Read-only desktop view of a P5 evaluation summary; uses the GUI venv and a temporary language file."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as error:
    if error.name != 'PySide6':
        raise
    raise unittest.SkipTest('Run desktop view tests with the separate GUI venv.') from error

from robolab.i18n import LanguageManager
from robolab.p5_eval_view import SUMMARY_SCHEMA, EvalSummary, rate_text, read_eval_summary


def stat(count, total):
    rate = count/total
    return dict(count=count, rate=rate, ci95=[max(0., rate-.1), min(1., rate+.1)])


def group(total, success):
    return dict(episodes=total, success=stat(success, total), part_hit=stat(success, total), lifted=stat(total, total),
                collision=stat(0, total), part_accuracy_given_contact=dict(stat(success, total), of=total))


def run(index, split='test_unseen', success=True, frames=2):
    keyframes = [dict(tick=20*i, actual_part='thick' if i else None, lift_m=.01*i,
                      files={camera: f'keyframes/{20*i:04d}_{camera}.png' for camera in ('agentview', 'robot0_eye_in_hand')})
                 for i in range(frames)]
    return dict(path=f'episodes/case_{index}', case_id=f'case_{index}', scene_id=f'scene_{index}', split=split,
                family='box_step', part='thick', phrasing='held_out' if index % 2 else 'seen',
                instruction=dict(en='Pick up the object by the thick end.', zh='抓住物体的粗端并抬起。'),
                metrics=dict(success=success, part_hit=success, collision=not success, obstacle_max_displacement_m=0.,
                             failure_reasons=[] if success else ['finger_obstacle_contact']),
                outcome=dict(held_parts=['thick'] if success else [], contacted_parts=[], max_lift_m=.16),
                error=None, keyframes=keyframes, reference_identical=True)


class EvalViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ)
        self.env.start()
        os.environ.pop('ROBOLAB_UI_LANGUAGE', None)
        self.app._robolab_language = LanguageManager(Path(self.tmp.name)/'ui.ini')
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.app.removeTranslator(self.app._robolab_language._qt_translator)
        del self.app._robolab_language
        self.env.stop()
        self.tmp.cleanup()

    def write(self, runs, **extra):
        for item in runs:
            for frame in item['keyframes']:
                for name in frame['files'].values():
                    path = self.root/item['path']/name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    image = QImage(32, 32, QImage.Format.Format_RGB888)
                    image.fill(0x804020)
                    image.save(str(path))
        data = dict(schema=SUMMARY_SCHEMA, status='finalized', policy=dict(policy_id='teacher', privileged=True),
                    is_vla=False, finished=len(runs), planned=len(runs),
                    harness_checks=dict(all_planned_finished=True, recheck_identical=True, reference_identical=True),
                    results=dict(overall=group(3, 2), by_split=dict(test_unseen=group(3, 2)),
                                 by_split_phrasing={'test_unseen/seen': group(2, 1), 'test_unseen/held_out': group(1, 1)}),
                    controls=group(1, 0), runs=runs)
        data.update(extra)
        path = self.root/'summary.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def test_summary_groups_rows_keyframes_and_language_switch(self):
        runs = [run(0), run(1), run(2, success=False), run(3, split='control', success=False, frames=0)]
        dialog = EvalSummary(self.write(runs))
        self.assertIn('teacher', dialog.status.text())
        self.assertIn('复跑一致 通过', dialog.status.text())
        self.assertIn('数据集测试场景 · 圆柱防滚 关 · 外观变化 无', dialog.scene_line.text())     # pre-P5-B summary
        self.assertEqual(dialog.groups.topLevelItemCount(), 5)      # all, split, two phrasings, controls
        self.assertTrue(dialog.groups.topLevelItem(0).text(5).startswith('0/3'))      # zero collisions keep 0/n
        self.assertEqual(dialog.table.topLevelItemCount(), 4)
        self.assertEqual(dialog.slider.maximum(), 1)
        self.assertFalse(dialog.views['agentview'].pixmap.isNull())
        self.assertIn('第 20 步', dialog.frame_label.text())
        dialog.slider.setValue(0)
        self.assertIn('第 0 步', dialog.frame_label.text())
        dialog.table.setCurrentItem(dialog.table.topLevelItem(2))
        self.assertIn('未通过原因', dialog.detail.text())
        dialog.table.setCurrentItem(dialog.table.topLevelItem(3))
        self.assertTrue(dialog.views['agentview'].pixmap.isNull())
        self.app._robolab_language.set_language('en')
        self.assertEqual(dialog.windowTitle(), 'P5 closed-loop evaluation')
        self.assertIn('Re-run identical', dialog.status.text())
        self.assertIn('Dataset test scenes', dialog.scene_line.text())
        dialog.close()

    def test_scene_conditions_line(self):
        conditions = dict(scene_set='extended', anti_roll=True, appearance=['lighting', 'objects'], appearance_seed=1)
        dialog = EvalSummary(self.write([run(0)], conditions=conditions,
                                        harness_checks=dict(reference_physics_identical=True)))
        self.assertIn('扩展测试场景 · 圆柱防滚 开 · 外观变化 光照 + 物体颜色 · 与数据集物理一致 通过', dialog.scene_line.text())
        self.app._robolab_language.set_language('en')
        self.assertIn('Cylinder anti-roll on · Appearance variation lighting + object colours', dialog.scene_line.text())
        dialog.close()

    def test_rejects_paths_outside_the_result_folder(self):
        bad = run(0)
        bad['keyframes'][0]['files']['agentview'] = '../../../etc/passwd'
        with self.assertRaises(ValueError):
            read_eval_summary(self.write([bad]))
        escaping = run(1)
        escaping['path'] = '../outside'
        with self.assertRaises(ValueError):
            read_eval_summary(self.write([escaping]))
        with self.assertRaises(ValueError):
            read_eval_summary(self.write([run(2)], schema='robolab.p4_dataset_index.v1'))

    def test_rate_text(self):
        self.assertEqual(rate_text(dict(count=3, rate=.75, ci95=[.3, .95]), 4), '3/4 · 75% [30–95]')
        self.assertEqual(rate_text(dict(count=0, rate=0., ci95=[0., .06]), 64), '0/64 · 0% [0–6]')
        self.assertEqual(rate_text(dict(count=0, rate=None, ci95=[None, None])), '—')
        self.assertEqual(rate_text(dict(count=1, of=2, rate=.5, ci95=[.1, .9])), '1/2 · 50% [10–90]')


if __name__ == '__main__':
    unittest.main()
