# P3 B 批接触记录与兼容

2026-11-10，实际工作树为 WSL 的 `/home/openvla/projects/robolab-studio`。在 P2 第二批和 P3-A 本地代码上继续开发，Git HEAD 为 `3f86aa013956b8a4724d0d6d02225a842f09553d`。

## 使用

在既有 WSL 项目中运行一次真实仿真验收，指定一个新目录。

```bash
cd /home/openvla/projects/robolab-studio
bash scripts/launch.sh --p3b-test --output-dir outputs/p3b_check
```

通过 GUI 的「P2 记录 / 状态查看」选择上述目录下的 `episodes`，选中记录即可分别查看原始 P2 结果和 P3 接触记录状态。该验收还会生成明确标记的失败注入和单独的 `reader_fixtures`，不要把它们计入抓取成功率。

```bash
bash scripts/launch.sh --p3b-test --inspect /home/openvla/projects/robolab-studio/outputs/p3b_verification/episodes/20261110T152725Z_f2bd1b
```

仅查看时输出 P2/P3 状态 JSON，退出码表示检查程序能否执行。程序调用 `Engine.configure_contacts()` 后使用 `Engine.start_grasp_test(record_contacts=True)`，IPC 对应 `contact_configure` 和 `grasp_test` 的布尔参数 `record_contacts`。正式读取接口为 `robolab.contact_store.load_contact_episode(directory)`。原 GUI 规则抓取入口仍沿用默认配置，本批通过 CLI 或后端 API 开启附属记录。

## 数据含义

- `p3_contact_metadata.json` 使用 `robolab.p3_contact_episode.v1`，`p3_contacts.jsonl` 使用 `robolab.p3_contact_frame.v1`。A 批内存帧的字段延用到正式 JSONL。持久化写入单独的 schema 标识，旧草案例子明确使用示例标识。
- 每条接触帧同时保留求解器输入时间和积分后时间。第 i 个接触样本匹配 P2 第 i+1 行，按积分后时间关联。初始 P2 行没有求解器样本。已采样且无接触用空列表，立即取消可以完整结束为 0 帧。
- P2 先结束，然后 P3 刷新轨迹并原子替换清单。P3 清单绑定父轨迹 SHA256、XML SHA256、父样本数与执行结果，并记录自身轨迹 SHA256、首末时刻、模型维度、profile 和源码哈希。哈希用于一致性校验。
- P3 是否完整与 P2 是否成功独立。写入中故障暂停仿真，保留可读前缀和失败状态。结束清单写入出错保留独立失败，原 P2 结果仍由父记录决定。中断可能只留下未结束的记录，不自动修复或补造接触。跨机器重建场景仍要求匹配本地资产。
- 帧中是原始模拟求解器输出和双相机标定，未保存同步 RGB。当前记录不具备完整物理重执行或训练集语义。

## 验收

| 实测 | 结果 |
| --- | --- |
| 默认汤罐 seed 0 / init 0，关闭和开启正式记录各一次 | 两次均完成 22 秒，P2 轨迹 1101 行逐字节一致。启用时校验通过 1100 帧，约 10.57 MB，求解器时间 0.018–21.998 秒，左右指垫都出现目标承载接触。 |
| 实际未抓住物体 | 验收进程禁用接近 IK，实际物理执行。P2 正确失败，P3 550 帧有效，无目标承载接触。 |
| 两次取消 | 立即取消 0 帧、暂停后单步三次再取消 3 帧，两条 P2 均为取消，P3 均校验通过。三帧的接触列表为空。 |
| 写入故障注入 | 模拟流关闭后写入失败，仿真停止。模拟最终清单写入失败，原 P2 取消结果保留。 |
| 8 个读取夹具 | 覆盖中断、缺清单、缺轨迹、截断、字节修改、重算哈希后的错误行关联、未知格式、父哈希不匹配。P3 分类符合预期，原 P2 取消结果保留。 |
| 旧 v1/v2 | 各读到一条真实旧成功记录，P3 都显示未采集。 |
| 结构合同 | 1653 个真实帧逐帧通过发布的 JSON Schema，两个历史设计示例被拒绝。 |
| 回归 | 36 项后端及 7 项 GUI 本地化测试通过。原桌面编辑、双 RGB、Mock → 官方 OSC 和默认规则抓取真实运行通过。 |
| WSLg 桌面 | 中英文均显示已校验状态，切换保持同一后端。验证未采集、零帧取消和写入失败，P3 损坏时仍可打开有效的 P2 状态查看。退出后后端正常停止。 |

验收目录 `outputs/p3b_verification/` 含原始记录、`result.json`、`schema-check.json`、`gui-result.json` 和双语截图。完整 B 批仿真和读取验收约 46 秒。首次后端测试命令遗漏项目的 LIBERO 路径，补齐项目现有运行参数后通过。首次桌面验收脚本同时发出两个刷新请求，等待到了旧目录的回复。改为设置目录后再打开记录页，复测通过，首轮日志保存在 `gui-first-attempt.json`。

## 后续范围

下一批 C 接入基于求解器采样状态的接触点和法向显示，再扩展官方初态验证。随后 P4 接同步双 RGB、参数化部位任务及数据划分。B 批验证仅覆盖默认汤罐及选定对照，OFT 已知 protobuf 故障维持现状。未安装或升级环境，没有下载模型、真实 VLA 推理、训练或硬件测试。修改留在实际 WSL 项目，Windows 源码副本未同步，尚未提交或推送。
