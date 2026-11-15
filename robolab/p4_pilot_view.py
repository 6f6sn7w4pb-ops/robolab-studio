"""Read-only view of the pilot index, separate from collecting data."""
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout

from .i18n import language_manager, set_text, tr
from .p1_panel import open_directory


PILOT_INDEX = 'robolab.p4_pilot_index.v1'
OSC_INDEX = 'robolab.p4_osc_audit.v1'
SPLITS = {'train':'训练划分', 'validation':'验证划分', 'test_unseen':'未见形状族', 'control':'物理对照',
          'osc_check':'OSC 闭环'}
PARTS = {'thick':'粗端', 'thin':'细端'}
STATE = {'recording':'采集中', 'finalized':'已结束', 'cancelled':'已取消', 'verification_error':'校验出错'}


def read_pilot_index(path):
    with path.open('rb') as stream:
        payload = stream.read(1_000_001)
    if len(payload) > 1_000_000:
        raise ValueError('P4 pilot index exceeds 1 MB')
    data = json.loads(payload)
    if data.get('schema') not in (PILOT_INDEX, OSC_INDEX) or data.get('status') not in STATE:
        raise ValueError('Invalid P4 results index')
    runs = data.get('runs')
    if not isinstance(runs, list) or len(runs) > 25:
        raise ValueError('Invalid P4 pilot runs')
    for run in runs:
        if (run['split'] not in SPLITS or run['part'] not in PARTS
                or type(run['files_verified']) is not bool
                or Path(run['path']).is_absolute()
                or not (path.parent/run['path']).resolve().is_relative_to(path.parent.resolve())):
            raise ValueError('Invalid P4 pilot row')
        if run['files_verified'] and type(run.get('outcome', {}).get('passed')) is not bool:
            raise ValueError('Verified P4 pilot row lacks a physical verdict')
        if any(part not in PARTS for part in run.get('outcome', {}).get('held_parts', [])):
            raise ValueError('Invalid P4 pilot held part')
    return data


class PilotSummary(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self.data = read_pilot_index(path)
        self.is_osc = self.data['schema'] == OSC_INDEX
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(860, 550)
        layout = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QTreeWidget()
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.detail)
        row = QHBoxLayout()
        row.addStretch()
        self.open_record = QPushButton()
        self.open_record.clicked.connect(self.open_selected)
        row.addWidget(self.open_record)
        layout.addLayout(row)
        self.table.itemSelectionChanged.connect(self.show_detail)
        language_manager().changed.connect(self.refresh)
        self.refresh()

    def refresh(self, *_):
        set_text(self, 'P4 OSC 验收' if self.is_osc else 'P4 试采汇总', method='setWindowTitle')
        set_text(self.open_record, '打开结果目录')
        runs = self.data['runs']
        indexed = [run for run in runs if run['split'] != 'control']
        successes = sum(run['files_verified'] and run.get('outcome', {}).get('passed') is True for run in indexed)
        controls = len(runs)-len(indexed)
        template = ('OSC 验收状态：{state} · 任务通过 {passed}/{total} · 独立物理对照 {controls} 条'
                    if self.is_osc else '试采状态：{state} · 任务通过 {passed}/{total} · 独立物理对照 {controls} 条')
        set_text(self.status, template,
                 state=tr(STATE[self.data['status']]), passed=successes, total=len(indexed), controls=controls)
        self.table.setHeaderLabels([tr('场景'), tr('用途' if self.is_osc else '划分'), tr('指定部位'),
                                    tr('实际夹持'), tr('结果'), tr('双 RGB 对数')])
        for index, size in enumerate((175, 125, 90, 105, 110, 85)):
            self.table.setColumnWidth(index, size)
        selection = self.table.indexOfTopLevelItem(self.table.currentItem())
        self.table.clear()
        for run in runs:
            outcome = run.get('outcome', {})
            held = ', '.join(tr(PARTS[part]) for part in outcome.get('held_parts', [])) or '—'
            verdict = ('通过' if outcome.get('passed') is True else '未通过') if run['files_verified'] else '未校验'
            item = QTreeWidgetItem([run['scene_id'], tr(SPLITS[run['split']]), tr(PARTS[run['part']]), held,
                                   tr(verdict), str(run.get('rgb_pairs', '—'))])
            item.setData(0, Qt.ItemDataRole.UserRole, run)
            self.table.addTopLevelItem(item)
        if runs:
            self.table.setCurrentItem(self.table.topLevelItem(max(0, selection)))
        self.show_detail()

    def selected(self):
        item = self.table.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def show_detail(self):
        run = self.selected()
        self.open_record.setEnabled(run is not None)
        if run is None:
            self.detail.clear()
        else:
            text = run.get('instruction', {}).get(language_manager().language, '')+'\n'+run['path']
            replay = run.get('replay')
            if self.is_osc and run['files_verified'] and replay:
                text += '\n'+tr('已复执行 {ticks} 个 OSC 动作 · 最大 qpos 差 {error:g}',
                                 ticks=replay['ticks'], error=replay['max_qpos_difference'])
            self.detail.setText(text)

    def open_selected(self):
        run = self.selected()
        if run:
            directory = (self.path.parent/run['path']).resolve()
            if directory.is_relative_to(self.path.parent.resolve()) and directory.is_dir():
                open_directory(directory)
