# P5-B 实验条件加强与对比报表：实现与验收报告（2026-11-12）

## 1. 目标与边界

在 P5 评估框架上补齐笔记本端的四项工作：
- 小收尾：清理未使用的 import；P3-C 的 GUI 验收不再必须手动指定 `--matrix-dir`。
- 扩大测试规模：加入额外的测试场景，缩小置信区间。
- 场景条件：圆柱防滚；光照、背景、物体颜色的外观变化。
- 对比报表：把多次评估放在同一批条目上比较，输出可以直接用于论文的表格和图。

所有新选项**只用于评估**，P4-D 数据集、它的计划 sha 和示范都没有改动。本批仍然没有学习得到的模型或真实 VLA，没有训练、硬件或环境改动。下面的结果都是用特权规则教师对评估框架做的验收，不是模型性能。用法见 [P5_EVAL_FORMAT.md](P5_EVAL_FORMAT.md) 第 6–7 节。

## 2. 小收尾

- 删掉 6 处确实没用到的 import（`gui.py` 的 `QFrame`、两个 GUI 验收脚本的 `Path`、`run_p4_pilot.py` 和 `test_engine.py` 的 `json`、`verify_p2_batch2.py` 的 `list_episodes`）。`smoke_test_libero.py` 里的 `import mujoco` 是有意的导入检查，保留不动。
- `--p3c-gui-test` 原来默认读 `outputs/p3c_verification`，而那是一份**未通过**的首轮结果，所以每次都得手动传 `--matrix-dir`。现在默认在 `outputs/` 下找最新一份“通过、且历史记录目录仍存在”的后端矩阵；找不到时直接给出要先运行的命令。实测自动找到 `outputs/p3c_acceptance`，4 项检查全部通过。

## 3. 圆柱自转：根因与防滚

P5 报告记录过 4 个圆柱测试场景会自发转动。这次在全部 40 个圆柱场景上做了被动实验：机械臂不动、夹爪张开、222 个 tick，并把转动拆成绕长轴的滚动、绕竖直轴的偏航和位移（脚本和数据在 `outputs/p5b_logs/roll_diagnosis.{py,jsonl}`）。

| 接触设置 | 滚动 ≥ 1° 的场景 | 最大滚动 | 最大偏航 | 最大位移 |
| --- | --- | --- | --- | --- |
| 数据集原设置（condim 3） | 31/40 | 10.70° | 0.002° | 0.35 mm |
| 目标几何体 condim 4（加扭转摩擦） | 31/40 | 10.70° | 0.002° | 0.35 mm |
| 目标几何体 condim 6（加扭转和滚动摩擦） | 0/40 | 0 | 0 | 0 |
| **P5-B `--anti-roll`：只对目标—地面接触用 condim 6** | **0/40** | **0** | **0** | **0** |

- 现象是**绕长轴的缓慢滚动**，不是原地打转。它也不只出现在测试集：训练集 23/30、验证集 4/4、测试集 4/6 的场景都会滚，开始时间在第 137–221 个 tick。
- 原因：两段圆柱都只有滑动摩擦的线接触，几乎没有抵抗滚动的力，物体在接触求解的柔顺性下慢慢滚开。只加扭转摩擦没有用，加了滚动摩擦就完全消除。
- 数据集示范没有受影响：采集时有抓取前扰动检查（数据集指标 v3），所有示范都在滚动开始前就已经接触到物体，并且通过了检查。受影响的是评估：动作慢的策略可能面对一个已经在滚的物体。
- 实现：用显式接触对 `pair`，只作用于目标两段和地面之间。摩擦、solref、solimp 都取 MuJoCo 原来对这两个几何体混合出的值，只把 condim 改成 6（扭转 0.02、滚动 0.001），并用 `exclude` 去掉原来的动态接触。手指、障碍物和机械臂的接触完全不变；盒体和椭球场景不受影响。
- 代价：开了防滚的圆柱条目，物理与录制示范不再逐 tick 相同（从第 0 个 tick 的 qpos 起就有差别）。所以汇总时这些条目不计入“与数据集一致”检查，而是单独计数为 `excluded_physics_variant`。

## 4. 扩展测试场景

- 新增 88 个场景、176 条：盒体和圆柱各 24 个新实例（test_instance），椭球 40 个（test_unseen）。各族一半的场景用留出说法。
- 使用与数据集相同的采样器、尺寸范围、稳定性约束和提示词池，种子 20261112，场景 id 带 `-x`。
- 与**整份**数据集计划（训练集 120 条、验证集 16 条，加上原测试集）一起审计：形状实例、布局不重复，未见形状族和留出说法不会泄漏到训练和验证数据。
- 合计每个测试划分 120 条（60 个场景），四个“划分 × 说法”格子各 60 条。95% 区间宽度：总体 240 条约 ±6%（成功率 50% 时），每个格子约 ±12%。

## 5. 外观变化

由场景 id 和固定种子决定，同一场景的粗端/细端两条外观相同；只改渲染字段，不改物理。三组可以任意组合：

- **lighting**：两盏灯的亮度（原值 × 0.55–1.15）、色温、水平位置和高度、环境光、高光，各 50% 概率开阴影；头灯亮度。
- **background**：地面纹理在木地板和墙面灰泥之间二选一，平铺密度和色调随机；墙面色调。
- **objects**：目标物颜色（两段同色，颜色不能透露部位）和障碍物颜色，两者色相至少相差 60°。

调参中改过两处：
1. 起初让灯对准工作区，224 像素画面大面积过曝，于是改成保持灯原来的朝向，只缩放亮度、移动位置；
2. 起初的纹理候选里有 `tex-living_room`，它是一张迷彩一样的拼贴，其中的橙色块和目标物很像，于是去掉了。

