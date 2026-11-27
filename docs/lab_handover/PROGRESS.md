# RoboLab Studio 实验室电脑进度记录

## 0. 下次如何回到这次对话（先看这里）

1. **启动目录**：这次的 Claude Code 是在 WSL `Ubuntu-22.04` 的 **`/home/rami`**（即 `~`）里输入 `claude` 启动的。启动时终端里手动激活着 vla 环境 `/home/rami/vla/.venv`，启动文件不会自动激活它。
   - 对话记录：`~/.claude/projects/-home-rami/bd700079-2201-4111-a0c8-1e0c0df671de.jsonl`
   - 会话 ID：`bd700079-2201-4111-a0c8-1e0c0df671de`
2. **回到这次对话**：打开 Ubuntu-22.04 终端（Windows Terminal 里的 Ubuntu-22.04 标签，或在 PowerShell 里运行 `wsl -d Ubuntu-22.04`），依次输入：
   ```bash
   cd ~
   claude --resume bd700079-2201-4111-a0c8-1e0c0df671de
   ```
   - 必须在 `~` 下运行：Claude Code 按启动目录保存对话。
   - 如果提示找不到，在 `~` 下运行 `claude --resume`，从列表里按时间（2026-11-18 下午）选这一条。
   - `claude --continue` 会直接接上 `~` 下最近的一次对话；如果中间在 `~` 开过别的对话就会接错，所以优先用会话 ID。
   - 不需要先激活 vla 环境。
3. **接不回来时，在新对话里先让 Claude 依次读这些文件**：
   1. `~/lab_logs/PROGRESS.md`：本文件，**先看下面最上面的那一节“当前状态”**（标着“最新”的那节）。
   2. `~/projects/robolab-studio/docs/LAB_STAGE_HANDOFF.md`：交接文档（工作规则、已定决定、下一步）。
   3. `~/projects/robolab-studio/docs/LAB_MASTER_PLAN.md`：项目全景（从开始到以后、建模尺寸、实验室布局、新决定、与之前的不同）。
   4. `~/projects/robolab-studio/docs/LAB_ZEROSHOT_REPORT.md`：零样本评估结果。
   5. 桥接代码：`pi05-bridge` 分支的 `scripts/serve_openpi_bridge.py`、`robolab/policy.py`（`OpenpiAdapter`）、`tests/test_openpi_bridge.py`（`git show --stat 37b4e4c`）。

   注意：第 2–3 份文档的最新版（2026-11-25）在分支 `lerobot-conversion` 上，推送并合并之前 GitHub 的 `main` 上还是旧版。如果检出的不是它，用 `git show lerobot-conversion:docs/LAB_MASTER_PLAN.md` 这样的命令读。

   开场可以直接说：“先读 ~/lab_logs/PROGRESS.md 和其中列出的文档，然后从‘当前状态’里的第一项待办接着做，动手前先告诉我你的理解和计划。”
   另外，Claude 的持久记忆（`~/.claude/projects/-home-rami/memory/`）里已经记下这份文件的位置和各项约束，只要在 `~` 下开新对话，就会自动读到。

---

## 当前状态（2026-11-25 收工时）——最新，先看这一节

**环境**：
- 收工时没有后台进程，显卡空闲；C 盘剩约 60 GB。
- 当前检出的分支是 `lab-cameras`（`8a41ad4`，本地，未推送）。
- 本地 `main` 还停在 `917e890`，没有 pull；`origin/main` 是 `a512033`（含 PR #12、#13）。

**今天完成**
- **上午**：
  - 读了 D455 参数文档（PR #12、#13）。
  - 用户定了批量 32（`max_token_len` 保持 200）和正式训练的规则，写进了 `lerobot-conversion` 分支上 `docs/LAB_MASTER_PLAN.md` 的 14.2 节。
  - 经用户同意，删除了 `probe_bs64_mf090`、空目录 `probe_bs64` 和 `p4d_v1_train_clean`；`probe_bs32` 保留。
  - 把 `origin/main` 合进 `lerobot-conversion`（`e16f2df`），又提交了一次文档（`054b718`）。之后用户推送了 `lerobot-conversion`。
- **下午**：做了第 5 步相机里不需要联网的部分，都在分支 `lab-cameras` 上（从 `lerobot-conversion` 拉出）。
  - **相机分配**：第一台（246322302827，fovy 63.789°）装腕部；第二台（141322252821，fovy 65.031°，USB 口偏松）做外部相机。`docs/calibration/d455_cameras.json` 里的 `role` 已填好。
  - **`robolab/lab_scene.py`**：两台仿真相机各用自己的 fovy（`D455_UNITS`，注明出处），机身按"RGB 镜头偏离中心 11.4 mm"摆放；有测试核对这些常量和 JSON 一致。
  - **新增代码**：`robolab/clearance.py`（间隙计算）、`scripts/study_wrist_camera.py`（研究）、`scripts/wrist_mount_spec.py`（算打印用的数字）。后端测试 125 项通过，3 项按设计跳过。
  - **腕部相机研究跑完**（29 分钟）：42 个候选里 22 个可行。**初步推荐偏移 65 mm、仿真高度 10 mm（真机镜头离法兰面 16 mm）、倾角 36.8°**，光轴穿过抓取点。
  - **主要发现**：
    - 11-17 研究假设"镜头和法兰同高、偏移 6 cm"，这样会碰到 link7；
    - 镜头高度超过约 30 mm，会碰到"最低物体"的粗端；
    - 真机手指比仿真远离法兰 6.0 mm（franka_description 0.7.0 是 58.4 mm，robosuite 是 52.4 mm）。

