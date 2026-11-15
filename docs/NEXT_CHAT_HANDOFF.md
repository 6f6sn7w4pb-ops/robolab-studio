# RoboLab Studio 下一对话开发交接

整理日期：2026-11-02，Europe/London。本文件用于接续已有项目，不要求重新创建项目或重建环境。历史报告中的“当时尚未实现”须结合报告日期理解，以下当前状态优先。

## 1. 用户目标与开发边界

用户正在做机器人 VLA 毕业设计。RoboLab Studio 是辅助场景、控制、相机、接触数据和实验检查的本地桌面工具；研究最终目标不是 OpenVLA-OFT 本身。

- 笔记本：NVIDIA RTX 4060 Laptop，8 GB 显存，承担轻量仿真、软件开发、接口和小规模测试。
- 实验室：之后再承担大模型推理/训练与真机实验；实验室计算资源和驱动尚未核实。
- 最终模型方向：以 π₀.₅ / openpi 为基础，参考 G³VLA 多相机几何方法，加入 Gripper-Centric Contact 预测。上述研究模块目前没有实现。
- OpenVLA-OFT 只是首个可替换的 VLA Adapter。模型接口必须独立，后续换模型不应重写 GUI 或仿真器。
- 真实设备：Franka Emika Panda（FER，模型标识 `fer`）；两台 Intel RealSense D455，一台腕部、一台桌面固定，只使用 RGB。夹爪可能为原装 Franka Hand，仍待实物确认。
- 当前没有真实机器人/相机连接、实机标定或已验证的真实 VLA 推理。
- 不自动下载大型模型、不启动训练或微调；不升级、降级或重装已运行的环境，不改第三方源码、驱动、CUDA、全局 LIBERO 配置来凑测试结果。
- 沿用原生 PySide6 桌面架构，所有新增界面继续支持中英文一键切换。实际运行与测试后才报告完成功能。
- 用中文解释变更、用途、测试证据、未解决问题和下一步。常规可逆开发继续推进，不要不断要求用户重复确认。

## 2. 项目位置与已发布版本

GitHub：<https://github.com/6f6sn7w4pb-ops/robolab-studio>

本次交接核对的已发布基线为 `main`：

```text
3f86aa013956b8a4724d0d6d02225a842f09553d
```

仓库当前为公开仓库。发布内容为平台源码、脚本和开发文档；原始毕设资料、权重、虚拟环境、运行时设置、日志、实验输出未上传。

| 用途 | 路径 |
| --- | --- |
| Windows 当前工作区 | `C:\Users\24841\Documents\ChatGPT\毕设环境搭建` |
| Windows 源码/文档副本 | `C:\Users\24841\Documents\ChatGPT\毕设环境搭建\robolab-studio` |
| 实际 WSL 项目 | `/home/openvla/projects/robolab-studio` |
| WSL 发行版 / 用户 | `Ubuntu-22.04` / `openvla` |
| Windows 文件管理器访问实际项目 | `\\wsl.localhost\Ubuntu-22.04\home\openvla\projects\robolab-studio` |
| 仿真后端 Python | `/home/openvla/venvs/openvla-oft/bin/python` |
| 桌面 GUI Python | `/home/openvla/projects/robolab-studio/.venv/bin/python` |
| LIBERO | `/home/openvla/projects/LIBERO` |
| OpenVLA-OFT 源码 | `/home/openvla/projects/openvla-oft` |

Windows 与 WSL 项目是两个独立 Git 工作树，不是自动同步的链接。两边基线均已对齐到上述提交；编写本交接前均干净。**本交接文件是本轮新增的本地文件，Windows/WSL 各有一份，尚未提交或上传 GitHub。** 开始下一批时先检查实际 Git 状态，勿覆盖用户后续改动。运行与实际仿真测试在 WSL 执行，不使用 Windows Python 运行 MuJoCo 后端。

## 3. 已核查的环境

