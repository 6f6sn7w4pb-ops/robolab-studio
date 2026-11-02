# P0 / P1 运行与交接报告

日期：2026-11-01。范围仅为环境审计与 LIBERO 双相机离屏渲染，不含 P2、训练、大模型下载或真实硬件操作。

本文保留首次 CLI 验收记录。之后按用户要求完成的 GUI 接入单独记录在 [P1_GUI_REPORT.md](P1_GUI_REPORT.md)；本文中“未修改 GUI”等描述仅对应首次验收。

## 需求采用与本轮修改

已完整阅读用户提供的交接文档，按原始字节保存在本地项目根目录，原始毕设资料不随 GitHub 源码发布。SHA256：`9303619aa9efb55cfec13bc8afd2b1bd78d2ae5b67f3fbe747aed258abfe65aa`。当前开发范围见 [LAPTOP_ROADMAP.md](LAPTOP_ROADMAP.md)。

该文件作为后续分阶段需求；用户当前明确限定“本轮仅 P0/P1”，优先于其较长的后续排期。文档内机械臂型号未知的历史内容由对话中的 Panda（FER）、双 D455 RGB 配置补充；不假设实验室 GPU 或实机驱动已可用。

本轮新增：

- `scripts/audit_environment.py`：读取安装版本、真实导入结果、Git、配置、GPU、受保护文件哈希；将 OFT 导入放入离线 CPU-only 子进程，不读取模型权重。
- `scripts/smoke_test_libero.py`：直接复用现有 LIBERO，构建一个真实双 RGB 离屏环境；reset、官方初态、短步进、图像验证、PNG/GIF 与运行元数据导出。
- `scripts/smoke_test_libero.sh`：使用现有后端 Python，在子进程配置 EGL，并移除 X11/Wayland 的显示变量。不是 GUI 启动器。
- 本报告与 `docs/ENV_AUDIT.md`；README 增加当前阶段入口与历史功能的区分；`.gitignore` 忽略 `outputs/`。

没有修改既有仿真引擎、GUI/策略业务逻辑、LIBERO、OpenVLA-OFT 或 Python 包。没有复制 venv 或重建环境。

## 一条 WSL 运行命令

在 WSL Ubuntu 的终端执行：

```bash
bash ~/projects/robolab-studio/scripts/smoke_test_libero.sh
```

无需先激活虚拟环境、启动 GUI、下载模型或启动推理服务。每次运行约需初始化一次完整 LIBERO 场景；本轮实测约 71.4–73.4 秒。该耗时是本机观测，不是其他机器的性能承诺。

默认值与可覆盖方式：

| 配置 | 默认 | 覆盖 |
| --- | --- | --- |
| 后端 Python | `$HOME/venvs/openvla-oft/bin/python` | `ROBOLAB_BACKEND_PYTHON`，亦接受 `BACKEND_PYTHON` |
| LIBERO 源码 | `$HOME/projects/LIBERO` | `--libero-root`、`LIBERO_ROOT`、`ROBOLAB_LIBERO_ROOT` |
| 输出根目录 | 项目 `outputs/smoke_test` | `--output-dir`、`OUTPUT_DIR`、`ROBOLAB_OUTPUT_DIR` |
| LIBERO 配置 | `$HOME/.libero/config.yaml` | `LIBERO_CONFIG_PATH` 指定其所在目录；缺失时明确失败，不自动创建 |
| 任务/种子/初态 | `--task-id 0 --seed 0 --init-index 0` | 同名参数；索引是 0 起始 |
| 帧与步数 | `--size 256 --steps 20` | 同名参数；P1 限制 size 64–512、steps 1–200 |

每次生成 `outputs/smoke_test/<UTC时间_标识>/`。终端显示目录，根目录的 `latest.json` 指向最近一次完成的尝试及其成功/失败状态。失败有独立记录，不覆盖旧成功图像，也不将旧图像认作本次成功。

## 实际验证结果

| 检查 | 结果 |
| --- | --- |
| 原 `test_libero_object.py` | EGL reset、一次 step，进程退出 0 |
| P0 核心导入 / `pip check` | 通过；OFT 导入失败，因此审计退出 1，详见环境报告 |
| 最终脚本第 1 次 | `20261101T220359Z_fad81b`，退出 0，73.416 秒 |
| 最终脚本第 2 次 | `20261101T220544Z_8742f3`，退出 0，71.416 秒 |
| 双 RGB | 同一任务的 `agentview_image`、`robot0_eye_in_hand_image`；各 `256×256×3`、uint8、非空白，两路不同 |
| 短步进 | 20 步，控制频率 20 Hz，物理步长 0.002 秒，实际仿真时间推进约 1 秒 |
| PNG / GIF | 两路独立 PNG、并排 PNG、GIF 均可读取；GIF 21 帧，512×280，50 ms/帧，并逐帧成功解码 |
| 可重跑性 | 相同任务、源码、seed 与官方初态；21 组双路 RGB 帧哈希逐一一致，初态 qpos / 终态 qpos / qvel 最大绝对差均为 0 |
| 无窗口依赖 | 运行元数据中 `DISPLAY`、`WAYLAND_DISPLAY` 均为 null；实际上下文模块为 `robosuite.renderers.context.egl_context` |
| 清理 | 两次最终进程均正常退出，无首轮的 EGL 析构错误 |
| 失败路径 | 非法 LIBERO 路径退出 1，生成 `passed:false` 及 FileNotFoundError 日志；`--steps 0` 退出 2，初始化前拒绝 |
| 静态检查 | 两个 Python 脚本 AST 解析通过；启动器 `bash -n` 通过 |
| 环境保护 | 包版本清单、6 个受保护文件哈希及两个第三方 Git 工作区状态前后一致 |