**下次从这里接着做**

(1) **GitHub**
- **PR #14**：另一个对话写的评估链路改进，内容是：评估改用无损 PNG、桥接服务固定噪声、桥接服务能加载 `pi05_robolab_lora`、自动检查加上桥接测试。已经开好，检查已通过，等用户合并。对应分支 `origin/claude/magical-clarke-jfktyz`（`c109f92`）。
- **`lerobot-conversion`**：已推送（`054b718`），PR 还没开。文案在 `~/lab_logs/2026-11-25/PR_lerobot-conversion.md`。
- **合并方式**：两个都要用 **“Create a merge commit”** 合并。
- **合并顺序**：两个 PR 改的文件不重叠，先合哪个都行。但 PR #14 的桥接服务要从 `scripts/openpi_robolab.py` 注册 `pi05_robolab_lora`，这个文件要等 `lerobot-conversion` 合并后才进 `main`。所以冒烟测试要等两个 PR 都合并后再做。
- 合并后我用公开接口查一下 Lightweight checks。实验室电脑没有 GitHub 凭据，推送由用户做。

(2) **相机**
- **研究结果**：
  - `~/projects/robolab-studio/outputs/wrist_camera_study/summary.md`：42 个候选的全表；
  - 同目录下的 `metrics.json`（全部数字）和 `images/`（推荐方案的画面）；
  - 简短记录：`~/lab_logs/2026-11-25/wrist_camera_study_record.md`；
  - 运行日志：`~/lab_logs/2026-11-25/wrist_camera_study.log`。
- **还差支架尺寸说明书**：下次写。打印用的数字用 `.venv-sim/bin/python scripts/wrist_mount_spec.py --offset-mm 65 --height-mm 10` 算。
- **相机分支**：`lab-cameras`（`8a41ad4`，基于 `lerobot-conversion` 的 `054b718`），只在本地，还没推送。两个 PR 合并后，要先把最新的 `main` 合进 `lab-cameras`。
- **等用户确认**：
  - 推荐方案（65 mm / 10 mm / 36.8°）；
  - "部位在画面中间"按"光轴穿过抓取点"来理解行不行。现在抓取点在正中，但画上夹爪后，看得见的那部分重心偏上约 0.24 个半画面高。
- **写说明书前要用卡尺或实物核对的假设**：D455 机身尺寸、RGB 镜头在机身上的位置、前玻璃比光心靠前约 5 mm、TCP 103.4 mm，以及 D455 的安装孔。

(3) **两个 PR 都合并后，做一次冒烟测试，只验证链路通不通，不看成功率。** 步骤（用户原文）：
- 先把相机分支的改动提交好，确认工作区干净，再切到 main 并 git pull。
- 终端 A：cd ~/projects/openpi，然后运行 env -u VIRTUAL_ENV XLA_PYTHON_CLIENT_PREALLOCATE=false HF_HUB_OFFLINE=1 ~/projects/openpi/.venv/bin/python ~/projects/robolab-studio/scripts/serve_openpi_bridge.py --config pi05_robolab_lora --checkpoint <probe_bs32 最后一步的目录，例如 outputs/openpi_checkpoints/pi05_robolab_lora/probe_bs32/299 的绝对路径> --port 8000 --request-log ~/lab_logs/<日期>/smoke_requests.jsonl
- 等服务打印 ready 后，终端 B：cd ~/projects/robolab-studio，然后运行 bash scripts/launch.sh --p5-eval --policy adapter:pi05 --endpoint http://127.0.0.1:8000 --timeout 60 --dataset outputs/p4d_lab --scene-set extended --anti-roll --execute-steps 5 --limit 2 --workers 1 --recheck 2 --output-dir outputs/lab_smoke_probe_bs32
- 跑完先停评估、再停服务，然后汇报：服务 ready 日志里的 noise 字段；eval_config.json 里 policy_spec.image_encoding 是不是 png；result.json 里 policy.adapter_status.service_noise 的内容；summary.json 的 recheck_identical 是 true 还是 false。如果是 false，找出第一处不同的地方（文件和 tick），并对比请求日志里同一条目两次运行的 noise_key 是否相同，判断是噪声的问题，还是显卡计算本身不确定。

补充说明（不是用户原文）：
- probe_bs32 最后一步的绝对路径是 `/home/rami/projects/robolab-studio/outputs/openpi_checkpoints/pi05_robolab_lora/probe_bs32/299`，11-25 核实过存在。
- `<日期>` 填做测试当天的日期，先 `mkdir -p ~/lab_logs/<日期>`。
- 相机分支 11-25 已经提交（`8a41ad4`），工作区是干净的。