| 项目 | 版本/状态 |
| --- | --- |
| Ubuntu / Python | WSL2 Ubuntu 22.04 / Python 3.10.12 |
| GUI | 独立 `.venv`，PySide6 6.8.3 |
| MuJoCo | 2.3.7 |
| robosuite | 1.4.1 |
| LIBERO | 0.1.0，可编辑安装；commit `8f1084e3132a39270c3a13ebe37270a43ece2a01` |
| OpenVLA-OFT | 0.0.1，可编辑安装；commit `e4287e94541f459edc4feabc4e181f537cd569a8` |
| PyTorch | 2.2.0+cu121，CUDA 可用 |
| numpy / Pillow | 1.26.4 / 12.3.0 |
| OpenCV | 4.11.0.86；GUI 不导入它，避免 Qt 插件冲突 |
| GUI 仿真渲染 | WSLg Wayland + glfw，已实际通过 |
| P1 离屏渲染 | 独立子进程 EGL，清除 DISPLAY/WAYLAND_DISPLAY，已实际通过 |

OFT 存在尚未修复的导入错误：

```text
ImportError: cannot import name 'runtime_version' from 'google.protobuf'
```

已知链路为 prismatic → RLDS → dlimp → tensorflow_datasets → tensorflow_metadata。当前 protobuf 4.25.9 / tensorflow-metadata 1.21.0 / TensorFlow 2.15.0 存在运行时兼容问题；`pip check` 通过也不代表这些导入正常。此故障不阻断无 VLA 的 LIBERO 仿真，**不要为下一批 GUI/控制开发直接升级 protobuf 或修装现有研究环境**。本机尚无已确认完整权重或已配置远程推理服务。

已有 robosuite 私有宏提示、Gym 维护提示和旧测试中的临时 XML ResourceWarning 属于已记录的非阻断问题，不通过修改库来隐藏。

## 4. 已完成功能

### P0 / P1 与桌面基础

- 环境检查和依赖/受保护文件基线；原有环境及 LIBERO/OFT 源码保持不变。
- 原生桌面 GUI，独立 QProcess 后端，JSON 行协议 v1。MuJoCo 和 GL 在后端主线程使用。
- LIBERO 任务列表与加载，默认 LIBERO-Object 第一个汤罐放篮筐任务；不是静态示例场景。
- 真正 MuJoCo 三维渲染、旋转/平移/缩放、射线选择、聚焦、场景树、属性检查。
- LIBERO 自由物体位姿编辑；添加/删除基础立方体、球、圆柱，修改尺寸、质量、摩擦及碰撞。
- 七关节与夹爪控制、末端世界位姿，物理运行/暂停/停止/重置/20 ms 单步；保存/恢复场景布局和截图。
- 当前编辑场景双相机实时 RGB 预览；P1 独立离屏测试入口、日志、进度、双路 PNG/GIF 和目录操作。
- English / 中文即时切换并保存偏好；保留用户输入、原始任务指令和原始日志，不重启仿真/策略。

“物理运行”仅推进物理仿真，不代表正在执行自动任务。编辑模式会暂停并更新重置基准；重置恢复最近的编辑基准，不一定恢复最初官方初态。已有项目 JSON 是布局文件，不是完整动态回放文件。

### 策略接口

- 模型无关的 observation/action 合同、Adapter 注册表和可选插件入口。
- Mock Policy：真实双 RGB 和机器人状态 → Mock 动作 → 官方 LIBERO OSC → 实际物理变化；不标为真实 VLA。
- OpenVLA-OFT Adapter、本地权重完整性检查、显式独立 HTTP 桥接服务。服务不会随 GUI 自动下载或加载模型。
- 模型状态分开显示：仅配置、权重文件找到、模型已加载、真实推理通过；Mock/规则策略不能提升真实模型状态。
- 模型协议中的 LIBERO 动作是归一化末端增量 6 维 + 夹爪 1 维，不是七关节角。手动/规则关节控制与该 OSC 动作空间分开。

### P2 第一批：默认规则抓取与记录

