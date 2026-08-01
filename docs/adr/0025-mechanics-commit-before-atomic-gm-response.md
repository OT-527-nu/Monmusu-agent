# ADR-025：机械即时提交，GM 答复原子提交

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-006](0006-gm-chooses-mechanics-harness-resolves.md) 与 [ADR-009](0009-mechanics-in-tools-facts-in-final-response.md)

COC 工具成功结算后，Harness 在一次原子写入中同时保存骰点、HP、SAN、幸运等机械变化、`ToolInteraction` 幂等映射和 assistant/tool 协议消息；这些结果不因后续模型超时、结构错误或步骤超限而回滚，也不能通过重试重新掷骰。GM 最终答复中的玩家叙事和事实变化则作为另一个整体提交，结构无效时两者都不写入。

若机械已经提交而 GM 尚未产生合法最终答复，Harness 保存一个未完成回合。恢复时把原玩家输入、既有工具交互和机械结果重新交给同一个 GM Agent Loop，让它继续完成该回合；不得创建新回合或重复结算。多次恢复仍失败时只报告明确的技术中断，不由 Harness 编造虚构内容。
