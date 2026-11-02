# P0 环境核查报告

检查日期：2026-11-01，Europe/London。本报告来自本轮实际运行，不采用历史截图或旧测试结论作为本轮证据。

这是首次 P0/P1 审计记录。后续 GUI 接入与其复测另见 [P1_GUI_REPORT.md](P1_GUI_REPORT.md)，环境问题仍按实际审计结果区分。

结论：**环境核查已完成；现有 LIBERO 仿真环境满足 P1。OpenVLA-OFT 源码及安装存在，但基础导入失败，不能标为“可推理”。** 本轮保留现有环境，不修装依赖、不下载权重。

## 已证实

### 路径、源码和已有软件

| 内容 | 实际位置与结果 |
| --- | --- |
| 执行平台 | WSL2 Ubuntu-22.04；Windows 副本只用于查看源码、启动入口与结果 |
| RoboLab Studio | `/home/openvla/projects/robolab-studio`，已有原生 PySide6 GUI、独立 worker、MuJoCo 引擎、场景编辑、策略接口及旧测试 |
| 后端 Python | `/home/openvla/venvs/openvla-oft/bin/python`，3.10.12 |
| GUI Python | 项目 `.venv/bin/python`，3.10.12；独立导入 PySide6 6.8.3 成功 |
| LIBERO | `/home/openvla/projects/LIBERO`，可编辑安装对应此目录 |
| OpenVLA-OFT | `/home/openvla/projects/openvla-oft`，可编辑安装对应此目录 |
| 原有显示脚本 | `~/projects/show_libero.py` 存在；本轮读取，未启动其窗口、未修改 |
| 原有离屏脚本 | `~/projects/test_libero_object.py` 存在；本轮以 EGL 实际运行 reset/step，退出码 0 |
| LIBERO 配置 | `~/.libero/config.yaml` 存在，包含 `benchmark_root`、`bddl_files`、`init_states`、`assets`、`datasets`；P1 实际读取了任务 BDDL、资产和官方初态文件 |
| GPU | `nvidia-smi`：RTX 4060 Laptop，8188 MiB，驱动 591.74；PyTorch `cuda.is_available()` 为 true |
| WSL 内存 | `free -h`：本轮 WSL 可见内存约 7.4 GiB、swap 2 GiB；这不是 Windows 物理内存测量 |

已有 `robolab/gui.py`、`client.py`、`worker.py`、`engine.py`、`policy.py`、`openvla_service.py`、`task_control.py` 等代码。本轮复用其已建立的项目与依赖；独立 P1 直接调用官方 `OffScreenRenderEnv`，不为验证相机而重建 UI 或再写仿真引擎。旧 GUI/抓取测试本轮没有重跑。

### 实际依赖与导入

| 依赖 | 版本 | 本轮证据 |
| --- | --- | --- |
| MuJoCo | 2.3.7 | 导入与 EGL 仿真通过 |
| robosuite | 1.4.1 | 导入、Panda 控制器和相机输出通过 |
| LIBERO | 0.1.0 | 导入、任务初始化及 step 通过 |
| PyTorch | 2.2.0+cu121 | 导入通过，CUDA 构建版本 12.1 |
| numpy | 1.26.4 | 实际参与双相机与状态验证 |
| Pillow | 12.3.0 | PNG/GIF 保存与逐帧读取通过 |
| OpenCV | 4.11.0.86 | 安装元数据；P1 不需要导入或弹窗 |
| OpenVLA-OFT | 0.0.1 | 可编辑安装存在；`prismatic`、`experiments.robot.openvla_utils` 导入失败 |
| transformers | 4.40.1 | 安装来自 OFT 专用 fork，commit `bc339d9ad707454c0c115970db43c260067c61ab` |
| TensorFlow | 2.15.0 | OFT 导入依赖链涉及此包；未做训练或完整功能验证 |
| tensorflow-metadata | 1.21.0 | 导入故障所在依赖 |
| protobuf | 4.25.9 | 当前缺少依赖所需的 `runtime_version` |

`python -m pip check` 本轮前后均返回 0，输出 `No broken requirements found.`。这只检查声明的依赖关系，不保证所有运行时导入兼容。后端未安装 PySide6 是预期的进程隔离；GUI 自身环境已安装。后端未安装 bitsandbytes，不是 P0/P1 的必备项。

### Git、可追溯性与环境保护

