# 在实验室安装与运行

支持目标为 Ubuntu 22.04、Python 3.10、Linux 桌面或 Windows WSL2 + WSLg。当前真实验证来自笔记本 WSLg/NVIDIA；实验室电脑和全新隔离环境仍需首次验收。GitHub 保存源码，不包含虚拟环境、LIBERO 资产、训练数据或 VLA 权重。

## 1. 获取代码

在 Ubuntu / WSL 终端中执行：

```bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/6f6sn7w4pb-ops/robolab-studio.git
cd robolab-studio
```

也可以从 GitHub 的 Code → Download ZIP 下载并解压。请在 Linux 文件系统中运行；Windows Python 不是仿真后端。

## 2A. 实验室已有可用的 LIBERO 环境（优先）

先用实际路径替换以下示例。配置命令只创建本项目 `settings/runtime.json` 和 `settings/libero/config.yaml`；不修改已有 Python 包、LIBERO 源码或 `~/.libero/config.yaml`。

```bash
python3 scripts/configure_runtime.py \
  --backend-python "$HOME/venvs/openvla-oft/bin/python" \
  --libero-root "$HOME/projects/LIBERO"
bash scripts/launch.sh --doctor
bash scripts/setup.sh
```

后端需与已验证的 MuJoCo 2.3.7、robosuite 1.4.1、LIBERO 0.1.0、numpy 1.26.4 兼容。LIBERO 固定源码版本为 `8f1084e3132a39270c3a13ebe37270a43ece2a01`。若已有研究环境版本不同，不要直接升级/降级它，使用下面的隔离安装。

配置优先级为环境变量 → `settings/runtime.json` → 自动路径。`~` 可用于 JSON 路径；相对路径以项目根目录为基准。完整模板见 [runtime.example.json](../settings/runtime.example.json)。配置文件含本机绝对路径，Git 会忽略。

## 2B. 新电脑没有仿真环境

这一入口仅供新机器显式执行。它会联网下载 Python 仿真包、CPU PyTorch 和固定版本的 LIBERO 源码/场景资产，创建本项目 `.venv-sim`；不会下载 VLA 权重，不安装 OpenVLA 训练栈，也不使用系统 Python 安装包。CPU PyTorch用于读取 LIBERO 初始状态；MuJoCo 渲染仍需要正常的 OpenGL/EGL。

如缺少系统依赖，先在有管理权限的 Ubuntu 22.04 中安装：

```bash
sudo apt-get update
sudo apt-get install git python3.10 python3.10-venv libegl1 libgl1 libglfw3 \
  libopengl0 libxkbcommon-x11-0 libxcb-cursor0 fonts-noto-cjk
```

然后在项目根目录执行：

```bash
bash scripts/bootstrap_sim.sh --create-isolated-env
bash scripts/setup.sh
```

核心依赖见 [requirements/sim.txt](../requirements/sim.txt)。它是基于本机版本提取的安装约束，不是所有传递依赖的完整 lockfile。**全新下载/安装路线尚未在干净实验室机器验证**；当前迁移验收复用已运行的后端。脚本遇到已有 `.venv-sim`、LIBERO 副本或配置会退出，不会覆盖；若安装中断，保留错误日志后按报错处理。

## 3. 验收与启动

```bash
# 只检查路径、依赖导入和默认任务资源，不代表渲染已经通过
bash scripts/launch.sh --doctor

# 无需桌面，实际验证两路 RGB 离屏渲染与物理步
bash scripts/smoke_test_libero.sh

# 打开桌面 GUI
bash scripts/launch.sh
```

GUI 中可切换中英文、加载任务、查看三维场景与两路 RGB、执行 Mock 接口测试，以及默认任务的 P2 规则抓取/记录。物理“运行”仅推进仿真；自动抓取请点击“策略 / 自检 → P2 规则抓取与记录（非 VLA）”。模型真实加载和真实推理会单独显示状态。

`bash scripts/launch.sh --smoke` 会真实操作窗口并自动关闭；`--p2-test` 会实际抓取、暂停/单步、验证记录并关闭。验证输出写入本项目 `outputs/` 或 `evidence/`，不进入 Git。

无桌面的 SSH 会话可运行 P1，不能直接显示桌面 GUI。需要 Linux 桌面、远程桌面或 WSLg。Linux 自动选择 Wayland/X11；必要时可用 `ROBOLAB_QT_PLATFORM=xcb bash scripts/launch.sh`。本机只实际验证了 WSLg Wayland；新机器图形驱动和 Qt 系统库需单独检查。

Windows 启动器默认使用 `Ubuntu-22.04` 的默认用户、Linux 的 `~/projects/robolab-studio`。可用 `ROBOLAB_WSL_DISTRO`、`ROBOLAB_WSL_USER`、`ROBOLAB_WSL_PROJECT` 覆盖，或运行 `Start-RoboLab.ps1 -Distro <发行版> -User <用户> -ProjectPath <Linux路径>`。推荐先在 WSL 终端完成验收。

## 4. 后续更新与实验数据

先关闭旧平台，然后在仓库根目录执行 `git pull --ff-only` 再启动。仓库更新不会替代环境验收；若依赖要求变化，应在隔离环境中处理。

数据留在本地 `outputs/`，另外备份到实验室存储。当前记录支持抓取证据与轨迹检查，尚未实现完整物理回放。双相机是仿真相机，不表示真实 D455/FER 已连接。

OpenVLA-OFT 和之后的 openpi 应作为独立模型进程/环境接入；平台基本功能不要求 VLA 权重。本机 OFT 的 protobuf 导入问题仍未解决，不会随着上传自动消失。

原笔记本固定文件哈希核对保留为 `bash scripts/launch.sh --baseline-doctor`，仅用于原机历史基线。实验室使用可移植的 `--doctor`，不要把原机哈希当成通用安装条件。
