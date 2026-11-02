# P1 接入 RoboLab Studio

本轮按用户“开始”的要求，把 P1 双相机与离屏自检接入已有桌面软件。仍限于 P1，没有开始抓取、接触采集、VLA 推理或训练。

## 如何操作

关闭旧窗口，双击 Windows 工作区中的 `Launch RoboLab Studio.cmd`，或按原来的启动方式打开软件。旧进程不会自动加载更新后的代码。

中间视图区有三个页签；“验证”菜单也可直接切换：

| 页签 | 用途 | 运行方式 |
| --- | --- | --- |
| 三维场景 | 原有旋转、选取与场景编辑 | 保留原有操作 |
| 双相机 · 实时 | 当前编辑器场景的外部、腕部 RGB；含当前任务和仿真时间 | 进入此页自动刷新，最高 5 Hz；可单独暂停/继续刷新 |
| P1 离屏自检 | 对官方 LIBERO-Object 初始场景独立执行 reset/step 与图像导出 | 选择任务、seed、步数，点击“运行离屏自检” |

实时查看不会推进物理时间，也不会启动策略。顶部“启动物理仿真”仍单独控制物理时间；暂停物理后，两路相机仍可刷新同一时刻。离开实时页后停止相机流，减少不需要的渲染和传输。

离屏自检默认 20 步、seed 0、官方初态 0、每路 256×256 RGB。它重新创建官方任务初始场景，**不带入编辑器中新加物体、位姿改动或当前运行状态**。初始化较慢时显示不定进度条；步进后显示真实已完成步数。结束后必须同时通过进程退出、metadata 状态与导出哈希检查，才显示“P1 通过”。

通过后可在“本次双 RGB”查看 PNG，在“GIF 回放”播放/暂停 GIF，在“本次日志”查看子进程输出。另有“复制结果路径”和“打开结果目录”；WSL 下复制为 Windows 可用的 WSL UNC 路径。

## 结果、失败和取消

正常使用的输出仍在项目 `outputs/smoke_test/<时间_标识>/`，包含 `external.png`、`wrist.png`、`side_by_side.png`、`simulation.gif`、`metadata.json` 和 `run.log`。GUI 另保存 `gui_process.log` 与 `gui_result.json`，记录进程输出和桌面端验收结论。

每次点击开始即清空上次预览，禁用重复启动。GUI 只接受本次子进程发出的目录，不读取别的进程更新的 `latest.json` 作为自己的成功结果。错误路径、Python 启动失败、非正常退出、缺失/损坏的结果都不能标为通过。

“取消自检”只停止本次 P1 进程，不停止原编辑器。取消或关闭期间不能标为成功；被中断的运行可能没有完整 metadata/图像，GUI 会保留自己的取消记录。关闭窗口会同时关闭本窗口所属的仿真后端与 P1 子进程，超时才终止；不操作其他窗口的进程。

实时图像使用原有相机提取逻辑，每路 224×224、RGB、左上角原点；按比例显示，不把它拉伸为编辑视图尺寸。它与 P1 保存的原生 256×256 图像是两个明确标注的来源；均为仿真相机，不是已接入的 D455 硬件。

## 实现边界

- `robolab/p1_panel.py`：只用既有 PySide6 与标准库，提供双图、进程状态、结果校验、GIF、取消与目录操作。
- `robolab/gui.py`：接入新页签、菜单与关闭协调；原物理/策略按钮含义保留。
- `robolab/engine.py`：从既有策略观察函数提取 `camera_images()`，让相机查看不依赖模型；原策略观察仍复用相同逻辑。
- `robolab/worker.py`：新增可开关、最高 5 Hz 的双相机帧事件；所有 MuJoCo/GL 操作仍在后端主线程。
- `scripts/smoke_test_libero.py`：仅增加可选 `--json-events` 进度输出；GUI 调用的仍是同一个真实 EGL 测试，不重写仿真流程。

GUI 不导入 MuJoCo、robosuite、numpy、OpenCV 或模型包。P1 子进程单独设置 EGL 并移除 DISPLAY/Wayland，原 GUI 后端继续使用原有 glfw/WSLg 配置。没有安装新依赖或修改 LIBERO/OFT 源码。

## 验收入口