(4) **还没定的事**（用户原文）：正式训练要不要设 discrete_state_input=True；学习率的 decay_steps 要和 num_train_steps 对齐；keep_period 设成 10,000；每个模型跑 2–3 个随机种子；1 TB 硬盘到货后建工作盘（mkfs 之前先把 lsblk 的输出给我看）；新数据生成器的设计说明。

补充说明（不是用户原文）：
- decay_steps 对齐、keep_period 设 10,000、每个模型 2–3 个种子，11-25 已经作为规则写进 `LAB_MASTER_PLAN` 14.2 节（在 `lerobot-conversion` 上）。但 `scripts/openpi_robolab.py` 还没改：缺 `--seed`、`--resume`、`lr_schedule`，`keep_period` 也还是短时测量时的设置。
- 上午提过的问题还没回答："每个模型的训练步数用试训的验证曲线来定"，是所有模型共用一个步数（A），还是各模型分别定步数（B）？
- 在 PR #14 合并之前，那 5 个评估链路文件继续不改。另一个对话还改了 `robolab/p5_compare.py`、`scripts/run_p5_eval.py`、`tests/test_p5_eval.py`、`tests/test_p5_extensions.py`、`docs/P5_EVAL_FORMAT.md`、`docs/VLA_INTEGRATION.md`，这些我们这边也一直没动。

---

## 当前状态（2026-11-23 收工时）（已过时，留作记录）

**环境**：没有后台进程，显卡空闲；C 盘剩约 59 GB（`df -h /mnt/c`）；当前检出分支 `lerobot-conversion`（`5c965e2`，**本地，未推送**）。

**今天完成**（交接文档第 6 节第 2–4 步；文档已写进分支：`docs/LAB_STAGE_HANDOFF.md` 5.4 节、新的 `docs/LAB_LORA_PROBE.md`）
- 第 2 步：桥接服务 PR #11 已以合并提交并入 `main`（`917e890`），`37b4e4c` 在 `main` 历史里。空提交 `bc961fe` 已去掉（reflog 可找回）。
- 第 3 步：训练集转 LeRobot：`outputs/lerobot/robolab/p4d_v1_train`（2.72 GB，120 条 / 26,640 帧，53,280 张图逐像素一致，可追加）；用已提交代码 `0b6a173` 转换，`dirty: false`，与旧版逐字节相同；旧版已删、新版改名回 `robolab/p4d_v1_train`（manifest 里记了 `renamed_from`）。
- 第 4 步：下载了 `pi05_base`（11.6 GiB，22.5 分钟）；LoRA 配置在 `scripts/openpi_robolab.py`（运行时注册进 openpi 的 `_CONFIGS_DICT`）；归一化统计已算；300 步测量：
  - 批量 32：第一步 118 s（编译），每步 5.43 s，显存峰值 23.3 GiB，检查点 9.56 GB，损失 0.080 → 0.019；
  - 批量 64：默认 75% 显存上限下第一步显存不足（日志 `~/lab_logs/2026-11-23/openpi_train_bs64.log`）；加 `XLA_PYTHON_CLIENT_MEM_FRACTION=0.9` 重跑成功：每步 12.0 s，峰值 31.5 GiB，检查点 9.57 GB，损失 0.078 → 0.019；
  - 两次显卡利用率都约 100%（不是卡在读数据）；整机内存最多 22.7 GiB / 30 GiB。

**等用户做 / 决定的事**
1. **推送并开 PR**：在单独的终端 `cd ~/projects/robolab-studio && git push -u origin lerobot-conversion`，开 PR（文案 `~/lab_logs/2026-11-23/PR_lerobot-conversion.md`），**合并时选 “Create a merge commit”**（数据集记录的提交 `0b6a173` 要留在 `main` 历史里）。后端测试 112 项已通过。
2. **定批量大小**：建议 **32**（批量 64 每秒样本数反而略低，5.3 vs 5.9，且要把显存上限提到 90%）。定下后所有要比较的模型都用同一个。
3. **删除前要问用户的东西**（目前都还在）：测试检查点 `outputs/openpi_checkpoints/pi05_robolab_lora/probe_bs32`（9.0 GB）、`probe_bs64_mf090`（9.0 GB）、失败那次留下的空目录 `probe_bs64`（4 KB）；转换中间产物 `outputs/lerobot/robolab/p4d_v1_train_clean`（2.6 GB，代码版本记成了 dirty，与正式版数据逐字节相同）。注意：删除不会让 C 盘变大（WSL 虚拟磁盘不缩小），只是给虚拟磁盘内部腾地方。
4. **正式训练的步数**：批量 32 每步 5.4 s，3 万步约 45 小时、1 万步约 15 小时，要据此规划 M1–M3。

**下次接着做（按顺序）**：PR 合并 → 第 5 步相机（在仿真里定位置、给出 3D 打印支架尺寸；用户读 D455 内参）→ 第 6 步移动硬盘到货后建工作盘（`mkfs` 前给用户看 `lsblk`）→ 第 7 步新生成器的设计说明。另一个对话的 PR（无损 PNG、CI 加桥接测试、固定噪声）合并前，不要改那 5 个文件（见下面 11-20 一节里的列表）。

---

## 当前状态（2026-11-20 收工时）（已过时，留作记录）