复现比较见 [reproducibility.json](../outputs/smoke_test/reproducibility.json)。像素一致性仅在当前机器/依赖上验证，未测试跨 GPU 的逐像素一致。

首次尝试 `20261101T220014Z_bf80e2` 已完成渲染和步进，但在解释器退出时产生 EGL 资源析构报错；其图像与记录保留。新脚本显式按 MuJoCo → EGL → env 顺序释放资源，两次最终复测通过。本报告不把首次尝试视作最终干净退出的证据。

## 图像与动作约定

任务来自 `libero_object`，任务索引 0，语言为 `pick up the alphabet soup and place it in the basket`，采用官方 `.pruned_init` 的第 0 个初态。metadata 保存 BDDL 与初态文件路径及 SHA256。

两路是 LIBERO 原生相机，尚未替换成 D455 模型。深度关闭。当前 robosuite 返回 `opengl` 图像；导出时只做一次上下翻转，得到左上角原点的 RGB PNG。未混用 BGR，也没有做任何模型特定图像旋转。metadata 中的 K 来自仿真 fovy，`T_world_camera_initial` 是初始时刻相机位姿，使用 x 向右、y 向下、z 向前，单位米；不表示实机已标定或腕部外参在运动中固定不变。

实际控制器为 `OperationalSpaceController / OSC_POSE`，`use_delta=true`，7 维动作取值范围均为 [-1,1]：末端平移增量 3 维、旋转增量向量 3 维、夹爪 1 维。不是七个关节角。控制器的平移输出范围为 ±0.05 m，旋转为 ±0.5 rad；夹爪 -1 打开，+1 闭合。本轮动作始终为 `[0,0,0,0,0,0,-1]`，仅保持末端目标并打开夹爪，检查物理时间与图像输出；非零方向控制与抓取留在 P2 验收。

观测包含关节位置/速度、末端位置与 xyzw 四元数、夹爪状态、双 RGB、对象状态等 40 个键。终端打印完整键名、双路 shape/dtype 及控制器；metadata 保存每个键的 shape/dtype、相机参数、帧时间戳、机器人状态和动作空间。

脚本执行 `env.env.reset()` 一次后调用官方 `set_init_state()`：本机 LIBERO wrapper 的 `reset()` 使用 `finally: continue`，可能吞掉异常并无限重试；测试入口绕开这一问题以保留真实异常，不修改第三方文件。

## 结果文件

以第 2 次最终运行为例：

- [external.png](../outputs/smoke_test/20261101T220544Z_8742f3/external.png)、[wrist.png](../outputs/smoke_test/20261101T220544Z_8742f3/wrist.png)：两路最终帧。
- [side_by_side.png](../outputs/smoke_test/20261101T220544Z_8742f3/side_by_side.png)、[simulation.gif](../outputs/smoke_test/20261101T220544Z_8742f3/simulation.gif)：并排查看与短时间序列。
- [metadata.json](../outputs/smoke_test/20261101T220544Z_8742f3/metadata.json)、[run.log](../outputs/smoke_test/20261101T220544Z_8742f3/run.log)：任务、obs/action、版本、哈希、seed 与日志。另保存两路 `*_initial.png`。

原始结果位于 WSL 项目，同时复制到 Windows 工作区中的同名 `outputs/` 供查看。outputs 已加入忽略规则；不要把生成物或 venv 当作实验室迁移源码。

## 本轮的完成边界与下一步

**已验证：真实场景初始化、双路 RGB、短 step、导出与同机可复现性。未验证：自动完成任务、抓取/提升、接触采集、VLA 推理、实机。** GIF 中的短物理变化不构成抓取成功，也不能从任务文字推断机器人正在完成抓取。

本轮停在 P1。下一步在获准进入 P2 后，复用已有控制实现并做独立的机械臂动作与真实抓取/提升验收；继续不以下载 VLA 权重为前置条件。正式模型仍以 π₀.₅ 为研究方向，OFT 仅为可替换的工程适配器。
