# 实验硬件记录

更新日期：2026-11-01，Europe/London。来源：用户在本项目对话中提供并确认的信息。

本文记录实验配置与待确认参数；不是已加载的驱动配置，不表示实机已经连接或测试。当前软件继续使用已有 LIBERO Panda 仿真，不替换其工作环境。

## 已确认的设备与用途

| 组件 | 配置 | 确认状态 |
| --- | --- | --- |
| 机械臂 | Franka Emika Robot（FER，原 Panda），实验室设备；模型标识 `fer` | 用户已明确确认型号为 `fer` |
| 模型来源 | https://github.com/frankarobotics/franka_description/ | 用户提供；具体版本/commit 尚未固定 |
| 腕部相机 | Intel RealSense D455，安装于机械臂腕部 | 用户已确认 |
| 外部相机 | Intel RealSense D455，固定于桌面/工作区外部位置 | 用户已确认 |
| 相机使用范围 | 两路 RGB；不采集深度用于本项目的实机观察输入 | 用户明确要求 |
| 夹爪 | 预计为原装 Franka Hand | 用户表示“应该也是”，仍需在实验室核对 |
| 笔记本用途 | 软件、仿真、Mock 和轻量接口测试 | 不要求在笔记本加载完整 VLA 权重 |
| 实验室用途 | 真实模型推理/训练/评测与硬件实验 | 具体计算资源与驱动栈待核对 |

## 软件中的观察角色

| 观察角色 | 实机对应 | 当前仿真对应 |
| --- | --- | --- |
| 外部 RGB | 桌面 D455 的彩色光学相机 | `agentview` |
| 腕部 RGB | 腕部 D455 的彩色光学相机 | `robot0_eye_in_hand` |

两台设备分别按序列号绑定，不依赖设备枚举顺序。分辨率、帧率、序列号、曝光设置和 SDK/固件版本均尚未确认；应在实验室枚举实际支持的 RGB profile 后记录。

RealSense 采集时应显式选择 color 流，检查解析出的实际流列表，确认没有隐式启用 depth/IR。不要使用“默认启动所有流”的方式代替 RGB-only 配置。本轮没有安装 SDK、启动相机或修改运行代码。

## 几何与标定

只使用 RGB 仍需要每个 RGB 相机的内参和畸变参数，以及相机相对于机械臂的外参。标定以实际彩色光学坐标系为准，不能用深度/红外相机参数代替。

约定 `T_A_B` 把 B 坐标系中的点变换到 A 坐标系：

- B：机械臂基座坐标系。
- E：选定的机器人末端参考坐标系；具体 link/frame 名随实验室 URDF 和驱动核对。
- Cw：腕部 D455 的 RGB 光学坐标系。
- Ct：桌面 D455 的 RGB 光学坐标系。
- G：研究使用的夹爪坐标系；其与机器人末端的关系需明确。

需要记录：

```text
桌面相机：T_B_Ct，安装固定后保持不变。
腕部相机：T_B_Cw(t) = T_B_E(t) @ T_E_Cw。
夹爪坐标：T_B_G(t) = T_B_E(t) @ T_E_G。
```

`T_E_Cw` 通过实际安装后的手眼标定获得；`T_E_G` 由核对后的夹爪模型/TCP 定义获得。腕部相机位姿随关节状态更新。两路 RGB 与机器人状态应记录时间戳并验证时间对齐，不能假定已实现硬件同步。

图像缩放、裁剪、翻转等模型预处理，应与对应的相机几何变换一致。规范观察与模型内部转换保持独立，后续 π₀.₅、G³VLA 几何模块及 Gripper-Centric Contact 通过模型适配器接入。

## Panda 模型与实机对接范围

用户已明确确认实验室机械臂型号为 **FER**。后续模型生成和实机适配以 `robot_model=fer` 为选择依据；具体仓库版本/commit、驱动版本与关节/TCP 参数仍需与实验室配置核对。机械臂型号的确认不等于夹爪型号已确认。

当前官方 `franka_description` 将原 Panda 标识为 `fer`，并保留其生成能力，但 README 标明不再对该型号提供维护支持。后续按实际 Panda/FER 型号选择并固定对应版本，不将 FR3/FP3 模型作为 Panda 的替代品。

URDF/mesh 提供模型、运动学及碰撞描述；实机控制仍需匹配实验室已有的驱动、控制器版本与接口。当前 MuJoCo 中的关节 PD 适配器不作为实机驱动使用。

夹爪尚需确认：是否原装 Franka Hand、指尖有无改装、开合范围、TCP/研究坐标系，以及可读取的状态。接触标注可先在仿真中生成；实机接触与指定部位命中的验证方法另行设计，不将 RGB 相机视为直接的接触传感器。

## 官方资料

- Franka 模型说明：https://github.com/frankarobotics/franka_description
- RealSense SDK：https://github.com/realsenseai/librealsense
- 设备和流配置 API：https://realsenseai.github.io/librealsense/python_docs/_generated/pyrealsense2.config.html
- Intel D455 规格：https://www.intel.com/content/www/us/en/products/sku/205847/intel-realsense-depth-camera-d455/specifications.html

本轮仅更新硬件记录，没有修改仿真代码、模型权重或已运行的 Python/渲染环境；没有进行真实相机或机械臂测试。
