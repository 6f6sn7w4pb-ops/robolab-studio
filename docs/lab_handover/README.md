# 实验室电脑的交接记录（2026-11-27 换电脑时复制）

这些文件原本只在旧实验室电脑的 `~/lab_logs/` 下，换电脑时复制进仓库，内容没有改动（只把 `PROGRESS.md` 里的邮箱地址换成了占位符）。

**用户 2026-11-27 决定：数据、检查点在新电脑上重新生成，不拷贝。** 步骤见 [REGENERATE.md](REGENERATE.md)。

| 文件 | 内容 |
| --- | --- |
| [REGENERATE.md](REGENERATE.md) | 在新电脑上装环境、重新生成数据集和测试检查点的步骤（命令和旧电脑上的结果、用时）；**新电脑是 Ubuntu 20.04**，注意事项在第 1 节 |
| [archive/README.md](archive/README.md) | 旧电脑的存档：零样本评估的全部数据（不含图片）和 `~/lab_logs` 日志，各一个压缩包；零样本关键帧图片放在 GitHub Release `lab-pc-archive-2026-11` |
| [PROGRESS.md](PROGRESS.md) | 进度记录，**先看开头的“当前状态（2026-11-25 收工时）”**：下次从哪里接着做、冒烟测试的确切步骤、还没定的事 |
| [wrist_camera_study_record.md](wrist_camera_study_record.md) | 腕部 D455 安装位置研究的简短记录和初步推荐（偏移 65 mm、仿真高度 10 mm、倾角 36.8°） |
| [wrist_camera_study_summary.md](wrist_camera_study_summary.md) | 该研究 42 个候选的全表（`outputs/wrist_camera_study/summary.md`） |
| [PR_lerobot-conversion.md](PR_lerobot-conversion.md) | `lerobot-conversion` 分支的 PR 标题和描述 |

注意：
- 记录里的路径（`~/projects/...`、`~/lab_logs/...`、`outputs/...`）和 Claude 会话 ID 都是旧电脑上的；新电脑上不能用 `claude --resume` 接回旧对话。
- 数据和检查点不在 GitHub 上（单个文件超过 GitHub 的上限）：`outputs/p4d_lab`（11 GB）、`outputs/lerobot`（2.6 GB）、`outputs/openpi_checkpoints/.../probe_bs32`（9 GB）、各次评估结果、`~/.cache/openpi` 里的 `pi05_base`/`pi05_libero` 权重。按用户决定，在新电脑上按 [REGENERATE.md](REGENERATE.md) 重新生成或下载。
