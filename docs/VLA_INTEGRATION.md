# VLA 接入与轻量测试

本轮目标是将物理仿真、动作来源和模型状态分开。OpenVLA-OFT 是软件开发阶段的首个 VLA Adapter，不代表最终毕业设计模型。后续以 π₀.₅ / openpi 为基础，参考 G³VLA 多相机几何并增加 Gripper-Centric Contact 预测。

## 当前实际状态

- 已检查 WSL / Windows 默认 Hugging Face 缓存、WSL 指定的 HF 环境变量路径及 `/home/openvla/projects`。在这些路径中没有发现文件齐全的 OpenVLA-OFT 权重候选。证据：`evidence/weight-scan.json`。
- 没有下载模型、安装新依赖、启动微调或在 GPU 上加载 VLA。
- Mock 已通过真实相机输入、动作输出与官方 LIBERO OSC 控制器的物理集成测试。
- 默认汤罐任务的规则抓取自检已通过 LIBERO 成功判定；它使用仿真真值与 IK，不是 VLA 结果。
- 真实 VLA 模型加载和真实模型推理尚未验证，因为没有可用权重或服务地址。相关按钮不会将配置或 Mock 标为真实模型成功。

## 使用新界面

更新代码后关闭旧窗口，再通过 Windows 启动入口重新打开。打开右侧“策略 / 自检”页：

1. 点击“扫描本地权重（只读）”查看当前文件检查结果。
2. 保持 `Mock Policy`，点击“配置 / 检查模型目录”。此时状态为 **Mock 已就绪（非模型）**。
3. 点击“单次观察 → 动作测试”：采集两路真实仿真 RGB、8 维本体状态和任务文字，返回 Mock 动作；此操作不推进物理时间。
4. 点击“执行已配置策略”：Mock 动作经过官方 LIBERO 的 OSC 控制器推进物理仿真，显示步数、进度及动作来源。默认最多 20 步。Mock 的小幅抬手动作仅验证接口，不尝试完成抓取。
5. 使用“暂停”冻结物理/策略执行，“启动物理仿真”继续当前执行；“结束策略”停止动作来源并保留当前姿态。“停止/重置”则恢复编辑基准。
6. 如需看到完整抓取，在默认 LIBERO-Object 第一个任务上点击“运行规则抓取自检（非 VLA）”。约 22 秒仿真时间内完成接近、对齐、夹紧、抬起、移至篮子、松开和检查。其他任务尚未实现规则控制。

工具栏的“启动物理仿真”只推进时间、碰撞和已有关节伺服目标；它不会根据任务文字自动生成动作。

## 模型状态

| 状态 | 条件 | 不代表什么 |
| --- | --- | --- |
| 仅配置 | 选定适配器或配置了地址/目录 | 不代表权重存在 |
| 权重文件已找到 | 配置、tokenizer、模型分片、OFT action head、本体状态 projector 等文件检查齐全 | 不代表张量内容有效，不代表已占用 GPU |
| 模型已加载 | 独立真实推理服务完成初始化，并报告对应模型已加载 | 不代表实际推理调用成功 |
| 真实推理通过 | 真正模型的推理请求返回有限且符合动作空间的动作 | 不代表机器人任务成功 |
| Mock 已就绪 | 无模型的测试适配器可工作 | 不会将 `model_loaded` 或 `real_inference_verified` 设为真 |

另有独立的 **任务执行结果**：LIBERO 成功判定与模型状态分开。规则抓取可以成功，但模型状态仍保持未加载。Mock 完成只表示接口执行到达步数上限。

## 架构与替换模型

`robolab/policy.py` 仅依赖标准库，不导入 MuJoCo、Qt、PyTorch 或 TensorFlow。`VlaAdapter` 包含 `status / load / predict / close`，通过注册表创建。

- `MockPolicy`：确定性、小幅、有限的 LIBERO OSC 动作，无模型权重。
- `OpenVLAOFTAdapter`：只读检查本地 checkpoint；通过独立 HTTP 推理服务加载/推理。不会隐式下载或加载模型。
- `HttpPolicy`：RoboLab 桥接协议客户端；没有假设官方 OpenVLA 或 openpi 已提供这个 HTTP API。
- `task_control.LiberoRuntime`：负责将规范动作送入官方 LIBERO OSC，并同步真实模拟状态；模型适配器不直接写机器人或物体位姿。
- `task_control.PolicyExecution`：按 20 Hz 消费动作块；网络推理在独立线程中执行，MuJoCo、相机和 GL 仍由后端主线程访问。等待推理时不虚构动作或推进物理时间。结束后丢弃未执行动作。