**环境**：没有后台进程，显卡空闲；C 盘剩 98 GB；当前检出分支 `lab-docs-2026-11-20`。

**已完成**
- 交接文档第 6 节**第 1 步**：π₀.₅ 零样本评估（旧布局 240 条）完成。成功 36/240 = 15.0% [11.0, 20.1]，0 条策略错误，接口检查全部正确；模型基本不区分部位，43 条伸向障碍物。详见 `docs/LAB_ZEROSHOT_REPORT.md`。
- **文档**：本地分支 `lab-docs-2026-11-20` 提交 `e5db6ba`（基于 GitHub `main` 的 `d744aac`）：新增 `LAB_MASTER_PLAN.md`、`LAB_ZEROSHOT_REPORT.md`，更新 `LAB_STAGE_HANDOFF.md`、`NEXT_CHAT_HANDOFF.md`。已取消该分支对 `origin/main` 的上游关联（防止误推到 main）。
- **11-20 的新决定**（已写进上述文档）：只有一台电脑，生成/训练/评估排队；数据 5,000 条（先 2,000）；任务扩展到任意命名部位；物体 A 参数化日常物体 + B PartNet + C 奇怪形状；四层留出（新个体、整类别、说法、部位类型）；实验只用英文；新数据和测试全部用实验室布局；相机位置在仿真里定、真机 3D 打印照做（不量支架，下周读 D455 内参）；1 TB 移动硬盘到货前不生成新数据、不正式训练。

**2026-11-23 更新**：文档分支**已由用户推送**到 GitHub（`origin/lab-docs-2026-11-20` = `e5db6ba`），自动检查 Lightweight checks **通过**；**还没有开 PR**。另外 GitHub `main` 新合并了 PR #9（`b5a35fa`，11-22，只新增 `docs/PAPER_NOTES_2026-11.md`：两篇相关论文的笔记和真实环境鲁棒性），与文档分支不冲突。待办 1 只剩“开 PR → 合并”。

**2026-11-23 第 2 步**：`bc961fe`（空提交）已去掉（reflog `pi05-bridge@{1}` 可找回）；`pi05-bridge` = `37b4e4c` + 合并 `main` 的 `78d1434`；后端 112 项、界面测试、`--smoke` 通过；用户已推送，PR 由用户开（合并时必须选 “Create a merge commit”，保留 `37b4e4c`）。PR 文案：`~/lab_logs/2026-11-23/PR_pi05-bridge.md`。

**第 2 步 PR**：#11（`pi05-bridge` → `main`，头 `78d1434`），Lightweight checks（push 和 pull_request 两次）都通过；由用户合并，必须选 “Create a merge commit”。

**第 3、4 步的确切命令（`HF_LEROBOT_HOME` 必须前后一致）**：LeRobot 数据放在 `outputs/lerobot/`，以后 `outputs/` 换成移动硬盘的链接时会一起搬过去。所有用到 LeRobot 数据的命令（转换、校验、openpi 的 `compute_norm_stats.py`、`train.py`）都先设：
```bash
deactivate 2>/dev/null; unset VIRTUAL_ENV
export HF_LEROBOT_HOME=~/projects/robolab-studio/outputs/lerobot
export HF_HUB_OFFLINE=1
```
- 第 3 步转换和校验（分支 `lerobot-conversion`；用 openpi 环境的 Python，因为 openpi 固定了 LeRobot 版本）：
  ```bash
  cd ~/projects/robolab-studio
  ~/projects/openpi/.venv/bin/python scripts/convert_p4_to_lerobot.py convert --hdf5 outputs/p4d_lab/export/p4_osc_train.hdf5 --repo-id robolab/p4d_v1_train
  ~/projects/openpi/.venv/bin/python scripts/convert_p4_to_lerobot.py verify  --hdf5 outputs/p4d_lab/export/p4_osc_train.hdf5 --repo-id robolab/p4d_v1_train
  ```
  数据集位置 = `$HF_LEROBOT_HOME/robolab/p4d_v1_train`；openpi 配置里的 `repo_id` 写 `robolab/p4d_v1_train`。
- 第 4 步（等用户确认后才做；下载 `pi05_base` 前先问）：在同一个终端（上面三个变量已设）里，从我们自己的仓库注册 LoRA 配置后调用 openpi 的 `compute_norm_stats.py` / `train.py`；另外加 `XLA_PYTHON_CLIENT_PREALLOCATE=false`（测真实显存峰值），关 wandb。