- 右侧“策略 / 自检 → P2 规则抓取与记录（非 VLA）”。规则使用仿真真值、IK、关节 PD，不是 VLA。
- 验证双指接触 ≥0.20 s、在双指接触下提升 ≥0.10 m 并持续 ≥0.50 s、最后由官方 LIBERO 判定任务完成。三项通过且正常结束才记 P2 通过。
- 支持暂停、20 ms 单步、继续、取消；取消形成独立 cancelled 记录，不复用历史成功。
- 输出 `outputs/p2_grasp/<UTC时间_标识>/metadata.json` 与 `trajectory.jsonl`，界面支持分项结果、复制路径和打开目录。
- 元数据 schema 为 `robolab.p2_episode.v1`；记录代码/版本/任务/初态/阈值/样本数/轨迹哈希等。
- 轨迹记录时间、阶段、实际关节位置/速度、关节目标/限速伺服目标、夹爪、原生控制量、末端世界位姿、目标世界位姿和两个手指接触布尔值。

**现有记录缺少每帧全场景状态、求解器快照和双 RGB；不能直接宣称完整、精确的 episode 回放或可训练数据集。接触仅为手指与目标的布尔值，不是 P3 指尖接触点/法向/力标签。** 位姿四元数是 wxyz，长度 m、角度 rad；手臂原生控制量为力矩，手指执行器为位置，不能混用单位。

默认规则抓取只针对已支持的汤罐任务，带额外编辑器刚体的场景不属于当前验收范围。没有任意物体自动抓取或多任务泛化结论。

## 5. 已有真实测试证据

- P2 默认 seed 0 / 官方初态 0，3 次均成功；每次 22 秒仿真、1101 个样本，最终 qpos 差为 0。
- 最大提升约 0.284522 m，最大连续双指接触约 3.92 s，持续有效提升约 1.82 s；伺服变化率和控制量范围检查通过。
- 禁用接近 IK 的独立失败对照，实际未抓住物体，被正确判为 failed；不是把假轨迹当成功。
- GUI 实际操作验证暂停/单步/继续、双语结果、路径复制、取消与关闭清理。
- 发布阶段共 26 项轻量测试通过（runtime 4、policy 8、P2 证据 4、schema 3、i18n 7）。
- 仅含发布文件的新目录（路径含空格）中，P1 两路 256×256 RGB、20 个控制步通过；P2 完整桌面操作及结果保存通过。
- 已从 GitHub 真正重新克隆 62 个文件并逐一核对 blob；新副本 `--doctor` 和 GUI `--smoke` 均通过，所属后端关闭干净。
- GitHub Actions 轻量检查通过：<https://github.com/6f6sn7w4pb-ops/robolab-studio/actions/runs/41532124914>。
- 发布/迁移测试复用了原机已有后端和 GUI 环境；**全新隔离安装、实验室新硬件、X11 桌面以及真实 VLA 推理尚未验收**。

主要本机证据目录：`outputs/p2_verification/`、`outputs/p2_audit/`、`outputs/p2_gui_test/`、`outputs/github_release/`、`evidence/`。这些是被 Git 忽略的真实本机数据，新克隆不会带上它们。发布证据摘要为 `outputs/github_release/publication.json`。

## 6. 下一批：先完成 P2 第二批

请先读取代码并汇报当前状态和小批开发计划，然后实际实现，不要只给建议或界面外观。优先顺序如下：

1. **末端 XYZ 小步点动**：沿用 Panda 适配器与既有 IK/关节限速机制，明确坐标系、单位和步长；处理关节限制、不可达目标、策略运行时的控制冲突。编辑态与物理态保持既有语义，不通过直接移动目标物体来伪造运动。
2. **记录浏览**：在 GUI 中列出/打开 P2 记录，显示任务、初态、状态、样本数及接触/提升/官方成功；区分成功、失败、取消、未完成或损坏的数据。
3. **有明确语义的状态回放**：先评估现有记录能够恢复什么。可增加有版本区分的新记录字段来支持回放，同时兼容 v1。旧记录缺少的数据必须明确提示；状态展示、物理重新执行、原始成功证据不能混为一谈。回放不要破坏当前编辑布局或启动真实模型/硬件。
4. **少量不同 seed/官方初态测试**：如实记录失败与适用范围，不把单一初态重复成功解释为泛化性能。

