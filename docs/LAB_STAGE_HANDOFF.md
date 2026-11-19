# 实验室阶段交接（新对话从这里开始）

更新：2026-11-19。笔记本阶段已全部完成，之后的工作都在实验室电脑上进行。本文件汇总了此前所有对话里的背景、已经做出的决定、当前进度和下一步。新的对话（实验室电脑上的 Claude Code、云端的 Claude Code，或 claude.ai 网页对话）请先完整读完本文件，再动手。

本文与其他文档冲突时，**以本文为准**：其他文档里有些内容写于更早的时候，比如“实验室是 A100 80GB”，那是错的。

---

## 0. 给新对话的开场话

**实验室电脑上的 Claude Code**（主要工作方式）：

```text
这是我的毕业设计项目，之前的工作在另一台笔记本上完成，现在在实验室继续。请全程用中文，讲解要详细易懂。先完整阅读 ~/projects/robolab-studio/docs/LAB_STAGE_HANDOFF.md，再读 ~/lab_logs/PROGRESS.md（实验室电脑上的进度记录），然后按交接文档第 6 节的顺序，从第一个未完成的任务接着做。动手之前先告诉我你的理解和计划。
```

**云端 claude.ai 网页对话**（讨论方案、写论文；它不能在实验室电脑上运行命令）：先把第 9 节列出的文件上传到对话或项目知识库，再说：

```text
这是我的毕业设计项目。请先阅读我上传的 LAB_STAGE_HANDOFF.md（交接文档）和其他文件，全程用中文、讲解详细易懂。之后我会在实验室电脑上用 Claude Code 执行具体操作，请你帮我分析结果、规划步骤、撰写论文。
```

---

## 1. 项目是什么

- **题目**：Gripper-Centric Contact for Zero-Shot VLA Grasping of Unseen Objects（面向未见物体零样本 VLA 抓取的夹爪中心接触建模）。
- **研究问题**：在相同的训练数据预算和统一的评估协议下，在 VLA 策略中引入**夹爪坐标系的接触位置监督**，能否提高**未见刚性物体**上“**按语言指定部位抓取**”的成功率，并减少碰撞？
- **任务**：桌上一根两段式短棒（一段粗、一段细，同色），旁边有障碍物。指令说抓粗端或细端，机械臂要抓对、抬起、不碰障碍。训练只见过长方体族和圆柱族，椭球族整族留作“未见形状”测试。
- **基线模型**：π₀.₅（openpi）。参考论文 G³VLA（相机几何）、π₀.₅、OpenVLA 都在 LIBERO 上做仿真评测。
- **硬件**：Franka Emika Panda（FER）机械臂；两台 Intel RealSense D455，一台装在腕部、一台在外部，只用 RGB。真机实验是拓展目标。
- **平台**：RoboLab Studio（本仓库）。MuJoCo 2.3.7 + robosuite 1.4.1 + LIBERO 的本地仿真工作站，包含桌面界面、接触采样、数据集生成、批量闭环评估。

完整的技术总结和实验室计划见 [PROJECT_REPORT.md](PROJECT_REPORT.md)，写得最详细，适合写论文时引用。

## 2. 用户的工作习惯和规则（请严格遵守）

