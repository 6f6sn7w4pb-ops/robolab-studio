# 实验室阶段交接（新对话从这里开始）

更新：2026-11-25。笔记本阶段已全部完成，之后的工作都在实验室电脑上进行。本文件汇总了此前所有对话里的背景、已经做出的决定、当前进度和下一步。新的对话（实验室电脑上的 Claude Code、云端的 Claude Code，或 claude.ai 网页对话）请先完整读完本文件，再动手。

- **整体分析**请再读 [LAB_MASTER_PLAN.md](LAB_MASTER_PLAN.md)：从开始到现在再到以后的全景，包括所有建模尺寸、实验室布局、新数据生成器的设计要求、与之前计划的不同。
- 零样本评估的完整结果见 [LAB_ZEROSHOT_REPORT.md](LAB_ZEROSHOT_REPORT.md)。

本文与其他文档冲突时，**以本文为准**：其他文档里有些内容写于更早的时候，比如“实验室是 A100 80GB”“数据集 1–2 万条”，那是旧的。

---

## 0. 给新对话的开场话

**实验室电脑上的 Claude Code**（主要工作方式）：

```text
这是我的毕业设计项目，之前的工作在另一台笔记本上完成，现在在实验室继续。请全程用中文，讲解要详细易懂。先完整阅读 ~/projects/robolab-studio/docs/LAB_STAGE_HANDOFF.md 和 docs/LAB_MASTER_PLAN.md，再读 ~/lab_logs/PROGRESS.md（实验室电脑上的进度记录），然后按交接文档第 6 节的顺序，从第一个未完成的任务接着做。动手之前先告诉我你的理解和计划。
```

**云端 claude.ai 网页对话**（讨论方案、写论文；它不能在实验室电脑上运行命令）：先把第 9 节列出的文件上传到对话或项目知识库，再说：

```text
这是我的毕业设计项目。请先阅读我上传的 LAB_STAGE_HANDOFF.md（交接文档）、LAB_MASTER_PLAN.md（项目全景）和其他文件，全程用中文、讲解详细易懂。之后我会在实验室电脑上用 Claude Code 执行具体操作，请你帮我分析结果、规划步骤、撰写论文。
```

---

## 1. 项目是什么

- **题目**：Gripper-Centric Contact for Zero-Shot VLA Grasping of Unseen Objects（面向未见物体零样本 VLA 抓取的夹爪中心接触建模）。
- **研究问题**：在相同的训练数据预算和统一的评估协议下，在 VLA 策略中引入**夹爪坐标系的接触位置监督**，能否提高**未见刚性物体**上“**按语言指定部位抓取**”的成功率，并减少碰撞？
- **任务（2026-11-20 起）**：桌上一个目标物体和 0–2 个障碍物，英文指令说明要抓物体的**哪个部位**（例如杯子的把柄），机械臂要抓对、抬起、不碰障碍。物体来自 **A 参数化日常物体、B PartNet 模型库、C 奇怪形状**三类；用**四层留出**检验推广（同类别新个体、整类别、说法、部位类型）。
- **前期任务**（至今所有数据和评估）：一根两段式短棒（一段粗、一段细，同色），指令说抓粗端或细端；训练只见过长方体族和圆柱族，椭球族整族留作未见测试。它是新任务的最简特例，结果保留。
- **基线模型**：π₀.₅（openpi）。参考论文 G³VLA（相机几何）、π₀.₅、OpenVLA 都在 LIBERO 上做仿真评测。
- **硬件**：Franka Emika Panda（FER）机械臂；两台 Intel RealSense D455，一台装在腕部、一台在外部，只用 RGB。真机实验是拓展目标。
- **平台**：RoboLab Studio（本仓库）。MuJoCo 2.3.7 + robosuite 1.4.1 + LIBERO 的本地仿真工作站，包含桌面界面、接触采样、数据集生成、批量闭环评估。

完整的技术总结见 [PROJECT_REPORT.md](PROJECT_REPORT.md)（原理讲解最详细，适合写论文时引用）；现在的全景和以后的计划见 [LAB_MASTER_PLAN.md](LAB_MASTER_PLAN.md)。

## 2. 用户的工作习惯和规则（请严格遵守）

