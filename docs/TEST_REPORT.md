# RoboLab Studio 第一阶段交付与测试报告

这是最初的 0.1 / P0 报告。0.2 新增模型接入、Mock 与规则抓取测试，请同时阅读 `VLA_INTEGRATION.md` 和 `VLA_TEST_REPORT.md`，不要将下文历史范围当作 0.2 的最新状态。

验证日期：2026-10-31，Europe/London。实际执行位置为用户本机 Windows / WSL2 Ubuntu-22.04。

## 交付位置

- 运行项目：`/home/openvla/projects/robolab-studio`。
- Windows 源码和启动入口：`C:\Users\24841\Documents\ChatGPT\毕设环境搭建\robolab-studio`。
- Windows 启动：双击 `Launch RoboLab Studio.cmd`。
- 操作说明：项目根目录 `README.md`。

## 环境检查与隔离结果

| 项目 | 实际检查结果 |
| --- | --- |
| Python 后端 | 原环境 Python 3.10.12 |
| MuJoCo / robosuite / LIBERO | 2.3.7 / 1.4.1 / 0.1.0 |
| torch / numpy / OpenCV / Pillow | 2.2.0+cu121 / 1.26.4 / 4.11.0.86 / 12.3.0 |
| GPU | RTX 4060 Laptop，8188 MiB，驱动 591.74 |
| WSLg | `DISPLAY=:0`、`WAYLAND_DISPLAY=wayland-0`、X11 socket 存在；GUI 实测使用 Wayland |
| GUI 新依赖 | 仅安装在新项目 `.venv`：PySide6 6.8.3 及其配套 wheel |
| 原脚本 / 配置 | `show_libero.py`、`test_libero_object.py`、`.libero/config.yaml`、原 venv 的 `pyvenv.cfg` SHA-256 均与开发前一致 |
| 原环境包清单 | 在相同搜索路径下，SHA-256 与开发前一致：`ea323012208d8a81b73e3561ffe4a08b284220bd13dab4e669ee2a6e45fd4eb6` |
| LIBERO 仓库 | `git status --porcelain` 为空 |

环境证据：`evidence/environment-before.json`、`evidence/environment-after.json`。可通过 `bash scripts/launch.sh --doctor` 重新检查。

初次包清单比对未设置原检查使用的 `PYTHONPATH`，导致 source egg-info 的枚举范围不同，产生摘要差异。以原始检查命令重跑后，摘要与开发前一致；`doctor.py` 已固定此检查上下文。初次差异保存在 `evidence/environment-search-path-mismatch.json`，未通过安装、升级或回滚包来消除差异。

## 功能与证据

| 功能 | 实际验证 | 结果 |
| --- | --- | --- |
| 原环境 LIBERO 最小示例 | 读取原脚本配置，加载官方初始状态、实际 `env.step`、960×640 原生 MuJoCo 渲染 | 通过 |
| 桌面 GUI | 可见 WSLg / PySide6 窗口，真实子进程及嵌入 RGB 视口 | 通过 |
| LIBERO 任务 | 读取 10 个 LIBERO-Object 任务；实际加载第 0、1 个任务 | 通过 |
| 交互三维相机 | 渲染像素随旋转变化；实际 pan/zoom；MuJoCo 射线拾取；Qt 鼠标拖动改变相机 | 通过 |
| 仿真控制 | Play 推进物理时间，Pause 冻结时间，Reset 恢复布局归零，Step 精确推进 20 ms | 通过 |
| 模式边界 | 仿真模式禁止添加物体；切换编辑态后允许编辑 | 通过 |
| 物体编辑 | 创建 box/sphere/cylinder；编译实际刚体；位置、旋转、尺寸、质量、摩擦及碰撞位掩码检查；删除后模型不再包含该物体 | 通过 |
| 任务物体编辑 | LIBERO 自由物体的位置和旋转实际更新 | 通过 |
| 机器人控制 | 七关节限位、越界目标拒绝、实际物理步后 PD 关节追踪目标、夹爪真实开合、末端位姿变化 | 通过 |
| 物理真实性 | 新增物体受重力下降；仿真产生接触 | 通过 |
| 保存与恢复 | JSON 保存 → 删除物体 → 重开文件；恢复位姿、物理属性及机器人姿态；无效 schema 不替换原场景 | 通过 |
| 截图 | 实际 MuJoCo PNG 导出，验证文件尺寸；GUI 截图人工查看 | 通过 |
| 错误与释放 | IPC 越界命令被拒绝，后端继续可用；窗口关闭后子进程退出并释放 GL；最终无遗留 worker | 通过 |
| Windows 启动 | 实际执行 Windows `.cmd --smoke`，自动加载场景并运行 Play/Pause/Reset | 通过，exit code 0 |

