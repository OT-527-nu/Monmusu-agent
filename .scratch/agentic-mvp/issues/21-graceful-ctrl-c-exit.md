# 21 — Ctrl+C 优雅退出

**What to build:** 让 Agentic CLI 在游戏流程的任何 Ctrl+C 落点（建局问答、回合之间的行动输入、模型调用进行中、恢复执行中）都以明确提示和稳定退出码结束，不打印 traceback、不改变存档语义、不创建新回合，也不承诺任何未保存的进度。未完成回合继续由 Harness 在模型调用前原子持久化，恢复仍走 Ticket 07/09 的显式恢复门。

Blocked by: 09 — 用未完成回合门控 CLI

Status: ready-for-human

## References

- [Parent spec](../spec.md): User Stories 23-27；Implementation Decisions “Incomplete turns and recovery” 与 “Highest stable seams”。
- [Migration](../../../docs/agentic_mvp/migration.md): 增量 2 的 CLI 恢复/退出门与“启动、查看状态和退出都不能自动调用模型或改变存档”。
- [Data contracts](../../../docs/agentic_mvp/contracts.md): `IncompleteTurn` 持久化时序与原子性不变量。
- [ADR-034](../../../docs/adr/0034-cli-is-the-only-mvp-player-interface.md), [ADR-038](../../../docs/adr/0038-player-explicitly-resumes-incomplete-turn.md)。
- 参考：deepseek-harness 的 SIGINT→130 / SIGTERM→0 与 teardown 约定（`apps/cli/src/profile-boot.ts`、`process-shutdown.ts`）。

- [x] 建局问答中 Ctrl+C 打印“已退出。”并以 130 退出，不创建会话、不调用模型。
- [x] 回合之间（行动输入处）Ctrl+C 打印“已退出；本局已提交回合均已保存。”并以 130 退出，已提交回合保持完整、无未完成回合。
- [x] 模型调用中 Ctrl+C 按存档真实状态提示（未完成回合已保留 → “下次启动选择恢复即可继续”；否则 → 已提交回合已保存），并以 130 退出；已持久化的未完成回合仍被 `find_incomplete_session_ids` 发现。
- [x] 恢复执行中 Ctrl+C 同样优雅退出，未完成回合保持同一 `turn_id` 与输入。
- [x] `main()` 收敛：seam 捕获后抛出统一 `CliPlayerInterrupt`，`_run_main` 返回 130；未被 seam 覆盖的窗口（终端配置、env 装载、会话读取等）兜底打印“已退出。”并返回 130。
- [x] 所有退出路径不打印 traceback、API key、隐藏事实或 provider 诊断；`main()` 的 `finally` 继续还原终端。
- [x] CLI seam 测试使用真实临时 session 目录与可编程假 model 覆盖：回合中中断（未完成回合保留 + 重启可发现）、输入处中断（已提交回合保留、零新模型调用）、建局中断（零会话、零模型调用）、`main` 的 130 收敛与兜底；断言 model request 数与存档字节不变。

**Not in this ticket:** 一般已完成/进行中历史 session 的选择和续玩（Increment 4 会话浏览）、SIGTERM 处理、二次 Ctrl+C 的显式强制升级、退出码对 `_select_incomplete_session`/`_read_recovery_choice` 既有路径的改动（保持 Ticket 09 已验收行为）。

## Comments

- 2026-08-18：建立本票。现状：`_execute_attempt` 只捕获 `ModelCallError`/`Exception`，`KeyboardInterrupt` 沿调用栈逃逸并在解释器层打印 traceback；回合内中断时未完成回合壳已在模型调用前原子写盘，输入处中断时本回合尚未开始，两者都缺玩家可见提示与统一退出码。
- 2026-08-18：实现完成。CLI 层新增 `CliPlayerInterrupt` 与三条退出提示常量；`run_new_session_cli`、`_run_session_cli`（恢复执行、行动输入、回合执行三处）捕获 `KeyboardInterrupt` 后按存档真实状态提示并抛出，回合内路径用 `_interrupt_exit_message` 经只读 `get_session_state` 区分“未完成回合已保留”与“已提交回合已保存”；`_run_main` 将 `CliPlayerInterrupt` 收敛为退出码 130，并对未覆盖窗口的裸 `KeyboardInterrupt` 打印“已退出。”兜底；`main()` 的 `finally` 继续还原终端，启动窗口外的 Ctrl+C 同样收敛为 130。Harness 未改动：未完成回合的写盘时序与恢复语义保持 Ticket 07/09 验收行为。
- 2026-08-18：验证（解释器为项目 `.venv` 的 Python 3.12.3）：`PYTHONPATH=src .venv/bin/python -m unittest tests.test_agentic_cli`（23 passed，新增 5 个 seam 测试覆盖回合中/输入处/建局中断与 130 收敛）；`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`（332 passed）；`.venv/bin/mypy src/monmusu_agent/agentic_cli.py`、`.venv/bin/ruff check src/monmusu_agent/agentic_cli.py tests/test_agentic_cli.py` 与 `git diff --check` 均通过。按本票范围未运行真实 DeepSeek，也未做真实终端 SIGINT 冒烟。