- **全程中文**，讲解要详细、通俗；用户对仿真和机器人底层不熟悉，需要解释原理时从基础讲起。实验数据和 VLA 指令只用英文。
- **只报告实际验证过的结果**。“有按钮”“日志写已加载”“Mock 返回了动作”都不算通过；规则教师（读取仿真真值）的成功率不能说成模型性能。失败的证据要保留。
- **不要升级、降级、重装已有环境**，不要修改第三方库源码、显卡驱动、CUDA、全局 LIBERO 配置。新东西都装进隔离环境。
- **需要 sudo、删除文件、格式化磁盘、推送代码这类不可撤销或对外的操作，先问用户。** sudo 由用户在单独的 WSL 终端里执行（Claude Code 的 `!` 前缀不能输入 sudo 密码）。
- **提交和推送只在用户要求时做**。先建分支，通过 PR 合并进 `main`；提交作者沿用仓库已有提交的身份（`git log` 可查，实验室电脑上没有 git 用户配置，用 `git -c user.name=... -c user.email=...` 临时指定）。提交说明末尾加 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`，PR 描述末尾加 `🤖 Generated with [Claude Code](https://claude.com/claude-code)`。检查状态用 `git --no-optional-locks status`。
- 不要提交 `outputs/`、虚拟环境、模型权重、数据集、个人配置。仓库是**公开的**，不要写入个人信息。
- 推送后要确认 GitHub 上的自动检查（Lightweight checks）通过，再说完成。
- 动手之前先说明理解和计划；跑完一步先报告和分析，等用户看过再做下一步。

## 3. 已经做出的决定（不要再推翻，除非用户提出）

| 决定 | 内容 | 原因 |
| --- | --- | --- |
| 不换 ManiSkill3 | 继续用 MuJoCo + LIBERO | 老师让比较过。参考论文都用 LIBERO；官方 `pi05_libero` 权重和评测直接可用；已验证的平台和数据能保留 |
| 只用 LoRA 微调 | openpi 的 **JAX 版**（PyTorch 版不支持 LoRA） | 老师要求（担心全参数不收敛）；实验室显卡 48 GB，全参数需要 70 GB 以上，本来也跑不了 |
| π₀.₅ 的 LoRA 配置要自己写 | 参照 openpi 里 `pi0_libero_low_mem_finetune`：`paligemma_variant="gemma_2b_lora"`、`action_expert_variant="gemma_300m_lora"`、`freeze_filter=...get_freeze_filter()`、`ema_decay=None`，再加上 `pi05=True, action_horizon=10, discrete_state_input=False`（草案见 LAB_MASTER_PLAN 14.1 节） | openpi 只有 π₀ 的 LoRA 配置，没有 π₀.₅ 的；能否正常训练要实测 |
| 只有一台电脑 | 没有外部服务器；生成数据、训练、评估全在实验室这台电脑上，**排队轮流做** | 训练时显卡基本被占满 |
| 任务扩展 | 从“粗端/细端”扩展到**任意命名部位**；物体用 **A 参数化日常物体 + B PartNet + C 奇怪形状** | 用户 2026-11-20 决定 |
| 四层留出 | 同类别新个体、整类别留出、留出说法、留出部位类型；留出的部位类型要出现在训练见过的类别上 | 用户 2026-11-20 决定；测试集结构见 LAB_MASTER_PLAN 13.6 节 |
| 只用英文 | 实验数据和评估只用英文指令（界面仍中英文切换） | 用户 2026-11-20 决定 |
| 数据集 | **5,000 条**，先 2,000 条试训；按**实验室布局**生成；小数据集是大数据集的前缀，扩充不重新生成；5,000 条时原始记录、HDF5、LeRobot 三份都保留 | 用户 2026-11-20 决定（原计划 1–2 万条）。现有 200 条是旧布局，用于调试训练流程 |
| 全部改用实验室布局 | 新数据和新测试场景都用实验室布局（两张桌子、真实相机位置）；旧布局的 240 条测试和零样本结果作为前期工作保留，规则教师和零样本要在新布局上重跑 | 用户 2026-11-20 决定 |
| 相机位置 | **在仿真里定好，真机支架按仿真位置 3D 打印**；不再测量腕部支架；两台 D455 的内参已于 2026-11-25 读出（[D455_CAMERA_PARAMS.md](D455_CAMERA_PARAMS.md)） | 导师意见（2026-11-20） |
| 外部相机 | 物体桌**正对面**、离地约 **1.30 m**（比桌面高约 56 cm、远边外约 30 cm、俯视约 43°）；备选左侧 1.30 m | 仿真对比结果，见 [LAB_CAMERA_STUDY.md](LAB_CAMERA_STUDY.md) |
| 腕部相机要倾斜 | 倾角 ≈ arctan(偏移 ÷ 镜头到指尖中心距离) − 20°；偏移 6/8/10 cm 时约倾 10°/20°/30°；具体值在仿真里选定 | 同上 |
| 训练、评估都用实验室机器生成的数据 | 用 `outputs/p4d_lab` 及以后在实验室生成的数据 | 不同显卡渲染的图像有细微差别；训练和评估同一台机器才能保证输入一致 |
| 磁盘 | 第 1–4 步在 C 盘做，始终留 15 GB 以上；1 TB 移动固态硬盘作为工作盘（第 7 节）；**硬盘到之前不生成新数据集、不开始正式训练** | 用户 2026-11-20 决定 |
| 批量 32 | 之后所有要比较的模型都用批量 32；`max_token_len` 保持 π₀.₅ 的默认值 200 | 用户 2026-11-25 决定，依据 [LAB_LORA_PROBE.md](LAB_LORA_PROBE.md) |
| 正式训练的规则 | 每个模型的训练步数用 2,000 条试训的验证曲线来定；M1、M2、M3 各跑 2–3 个随机种子；学习率的 `decay_steps` 和 `num_train_steps` 对齐，所有要比较的模型完全一致；`keep_period` 设为 10,000，用验证集挑出最好的检查点后删掉其余的（删除前问用户）；长时间训练前设 Windows 不睡眠、推迟自动更新重启，中断后用 `--resume` 续训。详见 LAB_MASTER_PLAN 14.2 节 | 用户 2026-11-25 决定；训练时间不是约束 |

## 4. 实验室电脑

| 项目 | 情况 |
| --- | --- |
| 系统 | Windows + WSL，发行版 `Ubuntu-22.04`；项目都在 WSL 用户主目录 `~` 下 |
| 显卡 | NVIDIA **RTX 6000 Ada，48 GB**，驱动 596.71，CUDA 13.2（**不是 A100 80GB**） |
| 内存 / CPU | 30 GiB / 20 核；每个仿真评估进程约 2.7 GB 内存 |
| 空间 | WSL 的虚拟磁盘在 **C 盘**，2026-11-20 剩 **98 GB**。**查看真实剩余用 `df -h /mnt/c`**（WSL 里 `df -h /` 显示的是虚拟磁盘上限，不准），始终留 15 GB 以上 |
| 已有的别人的环境 | `~/vla/.venv`：**不要用，也不要改**。里面的 robosuite 版本不对，还误装了无关的 `robotsuite`。终端里可能默认激活着它，运行我们的命令前要 `deactivate` 或去掉 `VIRTUAL_ENV`（`uv pip` 会优先装进激活的环境） |
| 用户权限 | 有 WSL 里的 sudo 密码；sudo 命令由用户自己执行 |

**已装好的东西：**

| 内容 | 位置 | 版本 |
| --- | --- | --- |
| RoboLab Studio | `~/projects/robolab-studio` | 本仓库（桥接服务 `pi05-bridge` 已于 2026-11-23 通过 PR #11 合并） |
| 仿真环境 | `~/projects/robolab-studio/.venv-sim` | Python 3.10.12；MuJoCo 2.3.7；robosuite 1.4.1；NumPy 1.26.4；LIBERO 0.1.0（提交 `8f1084e3132a39270c3a13ebe37270a43ece2a01`，在 `third_party/LIBERO`）；torch 2.2.0+cpu；h5py 3.16.0 |
| 界面环境 | `~/projects/robolab-studio/.venv` | PySide6 6.8.3 |
| uv | `~/.local/bin/uv` | 0.13.9 |
| openpi | `~/projects/openpi` | 提交 `215abfb`（2026-08-24）；环境 `.venv`（Python 3.11.18，jax/jaxlib 0.5.3，flax 0.10.2，约 7.7 GB）；JAX 能用显卡 |
| π₀.₅ 权重 | `~/.cache/openpi/openpi-assets/checkpoints/pi05_libero` | 12 GB；分词器在 `~/.cache/openpi/big_vision/` |

**不要用 `pip install libero`**：PyPI 上的 0.1.1 是 HuggingFace 的分支（lerobot-libero），要求 MuJoCo ≥ 3.0、robosuite 1.4.0，和我们不兼容。

## 5. 已完成的工作

### 5.1 笔记本阶段（2026-11-01 至 11-17，全部完成）

| 阶段 | 结果 |
| --- | --- |
| P0–P1 | 环境核查；双相机离屏渲染；桌面界面 |
| P2 | 规则抓取闭环与三项物理判定；末端点动、记录浏览 |
| P3 | 指尖接触采样、夹爪坐标系转换（p_g = Rᵀ(p_w − t)）、接触记录与可视化 |
| P4 | 双部位物体（长方体/圆柱/椭球）、成对指令（已见/留出说法）、真实 OSC 采集；**第一版数据集 200 条**，泄漏审计，HDF5 训练视图（含夹爪系接触标签） |
| P5 | 批量闭环评估：成功/部位命中/抬起/碰撞，Wilson 区间；策略看到的输入与训练数据逐字节一致 |
| P5-B | 扩展测试集 240 条（2×2：形状族已见/未见 × 说法已见/留出，每格 60）；圆柱防滚；外观变化；多运行对比报表（McNemar） |
| 实验室建模 | 按实测尺寸建两张桌子和 D455；外部相机位置和腕部倾角研究；机械臂在物体桌离底座 30–70 cm、左右 ±40 cm 内都能抓（24/25） |
| 数据集指纹 | `docs/fingerprints/p4d_dataset_v1.json.gz`，用于跨机器核对 |

细节见 [PROJECT_REPORT.md](PROJECT_REPORT.md) 第二部分和各批报告。笔记本上的 `outputs/` 已打包存档（1.6 GB，只在笔记本上），原目录和 11 GB 数据集已删除。

### 5.2 实验室第一天（2026-11-18）

1. **安装**：补装系统库（见 [INSTALL.md](INSTALL.md)，含编译工具 `build-essential linux-libc-dev python3.10-dev`），用 `bootstrap_sim.sh` 建隔离环境，补装 h5py。
2. **复刻验证，全部通过**：
   - 后端测试 98/98；界面测试和 `--smoke` 通过。
   - 重新生成数据集 `outputs/p4d_lab`，指纹核对结论 `images_differ_physics_identical`：物理逐步一致，图像因显卡不同有差别。归一化统计 `norm_stats.json` 与笔记本逐字节相同。训练视图导出约 6 GB，校验通过。
   - 规则教师评估 `outputs/lab_eval_teacher`：64/64 成功，阻挡对照 4/4 检出碰撞，与 `p4d_lab` 逐帧比对 68/68 一致，各项检查全部为 true。
3. **openpi**：安装成功，没有用 Docker。下载 `pi05_libero`（匿名下载，无需谷歌云账号，约 110 MB/s）。
4. **推理测试**（随机假观测）：输出 (10, 7)，全部有限；加载 9.0 s，首次推理 19.8 s（编译），之后平均 **88 ms**；占显存约 **8.4 GiB**。夹爪一维略超 ±1（−1.0007 ~ 1.0109）。
5. **桥接服务**：`scripts/serve_openpi_bridge.py`、`robolab/policy.py` 里的 `OpenpiAdapter`（注册名 `pi05`）、`tests/test_openpi_bridge.py`（14 项，不依赖显卡）；后端测试 112 项通过；2 条小规模试跑通过。代码提交 `37b4e4c`，2026-11-23 以合并提交并入 `main`（PR #11，`917e890`），零样本评估记录的就是这个提交号；当初标记“未完成”的空提交已去掉。

### 5.3 2026-11-20

1. **零样本评估完成**（`outputs/lab_eval_pi05_libero_zeroshot`，旧布局 240 条）：成功 **36/240 = 15.0%** [11.0, 20.1]；部位命中 31.7%；抬起 46.7%；碰撞 32.1%；接触时部位正确率 51.8%（粗端 76.1%，细端 27.1%，模型基本不区分部位）；43 条把机械臂伸向障碍物；**0 条策略错误**（最大夹爪超出 0.0244，全部在裁剪范围内）；服务端平均推理 111 ms；总用时 27.5 分钟。接口检查（朝物体移动、夹爪开合、控制和图像方向）全部正确。复跑不一致是因为 openpi 的采样噪声取决于请求到达服务的顺序。详见 [LAB_ZEROSHOT_REPORT.md](LAB_ZEROSHOT_REPORT.md)。
2. 和用户定下了第 3 节标注 2026-11-20 的决定；写了 [LAB_MASTER_PLAN.md](LAB_MASTER_PLAN.md)。

### 5.4 2026-11-23

1. **第 2 步完成**：桥接服务经另一个对话审过（在 Python 3.13、numpy 2.5 的环境里 14 项桥接测试也通过），PR #11 以合并提交并入 `main`，`37b4e4c` 保留在 `main` 历史里。
2. **第 3 步完成：现有训练集转成 LeRobot**（`scripts/convert_p4_to_lerobot.py`，用 openpi 环境的 Python）。
   - 数据集 `robolab/p4d_v1_train`，位于 `$HF_LEROBOT_HOME/robolab/p4d_v1_train`（`HF_LEROBOT_HOME=outputs/lerobot`）；LeRobot v2.1（openpi 固定的版本）。
   - 只有基线字段：`image`、`wrist_image`（224×224×3，`dtype: image`，即无损 PNG，没有视频）、`state`（8）、`actions`（7）、`task`（英文）；**fps 20**；图像方向 `libero_rotated_180`（规范图像水平翻转）。
   - 读回校验（另一条代码路径直接读 HDF5）：**120 条 / 26,640 帧，53,280 张图像逐像素完全一致**，状态、动作、指令、时间戳全部一致。
   - 大小 **2.72 GB**（每条 22.7 MB）。
   - **可以往已有数据集追加**：重新打开后追加一条，结果和一次写完的数据集逐字节相同。
   - 数据集用已提交的代码（`0b6a173`）转换，记录 `dirty: false`；和之前用未提交代码转出的版本逐字节相同。脚本留了 `EXTRA_FIELDS` 接口，以后加预期接触等标签。
3. **第 4 步完成（测量）**：π₀.₅ LoRA 配置写在我们自己的仓库（`scripts/openpi_robolab.py`），运行时注册进 openpi 的 `_CONFIGS_DICT`，再调用 openpi 的 `compute_norm_stats.py`、`train.py` 的 `main`，不改 openpi 源码。已下载 `pi05_base`（11.6 GiB）。各 300 步：批量 32 每步 5.43 s、显存峰值 23.3 GiB；批量 64 每步 12.0 s、峰值 31.5 GiB（需 `XLA_PYTHON_CLIENT_MEM_FRACTION=0.9`，默认 75% 上限下第一步显存不足）；显卡利用率约 100%（不是卡在读数据）；检查点每个 9.6 GB；损失约 0.08 → 0.019。建议批量 32，待用户确认（2026-11-25 已定为 32，见 5.5 节）。详见 [LAB_LORA_PROBE.md](LAB_LORA_PROBE.md)。

### 5.5 2026-11-25

1. **两台 D455 的参数已读**（用户在 Windows 上读；PR #12 是读取脚本，PR #13 是 [D455_CAMERA_PARAMS.md](D455_CAMERA_PARAMS.md) 和 `docs/calibration/d455_cameras.json`）：仿真用的 fovy 第一台 63.789°、第二台 65.031°。在实验室电脑上核对过：文档里的视场角和 fovy 都能由原始内参重算出来；MuJoCo 2.3.7 的相机只能填 fovy（不认 `resolution` 等内参属性）。
2. **用户的决定**：批量定 32（`max_token_len` 保持默认 200）；正式训练的规则见第 3 节和 LAB_MASTER_PLAN 14.2 节。
3. **清理**：经用户同意，删除了 `probe_bs64_mf090`、空目录 `probe_bs64` 和 `outputs/lerobot/robolab/p4d_v1_train_clean`。`probe_bs32` 保留，用来做冒烟测试（见第 7 节）。
4. **要等另一个对话的 PR 合并后才做的**：冒烟测试（正式训练前）、第 5 步相机。

日志：实验室电脑的 `~/lab_logs/2026-11-18/`、`~/lab_logs/2026-11-20/`、`~/lab_logs/2026-11-23/`、`~/lab_logs/2026-11-25/` 和 `outputs/p4d_lab/logs/`；进度记录：`~/lab_logs/PROGRESS.md`（含确切命令）。

## 6. 下一步（按顺序）

| # | 任务 | 完成标志 |
| --- | --- | --- |
| 1 | ~~续跑 π₀.₅ 零样本评估~~ | **已完成（2026-11-20）**，见 5.3 节 |
| 2 | ~~复核桥接服务并合并 `pi05-bridge`~~ | **已完成（2026-11-23，PR #11）**。“固定噪声”、观测改用无损 PNG、把桥接测试加进 CI 由另一个对话合成一个单独的 PR（见第 7 节）；官方 LIBERO 复现留到显卡空闲时 |
| 3 | ~~现有数据转 LeRobot 格式~~ | **已完成（2026-11-23）**，见 5.4 节。接触相关标签以后通过转换脚本的 `EXTRA_FIELDS` 加进同一份数据，按“部位表”设计、不写死粗端/细端 |
| 4 | ~~写 π₀.₅ LoRA 配置并做短时训练~~ | **已完成**（2026-11-23 测量，见 [LAB_LORA_PROBE.md](LAB_LORA_PROBE.md)；2026-11-25 用户定批量 32，之后所有要比较的模型都用 32）。测试检查点 `probe_bs32` 留着做冒烟测试（见第 7 节） |
| 5 | 相机（**另一个对话的 PR 合并后再做**） | 在仿真里定腕部相机偏移、高度、倾角和外部相机位置，输出 3D 打印支架的尺寸说明；两台 D455 内参已读（2026-11-25，[D455_CAMERA_PARAMS.md](D455_CAMERA_PARAMS.md)：第一台 fovy 63.789°、第二台 65.031°），确定哪台在腕部后按相机填进 `robolab/lab_scene.py`；装好后真机照片与仿真画面叠图核对 |
| 6 | 工作盘（移动硬盘到货后） | 按第 7 节建 vhdx 工作盘并挂进 WSL；`outputs/` 和 openpi 检查点目录换成链接 |
| 7 | 新数据生成器（实验室布局） | 先写设计说明给用户和导师确认；再分阶段实现：A0（通用部位格式 + 通用规则教师，用两段式短棒验证）→ A（参数化日常物体）→ 按四层留出生成新测试场景（约 360 条），跑规则教师和零样本（新的 M0）→ 生成 2,000 条试训 → B（PartNet）、C（奇怪形状）→ 扩到 5,000 条。详见 LAB_MASTER_PLAN 第 12–13 节 |
| 8 | 训练基线和本文方法，做对比评估 | 见第 8 节；正式训练的规则见第 3 节和 LAB_MASTER_PLAN 14.2 节；开始前先用 `probe_bs32` 做冒烟测试（见第 7 节） |

## 7. 注意事项和已知问题

- **夹爪裁剪规则**：评估框架把超出 [−1, 1] 的动作判为策略错误。桥接服务的规则是：超出不到 0.05 的裁剪到 ±1 并记录次数和最大超出量；超出 0.05 及以上的报错、不裁剪。零样本评估中最大超出 0.0244，只发生在夹爪一维。
- **图像方向**：数据集和评估用的是“规范图像”（左上角为原点）。`pi05_libero` 按 openpi 的 LIBERO 示例训练，原始 robosuite 图像旋转 180°，**等价于对规范图像做水平翻转 `image[:, ::-1]`**。训练和推理必须用同一种方向。
- **8 维状态**与 openpi LIBERO 示例相同：末端位置 3 + 末端姿态轴角 3 + 两指关节 2。夹爪朝下时轴角在 ±π 附近跳变，数据里有完整的夹爪位姿矩阵，必要时可换成连续表示。`pi05_libero` 本身不读状态（π₀.₅ 不加状态 token，且 `discrete_state_input=False`）。
- **动作块**：`pi05_libero` 一次输出 10 步，按官方做法每次执行前 5 步（评估用 `--execute-steps 5`），训练和评估要一致。评估时 `--timeout 60`，推理服务要常驻并预热，服务设 `XLA_PYTHON_CLIENT_PREALLOCATE=false`（否则 JAX 预占约 36 GB，4 个仿真进程的渲染会抢不到显存）。
- **停止评估的顺序**：先停评估（对主进程发 SIGTERM，它会等每个进程做完当前这条），再停服务；先停服务的话，正在跑的条目会被记成策略错误，且 `--resume` 不会重跑。`--resume` 时参数从 `eval_config.json` 读，`summary.json` 的 `elapsed_wall_s` 只记本次用时。
- **复跑噪声**：openpi 每处理一次请求就从同一个随机数种子切出新噪声，噪声取决于请求到达顺序；所以模型评估的复跑不会逐字节一致（规则教师会）。
- **检查点占空间**：实测每个 9.6 GB（params 6.0 GB + train_state 3.1 GB，见 [LAB_LORA_PROBE.md](LAB_LORA_PROBE.md)）。openpi 每 `save_interval`（默认 1,000）步存一次，只保留最新的一个，再加上步数是 `keep_period` 整数倍的（默认 5,000）。正式训练把 `keep_period` 设为 10,000（3 万步时留第 1 万、2 万步和最后一步共 3 个，约 29 GB）；用验证集挑出最好的检查点后删掉其余的（删除前问用户）。
- **`scripts/openpi_robolab.py` 目前只适合短时测量**：只在最后一步存检查点（`keep_period=None`），没设学习率计划（openpi 默认的 `decay_steps` 固定是 30,000，不跟总步数走），没有 `--seed` 和 `--resume` 选项。正式训练前要改，见 LAB_MASTER_PLAN 14.2 节。
- **工作盘（1 TB 移动固态硬盘，预计 2026-11-20 的下一周到货）**：格式化为 NTFS，固定盘符；在上面建一个 vhdx 虚拟磁盘，挂进 WSL（`wsl --mount --vhd <路径> --name robodata`，挂载点 `/mnt/wsl/robodata`），格式化成 ext4；**用 mkfs 格式化之前，必须先把 `lsblk` 的输出给用户确认**；再把 `outputs/` 和 openpi 的检查点目录换成指向它的链接；`outputs` 换成链接后，在 `.git/info/exclude` 里加一行 `/outputs`（`.gitignore` 里的 `outputs/` 不匹配同名链接）。5,000 条时三份数据都能保留；只有扩到 1 万条才需要“每批转换后清理中间文件”，自动删除默认关闭，要用户同意才打开。
- **空间回收**：在 WSL 里删文件不会让 C 盘变大。如需回收，WSL 3.x 可用 `wsl --manage Ubuntu-22.04 --set-sparse true`（先 `wsl --shutdown`），要在 Windows 的 PowerShell 里做，会关掉 WSL 里的 Claude Code。
- **相机视场角**：D455 彩色相机规格有两种说法（90°×65° 或 86°×57°）。实测 1280×800 下第一台 89.67°×63.79°、第二台 91.06°×65.01°，两台焦距差 2.4%，仿真里每台用自己的 fovy（[D455_CAMERA_PARAMS.md](D455_CAMERA_PARAMS.md)）。openpi 把 1280×800 缩放到 224×140 再补边成 224×224，新数据的仿真图像应按同样方式处理。
- **WSL 与 USB 相机**：WSL 默认看不到 USB 设备，读内参在 Windows 上做（方法见 [D455_CAMERA_PARAMS.md](D455_CAMERA_PARAMS.md) 第 6 节）。
- **控制真机的电脑**：Franka 一般接一台装了实时内核的 Ubuntu 电脑，很可能不是当前这台，需要确认。
- **现有代码写死了“两个部位”**：`p4_parts.PARTS`、`p4_dataset_export.PART_IDS`（2 表示两段都有）、`p5_eval.other_part()`、按部位的指令模板、界面里的 `PARTS`。新生成器要改成按“部位表”的通用格式。
- **`lab_xml_hook` 只把旧场景的物体抬到桌面高度**，水平位置仍是旧范围；新生成器要按物体桌（底座前 0.30–0.70 m、左右 ±0.40 m）重新采样。
- **`HF_LEROBOT_HOME` 必须前后一致**：设成 `~/projects/robolab-studio/outputs/lerobot`。转换脚本、openpi 的 `compute_norm_stats.py` 和 `train.py` 都通过 `$HF_LEROBOT_HOME/<repo_id>` 找数据；同时设 `HF_HUB_OFFLINE=1`，数据缺文件时报错而不是去下载。
- **openpi 配置写在我们的仓库里**：`scripts/openpi_robolab.py` 在运行时把配置注册进 openpi 的 `_CONFIGS_DICT`，**不改 openpi 的 `src/openpi/training/config.py`**。桥接服务的 `load_openpi_policy` 也用 `get_config`，以后加载我们训练出的模型前也要先注册。
- **显存上限**：只设 `XLA_PYTHON_CLIENT_PREALLOCATE=false` 时，JAX 的分配上限仍是显卡的 75%（约 36 GiB）；要用到约 43 GiB，需同时设 `XLA_PYTHON_CLIENT_MEM_FRACTION=0.9`。
- **另一个对话正在做的 PR**（合并前**不要改** `robolab/policy.py`、`robolab/p5_eval.py`、`scripts/serve_openpi_bridge.py`、`tests/test_openpi_bridge.py`、`.github/workflows/checks.yml`）：评估发给模型的图像现在是 JPEG（质量 95），训练数据是无损的，要改成无损 PNG；把 `tests/test_openpi_bridge.py` 加进 GitHub 的自动检查；“固定噪声”：用观测内容（图像、指令、`episode_tick`）的哈希作随机种子，生成 (10, 32) 的噪声传给 `policy.infer(obs, noise=...)`，不用 `observation_id`（它只是请求计数）。这个 PR 也会让桥接服务能加载我们注册的配置。**合并后**：先用测试检查点 `outputs/openpi_checkpoints/pi05_robolab_lora/probe_bs32/299` 做一次冒烟测试（训练出的检查点 → 桥接服务 → 评估 2 条），确认桥接服务能加载我们注册的 LoRA 配置，在正式训练前做；第 5 步相机也等合并后再做。
- **待办**：在官方 LIBERO 任务上复现 `pi05_libero` 的成绩，证明“真实推理”接口正确；等显卡空闲时做。

## 8. 研究方案（训练与对比实验）

详细设计见 [LAB_MASTER_PLAN.md](LAB_MASTER_PLAN.md) 第 13–14 节和 [PROJECT_REPORT.md](PROJECT_REPORT.md) 第 17.4 节。

### 8.1 接触监督的建议方案

- **监督目标（建议）**：“预期接触”。找到一条示范中抓稳的时刻，取两指接触点（世界坐标，抓取前物体静止）；对之前每一帧，用当时的夹爪位姿换算到夹爪坐标系；抓稳后用当前帧的真实接触。这个定义和部位种类无关，扩展到任意命名部位后同样适用。
- **接入方式（建议先做）**：把预期接触点（两指共 6 个数）拼在动作向量后面一起预测，推理时只取前 7 维。之后可以改成辅助预测头。
- **关键对照**：用“指定部位中心”代替接触点做监督（M3），以区分“接触”本身的作用和“给一个目标点”的作用。
- 模型：M0 官方权重不微调；M1 基线 LoRA；M2 本文方法；M3 部位中心对照（以上必做）；M4 当前帧接触、M5 加相机几何、M6 外观变化（视时间）。同样数据、步数、批量（批量 32）；M1、M2、M3 各跑 2–3 个随机种子。

### 8.2 更完整的实施方案（用户整理的 Book1.xlsx）

用户整理过一份更完整的实施表，要点如下。设计上采纳，但分阶段做，先做最小版本：

- 五类参数化形状及区域划分（现在扩展为 A/B/C 三类物体来源和部位表）；指令模板加语言模型扩写（只英文）；0–2 个干扰物；起始姿态扰动。
- 示范用“采样对跖抓取 + 运动规划”生成，20–30% 的轨迹中途注入偏移再纠正。
- 标签：两级查询区域的占据（目标/障碍/空）、指尖最终接触点（变换到当前夹爪坐标系）、指尖到目标表面的有符号距离。
- 模型改动：射线嵌入（相机几何）、冻结的 VGGT/π³ 特征适配器、占据头和接触头；损失 = 动作 + 占据 + 接触，λ 初始 0.1。
- 主对比：**π₀.₅ 原版、相机坐标系监督（深度）、本方法（夹爪坐标系监督）**，同一检查点、同样数据和步数；资源允许时每组 2–3 个随机种子。
- 消融：去掉接触头、障碍物通道、几何特征、射线嵌入；线性探针。

建议的最小版本顺序：实验室布局 + 通用部位格式 + 通用规则教师 → 参数化日常物体 → 新测试场景和新 M0 → 2,000 条 → 接触标签和 π₀.₅ 原版基线 → 接触头和相机坐标系对照 → PartNet 和奇怪形状、扩到 5,000 条 → 主对比评估 → 之后再加占据、射线嵌入、纠错段，VGGT 放最后。实验室只有一张 48 GB 显卡，训练只能排队跑；训练时间不是约束（用户 2026-11-25 确认），M1、M2、M3 各跑 2–3 个随机种子。

### 8.3 评估

在实验室布局的新测试集上评估（约 360 条，四层留出），加防滚；再加 `--appearance all` 做外观鲁棒性。所有模型用同一批场景，用 `--p5-compare` 汇总：Wilson 区间、McNemar 配对检验、可直接放进论文的表格和图。主要指标：**整类别留出**上的成功率；次要指标：部位命中、碰撞、接触时部位正确率、留出说法、留出部位类型、外观变化。旧布局 240 条上的结果（规则教师 240/240、零样本 15.0%）作为前期工作。

## 9. 文档索引

| 想了解 | 读这个 |
| --- | --- |
| 全景：从开始到以后、建模尺寸、布局、新决定、与之前的不同 | [LAB_MASTER_PLAN.md](LAB_MASTER_PLAN.md) |
| 零样本评估结果 | [LAB_ZEROSHOT_REPORT.md](LAB_ZEROSHOT_REPORT.md) |
| 原理讲解、技术细节、早期实验室计划 | [PROJECT_REPORT.md](PROJECT_REPORT.md) |
| 数据集格式、训练视图字段、图像方向、LeRobot 转换 | [P4_DATASET_FORMAT.md](P4_DATASET_FORMAT.md) |
| 评估协议、命令选项、输出格式、对比报表 | [P5_EVAL_FORMAT.md](P5_EVAL_FORMAT.md) |
| 模型接口、观测和动作合同、HTTP 协议 | [VLA_INTEGRATION.md](VLA_INTEGRATION.md) |
| 实验室场景尺寸和相机位置 | [LAB_CAMERA_STUDY.md](LAB_CAMERA_STUDY.md) |
| 两台 D455 的实测内参（第一台、第二台） | [D455_CAMERA_PARAMS.md](D455_CAMERA_PARAMS.md) |
| 安装 | [INSTALL.md](INSTALL.md) |
| 硬件 | [HARDWARE_PROFILE.md](HARDWARE_PROFILE.md) |
| 按日期的开发记录 | [NEXT_CHAT_HANDOFF.md](NEXT_CHAT_HANDOFF.md) |

**给云端 claude.ai 对话上传的文件**：本文件、`LAB_MASTER_PLAN.md`、`LAB_ZEROSHOT_REPORT.md`、`PROJECT_REPORT.md`、`P4_DATASET_FORMAT.md`、`P5_EVAL_FORMAT.md`、`VLA_INTEGRATION.md`、`LAB_CAMERA_STUDY.md`、`D455_CAMERA_PARAMS.md`，以及实验室电脑上的 `~/lab_logs/PROGRESS.md`。这份进度记录不在 GitHub 上，要从实验室电脑拷出来。

## 10. 常用命令

在实验室电脑上，先进入项目目录 `cd ~/projects/robolab-studio`，确认没有激活 `~/vla/.venv`：

```bash
git pull
```

```bash
bash scripts/launch.sh --doctor
```

```bash
bash scripts/test_engine.sh
```

```bash
bash scripts/launch.sh --p5-eval --policy teacher --dataset outputs/p4d_lab --scene-set extended --anti-roll --workers 4 --output-dir outputs/<新目录>
```

```bash
bash scripts/launch.sh --p5-compare --output-dir outputs/<对比目录> 标签1=outputs/<运行1> 标签2=outputs/<运行2>
```

π₀.₅ 桥接服务和零样本评估的命令见 [LAB_ZEROSHOT_REPORT.md](LAB_ZEROSHOT_REPORT.md) 第 7 节。

LeRobot 转换和 LoRA 训练（openpi 环境的 Python；先设好三个变量）：

```bash
deactivate 2>/dev/null; unset VIRTUAL_ENV
export HF_LEROBOT_HOME=~/projects/robolab-studio/outputs/lerobot HF_HUB_OFFLINE=1
PY=~/projects/openpi/.venv/bin/python
$PY scripts/convert_p4_to_lerobot.py convert --hdf5 outputs/p4d_lab/export/p4_osc_train.hdf5 --repo-id robolab/p4d_v1_train
$PY scripts/convert_p4_to_lerobot.py verify  --hdf5 outputs/p4d_lab/export/p4_osc_train.hdf5 --repo-id robolab/p4d_v1_train
$PY scripts/openpi_robolab.py norm-stats
XLA_PYTHON_CLIENT_PREALLOCATE=false XLA_PYTHON_CLIENT_MEM_FRACTION=0.9 \
  $PY scripts/openpi_robolab.py train --batch-size 32 --steps 300 --exp-name <实验名> --measure outputs/openpi_probe/<实验名>.json
```

openpi 的命令在 `~/projects/openpi` 下运行，先确认没有激活 `vla` 环境：

```bash
uv run scripts/compute_norm_stats.py --config-name <配置名>
```

```bash
XLA_PYTHON_CLIENT_MEM_FRACTION=0.9 uv run scripts/train.py <配置名> --exp-name=<实验名> --overwrite
```

**回到实验室那次对话**：在 WSL 里先 `cd ~`，再运行 `claude --resume bd700079-2201-4111-a0c8-1e0c0df671de`；接不回来就按第 0 节开新对话。
