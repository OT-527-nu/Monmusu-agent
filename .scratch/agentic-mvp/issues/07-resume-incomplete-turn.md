# 07 — 恢复同一个未完成回合

**What to build:** 在现有 `AgenticHarness` lifecycle seam 上增加只读玩家安全状态投影和显式恢复。调用者先查询一局是否存在未完成回合，再以明确的 `turn_id` 恢复同一输入、冻结 profile、合法 provider 前缀和已提交工具结果；恢复开启新执行尝试，但不创建新回合、不重掷，也不接受替代玩家行动。

Blocked by: 06 — 约束并修正 Agentic 执行尝试

Status: ready-for-agent

## References

- [Parent spec](../spec.md): User Stories 23-27, 38-40 and 47; Implementation Decisions “Highest stable seams”, “Tool protocol and idempotency” and “Explicit recovery”; recovery and player-visibility acceptance gates.
- [Migration](../../../docs/agentic_mvp/migration.md): “增量 2：未完成回合与运行恢复”的未完成回合、显式恢复与冻结配置范围。
- [Data contracts](../../../docs/agentic_mvp/contracts.md): `IncompleteTurn`, GM 上下文、运行配置、CLI 回合结果以及原子性与不变量。
- [Agent Loop](../../../docs/agentic_mvp/agent_loop.md): “执行尝试”, “未完成回合”, “故障处理”和“显式恢复时序”。
- [ADR-025](../../../docs/adr/0025-mechanics-commit-before-atomic-gm-response.md), [ADR-036](../../../docs/adr/0036-agent-loop-has-eight-round-trip-safety-fuse.md), [ADR-037](../../../docs/adr/0037-agent-loop-has-request-and-turn-deadlines.md), and [ADR-038](../../../docs/adr/0038-player-explicitly-resumes-incomplete-turn.md).

- [ ] 保持一个深 Harness 接口，最小公开 lifecycle 为 `get_session_state(game_id) -> SessionLifecycleView`、现有 `start_turn(game_id, player_input, ...) -> TurnResult` 和 `resume_turn(game_id, turn_id, ...) -> TurnResult`；不公开 store、provider 消息、工具执行器或恢复内部步骤。
- [ ] `get_session_state` 只读真实 session aggregate，返回会话状态、是否有未完成回合、稳定 `turn_id`、玩家可见的技术状态和已提交公开机械投影；它不调用模型、不修改存档，也不返回隐藏事实、隐藏机械、provider envelope、私有诊断或 reasoning content。
- [ ] `resume_turn` 必须显式匹配当前 `IncompleteTurn.turn_id`；未知、已完成、过期或与当前阻塞不匹配的 ID 在模型调用前失败，且不能创建新 `turn_id`、覆盖原输入或清除恢复记录。
- [ ] 恢复沿用原玩家输入、Prompt 修订、model profile、attempt limits、工具 schema 版本、启用工具列表、同一局正典、合法 provider 消息前缀和已提交工具结果；冻结 profile 或版本不可用时不静默换模型/配置，也不发起请求。
- [ ] 每次显式恢复调用开启一个新的有界执行尝试，重新获得 8 个响应、180 秒和一次结构修正预算，同时保留累计尝试、往返、延迟与修正诊断；`start_turn` 和 `resume_turn` 共用 Ticket 06 的执行尝试实现。
- [ ] 普通中断从已保存对话继续；缺失/不可用/重复 ID 等不可配对响应只保留受限原始记录，并从最后一个合法可回放前缀继续。既有 assistant/tool 配对和 tool result 必须按原顺序进入下一请求，不伪造或丢弃已提交机械。
- [ ] 恢复成功只通过既有原子 final 提交写入 `CommittedTurn`、事实与状态并清空 `incomplete_turn`；再次中断保留同一 `turn_id`，成功或失败都不制造重复回合、事实或机械。
- [ ] 使用重新构造的 Harness 实例、真实临时 session 目录和可编程假 model 测试 provider/tool/final-write 中断后的恢复、错误 `turn_id`、冻结 profile 不可用、重复进程启动和再次中断；通过公开 lifecycle 返回值与磁盘投影证明连续性、阻塞和玩家可见性。

**Not in this ticket:** 同 ID 工具重放的完整幂等规则、CLI 交互与会话发现列表、thinking transport、真实 DeepSeek 恢复证明、通用 session handle 或 Increment 4 的正常历史会话续玩。
