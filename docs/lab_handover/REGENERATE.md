# 在新电脑上重新生成数据和检查点（2026-11-27）

用户 2026-11-27 决定：旧实验室电脑上的数据、检查点和评估结果**不拷贝**，到新电脑上重新生成。

这也符合"数据在哪台电脑渲染，就在哪台训练和评估"的规则。11-18 在旧电脑上重新生成数据集后，和笔记本上的版本做过指纹核对：物理过程完全一致，但有 90,576 张图像因为显卡不同而略有差别。

下面的命令、结果和用时都来自旧电脑上的实际记录。旧电脑配置：RTX 6000 Ada 48 GB、20 核、30 GiB 内存、WSL Ubuntu 22.04。

## 0. 开始前

- **先确认显卡显存**：
  - LoRA 短时训练（批量 32）的显存真实峰值是 23.3 GiB。JAX 默认最多只用显卡的 75%，所以显卡最好有 32 GB 以上。
  - 显存不够的话，先停下来和用户商量，不要擅自改批量。
- **再确认磁盘**：下面这些加起来大约需要 70 GB。
  - 数据集 11 GB，LeRobot 版本 2.7 GB；
  - 测试检查点 9.6 GB；
  - `pi05_base` 权重 11.6 GiB；
  - openpi 环境约 8 GB。
- **工作规则照旧**（见 [LAB_STAGE_HANDOFF.md](../LAB_STAGE_HANDOFF.md)）：
  - 缺系统库先问用户，不自己运行 sudo；
  - 下载 `pi05_base` 这类大文件之前先问；
  - 不改第三方库的源码。
- **旧电脑专属的内容不要照搬**：`PROGRESS.md` 里的 C 盘、vhdx 工作盘、会话 ID、`~/lab_logs` 路径都是旧电脑的情况。新电脑的磁盘怎么安排，要重新和用户商量。
- **提交身份**：新电脑上 git 的提交身份怎么配，由用户决定。

## 1. 环境

1. 克隆本仓库，按 [INSTALL.md](../INSTALL.md) 安装平台。安装文档里已经包括编译工具和 h5py。固定版本是：MuJoCo 2.3.7、robosuite 1.4.1、NumPy 1.26.4、LIBERO `8f1084e3`、h5py 3.16.0。装好后运行：
   ```bash
   bash scripts/launch.sh --doctor
   bash scripts/test_engine.sh          # 旧电脑：125 项通过，3 项按设计跳过
   ```
2. 安装 openpi。用 uv 0.13.9，openpi 固定在提交 `215abfb217dbac7d5f1273282331b9b1866c0479`，子模块是 aloha `d1dc83a`、libero `f78abd6`。运行前不要激活任何虚拟环境：
   ```bash
   git clone --recurse-submodules https://github.com/Physical-Intelligence/openpi.git ~/projects/openpi
   cd ~/projects/openpi
   git checkout 215abfb217dbac7d5f1273282331b9b1866c0479 && git submodule update --init --recursive
   GIT_LFS_SKIP_SMUDGE=1 uv sync
   GIT_LFS_SKIP_SMUDGE=1 uv pip install -e .
   ```

## 2. 数据和测试检查点（按顺序做）

以下命令都在 `~/projects/robolab-studio` 下运行。

