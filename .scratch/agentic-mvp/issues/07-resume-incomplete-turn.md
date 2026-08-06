# 07 — 恢复同一个未完成回合

**What to build:** 在现有 `AgenticHarness` lifecycle seam 上增加只读玩家安全状态投影和显式恢复。调用者先查询一局是否存在未完成回合，再以明确的 `turn_id` 恢复同一输入、冻结 profile、合法 provider 前缀和已提交工具结果；恢复开启新执行尝试，但不创建新回合、不重掷，也不接受替代玩家行动。

Blocked by: 06 — 约束并修正 Agentic 执行尝试

Status: done

## References

- [Parent spec](../spec.md): User Stories 23-27, 38-40 and 47; Implementation Decisions “Highest stable seams”, “Tool protocol and idempotency” and “Explicit recovery”; recovery and player-visibility acceptance gates.
- [Migration](../../../docs/agentic_mvp/migration.md): “增量 2：未完成回合与运行恢复”的未完成回合、显式恢复与冻结配置范围。
- [Data contracts](../../../docs/agentic_mvp/contracts.md): `IncompleteTurn`, GM 上下文、运行配置、CLI 回合结果以及原子性与不变量。
- [Agent Loop](../../../docs/agentic_mvp/agent_loop.md): “执行尝试”, “未完成回合”, “故障处理”和“显式恢复时序”。
- [ADR-025](../../../docs/adr/0025-mechanics-commit-before-atomic-gm-response.md), [ADR-036](../../../docs/adr/0036-agent-loop-has-eight-round-trip-safety-fuse.md), [ADR-037](../../../docs/adr/0037-agent-loop-has-request-and-turn-deadlines.md), and [ADR-038](../../../docs/adr/0038-player-explicitly-resumes-incomplete-turn.md).

- [x] 保持一个深 Harness 接口，最小公开 lifecycle 为 `get_session_state(game_id) -> SessionLifecycleView`、现有 `start_turn(game_id, player_input, ...) -> TurnResult` 和 `resume_turn(game_id, turn_id, ...) -> TurnResult`；不公开 store、provider 消息、工具执行器或恢复内部步骤。
- [x] `get_session_state` 只读真实 session aggregate，返回会话状态、是否有未完成回合、稳定 `turn_id`、玩家可见的技术状态和已提交公开机械投影；它不调用模型、不修改存档，也不返回隐藏事实、隐藏机械、provider envelope、私有诊断或 reasoning content。
- [x] `resume_turn` 必须显式匹配当前 `IncompleteTurn.turn_id`；未知、已完成、过期或与当前阻塞不匹配的 ID 在模型调用前失败，且不能创建新 `turn_id`、覆盖原输入或清除恢复记录。
- [x] 恢复沿用原玩家输入、Prompt 修订、model profile、attempt limits、工具 schema 版本、启用工具列表、同一局正典、合法 provider 消息前缀和已提交工具结果；冻结 profile 或版本不可用时不静默换模型/配置，也不发起请求。
- [x] 每次显式恢复调用开启一个新的有界执行尝试，重新获得 8 个响应、180 秒和一次结构修正预算，同时保留累计尝试、往返、延迟与修正诊断；`start_turn` 和 `resume_turn` 共用 Ticket 06 的执行尝试实现。
- [x] 普通中断从已保存对话继续；缺失/不可用/重复 ID 等不可配对响应只保留受限原始记录，并从最后一个合法可回放前缀继续。既有 assistant/tool 配对和 tool result 必须按原顺序进入下一请求，不伪造或丢弃已提交机械。
- [x] 恢复成功只通过既有原子 final 提交写入 `CommittedTurn`、事实与状态并清空 `incomplete_turn`；再次中断保留同一 `turn_id`，成功或失败都不制造重复回合、事实或机械。
- [x] 使用重新构造的 Harness 实例、真实临时 session 目录和可编程假 model 测试 provider/tool/final-write 中断后的恢复、错误 `turn_id`、冻结 profile 不可用、重复进程启动和再次中断；通过公开 lifecycle 返回值与磁盘投影证明连续性、阻塞和玩家可见性。

**Not in this ticket:** 同 ID 工具重放的完整幂等规则、CLI 交互与会话发现列表、thinking transport、真实 DeepSeek 恢复证明、通用 session handle 或 Increment 4 的正常历史会话续玩。

## Comments

- 2026-08-06：Ticket 07 实现完成。`AgenticHarness` 增加只读 `SessionLifecycleView` 投影和显式 `resume_turn`；新回合与恢复分别准备 `IncompleteTurn` 后进入同一个 `_execute_attempt`。恢复严格匹配当前 `turn_id` 和冻结 `model_profile`，沿用存档中的原输入、attempt limits、合法消息前缀、工具交互与机械，并在模型调用前原子写入递增的尝试编号和重置后的本次预算。
- 2026-08-06：契约裁决：本票验收文字提到累计延迟，但 `contracts.md` 的规范 `IncompleteTurn` 没有 per-attempt 或累计 latency 字段，Ticket 06 也明确未扩展 provider usage/latency 数据形状。本实现按数据形状权威保留现有 `attempt_number`、累计往返和累计结构修正；如需持久化 latency，应先修改权威契约后另行实现，不能在本票静默扩 schema。
- 2026-08-06：公开 lifecycle 测试使用真实临时 session 目录和重新构造的 Harness，覆盖 provider 中断、公开/隐藏工具后中断、不可配对 provider 响应、最终写失败、错误/已完成 ID、冻结 profile 不可用和连续再次中断；断言状态查询零模型调用/零写入、恢复不分配新回合、不重掷、不重复机械/事实/回合，并隔离隐藏事实、隐藏机械与受限 provider 材料。
- 2026-08-06：双轴复核（固定点 `a0e72e3`）中，Standards 轴无发现；Spec 轴发现恢复未保留“等待无工具结构修正响应”的协议相位。已改为从 Harness 持久化的末条修正 prompt 恢复该相位，首个恢复请求继续禁用工具；若答复仍无效，新尝试仍可使用自己的一次结构修正预算。新增公开 seam 回归测试覆盖这条路径。
- 2026-08-06：Spec follow-up 又发现自定义 `max_structure_repairs > 1` 可能让同一尝试连续修正；已恢复“本尝试修正 prompt 后再次无效立即中断”的 phase guard，并增加 `max=2` 反例测试，保持每次尝试最多一次修正。
- 验证：`PYTHONPATH=src .venv/bin/python -m unittest tests.test_agentic_harness tests.test_agentic_session`（61 passed）；`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`（170 passed）；targeted mypy、改动文件 Ruff 基础规则和 `git diff --check` 均通过。验证解释器为项目 `.venv` 的 Python 3.12.3。
