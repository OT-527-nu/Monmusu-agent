# Agentic MVP 权威文档集

## 文档状态

本目录定义 Monmusu Agent 下一版 MVP 的目标产品、目标架构与验收方式，落实 [ADR-005](../adr/0005-gm-authority-over-fictional-causality.md) 至 [ADR-044](../adr/0044-real-llm-vertical-slice-leads-migration.md)。它是新实现的设计权威，但不是当前源码已经完成的能力清单。

截至 2026-08-16，默认入口仍运行旧的模组 JSON、`request_check`、`apply_effect`、效果白名单、关系阶段与六格威胁时钟。独立的 opt-in Agentic 入口已经完成 Increment 1 至 Increment 3：不可变 `SessionSetup`、自然语言事实账本、GM 最终答复原子提交、六张生产角色卡、统一 `CocTool` 生命周期、统一 `PublicMechanic` 投影、`make_check` / `push_check` / `spend_luck` / `deal_damage` / `make_sanity_check` 五工具、non-thinking/thinking DeepSeek adapter、连续 CLI、`IncompleteTurn` 持久化、显式 CLI 恢复门、同 ID 工具结果幂等重放、结构修正、执行尝试时限/往返保险丝，以及 `reasoning_content` 受限回放均已交付；Ticket 18 场景二和场景三已在 2026-08-16 由项目所有者采纳通过。已有完整会话的继续入口、完整短篇六场景与开放试玩、模型评估矩阵、默认入口切换与旧路径退役仍未交付。具体差异与迁移门见[迁移清单](migration.md)。

## 产品核心

MVP 是一局由真实 LLM GM 主持、玩家可以自由行动并形成叙事收束的《逃离塔纳里昂》CLI 短篇。

- GM 拥有虚构世界、NPC、因果与正典的裁定权。
- 模组是全文提供给 GM 的 Markdown 参考书，不是剧情规则或权限表。
- 玩家拥有调查员的主动意志；GM 不替玩家决定台词、信念或重大行动。
- Agent Harness 结算 COC 机械、维护事实与完整记录、保护持久化一致性，但不审批故事能否发生。
- 每轮只有一个连续 GM Agent Loop；没有 router、planner、critic、角色 Agent 或 Memory Agent。

## 阅读顺序

| 文档 | 权威职责 |
| --- | --- |
| [MVP 产品方案](mvp_design.md) | 产品目标、范围、成功条件与交付增量 |
| [系统架构](architecture.md) | 深模块、接口、seam、权威与数据流 |
| [Agent Loop](agent_loop.md) | 正常回合、工具调用、提交、失败与恢复时序 |
| [数据契约](contracts.md) | 角色卡、事实、工具、回合与运行配置的规范结构 |
| [GM 能力章程与 Prompt](gm_prompt.md) | GM 的正向主持职责、上下文组装和结构修正提示 |
| [《逃离塔纳里昂》模组参考书](module_reference.md) | GM 可采用、改编或舍弃的世界与短篇素材 |
| [调查员与 NPC 参考](characters.md) | 预生成调查员机械数据及 NPC 人格、秘密与动机 |
| [MVP COC 技能目录](skill_catalog.md) | 规范化技能键、中文显示名、基础值与专长编码 |
| [验证与模型评估](evaluation.md) | 确定性测试、真实 DeepSeek 场景、评分与选型流程 |
| [迁移清单](migration.md) | 当前实现到目标设计的增量顺序、替换与删除项 |

领域词汇以仓库根目录的 [CONTEXT.md](../../CONTEXT.md) 为准。若本目录内部发生冲突，数据形状以[数据契约](contracts.md)为准，执行顺序以 [Agent Loop](agent_loop.md) 为准，人物事实以[调查员与 NPC 参考](characters.md)为准，模组素材以[模组参考书](module_reference.md)为准；ADR 解释这些取舍为何成立。

## 不再作为新设计依据的文档

现有顶层 `docs/mvp_design.md`、`architecture.md`、`turn_loop.md`、`schemas.md`、`prompts.md`、`game_design.md`、`character_cards.md` 与 `thalarion_setting.md` 保留在原路径，用于比较旧实现和规划迁移。它们不再约束本目录，也不会在本阶段被覆盖或移动。

## 设计纪律

- 先证明真实 DeepSeek GM 能处理未预写行动并维持两回合连续性，再扩展完整机械。
- 只为真实变化建立 seam：`GameMasterModel` 同时拥有 DeepSeek adapter 与确定性假 adapter；本地存储与 COC 规则保持在 Harness 深模块内部。
- 结构化数据只服务于受信机械、持久化完整性和恢复。虚构世界不预先拆成路线、线索、旗标、时钟或结局字段。
- 目标设计和当前实现必须明确区分；文档不得把计划能力写成已经交付。
