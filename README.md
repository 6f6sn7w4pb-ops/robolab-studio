# RoboLab Studio

本地机器人仿真工作站，0.2 版：原生桌面 GUI、LIBERO/MuJoCo 三维场景、双 RGB 相机、物体编辑、Panda 控制、Mock / 可替换 VLA Adapter，以及默认任务的规则抓取验证和轨迹记录。

## 实验室快速开始

支持 Ubuntu 22.04 / Python 3.10，桌面显示使用 Linux 桌面或 WSLg。源码可下载；Python 环境、场景资产和驱动需要在实验室首次配置。**不需要下载 VLA 权重即可使用基础平台。**

```bash
git clone https://github.com/6f6sn7w4pb-ops/robolab-studio.git
cd robolab-studio
```

已有 LIBERO 环境时，使用实际路径完成一次配置：

```bash
python3 scripts/configure_runtime.py \
  --backend-python "$HOME/venvs/openvla-oft/bin/python" \
  --libero-root "$HOME/projects/LIBERO"
bash scripts/setup.sh
bash scripts/launch.sh --doctor
bash scripts/smoke_test_libero.sh
bash scripts/launch.sh
```

新机器没有仿真环境时，先阅读 [实验室安装说明](docs/INSTALL.md)，再显式执行隔离安装入口 `bash scripts/bootstrap_sim.sh --create-isolated-env`。安装仅作用于项目虚拟环境，不修改实验室已有研究环境。全新安装路线仍待实验室验收。

平台更新与开发进度见 [开发路线](docs/LAPTOP_ROADMAP.md)；迁移验证范围见 [GitHub 发布与迁移验收](docs/GITHUB_RELEASE.md)。自动化轻量测试不代表真实渲染或 VLA 已通过。

## 原笔记本配置与功能说明

以下记录中的 `/home/openvla` 是原笔记本历史路径示例。原机实际项目位于 WSL2：

```text
/home/openvla/projects/robolab-studio
```

Windows 工作区中的同名目录是源码和启动入口副本。不要在 Windows Python 中运行 MuJoCo 后端。

**界面语言：**顶部工具栏点击 **English / 中文** 可立即切换中英文，并记住选择。切换不重启仿真、不清空编辑值。任务原始指令、物体标识、路径和原始日志保留原文。使用与测试说明见 [LANGUAGE.md](docs/LANGUAGE.md)。更新代码后须先关闭旧窗口再启动。

**2026-11-02 · P2 第一批：**已有默认任务的规则抓取现在自动记录轨迹，并分别验证双指接触、持续提升和官方任务结果。右侧“策略 / 自检”中点击“P2 规则抓取与记录（非 VLA）”，可查看分项结果、复制路径和打开记录。实测及边界见 [P2_REPORT.md](docs/P2_REPORT.md)，后续顺序见 [LAPTOP_ROADMAP.md](docs/LAPTOP_ROADMAP.md)。

实验机械臂已明确为 Franka Emika Panda（FER），模型标识 `fer`；腕部与桌面各一台 D455，两路只用 RGB。原装 Franka Hand 尚待实物核对。详情见 [HARDWARE_PROFILE.md](docs/HARDWARE_PROFILE.md)。这不表示已经完成实机接入。

## 当前交接与 P0 / P1 验收（2026-11-01）

**P1 GUI 接入更新：**中间视图区新增“双相机 · 实时”和“P1 离屏自检”。前者显示当前编辑场景的同步外部/腕部 RGB；后者可直接点击启动已验证的 EGL 测试，查看进度、日志、双路 PNG 及 GIF。修改后请关闭旧窗口并重新启动。该次 P1 验收记录见 [P1_GUI_REPORT.md](docs/P1_GUI_REPORT.md)，最新 P2 进展见上文。

已采用用户交接要求分阶段开发，原始毕设交接资料保留在本地。仓库中的 [开发路线](docs/LAPTOP_ROADMAP.md) 和测试报告记录当前可运行范围。实验室计算资源仍待核查。

首次交接验收进行了环境检查及 LIBERO 双 RGB 的 EGL 离屏渲染。**P0 核查完成，发现 OFT 导入问题；P1 两次运行通过。** 随后接入现有 GUI，再逐步开展 P2。各次测试范围按对应报告区分；本机没有下载模型或开展训练。

在 WSL Ubuntu 中复制这一条命令即可运行 P1（无需先激活 venv）：

```bash
bash ~/projects/robolab-studio/scripts/smoke_test_libero.sh
```

终端入口仍可独立使用。GUI 中点击“P1 离屏自检 → 运行离屏自检”调用同一脚本。每次结果存入独立的 `outputs/smoke_test/<时间_标识>/`，终端/GUI 显示目录，`outputs/smoke_test/latest.json` 指向最近一次结果并记录成功/失败。GUI 根据本次子进程的目录和退出结果验收，不依赖共享 latest 指针。PNG/GIF 是物理仿真与相机接口测试，不是自动抓取演示。