**另一个对话审过桥接代码（可以合并），记下的待办——会在另一个对话里合成一个单独的 PR：**
- 评估发给模型的图像是 JPEG（`p5_eval.encode_jpeg`，质量 95），训练数据是无损的：要给观测加无损 PNG 编码，评估改用 PNG（`policy.validate_observation` 现在只接受 `jpeg_base64`）。
- GitHub 的 Lightweight checks 没跑 `tests/test_openpi_bridge.py`（需要 Pillow），要加进 `.github/workflows/checks.yml`。
- 固定噪声：用观测内容（图像、指令、`episode_tick`）的哈希作种子，生成 (10, 32) 噪声传给 `policy.infer(obs, noise=...)`；**不要用 `observation_id`**（只是请求计数）。
- **在那个 PR 合并之前，不要改** `robolab/policy.py`、`robolab/p5_eval.py`、`scripts/serve_openpi_bridge.py`、`tests/test_openpi_bridge.py`、`.github/workflows/checks.yml`。
- 官方 LIBERO 上复现 `pi05_libero` 的成绩：显卡空闲时再做。
- 第 4 步：π₀.₅ LoRA 配置写在我们自己的仓库里，**不改 openpi 的 `src/openpi/training/config.py`**；运行前把配置注册进 openpi 的 `_CONFIGS_DICT`（config.py 第 975 行，`get_config` 在第 982 行），再从外部调用 `scripts/train.py` 的 `main(config)`、`scripts/compute_norm_stats.py` 的 `main(config_name)`。桥接服务的 `load_openpi_policy` 也用 `get_config`，加载我们训练的模型前也要先注册。

**待办（按顺序）**
1. **推送文档分支**（✅ **全部完成**：11-23 推送，PR #10 已合并进 `main` = `f3374bd`；本地 `main` 已快进到 `f3374bd` 并检出。下面这一条的说明留作参考）（用户已同意，顺序改为先推文档、再做第 2 步）。**卡在：这台电脑没有 GitHub 推送凭据**（没有凭据助手、没有 `gh`、没有 SSH 密钥；`GIT_TERMINAL_PROMPT=0 git push` 报 `could not read Username`）。两个办法：
   - **用户自己推**（最快）：先在 GitHub 网页生成一个 fine-grained personal access token（只选 robolab-studio 仓库；权限 Contents 读写；要在命令行开 PR 的话再加 Pull requests 读写），然后在单独的 Ubuntu-22.04 终端运行：
     ```bash
     cd ~/projects/robolab-studio
     git push -u origin lab-docs-2026-11-20
     ```
     用户名填有写权限的 GitHub 用户名，密码处粘贴 token（不是账号密码，输入时不显示）。推完在 GitHub 网页点 “Compare & pull request”，标题和描述从 `~/lab_logs/2026-11-20/PR_lab-docs-2026-11-20.md` 复制。
   - **长期办法（推荐，需用户同意安装）**：把 GitHub 官方命令行工具 `gh` 装到 `~/.local/bin`（不用 sudo），用户在终端运行一次 `gh auth login` 和 `gh auth setup-git`。之后 Claude 就能自己推送、开 PR、查看自动检查，第 2 步也要用到。
   - 推送后确认 GitHub 的自动检查（`.github/workflows/checks.yml`，push 时就会运行）通过，再合并 PR。没有 `gh` 时可以查公开接口：`curl -s "https://api.github.com/repos/6f6sn7w4pb-ops/robolab-studio/actions/runs?branch=lab-docs-2026-11-20"`。
2. **第 2 步**：复核桥接服务并合并 `pi05-bridge`：`git switch pi05-bridge`，合并最新 `main`，跑 `bash scripts/test_engine.sh`，推送前问用户，开 PR，CI 通过后合并。同时讨论：采样噪声是否改为由“条目 + tick”确定（`Policy.infer` 支持传入 `noise`）；是否在官方 LIBERO-Object 上复现一次成绩。
3. 之后：第 3 步（现有数据转 LeRobot，记录大小、核实能否追加）、第 4 步（LoRA 短时训练，测每步耗时、显存/内存峰值、检查点大小）、相机（仿真里定位置并给出 3D 打印支架尺寸；用户下周读 D455 内参）、移动硬盘到货后建工作盘（`mkfs` 前先给用户看 `lsblk`）。详见交接文档第 6 节。

---

（以下是收工前的逐次记录，内容已汇总到上面。）

**2026-11-20 文档更新（晚些时候）**：新建本地分支 `lab-docs-2026-11-20`（从 GitHub 最新 `main` = `d744aac` 拉出），改了 4 个文档，**已本地提交 `e5db6ba`，未推送**（已取消该分支对 `origin/main` 的上游关联，防止误推到 main；用户同意的合并顺序：第 2 步先合并 `pi05-bridge`，再推送这个文档分支，推送前都要先问用户）。当前检出的是这个文档分支，跑 π₀.₅ 评估前要 `git switch pi05-bridge`：新增 `docs/LAB_MASTER_PLAN.md`（项目全景：从开始到以后、建模尺寸、实验室布局、新决定、与之前的不同）和 `docs/LAB_ZEROSHOT_REPORT.md`（零样本评估报告）；更新 `docs/LAB_STAGE_HANDOFF.md`（第 3–10 节按 11-20 的决定修改）和 `docs/NEXT_CHAT_HANDOFF.md`（新增第 23 节）。`pi05-bridge` 分支没有动（桥接代码只在那个分支上；切到文档分支时 `robolab/policy.py` 看不到 `OpenpiAdapter` 是正常的）。11-20 的新决定：5,000 条；任意命名部位；A+B+C 三类物体；四层留出；只用英文；全部改用实验室布局；相机在仿真里定、3D 打印；只有一台电脑排队用。