## 测试命令与记录

引擎和数据验证：

```bash
cd /home/openvla/projects/robolab-studio
bash scripts/test_engine.sh
```

本轮 4 个自动测试通过，其中 1 个为真实 MuJoCo/LIBERO 集成测试、3 个为项目格式验证测试。完整集成流程覆盖多个功能断言，未使用仿真 mock。`evidence/engine-tests-final.log`：`Ran 4 tests in 157.273s`，`OK`。

桌面完整回归：

```bash
bash scripts/launch.sh --self-test
```

9 组桌面检查全部通过，耗时 150.75 秒，后端正常退出。见 `evidence/gui-tests-final.log`、`evidence/gui-test-result.json`。

最后仅调整默认视角的包围范围和渲染对象分配顺序，随后通过 Windows 入口复测新代码：

```powershell
& '.\Launch RoboLab Studio.cmd' --smoke
```

3 组启动检查通过，耗时 43.81 秒，exit code 0；包括首帧、最新默认视角、实际 Play/Pause/Reset 和资源释放。见 `evidence/desktop-smoke-result.json`。项目 Python 源码编译检查也通过。

## 截图

- `evidence/studio-default-view.png`：最新默认视角，完整机械臂与操作区域。
- `evidence/studio-edited.png`：新增刚体、场景树、实际属性编辑。
- `evidence/studio-robot.png`：七关节滑块、夹爪和末端遥测。
- `evidence/gui-export.png`：GUI 命令触发的真实 MuJoCo 截图导出。
- `evidence/engine-edited.png`：引擎编辑测试后的渲染。

截图为本机软件实际运行所得，已经人工查看；不是概念设计图。

## 开发中发现并处理的问题

1. 每次基础物体编辑都新建渲染上下文，初始化渲染资源约需 32–36 秒。已在同一任务内复用不可变的网格、纹理资源；编辑时重新编译真实物理模型，实测通常约 1.5–4 秒。原环境未升级。
2. 初始相机沿用常见桌面高度，和当前 LIBERO 地面坐标不符。现根据机器人关节及自由物体位置自动计算默认包围范围；Windows 启动复测截图确认完整入镜。
3. 首轮桌面脚本在删除步骤超时：动态属性面板的选择/可见状态不够稳定。回归流程已明确重新选择目标、滚动到可见控件并检查启用状态，再触发 Qt 按钮；实际删除、项目恢复与任务切换均通过。首轮失败日志仍保留为 `evidence/gui-tests.log` 和 `evidence/studio-failure.png`。

## 尚未解决及未覆盖的范围

- 首次加载或任务切换仍需约 30–60 秒初始化渲染资源；GUI 保持独立运行。未完成大规模任务切换压力测试或长期内存稳定性测试。
- 实际场景测试限于 LIBERO-Object 的第 0、1 个任务；其他任务和其他 suite 尚未逐一加载验证。
- 只对新增基础物体提供完整的尺寸/质量/摩擦/碰撞编辑与删除；原始 LIBERO 网格支持位姿编辑，其余属性保留。
- 没有鼠标变换手柄、撤销栈、完整网格建模、STL/OBJ 导入或运动轨迹回放。
- 当前使用 MuJoCo 原生物理和手动关节 PD 伺服，未接入 LIBERO 的任务成功率评测循环，也没有自主抓取或 VLA 推理。
- 真实机械臂、夹爪、相机尚未接入；已保留分离的驱动协议和仿真适配器注册接口。本版默认设备为 LIBERO Panda。
- 双相机、标定、深度/分割、接触监督、数据集和策略推理属于 P1/P2，未作为已完成功能交付。
- 原库仍会输出 Gym、robosuite 私有宏及资源读取警告。没有为消除这些警告更改原库。

参考资料阅读范围：交接文档全文、两份研究 DOCX 的正文、成功截图，以及 G³VLA PDF 的首页摘要和引言。P0 不实现论文中的几何融合模块，后续实现前需继续核对完整方法与实验细节。