每小批完成后跑与改动相关的实际测试；新增 GUI 功能提供真实操作验收并验证中英切换。默认任务原有抓取、P1 相机与 Mock 接口按风险做针对性回归，不为简单文档改动重复跑全部长测试。

P2 完成后再进入 P3：筛选指尖与目标接触，提取接触点、法向/力，转换夹爪坐标，覆盖空接触与坐标转换测试及可视化。P4 场景/标签/数据划分和 P5 批量评估仍待开发，本轮不要跳到完整研究模型或训练。

## 7. 优先阅读与代码定位

按项目根目录定位以下文件：

| 文件 | 用途 |
| --- | --- |
| `README.md` | 当前功能、启动与边界 |
| `docs/LAPTOP_ROADMAP.md` | 已完成/下一批/P3–P5 路线 |
| `docs/P2_REPORT.md` | 抓取判定、记录语义、真实测试 |
| `docs/GITHUB_RELEASE.md` | 可移植配置、发布阶段测试与限制 |
| `docs/INSTALL.md` | 实验室复用环境/新环境两条安装路线 |
| `docs/VLA_INTEGRATION.md` | 模型合同与独立推理进程 |
| `docs/HARDWARE_PROFILE.md` | FER/D455 硬件确认与未确认项 |
| `docs/LANGUAGE.md` | 中英切换机制与测试 |
| `docs/ENV_AUDIT.md` | 历史 P0 依赖故障与受保护基线；“尚无 Git/P2”等为当时状态 |
| `robolab/gui.py` | 原生窗口、控制面板、结果卡 |
| `robolab/client.py` / `worker.py` | QProcess 协议与主线程物理/GL |
| `robolab/engine.py` | 任务、模型、编辑、物理步与执行状态 |
| `robolab/adapters.py` | 机器人/相机/硬件协议及 Panda 限制 |
| `robolab/task_control.py` | 规则抓取与策略执行 |
| `robolab/p2_recording.py` | 接触布尔值、提升证据、v1 记录 |
| `robolab/policy.py` / `openvla_service.py` | Mock、VLA 注册、权重检查与显式服务 |
| `robolab/p1_panel.py` | 双相机预览与独立 P1 子进程 |
| `robolab/i18n.py` / `locales/en.json` | 本地化；动态文本复用 `set_text`/`tr` |
| `robolab/config.py` / `scripts/runtime.sh` | 运行时路径与进程环境 |
| `tests/` / `robolab/qa_*` | 单测与真实桌面验收 |

原始需求文档 `Codex_毕业设计双环境仿真开发交接.md` 保存在本机项目根目录，被 Git 忽略；本机可完整读取，但不要假设从 GitHub 克隆后也存在。后续明确的 FER/D455 确认信息和本交接当前进展优先于该文档的历史未知项。若原始文档在新机器不存在，可先依据仓库文档和本交接继续，不要重复询问已有信息。

## 8. 运行与检查入口

在原笔记本 WSL 中：

```bash
cd /home/openvla/projects/robolab-studio
git status --short
bash scripts/launch.sh --doctor
bash scripts/launch.sh
```

Windows 可在源码目录双击 `Launch RoboLab Studio.cmd`；如路径/发行版不同，用 `ROBOLAB_WSL_PROJECT`、`ROBOLAB_WSL_DISTRO`、`ROBOLAB_WSL_USER` 配置。启动后修改代码不会自动热更新，要关闭旧窗口重开。

按需要选择验证入口：

```bash
bash scripts/smoke_test_libero.sh       # 实际 P1，独立 EGL，无桌面依赖
bash scripts/launch.sh --smoke          # 真实桌面加载、运行/暂停/重置、清理
bash scripts/launch.sh --p2-test        # 真实桌面抓取、暂停/单步、结果、取消
bash scripts/launch.sh --policy-test    # Mock/策略桌面回归
bash scripts/launch.sh --language-test  # 较长的双语桌面回归
```