- **全程中文**，讲解要详细、通俗；用户对仿真和机器人底层不熟悉，需要解释原理时从基础讲起。
- **只报告实际验证过的结果**。“有按钮”“日志写已加载”“Mock 返回了动作”都不算通过；规则教师（读取仿真真值）的成功率不能说成模型性能。失败的证据要保留。
- **不要升级、降级、重装已有环境**，不要修改第三方库源码、显卡驱动、CUDA、全局 LIBERO 配置。新东西都装进隔离环境。
- **需要 sudo、删除文件、推送代码这类不可撤销或对外的操作，先问用户。**
- **提交和推送只在用户要求时做**。先建分支，通过 PR 合并进 `main`；提交作者沿用仓库已有提交的身份（`git log` 可查）。提交说明末尾加 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`，PR 描述末尾加 `🤖 Generated with [Claude Code](https://claude.com/claude-code)`。
- 不要提交 `outputs/`、虚拟环境、模型权重、数据集、个人配置。仓库是**公开的**，不要写入个人信息。
- 推送后要确认 GitHub 上的自动检查（Lightweight checks）通过，再说完成。

## 3. 已经做出的决定（不要再推翻，除非用户提出）

| 决定 | 内容 | 原因 |
| --- | --- | --- |
| 不换 ManiSkill3 | 继续用 MuJoCo + LIBERO | 老师让比较过。参考论文都用 LIBERO；官方 `pi05_libero` 权重和评测直接可用；已验证的平台和数据能保留 |
| 只用 LoRA 微调 | openpi 的 **JAX 版**（PyTorch 版不支持 LoRA） | 老师要求（担心全参数不收敛）；实验室显卡 48 GB，全参数需要 70 GB 以上，本来也跑不了 |
| π₀.₅ 的 LoRA 配置要自己写 | 参照 openpi 里 `pi0_libero_low_mem_finetune`：`paligemma_variant="gemma_2b_lora"`、`action_expert_variant="gemma_300m_lora"`、`freeze_filter=...get_freeze_filter()`、`ema_decay=None`，再加上 `pi05=True, action_horizon=10, discrete_state_input=False` | openpi 只有 π₀ 的 LoRA 配置，没有 π₀.₅ 的；能否正常训练要实测 |
| 外部相机 | 物体桌**正对面**、离地约 **1.30 m**（比桌面高约 56 cm、远边外约 30 cm、俯视约 43°）；备选左侧 1.30 m | 仿真对比结果，见 [LAB_CAMERA_STUDY.md](LAB_CAMERA_STUDY.md) |
| 腕部相机要倾斜 | 倾角 ≈ arctan(偏移 ÷ 镜头到指尖中心距离) − 20°；偏移 6/8/10 cm 时约倾 10°/20°/30° | 同上。实际偏移和高度要在装好支架后量 |
| 数据集 | 目标 1–2 万条，按实验室布局（两张桌子、真实相机位置）重新生成；先 2,000 条试训 | 现有 200 条是旧布局（物体在地面、LIBERO 默认相机），用于调试训练流程 |
| 训练、评估都用实验室机器生成的数据 | 用 `outputs/p4d_lab` 及以后在实验室生成的数据 | 不同显卡渲染的图像有细微差别；训练和评估同一台机器才能保证输入一致 |

## 4. 实验室电脑

| 项目 | 情况 |
| --- | --- |
| 系统 | Windows + WSL，发行版 `Ubuntu-22.04`；项目都在 WSL 用户主目录 `~` 下 |
| 显卡 | NVIDIA **RTX 6000 Ada，48 GB**，驱动 596.71，CUDA 13.2（**不是 A100 80GB**） |
| 空间 | WSL 的虚拟磁盘在 **C 盘**，C 盘剩约 70 GB（下载 `pi05_libero` 后）。用户有一块 1TB 移动固态硬盘可以一直插着，正式训练前要处理空间（见第 7 节） |
| 已有的别人的环境 | `~/vla/.venv`：**不要用，也不要改**。里面的 robosuite 版本不对，还误装了无关的 `robotsuite`。终端里可能默认激活着它，运行我们的命令前要 `deactivate` 或去掉 `VIRTUAL_ENV` |
| 用户权限 | 有 WSL 里的 sudo 密码；sudo 命令由用户自己执行 |

**已装好的东西：**

| 内容 | 位置 | 版本 |
| --- | --- | --- |
| RoboLab Studio | `~/projects/robolab-studio` | 本仓库 `main` |
| 仿真环境 | `~/projects/robolab-studio/.venv-sim` | Python 3.10.12；MuJoCo 2.3.7；robosuite 1.4.1；NumPy 1.26.4；LIBERO 0.1.0（提交 `8f1084e3132a39270c3a13ebe37270a43ece2a01`，在 `third_party/LIBERO`）；torch 2.2.0+cpu；h5py 3.16.0（手动补装） |
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
5. **桥接服务**（据实验室进度记录，结果尚未复核）：`scripts/serve_openpi_bridge.py`、`robolab/policy.py` 里的 `OpenpiAdapter`（注册名 `pi05`）、`tests/test_openpi_bridge.py`，提交在**本地分支 `pi05-bridge`（`37b4e4c`），未推送**。
6. **零样本评估** `outputs/lab_eval_pi05_libero_zeroshot`：已开跑，下班时停在一半，可用 `--resume` 续跑。

日志：实验室电脑的 `~/lab_logs/2026-11-18/` 和 `outputs/p4d_lab/logs/`；进度记录：`~/lab_logs/PROGRESS.md`（含确切命令）。

## 6. 下一步（按顺序）

| # | 任务 | 完成标志 |
| --- | --- | --- |
| 1 | 续跑 π₀.₅ 零样本评估：先按 `PROGRESS.md` 启动桥接服务，再用同一个输出目录加 `--resume` | 240 条跑完；报告成功率、部位命中率、碰撞率（分 2×2 格子）、失败原因、裁剪次数、推理延迟。成功率很低是预期的，这一行是论文的“不微调”基准 |
| 2 | 复核桥接服务并合并 `pi05-bridge` | 测试通过；从 GitHub `git pull` 最新 `main` 后，把分支推上去开 PR，CI 通过后合并（推送前问用户） |
| 3 | 数据集转 LeRobot 格式 | 用 `robolab.p4_dataset_export.iter_policy_frames(path, language='en', image_convention='libero_rotated_180')` 逐帧写入 `LeRobotDataset`；读回检查图像方向、帧数（训练集 120 条 / 26,640 帧）、字段 |
| 4 | 写 π₀.₅ LoRA 配置并做短时训练 | 先下载 `pi05_base`；用 openpi 的脚本算归一化统计；跑几百步，记录每步耗时、显存峰值、**每个检查点的大小**，损失在下降 |
| 5 | 处理空间 | 根据第 4 步实测的检查点大小，开启 WSL 稀疏模式或把 WSL 搬到移动固态硬盘；调整检查点保留策略（`keep_period`、`save_interval`） |
| 6 | 实验室布局的新数据集 | 先确定相机：量腕部支架偏移和高度、确认外部相机位置、读两台 D455 的内参（`rs-enumerate-devices -c`，注意 WSL 默认看不到 USB 相机），填进 `robolab/lab_scene.py`，仿真画面与真机照片对比。再加入：教师随机性、防滚、外观和相机随机化、视频压缩存储；先生成 2,000 条 |
| 7 | 训练基线和本文方法，做对比评估 | 见第 8 节 |

## 7. 注意事项和已知问题

- **夹爪裁剪规则**：评估框架把超出 [−1, 1] 的动作判为策略错误。桥接服务的规则是：超出不到 0.05 的裁剪到 ±1 并记录次数和最大超出量；超出更多的报错。
- **图像方向**：数据集和评估用的是“规范图像”（左上角为原点）。`pi05_libero` 按 openpi 的 LIBERO 示例训练，原始 robosuite 图像旋转 180°，**等价于对规范图像做水平翻转 `image[:, ::-1]`**。训练和推理必须用同一种方向。
- **8 维状态**与 openpi LIBERO 示例相同：末端位置 3 + 末端姿态轴角 3 + 两指关节 2。夹爪朝下时轴角在 ±π 附近跳变，数据里有完整的夹爪位姿矩阵，必要时可换成连续表示。
- **动作块**：`pi05_libero` 一次输出 10 步，按官方做法每次执行前 5 步（评估用 `--execute-steps 5`），训练和评估要一致。评估时 `--timeout 60`，推理服务要常驻并预热。
- **检查点占空间**：openpi 默认每 1,000 步存一次、永久保留每 5,000 步的检查点；按权重约 12 GB 估算，一轮 3 万步训练可能留下七八十 GB。正式训练前必须处理空间问题。
- **空间回收**：在 WSL 里删文件不会让 C 盘变大。WSL 3.x 可用 `wsl --manage Ubuntu-22.04 --set-sparse true` 开启稀疏模式（先 `wsl --shutdown`）；或用 `wsl --manage Ubuntu-22.04 --move <新位置>` 搬到移动硬盘。两者都要在 Windows 的 PowerShell 里做，会关掉 WSL 里的 Claude Code。
- **相机视场角**：D455 彩色相机规格有两种说法（90°×65° 或 86°×57°），仿真里两种都算过，结论相同；以实际读出的内参为准。
- **WSL 与 USB 相机**：WSL 默认看不到 USB 设备，需要 `usbipd-win`（Windows 管理员安装），且 RealSense 在 WSL 里不一定稳定。读内参可以在 Windows 上做，或在控制机械臂的 Ubuntu 电脑上做。
- **控制真机的电脑**：Franka 一般接一台装了实时内核的 Ubuntu 电脑，很可能不是当前这台，需要确认。

## 8. 研究方案（训练与对比实验）

### 8.1 接触监督的建议方案

详见 [PROJECT_REPORT.md](PROJECT_REPORT.md) 第 17.4 节。

- **监督目标（建议）**：“预期接触”。找到一条示范中抓稳的时刻，取两指接触点（世界坐标，抓取前物体静止）；对之前每一帧，用当时的夹爪位姿换算到夹爪坐标系；抓稳后用当前帧的真实接触。所需数据都已经在训练视图里。
- **接入方式（建议先做）**：把预期接触点（两指共 6 个数）拼在动作向量后面一起预测，推理时只取前 7 维。之后可以改成辅助预测头。
- **关键对照**：用“指定部位中心”代替接触点做监督，以区分“接触”本身的作用和“给一个目标点”的作用。

### 8.2 更完整的实施方案（用户整理的 Book1.xlsx）

用户整理过一份更完整的实施表，要点如下。设计上采纳，但分阶段做，先做最小版本：

- 五类参数化形状及区域划分；指令模板加语言模型扩写；0–2 个干扰物；起始姿态扰动。
- 示范用“采样对跖抓取 + 运动规划”生成，20–30% 的轨迹中途注入偏移再纠正。
- 标签：两级查询区域的占据（目标/障碍/空）、指尖最终接触点（变换到当前夹爪坐标系）、指尖到目标表面的有符号距离。
- 模型改动：射线嵌入（相机几何）、冻结的 VGGT/π³ 特征适配器、占据头和接触头；损失 = 动作 + 占据 + 接触，λ 初始 0.1。
- 主对比：**π₀.₅ 原版、相机坐标系监督（深度）、本方法（夹爪坐标系监督）**，同一检查点、同样数据和步数；资源允许时每组 2–3 个随机种子。
- 消融：去掉接触头、障碍物通道、几何特征、射线嵌入；线性探针。

建议的最小版本顺序：环境和 2–3 类形状 → 2,000 条轨迹 → 接触标签和 π₀.₅ 原版基线 → 接触头和相机坐标系对照 → 主对比评估 → 之后再加占据、射线嵌入、纠错段、扩到 2 万条，VGGT 放最后。实验室只有一张 48 GB 显卡，训练只能排队跑，要严格控制训练次数。

### 8.3 评估

在 240 条扩展测试集上评估，加防滚；再加 `--appearance all` 做外观鲁棒性。所有模型用同一批场景，用 `--p5-compare` 汇总：Wilson 区间、McNemar 配对检验、可直接放进论文的表格和图。主要指标：未见形状族上的成功率；次要指标：部位命中、碰撞、接触时部位正确率、留出说法、外观变化。

## 9. 文档索引

| 想了解 | 读这个 |
| --- | --- |
| 全部背景、技术细节、实验室计划 | [PROJECT_REPORT.md](PROJECT_REPORT.md) |
| 数据集格式、训练视图字段、图像方向、LeRobot 转换 | [P4_DATASET_FORMAT.md](P4_DATASET_FORMAT.md) |
| 评估协议、命令选项、输出格式、对比报表 | [P5_EVAL_FORMAT.md](P5_EVAL_FORMAT.md) |
| 模型接口、观测和动作合同、HTTP 协议 | [VLA_INTEGRATION.md](VLA_INTEGRATION.md) |
| 实验室场景尺寸和相机位置 | [LAB_CAMERA_STUDY.md](LAB_CAMERA_STUDY.md) |
| 安装 | [INSTALL.md](INSTALL.md) |
| 硬件 | [HARDWARE_PROFILE.md](HARDWARE_PROFILE.md) |
| 按日期的开发记录 | [NEXT_CHAT_HANDOFF.md](NEXT_CHAT_HANDOFF.md) |

**给云端 claude.ai 对话上传的文件**：本文件、`PROJECT_REPORT.md`、`P4_DATASET_FORMAT.md`、`P5_EVAL_FORMAT.md`、`VLA_INTEGRATION.md`、`LAB_CAMERA_STUDY.md`，以及实验室电脑上的 `~/lab_logs/PROGRESS.md`。这份进度记录不在 GitHub 上，要从实验室电脑拷出来。

## 10. 常用命令

在实验室电脑上，先进入项目目录 `cd ~/projects/robolab-studio`：

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

openpi 的命令在 `~/projects/openpi` 下运行，先确认没有激活 `vla` 环境：

```bash
uv run scripts/compute_norm_stats.py --config-name <配置名>
```

```bash
XLA_PYTHON_CLIENT_MEM_FRACTION=0.9 uv run scripts/train.py <配置名> --exp-name=<实验名> --overwrite
```

**回到实验室那次对话**：在 WSL 里先 `cd ~`，再运行 `claude --resume bd700079-2201-4111-a0c8-1e0c0df671de`；接不回来就按第 0 节开新对话。
