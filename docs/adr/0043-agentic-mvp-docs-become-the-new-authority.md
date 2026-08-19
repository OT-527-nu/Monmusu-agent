# ADR-043：Agentic MVP 文档集成为新的设计权威

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：落实 [ADR-005](0005-gm-authority-over-fictional-causality.md) 至 [ADR-042](0042-model-selection-repeats-focused-and-full-playtests.md)，并取代现有 `docs/README.md` 中“只维护一套稳定文件名、不保留并行版本”的文档政策

新版完整方案写入 `docs/agentic_mvp/`，并由 `docs/README.md` 明确标记为当前设计权威。文档集分别覆盖 MVP 产品方案、系统架构与 Agent Loop、数据契约、GM 能力章程与 Prompt、《逃离塔纳里昂》模组参考书、预生成调查员与 NPC 参考、真实 GM 评估方案，以及旧实现迁移与删除清单。职责拆分用于保持契约可审查，不代表增加运行时模块或 Agent。

现有顶层 MVP、架构、回合、schema、Prompt、模组和角色文档保留在原路径，作为规则驱动旧架构的迁移对照，不覆盖、不移动，也不再作为新实现依据。除切换中央索引中的权威说明外，旧文档内容保持原样；源码只有在新版纵向切片通过相应测试后才逐步迁移或删除。

## 2026-08-19 后续决定

项目所有者确认旧基线没有继续比较的价值，决定从当前树删除上述旧顶层文档，并以 ADR、`docs/archive/` 和 Git 历史承担追溯职责。本后续决定只改变旧文档保留政策；`docs/agentic_mvp/` 的权威地位不变，也不表示六场景、开放试玩或模型评估已经通过。
