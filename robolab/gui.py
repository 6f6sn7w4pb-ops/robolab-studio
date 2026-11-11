"""Native Qt desktop workstation with an embedded, interactive MuJoCo view."""
import base64
import math
import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer, QRectF, QProcess
from PySide6.QtGui import QAction, QColor, QFont, QFontDatabase, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QSplitter, QTreeWidget, QTreeWidgetItem, QTabWidget, QFormLayout,
    QDoubleSpinBox, QCheckBox, QSlider, QPlainTextEdit, QFileDialog, QToolBar, QFrame, QScrollArea,
    QLineEdit, QSpinBox, QProgressBar, QGridLayout)

from .client import BackendClient
from .config import ROOT, DEFAULT_PROJECTS
from .p1_panel import DualCameraView, P1TestPanel, desktop_path, open_directory
from .i18n import language_manager, set_text, tr
from .p2_browser import P2Browser
from .contact_panel import ContactControls
from .p4_panel import PartGraspPanel

STYLE = '''
QMainWindow, QWidget { background: #f3f5f8; color: #18283e; font-size: 13px; }
QMenuBar, QMenu, QToolBar { background: #ffffff; }
QToolBar { border-bottom: 1px solid #dce2eb; spacing: 8px; padding: 8px; }
QPushButton { background: white; border: 1px solid #cdd6e3; border-radius: 5px; padding: 7px 12px; }
QPushButton:hover { border-color: #3274ce; background: #edf4ff; }
QPushButton:disabled { color: #9aa6b6; background: #eef0f4; }
QPushButton#primary { background: #2267c7; color: white; border: none; }
QLabel#brand { font-size: 21px; font-weight: 700; color: #17345b; padding: 9px 4px; }
QLabel#muted { color: #66768c; font-size: 12px; }
QLabel#panelTitle { font-size: 14px; font-weight: 650; padding: 9px 1px; }
QLabel#error { background: #fff1ea; color: #a03418; padding: 9px; border-radius: 4px; }
QTreeWidget, QPlainTextEdit { background: #fff; border: 1px solid #dce2eb; border-radius: 5px; }
QTreeWidget::item { padding: 4px 1px; }
QTreeWidget::item:selected { background: #e4edff; color: #17345b; }
QComboBox, QDoubleSpinBox { background: white; border: 1px solid #ccd5e2; padding: 5px; border-radius: 4px; }
QTabWidget::pane { border: 1px solid #dce2eb; background: white; }
QTabBar::tab { background: #e8edf4; padding: 9px 14px; }
QTabBar::tab:selected { background: white; color: #2267c7; }
QSlider::groove:horizontal { height: 5px; background: #d9e1ec; border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; margin: -4px 0; background: #2267c7; border-radius: 6px; }
QStatusBar { background: white; color: #53657b; border-top: 1px solid #dce2eb; }
'''

def label(text, name=None, literal=False):
    value = QLabel(text)
    value.setProperty('i18n_literal_text',literal)
    if name: value.setObjectName(name)
    return value

class Viewport(QWidget):
    camera = Signal(str, float, float)
    picked = Signal(float, float)
    focused = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName('viewport')
        self.setMinimumSize(420, 340)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.pixmap = QPixmap()
        self.rect_image = QRectF()
        self.anchor = self.previous = None
        self.button = None
        self.dragged = False
        self.pending_camera = None
        self.timer = QTimer(self); self.timer.setInterval(32); self.timer.timeout.connect(self.flush_camera)
        self.timer.start()

    def set_frame(self, jpeg):
        pixmap = QPixmap()
        if pixmap.loadFromData(base64.b64decode(jpeg), 'JPEG'):
            self.pixmap = pixmap
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#182333'))
        if self.pixmap.isNull():
            painter.setPen(QColor('#d5e0f0'))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr('等待 MuJoCo 三维场景…\n首次加载可能需要约一分钟'))
            return
        ratio = min(self.width()/self.pixmap.width(), self.height()/self.pixmap.height())
        w, h = self.pixmap.width()*ratio, self.pixmap.height()*ratio
        self.rect_image = QRectF((self.width()-w)/2, (self.height()-h)/2, w, h)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(self.rect_image, self.pixmap, QRectF(self.pixmap.rect()))

    def mousePressEvent(self, event):
        self.anchor = self.previous = event.position()
        self.button = event.button(); self.dragged = False

    def mouseMoveEvent(self, event):
        if self.previous is None or not self.button or self.pixmap.isNull(): return
        point = event.position(); delta = point-self.previous
        if (point-self.anchor).manhattanLength() > 4: self.dragged = True
        self.previous = point
        action = 'orbit' if self.button == Qt.MouseButton.LeftButton else 'pan'
        height = max(1, self.rect_image.height())
        dx, dy = delta.x()/height, delta.y()/height
        if self.pending_camera and self.pending_camera[0] == action:
            _, x, y = self.pending_camera; self.pending_camera = (action, x+dx, y+dy)
        else:
            self.flush_camera(); self.pending_camera = (action, dx, dy)

    def flush_camera(self):
        if self.pending_camera:
            self.camera.emit(*self.pending_camera); self.pending_camera = None

    def mouseReleaseEvent(self, event):
        self.flush_camera()
        if not self.dragged and self.button == Qt.MouseButton.LeftButton and self.rect_image.contains(event.position()):
            p = event.position()
            self.picked.emit((p.x()-self.rect_image.left())/self.rect_image.width(),
                             (p.y()-self.rect_image.top())/self.rect_image.height())
        self.previous, self.button = None, None

    def mouseDoubleClickEvent(self, event): self.focused.emit()

    def wheelEvent(self, event):
        if not self.pixmap.isNull(): self.camera.emit('zoom', 0, event.angleDelta().y()/120)
        event.accept()

