# ADR-009：机械在工具中结算，事实随最终答复提交

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-006](0006-gm-chooses-mechanics-harness-resolves.md) 与 [ADR-008](0008-gm-narration-establishes-canon.md)

最小 Agent Loop 中，GM 可以调用 COC 机械工具并观察 Harness 返回的随机与数值结果，再继续推理或完成回合。GM 的最终答复同时包含玩家可见叙事，以及本轮确立、改变或结束的公开和隐藏世界事实；Harness 将答复与事实变化写入回合记录，并据此刷新事实索引。

世界事实变化不要求逐项调用 `record_fact` 一类工具，也不使用第二个 LLM 从叙事中抽取。Harness 可以拒绝结构无效或无法完整写入的最终答复，但不得审批 GM 的虚构内容。没有持续事实变化的回合可以提交空变化集；机械结果仍以工具记录为准，不能被最终答复改写。
