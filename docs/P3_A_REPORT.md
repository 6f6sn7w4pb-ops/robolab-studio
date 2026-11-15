# P3 A 批 原生接触采样与夹爪坐标转换

日期：2026-11-04，Europe/London。实际开发与运行：`/home/openvla/projects/robolab-studio`。基于已有未提交的 P2 第二批工作树继续开发；Git HEAD 仍为 `3f86aa013956b8a4724d0d6d02225a842f09553d`。

## 完成范围

已完成原生 Euler PD 路径的真实求解器接触采样、左右指垫身份、点／法向／力的夹爪坐标转换，以及 CLI 实际抓取验收。采样为显式启用，默认关闭；它读取物理数据，不改写控制命令或物体位姿。

本批提供后端 API、IPC 和 CLI 验证入口。正式 P3 sidecar 写入／兼容属于 B 批，接触 GUI 点和箭头及历史标签查看属于 C 批，均尚未实现。诊断图是验证工具输出，不代表接触 GUI 已完成。

规则策略继续使用原有流程，没有真实 VLA 推理、模型下载、训练或硬件接入。

## 使用方法

在既有 WSL 项目中：

```bash
cd /home/openvla/projects/robolab-studio
bash scripts/launch.sh --p3a-test
```

该命令自动使用进程局部 EGL，运行原生采样／坐标检查、关闭和开启采样的真实抓取、物理失败对照、Mock 路径边界与编辑失效检查，然后退出。不会自动安装环境或模型。

```bash
# 仅能力、身份映射、空接触和单步检查，不作完整抓取验收声明
bash scripts/launch.sh --p3a-test --quick
# 指定独立证据目录
bash scripts/launch.sh --p3a-test --output-dir outputs/p3a_another_check
# 可选：使用已有 matplotlib 从真实样本绘制指垫投影
/home/openvla/venvs/openvla-oft/bin/python scripts/plot_p3_contacts.py
```

在已有 `Engine` 对象上可调用：

```python
engine.configure_contacts(enabled=True)
engine.step()  # 一个原生控制周期；不设置自动任务来源
status = engine.contact_sampling_status()
sample = engine.contact_sample()  # 独立副本，可能为 None
engine.configure_contacts(enabled=False)
```

策略执行中，包括暂停的规则任务，不能更改采样配置。应在任务开始前配置，结束后关闭。普通物理暂停不会增加样本，20 ms 单步增加一个尾子步样本。

IPC 新增 `contact_configure` 和 `contact_sample`。前者接受 enabled、target_body、force_epsilon_N；后者返回 status 和 sample。常规 `state` 增加轻量 contact_sampling 状态，不把完整接触帧塞入每条视口消息。新增配置错误文案有中英文模板。

## 采样和数据语义

参考系使用当前 `gripper0_grip_site`。两个模拟指垫为 `gripper0_finger1_pad_collision` 和 `gripper0_finger2_pad_collision`；其余手指几何体分开标记。分类支持手指—目标、环境、动态干扰物和机器人其他部分；本轮默认真实抓取中实际出现的是两指的 pad—target 接触。环境／干扰物身份映射已用当前实际模型 ID 检查，但主动碰桌或干扰物的完整 Panda 动作场景仍待后续扩展。

采样器要求已验证的 MuJoCo 2.3.7、Euler、当前无激活动力学／mocap 的 Panda 配置及无额外控制回调。缺少参考点／指垫、未知积分器或回调、禁用接触求解等情况明确拒绝或报告不支持，不自动修改环境和积分器。

每个 20 ms 控制周期保留最后一个 2 ms 子步的求解器样本：先复制该子步的输入状态，再按原顺序步进，随后立即读取接触与力。第一帧源时刻为 `0.018 s`，积分后时刻为 `0.020 s`。首版正常 22 s episode 产生 1100 个 P3 样本，对应 1101 行 P2 中除初始行外的记录。

内存/API 样本 schema 为 `robolab.p3_contact_sample.v1`，它是采样结果，尚未冻结为 B 批持久化帧合同。包含：

- sample_index、原始采样来源、求解器输入／积分后时刻、dt，及存在时的 P2 episode／轨迹行关联；
- 求解器输入 qpos/qvel/ctrl、T_world_gripper、参考点和 profile SHA256；
- 双相机对应 224×224 的内外参，明确 calibration_only，`original_rgb_recorded=false`；
- 每点几何 ID／名称、手指和对象类别、表面区域、世界／夹爪坐标位置及法向；
- 原始接触 frame 和六维 wrench、转换后作用在另一接触体上的力、法向力、距离、约束激活和承载标记。

接触位置明确是求解器中点，不是高精度表面测量。点转换包含平移，法向和力只旋转。法向统一从手指指向另一接触体；力统一为作用在另一接触体上的力，处理 geom1/geom2 交换。原始力矩保留但没有声称转换成夹爪原点力矩。

约束激活与承载独立于几何距离，保留 margin/gap 内的非激活候选。默认 `1e-6 N` 只是数值零区分阈值，不是抓取成功标准。原 P2 判定不变。