class MainWindow(QMainWindow):
    def __init__(self, auto_load=True):
        super().__init__()
        self.setWindowTitle('RoboLab Studio 0.2 · 本地机器人仿真工作站')
        self.resize(1510, 970)
        self.setMinimumSize(1180, 760)
        self.auto_load = auto_load
        self.state = {'loaded': False, 'running': False, 'mode': 'edit', 'time': 0}
        self.scene_data = None
        self.selected_info = None
        self.busy = False
        self.closing_window = False
        self.project_path = None
        self.pending_joints = {}
        self.pending_gripper = None
        self.joint_sliders, self.joint_labels = [], []
        self.frame_count = 0
        self.camera_frame_count = 0
        self.live_cameras_enabled = True
        self._camera_requested = False
        self.last_reply = {}
        self.tasks = []
        (ROOT/'logs').mkdir(exist_ok=True)
        self.log_stream = (ROOT/'logs'/time.strftime('studio-%Y%m%d-%H%M%S.log')).open('a', encoding='utf-8')
        self.client = BackendClient(self)
        self.client.message.connect(self.on_message)
        self.client.diagnostic.connect(self.log)
        self.client.disconnected.connect(self.on_disconnected)
        self._build()
        self.i18n=language_manager()
        self.i18n.localize_tree(self)
        self.i18n.changed.connect(self._language_changed)
        self._language_changed(self.i18n.language)
        self.joint_timer = QTimer(self); self.joint_timer.setSingleShot(True); self.joint_timer.setInterval(70)
        self.joint_timer.timeout.connect(self.flush_joints)
        self._update_enabled()
        QTimer.singleShot(0, self.client.start)

    def _build(self):
        files = self.menuBar().addMenu('文件')
        for title, shortcut, callback in [('打开项目…','Ctrl+O',self.open_dialog),('保存项目','Ctrl+S',self.save_dialog),
            ('另存为…','Ctrl+Shift+S',lambda:self.save_dialog(True)),('导出场景截图…','Ctrl+E',self.screenshot_dialog)]:
            action = QAction(title,self); action.setShortcut(shortcut); action.triggered.connect(callback); files.addAction(action)
        files.addSeparator(); files.addAction('退出', self.close)
        help_menu = self.menuBar().addMenu('帮助')
        help_menu.addAction('查看操作提示', lambda:self.log('左键拖动旋转；右键/中键拖动平移；滚轮缩放；单击选取；双击聚焦。编辑态修改布局，仿真态关节滑块设置真实伺服目标。'))
        help_menu.addAction('重启仿真后端', self.restart_backend)
        verify_menu = self.menuBar().addMenu('验证')
        verify_menu.addAction('双相机实时查看', lambda: self.view_tabs.setCurrentIndex(1))
        verify_menu.addAction('P1 离屏自检', lambda: self.view_tabs.setCurrentIndex(2))
        verify_menu.addAction('P4 部位抓取', lambda: self.view_tabs.setCurrentWidget(self.p4_panel))
        toolbar = QToolBar(); toolbar.setMovable(False); self.addToolBar(toolbar)
        toolbar.addWidget(label('RoboLab Studio', 'brand'))
        toolbar.addSeparator()
        self.buttons = {}
        for op,title in [('play','▶ 启动物理仿真'),('pause','Ⅱ 暂停'),('stop','■ 停止'),('reset','重置'),('step','单步 +20ms')]:
            btn=QPushButton(title); btn.setObjectName(op); btn.clicked.connect(lambda checked=False,o=op:self.command(o))
            toolbar.addWidget(btn); self.buttons[op]=btn
        self.buttons['play'].setToolTip('只推进物理时间与关节伺服。自动动作请在“策略 / 自检”中执行。')
        toolbar.addSeparator()
        self.mode = QComboBox(); self.mode.addItem('编辑模式','edit'); self.mode.addItem('仿真模式','simulate')
        self.mode.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.mode.setMinimumContentsLength(15)
        self.mode.currentIndexChanged.connect(lambda:self.command('mode',mode=self.mode.currentData()))
        toolbar.addWidget(self.mode)
        self.time_label = label('t = 0.000 s'); toolbar.addWidget(self.time_label)
        self.language_button=QPushButton('English'); self.language_button.setObjectName('language_toggle')
        self.language_button.setProperty('i18n_skip',True)
        self.language_button.clicked.connect(lambda: self.i18n.set_language('en' if self.i18n.language=='zh' else 'zh'))
        toolbar.addSeparator(); toolbar.addWidget(self.language_button)
        central=QWidget(); outer=QVBoxLayout(central); outer.setContentsMargins(14,10,14,8)
        self.error_banner=label('', 'error'); self.error_banner.setWordWrap(True); self.error_banner.hide(); outer.addWidget(self.error_banner)
        split=QSplitter(Qt.Orientation.Horizontal); outer.addWidget(split,1)
        left=QWidget(); ll=QVBoxLayout(left); ll.setContentsMargins(0,0,8,0)
        ll.addWidget(label('任务库', 'panelTitle'))
        self.suite_combo=QComboBox(); self.suite_combo.addItems(['libero_object','libero_spatial','libero_goal','libero_10'])
        self.suite_combo.currentTextChanged.connect(self.change_suite); ll.addWidget(self.suite_combo)
        self.task_combo=QComboBox(); self.task_combo.setObjectName('task_combo'); self.task_combo.setMinimumWidth(190)
        ll.addWidget(self.task_combo)
        self.task_description=label('读取本机 LIBERO 任务列表…','muted'); self.task_description.setWordWrap(True); ll.addWidget(self.task_description)
        self.task_combo.currentIndexChanged.connect(self.task_description_changed)
        self.load_button=QPushButton('加载任务场景'); self.load_button.setObjectName('primary'); self.load_button.clicked.connect(self.load_task); ll.addWidget(self.load_button)
        ll.addWidget(label('场景树', 'panelTitle'))
        self.tree=QTreeWidget(); self.tree.setObjectName('scene_tree'); self.tree.setHeaderHidden(True); self.tree.itemClicked.connect(self.select_tree)
        ll.addWidget(self.tree,1)
        ll.addWidget(label('添加基础物体', 'panelTitle'))
        addrow=QHBoxLayout()
        self.add_buttons=[]
        for kind,title in [('box','立方体'),('sphere','球'),('cylinder','圆柱')]:
            b=QPushButton(title); b.setObjectName('add_'+kind); b.clicked.connect(lambda checked=False,k=kind:self.command('add',kind=k)); addrow.addWidget(b); self.add_buttons.append(b)
        ll.addLayout(addrow)
        note=label('蓝色物体由编辑器创建。\nLIBERO 物体可调整位姿；原始资产受到保护。','muted'); note.setWordWrap(True); ll.addWidget(note)
        split.addWidget(left)
        middle=QWidget(); middle_layout=QVBoxLayout(middle); middle_layout.setContentsMargins(4,0,4,0)
        self.view_tabs=QTabWidget(); middle_layout.addWidget(self.view_tabs)
        scene_page=QWidget(); ml=QVBoxLayout(scene_page); ml.setContentsMargins(4,0,4,0)
        viewtop=QHBoxLayout(); viewtop.addWidget(label('三维场景 · MuJoCo', 'panelTitle')); viewtop.addStretch()
        self.axes_box=QCheckBox('坐标轴'); self.axes_box.setChecked(True); self.axes_box.toggled.connect(lambda value:self.command('axes',visible=value)); viewtop.addWidget(self.axes_box)
        home=QPushButton('默认视角'); home.clicked.connect(lambda:self.command('camera',action='home')); viewtop.addWidget(home)
        focus=QPushButton('聚焦选择'); focus.clicked.connect(lambda:self.command('camera',action='focus')); viewtop.addWidget(focus)
        ml.addLayout(viewtop)
        self.viewport=Viewport(); self.viewport.camera.connect(lambda action,dx,dy:self.command('camera',action=action,dx=dx,dy=dy)); self.viewport.picked.connect(lambda x,y:self.command('pick',x=x,y=y)); self.viewport.focused.connect(lambda:self.command('camera',action='focus'))
        ml.addWidget(self.viewport,1)
        self.contact_controls = ContactControls()
        self.contact_controls.changed.connect(lambda: self.command(
            'contact_view', enabled=self.contact_controls.show_contacts.isChecked(), **self.contact_controls.options()))
        ml.addWidget(self.contact_controls)
        ml.addWidget(label('左键拖动 旋转    右键/中键 平移    滚轮 缩放    单击 选取    双击 聚焦','muted'))
        self.scene_status=label('正在连接本机仿真进程…','muted'); ml.addWidget(self.scene_status)
        self.execution_status=label('任务执行：未启动。物理仿真与策略执行分开控制。','muted'); self.execution_status.setWordWrap(True); ml.addWidget(self.execution_status)
        self.view_tabs.addTab(scene_page,'三维场景')
        camera_page=QWidget(); camera_layout=QVBoxLayout(camera_page)
        camera_note=label('当前编辑器场景的同步双 RGB，最高 5 Hz。\n暂停物理仿真时仍可查看；相机刷新不会执行策略或推进时间。','muted')
        camera_note.setWordWrap(True); camera_layout.addWidget(camera_note)
        self.camera_refresh_button=QPushButton('暂停相机刷新'); self.camera_refresh_button.clicked.connect(self.toggle_camera_refresh)
        camera_layout.addWidget(self.camera_refresh_button)
        self.live_cameras=DualCameraView(); camera_layout.addWidget(self.live_cameras,1)
        self.view_tabs.addTab(camera_page,'双相机 · 实时')
        self.p1_panel=P1TestPanel(); self.view_tabs.addTab(self.p1_panel,'P1 离屏自检')
        self.p1_panel.diagnostic.connect(self.log)
        self.p1_panel.completed.connect(lambda _: QTimer.singleShot(0,self.close) if self.closing_window else None)
        self.view_tabs.currentChanged.connect(self._sync_camera_stream)
        self.p2_browser = P2Browser(self.command)
        self.view_tabs.addTab(self.p2_browser, 'P2 记录 / 状态查看')
        self.view_tabs.currentChanged.connect(lambda index: self.p2_browser.reload() if index == 3 else self.p2_browser.stop_play())
        self.p4_panel = PartGraspPanel()
        self.view_tabs.addTab(self.p4_panel, 'P4 部位抓取')
        self.p4_panel.completed.connect(lambda _: QTimer.singleShot(0,self.close) if self.closing_window else None)
        split.addWidget(middle)
        self.tabs=QTabWidget()
        self.properties_scroll=QScrollArea(); self.properties_scroll.setWidgetResizable(True); self.properties_scroll.setMinimumWidth(300)
        self.tabs.addTab(self.properties_scroll,'物体属性')
        self.robot_widget=QWidget(); self.robot_layout=QVBoxLayout(self.robot_widget); self.robot_layout.setContentsMargins(12,12,12,12)
        self.robot_layout.addWidget(label('Panda · 7 自由度', 'panelTitle'))
        self.robot_note=label('编辑模式：设置关节姿态。\n仿真模式：发送限速伺服目标。','muted'); self.robot_note.setWordWrap(True); self.robot_layout.addWidget(self.robot_note)
        for i in range(7):
            lbl=label(f'J{i+1}  等待关节状态…'); self.robot_layout.addWidget(lbl); self.joint_labels.append(lbl)
            slider=QSlider(Qt.Orientation.Horizontal); slider.setObjectName(f'joint_{i}'); slider.valueChanged.connect(lambda v,j=i:self.queue_joint(j,v)); self.robot_layout.addWidget(slider); self.joint_sliders.append(slider)
        self.robot_layout.addWidget(label('夹爪开合 · 闭合 0% / 打开 100%', 'panelTitle'))
        self.gripper_slider=QSlider(Qt.Orientation.Horizontal); self.gripper_slider.setObjectName('gripper'); self.gripper_slider.setRange(0,100); self.gripper_slider.setValue(100)
        self.gripper_slider.valueChanged.connect(self.queue_gripper); self.robot_layout.addWidget(self.gripper_slider)
        self.eef_label=label('末端位姿：等待数据','muted'); self.eef_label.setWordWrap(True); self.robot_layout.addWidget(self.eef_label)
        self.robot_layout.addWidget(label('末端 XYZ 点动 · 世界坐标 / mm', 'panelTitle'))
        self.jog_step = QDoubleSpinBox(); self.jog_step.setRange(1,10); self.jog_step.setDecimals(1); self.jog_step.setValue(5); self.jog_step.setSuffix(' mm'); self.robot_layout.addWidget(self.jog_step)
        row=QGridLayout(); self.jog_buttons=[]
        for axis in range(3):
            for direction in (-1,1):
                button=QPushButton('XYZ'[axis]+('−' if direction<0 else '+')); button.setObjectName(f'jog_{axis}_{direction}')
                button.clicked.connect(lambda checked=False,a=axis,d=direction:self.jog_axis(a,d)); row.addWidget(button,0 if direction<0 else 1,axis); self.jog_buttons.append(button)
        self.robot_layout.addLayout(row)
        self.jog_status=label('保持当前末端姿态；编辑态更新基准，仿真态需运行或单步。仅检查运动学可达性，不保证无碰撞。','muted'); self.jog_status.setWordWrap(True); self.robot_layout.addWidget(self.jog_status)
        self.jog_feedback=label('点动目标状态：空闲','muted'); self.robot_layout.addWidget(self.jog_feedback)
        self.robot_layout.addStretch()
        robot_scroll=QScrollArea(); robot_scroll.setWidgetResizable(True); robot_scroll.setWidget(self.robot_widget); self.tabs.addTab(robot_scroll,'机械臂控制')
        self._build_policy_tab()
        split.addWidget(self.tabs)
        split.setSizes([245,880,330]); split.setStretchFactor(1,1)
        self.logs=QPlainTextEdit(); self.logs.setReadOnly(True); self.logs.setMaximumBlockCount(600); self.logs.setMaximumHeight(105); self.logs.setPlaceholderText('仿真日志与错误')
        outer.addWidget(self.logs)
        self.setCentralWidget(central)
        self.connection_label=label('连接中'); self.statusBar().addPermanentWidget(self.connection_label)
        self.show_properties(None)

    def _build_policy_tab(self):
        scroll=QScrollArea(); scroll.setWidgetResizable(True)
        panel=QWidget(); box=QVBoxLayout(panel); box.setContentsMargins(12,12,12,12)
        title=label('策略 / 轻量接入测试','panelTitle'); box.addWidget(title)
        text=label('物理仿真只推进时间。这里单独启动动作来源。\nMock 与规则自检均不是 VLA 模型。','muted'); text.setWordWrap(True); box.addWidget(text)
        self.policy_combo=QComboBox(); self.policy_combo.setObjectName('policy_adapter')
        self.policy_combo.addItem('Mock Policy · 无模型自检','mock'); self.policy_combo.addItem('OpenVLA-OFT · 首个模型适配器','openvla_oft'); box.addWidget(self.policy_combo)
        self.policy_combo.currentIndexChanged.connect(lambda:self._update_enabled() if hasattr(self,'run_policy_button') else None)
        self.scan_button=QPushButton('扫描本地权重（只读）'); self.scan_button.clicked.connect(lambda:self.command('policy_scan')); box.addWidget(self.scan_button)
        self.weight_status=label('尚未扫描；不会自动下载模型。','muted'); self.weight_status.setWordWrap(True); box.addWidget(self.weight_status)
        row=QHBoxLayout(); self.checkpoint_input=QLineEdit(); self.checkpoint_input.setPlaceholderText('本地模型目录（可暂空）'); row.addWidget(self.checkpoint_input)
        browse=QPushButton('选择'); browse.clicked.connect(self.choose_checkpoint); row.addWidget(browse); box.addLayout(row)
        self.endpoint_input=QLineEdit(); self.endpoint_input.setPlaceholderText('独立推理服务 http://主机:端口（可暂空）'); box.addWidget(self.endpoint_input)
        self.configure_button=QPushButton('配置 / 检查模型目录'); self.configure_button.clicked.connect(self.configure_policy); box.addWidget(self.configure_button)
        self.connect_policy_button=QPushButton('连接真实推理服务'); self.connect_policy_button.clicked.connect(lambda:self.command('policy_load')); box.addWidget(self.connect_policy_button)
        self.model_status_label=label('模型状态：仅配置 · 尚未加载真实模型','muted'); self.model_status_label.setWordWrap(True); box.addWidget(self.model_status_label)
        self.preview_button=QPushButton('单次观察 → 动作测试'); self.preview_button.clicked.connect(lambda:self.command('policy_preview')); box.addWidget(self.preview_button)
        row=QHBoxLayout(); row.addWidget(label('执行步数上限'))
        self.policy_steps=QSpinBox(); self.policy_steps.setRange(1,1000); self.policy_steps.setValue(20); row.addWidget(self.policy_steps); box.addLayout(row)
        self.run_policy_button=QPushButton('执行已配置策略'); self.run_policy_button.setObjectName('run_policy'); self.run_policy_button.clicked.connect(lambda:self.command('policy_run',max_steps=self.policy_steps.value())); box.addWidget(self.run_policy_button)
        self.stop_policy_button=QPushButton('结束策略（保留当前姿态）'); self.stop_policy_button.clicked.connect(lambda:self.command('policy_stop')); box.addWidget(self.stop_policy_button)
        self.policy_progress=QProgressBar(); self.policy_progress.setRange(0,100); self.policy_progress.setValue(0); box.addWidget(self.policy_progress)
        self.policy_detail=label('观察输入：双 RGB、8 维本体状态、任务文字。\n动作输出：LIBERO 7 维 OSC 命令，不是关节角。','muted'); self.policy_detail.setWordWrap(True); box.addWidget(self.policy_detail)
        self.record_contacts = QCheckBox('同时保存 P3 接触记录')
        box.addWidget(self.record_contacts)
        self.grasp_test_button=QPushButton('P2 规则抓取与记录（非 VLA）')
        self.grasp_test_button.clicked.connect(lambda: self.command('grasp_test', record_contacts=self.record_contacts.isChecked()))
        box.addWidget(self.grasp_test_button)
        p2_note=label('P2：关节 PD。双指接触 ≥0.2s；提升 ≥0.10m 且双指接触持续 ≥0.5s。','muted'); p2_note.setWordWrap(True); box.addWidget(p2_note)
        self.p2_record_dir=None
        self.p2_result_label=label('P2：尚无执行记录','muted'); self.p2_result_label.setWordWrap(True); box.addWidget(self.p2_result_label)
        row=QHBoxLayout()
        self.p2_copy_button=QPushButton('复制抓取记录路径'); self.p2_copy_button.setEnabled(False); self.p2_copy_button.clicked.connect(self.copy_p2_path)
        self.p2_open_button=QPushButton('打开抓取记录'); self.p2_open_button.setEnabled(False); self.p2_open_button.clicked.connect(self.open_p2_folder)
        row.addWidget(self.p2_copy_button); row.addWidget(self.p2_open_button); box.addLayout(row)
        note=label('规则抓取自检仅支持默认汤罐任务。\n最终 π₀.₅ / G³VLA / 接触预测模块通过独立适配器接入。','muted'); note.setWordWrap(True); box.addWidget(note)
        box.addStretch(); scroll.setWidget(panel); self.tabs.addTab(scroll,'策略 / 自检')

    def choose_checkpoint(self):
        path=QFileDialog.getExistingDirectory(self,tr('选择模型目录'),str(Path.home()))
        if path: self.checkpoint_input.setText(path)

    def copy_p2_path(self):
        if self.p2_record_dir: QApplication.clipboard().setText(desktop_path(self.p2_record_dir))

    def open_p2_folder(self):
        if self.p2_record_dir and not open_directory(self.p2_record_dir):
            self.log('无法打开文件管理器，请使用“复制结果路径”。')

    def _language_changed(self, language):
        # Translation only: no scene rebuild, control command or input-value reset.
        self.language_button.setText('English' if language=='zh' else '中文')
        self.i18n.bind(self.language_button,'切换到英文' if language=='zh' else '切换到中文','setToolTip')
        self.viewport.update()
        if self.i18n.last_save_error:
            self.statusBar().showMessage(tr('无法保存语言偏好：{error}',error=self.i18n.last_save_error),10000)

    def configure_policy(self):
        self.command('policy_configure',kind=self.policy_combo.currentData(),checkpoint=self.checkpoint_input.text().strip(),endpoint=self.endpoint_input.text().strip())

    def _sync_camera_stream(self, *_):
        connected=self.client.process.state()==QProcess.ProcessState.Running
        enabled=bool(connected and self.state.get('loaded') and not self.busy and not self.closing_window
                     and self.view_tabs.currentIndex()==1 and self.live_cameras_enabled)
        if enabled != self._camera_requested:
            self._camera_requested=enabled
            if connected: self.client.send('camera_stream',enabled=enabled)

    def toggle_camera_refresh(self):
        self.live_cameras_enabled=not self.live_cameras_enabled
        set_text(self.camera_refresh_button, '暂停相机刷新' if self.live_cameras_enabled else '继续相机刷新')
        if not self.live_cameras_enabled: set_text(self.live_cameras.info, '相机刷新已暂停；画面为最近一帧。')
        self._sync_camera_stream()

    def log(self, text):
        text=str(text)
        if not text.strip(): return
        line=time.strftime('%H:%M:%S')+'  '+text
        self.logs.appendPlainText(line)
        if not self.log_stream.closed:
            self.log_stream.write(line+'\n'); self.log_stream.flush()

    def command(self, op, **args):
        if op in ('load','load_task','add','update','delete','policy_scan','policy_configure','policy_load','policy_run','grasp_test','replay_open'):
            if op in ('load','load_task','add','update','delete'): self.live_cameras.clear('正在更新场景，等待新图像…')
            self.busy=True; self.error_banner.hide(); self._update_enabled()
        return self.client.send(op, **args)

    def change_suite(self, suite):
        self.command('list_tasks',suite=suite)

    def populate_tasks(self, tasks):
        self.tasks=tasks
        self.task_combo.blockSignals(True); self.task_combo.clear()
        for task in tasks:
            self.task_combo.addItem(f"{task['index']+1:02d} · {task['language']}",task['index'])
        self.task_combo.blockSignals(False); self.task_description_changed()

    def task_description_changed(self):
        index=self.task_combo.currentIndex()
        if 0 <= index < len(self.tasks): set_text(self.task_description, self.tasks[index]['language'],raw=True)

    def load_task(self):
        if self.task_combo.currentData() is not None:
            self.command('load_task',suite=self.suite_combo.currentText(),index=self.task_combo.currentData())

    def on_message(self, message):
        event=message['event']
        if event=='ready':
            set_text(self.connection_label, '后端已连接 · 协议 v1')
            self.populate_tasks(message['tasks']); self.log('仿真后端就绪；使用独立 Python 进程。')
            self.p1_panel.set_tasks(message['tasks'])
            if message.get('adapters'):
                self.policy_combo.clear()
                for adapter in message['adapters']: self.policy_combo.addItem(adapter['label'],adapter['id'])
                self.i18n.localize_tree(self.policy_combo)
            if self.auto_load: self.load_task()
            else: self.load_button.setEnabled(True)
        elif event=='busy':
            self.busy=True; self._update_enabled()
            set_text(self.scene_status, '正在验证记录并重建独立状态场景…' if message['op']=='replay_open' else '正在应用场景编辑并编译物理模型…' if message['op'] in ('add','update','delete') else '正在加载与编译真实 LIBERO 场景…')
        elif event=='frame':
            self.viewport.set_frame(message['jpeg']); self.frame_count+=1; self.update_state(message['state'])
            availability = self.state.get('contact_sampling', {}).get('availability')
            hint = {'unsupported': '当前策略没有原生求解器接触样本',
                    'error': '接触采样出错'}.get(availability, '等待原生物理步进采样')
            self.contact_controls.show_sample(message.get('rendered_contacts'), hint)
        elif event=='camera_frame':
            if not self._camera_requested or self.busy or not self.scene_data or message['task'] != self.scene_data['task']: return
            try:
                self.live_cameras.set_packet(message); self.camera_frame_count+=1
            except (KeyError, ValueError, TypeError) as error:
                self.live_cameras_enabled=False; set_text(self.camera_refresh_button, '继续相机刷新')
                self.live_cameras.clear('图像验证失败：'+str(error)); self.log(self.live_cameras.info.text()); self._sync_camera_stream()
        elif event=='camera_fault':
            self.live_cameras_enabled=False; set_text(self.camera_refresh_button, '继续相机刷新')
            self.live_cameras.clear(message['error']); self.log(message['error']); self._sync_camera_stream()
        elif event=='reply':
            op=message['op']; self.last_reply[op]=message
            self.p2_browser.accept(op, message)
            if op in ('load','load_task','add','update','delete','policy_scan','policy_configure','policy_load','policy_run','grasp_test','replay_open'): self.busy=False
            if 'state' in message: self.update_state(message['state'])
            if not message['ok']:
                set_text(self.error_banner, message['error']); self.error_banner.show(); self.log('错误 · '+message['error'])
                self._update_enabled(); return
            result=message.get('result')
            if op=='list_tasks': self.populate_tasks(result)
            if isinstance(result,dict) and 'bodies' in result:
                self.update_scene(result)
                if op in ('load','load_task'):
                    self.p2_browser.clear_view()
                    self.log('场景已加载（尚未自动执行任务）· '+result['task']['language'])
                    if op=='load': self.project_path=self._opening_path
                    else: self.project_path=None
                elif op in ('add','update','delete','placement'): self.log('场景编辑已应用 · '+op)
            if op in ('select','pick'):
                self.show_properties(result)
                if result: self.highlight_tree(result['id'])
                self.log('选择 · '+(result['id'] if result else '无'))
            if op in ('stop','reset'):
                if self.state.get('selected'): self.command('select',id=self.state['selected'])
                self.log('布局已恢复，仿真时间归零。')
            if op in ('save','screenshot'):
                self.log('已保存 · '+result['path']); self.statusBar().showMessage(result['path'],10000)
                if op=='save': self.project_path=result['path']
            if op=='policy_scan':
                set_text(self.weight_status, f"扫描完成：{result['complete_candidates']} 个文件齐全候选，{len(result['checkpoints'])} 个已检查目录。未加载权重。")
                self.log(self.weight_status.text())
                (ROOT/'evidence').mkdir(exist_ok=True)
                import json
                (ROOT/'evidence'/'weight-scan.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
            if op in ('policy_configure','policy_load'):
                self.log('策略状态 · '+result['detail'])
            if op=='policy_preview':
                prediction=result['prediction']; observation=result['observation']
                set_text(self.policy_detail, f"上次单次测试 / {'Mock' if prediction.get('is_mock') else '真实模型'}：双 RGB → {observation['proprio_dim']} 维本体状态 → {len(prediction['actions'])} 个 7 维动作。\n单次测试未推进物理仿真。")
                self.log(self.policy_detail.text())
            if op=='jog_xyz': self.log(self.jog_feedback.text())
            self._update_enabled()
        elif event=='fault':
            self.busy=False; set_text(self.error_banner, message['error']); self.error_banner.show(); self.log('后端错误 · '+message['error'])

    def update_state(self, state):
        self.state=state
        set_text(self.time_label, f"t = {state['time']:.3f} s")
        self.mode.blockSignals(True); self.mode.setCurrentIndex(0 if state['mode']=='edit' else 1); self.mode.blockSignals(False)
        if state.get('loaded'):
            self.contact_controls.set_active(state.get('contact_view', {}).get('enabled', False))
            robot=state['robot']
            jog_status={'idle':'空闲','pending':'等待运动到达','reached':'已到达','stalled':'超时，保持当前关节'}
            set_text(self.jog_feedback, '点动目标状态：'+jog_status.get(state.get('jog',{}).get('status','idle'),'空闲'))
            for i,slider in enumerate(self.joint_sliders):
                low,high=robot['limits'][i]
                slider.blockSignals(True); slider.setRange(math.ceil(low*1000),math.floor(high*1000))
                if not slider.isSliderDown() and i not in self.pending_joints: slider.setValue(round(robot['targets'][i]*1000))
                slider.blockSignals(False)
                set_text(self.joint_labels[i], f"J{i+1}  实测 {math.degrees(robot['qpos'][i]):.1f}°  /  目标 {math.degrees(robot['targets'][i]):.1f}°")
            if not self.gripper_slider.isSliderDown() and self.pending_gripper is None:
                self.gripper_slider.blockSignals(True); self.gripper_slider.setValue(round(robot['gripper_target']*100)); self.gripper_slider.blockSignals(False)
            p,q=robot['eef_position'],robot['eef_quaternion_wxyz']
            set_text(self.eef_label, '末端位置 / m\n'+', '.join(f'{v:.4f}' for v in p)+'\n四元数 / wxyz\n'+', '.join(f'{v:.3f}' for v in q)+'\n指关节 / m\n'+', '.join(f'{v:.4f}' for v in robot['gripper_qpos']))
            status='物理时间推进中' if state['running'] else '已暂停'
            set_text(self.scene_status, f"{status} · {'编辑' if state['mode']=='edit' else '仿真'} · {state['contacts']} 个接触 · 真实 MuJoCo 图像")
            execution=state.get('execution',{})
            phase=execution.get('phase','尚未执行策略')
            if execution.get('status')=='running' and not state['running']: phase='已暂停 · '+phase
            source={'mock':'Mock（非 VLA）','scripted_self_test':'规则自检（非 VLA）','vla':'真实 VLA'}.get(execution.get('kind'),'未启动策略')
            set_text(self.execution_status, '任务执行：'+source+' · '+phase)
            self.policy_progress.setValue(round(100*execution.get('progress',0)))
            model=state.get('policy',{})
            names={'configured':'仅配置','weights_found':'权重文件已找到','model_loaded':'模型已加载','inference_verified':'真实推理通过','mock_ready':'Mock 已就绪（非模型）'}
            set_text(self.model_status_label, '模型状态：'+names.get(model.get('stage'),'仅配置')+'\n'+model.get('detail',''))
            active_record=execution.get('kind')=='scripted_self_test' and execution.get('status')=='running'
            record=execution if active_record else state.get('last_episode')
            if record:
                self.p2_record_dir=record.get('record_dir') or record.get('directory')
                metrics=record.get('metrics',{})
                status='记录中' if active_record else '通过' if record.get('p2_passed') is True else '已取消' if record.get('status')=='cancelled' else '未通过'
                symbol=lambda value:'✓' if value is True else '—'
                set_text(self.p2_result_label,f"P2 记录：{status}\n双指接触 / 持续提升 / 官方任务：{symbol(metrics.get('finger_contact_verified'))} / {symbol(metrics.get('lift_verified'))} / {symbol(record.get('official_task_success'))}\n最大提升 {metrics.get('max_height_gain_m',0):.3f} m · 连续有效提升 {metrics.get('max_stable_lift_s',0):.2f} s\n{record.get('sample_count',0)} 帧\n{self.p2_record_dir}")
                self.p2_copy_button.setEnabled(bool(self.p2_record_dir)); self.p2_open_button.setEnabled(bool(self.p2_record_dir))
            else:
                self.p2_record_dir=None
                set_text(self.p2_result_label,'P2：尚无执行记录')
                self.p2_copy_button.setEnabled(False); self.p2_open_button.setEnabled(False)
        self._update_enabled()

    def _update_enabled(self):
        connected=self.client.process.state()==QProcess.ProcessState.Running
        loaded=self.state.get('loaded',False) and not self.busy and connected
        editing=loaded and self.state['mode']=='edit' and not self.state['running']
        active=self.state.get('execution',{}).get('status')=='running'
        can_preview = loaded and (not active or self.state.get('contact_sampling', {}).get('enabled', False))
        self.contact_controls.setEnabled(can_preview)
        for b in self.jog_buttons: b.setEnabled(loaded and not active and self.state.get('jog',{}).get('status')!='pending')
        self.jog_step.setEnabled(loaded and not active)
        self.p2_browser.set_available(connected and not self.busy, loaded and not active and not self.state['running'])
        for op,b in self.buttons.items():
            b.setEnabled(loaded and (op!='pause' or self.state['running']) and (op!='play' or not self.state['running']) and (op!='step' or not self.state['running']))
        self.mode.setEnabled(loaded)
        self.load_button.setEnabled(connected and not self.busy and self.task_combo.count()>0)
        self.suite_combo.setEnabled(not self.busy); self.task_combo.setEnabled(not self.busy)
        for b in self.add_buttons: b.setEnabled(editing)
        for s in self.joint_sliders+[self.gripper_slider]: s.setEnabled(loaded and not active)
        for button in (self.scan_button,self.configure_button,self.connect_policy_button,self.preview_button,self.run_policy_button,self.grasp_test_button):
            button.setEnabled(loaded and not active)
        policy=self.state.get('policy',{})
        matching=policy.get('adapter_id',policy.get('family'))==self.policy_combo.currentData()
        ready=matching and (policy.get('stage')=='mock_ready' or policy.get('model_loaded') is True)
        self.preview_button.setEnabled(loaded and not active and ready)
        self.run_policy_button.setEnabled(loaded and not active and ready)
        self.connect_policy_button.setEnabled(loaded and not active and matching and policy.get('real_model') is True)
        self.policy_combo.setEnabled(not active and not self.busy)
        self.checkpoint_input.setEnabled(not active and not self.busy)
        self.endpoint_input.setEnabled(not active and not self.busy)
        set_text(self.run_policy_button, '执行 Mock 自检' if policy.get('family')=='mock' and matching else '执行已配置 VLA 策略')
        self.stop_policy_button.setEnabled(loaded and active)
        if self.scene_data:
            t=self.scene_data['task']
            self.grasp_test_button.setEnabled(loaded and not active and t['suite']=='libero_object' and t['index']==0)
        self.record_contacts.setEnabled(self.grasp_test_button.isEnabled())
        if hasattr(self,'apply_button'):
            self.apply_button.setEnabled(editing and self.selected_info is not None and self.selected_info['type'] in ('primitive','libero_object'))
            self.delete_button.setEnabled(editing and self.selected_info is not None and self.selected_info['type']=='primitive')
            self.fields_widget.setEnabled(editing)
        self.camera_refresh_button.setEnabled(loaded)
        self._sync_camera_stream()

    def update_scene(self, scene):
        self.scene_data=scene
        self.tree.clear(); items={}
        root=QTreeWidgetItem(self.tree,['World · LIBERO / Panda']); root.setExpanded(True)
        items['world']=root
        for body in scene['bodies']:
            text=body['id']
            if body['type']=='primitive': text='● '+text
            item=QTreeWidgetItem([text]); item.setData(0,Qt.ItemDataRole.UserRole,body['id']); items[body['id']]=item
        for body in scene['bodies']:
            parent=items.get(body['parent'],root); parent.addChild(items[body['id']])
            if body['type'] in ('primitive','libero_object') or body['parent']=='world': items[body['id']].setExpanded(True)
        selected=self.state.get('selected')
        info=next((b for b in scene['bodies'] if b['id']==selected),None)
        self.show_properties(info)
        if info: self.highlight_tree(info['id'])

    def highlight_tree(self, object_id):
        def visit(item):
            if item.data(0,Qt.ItemDataRole.UserRole)==object_id:
                self.tree.setCurrentItem(item); self.tree.scrollToItem(item); return True
            return any(visit(item.child(i)) for i in range(item.childCount()))
        for i in range(self.tree.topLevelItemCount()):
            if visit(self.tree.topLevelItem(i)): break

    def select_tree(self, item, column):
        object_id=item.data(0,Qt.ItemDataRole.UserRole)
        if object_id: self.command('select',id=object_id)

    def show_properties(self, info):
        self.selected_info=info
        old=self.properties_scroll.takeWidget()
        if old: old.deleteLater()
        panel=QWidget(); layout=QVBoxLayout(panel); layout.setContentsMargins(12,12,12,12)
        layout.addWidget(label(info['id'] if info else '选择场景中的物体','panelTitle',literal=info is not None))
        self.fields_widget=QWidget(); form=QFormLayout(self.fields_widget); form.setContentsMargins(0,0,0,0)
        self.property_fields={}
        if info:
            note={'primitive':'编辑器物体 · 全部物理属性可编辑','libero_object':'LIBERO 物体 · 可编辑位姿','readonly':'固定场景 / 机器人部件 · 只读'}[info['type']]
            lbl=label(note,'muted'); lbl.setWordWrap(True); layout.addWidget(lbl)
            if info['type'] in ('primitive','libero_object'):
                for key,title,low,high in [('position','位置 XYZ / m',-10,10),('rotation','旋转 XYZ / °',-360,360)]:
                    self._vector_field(form,key,title,info[key],low,high)
                if info['type']=='primitive':
                    self._vector_field(form,'size','尺寸 XYZ / m',info['size'],.002,2)
                    dim=label('球：X 为直径。圆柱：X 为直径，Z 为高度。\n立方体：XYZ 为各边完整长度。','muted'); dim.setWordWrap(True); form.addRow(dim)
                    spin=QDoubleSpinBox(); spin.setDecimals(3); spin.setRange(.001,100); spin.setValue(info['mass']); spin.setSingleStep(.05); self.property_fields['mass']=spin; form.addRow('质量 / kg',spin)
                    self._vector_field(form,'friction','摩擦 滑动/扭转/滚动',info['friction'],0,10)
                    check=QCheckBox('启用物理碰撞'); check.setChecked(info['collision']); self.property_fields['collision']=check; form.addRow(check)
            else:
                form.addRow(label('世界位置 / m\n'+', '.join(f'{v:.4f}' for v in info['position']),'muted'))
        layout.addWidget(self.fields_widget)
        self.apply_button=QPushButton('应用属性'); self.apply_button.setObjectName('apply_properties'); self.apply_button.clicked.connect(self.apply_properties); layout.addWidget(self.apply_button)
        self.delete_button=QPushButton('删除编辑器物体'); self.delete_button.setObjectName('delete_object'); self.delete_button.clicked.connect(lambda:self.command('delete',id=self.selected_info['id'])); layout.addWidget(self.delete_button)
        layout.addStretch(); self.properties_scroll.setWidget(panel)
        language_manager().localize_tree(panel)
        self._update_enabled()

    def _vector_field(self, form, key, title, values, low, high):
        form.addRow(label(title,'muted')); row=QWidget(); box=QHBoxLayout(row); box.setContentsMargins(0,0,0,0); box.setSpacing(3); spins=[]
        for i,value in enumerate(values):
            spin=QDoubleSpinBox(); spin.setObjectName(f'property_{key}_{i}'); spin.setDecimals(4); spin.setRange(low,high); spin.setSingleStep(.01 if key!='rotation' else 5); spin.setValue(value); spin.setMinimumWidth(62); box.addWidget(spin); spins.append(spin)
        self.property_fields[key]=spins; form.addRow(row)

    def apply_properties(self):
        if not self.selected_info: return
        values={}
        for key,field in self.property_fields.items():
            values[key]=[f.value() for f in field] if isinstance(field,list) else field.isChecked() if isinstance(field,QCheckBox) else field.value()
        object_id=self.selected_info['id']
        if self.selected_info['type']=='primitive': self.command('update',id=object_id,changes=values)
        else: self.command('placement',id=object_id,position=values['position'],rotation=values['rotation'])

    def queue_joint(self,index,value):
        self.pending_joints[index]=value/1000
        if not self.joint_timer.isActive(): self.joint_timer.start()

    def jog_axis(self, axis, direction):
        self.flush_joints()
        delta=[0.,0.,0.]; delta[axis]=direction*self.jog_step.value()/1000
        self.command('jog_xyz', delta=delta, frame='world')

    def queue_gripper(self,value):
        self.pending_gripper=value/100
        if not self.joint_timer.isActive(): self.joint_timer.start()

    def flush_joints(self):
        for index,value in self.pending_joints.items(): self.command('joint',index=index,radians=value)
        self.pending_joints.clear()
        if self.pending_gripper is not None:
            self.command('gripper',opening=self.pending_gripper); self.pending_gripper=None

    def open_dialog(self):
        path,_=QFileDialog.getOpenFileName(self,tr('打开 RoboLab 项目'),str(DEFAULT_PROJECTS),'RoboLab (*.json)')
        if path: self.open_path(path)

    def open_path(self,path):
        self._opening_path=str(path); self.command('load',path=str(path))

    def save_dialog(self,save_as=False):
        if not self.state.get('loaded'): return
        path=self.project_path
        if save_as or not path:
            DEFAULT_PROJECTS.mkdir(exist_ok=True)
            path,_=QFileDialog.getSaveFileName(self,tr('保存场景布局'),str(DEFAULT_PROJECTS/'scene.robolab.json'),'RoboLab (*.json)')
        if path: self.command('save',path=path)

    def screenshot_dialog(self):
        if not self.state.get('loaded'): return
        DEFAULT_PROJECTS.mkdir(exist_ok=True)
        path,_=QFileDialog.getSaveFileName(self,tr('导出 MuJoCo 场景截图'),str(DEFAULT_PROJECTS/'scene.png'),'PNG (*.png)')
        if path: self.command('screenshot',path=path)

    def restart_backend(self):
        if self.client.process.state()!=QProcess.ProcessState.NotRunning:
            self.log('请先关闭当前窗口后重新启动，以便释放原仿真进程。'); return
        self.state['loaded']=False; self.error_banner.hide(); self.log('重新启动仿真后端…'); self.client.start()

    def on_disconnected(self,text):
        self.p2_browser.clear_view()
        self._camera_requested=False; self.live_cameras.clear('仿真后端已断开')
        self.busy=False; self.state['loaded']=False; set_text(self.connection_label, '后端已断开'); self.log(text); self._update_enabled()
        if self.closing_window: self.close()
        else:
            set_text(self.error_banner, text+'；可在“帮助”菜单重启后端。'); self.error_banner.show()

    def closeEvent(self,event):
        self.p2_browser.stop_play()
        self.closing_window=True
        self._sync_camera_stream()
        pending=False
        if self.client.process.state()!=QProcess.ProcessState.NotRunning:
            if not self.client.closing: self.client.close()
            pending=True
        if self.p1_panel.is_running:
            self.p1_panel.cancel(); pending=True
        if self.p4_panel.is_running:
            self.p4_panel.cancel(); pending=True
        if pending:
            self.setEnabled(False); event.ignore(); return
        if self.p1_panel.movie: self.p1_panel.movie.stop()
        self.log_stream.close(); event.accept()

def configure_application():
    app=QApplication.instance() or QApplication([])
    fontpath=Path('/mnt/c/Windows/Fonts/msyh.ttc')
    if fontpath.is_file():
        font_id=QFontDatabase.addApplicationFont(str(fontpath))
        families=QFontDatabase.applicationFontFamilies(font_id)
        if families: app.setFont(QFont(families[0],10))
    app.setStyle('Fusion'); app.setStyleSheet(STYLE)
    language_manager()
    return app