| 项目 | commit | 工作区 |
| --- | --- | --- |
| LIBERO | `8f1084e3132a39270c3a13ebe37270a43ece2a01` | 前后均干净 |
| OpenVLA-OFT | `e4287e94541f459edc4feabc4e181f537cd569a8` | 前后均干净 |
| RoboLab Studio | 无 | 实际目录不是 Git 仓库；本轮没有擅自初始化或伪造 commit |

前后 175 个唯一已安装包的名称/版本摘要一致，SHA256 为 `9a527653e1d94937449abefa78e1c519bb4305009cc4333c3b278d2394dde40f`。这不是可直接安装的 lockfile；完整版本清单已保存，用于后续在实验室制定安装锁定方案。

以下 6 项的 SHA256 前后完全相同：`show_libero.py`、`test_libero_object.py`、`~/.libero/config.yaml`、后端 `pyvenv.cfg`、LIBERO 的 `env_wrapper.py`、OFT 的 `openvla_utils.py`。没有修改第三方源码、venv 包、CUDA、驱动、全局渲染变量或已有 GUI 的 glfw/WSLg 启动配置。

机器可读证据：[before.json](../outputs/env_audit/before.json)、[after.json](../outputs/env_audit/after.json)、[integrity.json](../outputs/env_audit/integrity.json)。P1 的 metadata 另记录脚本 SHA256、任务文件 SHA256、官方初态 SHA256 与依赖 commit，弥补本轮没有项目 Git commit 的限制。

## 发现的问题及处理边界

1. **OFT 运行时导入失败，未修复。** 离线、禁用 GPU 可见性的独立导入检查报：

   ```text
   ImportError: cannot import name 'runtime_version' from 'google.protobuf'
   ```

   实际依赖链为 `prismatic → RLDS → dlimp → tensorflow_datasets → tensorflow_metadata/proto/v0/anomalies_pb2.py`，该生成文件导入当前 protobuf 不提供的符号。完整堆栈保存在 `after.json` 的 `openvla_oft_imports_cpu_offline.stdout`。因此审计脚本退出码为 **1**，不是假装所有环境都健康。P1 不导入这条 VLA 依赖链，两次真实仿真通过。

   后续最小修复方向是在独立的 VLA 环境核对 TF 2.15 与 protobuf、tensorflow-metadata 的兼容版本组合，先通过导入测试再谈模型。此次没有直接升级 protobuf，也没有重装整个现有仿真环境。仅凭解决这个首个错误，仍不能承诺其余 OFT 推理依赖全部可用。

2. **首轮 P1 退出时出现 EGL 析构错误，已在新脚本内修复并复测。** robosuite 1.4 的上下文引用可能晚于 `eglTerminate()` 才释放。新增测试入口在关闭环境前显式释放 MuJoCo/EGL 资源，未改库；修复后的两次独立进程退出码均为 0，无该错误。首次尝试仍保留，不充当最终通过证据。

3. 仍有 robosuite “No private macro file”与旧 Gym 的维护提示；没有阻断本轮 reset/step/导出。OFT 导入 stderr 还有 TensorFlow CUDA factory 重复注册日志；CPU-only 导入也可见，未据此声称完成了 GPU 模型验证。

4. 旧 `policy.py`、`doctor.py` 等仍有个人机器的默认/历史路径，迁移存在技术债。本轮新 P0/P1 入口使用 `Path.home()`、参数及环境变量，不扩展为全项目重构。RoboLab 缺少 Git 版本与经过复建验证的锁文件，也需在实验室迁移前处理。

## 未证实或缺失

- 本轮没有确认模型权重完整性、加载模型或执行真实 VLA 推理；没有配置/测试模型服务。不能把 OFT 源码安装状态标为“已找到权重”或“已推理验证”。
- π₀.₅/openpi、G³VLA、几何与接触模块未在本轮安装或验证。
- 本轮只验收 P1；已有规则抓取代码的存在不等于 P2 抓取/提升验收通过，接触采集、区域标签与 unseen 评估均未验收。
- 实验室 GPU、内存、操作系统与权限未核实。用户已确认机械臂为 **Panda（FER）**、腕部和桌面各一台 D455 且只用 RGB；夹爪仍需核对。当前相机图像来自 LIBERO，不是 D455 实机采集或标定。

## 后续行动

本轮停在 P1。下一阶段可复用已有控制代码，在固定场景中验证小范围动作、闭爪、实际抓取/提升与官方成功判定，再进入接触提取。按当前用户范围，尚未开始 P2。OFT 导入修复可留到单独的模型环境维护阶段，不是继续无模型仿真的前置条件。
