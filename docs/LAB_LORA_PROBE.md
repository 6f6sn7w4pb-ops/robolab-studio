# π₀.₅ LoRA 短时训练测量（实验室，2026-11-23）

对应 [LAB_STAGE_HANDOFF.md](LAB_STAGE_HANDOFF.md) 第 6 节第 4 步：在现有 120 条训练集（`robolab/p4d_v1_train`，26,640 帧）上，用我们自己写的 π₀.₅ LoRA 配置做几百步短时训练，测资源需求，为定批量大小和规划正式训练提供依据。**这不是正式训练，模型不用于评估。**

## 1. 配置

- 配置 `pi05_robolab_lora` 写在 [scripts/openpi_robolab.py](../scripts/openpi_robolab.py)，运行时注册进 openpi 的 `_CONFIGS_DICT`，再调用 openpi 自己的 `scripts/compute_norm_stats.py` 的 `main(config_name)` 和 `scripts/train.py` 的 `main(config)`；**没有改 openpi 源码**。
- 模型：`Pi0Config(pi05=True, action_horizon=10, discrete_state_input=False, paligemma_variant="gemma_2b_lora", action_expert_variant="gemma_300m_lora")`，对应的 `freeze_filter`，`ema_decay=None`，权重从 `pi05_base` 加载。
- 数据：`LeRobotLiberoDataConfig(repo_id="robolab/p4d_v1_train", prompt_from_task=True, extra_delta_transform=False)`，`HF_LEROBOT_HOME=outputs/lerobot`，fps 20（动作块 10 步 = 0.5 s）。
- 其他：wandb 关闭；`log_interval=1`；`save_interval` = 总步数 + 1000，所以只在最后一步存一个检查点（`train.py` 第 272 行的规则）；`keep_period=None`；学习率用 openpi 默认（余弦衰减，前 1,000 步预热，所以这几百步一直在预热阶段）。
- 归一化统计用 openpi 的脚本在整个训练集上算出，存在 `outputs/openpi_assets/pi05_robolab_lora/robolab/p4d_v1_train/norm_stats.json`。
- `pi05_base` 权重 11.6 GiB，下载用时 22.5 分钟（约 8.5 MB/s，比 `pi05_libero` 那次慢很多），存在 `~/.cache/openpi/openpi-assets/checkpoints/pi05_base`。

## 2. 测量方法

都在同一个进程里测（`train --measure`）：

- **每步耗时**：`train.py` 每步打印一行 “Step N: …”，打印前会 `jax.device_get` 等这一步算完，所以相邻两行的时间差就是一步的真实耗时；“稳定”取第 10 步以后。
- **第一步（编译）**：从“训练状态初始化完成”到第 0 步那一行。
- **显存**：设 `XLA_PYTHON_CLIENT_PREALLOCATE=false`（JAX 不预占），训练结束后读 JAX `memory_stats()` 的 `peak_bytes_in_use`；另外每 0.5 s 用 nvidia-smi 采样显存和利用率。
- **内存**：每 0.5 s 用 psutil 采样训练进程及其数据加载子进程的 RSS 之和，以及整机已用内存。
- **检查点大小**：训练结束后统计检查点目录。

## 3. 结果

| 项目 | 批量 32 | 批量 64 |
| --- | --- | --- |
| 显存分配上限（JAX） | 36.0 GiB（默认 75%） | 43.2 GiB（`XLA_PYTHON_CLIENT_MEM_FRACTION=0.9`） |
| 训练准备（加载权重、数据） | 42.7 s | 35.2 s |
| **第一步（含编译）** | **118.2 s** | 26.6 s（*命中了失败那次留下的编译缓存，不代表真实编译时间*） |
| **稳定后每步** | **5.43 s**（90 分位 5.46 s） | **12.01 s**（90 分位 12.06 s） |
| 每秒样本数 | 5.9 | 5.3 |
| **显存真实峰值**（`peak_bytes_in_use`） | **23.3 GiB** | **31.5 GiB** |
| nvidia-smi 显存最大值 | 34.5 GB | 45.9 GB |
| 训练时显卡利用率 | 平均 99.4%，中位 100%，低于 50% 的时间 0% | 平均 99.5%，中位 100%，低于 50% 的时间 0% |
| 整机内存最大已用 | 22.7 GiB（共 30 GiB） | 22.5 GiB |
| 进程树 RSS 之和最大 | 28.2 GiB（数据加载子进程的共享内存会被重复计数，偏高） | 28.2 GiB |
| **检查点大小** | **9.56 GB**（params 6.0 GB + train_state 3.1 GB） | **9.57 GB** |
| 300 步总用时 | 30.2 分钟 | 61.4 分钟 |
| **损失**（前 10 步均值 → 最后 10 步均值） | **0.0802 → 0.0192** | **0.0782 → 0.0188** |
| 损失（第 0/50/100/150/200/250/299 步） | 0.084, 0.059, 0.054, 0.023, 0.023, 0.022, 0.019 | 0.081, 0.063, 0.043, 0.037, 0.020, 0.022, 0.017 |