`--doctor` 只验路径、依赖导入和默认任务资源，不证明 GL 或模型正常；`--baseline-doctor` 是原笔记本历史固定哈希核对，不适合当成实验室通用条件。

配置优先级：环境变量 → 本机 `settings/runtime.json` → 自动路径。支持 `ROBOLAB_BACKEND_PYTHON`、`ROBOLAB_LIBERO_ROOT`、`LIBERO_CONFIG_PATH`、`ROBOLAB_OFT_ROOT`；不要把 `/home/openvla` 固定到新业务代码。

**已有笔记本不运行 `bootstrap_sim.sh` 重建环境。** 该显式安装入口仅供新电脑使用；全新安装仍待验收。不要提交 `.venv`、`.venv-sim`、`third_party`、模型、数据、个人运行配置或原始实验输出。

## 9. 下一对话的交付要求

先检查版本、工作区和现有环境，不重复搭建 P0/P1，不把 P2 第一批当成缺失功能重写。对计划和不确定项做简洁中文说明，随后完成上述 P2 第二批中的实际可验收工作。报告修改文件、GUI 操作方法、测试命令/结果/证据路径、尚未验证的范围及下一小批任务。保留失败证据；界面出现按钮、日志写“已加载”或 Mock 返回动作，都不能代替真实执行验收。


## 10. P2 第二批本地接续更新（2026-11-02）

本节为本轮追加，前文原样保留；当前进度以本节和 [P2_BATCH2_REPORT.md](P2_BATCH2_REPORT.md) 为准。

- WSL 已实现世界坐标 1–10 mm XYZ 点动、控制互斥、五类 P2 记录浏览、v2 独立采样状态查看；v1 仅浏览结果。不是完整物理回放或原始双 RGB 回放。
- 34 项轻量检查、真实桌面点动／浏览／双语／Mock OSC／双 RGB 验收通过；5 次正常抓取、4 个官方初态通过。相同官方初态下只换 seed 不增加布局覆盖。
- 证据：outputs/p2_batch2/{features,matrix,gui,final-checks}.json。默认最终 qpos 与旧 v1 基线最大差为 0；环境保护比对通过。首次四元数浮点断言失败保留，最终容差复核通过。
- 启动：bash scripts/launch.sh；新增桌面验收：bash scripts/launch.sh --p2-batch2-test。已关闭验收窗口和所属后端。
- 本轮修改只在 WSL，未同步 Windows 源码树，未提交或推送，Git HEAD 仍为 3f86aa013956b8a4724d0d6d02225a842f09553d。后续先检查并保留这些未提交改动。
- OFT protobuf 故障未修复；没有真实 VLA、训练或硬件测试。下一小批可进入 P3 指尖接触筛选、点／法向／力与夹爪坐标转换。


## 11. P3 设计规划更新（2026-11-03）

- 用户已要求开始设计规划。设计保存于 [P3 设计目录](design/p3/README.md)，A–C 批尚待实施；不能标为 P3 功能完成。
- A 批先实现原生 Euler PD 接触采样、指垫身份和夹爪坐标／力方向核验；B 批 sidecar 与旧记录兼容；C 批原生 GUI 和相关回归。
- 已核对真实保存 XML 的 gripper0_grip_site 和两指 pad_collision；明确区分求解器输入时刻与积分后时刻。Mock/OFT 内部执行路径的原始接触力首版暂不支持。
- 独立语义探针和 12 项合同草案检查已完成，仅证明设计依据；未运行新的 Panda P3 抓取验收、模型或硬件。证据在 outputs/p3_design/。
- 业务源码保持本轮开始时的哈希；未安装、升级环境，未提交或推送，Windows 源码树未同步。


## 12. P3 A 批实现更新（2026-11-04）

