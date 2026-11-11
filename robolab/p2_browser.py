"""Native record browser; backend owns all MuJoCo state-view rendering."""
import base64
import bisect
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QLineEdit, QFileDialog, QTreeWidget, QTreeWidgetItem, QSlider, QSizePolicy)

from .episode_store import output_root
from .i18n import language_manager, set_text, tr
from .p1_panel import open_directory
from .contact_panel import ContactControls

STATUS = {'success':'成功','failed':'失败','cancelled':'已取消','incomplete':'不完整','corrupt':'损坏'}
CONTACT_STATUS = {'available':'已校验 · {count} 帧','not_collected':'未采集',
                  'incomplete':'不完整','corrupt':'损坏','failed':'写入失败','unsupported':'格式不支持'}
CONTACT_REASON = {'p3_manifest_missing':'缺少接触记录清单','p3_trace_missing':'缺少接触轨迹文件',
                  'p3_trace_truncated':'接触轨迹不完整','p3_unfinalized':'接触记录仍在写入或已中断',
                  'p3_parent_unfinalized':'P2 父记录尚未结束','p3_schema_unsupported':'未知 P3 接触记录版本',
                  'p3_parent_unsupported':'此 P2 格式不支持 P3 接触关联','p3_write_failed':'P3 接触采集出错',
                  'p3_invalid_record':'接触记录校验失败'}


