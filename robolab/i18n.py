"""GUI 本地化；不重建控件，不改变任务文本、标识符或仿真协议。"""
import json
import os
from pathlib import Path
import re
import string
import weakref

from PySide6.QtCore import (QObject, Signal, QSettings, QSignalBlocker, QTranslator,
                            QLibraryInfo, QLocale, QCoreApplication)
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QLabel, QAbstractButton, QLineEdit, QPlainTextEdit,
                              QMenu, QComboBox, QTabWidget)

from .config import ROOT


class LanguageManager(QObject):
    changed = Signal(str)

    def __init__(self, settings_path=None, parent=None):
        super().__init__(parent)
        self.settings_path = Path(settings_path or os.getenv('ROBOLAB_UI_SETTINGS', str(ROOT/'settings'/'ui.ini'))).expanduser()
        self.settings = QSettings(str(self.settings_path), QSettings.Format.IniFormat)
        requested = os.getenv('ROBOLAB_UI_LANGUAGE', self.settings.value('ui/language', 'zh'))
        self.language = requested if requested in ('zh', 'en') else 'zh'
        self.catalog = json.loads((Path(__file__).parent/'locales'/'en.json').read_text(encoding='utf-8'))
        self.reverse = {value: key for key, value in self.catalog.items()}
        self._bindings = weakref.WeakKeyDictionary()
        self._patterns = []
        formatter = string.Formatter()
        for chinese, english in self.catalog.items():
            for source in (chinese, english):
                if '{' not in source: continue
                pattern = []; formats = {}; seen = set()
                for literal, field, spec, conversion in formatter.parse(source):
                    pattern.append(re.escape(literal))
                    if field is not None:
                        pattern.append(f'(?P={field})' if field in seen else f'(?P<{field}>.*?)')
                        seen.add(field); formats[field] = spec
                self._patterns.append((re.compile(''.join(pattern), re.DOTALL), chinese, english, formats))
        self._patterns.sort(key=lambda item: len(item[0].pattern), reverse=True)
        self._qt_translator = QTranslator(self)
        self.qt_chinese_available = self._qt_translator.load('qtbase_zh_CN', QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
        self.last_save_error = None
        self._apply_qt_language()

    def _apply_qt_language(self):
        app = QCoreApplication.instance()
        if app:
            app.removeTranslator(self._qt_translator)
            if self.language == 'zh' and self.qt_chinese_available: app.installTranslator(self._qt_translator)
        QLocale.setDefault(QLocale('zh_CN' if self.language == 'zh' else 'en_GB'))

    def message(self, text, depth=0):
        """翻译已知状态模板；未知诊断信息保持原文，避免丢失排错依据。"""
        text = str(text)
        if text in self.catalog: return text if self.language == 'zh' else self.catalog[text]
        if text in self.reverse: return self.reverse[text] if self.language == 'zh' else text
        for pattern, chinese, english, formats in self._patterns:
            match = pattern.fullmatch(text)
            if not match: continue
            values = match.groupdict()
            try:
                for key, value in values.items():
                    spec = formats[key]
                    if spec.endswith('d'): values[key] = int(value)
                    elif spec.endswith(('f', 'g', 'e')): values[key] = float(value)
                    elif depth < 2 and key in ('status','mode','source','phase','stage','detail','error','message'):
                        values[key] = self.message(value, depth+1)
                return (chinese if self.language == 'zh' else english).format(**values)
            except (ValueError, TypeError, KeyError): continue
        return text

    def translate(self, source, **values):
        if values:
            template = self.catalog.get(source, source) if self.language == 'en' else self.reverse.get(source, source)
            return template.format(**values)
        return self.message(source)

    def bind(self, obj, source, method='setText', prefix=(), literal=False, **values):
        if not literal: source = self.reverse.get(source, source)
        key = (method, tuple(prefix))
        self._bindings.setdefault(obj, {})[key] = (source, values, literal)
        self._apply(obj, key, source, values, literal)
        return obj

    def _apply(self, obj, key, source, values, literal=False):
        blocker = QSignalBlocker(obj)
        try: getattr(obj, key[0])(*key[1], source if literal else self.translate(source, **values))
        finally: del blocker

    def localize_tree(self, root):
        """登记静态 UI 文案；输入框内容、任务数据与日志正文不在翻译范围内。"""
        def register(obj, text, method='setText', prefix=()):
            if method == 'setText' and obj.property('i18n_literal_text'): return
            if text and (text in self.catalog or text in self.reverse or re.search('[\u3400-\u9fff]', text)):
                self.bind(obj, text, method, prefix)
        for obj in [root]+root.findChildren(QObject):
            if obj.property('i18n_skip'): continue
            if isinstance(obj, (QLabel, QAbstractButton, QAction)): register(obj, obj.text())
            if isinstance(obj, QMenu): register(obj, obj.title(), 'setTitle')
            if isinstance(obj, (QLineEdit, QPlainTextEdit)): register(obj, obj.placeholderText(), 'setPlaceholderText')
            if hasattr(obj, 'toolTip'): register(obj, obj.toolTip(), 'setToolTip')
            if isinstance(obj, QTabWidget):
                for index in range(obj.count()): register(obj, obj.tabText(index), 'setTabText', (index,))
            if isinstance(obj, QComboBox):
                entries = self._bindings.get(obj, {})
                for key in list(entries):
                    if key[0] == 'setItemText': del entries[key]
                for index in range(obj.count()): register(obj, obj.itemText(index), 'setItemText', (index,))
        if hasattr(root, 'windowTitle'): register(root, root.windowTitle(), 'setWindowTitle')

    def set_language(self, language):
        if language not in ('zh', 'en'): raise ValueError('Unsupported UI language')
        if language == self.language: return
        self.language = language
        self.last_save_error = None
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings.setValue('ui/language', language); self.settings.sync()
            if self.settings.status() != QSettings.Status.NoError: raise OSError('QSettings: '+self.settings.status().name)
        except OSError as error: self.last_save_error = str(error)
        self._apply_qt_language()
        for obj, entries in list(self._bindings.items()):
            try:
                for key, (source, values, literal) in entries.items(): self._apply(obj, key, source, values, literal)
            except RuntimeError: self._bindings.pop(obj, None)  # inspector widgets may already be deleted
        self.changed.emit(language)


def language_manager():
    app = QCoreApplication.instance()
    if app is None: raise RuntimeError('Create QApplication before requesting UI translations')
    if not hasattr(app, '_robolab_language'):
        app._robolab_language = LanguageManager(parent=app)
    return app._robolab_language


def tr(source, **values):
    return language_manager().translate(source, **values)


def set_text(obj, source, raw=False, **values):
    obj.setProperty('i18n_literal_text', raw)
    return language_manager().bind(obj, source, literal=raw, **values)
