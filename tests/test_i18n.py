"""本地化的协议/输入保护测试；使用 GUI venv，不需要仿真或模型。"""
import os
from pathlib import Path
import string
import tempfile
import unittest
from unittest.mock import patch

try:
    from PySide6.QtCore import QSettings
    from PySide6.QtTest import QSignalSpy
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QComboBox, QTabWidget, QLineEdit, QLabel
    import shiboken6
except ModuleNotFoundError as error:
    if error.name not in ('PySide6','shiboken6'): raise
    raise unittest.SkipTest('Run GUI localization tests with the separate GUI venv; the simulation backend does not need Qt.') from error

from robolab.i18n import LanguageManager


class LocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ)
        self.env.start(); os.environ.pop('ROBOLAB_UI_LANGUAGE', None)
        self.path = Path(self.tmp.name)/'ui.ini'
        self.manager = LanguageManager(self.path)

    def tearDown(self):
        self.app.removeTranslator(self.manager._qt_translator)
        self.manager.deleteLater(); self.env.stop(); self.tmp.cleanup()

    def test_all_catalog_placeholders_preserve_names_and_formats(self):
        formatter = string.Formatter()
        for source, target in self.manager.catalog.items():
            fields = lambda text: sorted((name, spec, conversion) for _,name,spec,conversion in formatter.parse(text) if name is not None)
            self.assertEqual(fields(source), fields(target), source)

    def test_switch_does_not_emit_control_selection_signals(self):
        root = QWidget(); layout = QVBoxLayout(root)
        combo = QComboBox(); combo.addItem('编辑模式','edit'); combo.addItem('仿真模式','simulate'); combo.setCurrentIndex(1)
        tabs = QTabWidget(); tabs.addTab(QWidget(),'三维场景'); tabs.addTab(QWidget(),'双相机 · 实时'); tabs.setCurrentIndex(1)
        layout.addWidget(combo); layout.addWidget(tabs)
        self.manager.localize_tree(root)
        combo_spy, tab_spy = QSignalSpy(combo.currentIndexChanged), QSignalSpy(tabs.currentChanged)
        self.manager.set_language('en')
        self.assertEqual(combo.currentData(),'simulate'); self.assertEqual(combo.currentText(),'Simulation mode')
        self.assertEqual(tabs.currentIndex(),1); self.assertEqual(tabs.tabText(1),'Dual cameras · Live')
        self.assertEqual(combo_spy.count(),0); self.assertEqual(tab_spy.count(),0)
        root.close()

    def test_user_input_and_literal_task_data_are_never_translated(self):
        root = QWidget(); layout = QVBoxLayout(root)
        edit = QLineEdit('/tmp/重置/模型目录'); edit.setPlaceholderText('本地模型目录（可暂空）'); layout.addWidget(edit)
        instruction = QLabel('Save project'); layout.addWidget(instruction)
        self.manager.bind(instruction,'Save project',literal=True)
        instruction.setProperty('i18n_literal_text',True)
        self.manager.localize_tree(root)
        self.manager.set_language('en'); self.manager.set_language('zh')
        self.assertEqual(edit.text(),'/tmp/重置/模型目录'); self.assertEqual(instruction.text(),'Save project')
        root.close()

    def test_dynamic_status_round_trip_and_raw_error_fallback(self):
        widget = QLabel()
        source = '任务执行：Mock（非 VLA） · Mock 接口自检完成（非 VLA，不代表抓取成功）'
        self.manager.bind(widget,source); self.manager.set_language('en')
        self.assertIn('Mock interface test completed',widget.text()); self.assertNotIn('执行',widget.text())
        self.manager.set_language('zh'); self.assertEqual(widget.text(),source)
        detail = 'unregistered diagnostic: id=abc/路径'
        self.assertEqual(self.manager.message(detail),detail)

    def test_deleted_inspector_bindings_are_safe(self):
        widget = QLabel('质量 / kg'); self.manager.bind(widget,'质量 / kg')
        shiboken6.delete(widget)
        self.manager.set_language('en'); self.manager.set_language('zh')

    def test_preference_is_saved_and_read_by_another_manager(self):
        self.manager.set_language('en')
        other = LanguageManager(self.path)
        self.assertEqual(other.language,'en'); self.assertIsNone(self.manager.last_save_error)
        self.app.removeTranslator(other._qt_translator); other.deleteLater()

    def test_invalid_saved_language_falls_back_to_chinese(self):
        settings = QSettings(str(self.path),QSettings.Format.IniFormat)
        settings.setValue('ui/language','invalid'); settings.sync()
        other = LanguageManager(self.path)
        self.assertEqual(other.language,'zh')
        self.app.removeTranslator(other._qt_translator); other.deleteLater()


if __name__ == '__main__': unittest.main()