后续 π₀.₅ / openpi 适配器可将统一观察映射为 openpi 所需的键名和格式，将其输出映射为同一动作合同。G³VLA 几何模块和 Gripper-Centric Contact 可作为适配器内的模型组件或包装器；仿真器和 GUI 不需要依赖这些模型的内部实现。

注册新适配器：

```python
from robolab.policy import register_vla_adapter
register_vla_adapter('pi05', MyOpenpiAdapter)
```

安装型插件可以提供 `robolab.vla_adapters` entry point，映射名称到 factory。GUI 从后端的 adapter catalog 读取选项。factory 接受通用 `checkpoint`、`endpoint` 参数，模型专用转换封装在适配器内。不要向原有环境自动安装插件；未来也可以通过独立服务封装模型运行环境。

## 观察和动作合同

观察版本 `robolab.observation.v1` 包含：

- `instruction`、`sim_time`、`observation_id`。
- 两路 224×224 RGB/JPEG：`agentview`、`robot0_eye_in_hand`。相机来自真实 MuJoCo 场景，使用方形渲染视口，统一为图像左上角原点的标准 RGB。模型训练专用的翻转/裁剪由模型适配器负责，仿真器不包含 OpenVLA 专用图像变换。
- `proprio`：末端世界位置 3、旋转轴角 3、两指关节位置 2，共 8 维；另附 7 个实际关节位置。
- `calibration`：两相机的仿真内参 K、`T_world_camera`、图像旋转和轴方向约定；腕部外参每次观察更新。这是后续几何模块的输入结构，尚未完成 G³VLA 几何训练或精度评测。

动作合同为 `libero_osc_7d`：归一化的末端平移增量 3、旋转增量 3、夹爪指令 1。它不是七个关节角。OpenVLA-OFT 桥接器调用官方 `process_action` 处理夹爪约定，动作会经范围及有限值检查再交给 LIBERO。

第一版策略执行要求使用未添加额外编辑器刚体的 LIBERO 场景。编辑器自由视角不作为模型训练视角。多模型动作空间转换由适配器负责，不能直接把别的机器人或基座模型的动作套用到 Panda。

OFT 桥接器按本机 robosuite 1.4.1 的 `opengl` 原始图像与 OFT 辅助函数的 180 度旋转组合处理输入。MuJoCo `Renderer` 已经做了上下翻转，因此从规范图像到 OFT 的等价变换是水平翻转；其等价性有独立像素数组测试。规范观察和相机 K/T 保持不变，后续 openpi / 几何模块可以采用自己的图像转换。

## 有权重或远程设备后的真实接入

选择 `OpenVLA-OFT`，填写本地完整目录可先做文件检查。缺少文件只会显示未就绪，不会补下载。

提供了可选的 OpenVLA-OFT 桥接服务：

```bash
cd /home/openvla/projects/robolab-studio
# 仅检查文件，不导入 torch/tensorflow，不加载权重：
/home/openvla/venvs/openvla-oft/bin/python -m robolab.openvla_service \
  --checkpoint /path/to/local/complete-checkpoint --check

# 仅在有权重、显存及依赖的设备上，明确执行此命令才会加载模型：
PYTHONPATH="$PWD:/home/openvla/projects/openvla-oft:/home/openvla/projects/LIBERO" \
/path/to/model-environment/bin/python -m robolab.openvla_service \
  --checkpoint /path/to/local/complete-checkpoint --serve --precision int4
```

第二条命令本轮**没有执行**。量化不保证能在 8 GB 显存上与仿真同时运行，运行环境还需具备相应量化依赖。优先可在更大显存设备上启动服务，通过隧道访问其 localhost 端口。GUI 填写桥接地址，点击“连接真实推理服务”，再进行单次观察/推理测试。

服务只接受本地已有 checkpoint，不下载；OFT 原有 helper 会同步配置文件，因此桥接器把小型元数据复制到项目 `model-cache/`，大权重只做只读链接，避免改写原 checkpoint 或 OpenVLA-OFT 源码。服务器必须成功初始化真实模型后才提供 `model_loaded=true`。

桥接协议 v1：`GET /health`，`POST /predict`。真实模型响应必须明确 `real_inference=true`、`is_mock=false`，且动作空间正确。带 `test_backend=true` 的服务不能被 OFT Adapter 接纳为真实模型。

## 测试与边界

```bash
bash scripts/test_engine.sh
bash scripts/launch.sh --policy-test
bash scripts/launch.sh --doctor
```

证据保存在 `evidence/`。`vla-pipeline-result.json` 明确记录 `real_vla_tested=false`、`weights_downloaded=false`。真实 VLA 的张量加载、输出质量、真实抓取表现以及本机 8 GB 的量化适配仍未验证。
