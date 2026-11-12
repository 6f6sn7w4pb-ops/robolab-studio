"""Read-only desktop view of a P5 closed-loop evaluation summary with keyframe playback."""
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QSplitter, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .i18n import language_manager, set_text, tr
from .p1_panel import ImageView, open_directory


SUMMARY_SCHEMA = 'robolab.p5_eval_summary.v1'
MAX_BYTES = 8_000_000
MAX_RUNS = 1000
SPLITS = {'train': '训练划分', 'validation': '验证划分', 'test_instance': '已见形状族新实例',
          'test_unseen': '未见形状族', 'control': '物理对照'}
PARTS = {'thick': '粗端', 'thin': '细端'}
PHRASING = {'seen': '已见说法', 'held_out': '未见说法'}
STATE = {'finalized': '已结束', 'incomplete': '未完成', 'cancelled': '已取消'}
RATES = (('success', '成功率'), ('part_hit', '部位命中率'), ('lifted', '抬起率'), ('collision', '碰撞率'))
CAMERAS = ('agentview', 'robot0_eye_in_hand')
SCENE_SETS = {'dataset': '数据集测试场景', 'extended': '扩展测试场景'}
APPEARANCE = {'lighting': '光照', 'background': '背景', 'objects': '物体颜色'}


def read_eval_summary(path):
    with Path(path).open('rb') as stream:
        payload = stream.read(MAX_BYTES+1)
    if len(payload) > MAX_BYTES:
        raise ValueError('P5 evaluation summary is too large')
    data = json.loads(payload)
    if data.get('schema') != SUMMARY_SCHEMA or data.get('status') not in STATE:
        raise ValueError('Invalid P5 evaluation summary')
    runs = data.get('runs')
    if not isinstance(runs, list) or len(runs) > MAX_RUNS:
        raise ValueError('Invalid P5 evaluation runs')
    root = Path(path).parent.resolve()
    for run in runs:
        if (run['split'] not in SPLITS or run['part'] not in PARTS or run['phrasing'] not in PHRASING
                or type(run['metrics'].get('success')) is not bool or Path(run['path']).is_absolute()
                or not (root/run['path']).resolve().is_relative_to(root)):
            raise ValueError('Invalid P5 evaluation row')
        for frame in run.get('keyframes', []):
            for name in frame['files'].values():
                if Path(name).is_absolute() or not (root/run['path']/name).resolve().is_relative_to(root):
                    raise ValueError('Invalid P5 keyframe path')
    return data


def rate_text(stat, total=None):
    """'count/total · rate% [CI]'; the denominator is explicit so a zero rate keeps it."""
    if not stat or stat.get('rate') is None:
        return '—'
    low, high = stat['ci95']
    total = stat.get('of', total)
    fraction = f"{stat['count']}/{total}" if total is not None else str(stat['count'])
    return f"{fraction} · {100*stat['rate']:.0f}% [{100*low:.0f}–{100*high:.0f}]"


