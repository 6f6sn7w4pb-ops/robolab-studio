# P3 设计包

设计日期：2026-11-03。更新：2026-11-10，A–C 批已验收。见 [A 批报告](../../P3_A_REPORT.md)、[B 批报告](../../P3_B_REPORT.md) 与 [C 批报告](../../P3_C_REPORT.md)。

- [设计说明](P3_DESIGN.md)：范围、坐标／力／采样语义、架构、数据与兼容边界。
- [分批实施与验收](P3_IMPLEMENTATION_PLAN.md)：A–C 批交付和完成标准。
- [正式帧合同](contact-frame.schema.json)：持久化 v1 的 JSON Schema。读取器另查父轨迹、维度、时间及哈希。
- [原有接触草案示例](contact-frame.example.json)、[原空接触草案示例](empty-frame.example.json)：历史设计示例，使用独立标识。真实帧保存在 B 批验收目录。
- [独立语义检查](../../../outputs/p3_design/design-audit.json)：保存 XML 的只读核查、小型 MuJoCo 力方向与时序实验；不代替 Panda 集成验收。
- [历史合同草案检查](../../../outputs/p3_design/contract-validation.json)：实施前的旧合同检查。正式合同的实测结果见 B 批报告。
- [论文阅读与接入考虑](G3VLA_PI05_READING_NOTES.md)：本地 G³VLA v2 与 π₀.₅ v1 的依据。

下一批 P4 建设部位抓取任务和同步 RGB 数据。模型、训练和真实设备属于后续阶段。A 批的内存帧 schema 与 B 批的持久化帧标识独立。
