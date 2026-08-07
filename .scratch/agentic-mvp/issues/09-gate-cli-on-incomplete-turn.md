# 09 — 用未完成回合门控 CLI

**What to build:** 让 opt-in Agentic CLI 在启动及接受下一条行动前通过 Harness 查询未完成回合。存在阻塞时，只展示玩家安全的技术状态和已提交公开机械，并要求玩家明确选择恢复或退出；启动、查看状态和退出都不能自动调用模型或改变存档。

Blocked by: 08 — 幂等重放已提交工具结果

Status: ready-for-human

## References

- [Parent spec](../spec.md): User Stories 23-27, 40-41 and 47; Implementation Decisions “Highest stable seams”, “CLI boundary” and “Explicit recovery”; recovery and player-authority/visibility acceptance gates.
- [Migration](../../../docs/agentic_mvp/migration.md): “增量 2：未完成回合与运行恢复”的 CLI 恢复/退出门和启动不自动调用模型要求。
- [Data contracts](../../../docs/agentic_mvp/contracts.md): `IncompleteTurn`, 运行配置、CLI 回合结果以及公开/隐藏投影边界。
- [Agent Loop](../../../docs/agentic_mvp/agent_loop.md): “非流式输出边界”, “故障处理”和“显式恢复时序”。
- [ADR-025](../../../docs/adr/0025-mechanics-commit-before-atomic-gm-response.md), [ADR-034](../../../docs/adr/0034-cli-is-the-only-mvp-player-interface.md), and [ADR-038](../../../docs/adr/0038-player-explicitly-resumes-incomplete-turn.md).

- [x] CLI 只通过 `AgenticHarness.get_session_state`、`start_turn` 和 `resume_turn` 工作；不直接读取 `session.json`、provider 消息、tool interactions、隐藏事实或私有诊断，也不复制 Harness 的恢复判定。
- [x] 启动只发现具有 `incomplete_turn` 的 Agentic 会话并让玩家选择；发现/展示本身不初始化新局、不发起模型请求、不自动恢复。最小发现能力留在现有本地 SessionStore 边界，不建设通用 session repository 或跨存储查询层。
- [x] 选定未完成会话后显示稳定 `turn_id` 的技术中断摘要和已提交公开机械，并只接受明确的“恢复”或“退出”；不能接受新的虚构行动、替换原输入或用菜单文案暗示未提交叙事已发生。
- [x] “恢复”把同一 `turn_id` 交给 Harness；新公开机械只在新提交后展示，最终 narration 只在原子 final 提交后展示，再次中断仍回到恢复/退出门。
- [x] “退出”只结束当前进程，保留未完成回合和全部机械；再次启动仍发现同一恢复入口。EOF、键盘中断或输入错误也不能清除状态或转成新游戏。
- [x] 当没有未完成回合时保留现有 opt-in 新游戏流程；本票不加入一般已完成/进行中历史 session 的选择和续玩，该正常会话 UX 留给 Increment 4。
- [x] 玩家输出过滤隐藏机械、隐藏事实、provider envelope、无效 final、reasoning content 和私有诊断；技术错误只展示稳定、可行动的恢复状态，不打印 API key、鉴权头或原始 provider 异常。
- [x] CLI 端到端测试使用真实临时 session 目录、输入/输出替身和会记录请求的假 model，覆盖启动发现、恢复、再次中断、退出、EOF/键盘中断、恢复成功后接收下一行动，以及隐藏内容过滤；明确断言发现和退出时 model request 数为零。

**Not in this ticket:** 默认入口切换、一般 session browser、自动恢复、删除存档、正常历史会话续玩、thinking transport、真实网络验证或 Increment 4 内容整合。

## Comments

- 2026-08-07：Ticket 09 已完成。`AgenticSessionStore.find_incomplete_session_ids()` 只读枚举并完整校验已发布本地会话，只返回具有 `incomplete_turn` 的稳定游戏 ID；CLI 选择后仅通过 `SessionLifecycleView` 展示 `turn_id`、已提交公开机械和安全技术摘要，不读取聚合内部恢复材料。
- 2026-08-07：`run_agentic_cli()` 在新建会话前优先门控全部未完成会话；共享 CLI 生命周期循环在每次恢复或准备接收下一行动前查询 Harness。动作文本、无效编号、EOF、键盘中断与退出不会发起模型请求或改变存档；再次中断保留同一门，成功 final 提交后才解锁下一行动。恢复调用的公开 mechanics sink 只发布本次新提交事件，重启投影与新事件不会重复显示。
- 2026-08-07：公开 CLI seam 测试使用真实临时 session、确定性 RNG 与可记录请求的假 model，覆盖多 blocker 发现/选择、零调用退出、无效输入与进程中断、同 `turn_id` 连续恢复、新机械单次展示、恢复后下一行动、隐藏事实/机械/provider/无效 final/reasoning/凭据样式文本过滤，以及无 blocker 的新游戏回归。
- 2026-08-07：双轴复核发现冻结 `model_profile` 不可用时 Harness 会在模型调用前抛出 `AgenticTurnBlockedError`；CLI 现将该生命周期拒绝收敛为固定 `recovery_unavailable` 状态，保留 blocker 并回到恢复/退出门，新增 CLI 回归测试证明不泄露异常且 model request 数为零。复核同时移除单调用的恢复转发函数；Ticket 状态采用 tracker 规范的 `ready-for-human`，因为 triage-labels 未定义 `done`。
- 验证：`PYTHONPATH=src .venv/bin/python -m unittest tests.test_agentic_cli tests.test_agentic_harness tests.test_agentic_session`（85 passed）；`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`（184 passed）；`.venv/bin/mypy src/monmusu_agent/agentic_cli.py src/monmusu_agent/agentic_harness.py src/monmusu_agent/agentic_session.py src/monmusu_agent/agentic_model.py`、`PYTHONPATH=src .venv/bin/python -m compileall -q src tests`、targeted Ruff 基础规则与 `git diff --check` 均通过。验证解释器为项目 `.venv` 的 Python 3.12.3；本票按范围未运行真实 DeepSeek。