样例图在 `outputs/p5b_logs/appearance_grid_{a,b}.png`（由同目录的 `appearance_samples.py` 生成）。每格 5 列依次是：标准、只变光照、只变背景、只变颜色、全部变化；每列上方是桌面相机，下方是腕部相机。

## 6. 验收运行

| 运行 | 条数 | 成功 | 部位命中 | 碰撞 | 阻挡对照检出碰撞 | 复跑逐字节一致 | 与数据集比对 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `teacher`，扩展场景 + 防滚 | 240 + 4 对照 | 240/240（95% CI 98.4–100%） | 240/240 | 0/240 | 4/4 | 2/2 | 55/55 逐帧一致；13 条圆柱按设计排除 |
| `teacher`，数据集场景 + 全部外观变化 | 64 + 4 对照 | 64/64 | 64/64 | 0/64 | 4/4 | 2/2 | **物理 68/68 一致**；有导出图像的 64 条，图像全部从第 0 个 tick 起就不同 |

- **扩展场景**：四个“划分 × 说法”格子都是 60/60；接触前扰动 0 条（开了防滚后，圆柱的诊断噪声也消失了）；平均成功用时 9.5 s，与原教师运行相同。用时 34 分钟（2 个进程，每条中位数 15.9 s）。
- **外观变化**：动作、qpos 和判定与录制示范完全相同，同时图像不同。这说明外观变化只改变策略看到的输入，不改变任务本身；用它测出的成功率下降，可以全部归因到视觉泛化上。用时 15 分钟。
- **对比报表**（`outputs/p5b_compare_harness/`）：把原教师、外观变化教师、扩展场景教师、错部位教师和 Mock 放在 64 条共享条目上比较。三组教师成功率都是 64/64，彼此的 McNemar p = 1；与错部位教师和 Mock 相比，成功率差 +100%，p ≈ 1e-19。报表会自动提示“扩展运行有 176 条在共享集合之外”。
- 原来三组 P5 运行的 `summary.json` 用 `--resummarize` 只从文件重建了一遍，补上 `conditions` 和物理一致性字段。结果数字没有变化，重建前的版本保存在 `outputs/p5b_logs/pre_p5b_summary_*.json`。

## 7. 代码与接口变化

- 新增：
  - `robolab/p5_variation.py`：防滚、外观；
  - `robolab/p5_scenes.py`：场景集；
  - `robolab/p5_compare.py` 与 `scripts/compare_p5_evals.py`：对比报表，入口 `--p5-compare`。
- `PilotScene.install` 增加可选的 `xml_hook`；不传时编译出的模型与以前完全相同。实测标准条件下的轨迹和关键帧与原教师运行逐字节一致。
- `p5_eval`：
  - `select_cases` 改为返回 `(plan, cases, scene_set_info)`；
  - 配置、结果和汇总记录场景条件；
  - 参考比对新增 `physics_identical`（旧结果文件根据其中保存的首次偏离 tick 推导）；
  - 汇总新增 `reference_physics_identical` 检查。旧运行目录按“数据集场景、无防滚、标准外观”读取。
- 桌面 P5 窗口新增一行“场景条件”，显示场景集、防滚、外观变化和物理一致性，中英文都有。

## 8. 回归

- 后端 `scripts/test_engine.sh`：91 项通过，3 项按设计跳过（GUI 测试）。新增 `tests/test_p5_extensions.py`，已加入 CI 的 NumPy 步骤。
- GUI 环境：P5 窗口 4 项、P4-D 窗口和 i18n 测试通过。
- 真实 WSLg 桌面：
  - `--p5-gui-test` 打开 5 份汇总（包括 244 条的扩展运行），中英文切换时后端不重启，截图在 `outputs/p5b_gui/`；
  - `--p3c-gui-test` 不加 `--matrix-dir` 也能通过，结果在 `outputs/p5b_p3c_gui_regression/`；
  - `--smoke` 通过。

## 9. 复现命令

```bash
bash scripts/launch.sh --p5-eval --policy teacher --scene-set extended --anti-roll --splits test_instance,test_unseen,control --reference --workers 2 --output-dir outputs/p5b_eval_teacher_extended
```

```bash
bash scripts/launch.sh --p5-eval --policy teacher --appearance all --splits test_instance,test_unseen,control --reference --workers 2 --output-dir outputs/p5b_eval_teacher_appearance
```

```bash
bash scripts/launch.sh --p5-compare --output-dir outputs/p5b_compare_harness teacher=outputs/p5_eval_teacher teacher-appearance=outputs/p5b_eval_teacher_appearance teacher-ext-antiroll=outputs/p5b_eval_teacher_extended wrong-part=outputs/p5_eval_wrong_part mock=outputs/p5_eval_mock
```

## 10. 对实验室阶段的建议与限制

- 真实模型建议跑两轮：扩展场景 + 防滚 + 标准外观，以及再加 `--appearance all`。两轮的命令见 [P5_EVAL_FORMAT.md](P5_EVAL_FORMAT.md) 第 6 节。再用 `--p5-compare` 把它们和教师参照放在一起，就得到论文需要的“已见/未见形状族 × 已见/留出说法 × 外观”表格，以及成对检验。
- 限制：
  - 外观变化不包括相机位姿、纹理贴图替换或物体形状纹理，所以只能说明对光照和配色的鲁棒性。
  - 扩展场景没有录制示范，只能逐帧比对数据集里原有的那部分条目。
  - 防滚改变了圆柱与地面的接触模型。如果以后扩大数据集，建议采集时也开启防滚，让训练和评估的物理一致。
  - 图中文字用英文，因为系统里没有中文字体。
