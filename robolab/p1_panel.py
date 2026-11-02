"""桌面双 RGB 查看与独立 P1 子进程；GUI 不导入任何仿真或模型依赖。"""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil

from PySide6.QtCore import Qt, QProcess, QProcessEnvironment, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QMovie, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QSpinBox, QProgressBar, QPlainTextEdit, QTabWidget)

from .config import ROOT, BACKEND_PYTHON, LIBERO_ROOT
from .i18n import language_manager, set_text, tr

EVENT_PREFIX = 'ROBOLAB_P1_EVENT='


def desktop_path(path):
    distro=os.getenv('WSL_DISTRO_NAME')
    return '\\\\wsl.localhost\\'+distro+str(path).replace('/','\\') if distro else str(path)


def open_directory(path):
    explorer=shutil.which('explorer.exe') if os.getenv('WSL_DISTRO_NAME') else None
    if explorer:
        ok,_=QProcess.startDetached(explorer,[desktop_path(path)])
        return ok
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class ImageView(QWidget):
    """保持原图比例的只读图像控件；不把鼠标操作发送给场景。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(120, 120)
        self.pixmap = QPixmap()
        self.message = '等待图像'
        language_manager().changed.connect(self.update)

    def clear(self, message='等待本次图像'):
        self.pixmap = QPixmap()
        self.message = message
        self.update()

    def set_pixmap(self, pixmap):
        if pixmap.isNull():
            raise ValueError('图像读取失败')
        self.pixmap = pixmap
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#182333'))
        if self.pixmap.isNull():
            painter.setPen(QColor('#d5e0f0'))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr(self.message))
            return
        fitted = self.pixmap.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        x, y = (self.width()-fitted.width())//2, (self.height()-fitted.height())//2
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(x, y, fitted.width(), fitted.height(), self.pixmap)


class DualCameraView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.views = {}
        for role, title in [('agentview', '外部 RGB · agentview'),
                            ('robot0_eye_in_hand', '腕部 RGB · robot0_eye_in_hand')]:
            column = QVBoxLayout()
            column.addWidget(QLabel(title))
            view = ImageView()
            view.setObjectName(role+'_view')
            self.views[role] = view
            column.addWidget(view, 1)
            row.addLayout(column, 1)
        layout.addLayout(row, 1)
        self.info = QLabel('尚无本次图像')
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.last_packet = None

    def clear(self, message='等待本次图像'):
        self.last_packet = None
        for view in self.views.values(): view.clear(message)
        set_text(self.info, message)

    def set_packet(self, packet):
        decoded = {}
        for name in self.views:
            item = packet['images'][name]
            pixmap = QPixmap()
            if item['encoding'] != 'jpeg_base64' or not pixmap.loadFromData(base64.b64decode(item['data'], validate=True), 'JPEG'):
                raise ValueError('相机图像解码失败：'+name)
            if (pixmap.width(), pixmap.height()) != (item['width'], item['height']):
                raise ValueError('相机分辨率与元数据不一致：'+name)
            decoded[name] = pixmap
        for name, pixmap in decoded.items(): self.views[name].set_pixmap(pixmap)
        self.last_packet = packet
        shape = next(iter(decoded.values())).size()
        set_text(self.info, f"同步 RGB · 每路 {shape.width()}×{shape.height()} · t={packet['sim_time']:.3f} s · 帧 {packet['sequence']}\n"+packet['task']['language'])

    def show_result(self, run_dir, metadata):
        for name, file in [('agentview', 'external.png'), ('robot0_eye_in_hand', 'wrist.png')]:
            self.views[name].set_pixmap(QPixmap(str(run_dir/file)))
        set_text(self.info, f"本次 P1 最终帧 · {metadata['requested_steps']} 步 · {metadata['elapsed_simulation_seconds']:.3f} s 仿真\n"+metadata['task']['language'])


class P1TestPanel(QWidget):
    completed = Signal(dict)
    diagnostic = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.backend_python, self.libero_root = BACKEND_PYTHON, LIBERO_ROOT
        self.output_root = Path(os.getenv('OUTPUT_DIR', os.getenv('ROBOLAB_OUTPUT_DIR', str(ROOT/'outputs'/'smoke_test')))).expanduser().resolve()
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.readyReadStandardOutput.connect(lambda: self._read(False))
        self.process.readyReadStandardError.connect(lambda: self._read(True))
        self.process.finished.connect(self._finish)
        self.process.errorOccurred.connect(self._process_error)
        self.kill_timer = QTimer(self)
        self.kill_timer.setSingleShot(True)
        self.kill_timer.timeout.connect(self.process.kill)
        self._active = self._cancelled = False
        self.run_dir = self.metadata = self.movie = None
        self.result = {'status': 'idle'}
        self._build()
        language_manager().localize_tree(self)

    @property
    def is_running(self):
        return self._active or self.process.state() != QProcess.ProcessState.NotRunning

    def _build(self):
        layout = QVBoxLayout(self)
        note = QLabel('独立验证官方 LIBERO-Object 初始场景，不包含编辑器改动。\n双 RGB、reset/step 与导出测试；不执行抓取或 VLA。')
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        row.addWidget(QLabel('测试任务'))
        self.task_combo = QComboBox()
        self.task_combo.setObjectName('p1_task')
        row.addWidget(self.task_combo, 1)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel('随机种子'))
        self.seed = QSpinBox(); self.seed.setRange(0, 2**31-1)
        row.addWidget(self.seed)
        row.addWidget(QLabel('步数'))
        self.steps = QSpinBox(); self.steps.setRange(1, 200); self.steps.setValue(20)
        row.addWidget(self.steps)
        row.addWidget(QLabel('RGB 256×256 · 官方初态 0'))
        row.addStretch()
        layout.addLayout(row)
        row = QHBoxLayout()
        self.run_button = QPushButton('运行离屏自检')
        self.run_button.setObjectName('run_p1_test')
        self.run_button.setEnabled(False)
        self.run_button.clicked.connect(self.start)
        self.cancel_button = QPushButton('取消自检')
        self.cancel_button.setEnabled(False); self.cancel_button.clicked.connect(self.cancel)
        self.copy_button = QPushButton('复制结果路径')
        self.copy_button.setEnabled(False); self.copy_button.clicked.connect(self.copy_path)
        self.open_button = QPushButton('打开结果目录')
        self.open_button.setEnabled(False); self.open_button.clicked.connect(self.open_folder)
        for button in (self.run_button, self.cancel_button, self.copy_button, self.open_button): row.addWidget(button)
        layout.addLayout(row)
        self.status = QLabel('待运行；尚无本次结果')
        self.status.setMinimumHeight(self.fontMetrics().lineSpacing()*2+4)
        self.status.setWordWrap(True); layout.addWidget(self.status)
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.result_tabs = QTabWidget()
        self.cameras = DualCameraView(); self.result_tabs.addTab(self.cameras, '本次双 RGB')
        gif_page = QWidget(); gif_layout = QVBoxLayout(gif_page)
        self.gif_view = ImageView(); gif_layout.addWidget(self.gif_view, 1)
        self.gif_button = QPushButton('播放 GIF'); self.gif_button.setEnabled(False)
        self.gif_button.clicked.connect(self.toggle_gif); gif_layout.addWidget(self.gif_button)
        self.result_tabs.addTab(gif_page, 'GIF 回放')
        self.logs = QPlainTextEdit(); self.logs.setReadOnly(True); self.logs.setMaximumBlockCount(800)
        self.result_tabs.addTab(self.logs, '本次日志')
        layout.addWidget(self.result_tabs, 1)

    def set_tasks(self, tasks):
        self.task_combo.clear()
        for task in tasks: self.task_combo.addItem(f"{task['index']+1:02d} · {task['language']}", task['index'])
        self.run_button.setEnabled(bool(tasks) and not self.is_running)

    def start(self):
        if self.is_running or self.task_combo.currentData() is None: return
        self.kill_timer.stop()
        if self.movie: self.movie.stop(); self.movie.deleteLater(); self.movie = None
        self.cameras.clear(); self.gif_view.clear()
        self.run_dir = self.metadata = None
        self._cancelled = self._got_complete = False
        self._protocol_error = None
        self._buffers = [bytearray(), bytearray()]
        self._transcript = []
        self.result = {'status': 'running'}
        self.logs.clear(); self.result_tabs.setCurrentIndex(2)
        self.gif_button.setEnabled(False); set_text(self.gif_button, '播放 GIF')
        self.copy_button.setEnabled(False); self.open_button.setEnabled(False)
        self.run_button.setEnabled(False); self.cancel_button.setEnabled(True)
        for widget in (self.task_combo, self.seed, self.steps): widget.setEnabled(False)
        self.progress.setRange(0, 0); set_text(self.status, '正在启动自检…')
        self._active = True
        env = QProcessEnvironment.systemEnvironment()
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QT_QPA_FONTDIR'): env.remove(key)
        for key, value in {'MUJOCO_GL':'egl', 'PYOPENGL_PLATFORM':'egl', 'PYTHONDONTWRITEBYTECODE':'1',
                           'PYTHONUNBUFFERED':'1', 'HF_HUB_OFFLINE':'1', 'TRANSFORMERS_OFFLINE':'1'}.items(): env.insert(key, value)
        self.process.setProcessEnvironment(env); self.process.setWorkingDirectory(str(ROOT))
        self.process.start(str(self.backend_python), [str(ROOT/'scripts'/'smoke_test_libero.py'), '--json-events',
            '--libero-root', str(self.libero_root), '--output-dir', str(self.output_root),
            '--task-id', str(self.task_combo.currentData()), '--seed', str(self.seed.value()), '--steps', str(self.steps.value())])

    def _read(self, stderr):
        data = bytes(self.process.readAllStandardError() if stderr else self.process.readAllStandardOutput())
        buffer = self._buffers[int(stderr)]
        buffer.extend(data)
        while b'\n' in buffer:
            line, _, rest = buffer.partition(b'\n'); buffer[:] = rest
            self._line(line.decode('utf-8', errors='replace'), stderr)

    def _line(self, line, stderr=False):
        self._transcript.append(line)
        if not stderr and line.startswith(EVENT_PREFIX):
            try:
                event = json.loads(line[len(EVENT_PREFIX):])
                if event['event'] == 'created':
                    path = Path(event['run_dir']).resolve()
                    if not path.is_relative_to(self.output_root): raise ValueError('结果目录不在配置的输出目录下')
                    self.run_dir = path
                elif event['event'] == 'phase':
                    self.progress.setRange(0, 0); set_text(self.status, event['text'])
                elif event['event'] == 'step':
                    self.progress.setRange(0, event['total']); self.progress.setValue(event['completed'])
                    set_text(self.status, f"已完成 {event['completed']}/{event['total']} 步 · t={event['sim_time']:.3f} s")
                elif event['event'] == 'complete': self._got_complete = True
            except (ValueError, KeyError, TypeError) as error: self._protocol_error = str(error)
        elif line.strip():
            self.logs.appendPlainText(line)

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._line('启动失败：'+self.process.errorString(), True)
            self._finish(-1, QProcess.ExitStatus.CrashExit)

    def _finish(self, code, exit_status):
        if not self._active: return
        self._read(False); self._read(True)
        for buffer in self._buffers:
            if buffer: self._line(buffer.decode('utf-8', errors='replace'), True); buffer.clear()
        self._active = False; self.kill_timer.stop()
        result = {'status': 'failed', 'exit_code': code, 'normal_exit': exit_status == QProcess.ExitStatus.NormalExit,
                  'run_dir': str(self.run_dir) if self.run_dir else None}
        try:
            if self._cancelled:
                result['status'] = 'cancelled'
                raise ValueError('已取消本次自检；没有将历史图像标记为本次结果。')
            if self._protocol_error: raise ValueError('进度协议错误：'+self._protocol_error)
            if self.run_dir is None: raise ValueError('未收到本次结果目录；请查看本次日志。')
            report = json.loads((self.run_dir/'metadata.json').read_text(encoding='utf-8'))
            if code != 0 or not result['normal_exit'] or not report['passed'] or not self._got_complete:
                raise ValueError(report.get('error', {}).get('message', '自检进程未通过；请查看本次日志。'))
            if Path(report['run_dir']).resolve() != self.run_dir: raise ValueError('结果不属于本次进程')
            for file in ('external.png', 'wrist.png', 'simulation.gif'):
                digest = hashlib.sha256((self.run_dir/file).read_bytes()).hexdigest()
                if digest != report['artifacts'][file]['sha256']: raise ValueError('导出文件校验失败：'+file)
            self.cameras.show_result(self.run_dir, report)
            self.movie = QMovie(str(self.run_dir/'simulation.gif'))
            self.movie.frameChanged.connect(lambda _: self.gif_view.set_pixmap(self.movie.currentPixmap()))
            if not self.movie.isValid() or self.movie.frameCount() < 2: raise ValueError('GIF 解码失败')
            self.movie.jumpToFrame(0)
            self.gif_button.setEnabled(True)
            self.metadata = report
            result['status'] = 'passed'
            result['message'] = f"P1 通过 · {report['requested_steps']} 步 · 双路 256×256 RGB · PNG/GIF 已校验\n{self.run_dir}"
            self.progress.setRange(0, 100); self.progress.setValue(100)
            self.result_tabs.setCurrentIndex(0)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.cameras.clear('本次没有通过验证的图像'); self.gif_view.clear('本次没有通过验证的 GIF')
            self.gif_button.setEnabled(False)
            self.progress.setRange(0, 100); self.progress.setValue(0)
            result['message'] = ('已取消' if self._cancelled else 'P1 未通过')+' · '+str(error)
            self.logs.appendPlainText(result['message'])
        set_text(self.status, result['message'])
        self.run_button.setEnabled(self.task_combo.count() > 0); self.cancel_button.setEnabled(False)
        for widget in (self.task_combo, self.seed, self.steps): widget.setEnabled(True)
        available = self.run_dir is not None and self.run_dir.is_dir()
        self.copy_button.setEnabled(available); self.open_button.setEnabled(available)
        if available:
            try:
                (self.run_dir/'gui_process.log').write_text('\n'.join(self._transcript), encoding='utf-8')
                (self.run_dir/'gui_result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            except OSError as error: self.logs.appendPlainText('GUI 日志保存失败：'+str(error))
        self.result = result
        self.diagnostic.emit(result['message']); self.completed.emit(result)

    def cancel(self):
        if not self.is_running or self._cancelled: return
        self._cancelled = True; self.cancel_button.setEnabled(False)
        set_text(self.status, '正在取消本次自检…')
        self.process.terminate(); self.kill_timer.start(4000)

    def toggle_gif(self):
        if not self.movie: return
        self.result_tabs.setCurrentIndex(1)
        if self.movie.state() == QMovie.MovieState.Running:
            self.movie.setPaused(True); set_text(self.gif_button, '继续播放 GIF')
        else:
            if self.movie.state() == QMovie.MovieState.Paused: self.movie.setPaused(False)
            else: self.movie.start()
            set_text(self.gif_button, '暂停 GIF')

    def desktop_path(self):
        return desktop_path(self.run_dir)

    def copy_path(self):
        if self.run_dir: QApplication.clipboard().setText(self.desktop_path())

    def open_folder(self):
        if not self.run_dir: return
        if not open_directory(self.run_dir): self.logs.appendPlainText('无法打开文件管理器，请使用“复制结果路径”。')
