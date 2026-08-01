# ADR-004：用户输入驱动的 GameEngine 回合与结果所有权

- 状态：Accepted
- 日期：2026-07-26
- 决策范围：Monmusu Agent 8 周 MVP 的外层回合入口、模型可见上下文和最终结果所有权
- 决策关系：局部取代 [ADR-001](0001-single-gm-agent-and-character-tools.md) 与 [ADR-002](0002-lightweight-player-facing-mvp-loop.md)
- 局部取代范围：`RoundTrigger`、旧 `TurnContext` 字段、模型直接生成完整 `GameMasterTurnResult`，以及 GameState/Memory 共用回合版本的描述

MVP 的外层回合只由 `GameEngine.run_turn(input_text)` 启动。程序不预分类 `trigger_type`；“继续”“等待”或让队友发言仍是普通用户原文，由 `GameMasterAgent` 理解。确定性系统事件和只读闲置提醒在出现真实 UI 或模组需求前不进入这一接口。

`TurnContext` 只在可信代码内部保存 `turn_id`、用户原文、初始 GameState、总工具步骤和各工具配额。模型不直接读取 `TurnContext` 或完整 GameState，而是接收由 GameEngine 组装的 `ModelRequest`：固定的 `GameMasterStateView`、当前场景投影、公开记忆、当步动态工具目录和此前工具交互。角色私有记忆、原始预算、`turn_id` 和隐藏状态不得进入该请求。

模型完成有限循环时只返回候选 `GameMasterDraft(strategy, narration, suggested_actions)`。GameEngine 从 `ToolSession.trace` 和最终状态快照组装 `GameMasterTurnResult`：检定、已提交效果、角色结果、`turn_id` 与 `ending_id` 都来自可信代码。调用层接收的 `GameTurnOutcome` 另外携带完整工具轨迹、降级标记和稳定失败码。模型不提交 `tool_trace_ids`、`is_ending` 或任何可信结果副本。

GameState 继续用独立 `state_version` 做状态提交乐观锁，但不保存 `turn_number`。当前 GameEngine 只读取并校验 Memory 的 `public_memory`，不在回合末写 Memory；Memory 不再复制 GameState 的 `state_version`，未来如需并发控制应使用自己的 `memory_version`。

## 后果

- 取消旧 `trigger_type`、`allow_state_changes`、`standing_assignment_ids` 和 `module_reaction_ids`，角色机械行动只接受本轮 `user_delegated`；用户自身行动使用 `user_declared`。
- 动态工具目录和确定性模块继续承担权限兜底，删除字段不扩大 GM 的状态权限。
- 当前未实现角色生成工具，因此 `character_turns` 由 GameEngine 置为空；以后只能从可信角色工具结果聚合。
- Agent 失败或 Draft 非法时，GameEngine 保留已经产生的检定和提交并返回确定性降级结果；状态、Memory 或静态数据损坏仍直接抛出。