class StateImage(QLabel):
    def __init__(self):
        super().__init__()
        self.source = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(100,120)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

    def set_source(self, pixmap):
        self.source = pixmap
        self.fit()

    def fit(self):
        if not self.source.isNull():
            self.setPixmap(self.source.scaled(self.size(),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()

    def clear(self):
        self.source = QPixmap()
        super().clear()


class P2Browser(QWidget):
    def __init__(self, command, parent=None):
        super().__init__(parent)
        self.command = command
        self.records = []; self.current = None; self.times = []; self.pending = False
        self.last_index = 0; self.playing = False; self.packet = None
        self.idle = False
        self.queued_index = None
        self.seek_request = None
        box = QVBoxLayout(self)
        note = QLabel('记录结果来自原始执行；状态回放只重建采样状态，不推进物理，不重新判定成功。\n重建画面不是原始 RGB，也不是完整物理回放；当前编辑场景保持独立。')
        note.setWordWrap(True); box.addWidget(note)
        row = QHBoxLayout(); self.root = QLineEdit(str(output_root())); row.addWidget(self.root, 1)
        self.choose = QPushButton('选择记录根目录'); self.choose.clicked.connect(self.choose_root); row.addWidget(self.choose)
        self.refresh = QPushButton('刷新记录'); self.refresh.clicked.connect(self.reload); row.addWidget(self.refresh); box.addLayout(row)
        self.table = QTreeWidget(); self.table.setRootIsDecorated(False); self.table.setMinimumHeight(180)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.table.itemDoubleClicked.connect(lambda *_: self.open_selected())
        box.addWidget(self.table, 1)
        self.detail = QLabel('请选择一条记录'); self.detail.setWordWrap(True); self.detail.setTextFormat(Qt.TextFormat.PlainText); box.addWidget(self.detail)
        self.contact_detail = QLabel()
        self.contact_detail.setWordWrap(True)
        box.addWidget(self.contact_detail)
        row = QHBoxLayout()
        self.open_button = QPushButton('打开记录 / 状态查看'); self.open_button.clicked.connect(self.open_selected); row.addWidget(self.open_button)
        self.folder_button = QPushButton('打开记录目录'); self.folder_button.clicked.connect(self.open_folder); row.addWidget(self.folder_button)
        self.close_button = QPushButton('关闭状态查看'); self.close_button.clicked.connect(self.close_view); row.addWidget(self.close_button); box.addLayout(row)
        self.image = StateImage(); box.addWidget(self.image, 2)
        self.contact_controls = ContactControls(historical=True)
        self.contact_controls.changed.connect(self.contact_options_changed)
        self.contact_controls.hide()
        box.addWidget(self.contact_controls)
        self.timeline = QLabel(); box.addWidget(self.timeline)
        row = QHBoxLayout(); self.play_button = QPushButton('播放状态序列'); self.play_button.clicked.connect(self.toggle_play); row.addWidget(self.play_button)
        self.slider = QSlider(Qt.Orientation.Horizontal); self.slider.setRange(0,0)
        self.slider.sliderPressed.connect(self.stop_play)
        self.slider.valueChanged.connect(self.seek); row.addWidget(self.slider,1); box.addLayout(row)
        self.timer = QTimer(self); self.timer.setInterval(100); self.timer.timeout.connect(self.advance)
        language_manager().changed.connect(self.retranslate)
        self.retranslate(); self.set_available(False, False)

    def retranslate(self, *_):
        self.table.setHeaderLabels([tr('记录 / 任务'),tr('状态'),tr('seed / 初态'),tr('样本数')])
        for index,width in enumerate((340,85,100,75)): self.table.setColumnWidth(index,width)
        selected = self.current['directory'] if self.current else None
        self.table.blockSignals(True); self.table.clear()
        for record in self.records:
            task = record.get('task', {})
            item = QTreeWidgetItem([record['id']+' · '+task.get('language',''), tr(STATUS[record['classification']]),
                                   f"{task.get('seed','?')} / {task.get('init_index','?')}", str(record.get('sample_count',0))])
            item.setData(0, Qt.ItemDataRole.UserRole, record)
            item.setToolTip(0, task.get('language',''))
            self.table.addTopLevelItem(item)
            if record['directory'] == selected: self.table.setCurrentItem(item)
        self.table.blockSignals(False); self.selection_changed()
        if self.packet: self.show_frame(self.packet)

    def set_available(self, connected, idle):
        self.idle = idle
        self.refresh.setEnabled(connected); self.choose.setEnabled(connected)
        self.open_button.setEnabled(idle and bool(self.current) and self.current['classification'] not in ('incomplete','corrupt'))
        self.folder_button.setEnabled(bool(self.current))
        self.play_button.setEnabled(connected and bool(self.times)); self.slider.setEnabled(connected and bool(self.times))
        self.close_button.setEnabled(connected and self.packet is not None)
        self.contact_controls.setEnabled(connected and self.packet is not None)

    def choose_root(self):
        path = QFileDialog.getExistingDirectory(self, tr('选择记录根目录'), self.root.text())
        if path: self.root.setText(path); self.reload()

    def reload(self):
        self.command('records_list', root=self.root.text())

    def set_records(self, result):
        self.records = result['records']; self.retranslate()
        if result.get('truncated'): set_text(self.detail, '仅显示最近 200 条记录，请选择更小的记录根目录')

    def selection_changed(self):
        previous = self.current['directory'] if self.current else None
        item = self.table.currentItem()
        self.current = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if previous and (not self.current or self.current['directory'] != previous):
            self.stop_play()
        if not self.current:
            set_text(self.detail, '请选择一条记录')
            self.contact_detail.hide()
            return
        record = self.current; metrics = record.get('metrics', {})
        flag = lambda v: '✓' if v is True else '✗' if v is False else '?'
        set_text(self.detail, '任务：{task}\n状态：{status} · 原始接触 / 提升 / 官方任务：{contact} / {lift} / {official}\n{schema} · {directory}\n{detail}',
                 task=record.get('task',{}).get('language','?'),
                 status=tr(STATUS[record['classification']]), contact=flag(metrics.get('finger_contact_verified')),
                 lift=flag(metrics.get('lift_verified')), official=flag(record.get('official_task_success')),
                 schema=record.get('schema','?'), directory=record['directory'], detail=tr(record.get('reason','')))
        contacts = record.get('contact_recording')
        self.contact_detail.setVisible(contacts is not None)
        if contacts is not None:
            label = CONTACT_STATUS[contacts['status']]
            status = tr(label, count=contacts['sample_count']) if contacts['status'] == 'available' else tr(label)
            set_text(self.contact_detail, 'P3 接触记录：{status}', status=status)
            self.contact_detail.setToolTip(tr(CONTACT_REASON.get(contacts.get('reason'), '')))
        # MainWindow refreshes the authoritative enabled state after replies.
        self.open_button.setEnabled(self.idle and record['classification'] not in ('incomplete','corrupt'))
        self.folder_button.setEnabled(True)

    def open_folder(self):
        if self.current: open_directory(self.current['directory'])

    def open_selected(self):
        if self.current and self.open_button.isEnabled():
            self.stop_play(); self.queued_index = self.seek_request = None; self.pending = False
            self.command('replay_open', directory=self.current['directory'])

    def close_view(self):
        self.command('replay_close'); self.clear_view()

    def clear_view(self):
        self.stop_play(); self.queued_index = self.seek_request = None; self.pending = False
        self.packet = None; self.times = []; self.image.clear(); self.timeline.clear()
        self.contact_controls.set_active(False)
        self.contact_controls.hide()
        self._size_record_table(False)

    def _size_record_table(self, contact_view):
        if contact_view:
            self.table.setFixedHeight(108)
        else:
            self.table.setMaximumHeight(16_777_215)
            self.table.setMinimumHeight(180)

    def contact_options_changed(self):
        self.stop_play()
        self.seek(self.slider.value())

    def seek(self, index):
        if self.pending:
            self.queued_index = index; return
        if self.times:
            controls = self.contact_controls
            self.seek_request = self.command('replay_seek', index=index, contact_view=controls.show_contacts.isChecked(),
                                             camera=controls.camera.currentData(), **controls.options())
            self.pending = self.seek_request is not None

    def accept(self, op, message):
        if op not in ('replay_open','replay_seek','records_list','replay_close'): return
        if op == 'replay_seek' and message['id'] != self.seek_request: return
        if not message['ok']:
            self.pending = False; self.queued_index = None; self.stop_play(); return
        result = message['result']
        if op == 'records_list': self.set_records(result)
        elif op in ('replay_open','replay_seek'):
            self.pending = False; self.seek_request = None
            if op == 'replay_open':
                self.contact_controls.set_active(False)
                self.contact_controls.setVisible(result.get('can_view_contacts', False))
            if not result['supported']:
                self.packet = result; self.times = []; self.image.clear(); self.slider.setRange(0,0)
                self._size_record_table(False)
                set_text(self.timeline, 'v1 缺少逐帧全场景状态：仅浏览原始结果，不提供场景回放')
            else:
                if result.get('times') is not None: self.times = result['times']
                if self.queued_index is not None:
                    index = self.queued_index; self.queued_index = None
                    self.seek(index)
                    return
                self.packet = result; self.show_frame(result)

    def show_frame(self, packet):
        if not packet.get('supported'): return
        self._size_record_table(packet.get('contact_view', False))
        pixmap = QPixmap(); pixmap.loadFromData(base64.b64decode(packet['jpeg']), 'JPEG')
        self.image.set_source(pixmap)
        self.slider.blockSignals(True); self.slider.setRange(0,packet['count']-1); self.slider.setValue(packet['index']); self.slider.blockSignals(False)
        self.last_index = packet['index']
        contacts = packet.get('rendered_contacts')
        self.contact_controls.show_sample(contacts, '初始 P2 行没有求解器接触样本')
        template = ('接触采样状态（重建画面）：{index} / {count} · t={time:.3f}s · {phase}\n{record_id}'
                    if contacts is not None else '状态查看（重建 RGB）：{index} / {count} · t={time:.2f}s · {phase}\n{record_id}')
        set_text(self.timeline, template,
                 index=packet['index']+1, count=packet['count'], time=packet['rendered_time_s'], phase=tr(packet['phase']),
                 record_id=packet['record']['id'])

    def stop_play(self):
        self.playing = False; self.timer.stop(); set_text(self.play_button, '播放状态序列')

    def toggle_play(self):
        if self.playing: self.stop_play(); return
        if not self.times: return
        if self.last_index == len(self.times)-1: self.last_index = 0
        self.playing = True; self.started_wall = time.monotonic(); self.started_time = self.times[self.last_index]
        set_text(self.play_button, '暂停状态序列'); self.timer.start()

    def advance(self):
        if self.pending or not self.times: return
        target = self.started_time+time.monotonic()-self.started_wall
        index = min(len(self.times)-1, bisect.bisect_right(self.times,target)-1)
        self.seek(index)
        if index == len(self.times)-1: self.stop_play()
