# P4 OSC 数据集格式（首版 v1）

本文说明 `scripts/build_p4_dataset.py` 生成的原始数据集，以及 `scripts/export_p4_dataset.py` 导出的训练视图。代码中的常量是格式的唯一来源；本文与代码不一致时，以代码为准，并应修正本文。

所有示范都来自读取仿真真值的几何规则教师，不是 VLA 输出。成功率只说明采集流程在这些场景中可用，不能当作模型性能。

## 1. 目录结构

```text
<dataset>/
├── plan.json                 # 场景计划、采样范围、禁区参数、计划审计、plan_sha256
├── result.json               # 索引：每条记录、划分统计、泄漏审计、复现复核、验收门槛
├── episodes/<case_id>/       # 原始记录，即数据来源
│   ├── manifest.json         # robolab.p4_osc_grasp.v1：控制器配置、初态、指令、结果、文件哈希
│   ├── model.xml             # 该场景的完整 MJCF（资产仍引用本机 LIBERO 路径）
│   ├── actions_and_contacts.jsonl   # 每个 50 ms 周期：实际发送的动作、OSC 目标、原始接触、周期末状态
│   ├── frames.jsonl          # 每个周期：双相机 PNG、K/T、指定部位中心、最近障碍表面点
│   ├── rgb/                  # 每周期两张 224×224 PNG，无叠加
│   ├── projection-preview.png       # 检查用投影图，非训练数据
│   └── verification.json     # 采集后立即做的文件校验和精确复执行结果
├── recheck/<case_id>/        # 抽样重新采集，用于逐字节复现比对
├── discarded/                # 中断或出错后被替换的旧尝试（保留，不删除）
├── logs/                     # 各采集进程的日志
└── export/                   # 训练视图（见第 4 节）
```

`case_id = <scene_id>_<part>`，`scene_id = <形状族>-<划分>-<序号>`。同一 `scene_id` 的粗端和细端两条记录共用同一个 XML 和初态。

## 2. 场景计划与划分

- 随机数：每个字段由 `SHA256(seed | scene_key | field | attempt)` 派生，不依赖 NumPy 的随机数流。换机器、换 NumPy 版本，只要代码和 seed 相同，计划就相同（`plan_sha256` 相同）。默认 seed 为 `20261111`。
- 形状族：`box_step`、`cylinder_step` 为已见形状族；`ellipsoid_step` 整族留作未见测试。
- 划分（场景数 × 2 条指令）：

| 划分 | 形状族 | 场景 | 记录 | 用途 |
| --- | --- | ---: | ---: | --- |
| `train` | 长方体、圆柱 | 60 | 120 | 训练 |
| `validation` | 长方体、圆柱 | 8 | 16 | 模型选择 |
| `test_instance` | 长方体、圆柱 | 12 | 24 | 已见形状族的新实例 |
| `test_unseen` | 椭球 | 20 | 40 | 未见形状族 |
| `control` | 三族均有 | 4 | 4 | 阻挡对照，预期失败，不进入任何划分 |

- 指令：同一场景的粗端、细端两条指令使用同一句式模板，只有部位词不同。训练和验证只用已见说法（每个部位 3 种，中英文成对）。两个测试划分各有一半场景使用留出说法（每个部位 2 种），构成“形状族已见/未见 × 说法已见/未见”的 2×2 设计。
- 物体朝向：偏航角在 0–360° 内均匀采样。训练集中，每个部位所在的方向都覆盖全部四个 90° 扇区，避免模型用“画面左侧 = 粗端”这类位置捷径代替对形状的理解。Panda 两指对称，教师选择与默认姿态最接近的 180° 等价朝向。
- 障碍盒：放在离物体 0.10–0.22 m 处，并且必须避开两个禁区：杆体外扩 3 cm 的区域，以及两个部位中心处沿夹爪开合方向 ±12.5 cm、沿杆轴 ±4.5 cm 的手指扫掠区。
- 静止稳定性：椭球段平放时，只有竖直半轴比横向半轴短，才处在稳定平衡。所以椭球族的两段都要求高/宽 ≤ 0.92（宽度范围相应调整，见 `ELLIPSOID_RANGES`）。长方体底面是平的，一定稳定；圆柱横放时可以随遇滚动，实测中没有发生自发滚动。第一次采集没有这条约束，2 个高宽比大于 1 的椭球场景在夹爪碰到之前就自己翻倒了，那批数据已作废，见 [P4 D 批报告](P4_D_REPORT.md)。
- 审计（`audit_plan`）：拒绝以下情况——未见形状族出现在其他划分；验证集或已见族测试集里出现训练集没有的形状族；两个场景共用同一个形状实例或同一个场景；留出说法出现在训练或验证集；同一句指令对应两个部位；对照组混进任何划分。采集后再核对：每对记录的 XML 和初态一致，不同场景的 XML 互不相同。

