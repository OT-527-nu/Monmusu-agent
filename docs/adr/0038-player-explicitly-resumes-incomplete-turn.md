# ADR-038：未完成回合由玩家明确恢复

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-025](0025-mechanics-commit-before-atomic-gm-response.md)、[ADR-034](0034-cli-is-the-only-mvp-player-interface.md)、[ADR-036](0036-agent-loop-has-eight-round-trip-safety-fuse.md) 与 [ADR-037](0037-agent-loop-has-request-and-turn-deadlines.md)

CLI 在启动时以及接受下一条游戏行动前检查是否存在未完成回合。若存在，它会展示技术中断和已经公开的机械结果，并只允许玩家明确选择继续恢复或退出；在该回合完成前，不接受会建立新虚构因果的玩家行动。CLI 不在启动时自动发起模型请求，避免未经玩家确认产生等待或费用。

继续恢复使用同一个 `turn_id`、原玩家输入、冻结的 model/tool profile、已有模型与工具交互以及已经提交的机械结果，不创建新回合、不重新执行已完成工具，也不重新掷骰；它开启新的执行尝试并重新获得八次往返、180 秒和一次结构修正预算。若冻结 profile 不可用，Harness 不发起模型请求并报告恢复错误。退出只关闭当前程序，不能删除未完成回合；下次启动仍提供同一恢复入口。Harness 只有在 GM 最终答复成功校验并提交后，才解除对新游戏行动的阻塞。