**2026-11-20 更新**：交接文档第 6 节第 1 步（π₀.₅ 零样本评估）**已完成**：240/240，`finalized`，0 条 policy_error。成功 36/240 = 15.0% [11.0, 20.1]；部位命中 31.7%；抬起 46.7%；碰撞 32.1%；接触时部位正确率 51.8%（约等于随机）。99 条没碰到物体，其中 43 条把机械臂伸向了蓝色障碍物。总用时 70.1 s + 1581.6 s ≈ 27.5 min。共 10,890 次请求，0 错误，只有夹爪一维被裁剪，最大超出 0.0244。分析图和日志在 `~/lab_logs/2026-11-20/`。下一步是第 2 步（复核并合并 pi05-bridge），**等用户看过结果再开始**。下面第 2–3 节是 11-18 的记录，续跑部分已完成。

更新时间：2026-11-18 16:55（GMT）。机器：Windows + WSL `Ubuntu-22.04`，用户 `rami`，显卡 RTX 6000 Ada 48 GB（交接文档写的是 A100 80GB，实际不是）。

当前状态：**所有后台进程已停止，显卡已释放**（nvidia-smi 显存 939 MiB，为 Windows 桌面基线，无残留进程）。

---

## 1. 已完成的事

### 1.1 平台复刻（robolab-studio，`main` 分支 a9ac07f）
- 系统库：libegl1、libgl1、libglfw3、libopengl0、libxkbcommon-x11-0、libxcb-cursor0、build-essential、linux-libc-dev、python3.10-dev、fonts-noto-cjk 均已安装。
  - INSTALL.md 漏了 **build-essential、linux-libc-dev**（robosuite → pynput → evdev 需要从源码编译）；requirements/sim.txt 漏了 **h5py**。笔记本那边会推送修复，这里不要改。
- `.venv-sim`：MuJoCo 2.3.7、robosuite 1.4.1、NumPy 1.26.4、LIBERO @ 8f1084e3（源码可编辑安装），另装 h5py 3.16.0（安装前后包列表对比只多这一个）。
- `.venv`（GUI）：PySide6 6.8.3。
- 测试：`--doctor` 通过；P1 冒烟（EGL 双路 RGB）通过；后端 98 项（现在含新测试共 112 项）通过；GUI 测试 14 项和 `--smoke` 通过。
- P4-D 数据集重新生成（`outputs/p4d_lab`，4 进程 30.1 分钟）：`passed=true`，gate 6 项全部 true，204/204 校验通过（200 示范 + 4 对照）。
- 指纹核对：`images_differ_physics_identical`。物理 204/204 一致，图像 0/204 一致，不一致图像 90,576 张（=全部，显卡/驱动不同）。**训练和评估要用本机渲染的数据。**
- 归一化统计 `norm_stats.json`：与笔记本**逐字节一致**（sha256 bb2f8010…87958），7 个统计量最大绝对差全为 0。
- 训练文件导出：`outputs/p4d_lab/export/`，4 个 HDF5 共 5.96 GB，200 示范 / 44,400 步，88,800 张图解码校验一致，标签不一致 0。
- 规则教师评估（`outputs/lab_eval_teacher`）：64/64 成功，阻挡对照 4/4 检出碰撞，harness_checks 4 项全 true。

### 1.2 openpi 安装
- uv 0.13.9 在 `~/.local/bin`（官方脚本，未改 shell 启动文件）。
- openpi：`~/projects/openpi`，HEAD 215abfb（2026-08-24），子模块 aloha d1dc83a、libero f78abd6。
- 环境：`~/projects/openpi/.venv`，Python 3.11.18，7.7 GB；jax/jaxlib 0.5.3、flax 0.10.2、torch 2.7.1+cu126。`jax.devices()` → `[CudaDevice(id=0)]`。
- vla 环境 `/home/rami/vla/.venv` 未改动（75 个包，前后逐行一致）。

### 1.3 pi05_libero 下载与推理测试
- 权重：`~/.cache/openpi/openpi-assets/checkpoints/pi05_libero`，12 GB（gcsfs 匿名下载，141 s）。分词器 `~/.cache/openpi/big_vision/paligemma_tokenizer.model`（4 MB）。**未下载 pi05_base 或任何数据集。**
- 推理测试（`make_libero_example()` 随机输入）：输出 (10, 7)，全部有限，范围 −1.0007 ~ 1.0109；首次（含 JIT）19.8 s，之后 10 次平均 88.1 ms；显存实际占用约 8.4 GiB（`XLA_PYTHON_CLIENT_PREALLOCATE=false`）。
- pi05_libero **不使用 state**（pi0.5 无 state token，且 `discrete_state_input=False`），所以我们 8 维 state 与 LIBERO 坐标系不同不影响它。

---

## 2. 正在做：π₀.₅ 接入评估框架 + 零样本评估