## 3. 原始记录的时间与动作约定

- 控制：原版 robosuite 1.4.1 Panda `SingleArm` + `OSC_POSE`，20 Hz。每个周期发送一次动作，推进 25 个 2 ms 子步。
- 动作：`[dx, dy, dz, rx, ry, rz, gripper]`，每维在 [-1, 1]。平移每轴缩放为 0.05 m，旋转向量每轴缩放为 0.5 rad，旋转左乘到当前末端朝向；夹爪 -1 张开、+1 闭合。超出范围的分量由教师截断到 ±1，数据里存的就是实际发送的值。
- 时间对齐：第 i 个周期的图像、接触和本体状态都取自该周期第一个子步的求解器输入状态（t = 0.05·i s），动作 i 在这一时刻发出。因此 (观测 i, 动作 i) 的对应关系和策略执行时一致。
- 结果判定：两个指垫都只和指定部位有承载接触（单点法向力 ≥ 0.2 N）且持续 0.2 s；物体抬高 ≥ 0.10 m 并保持 ≥ 1 s；手指没有碰到环境或障碍。数据集另加两项，都从静置阶段结束时的位姿算起（指标版本 v3）：
  - 障碍盒位移必须小于 2 mm。开场落地的 1.108 mm 单独记录，不算推动。
  - 从静置结束到夹爪开始闭合，物体转动必须小于 1°、位移必须小于 1 mm。这一项能排除物体自己滚翻的情况，也能发现手臂其他连杆撞到物体（接触采样只记录手指）。不满足时，失败原因记为 `object_moved_before_grasp`。
- 复执行：每条记录都会在新的控制器上按文件里的动作重新执行一遍，逐周期比较 qpos、qvel、控制器目标和原始接触，要求完全相同。

## 4. 训练视图（HDF5）

每个划分一个文件 `export/p4_osc_<split>.hdf5`，只包含结果符合预期的示范。布局沿用 LIBERO / robomimic：

```text
/ (attrs) schema=robolab.p4_training_view.v1, split, source_index_sha256, plan_sha256,
          control_frequency_hz=20, image_convention=canonical_top_left_rgb,
          libero_rotated_180_equivalent="image[:, ::-1]", state_names, action_names,
          part_ids, contact_role_ids, contact_region_ids, nearest_surface_ids, label_force_threshold_N
/data (attrs: total, num_demos)
/data/demo_<k> (attrs: num_samples, case_id, scene_id, family, split, requested_part, requested_part_id,
                phrasing, instruction_en, instruction_zh, source_path, source_manifest_sha256,
                model_xml_sha256, outcome)
    obs/agentview_rgb        (T,224,224,3) uint8   gzip
    obs/eye_in_hand_rgb      (T,224,224,3) uint8   gzip
    obs/state                (T,8)  float64        见下
    obs/joint_states         (T,7)  float64        robot0_joint1..7 (rad)
    actions                  (T,7)  float64        实际发送的归一化 OSC 动作
    states                   (T,nq+nv)             求解器输入时刻的完整 qpos、qvel
    time_s                   (T,)                  动作发出时刻
    dones                    (T,)   uint8          最后一步为 1
    labels/T_world_gripper   (T,4,4)               夹爪参考系（gripper0_grip_site）
    labels/finger_contact/present            (T,2) bool
    labels/finger_contact/pad                (T,2) bool   该手指的有效接触中是否有指垫接触
    labels/finger_contact/position_gripper_m (T,2,3)      按法向力加权的接触点，夹爪系；无接触为 NaN
    labels/finger_contact/normal_gripper     (T,2,3)      加权后重新归一化的法向（手指指向物体）；无接触为 NaN
    labels/finger_contact/normal_force_N     (T,2)        有效接触的法向力之和
    labels/finger_contact/part               (T,2) int8   -1 无、0 粗端、1 细端、2 两段都有
    labels/finger_obstacle_contact           (T,2) bool   手指与地面或障碍的承载接触
    labels/requested_center_gripper_m        (T,3)        指定部位中心，夹爪系
    labels/actual_part                       (T,)  int8   两指共同夹持的部位；-1 表示没有
    labels/lift_m                            (T,)         物体相对基准的抬升高度
    labels/nearest_obstacle/{point_gripper_m (T,3), signed_distance_m (T,), surface (T,) int8}
    camera/{agentview,eye_in_hand}/K               (3,3)
    camera/{agentview,eye_in_hand}/T_world_camera  (T,4,4)   OpenCV 约定：x 右、y 下、z 前
    contacts_raw/offsets (T+1,) 以及每个接触一行的 finger、role、region、other_part、load_bearing、
                 constraint_active、position_gripper_m、normal_gripper、force_gripper_N、normal_force_N
```