完整的桌面集成测试入口为 `bash scripts/launch.sh --p1-test`（在 WSL 项目中运行），使用真实 WSLg 窗口与真实 EGL 子进程，输出 `evidence/p1-gui-result.json` 与 `studio-dual-*.png`、`studio-p1-*.png`。

测试运行自身使用独立的 `outputs/p1_gui_test/`，不覆盖正常手动自检结果。

## 实际测试结果（2026-11-01）

两轮真实 WSLg 桌面集成测试均退出 0。最终版本的完整测试耗时 182.74 秒，8 项检查通过；GUI 内的 EGL 自检耗时 107.747 秒。Qt 心跳计时器触发 5936 次，并在子进程初始化期间确认原 worker 继续响应；不是只验证窗口能打开。

| 检查 | 实测结果 |
| --- | --- |
| 实时双相机 | 两路 224×224 有效 JPEG、视角不同；暂停时仍输出，物理时间保持不变 |
| 原有物理操作 | 启动、暂停、重置可用；相机可单独暂停/恢复，原场景帧继续更新 |
| 编辑后的相机 | 移动汤罐后两路图像都更新，物理时间仍为 0 |
| GUI 内 P1 | 点击按钮产生独立 EGL 子进程；20 步、1 秒仿真、双路 256×256 RGB 与 21 帧 GIF，进程退出 0 |
| 场景独立性 | P1 使用官方初态；结束后编辑器中修改过的汤罐位置不变 |
| 结果显示 | PNG 原图读取、导出 SHA256 校验、GIF 实际推进到后续帧并可暂停；复制结果路径可用 |
| 失败路径 | 实际错误 LIBERO 路径退出 1，明确显示原因并清除旧预览；Python 不存在时启动失败，可再次运行 |
| 取消 | 实际中断 P1 子进程，记录 cancelled；原仿真进程继续连接 |
| 关闭窗口 | 正在自检时关闭，两个所属子进程均退出；以 PID 存在性检查确认没有残留 |
| CLI 对照 | 与先前同 seed 官方 CLI 测试相比，21 组双路 RGB 帧哈希、初始/最终 qpos 与观察 schema 全部一致 |
| 环境保护 | 包版本清单、6 个受保护文件哈希、LIBERO/OFT Git 状态与 P0 保存的真实快照一致；`pip check` 与核心导入通过 |

最终成功目录：`outputs/p1_gui_test/20261101T231446Z_bad0d8/`。前一轮 GUI 成功目录为 `20261101T230942Z_1cf242/`（EGL 自检 109.527 秒）。原生 EGL 上下文已在 metadata 中确认，DISPLAY/Wayland 均不传给自检进程。

证据：

- [桌面自检结果 JSON](../evidence/p1-gui-result.json)
- [实时双相机截图](../evidence/studio-dual-rgb-paused.png)、[P1 成功截图](../evidence/studio-p1-passed.png)、[GIF 播放截图](../evidence/studio-p1-gif.png)、[失败提示截图](../evidence/studio-p1-failed.png)
- [本次 P1 metadata](../outputs/p1_gui_test/20261101T231446Z_bad0d8/metadata.json)、[GUI 进程记录](../outputs/p1_gui_test/20261101T231446Z_bad0d8/gui_result.json)、[完整子进程日志](../outputs/p1_gui_test/20261101T231446Z_bad0d8/gui_process.log)
- [CLI/GUI 对照](../outputs/p1_gui_audit/cli_gui_compare.json)、[环境完整性比较](../outputs/p1_gui_audit/integrity.json)、[接入后环境审计](../outputs/p1_gui_audit/after.json)

## 尚存限制与下一步

OpenVLA-OFT 的 `protobuf.runtime_version` 导入故障仍存在，审计脚本因此退出 1；本轮没有升级包。这不影响已实测的 GUI 双相机与独立 P1 自检，不能据此称真实模型推理通过。

实时视图只验收了已有 Panda 场景；D455 只用 RGB 的硬件采集/标定仍未接入。文件管理器按钮由桌面系统处理，自动测试验证了路径复制，未自动打开额外的 Explorer 窗口。当前没有项目 Git 仓库，变更以本地备份、diff 与 SHA256 清单记录，不伪造 commit。

本轮没有验收 P2 自动抓取、接触采集或真实模型。下一阶段才是固定场景下的控制与抓取/提升验收；目前无需下载大型模型。