### 2.1 代码（分支 `pi05-bridge`，已本地提交，**未推送**）
- `37b4e4c` 代码提交（评估配置里记录的就是这个版本）：
  - `scripts/serve_openpi_bridge.py`：桥接服务（用 openpi 环境的 Python 运行）。/health、/stats、/predict；图像水平翻转 `image[:, ::-1]`（= openpi LIBERO 示例对原始帧的 180° 旋转，已对照 examples/libero/main.py、docs/P4_DATASET_FORMAT.md §4 和 openvla_service.py 确认）；推理加锁串行；裁剪规则见第 5 节；`--request-log` 每次请求写一行 JSONL；只接受本地检查点目录，不会下载。
  - `robolab/policy.py`：`OpenpiAdapter`，注册名 `pi05`；拒绝 test_backend；`real_model`/`model_loaded` 按服务报告设置。OpenVLAOFTAdapter、MockPolicy 未改。
  - `robolab/locales/en.json`：新增适配器英文名一条。
  - `tests/test_openpi_bridge.py`：14 项不依赖显卡的测试（假策略对象）。
- `bc961fe` 空提交，只用来标记“未完成”状态。

### 2.2 已通过的测试
- 新增 14 项全部通过；故意改坏验证：去掉翻转、改成 180° 旋转、去掉推理锁，测试都会失败（测试有效）。
- `bash scripts/test_engine.sh`：112 项通过（原 98 + 新 14），3 项 GUI 测试按设计跳过；GUI 环境里 i18n/P4/P5 视图测试 14 项和 `--smoke` 通过。

### 2.3 小规模试跑 ✅（`outputs/lab_eval_pi05_smoke`）
- 2 条（box_step-test_instance-000 的粗端/细端）：无 policy_error，每条查询 45 次，`is_vla=true`。
- 关键帧确认机械臂真实运动（末端每轴移动 0.39–0.58 m，物体抬起 0.41 m）。两条都抓了**粗端**：粗端那条成功，细端那条判为抓错部位——模型基本不区分“粗端/细端”。
- 服务端：135 次请求，0 错误；569 个数值在 118 次响应中被裁剪，最大超出 0.0162；平均推理 92.8 ms。
- `recheck_identical=false` 是预期的：openpi 每次推理都抽新的随机噪声，复跑不会逐字节一致。

### 2.4 完整评估：**已开始，在 8/240 条时按计划停止**（`outputs/lab_eval_pi05_libero_zeroshot`）
- 第一段：16:51:57 开始，16:53:04 停止（eval 记录 70.1 s），`summary.json` 状态 `cancelled`，无半截 episode 目录。
- 这 8 条（都在 test_instance）的初步数字，**样本太少，不能下结论**：成功 3/8，部位命中 4/8，抬起 8/8，碰撞 2/8，0 错误。
- 服务端（这一段）：360 次请求，0 错误，1,431 个数值被裁剪（302 次响应），最大超出 0.0244；4 进程并发时平均推理 128.3 ms、平均排队 94.3 ms。
- 按这个速度，剩下 232 条约需 35 分钟。

### 2.5 还没做
- 完整评估剩余 232 条 + 结束时 2 条复跑。
- 结果报告：成功率、部位命中率、抬起率、碰撞率（附 95% 置信区间），按已见/未见形状族（test_instance / test_unseen）和已见/留出说法分组；最常见失败原因；裁剪次数和最大超出量；平均推理延迟；总用时。
- 没有写报告文档（docs 下），也没有更新 NEXT_CHAT_HANDOFF.md。

---

## 3. 下次接着做：第一步和确切命令

**开一个新的 Ubuntu-22.04 终端。如果提示符前有 `(.venv)`，先运行 `deactivate`。**

```bash
# 0. 确认分支与工作区
cd ~/projects/robolab-studio
git switch pi05-bridge
git --no-optional-locks status --short      # 应为空
nvidia-smi --query-gpu=memory.used --format=csv   # 约 1 GB，没有别的进程占显卡

# 1. 终端 A：启动桥接服务（约 35 s 后就绪；请求日志接着追加到同一个文件）
cd ~/projects/openpi
env -u VIRTUAL_ENV XLA_PYTHON_CLIENT_PREALLOCATE=false HF_HUB_OFFLINE=1 \
  ~/projects/openpi/.venv/bin/python ~/projects/robolab-studio/scripts/serve_openpi_bridge.py \
  --config pi05_libero --checkpoint ~/.cache/openpi/openpi-assets/checkpoints/pi05_libero --port 8000 \
  --request-log ~/lab_logs/2026-11-18/openpi_bridge_full_requests.jsonl \
  2>&1 | tee -ai ~/lab_logs/2026-11-18/openpi_bridge_full_server_resume.log
# 看到 {"event": "ready", ...} 再继续

# 2. 终端 B：确认服务，然后续跑评估（endpoint、timeout 等都从 eval_config.json 读，不用再写）
curl -s http://127.0.0.1:8000/health
cd ~/projects/robolab-studio
env -u VIRTUAL_ENV bash scripts/launch.sh --p5-eval --output-dir outputs/lab_eval_pi05_libero_zeroshot --resume --workers 4

# 3. 结束后：看汇总，取服务端统计，然后在终端 A 按 Ctrl+C 停服务
python3 -m json.tool outputs/lab_eval_pi05_libero_zeroshot/summary.json | less
curl -s http://127.0.0.1:8000/stats
```