| # | 做什么 | 命令 | 旧电脑上的结果和用时 |
| --- | --- | --- | --- |
| 1 | 生成 P4-D 数据集（旧布局，200 条示范 + 4 条对照） | `bash scripts/launch.sh --p4d-dataset --workers 4 --output-dir outputs/p4d_lab` | `passed=true`，204 条全部校验通过；种子 20261111；30 分钟；11 GB |
| 2 | 指纹核对 | `bash scripts/launch.sh --p4d-fingerprint check --dataset outputs/p4d_lab --fingerprint docs/fingerprints/p4d_dataset_v1.json.gz --report outputs/p4d_lab/fingerprint_check.json` | 结论是 `images_differ_physics_identical`：物理一致，图像因显卡不同略有差别，这是正常的。**如果物理不一致，就停下来报告** |
| 3 | 导出训练文件 | `bash scripts/launch.sh --p4d-export --dataset outputs/p4d_lab` | `outputs/p4d_lab/export/` 下 4 个 HDF5，共 5.96 GB；训练集 120 条 / 26,640 步；约 20 分钟 |
| 4 | 转成 LeRobot 并逐像素校验 | 见下面的代码块 | 120 条 / 26,640 帧，53,280 张图逐像素一致；2.72 GB；约 3 分钟 |
| 5 | 下载 `pi05_base`（**先问用户**），算归一化统计 | 见下面的代码块（`norm-stats`；第一次用到 `pi05_base` 时 openpi 会自动下载） | 11.6 GiB，旧电脑下载用了 22.5 分钟 |
| 6 | 重新训练测试检查点 `probe_bs32` | 见下面的代码块 | 每步 5.43 s，共 30 分钟；检查点在 `outputs/openpi_checkpoints/pi05_robolab_lora/probe_bs32/299`，9.6 GB |

第 4–6 步用 openpi 环境的 Python，先在同一个终端里设好变量：

```bash
deactivate 2>/dev/null; unset VIRTUAL_ENV
export HF_LEROBOT_HOME=~/projects/robolab-studio/outputs/lerobot HF_HUB_OFFLINE=1
PY=~/projects/openpi/.venv/bin/python
$PY scripts/convert_p4_to_lerobot.py convert --hdf5 outputs/p4d_lab/export/p4_osc_train.hdf5 --repo-id robolab/p4d_v1_train
$PY scripts/convert_p4_to_lerobot.py verify  --hdf5 outputs/p4d_lab/export/p4_osc_train.hdf5 --repo-id robolab/p4d_v1_train
$PY scripts/openpi_robolab.py norm-stats
XLA_PYTHON_CLIENT_PREALLOCATE=false $PY scripts/openpi_robolab.py train --batch-size 32 --steps 300 --exp-name probe_bs32 --measure outputs/openpi_probe/bs32.json
```

几点说明：
- 转换脚本会把代码版本记进数据集。转换前先确认工作区干净（`git status`），否则会记成"有未提交改动"。
- 下载 `pi05_base` 时需要 `HF_HUB_OFFLINE=1` 以外的网络访问。openpi 从 `gs://openpi-assets` 匿名下载，和 Hugging Face 无关。
- 旧电脑上，批量 32 在 JAX 默认的显存上限内就能跑，不用设 `XLA_PYTHON_CLIENT_MEM_FRACTION`。

## 3. 之后：冒烟测试

`lerobot-conversion` 和 PR #14 都合并以后，按 [PROGRESS.md](PROGRESS.md) 开头"当前状态"第 (3) 条的步骤做。`--checkpoint` 换成新电脑上 `probe_bs32/299` 的绝对路径，请求日志放到新电脑上的日志目录。

## 4. 不需要重新生成的

- **旧电脑上的评估结果**（规则教师 240 条、π₀.₅ 零样本 240 条）：数字已经写进 [LAB_ZEROSHOT_REPORT.md](../LAB_ZEROSHOT_REPORT.md)。旧布局的结果作为前期工作保留，新布局上本来就要重跑。
- **批量 64 的测量**：结论在 [LAB_LORA_PROBE.md](../LAB_LORA_PROBE.md)，批量已经定为 32。
- **腕部相机研究的完整输出**：结论和全表在本目录的 [wrist_camera_study_record.md](wrist_camera_study_record.md)、[wrist_camera_study_summary.md](wrist_camera_study_summary.md)。研究只取决于机器人和相机的几何，和电脑无关。需要逐帧数据时可以重跑，约 30 分钟：`.venv-sim/bin/python scripts/study_wrist_camera.py --output-dir outputs/wrist_camera_study --workers 8`。
- **`pi05_libero` 权重**（约 12 GB）：只有跑零样本评估或复现官方 LIBERO 成绩时才用，到时候再下（先问用户）。
