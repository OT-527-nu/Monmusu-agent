# Monmusu Agent 文档索引

## 当前权威

下一版 MVP 的设计权威是 [Agentic MVP 文档集](agentic_mvp/README.md)，由 [ADR-005](adr/0005-gm-authority-over-fictional-causality.md) 至 [ADR-045](adr/0045-bounded-provider-retry-policy.md) 记录其关键取舍。领域词汇统一见仓库根目录的 [CONTEXT.md](../CONTEXT.md)。

这次权威切换不表示目标架构已经全部实现。截至 2026-08-19，`monmusu-agent` 已切换为 Agentic CLI，旧规则驱动运行链、旧测试、旧 JSON 数据和旧顶层设计文档已退役；Agentic 路径已完成 Increment 1 至 Increment 4 的工程切片，包括不可变会话、GM 最终答复与事实账本、GM 五工具 COC 机械、六张生产角色卡、显式恢复、会话续玩和内容发布边界。真实六场景、完整短篇、开放试玩、模型评估矩阵和默认模型选择仍未完成。判断“代码现在会做什么”时检查源码和测试，判断目标设计时使用 `docs/agentic_mvp/`；具体差距见[迁移清单](agentic_mvp/migration.md)。

新设计的核心边界是：GM 裁定虚构因果并建立正典；Markdown 模组是参考书而不是权限表；Harness 提供可信 COC 机械、事实与回合持久化、结构校验和恢复，但不审批故事是否允许发生；运行时只有一个连续 GM Agent Loop。

## 阅读顺序

| 文档 | 职责 |
| --- | --- |
| [Agentic MVP 总览](agentic_mvp/README.md) | 文档状态、产品核心、内部权威顺序与完整索引 |
| [MVP 产品方案](agentic_mvp/mvp_design.md) | 核心假设、最终范围、成功标准与增量顺序 |
| [系统架构](agentic_mvp/architecture.md) | 权威分配、深模块、DeepSeek seam 与数据流 |
| [Agent Loop](agentic_mvp/agent_loop.md) | 工具循环、原子提交、保险丝、中断与显式恢复 |
| [数据契约](agentic_mvp/contracts.md) | 会话、角色卡、事实、五项 COC 工具与最终答复结构 |
| [GM 能力章程与 Prompt](agentic_mvp/gm_prompt.md) | 正向主持职责、完整上下文组装和单次结构修正 |
| [《逃离塔纳里昂》模组参考书](agentic_mvp/module_reference.md) | 可采用、改编或舍弃的世界、秘密、地点与发展素材 |
| [角色与预生成调查员](agentic_mvp/characters.md) | 三张调查员卡与三名同行者的机械、人格和主持参考 |
| [MVP COC 技能目录](agentic_mvp/skill_catalog.md) | 规范化技能键、基础值、派生公式与专长编码 |
| [验证与模型评估](agentic_mvp/evaluation.md) | 确定性测试、真实 DeepSeek 契约、六场景矩阵与试玩 |
| [当前实现迁移清单](agentic_mvp/migration.md) | 纵向切片、逐增量验收、旧路径退役和高风险冲突 |

本目录内部发生冲突时，字段形状以[数据契约](agentic_mvp/contracts.md)为准，执行顺序以 [Agent Loop](agentic_mvp/agent_loop.md) 为准，人物参考以[角色文档](agentic_mvp/characters.md)为准，模组素材以[模组参考书](agentic_mvp/module_reference.md)为准；ADR 解释为什么选择这些边界。

## 迁移边界

旧规则驱动源码、旧 JSON 数据、旧测试和旧顶层设计文档已经从当前树删除。旧 ADR、`docs/archive/`、Git 历史和本地备份仍保留，用于追溯决策；它们不再代表当前运行时契约。

[ADR-001](adr/0001-single-gm-agent-and-character-tools.md) 至 [ADR-004](adr/0004-user-input-gameengine-turn-contract.md) 保留为已退役规则驱动实现的历史决策。若它们与 ADR-005 至 ADR-045 或 `agentic_mvp/` 冲突，以后者作为当前设计依据；旧 ADR 不因被取代而删除。

## 参考与历史

- [ADR-031](adr/0031-deepseek-through-openai-sdk.md)记录 DeepSeek 接入边界；具体协议依据链接到 DeepSeek 官方文档。
- [`archive/`](archive/) 保存已退出当前设计的协议探索。
- 私有来源笔记与讨论草稿不随公开仓库分发；只有被当前权威文档或 ADR 明确采用的决定才属于产品契约。
- 清理前历史只保存在本地备份分支，不随公开分支上传；运行时也不维持永久双架构或通用版本路由。

项目名称 `Monmusu Agent` 保持不变。它是产品名称，不表示系统包含多个独立 Agent。