- 续跑会跳过已完成的 8 条，最后照常复跑 2 条。`summary.json` 的 `elapsed_wall_s` 只算续跑这一段，**总用时 = 第一段 70.1 s + 续跑的 elapsed_wall_s**。
- 服务重启后 /stats 计数会从 0 开始。整次评估的裁剪次数和最大超出量，用请求日志汇总：
  ```bash
  python3 -c "import json; L=[json.loads(l) for l in open('/home/rami/lab_logs/2026-11-18/openpi_bridge_full_requests.jsonl')]; print(len(L), 'requests,', sum(l['clipped_values'] for l in L), 'clipped values, max excess', max(l['max_excess'] for l in L), ', errors', sum(l['error_kind'] is not None for l in L))"
  ```
- 平均推理延迟：服务端看 /stats 的 `mean_inference_ms`、`mean_wait_ms`；评估端每条在 `episodes/<case>/timing.json` 的 `mean_policy_latency_ms`（含 JPEG 编码、HTTP、排队）。

---

## 4. 日志与输出位置

| 内容 | 位置 |
| --- | --- |
| 本次所有实验室日志 | `~/lab_logs/2026-11-18/` |
| ├ openpi 安装 | `openpi_uv_sync.log`、`openpi_install_notes.txt`、`vla_pip_{before,after}_openpi.txt` |
| ├ 权重下载 / 推理测试 | `pi05_libero_download.log`、`pi05_libero_infer_test.{py,log}`、`pi05_libero_infer_gpu_samples.csv` |
| ├ 桥接服务 | `openpi_bridge_{smoke,full}_server.log`、`openpi_bridge_{smoke,full}_requests.jsonl` |
| ├ 评估运行输出 | `lab_eval_pi05_smoke.log`、`lab_eval_pi05_full.log`、`lab_eval_pi05_full_task.output`、`smoke_keyframes.png` |
| └ 测试 | `test_engine_pi05.log`（112 项）、`gui_smoke_pi05.log` |
| 平台安装/复刻日志 | `~/projects/robolab-studio/outputs/p4d_lab/logs/install_2026-11-18/` |
| P4-D 数据集与导出 | `~/projects/robolab-studio/outputs/p4d_lab/`（`export/` 为训练文件，`fingerprint_check.json`） |
| 教师评估 | `~/projects/robolab-studio/outputs/lab_eval_teacher/` |
| π₀.₅ 试跑 | `~/projects/robolab-studio/outputs/lab_eval_pi05_smoke/` |
| π₀.₅ 完整评估（未完成） | `~/projects/robolab-studio/outputs/lab_eval_pi05_libero_zeroshot/` |
| openpi 代码与环境 | `~/projects/openpi/`（`.venv/`） |
| 权重与分词器 | `~/.cache/openpi/` |

Windows 资源管理器里打开：`\\wsl.localhost\Ubuntu-22.04\home\rami\lab_logs`（不要在地址栏输入 `/home/...`）。

---

## 5. 注意事项

- **先 deactivate vla**：终端里激活的 `/home/rami/vla/.venv` 会让 `python3`、`pip`、`uv pip` 指向 vla 环境。所有命令都用 `env -u VIRTUAL_ENV ...` 或先 `deactivate`。vla 环境不要改。
- **桥接服务**必须用 openpi 的 Python（`~/projects/openpi/.venv/bin/python`），评估用 robolab 的 `.venv-sim`（`launch.sh` 会自动选）。
- **`XLA_PYTHON_CLIENT_PREALLOCATE=false`**：否则 JAX 预占 75% 显存（约 36 GB），4 个仿真进程的 EGL 渲染会抢不到显存；服务实际只需约 8.6 GB。
- **动作裁剪规则**（夹爪维度常见 1.00x）：超出 [-1, 1] 不到 0.05 的裁剪到 ±1，并在响应里返回 `clipped_values`、`max_excess`；超出 ≥ 0.05 的返回错误、不裁剪，评估记为 `policy_error`。评估框架对 >1.0 的动作是严格判错，所以裁剪必须在服务端做。目前最大超出 0.0244，没有触发错误。
- **不要修改 `requirements/sim.txt` 和 `docs/INSTALL.md`**，笔记本那边会推送修复。
- 不要推送；不要下载 pi05_base 或任何数据集；不要训练；不为提高成功率做任何调整（这次是“不做微调”的基准）。
- 停止评估要先停评估、后停服务：对评估主进程发 SIGTERM（或 Ctrl+C），它会等每个 worker 做完当前这条再退出；如果先停服务，正在跑的 episode 会被记成 policy_error，而且 `--resume` 不会重跑它。
- 提交作者：本机没有 git 用户配置，提交时用 `git -c user.name="Last Faze" -c user.email=<以前提交用的邮箱> commit ...`（沿用仓库历史，没有写入配置）。检查状态用 `git --no-optional-locks status`，避免改动 `.git` 时间戳。
- `recheck_identical=false` 是预期结果（采样噪声），不是框架错误。
- C 盘只剩约 72 GB（WSL 虚拟磁盘在 C 盘，`df -h ~` 显示的 9xx GB 不准），下载 pi05_base（约 12 GB）或数据集前先确认。
- openpi 的下载代码会把 `~/.cache/openpi` 设成 777，这是 openpi 自身行为。