### 批量 64 第一次失败

第一次跑批量 64 时只设了 `XLA_PYTHON_CLIENT_PREALLOCATE=false`，没设显存比例，JAX 的分配上限仍是默认的 75%（约 36 GiB）。第一步就报显存不足：

```text
Allocator (GPU_0_bfc) ran out of memory trying to allocate 20.35GiB (rounded to 21848123392)
RESOURCE_EXHAUSTED: Out of memory while trying to allocate 21848123248 bytes.
```

完整日志在实验室电脑 `~/lab_logs/2026-11-23/openpi_train_bs64.log`。之后只加 `XLA_PYTHON_CLIENT_MEM_FRACTION=0.9`（上限约 43 GiB），其余不变，以新的实验名重跑，成功。峰值 31.5 GiB 虽然低于 36 GiB，但不预占时分配器要一次申请 20 GiB 的连续块，75% 上限下放不下。

## 4. 结论

1. **两种批量都能训练，损失都在下降**（约 0.08 → 0.019）。
2. **瓶颈是显卡计算，不是读数据**：训练时显卡利用率几乎一直 100%。
3. **批量 64 没有更快**：每秒样本数 5.3，比批量 32 的 5.9 还略低，且需要把显存上限提到 90% 才放得下。批量 32 在默认上限内就能跑，峰值 23.3 GiB，留有余量。**批量定为 32**（用户 2026-11-25 决定，之后所有要比较的模型都用 32；`max_token_len` 保持 π₀.₅ 的默认值 200）。
4. **训练时间**：批量 32 每步 5.4 s。按 openpi 默认的 3 万步，一次训练约 45 小时；1 万步约 15 小时。用户 2026-11-25 确认训练时间不是问题：每个模型的训练步数用 2,000 条试训的验证曲线来定，M1、M2、M3 各跑 2–3 个随机种子（见 [LAB_MASTER_PLAN.md](LAB_MASTER_PLAN.md) 14.2 节）。
5. **检查点每个约 9.6 GB**（与批量无关）。原来按 12 GB 估算偏大。openpi 只保留最新的一个检查点，再加上步数是 `keep_period` 整数倍的（默认 5,000，3 万步会留 6 个、约 58 GB）。正式训练把 `keep_period` 设为 10,000，用验证集挑出最好的检查点后删掉其余的（删除前问用户）。
6. **内存**：整机最多用到 22.7 GiB（共 30 GiB），可以接受，但训练时不宜同时跑评估或数据生成（与“排队轮流用”的安排一致）。

## 5. 文件位置（实验室电脑）

| 内容 | 位置 |
| --- | --- |
| 测量结果（含每步损失、全部采样） | `outputs/openpi_probe/bs32.json`、`outputs/openpi_probe/bs64_mf090.json` |
| 训练日志 | `~/lab_logs/2026-11-23/openpi_train_bs32.log`、`openpi_train_bs64.log`（失败）、`openpi_train_bs64_mf090.log` |
| 测试检查点（9.6 GB） | `outputs/openpi_checkpoints/pi05_robolab_lora/probe_bs32/299`：保留，等另一个对话的 PR 合并后做冒烟测试（检查点 → 桥接服务 → 评估 2 条）；批量 64 的 `probe_bs64_mf090` 已于 2026-11-25 经用户同意删除 |
| 归一化统计 | `outputs/openpi_assets/pi05_robolab_lora/robolab/p4d_v1_train/norm_stats.json` |

## 6. 复现

```bash
cd ~/projects/robolab-studio
deactivate 2>/dev/null; unset VIRTUAL_ENV
export HF_LEROBOT_HOME=~/projects/robolab-studio/outputs/lerobot HF_HUB_OFFLINE=1 XLA_PYTHON_CLIENT_PREALLOCATE=false
PY=~/projects/openpi/.venv/bin/python
$PY scripts/openpi_robolab.py norm-stats
$PY scripts/openpi_robolab.py train --batch-size 32 --steps 300 --exp-name probe_bs32 --measure outputs/openpi_probe/bs32.json
XLA_PYTHON_CLIENT_MEM_FRACTION=0.9 $PY scripts/openpi_robolab.py train --batch-size 64 --steps 300 --exp-name probe_bs64_mf090 --measure outputs/openpi_probe/bs64_mf090.json
```

`train.py` 会把编译缓存写到 `~/.cache/jax`，同一配置第二次运行时第一步会快很多。