- 用户已授权开发，A 批完成并真实验收。新增原生 Euler PD 尾子步采样、指垫分类、世界/夹爪坐标及力方向转换。默认关闭；GUI 接触面板尚未实现。
- CLI：bash scripts/launch.sh --p3a-test；--quick 只做能力与单步检查。API 为 configure_contacts / contact_sample / contact_sampling_status，IPC 为 contact_configure / contact_sample。
- 默认 seed 0 / init 0，采样关闭和开启均通过；1100 个 P3 采样、1101 行 P2，逐帧控制与最终 qpos 相同（差为 0）。物理失败对照没有误报承载接触。
- 41 项轻量测试、真实桌面 smoke 和环境保护比对通过。完整证据 outputs/p3a_verification/，报告 [P3_A_REPORT.md](P3_A_REPORT.md)。
- 原规则流程、PD 控制器和 P2 持久化代码内容保持；Mock 原始力明确不支持，但官方 OSC 动作链路通过。
- 下一批从 B 开始：正式 sidecar、哈希、父轨迹关联和旧记录兼容；随后 C 为接触 GUI 和扩展初态，不要重写 A。
- 未提交/推送；Windows 旧源码树未同步；OFT protobuf 故障仍在，无真实 VLA、训练或硬件验收。


## 13. P3 B 批实现更新（2026-11-10）

- B 批已完成并验收。显式启用 `record_contacts=True`，沿用 A 批原生接触采样并独立写 `p3_contact_metadata.json` 与 `p3_contacts.jsonl`，校验帧时间、维度、索引、父轨迹和哈希。
- GUI 的 P2 记录详情显示独立双语 P3 状态。旧 v1/v2 为未采集，损坏的 P3 不改变 P2 原判定或有效的 P2 状态查看。
- CLI 为 `bash scripts/launch.sh --p3b-test --output-dir outputs/p3b_check`，可用 `--inspect <episode>` 只读查看。实际证据见 `outputs/p3b_verification/`，细节见 [P3_B_REPORT.md](P3_B_REPORT.md)。
- 默认开启与关闭两次真实抓取 P2 轨迹逐字节一致。正式成功记录 1100 帧，物理失败对照 550 帧，实际取消 0 和 3 帧。写入故障、读取夹具、旧 v1/v2、1653 帧结构校验和真实桌面双语均通过。36 项后端和 7 项本地化回归通过。
- 下一批 C 做基于原始求解器时刻的接触图形和历史查看，并扩展官方初态。同步原始双 RGB、参数化部位抓取和训练数据属于后续 P4。当前 WSL HEAD 未变，无环境安装或升级、无真实 VLA、无硬件、未提交推送。


## 14. P3 C 批实现更新（2026-11-10）

- P3 A–C 已验收。新增实时接触预览、原生样本的历史桌面／腕部／半透明近景、筛选、法向、可选力箭头与夹爪轴。图像从独立求解器输入姿态重建。GUI 可在默认汤罐规则抓取前勾选保存 P3，旧记录沿用已有 P2 查看。
- 真实默认汤罐五次，覆盖官方初态 0–3，全部 P2 通过且各有 1100 帧 P3。seed 0/1 同为 init 0 时初始 qpos 哈希相同。验证了实时和历史姿态隔离、Mock 原生标签不可用、双 RGB 和双语桌面。36 项后端和 7 项本地化测试通过。
- 证据 `outputs/p3c_acceptance/`、`outputs/p3c_gui_final_layout/`，操作见 [P3_C_REPORT.md](P3_C_REPORT.md)。首轮历史视图沿用 P2 数据的缓存问题已隔离修复，原首轮证据保留。
- 下一批 P4 先做双部位参数化物体、同场景两条指令与指定／实际接触部位判定，采集同一求解器时刻的双 RGB，再做小批数据试采和划分。M2 数据闭环仍待完成。未提交推送，Windows 源码副本未同步，未改环境、未做真实 VLA 或硬件验收。


## 15. P4 A 批实现更新（2026-11-10）

