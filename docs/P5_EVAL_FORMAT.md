# P5 闭环评估：协议与结果格式

P5 批的用途是在 P4-D 数据集的测试场景上，**批量、可复现地**闭环评估任意策略，并统一给出成功率、部位命中率和碰撞率。代码在 [robolab/p5_eval.py](../robolab/p5_eval.py)，入口是 [scripts/run_p5_eval.py](../scripts/run_p5_eval.py)，桌面查看在 [robolab/p5_eval_view.py](../robolab/p5_eval_view.py)。

## 1. 闭环协议（每个 tick 50 ms）

1. **观测**：在当前状态上生成观测，这个状态就是下一个动作开始作用的求解器状态，和数据集里一帧 RGB 的定义相同。
   - 两路 224×224 RGB：agentview 和 robot0_eye_in_hand，左上角为原点（canonical）。
   - 8 维状态：与 HDF5 训练视图同一个 `StateReader`，即 grip site 位置、robot0_right_hand 姿态的轴角、两个手指关节。
   - 7 个臂关节角，相机 K / T_world_camera，以及指令（`--language en|zh`）。
2. **策略**：返回 1–32 个归一化的 7 维 OSC_POSE 动作 `[dx,dy,dz,rx,ry,rz,gripper] ∈ [-1,1]`。
   - 默认整块执行完再查询下一次；`--execute-steps N` 表示每块只执行前 N 个。
   - 返回格式不合规，或者数值超出 [-1,1]，本条记为 `policy_error`，算失败，不会被裁剪后继续执行。
3. **执行**：交给数据集采集时的同一套代码执行，即 robosuite 1.4.1 SingleArm/OSC_POSE，每个周期 25 个 2 ms 子步，首子步做接触采样。
4. **判定**：逐 tick 送入数据集用的 `PartEvidence`，包括双指 pad 部位、抬升和持续时间，接触力阈值 0.2 N。时长固定为 `--horizon`，默认 222 tick，即 11.1 s，与示范长度相同。成功后不会提前结束，所以碰撞统计覆盖整条轨迹。

策略什么时候“看到”画面、动作作用在哪个状态上，与训练数据完全一致。教师参照组的逐帧比对证明了这一点，见 [P5_REPORT.md](P5_REPORT.md)。

## 2. 指标定义（metrics v2）

| 指标 | 定义 |
| --- | --- |
| 成功 success | 同时满足以下条件：<br>• 指定部位被双指 pad 夹住，抬升 ≥ 10 cm 并持续 ≥ 1 s；<br>• 另一部位没有被这样夹持；<br>• 手指与障碍物、地面没有承载接触；<br>• 障碍物位移 < 2 mm；<br>• 没有策略或仿真错误。 |
| 部位命中 part_hit | 双指 pad 同时在指定部位上，连续 ≥ 0.2 s，不要求抬起 |
| 错部位 wrong_part_contacted | 另一部位满足同样的条件 |
| 接触时部位正确率 | 在碰到过任一部位的条目中，只碰指定部位的比例。用来把“部位选错”和“没抓到”分开 |
| 抬起 lifted | 双指都接触目标，抬升 ≥ 10 cm，并持续 ≥ 1 s（不区分部位） |
| 碰撞 collision | 手指与障碍或地面有承载接触，或障碍物相对静置位姿位移 ≥ 2 mm |
| 接触前扰动 | 第一次采样到指尖接触目标的 tick 之前一个 tick 为止，目标转动 ≥ 1° 或移动 ≥ 1 mm。接触在 tick 开头采样，手指可能在前一个 tick 内部已经碰到物体，所以那个 tick 不计入（v2）。**只作诊断**，不计入成功。另外，有 4 个圆柱测试场景的物体会自发转动，见 P5_REPORT 第 4 节 |
| 成功用时 | 满足夹持 1 s 条件的 tick，乘以 0.05 s |

- 障碍物和目标的参考位姿都取第 11 个 tick 结束时，与数据集指标 v3 一致，用来排除开始阶段物体落到地面的约 1 mm 位移。
- 接触采样只覆盖手指，机械臂其他连杆碰到物体时只能通过物体位移间接发现。
- `--resummarize` 遇到旧版本指标时，会根据 trace.jsonl 重算，并标记 `recomputed_from_files`。
- 分组统计按划分、形状族、部位、说法（已见/留出）以及划分×说法给出。比例都附 95% Wilson 区间。

## 3. 策略接口

| `--policy` | 说明 |
| --- | --- |
| `teacher` | P4 规则教师，每个 tick 从实时仿真读部位位置。**特权策略，只作参照上限**，不是学习得到的模型 |
| `wrong_part_teacher` | 同一个教师，故意去抓另一个部位。用来证明部位指标能区分“抬起了，但部位错了” |
| `adapter:<name>` | 任何已注册的 `VlaAdapter`，经现有的 `robolab.observation.v1` 桥接（JPEG base64）。`adapter:mock` 是无模型自检；实验室端的推理服务用 `--endpoint http://…` 接入 |

桥接观测在 v1 字段之外还带有以下字段，服务端可以据此确认数据约定：
- `state_layout='p4_training_view_v1'`
- `image_convention='canonical_top_left'`
- `action_space`
- `episode_tick`

openpi 的 LIBERO 例子把图像旋转 180°，这一步在适配器或服务端做，等价于 `canonical[:, ::-1]`，见 P4_DATASET_FORMAT.md。

## 4. 输出目录

```
<output>/eval_config.json     策略说明、数据集与计划 sha、划分、语言、时长、阈值、代码提交
<output>/episodes/<case_id>/
    result.json               场景、指令、PartEvidence 结果、指标、失败原因、可选的参考比对
    trace.jsonl               每个 tick：动作、是否查询策略、8 维状态、目标和障碍位姿、双指部位、抬升、接触计数
    keyframes/NNNN_<camera>.png   每 20 tick 一对，另加最后一帧；是策略实际看到的图像
    timing.json               墙钟时间与策略延迟（不参与逐字节比较）
<output>/recheck/<case_id>/   抽样重跑，与 episodes/ 逐字节比较
<output>/summary.json         schema robolab.p5_eval_summary.v1；分组统计、对照组、复跑和参考比对、每条概要
<output>/logs/
```

## 5. 复现和资源

- 场景来自数据集的 `plan.json`。载入时会按代码和种子重新生成计划，并核对 sha。
- 确定性策略的结果文件、轨迹和关键帧可以逐字节复现，`--recheck K` 会在每个划分抽一条重跑。
- 加 `--reference` 时，每个 tick 都与数据集里同一 case 比较：原始轨迹的动作和 qpos，以及 HDF5 训练视图的两路图像和 8 维状态。记录每一类第一次出现差异的 tick。
- `--resume` 会跳过已完成的条目；写到一半的目录移到 `discarded/`。`--resummarize` 只用已有文件重建汇总，不重新仿真。
- 每个进程约占 2.7 GB 内存。启动前读取 MemAvailable，自动限制 `--workers`（最多 4 个）。

```bash
bash scripts/launch.sh --p5-eval --policy teacher --splits test_instance,test_unseen,control --reference --workers 2 --output-dir outputs/p5_eval_teacher
```

```bash
bash scripts/launch.sh --p5-eval --policy adapter:mock --workers 2 --output-dir outputs/p5_eval_mock
```

```bash
bash scripts/launch.sh --p5-gui-test
```

桌面端操作：在 P4 面板选择结果文件，选中 `summary.json` 就会打开 P5 评估窗口。窗口里有分组比例、逐条结果、失败原因和关键帧滑块，可以切换中英文。
