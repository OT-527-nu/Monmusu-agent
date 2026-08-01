# ADR-016：每轮只有一个连续的 GM 工具循环

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-009](0009-mechanics-in-tools-facts-in-final-response.md)，局部取代 [ADR-002](0002-lightweight-player-facing-mvp-loop.md) 的三策略回合输出与 [ADR-004](0004-user-input-gameengine-turn-contract.md) 的 `strategy` 字段

每轮由 Harness 组装上下文并调用同一个 GM。GM 可以直接给出最终答复，也可以调用 COC 语义工具、观察结果后继续选择工具或完成回合；Harness 随后记录完整回合并更新事实索引。工具调用次数和结束时机由 GM 决定，Harness 只提供结构校验、超时与最大步骤等运行保障。

MVP 不实现输入分类器、回合路由器、独立 planner、critic、validator、memory Agent、角色生成器或叙事后的第二次 LLM 调用，也不要求固定 OODA 阶段或 `fast`、`dramatic`、`urgent` 策略标签。主持方法可以作为 GM 参考，但不能成为工作流状态机。
