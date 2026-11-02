# RoboLab Studio 0.2：模型接口与轻量测试报告

日期：2026-10-31，Europe/London。执行于用户本机 Windows、WSL2 Ubuntu 22.04、RTX 4060 Laptop 8 GB。

## 结论

本轮完成了权重只读扫描、可替换 VLA Adapter、Mock 观察/动作/物理接口、独立策略控制，以及默认任务的规则抓取自检。没有下载大模型、安装新包、启动微调或加载真实 VLA 张量。

实际扫描的 Hugging Face 缓存与项目目录中，文件齐全的 OpenVLA-OFT 权重候选数为 **0**。因此真实 VLA 的模型加载和推理状态仍未通过验证，不作为已完成功能报告。

## 本轮测试

| 检查 | 实际证据 | 结果 |
| --- | --- | --- |
| 权重扫描 | WSL / Windows 默认 HF hub、HF 环境变量路径、`/home/openvla/projects` 和已知项目模型目录 | 0 个完整候选；没有下载 |
| 文件状态与张量加载区分 | 不完整 checkpoint、文件齐全但内容为 fixture 的目录 | 不会把文件检查当作模型已加载 |
| 模型可替换 | 注册并创建替代 Adapter；GUI 使用后端 adapter catalog | 通过；π₀.₅ 模型本身未实现 |
| HTTP 协议 | 本地测试服务器握手；测试服务标记不能使 OFT 变成真实已加载状态 | 通过；不属于真实模型推理测试 |
| 动作验证 | 维度错误、NaN、布尔、超范围和空动作拒绝 | 通过 |
| 轻量检查不加载模型 | 独立子进程导入桥接服务模块后，`torch` 和 `tensorflow` 均不在模块表 | 通过 |
| 观察输入 | 从 MuJoCo 采集两路 224×224 RGB、8 维本体状态、7 个关节位置、任务文字和仿真时间 | 通过 |
| Mock 真实物理闭环 | 2 次观察请求、8 个有效动作，经过官方 LIBERO OSC；仿真前进 0.4 s，实际关节发生变化 | 通过；模型标志仍为 false |
| 物理仿真与策略分离 | Play 只推进物理；单次观察测试不推进时间；独立策略执行按钮驱动物理 | 通过 |
| 规则抓取 | 汤罐实际被夹爪抬起、移动和放入篮子；读取官方 LIBERO 成功判定 | `success=true`，明确标记非 VLA |
| 无模型时 UI 行为 | OFT 仅配置；真实推理/执行禁用；Mock 与规则结果不改变真实模型状态 | 通过 |
| 原有基础功能 | 三维视角、选取、关节/夹爪、编辑、物理属性、保存恢复、任务切换及错误处理回归 | 通过 |
| 退出资源释放 | 自动测试窗口关闭，所属后端进程退出；用户原先打开的窗口未被关闭 | 通过 |
| 环境完整性 | 原包清单、脚本、配置摘要一致；LIBERO 与 OpenVLA-OFT 仓库无修改 | 通过 |

## 自动测试记录

```bash
cd /home/openvla/projects/robolab-studio
bash scripts/test_engine.sh
```

`evidence/all-tests-vla.log`：**12 个测试通过，206.335 s，OK**。包含原有引擎/格式测试、7 个策略接口测试和一个真实观察/物理/规则抓取集成测试。Mock 和测试 checkpoint 不作为真实模型结果。

进一步收紧完整 checkpoint 文件检查，并将模型专用图像变换移出仿真器后，8 个策略单元测试再次通过，见 `evidence/policy-unit-tests-final.log`。新增测试验证 OpenVLA-OFT 图像变换与原始 OpenGL 辅助函数等价；最终真实观察/Mock/规则抓取集成测试再次通过，耗时 57.805 秒，见 `evidence/vla-pipeline-final.log`。

```bash
bash scripts/launch.sh --policy-test
```

`evidence/policy-gui-result.json` 记录实际可见桌面上的 4 组检查：物理与策略分离、Mock 输入/输出及物理闭环、无真实模型时的状态门控、真实物理规则抓取。最终通过 Windows `.cmd --policy-test` 入口复测，耗时 77.08 秒，exit code 0，全部通过，测试后端已退出。

## 运行证据

- `weight-scan.json`：扫描路径、候选和缺失情况。
- `vla-pipeline-result.json`：Mock 的请求/步数、非真实模型标记、规则抓取阶段和官方成功判定。
- `policy-agentview.jpg`、`policy-robot0_eye_in_hand.jpg`：实际采集的模型输入图像。
- `studio-mock-policy.png`：Mock 执行完成，真实模型状态仍为未加载。
- `studio-vla-not-loaded.png`：OpenVLA-OFT 仅配置，真实策略执行禁用。
- `studio-grasp-lift.png`：规则抓取中的实际抬起阶段。
- `studio-grasp-success.png`、`grasp-self-test-success.png`：放置完成与官方成功判定。
- `environment-after-vla.json`：原环境包/文件摘要、GPU 与仓库检查。

所有截图来自实际运行的 Qt/MuJoCo 程序。规则测试使用真实关节伺服、夹爪接触与物理步，没有通过直接写物体位姿来伪造抓取。

## 未完成与限制

- 当前没有完整权重和远程服务地址；没有执行真实 VLA 的加载、推理或抓取评测。桥接服务的原生 OFT 初始化路径必须在取得权重后进一步验证。
- 未验证 8 GB 显存下的 int4/int8 模型容量、性能和与渲染共存情况；没有自动下载量化依赖。
- Mock 只有确定性的小幅抬手动作，验证接口，不理解语言，也不完成抓取。规则抓取仅验证了默认 LIBERO-Object 汤罐任务及默认初始状态。
- 第一版策略执行要求官方 LIBERO 场景中没有额外编辑器刚体。更复杂场景、长期运行、失败恢复、其他任务和大批量种子未验证。
- π₀.₅ / openpi、G³VLA 几何模块和 Gripper-Centric Contact 模型仍是后续研究工作。当前实现的是它们可替换接入的合同、注册表及观察数据边界。
- 模型目录/地址不会自动转成已经加载的模型；本轮没有进行静默模型下载、自动训练或自动启动推理服务器。