class EvalSummary(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.root = self.path.parent.resolve()
        self.data = read_eval_summary(self.path)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(1180, 760)
        layout = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.scene_line = QLabel()
        self.scene_line.setWordWrap(True)
        layout.addWidget(self.scene_line)
        self.groups = QTreeWidget()
        self.groups.setRootIsDecorated(False)
        self.groups.setMaximumHeight(170)
        layout.addWidget(self.groups)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTreeWidget()
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        splitter.addWidget(self.table)
        preview = QWidget()
        column = QVBoxLayout(preview)
        column.setContentsMargins(0, 0, 0, 0)
        self.views = {}
        for camera in CAMERAS:
            view = ImageView()
            view.setObjectName(camera+'_keyframe')
            self.views[camera] = view
            column.addWidget(view, 1)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.valueChanged.connect(self.show_keyframe)
        column.addWidget(self.slider)
        self.frame_label = QLabel()
        column.addWidget(self.frame_label)
        splitter.addWidget(preview)
        splitter.setSizes([720, 420])
        layout.addWidget(splitter, 1)
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
        data = self.data
        set_text(self, 'P5 闭环评估', method='setWindowTitle')
        set_text(self.open_record, '打开结果目录')
        policy = data['policy']
        kind = ('真实 VLA' if data.get('is_vla') else
                '特权规则教师（读取仿真真值，仅作参照）' if policy.get('privileged') else '非 VLA 接口自检')
        checks = data.get('harness_checks', {})

        def check(value):
            return tr('通过') if value is True else tr('未通过') if value is False else '—'

        set_text(self.status, '评估状态：{state} · 策略 {policy}（{kind}）· 已完成 {finished}/{planned} · '
                 '复跑一致 {recheck} · 与数据集逐帧一致 {reference}', state=tr(STATE[data['status']]),
                 policy=policy['policy_id'], kind=tr(kind), finished=data['finished'], planned=data['planned'],
                 recheck=check(checks.get('recheck_identical')), reference=check(checks.get('reference_identical')))
        conditions = data.get('conditions') or {}
        appearance = ' + '.join(tr(APPEARANCE.get(name, name)) for name in conditions.get('appearance') or []) or tr('无')
        scene_set = conditions.get('scene_set', 'dataset')
        set_text(self.scene_line, '场景条件：{scene_set} · 圆柱防滚 {anti_roll} · 外观变化 {appearance} · 与数据集物理一致 {physics}',
                 scene_set=tr(SCENE_SETS.get(scene_set, scene_set)),
                 anti_roll=tr('开' if conditions.get('anti_roll') else '关'), appearance=appearance,
                 physics=check(checks.get('reference_physics_identical')))
        self.groups.setHeaderLabels([tr('分组'), tr('条数')]+[tr(label) for _, label in RATES]+[tr('接触时部位正确率')])
        for index, size in enumerate((190, 55, 150, 150, 150, 150, 160)):
            self.groups.setColumnWidth(index, size)
        self.groups.clear()
        results = data.get('results') or {}
        rows = []
        if results:
            rows.append((tr('全部测试'), results['overall']))
            rows += [(tr(SPLITS.get(name, name)), stat) for name, stat in results['by_split'].items()]
            for name, stat in results.get('by_split_phrasing', {}).items():
                split, phrasing = name.split('/')
                rows.append((tr(SPLITS.get(split, split))+' · '+tr(PHRASING[phrasing]), stat))
        if data.get('controls'):
            rows.append((tr('物理对照'), data['controls']))
        for label, stat in rows:
            values = [label, str(stat['episodes'])]+[rate_text(stat[key], stat['episodes']) for key, _ in RATES]
            values.append(rate_text(stat['part_accuracy_given_contact']))
            self.groups.addTopLevelItem(QTreeWidgetItem(values))
        self.table.setHeaderLabels([tr('场景'), tr('划分'), tr('指定部位'), tr('说法'), tr('结果'),
                                    tr('部位命中'), tr('碰撞')])
        for index, size in enumerate((250, 130, 70, 80, 70, 70, 60)):
            self.table.setColumnWidth(index, size)
        selection = self.table.indexOfTopLevelItem(self.table.currentItem())
        self.table.clear()
        for run in data['runs']:
            metrics = run['metrics']
            item = QTreeWidgetItem([run['scene_id'], tr(SPLITS[run['split']]), tr(PARTS[run['part']]),
                                    tr(PHRASING[run['phrasing']]), tr('成功' if metrics['success'] else '失败'),
                                    tr('是' if metrics['part_hit'] else '否'), tr('是' if metrics['collision'] else '否')])
            item.setData(0, Qt.ItemDataRole.UserRole, run)
            self.table.addTopLevelItem(item)
        if data['runs']:
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
            self.slider.setRange(0, 0)
            return
        metrics = run['metrics']
        text = run['instruction'].get(language_manager().language, '')+'\n'+run['path']
        if metrics['failure_reasons']:
            text += '\n'+tr('未通过原因：{reasons}', reasons=', '.join(metrics['failure_reasons']))
        held = ', '.join(tr(PARTS[part]) for part in run['outcome']['held_parts']) or '—'
        text += '\n'+tr('实际夹持 {held} · 最大抬升 {lift:.3f} m · 障碍物最大位移 {moved:.4f} m',
                        held=held, lift=run['outcome']['max_lift_m'], moved=metrics['obstacle_max_displacement_m'])
        if run.get('reference_identical') is not None:
            text += '\n'+tr('与数据集演示逐帧一致：{value}', value=tr('是' if run['reference_identical'] else '否'))
        if run.get('error'):
            text += '\n'+tr('策略或仿真出错：{message}', message=run['error']['message'])
        self.detail.setText(text)
        frames = run.get('keyframes', [])
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, len(frames)-1))
        self.slider.setValue(max(0, len(frames)-1))
        self.slider.blockSignals(False)
        self.show_keyframe()

    def show_keyframe(self, *_):
        run = self.selected()
        frames = run.get('keyframes', []) if run else []
        if not frames:
            for view in self.views.values():
                view.clear('没有关键帧')
            self.frame_label.clear()
            return
        frame = frames[self.slider.value()]
        for camera, view in self.views.items():
            view.set_pixmap(QPixmap(str(self.root/run['path']/frame['files'][camera])))
        part = tr(PARTS[frame['actual_part']]) if frame.get('actual_part') else '—'
        self.frame_label.setText(tr('第 {tick} 步（{seconds:.2f} s）· 当前双指部位 {part} · 抬升 {lift:.3f} m',
                                    tick=frame['tick'], seconds=frame['tick']*.05, part=part, lift=frame['lift_m']))

    def open_selected(self):
        run = self.selected()
        if run:
            directory = (self.root/run['path']).resolve()
            if directory.is_relative_to(self.root) and directory.is_dir():
                open_directory(directory)
