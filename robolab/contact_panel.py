"""Qt controls for live and recorded contact views."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .i18n import language_manager, set_text


class ContactControls(QWidget):
    changed = Signal()

    def __init__(self, *, historical=False):
        super().__init__()
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.show_contacts = QCheckBox('P3 接触采样状态' if historical else 'P3 实时接触预览')
        row.addWidget(self.show_contacts)
        self.filter = QComboBox()
        for name, value in (('目标', 'target'), ('环境与干扰物', 'obstacle'), ('指垫', 'pads'), ('所有手指接触', 'all')):
            self.filter.addItem(name, value)
        row.addWidget(self.filter)
        self.forces = QCheckBox('力箭头')
        row.addWidget(self.forces)
        self.camera = None
        if historical:
            self.camera = QComboBox()
            for name, value in (('桌面相机', 'agentview'), ('腕部相机', 'robot0_eye_in_hand'), ('半透明近景', 'gripper')):
                self.camera.addItem(name, value)
            self.camera.currentIndexChanged.connect(lambda _: self.changed.emit())
            row.addWidget(self.camera)
        row.addStretch()
        box.addLayout(row)
        self.info = QLabel()
        self.info.setWordWrap(True)
        box.addWidget(self.info)
        self.show_contacts.setToolTip('接触点与法向：橙色指 1，蓝色指 2。夹爪 XYZ 为红绿蓝。')
        self.forces.setToolTip('紫色为原始模拟接触力方向，长度按 3 mm/N 缩放，上限 70 mm。')
        language_manager().localize_tree(self)
        self.show_contacts.toggled.connect(self._toggled)
        self.filter.currentIndexChanged.connect(lambda _: self.changed.emit())
        self.forces.toggled.connect(lambda _: self.changed.emit())
        self.set_active(False)

    def _toggled(self, checked):
        self.set_active(checked)
        self.changed.emit()

    def set_active(self, checked):
        self.show_contacts.blockSignals(True)
        self.show_contacts.setChecked(checked)
        self.show_contacts.blockSignals(False)
        for control in (self.filter, self.forces, self.camera):
            if control is not None:
                control.setEnabled(checked)
        self.info.setVisible(checked)

    def options(self):
        return dict(contact_filter=self.filter.currentData(), forces=self.forces.isChecked())

    def show_sample(self, sample, message):
        if sample is None:
            set_text(self.info, message)
            return
        set_text(self.info, '原生求解器 t={solver:.3f} s · 积分后 t={end:.3f} s · 显示 {drawn}/{total} 点 · 最大法向力 {force:.2f} N',
                 solver=sample['solver_time_s'], end=sample['end_time_s'], drawn=sample['drawn'],
                 total=sample['total'], force=sample['max_normal_force_N'])