环境详情及已知问题见 [ENV_AUDIT.md](docs/ENV_AUDIT.md)，运行证据、参数与路径配置见 [P0_P1_REPORT.md](docs/P0_P1_REPORT.md)。现有 GUI 仍按下文启动，本轮不改其渲染设置。

**0.2 新增策略与自检：**物理仿真、Mock Policy、规则抓取和真实 VLA 状态分开控制。更新后需关闭旧窗口再启动；右侧新增“策略 / 自检”页。先选择 Mock 并配置，即可验证真实双相机观察、动作输出和官方 LIBERO 物理控制；默认汤罐任务另有“规则抓取自检（非 VLA）”。操作及模型扩展说明见 [VLA_INTEGRATION.md](docs/VLA_INTEGRATION.md)。目前未发现完整模型权重，真实 VLA 加载与推理仍未验证。

## 启动

Windows 双击 `Launch RoboLab Studio.cmd`，或在 PowerShell 执行：

```powershell
wsl.exe -d Ubuntu-22.04 -u openvla -- bash /home/openvla/projects/robolab-studio/scripts/launch.sh
```

也可以在 WSL 内执行：

```bash
cd /home/openvla/projects/robolab-studio
bash scripts/launch.sh
```

默认使用 WSLg Wayland。首次初始化 MuJoCo 的渲染资源可能需要约 30–60 秒，窗口和日志会显示加载状态。基础物体编辑复用 GPU 资源，仍需编译真实物理模型，通常需要几秒。

如果只有 GUI 环境缺失，运行 `bash scripts/setup.sh`。该脚本只安装项目 `.venv` 的 PySide6，不会向现有 `openvla-oft` 环境安装或升级包。

## 已实现的操作

- 默认加载 LIBERO-Object 第一个任务，使用官方初始状态及真实 Panda MJCF；可切换任务。任务列表来自本机 LIBERO，不是内置的示例文字。
- 嵌入 MuJoCo 渲染视口；左键拖动旋转，右键/中键拖动平移，滚轮缩放，单击射线选取，双击聚焦当前选择。支持默认视角、世界坐标轴及所选物体的坐标轴。
- 场景树与属性检查器。机器人和固定背景只读；LIBERO 自由物体可以编辑世界位置和 XYZ 旋转。
- 添加、删除立方体、球和圆柱；编辑位置、旋转、尺寸、质量、三项摩擦与碰撞开关。这些属性改变实际 MuJoCo 刚体、惯性和碰撞几何。
- 运行、暂停、停止、重置与 20 ms 单步；显示实际仿真时间、接触数量和日志。
- Panda 七关节滑块、夹爪开合、实测关节位置、末端世界坐标及 wxyz 四元数。编辑态设置合法姿态；仿真态使用限速、限力矩的 PD 关节伺服，并执行真实物理步。
- JSON 场景布局保存和恢复、MuJoCo PNG 截图导出。`Ctrl+O` 打开，`Ctrl+S` 保存，`Ctrl+Shift+S` 另存，`Ctrl+E` 导出截图。
- GUI 与仿真进程分离；错误进入窗口日志及 `logs/`。关闭窗口请求后端释放资源，超过 4 秒会终止本窗口所属后端进程。

## 编辑与仿真的语义

选择“编辑模式”会暂停仿真并将当前布局作为新的重置基准。编辑态的属性和关节修改会更新此基准。

“运行”进入仿真模式。“暂停”只冻结时间；修改物体前需切回编辑模式。“停止”和“重置”都恢复最近的编辑基准，时间归零，并返回编辑态。“单步”执行 20 ms 物理仿真后保持暂停。

项目文件保存的是可复现的场景布局、任务/初始状态索引、种子、物体物理属性、机器人姿态和交互相机。重新打开时从暂停的编辑态开始；不保存运动中的速度、历史轨迹或求解器内部状态。它不是完整的物理回放文件。

立方体尺寸是完整 XYZ 边长；球使用 X 作为直径；圆柱使用 X 作为直径、Z 作为高度。旋转使用绕固定世界轴的 XYZ 欧拉角、单位度；内部使用 wxyz 四元数。质量单位 kg，长度单位 m，关节命令单位 rad。

物理仿真模式使用手动关节伺服。策略执行模式通过独立 Adapter 输入动作，并调用官方 LIBERO OSC 控制器；规则抓取自检使用仿真真值与 IK，结果由官方 LIBERO 判定。Mock 和规则控制不属于真实 VLA。当前没有进行多任务、多随机种子的成功率评测。

## 环境隔离

| 进程 | Python | 依赖 |
| --- | --- | --- |
| 桌面 GUI | 项目 `.venv/bin/python` | PySide6 6.8.3 |
| 仿真后端 | `/home/openvla/venvs/openvla-oft/bin/python` | 原有 MuJoCo 2.3.7、robosuite 1.4.1、LIBERO 0.1.0、numpy、Pillow |

