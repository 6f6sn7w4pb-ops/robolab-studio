"""Read-only desktop view of the P4 dataset index; uses the GUI venv and a temporary language file."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as error:
    if error.name != 'PySide6':
        raise
    raise unittest.SkipTest('Run desktop view tests with the separate GUI venv.') from error

from robolab.i18n import LanguageManager
from robolab.p4_pilot_view import DATASET_INDEX, PILOT_INDEX, PilotSummary, read_pilot_index


def run(index, split='train', part='thick', passed=True, expected=True):
    return dict(path=f'episodes/case_{index}_{part}', case_id=f'case_{index}_{part}', scene_id=f'case_{index}',
                split=split, part=part, files_verified=True, expected_outcome_verified=expected, rgb_pairs=222,
                instruction=dict(en='Pick up the object by the thick end.', zh='抓住物体的粗端并抬起。'),
                outcome=dict(passed=passed, held_parts=[part] if passed else []),
                failure_reasons=[] if passed else ['finger_obstacle_contact'],
                replay=dict(ticks=222, max_qpos_difference=0.))


class DatasetViewTests(unittest.TestCase):
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

    def write(self, schema, runs, status='finalized'):
        path = self.root/'result.json'
        path.write_text(json.dumps(dict(schema=schema, status=status, runs=runs)), encoding='utf-8')
        return path

    def test_dataset_index_allows_many_rows_and_new_split(self):
        runs = [run(i, split='test_instance' if i % 5 == 0 else 'train') for i in range(60)]
        runs.append(run(99, split='control', passed=False, expected=True))
        data = read_pilot_index(self.write(DATASET_INDEX, runs))
        self.assertEqual(len(data['runs']), 61)
        with self.assertRaises(ValueError):
            read_pilot_index(self.write(PILOT_INDEX, runs))          # old pilot limit stays 25 rows

    def test_rows_cannot_point_outside_the_dataset(self):
        bad = run(1)
        bad['path'] = '../elsewhere'
        with self.assertRaises(ValueError):
            read_pilot_index(self.write(DATASET_INDEX, [bad]))

    def test_summary_counts_and_language_switch(self):
        runs = [run(0), run(0, part='thin', split='test_instance'),
                run(1, split='test_unseen', passed=False, expected=False),
                run(2, split='control', passed=False, expected=True)]
        dialog = PilotSummary(self.write(DATASET_INDEX, runs))
        try:
            self.assertIn('演示通过 2/3', dialog.status.text())
            self.assertIn('阻挡对照按预期失败 1/1', dialog.status.text())
            self.assertIn('已复执行校验 4/4', dialog.status.text())
            self.assertEqual(dialog.windowTitle(), 'P4 OSC 数据集')
            self.assertEqual(dialog.table.topLevelItem(1).text(1), '已见形状族新实例')
            dialog.table.setCurrentItem(dialog.table.topLevelItem(2))
            self.assertIn('finger_obstacle_contact', dialog.detail.text())
            self.app._robolab_language.set_language('en')
            self.assertIn('Demonstrations passed 2/3', dialog.status.text())
            self.assertEqual(dialog.windowTitle(), 'P4 OSC dataset')
            self.assertEqual(dialog.table.topLevelItem(1).text(1), 'Seen family, new instance')
            self.assertIn('Failure reasons', dialog.detail.text())
        finally:
            dialog.close()


if __name__ == '__main__':
    unittest.main()
