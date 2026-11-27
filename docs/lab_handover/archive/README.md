# 旧实验室电脑的存档（2026-11-27）

用户换电脑前，要求把零样本评估结果和日志上传到 GitHub。这两样没法原样重新生成。

## 1. 本目录里的文件（随仓库下载）

| 文件 | 内容 | 大小 | SHA-256 |
| --- | --- | ---: | --- |
| `lab_eval_pi05_libero_zeroshot_data.tar.gz` | π₀.₅ 零样本评估（M0，旧布局 240 条）的全部数据，不含关键帧图片。包括 `summary.json`、`eval_config.json`、每条的 `result.json` / `timing.json` / `trace.jsonl`、复跑检查 `recheck/` 和 `logs/`，共 737 个文件。解压前 39.5 MB | 11.6 MB | `faa2bfb990eeb56fcd648ef1cd18b7f236385336a20646300c48c56577475a7a` |
| `lab_logs_2026-11.tar.gz` | 旧电脑上的 `~/lab_logs`（2026-11-18 至 11-25），共 49 个文件。包括安装、下载、评估、桥接服务、LeRobot 转换、LoRA 训练的日志（含批量 64 显存不足的完整报错）、腕部相机研究的日志、各次 PR 文案和 `PROGRESS.md`。只把 `PROGRESS.md` 里的邮箱地址换成了占位符，其余未改 | 1.9 MB | `efaac0ea22f1f3a8c8dd5add1fb55d9fbd7f9ec88348d57b45bcedcfbb9af4fa` |

解压方法：在仓库根目录运行
```bash
tar -xzf docs/lab_handover/archive/lab_eval_pi05_libero_zeroshot_data.tar.gz -C outputs/   # 得到 outputs/lab_eval_pi05_libero_zeroshot/
tar -xzf docs/lab_handover/archive/lab_logs_2026-11.tar.gz -C <任意目录>                   # 得到 <目录>/lab_logs/
```

核对：零样本数据包解压后，和旧电脑上的原目录逐文件比对一致（PNG 除外）。

## 2. 关键帧图片（不在仓库里）

零样本评估的关键帧共 6,292 张 PNG，307 MB。每条评估每 20 个时刻存一张，agentview 和腕部相机各一张。图片太大，不放进 Git 历史，否则以后每次克隆都要多下载这么多。

- **文件**：打成了一个包 `lab_eval_pi05_libero_zeroshot_keyframes.tar`，333 MB。SHA-256：`f782c706b1e9a3e5f627e3d02f4046ec53e5b114156c79487b086e7cb6a636c4`。
- **存放位置**：作为 GitHub Release 的附件上传（tag `lab-pc-archive-2026-11`）。没上传的话，它只在旧电脑的 `~/` 下。
- **解压**：`tar -xf lab_eval_pi05_libero_zeroshot_keyframes.tar -C outputs/`。图片会放回各条评估的 `episodes/<条目>/keyframes/` 下，和上面的数据包合起来，就是完整的原目录。
- **能不能重新生成**：`trace.jsonl` 只记了每个时刻的动作和末端状态，没有关节角，所以没法直接重新渲染。要重新得到这些图片，只能按记录的动作重放整条评估，目前没有现成的工具。

## 3. 注意

- 记录里的路径（`/home/rami/...`、`~/lab_logs/...`）、机器名和 Claude 会话 ID 都是旧电脑上的。
- 这份零样本结果用的是当时的评估设置：发给模型的图像是 JPEG（质量 95），openpi 的噪声按请求顺序取。PR #14 合并后，评估改用无损 PNG 和固定噪声，所以新电脑上重跑的结果不会和这份逐字节相同。汇总数字见 [LAB_ZEROSHOT_REPORT.md](../../LAB_ZEROSHOT_REPORT.md)。