状态区分 disabled、not_sampled、available、stale、unsupported、error。已采样无接触使用空列表；未采样或失效返回 sample=None。编辑姿态、重置、切换模型后清除旧样本，重建模型后需重新配置 profile；调用者修改返回副本不影响内部状态。

Mock/OFT 在另一套执行环境中推进物理，复制回来后再 forward。当前对此返回 `policy_runtime_not_supported`，不把重算力当作原始执行力。原 Mock 动作链路继续工作，回到原生步进后可以重新采样。

## 实际验收

| 检查 | 结果 |
| --- | --- |
| 数值和真实小场景单测 | 7 项通过：平移／旋转／随机刚体往返、法向长度、接触轴和正负号、非法矩阵／非有限数、两种 geom 顺序的 1 kg 承重、非激活 margin 候选 |
| 既有轻量回归与本地化 | 原有 34 项通过；与新增 7 项合计 41 项，日志在 unit-tests.log |
| 默认规则抓取，采样关闭 | 22 s，1101 行 P2，接触／提升／官方任务和完整 P2 均通过 |
| 相同初态，采样开启 | 同样通过；1100 个原始 P3 样本，首／末源时刻约 0.018／21.998 s |
| 非侵入性 | 两次逐帧关节目标、伺服目标、原生执行器命令和实测关节位置完全一致；最终 qpos 最大差为 0；P2 所有结果及指标一致 |
| 两指指垫 | 两侧均有目标承载接触；实际原始接触点／力与实时 MuJoCo 数据逐项核对通过 |
| 坐标及时序 | 独立 data 在采样输入状态重建后，参考点和旋转与记录一致；重建未改变实时 qpos／ctrl／warm start |
| 指垫空间检查 | 求解器中点相对指垫碰撞盒的最大超出量约 6.60e-7 m；它是数值几何检查值，不代表真实定位精度 |
| 实际失败对照 | 仅验证进程禁用接近 IK，真实抓取失败；550 个 P3 样本、551 行 P2，无承载目标接触，未误报成功 |
| Mock 回归 | 2 步 Mock 经官方 OSC 执行完成，真实推理标志保持假；P3 正确提示原始来源不支持 |
| 真实桌面 smoke | 加载任务、真实 RGB、运行／暂停／重置和窗口关闭通过；所属后端正常退出 |

单个接触点的法向力峰值为 finger_1 约 20.812 N、finger_2 约 8.874 N。这些是模拟接触求解值，不是手指总夹持力或真机测量值。

本机单次观察中，20 ms 控制步处理的中位耗时由关闭采样时约 3.819 ms 变为开启时约 4.540 ms，P95 由约 5.707 ms 变为 7.988 ms。包含当前本地记录和控制开销，不作为其他机器或所有任务的实时保证。完整 CLI 验证约 80.554 s；桌面 smoke 约 57 s。

环境前后版本摘要、受保护文件、可编辑安装元数据和 LIBERO/OFT 工作树须以 `environment-comparison.json` 核对；pip check 与核心导入通过。OFT 离线导入仍返回既有 protobuf 错误，未修复，也未阻断本批无模型验收。

## 证据与修改文件

本机完整证据：`outputs/p3a_verification/`。

- result.json：正常对照、失败对照、时序、分类、耗时和源码哈希。
- native_contact_samples.jsonl：1100 个真实验证样本，约 11.17 MB，SHA256 为 `23f2302b3039f585460b2f5531b56400123beeb8575dac130efc2b55d3ef1a21`。它是 CLI 验证轨迹，不是正式 P3 sidecar 或已验收训练集。
- profile.json、plotted-contact-sample.json：几何 profile 和诊断图对应的实际样本。
- panda-pad-contact-lift.png：真实仿真画面中的指垫、接触点和法向；机械臂遮挡部分标记。
- panda-pad-contact-projections.png：同一真实采样状态的夹爪 XY／YZ 投影。盒体位置只在独立状态中重建，接触点和法向读取原始记录，没有用重算力替代。
- unit-tests.log/json、环境前后检查：可复查的轻量测试和环境保护证据。

新增 `contact_geometry.py`、`p3_contact.py`、两个单测文件、`verify_p3_contacts.py` 和可选绘图脚本；在 engine 中增加只读钩子与生命周期管理，worker 加 API，更新 locale、启动器和说明。原 `adapters.py` 控制器、`task_control.py` 规则流程和 `p2_recording.py` 判定／持久化代码保持本轮开始时内容。

## 尚未完成及下一批

当前只验收默认汤罐任务和 Panda 配置；没有任意对象／任务泛化结论，50 Hz 尾子步采样可能漏掉更短接触。更细指腹面片、接触表面投影、完整环境碰撞动作和其他积分器尚未验收。

下一批 B：正式 P3 sidecar 的原子结束、哈希、父轨迹关联，以及正常／失败／取消／中断／损坏和旧记录兼容。随后 C 批接入接触 GUI、历史标签查看和少量其他官方初态回归。本轮没有自动进入模型训练或 P4 数据集建设。

所有新增实现和报告保存在实际 WSL 项目；Windows 旧源码树未同步，当前修改未提交或推送 GitHub。已有未提交改动保留。
