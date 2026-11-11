# P4 C 批 OSC 动作闭环与记录

2026-11-10。实际运行位于 WSL 的 `/home/openvla/projects/robolab-studio`。

## 使用

```bash
cd /home/openvla/projects/robolab-studio
bash scripts/launch.sh --p4c-osc --output-dir outputs/p4c_check
```

使用新目录。命令顺次录制并复执行盒体粗端、盒体细端、带偏航的椭球细端和独立阻挡对照。桌面在「P4 部位抓取 → P4 试采汇总」打开生成的 `result.json`，可核对原任务结果、复执行次数、qpos 最大差和每条记录目录。旧 P4-B 的 25 条试采汇总仍可打开。

## 执行与记录合同

在合成模型上装配原版 robosuite 1.4.1 Panda `SingleArm` 和 `OSC_POSE`，使用实际 `SingleArm.control` 及 MuJoCo 原生子步。控制调用与 LIBERO 使用的 robosuite 环境循环一致。这里只使用自定义部位和提升判定，LIBERO 官方任务奖励未用于合成场景。几何规则教师由部位中心计算目标，现有 P4-A/B 的关节 PD 仍使用同一套路径规划。 [robosuite 1.4.1 SingleArm](https://github.com/ARISE-Initiative/robosuite/blob/v1.4.1/robosuite/robots/single_arm.py) · [环境子步循环](https://github.com/ARISE-Initiative/robosuite/blob/v1.4.1/robosuite/environments/base.py)

执行输入为 `[dx, dy, dz, rx, ry, rz, gripper]`，各维归一化到 [-1, 1]。本机控制器平移每轴缩放为 0.05 m，旋转向量每轴缩放为 0.5 rad，相对旋转左乘到当前末端朝向。Panda 夹爪 -1 张开、+1 闭合。脚本发送的值完整写盘，旧 P4-B 的观测增量保留原有语义。

- 每个 50 ms 周期接收一个动作并推进 25 个 2 ms 子步。记录同一时刻的双 RGB、原始接触、OSC 实际目标和已施加的归一化动作。RGB 和接触关联到周期首个求解器输入状态，原始力取首子步求解结果。另记整个周期的结束时刻和 qpos/qvel。
- `manifest.json` 使用 `robolab.p4_osc_grasp.v1`，保存实际控制器配置、初始状态、命令顺序、版本和文件哈希。 `actions_and_contacts.jsonl` 写入每个周期的命令、原始首子步接触和末状态。 `frames.jsonl` 关联同一时刻的图像、动作、标定、部位、障碍标签，`rgb/` 保存两路 224×224 PNG。
- 每条重新载入相同模型、初态和控制器，用文件里的动作依次驱动物理。检查每个周期结束状态、原生接触与实际控制器目标，以及动作、图像、标签的索引、时间和哈希。采样率为 20 Hz，短于采样间隔的接触可能漏检。

## 实测

| 场景 | 指定部位 | 夹持部位 | 最大提升 | 通过 |
| --- | --- | --- | ---: | --- |
| 同一盒体和初态 | 粗端 | 粗端 | 16.47 cm | 是 |
| 同一盒体和初态 | 细端 | 细端 | 16.38 cm | 是 |
| 双椭球，偏航 8° | 细端 | 细端 | 16.29 cm | 是 |
| 阻挡对照 | 细端 | 无 | 0.31 cm | 否 |

三个成功案例均达到指定部位持续提升 2.60 秒。阻挡对照记录到 35 帧手指障碍接触，无有效抬起。四条各 222 个动作、222 对同步 RGB，共 888 个动作和 1,776 张 PNG。复执行的逐周期 qpos、qvel、实际控制目标及原始接触样本相同，最大 qpos 差 0。形状错误、非有限值、布尔值和越界指令均被拒绝，时间及原生状态未变化。完整验收约 111 秒，记录约 97 MiB。

首轮独立双精度旋转检查使用 1e-8 阈值，超过阈值的最大矩阵元素差约 1.075e-8。核对 robosuite `quat2mat` 将输入转换为 float32 后，将独立检查的容差设为 1e-7。复执行的逐周期数据比较仍采用精确相等。首轮日志和记录保留在 `outputs/p4c_osc/`。

旧 P4-A 的细端入口实跑通过，新旧接触轨迹和帧清单逐字节一致。另重读 1 条 P4-A V1 和 3 条 P4-B V2（包括原阻挡失败），3 项标签合同及 7 项本地化测试通过。真实 WSLg 验收中英文 OSC 汇总、阻挡条目、原 25 条汇总及后端正常退出。

最终证据 `outputs/p4c_osc_final/`、`outputs/p4c_osc_gui/`、`outputs/p4c_p4a_regression/compatibility.json`。

## 下一步

先把同一 OSC 合同扩到已有场景划分，检查整批物理失败和数据分布，再生成第一版 200 条和模型数据适配。M2 仍待扩量和正式验收。本轮是几何规则教师的仿真采集，无真实 VLA 推理、训练或硬件接入。依赖未安装或升级，改动在 WSL 本地工作树，未提交或推送。