- 双部位台阶形物体、双语粗／细端指令、规则教师及部位／提升独立判定已实测。合成场景独立于官方 LIBERO 任务。三次同初态运行中，正常粗／细端均通过，指定粗端但实际抓细端的对照虽抬起仍判为失败。
- 每次 555 个原生接触帧和 111 对同一求解器时刻的 224×224 RGB，保存标定、指定中心、接触、投影和原生动作。三次 666 张原始 PNG 均通过重读和哈希校验。两台相机的代表帧复渲染逐像素一致。
- GUI「P4 部位抓取」及取消和双语实测通过。首轮 SIGTERM 只留下 recording 清单，后改为协作取消。P2 默认回归的轨迹与 P3-C 默认基线逐字节一致，36 项后端和 7 项本地化通过。详情见 [P4_A_REPORT.md](P4_A_REPORT.md)。
- CLI 为 `bash scripts/launch.sh --p4a-test --output-dir outputs/p4a_check`。真实证据 `outputs/p4a_acceptance/`、`outputs/p4a_gui_final/`。
- 下一批 P4-B 小批试采、几何与布局变化、障碍标签、7 维动作合同和按形状族划分。当前为固定布局的合成规则示范，未达 M2，未做真实 π₀.₅ 推理、训练和硬件。未提交推送，Windows 旧副本未同步。


## 16. P4 B 批实现更新（2026-11-10）

- 正式 24 条配对任务全通过，覆盖盒体、圆柱、椭球三个形状族和 12 个场景。训练／验证／未见为 12／4／8 条，双椭球整族留出，4 条同时留出中英文改写。模型文件和负例审计验证配对、实例、形状族和短语隔离。
- 额外物理阻挡对照真实失败，124 帧手指障碍接触，未抬起。与其余任务独立。25 次共 13,875 个接触样本、2,775 对双 RGB、2,750 段非末帧动作。V2 最近障碍为参考点至地面或盒子最近表面。
- 原生执行仍为 Panda 关节 PD。观测 7 维是相邻求解器图像对之间的世界平移／旋转加源夹爪符号。未声明为 OSC 命令，闭环动作适配先于扩量。旧 V1、细端入口、双语汇总和 3＋7 项轻量检查通过。
- CLI `bash scripts/launch.sh --p4b-pilot --output-dir outputs/p4b_check`。真实证据 `outputs/p4b_pilot/`、`outputs/p4b_pilot_gui/`。详见 [P4 B 批报告](P4_B_REPORT.md)。
- 下一批在合成场景接入并验收实际 OSC 后再扩到第一版 200 条。未到 M2，环境未改，无 VLA 或硬件，仍在 WSL 本地工作树，未提交推送。


## 17. P4 C 批实现更新（2026-11-10）

- 使用原版 robosuite 1.4.1 Panda SingleArm / OSC_POSE。实际发送归一化 7 维，20 Hz，每个周期 25 个 2 ms 子步。将首子步求解器状态和原始接触与实际命令、双 RGB 绑定，另存周期末状态。旧 A/B 仍为原生 PD。
- 同场景盒体粗端与细端、带偏航的椭球细端均通过。阻挡对照正确失败，35 帧指端障碍接触。四条各 222 周期、共 888 对 RGB 和实际动作。将文件动作送入新控制器复执行，逐周期状态、实际控制器目标及原始接触相同，qpos 最大差 0。
- 首轮旋转独立比较受 robosuite float32 精度影响，阈值改为 1e-7 并通过，精确复执行比较未放宽。旧 V1/V2 重读、细端轨迹逐字节对照、3＋7 轻量测试和真实桌面双语均通过。
- CLI `bash scripts/launch.sh --p4c-osc --output-dir outputs/p4c_check`，旧试采汇总可打开 OSC `result.json`。证据 `outputs/p4c_osc_final/`、`outputs/p4c_osc_gui/`，见 [P4 C 批报告](P4_C_REPORT.md)。
- 下一批先把实际 OSC 扩到已有划分，再向首版 200 条扩量、适配模型数据和验收 M2。环境未改，未做真实 VLA、训练或硬件，仍在 WSL 本地未提交推送。