后端仅导入现有环境。未修改原脚本、LIBERO 源码、全局环境变量、驱动、CUDA 或 `~/.libero/config.yaml`。`PYTHONPATH` 与 `MUJOCO_GL=glfw` 只设置给子进程；GUI 不导入 OpenCV，以隔离 Qt5/Qt6 插件。

可用环境变量或 `settings/runtime.json` 覆盖路径：`ROBOLAB_BACKEND_PYTHON`、`ROBOLAB_LIBERO_ROOT`、`LIBERO_CONFIG_PATH`、`ROBOLAB_OFT_ROOT`。GUI 平台可通过 `ROBOLAB_QT_PLATFORM` 指定；本机实际验证的是 `wayland`。启动器现在自动选择 Wayland / X11；具体配置见 [INSTALL.md](docs/INSTALL.md)。

## 架构及设备扩展

```text
robolab/gui.py       原生桌面界面与鼠标交互
robolab/client.py    QProcess 传输，不依赖仿真包
robolab/worker.py    协议 v1、主线程物理与 GL、图像发布
robolab/engine.py    LIBERO 导入、独立模型、编辑与布局管理
robolab/adapters.py  仿真机器人适配器注册表、硬件/相机/策略协议
robolab/schema.py    项目格式验证及原子写入
```

通信使用 stdin/stdout 的逐行 JSON；stderr 独立记录诊断。请求含 `id/op/args`，响应含 `id/ok/result/state`。视口帧是 JPEG/base64，最多 15 fps；实际帧率取决于本机 GPU/WSLg。只有后端主线程访问 MuJoCo 与 GL。

`RobotAdapter`、`CameraAdapter`、`PolicyAdapter` 以及独立的 `HardwareRobot`、`HardwareGripper`、`HardwareCamera` 协议定义了扩展边界。仿真机器人适配器通过 `register_robot_adapter()` 注册。当前实现仅针对 LIBERO Panda 的关节/电机参数；真实设备协议是预留合同，没有硬件驱动，也未连接真实机器人。

现有 GUI 默认创建七个 Panda 滑块；将来增加其他机器人时需根据其关节描述动态构建控件，并为任务导入增加对应模型适配。项目格式允许 1–32 个关节，但加载时必须与实际模型和适配器匹配，不能把 Panda 配置直接用于其他硬件。

MuJoCo 2.3.7 没有新的 `MjSpec` 编辑 API，也没有 `Renderer.close()`。与此版本相关的渲染资源释放和基础物体编辑资源复用集中在 `engine.py`；未来升级需要重新验证。这一兼容处理没有改动已安装库。

## 测试

可移植的只读路径、导入及任务资源检查：

```bash
cd /home/openvla/projects/robolab-studio
bash scripts/launch.sh --doctor
```

真实引擎集成测试：

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$PWD:/home/openvla/projects/LIBERO" MUJOCO_GL=glfw \
/home/openvla/venvs/openvla-oft/bin/python -m unittest discover -s tests -v
```

可见的桌面操作测试，会自动创建窗口、操作控件并关闭：

```bash
bash scripts/launch.sh --self-test
```

Windows 也可以运行 `Start-RoboLab.ps1 -SelfTest` 或 `Launch RoboLab Studio.cmd --self-test`。测试输出和截图位于 `evidence/`；最终验证结果见 `docs/TEST_REPORT.md`。

仅检查启动、默认视角和运行/暂停/重置：`Launch RoboLab Studio.cmd --smoke`。此检查会在完成后自动关闭窗口。

0.2 策略 / Mock / 规则抓取桌面回归：`bash scripts/launch.sh --policy-test`。新的执行和状态说明见 [VLA_INTEGRATION.md](docs/VLA_INTEGRATION.md)。

## 当前边界

- 本次实际场景测试覆盖 LIBERO-Object 的第 0、1 个任务。其他 suite 的任务列表可读取，其全部场景还未逐一验证。
- 原始 LIBERO 网格资产受到保护；仅支持位姿编辑。完整的删除、缩放、质量和碰撞编辑针对编辑器新增的基础物体。
- 场景变换通过数字属性应用；未提供鼠标拖拽变换手柄、撤销栈或网格建模工具。
- 任务切换需要初始化渲染资源，界面不阻塞，但仍有等待时间。
- 已有独立双相机实时预览及 P1 离屏验证，并为策略输入附带仿真相机几何参数；P2 已有规则抓取轨迹记录和双指接触布尔值。实机标定工具、STL/OBJ 导入、深度/分割、接触点/法向/力标注、完整回放和真实硬件接入仍未实现。
- OpenVLA-OFT 已有独立适配器和可选推理桥接服务；由于没有完整权重，本轮没有加载或验证真实 VLA。π₀.₅ / G³VLA / Gripper-Centric Contact 模块本身尚未实现。

研究背景遵循用户提供的 RoboLab 交接资料：当前阶段建设可用的仿真工作站；π₀.₅、OpenVLA-OFT 和 G³VLA 相关研究接口留待后续，不下载模型权重。
