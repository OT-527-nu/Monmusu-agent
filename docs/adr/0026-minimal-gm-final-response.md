# ADR-026：GM 最终答复只包含叙事、事实变化与会话状态

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-009](0009-mechanics-in-tools-facts-in-final-response.md)、[ADR-018](0018-fiction-state-is-a-natural-language-fact-ledger.md) 与 [ADR-021](0021-mvp-is-one-open-ended-short-session.md)

GM 最终答复只包含玩家可见 `narration`、要确立的公开或隐藏事实、按 `fact_id` 结束的事实及原因，以及值为 `ongoing` 或 `complete` 的短篇会话状态。Harness 负责附加回合 ID、机械记录、事实 ID 和诊断信息，并从真实工具结果与写入结果构造调用层返回值。

GM 不输出 `strategy`、固定 `suggested_actions`、独立 `character_turns`、检定或提交结果副本、`ending_id`、工具轨迹或故障字段。GM 可以在自然叙事中按需要提出可能做法，但最终答复 schema 不要求菜单，也不让模型伪造 Harness 已知的机械或运行事实。
