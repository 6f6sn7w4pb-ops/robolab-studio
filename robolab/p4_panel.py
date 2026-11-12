"""Desktop entry to the independent, lightweight P4 physical verification run."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                              QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from .config import BACKEND_PYTHON, ROOT, backend_environment
from .i18n import language_manager, set_text, tr
from .p1_panel import ImageView, open_directory
from .p4_pilot_view import PilotSummary
from .p5_eval_view import SUMMARY_SCHEMA, EvalSummary


def _schema(path):
    # Result files are canonical JSON with sorted keys, so "schema" can sit anywhere in the file.
    # Both result dialogs cap their files at 8 MB; anything larger is left to them to reject.
    with Path(path).open('rb') as stream:
        payload = stream.read(8_000_001)
    if len(payload) > 8_000_000:
        return None
    data = json.loads(payload)
    return data.get('schema') if isinstance(data, dict) else None


class PartGraspPanel(QWidget):
    completed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.started.connect(self._send_cancel)
        self.process.finished.connect(self._finish)
        self.process.errorOccurred.connect(self._process_error)
        self.kill_timer = QTimer(self)
        self.kill_timer.setSingleShot(True)
        self.kill_timer.timeout.connect(self.process.kill)
        self.run_dir = None
        self.result = {}
        self.display_run = None
        self._cancelled = False
        self._active = False
        layout = QVBoxLayout(self)
        note = QLabel('在独立仿真中用几何规则抓取粗端或细端。使用相同初态，保存同一求解器时刻的双 RGB 和接触。')
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.part = QComboBox()
        self.part.addItem('抓住物体的粗端并抬起。', 'thick')
        self.part.addItem('抓住物体的细端并抬起。', 'thin')
        row.addWidget(self.part, 1)
        self.run_button = QPushButton('运行 P4 部位抓取')
        self.run_button.clicked.connect(self.start)
        self.cancel_button = QPushButton('取消自检')
        self.cancel_button.clicked.connect(self.cancel)
        self.open_button = QPushButton('打开结果目录')
        self.open_button.clicked.connect(lambda: open_directory(self.run_dir))
        self.pilot_button = QPushButton('P4 试采汇总')
        self.pilot_button.clicked.connect(self.choose_pilot)
        for button in (self.run_button, self.cancel_button, self.open_button, self.pilot_button):
            row.addWidget(button)
        self.cancel_button.setEnabled(False)
        self.open_button.setEnabled(False)
        layout.addLayout(row)
        self.status = QLabel('待运行；尚无本次结果')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        legend = QLabel('左为桌面，右为腕部。白十字为指定部位中心，橙蓝为两个指垫的接触点。投影未判定遮挡。')
        legend.setWordWrap(True)
        layout.addWidget(legend)
        self.preview = ImageView()
        layout.addWidget(self.preview, 1)
        self.logs = QPlainTextEdit(readOnly=True)
        self.logs.setMaximumBlockCount(200)
        self.logs.setMaximumHeight(96)
        layout.addWidget(self.logs)
        language_manager().changed.connect(self._show_summary)

    def choose_pilot(self):
        path, _ = QFileDialog.getOpenFileName(self, tr('选择 P4 试采、OSC 验收、数据集或 P5 评估结果'),
                                               str(ROOT/'outputs'/'p4b_pilot'/'result.json'), 'JSON (*.json)')
        if path:
            self.open_pilot(Path(path))

    def open_pilot(self, path):
        try:
            dialog = EvalSummary(path, self) if _schema(path) == SUMMARY_SCHEMA else PilotSummary(path, self)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            self.logs.appendPlainText(str(error))
            QMessageBox.warning(self, tr('P4 试采汇总'), tr('无法读取 P4 试采结果。'))
            return None
        dialog.show()
        return dialog

    @property
    def is_running(self):
        return self._active or self.process.state() != QProcess.ProcessState.NotRunning

    def start(self):
        if self.is_running:
            return
        base = Path(os.getenv('ROBOLAB_P4_OUTPUT_DIR', str(ROOT/'outputs'/'p4_parts'))).expanduser().resolve()
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        self.run_dir = base/f'{stamp}_{uuid.uuid4().hex[:6]}'
        self.result = {}
        self.display_run = None
        self._cancelled, self._active = False, True
        self.preview.clear()
        self.logs.clear()
        self.part.setEnabled(False)
        self.run_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        set_text(self.status, '正在运行 P4 仿真抓取和双 RGB 验证…')
        environment = QProcessEnvironment()
        for key, value in backend_environment().items():
            environment.insert(key, value)
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(ROOT))
        self.process.start(str(BACKEND_PYTHON), [str(ROOT/'scripts'/'verify_p4_parts.py'),
                           '--part', self.part.currentData(), '--output-dir', str(self.run_dir),
                           '--accept-cancel-stdin'])

    def _read(self):
        data = bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace').strip()
        if data:
            self.logs.appendPlainText(data)

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.logs.appendPlainText(self.process.errorString())
            self._finish(-1, QProcess.ExitStatus.CrashExit)

    def _finish(self, code, exit_status):
        if not self._active:
            return
        self._active = False
        self.kill_timer.stop()
        self._read()
        try:
            result = json.loads((self.run_dir/'result.json').read_text(encoding='utf-8'))
            self.result = result
            if result['status'] == 'finalized':
                if exit_status != QProcess.ExitStatus.NormalExit:
                    raise ValueError('Unexpected P4 process exit')
                run = result['runs'][0]
                if run['name'] != self.part.currentData() or not run['files_verified']:
                    raise ValueError('Unexpected P4 run result')
                if 'preview_frame' in run:
                    pixmap = QPixmap(str(self.run_dir/run['name']/'projection-preview.png'))
                    if pixmap.size().width() != 448 or pixmap.height() < 224:
                        raise ValueError('Invalid P4 preview image')
                    self.preview.set_pixmap(pixmap.copy(0, 0, 448, 224))
                self.display_run = run
                self._show_summary()
        except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
            self.logs.appendPlainText(str(error))
            self.result = {}
        if self.result.get('status') == 'cancelled':
            set_text(self.status, '已取消本次 P4 运行，保留已写入的数据。')
        elif self._cancelled and not self.result:
            self.result = {'status': 'interrupted'}
            set_text(self.status, 'P4 进程已停止，记录可能尚未完整结束。')
        elif self.result.get('status') != 'finalized':
            set_text(self.status, 'P4 运行未完成，请查看本次日志。')
        self.part.setEnabled(True)
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.open_button.setEnabled(self.run_dir.is_dir())
        self.completed.emit(self.result)

    def _show_summary(self, *_):
        if self.display_run is None:
            return
        run = self.display_run
        outcome = run['outcome']
        labels = {'thick':'粗端', 'thin':'细端'}
        actual = ', '.join(tr(labels[part]) for part in outcome['held_parts']) or '—'
        set_text(self.status, 'P4 {status} · 指定 {requested} · 持续抓住 {actual} · 最大提升 {lift:.1f} cm · {pairs} 对 RGB',
                 status=tr('通过' if self.result['passed'] else '未通过'),
                 requested=tr(labels[run['instruction']['part']]), actual=actual,
                 lift=outcome['max_lift_m']*100, pairs=run['rgb_pairs'])

    def cancel(self):
        if not self.is_running or self._cancelled:
            return
        self._cancelled = True
        self.cancel_button.setEnabled(False)
        set_text(self.status, '正在取消本次自检…')
        self._send_cancel()
        self.kill_timer.start(12000)

    def _send_cancel(self):
        if self._cancelled and self.process.state() == QProcess.ProcessState.Running:
            self.process.write(b'cancel\n')
