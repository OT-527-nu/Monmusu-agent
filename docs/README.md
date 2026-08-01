# Monmusu Agent 文档索引

## 当前权威

下一版 MVP 的设计权威是 [Agentic MVP 文档集](agentic_mvp/README.md)，由 [ADR-005](adr/0005-gm-authority-over-fictional-causality.md) 至 [ADR-044](adr/0044-real-llm-vertical-slice-leads-migration.md) 记录其关键取舍。领域词汇统一见仓库根目录的 [CONTEXT.md](../CONTEXT.md)。

这次权威切换不表示目标架构已经全部实现。截至 2026-07-31，默认 `monmusu-agent` 仍运行旧的规则驱动路径；独立的 opt-in `monmusu-agent-agentic` 已完成 Increment 1：不可变会话、GM 最终答复与事实账本、`make_check`、真实 DeepSeek adapter 和两回合开放行动验证。正式恢复、其余 COC 工具、完整短篇与默认入口切换仍是目标工作。判断“代码现在会做什么”时必须检查源码和测试，判断“下一版应该实现什么”时使用 `docs/agentic_mvp/`；具体差距见[迁移清单](agentic_mvp/migration.md)。

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

## 迁移基线

以下顶层文档保留在原路径，用于理解当前源码、比较迁移差异和追溯旧决策。它们不再约束 Agentic MVP，也不会在本次设计重写中覆盖或移动。

| 旧文档 | 迁移价值 |
| --- | --- |
| [旧 MVP 设计](mvp_design.md) | 旧规则驱动产品范围与八周计划 |
| [旧系统架构](architecture.md) | `RuleEngine`、`StateCommitter`、场景投影和旧状态权威 |
| [旧回合循环](turn_loop.md) | `request_check` / `apply_effect`、工具预算与兜底路径 |
| [旧 JSON 数据契约](schemas.md) | 模组规则、效果授权、旧状态与记忆结构 |
| [旧 Prompt 契约](prompts.md) | `strategy`、固定建议、角色生成和模组边界 Prompt |
| [城市设定](thalarion_setting.md) | 新模组参考书吸收与重新解释的内容来源 |
| [旧模组设计](game_design.md) | 固定场景、线索、威胁时钟和结局基线 |
| [旧角色卡](character_cards.md) | 人物素材、关系阶段和角色工具方案基线 |
| [StateCommitter 同步计划](statecommitter-document-sync-plan.md) | 旧单效果授权 seam 的同步记录 |
| [GameEngine 同步说明](gameengine-design-sync.md) | 旧 `run_turn` 和可信结果组装的同步记录 |

[ADR-001](adr/0001-single-gm-agent-and-character-tools.md) 至 [ADR-004](adr/0004-user-input-gameengine-turn-contract.md) 同样保留为当前规则驱动实现的历史决策。若它们与 ADR-005 至 ADR-044 或 `agentic_mvp/` 冲突，以后者作为下一版设计依据；旧 ADR 不因被取代而删除。

## 参考与历史

- [ADR-031](adr/0031-deepseek-through-openai-sdk.md)记录 DeepSeek 接入边界；具体协议依据链接到 DeepSeek 官方文档。
- [`archive/`](archive/) 保存已退出当前设计的协议探索。
- 私有来源笔记与讨论草稿不随公开仓库分发；只有被当前权威文档或 ADR 明确采用的决定才属于产品契约。
- 清理前历史只保存在本地备份分支，不随公开分支上传；运行时也不维持永久双架构或通用版本路由。

项目名称 `Monmusu Agent` 保持不变。它是产品名称，不表示系统包含多个独立 Agent。
