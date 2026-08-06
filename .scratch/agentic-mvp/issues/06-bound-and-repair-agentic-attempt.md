# 06 — 约束并修正 Agentic 执行尝试

**What to build:** 把新回合和后续恢复都会使用的单次 GM 执行尝试收敛到同一个 Harness 内部流程，落实八次响应、单请求 60 秒、整次尝试 180 秒和一次无工具结构修正。所有边界都必须处理已经收到的响应并保存可恢复状态，不能以额外模型请求或 Harness 兜底叙事掩盖失败。

Blocked by: 05 — 验证真实两回合开放行动纵向切片

Status: done

## References

- [Parent spec](../spec.md): User Stories 31, 38-39 and 49; Implementation Decisions “One continuous loop”, “Runtime limits and ordinary failures” and “Eighth-response control flow”; protocol/eighth-response and recovery acceptance gates.
- [Migration](../../../docs/agentic_mvp/migration.md): “增量 2：未完成回合与运行恢复”的运行保险丝、结构修正、故障矩阵和无兜底叙事验收门。
- [Data contracts](../../../docs/agentic_mvp/contracts.md): `IncompleteTurn`, `GameMasterModel`, 运行配置、CLI 回合结果以及原子性与不变量。
- [Agent Loop](../../../docs/agentic_mvp/agent_loop.md): “执行尝试”, “单次结构修正”, “运行保险丝”和“故障处理”。
- [ADR-025](../../../docs/adr/0025-mechanics-commit-before-atomic-gm-response.md), [ADR-032](../../../docs/adr/0032-one-local-schema-repair-attempt.md), [ADR-036](../../../docs/adr/0036-agent-loop-has-eight-round-trip-safety-fuse.md), and [ADR-037](../../../docs/adr/0037-agent-loop-has-request-and-turn-deadlines.md).

- [x] `AgenticHarness.start_turn` 委托一个可由后续 `resume_turn` 复用的私有执行尝试；公开 lifecycle seam、`GameMasterModel` seam 和现有 `AgenticSessionStore` 依赖保持不变，不新增第二个 orchestrator、恢复 repository、provider registry 或按文件大小拆分的模块。
- [x] 每次执行尝试独立获得最多 8 个模型响应、每请求默认 60 秒和整次尝试默认 180 秒；请求实际 timeout 不超过尝试剩余时间，Harness 在下一次模型请求和最终提交前检查截止时间，已开始的原子写入不被中途拆断。
- [x] 第八个响应仍按实际类型完整处理且绝不请求第九个：合法 final 正常校验并提交；合法单工具调用正常执行并原子持久化后以 step-limit 中断；无效 final、结构化工具错误、可关联多工具错误和不可关联协议响应分别保存其应有状态后中断。
- [x] 最终答复第一次本地 schema 或事实引用校验失败时，只在往返和时间预算允许时请求同一个 GM 修正一次；修正请求保留原输入、上下文和已提交工具结果，禁用 function tools，只接受完整最终 JSON，不重跑或撤销机械。
- [x] 空 content、截断、鉴权、限流、服务端、网络、未知模型步骤、请求超时、尝试超时、额度耗尽、修正失败和最终写入失败都映射为稳定技术中断；Harness 不自动补偿请求、不生成虚构兜底叙事，也不清除已提交机械。
- [x] `IncompleteTurn` 持久化每次尝试的计数、限制、累计诊断和最后失败，同时保持现有合法 provider 消息前缀；技术失败输出只使用玩家安全字段，不暴露隐藏事实、隐藏机械、provider envelope、诊断或 reasoning content。
- [x] 使用可编程假 model、可控时钟、确定性 RNG、故障注入和真实临时 session 目录，从 `AgenticHarness.start_turn` 公开 seam 覆盖 60/180 秒、八种第八响应分支、修正成功/失败、工具后中断和 final 提交中断；断言公开事件、持久化状态、请求次数以及机械恰好一次，而不是私有 helper 调用。

**Not in this ticket:** 正式 `resume_turn`、CLI 会话发现、重复工具调用幂等矩阵、thinking `reasoning_content` 回放、真实 DeepSeek 恢复运行或 Increment 3 的其余 COC 工具。

## Comments

- 2026-08-06：Ticket 06 实现完成并收尾。`AgenticHarness.start_turn()` 通过 `_execute_attempt()` 统一有界执行；实现 8 次往返、请求 timeout 按剩余尝试时间裁剪、180 秒尝试截止、一次无工具结构修正、第八响应不请求第九次、多工具协议错误持久化、原子工具/最终提交和稳定技术中断。现有 `IncompleteTurn` 诊断字段为累计往返/结构修正计数、受限协议错误和最后失败；未向当前数据契约擅自增加 provider usage/latency 字段。
- 2026-08-06：公开 seam 测试覆盖合法/无效第八 final、合法单工具、结构化工具错误、可关联/不可关联多工具、不可关联单工具、空 content、截断响应、请求/尝试截止、结构修正和最终写入故障，并断言请求次数、公开机械/RNG 恰好一次及可加载存档。
- 双轴复核（固定点 `adcc5aa`，Ticket 06 基线 `54dbc51` 加本次收尾补丁）：Standards 无硬性违规；仅保留 `_final_validation_summary` 与 provider tool-call shape 校验的重复代码判断项。Spec 无明确运行时阻塞；此前第八响应测试覆盖缺口已在本次收尾补齐。
- 验证：`PYTHONPATH=src .venv/bin/python -m unittest tests.test_agentic_harness`（37 passed）；`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`（163 passed）；targeted mypy、改动文件 Ruff、`git diff --check` 均通过。系统 `/usr/bin/python3` 的两项 DeepSeek SDK API 缺失错误属于既有环境差异。
