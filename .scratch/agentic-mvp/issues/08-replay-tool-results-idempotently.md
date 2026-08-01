# 08 — 幂等重放已提交工具结果

**What to build:** 在同一 Harness 恢复路径中以 `(turn_id, tool_call_id)` 作为幂等键。模型重发已经处理的调用时，相同参数只复用持久化结果并继续协议；不同参数、多个调用或不可用 ID 按目标协议保存错误并中断或在剩余预算内继续，任何分支都不能再次掷骰或重复数值变化。

Blocked by: 07 — 恢复同一个未完成回合

Status: ready-for-agent

## References

- [Parent spec](../spec.md): User Stories 26-27, 30, 34 and 36-38; Implementation Decisions “Runtime authority identifiers”, “Tool protocol and idempotency” and “Eighth-response control flow”; protocol/eighth-response and recovery acceptance gates.
- [Migration](../../../docs/agentic_mvp/migration.md): “增量 2：未完成回合与运行恢复”的工具结果、无重掷、进程重启和故障矩阵验收门。
- [Data contracts](../../../docs/agentic_mvp/contracts.md): “工具调用统一外壳”, `ToolInteraction`, `IncompleteTurn` 以及原子性与不变量。
- [Agent Loop](../../../docs/agentic_mvp/agent_loop.md): “模型响应协议”, “工具分支”, “八次模型往返”和“显式恢复时序”。
- [ADR-025](../../../docs/adr/0025-mechanics-commit-before-atomic-gm-response.md), [ADR-036](../../../docs/adr/0036-agent-loop-has-eight-round-trip-safety-fuse.md), and [ADR-038](../../../docs/adr/0038-player-explicitly-resumes-incomplete-turn.md).

- [ ] 在每个回合内强制一个 `(turn_id, tool_call_id)` 最多对应一条持久化 `ToolInteraction`；跨回合相同 provider ID 不混淆，不把 `tool_call_id` 提升为 Harness 权威 ID。
- [ ] 同 ID、同工具名且同参数的重发返回已保存的成功结果或结构化错误，不调用 RNG、不重复角色数值变化、不追加第二条 mechanic/interaction，也不把旧公开机械再次报告为新提交事件。
- [ ] 幂等比较优先使用已通过 schema 的规范化参数；无法规范化时精确比较 `arguments_raw`，包括空白和转义差异。相同 ID 搭配不同工具名、不同规范参数或不同 raw 字符串返回稳定 protocol error，不能覆盖原记录或执行工具。
- [ ] 单工具调用缺失、非字符串、空或首尾有空白的 ID 不创建 synthetic assistant/tool 配对或 `ToolInteraction`；只原子保存受限 `provider_protocol_errors` 与稳定失败状态，从最后一个合法可回放前缀恢复。
- [ ] 多工具响应一个也不执行。所有 ID 可用且唯一时，原子保存每个失败 `ToolInteraction` 与对应错误 tool message，并仅在预算允许时继续；任一 ID 不可用或重复时只保存受限原始协议记录并中断。
- [ ] 第八响应上的成功工具、失败工具、可关联多工具和不可关联响应沿用 Ticket 06 分支：保存能保存的权威状态后中断，绝不为反馈请求第九个响应。
- [ ] 机械、角色变化、成功/失败 `ToolInteraction` 及可回放 assistant/tool 消息继续通过一次 `session.json` 原子替换提交；故障注入证明写入失败不会留下部分幂等映射，也不会让后续恢复误判工具已经或尚未执行。
- [ ] 从 `start_turn` / `resume_turn` 公开 seam，以真实临时 session、确定性 RNG、可编程假 model 和重复进程重建覆盖：成功/失败工具同参重发、raw fallback、异参重发、多工具各分支、不可用/重复 ID、工具提交故障、反复恢复；每案断言机械 ID、骰点和数值变化始终恰好一次。

**Not in this ticket:** 新 COC 工具、通用去重服务、CLI 菜单、thinking transport、真实 DeepSeek 证据或把 provider `tool_call_id` 作为跨会话身份。