- 有效接触：承载接触，并且法向力 ≥ 0.2 N（和部位判定用同一阈值）。没有有效接触的帧，几何字段为 NaN，力为 0，不会凭空补一个接触点。`contacts_raw` 保留全部原始接触，以后改标签定义时不需要重新解析 JSON。
- `obs/state` 的 8 维和 openpi LIBERO 示例一致：`robot0_eef_pos`（夹爪 site 的世界坐标位置，m）、`robot0_eef_quat`（right_hand 本体姿态，xyzw）经 robosuite `quat2axisangle` 得到的轴角，以及两个手指关节的 `robot0_gripper_qpos`（m）。注意：夹爪朝下时转角接近 π，而物体偏航随机分布在整圈，所以轴角的 x、y 分量会落在半径约 π 的圆上，并在 π 附近跳变（v1 训练集 q01–q99 约为 ±2.2）。这是轴角表示本身的特性。需要连续表示时，可以从 `labels/T_world_gripper` 换算成四元数或 6D 旋转。
- 图像：规范方向是左上角为原点的 RGB，和 K、T 一致。openpi LIBERO 示例对原始 robosuite 帧做 180° 旋转；由于原始帧是左下角为原点，那种约定等价于 `image[:, ::-1]`。水平翻转会改变像素几何关系，所以几何模块应该用规范图像配合存储的 K、T；如果一定要翻转，就必须同步改 K。
- 归一化统计：`export/norm_stats.json` 只用训练集计算，包含 state 和 actions 每维的 mean、std、min、max、q01、q99。训练框架有自己的变换和动作分块，实验室端应在其变换之后重新计算一次统计量，以框架的结果为准。
- 读取：`robolab.p4_dataset_export.iter_policy_frames(path, language='en'|'zh', image_convention='canonical'|'libero_rotated_180')` 逐帧给出 `image`、`wrist_image`、`state`（float32）、`actions`（float32）、`task`，和 openpi LIBERO LeRobot 转换脚本使用的字段同名。

## 5. 导出校验

导出后用第二条代码路径逐项核对，任何一项不一致都会让导出失败：

- 图像：用 OpenCV 重新解码每张 PNG，和 HDF5 数组逐像素比较；PNG 的 SHA256 必须和原始 `frames.jsonl` 里记录的一致。
- 动作：和实际发送的命令逐值相等。
- 状态：位置等于记录的夹爪位姿平移（误差 ≤ 1e-12 m）；手指和手臂关节等于按关节地址直接从 qpos 读出的值；轴角还原出的旋转矩阵等于独立运动学计算的手部姿态（误差 ≤ 1e-6）。
- 接触标签：从导出的 `contacts_raw` 用另一套聚合代码重新算一遍，和导出的逐指标签比较；每帧原始接触数等于源记录。
- 划分：每个文件里的示范和索引中的成功列表一一对应，同一条记录不会出现在两个文件里。
- 统计量：从训练文件重新计算，结果必须和 `norm_stats.json` 相同。

## 6. LeRobot / openpi

本机后端环境没有 `lerobot` 和 `pyarrow`，按约定也不往原环境安装新包，所以这一步在本机没有执行，`export_manifest.json` 中 `lerobot_written=false`。在实验室装有 openpi 的环境里，按 openpi LIBERO 转换脚本的方式，把 `iter_policy_frames` 给出的每一帧传给 `LeRobotDataset.add_frame`，每条示范结束时调用 `save_episode` 即可。写出后，要用 openpi 自带的统计脚本重新计算归一化统计，再做一次实际加载检查。
