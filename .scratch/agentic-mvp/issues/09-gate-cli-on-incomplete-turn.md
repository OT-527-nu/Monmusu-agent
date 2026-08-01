# 09 — 用未完成回合门控 CLI

**What to build:** 让 opt-in Agentic CLI 在启动及接受下一条行动前通过 Harness 查询未完成回合。存在阻塞时，只展示玩家安全的技术状态和已提交公开机械，并要求玩家明确选择恢复或退出；启动、查看状态和退出都不能自动调用模型或改变存档。

Blocked by: 08 — 幂等重放已提交工具结果

Status: ready-for-agent

## References

- [Parent spec](../spec.md): User Stories 23-27, 40-41 and 47; Implementation Decisions “Highest stable seams”, “CLI boundary” and “Explicit recovery”; recovery and player-authority/visibility acceptance gates.
- [Migration](../../../docs/agentic_mvp/migration.md): “增量 2：未完成回合与运行恢复”的 CLI 恢复/退出门和启动不自动调用模型要求。
- [Data contracts](../../../docs/agentic_mvp/contracts.md): `IncompleteTurn`, 运行配置、CLI 回合结果以及公开/隐藏投影边界。
- [Agent Loop](../../../docs/agentic_mvp/agent_loop.md): “非流式输出边界”, “故障处理”和“显式恢复时序”。
- [ADR-025](../../../docs/adr/0025-mechanics-commit-before-atomic-gm-response.md), [ADR-034](../../../docs/adr/0034-cli-is-the-only-mvp-player-interface.md), and [ADR-038](../../../docs/adr/0038-player-explicitly-resumes-incomplete-turn.md).

- [ ] CLI 只通过 `AgenticHarness.get_session_state`、`start_turn` 和 `resume_turn` 工作；不直接读取 `session.json`、provider 消息、tool interactions、隐藏事实或私有诊断，也不复制 Harness 的恢复判定。
- [ ] 启动只发现具有 `incomplete_turn` 的 Agentic 会话并让玩家选择；发现/展示本身不初始化新局、不发起模型请求、不自动恢复。最小发现能力留在现有本地 SessionStore 边界，不建设通用 session repository 或跨存储查询层。
- [ ] 选定未完成会话后显示稳定 `turn_id` 的技术中断摘要和已提交公开机械，并只接受明确的“恢复”或“退出”；不能接受新的虚构行动、替换原输入或用菜单文案暗示未提交叙事已发生。
- [ ] “恢复”把同一 `turn_id` 交给 Harness；新公开机械只在新提交后展示，最终 narration 只在原子 final 提交后展示，再次中断仍回到恢复/退出门。
- [ ] “退出”只结束当前进程，保留未完成回合和全部机械；再次启动仍发现同一恢复入口。EOF、键盘中断或输入错误也不能清除状态或转成新游戏。
- [ ] 当没有未完成回合时保留现有 opt-in 新游戏流程；本票不加入一般已完成/进行中历史 session 的选择和续玩，该正常会话 UX 留给 Increment 4。
- [ ] 玩家输出过滤隐藏机械、隐藏事实、provider envelope、无效 final、reasoning content 和私有诊断；技术错误只展示稳定、可行动的恢复状态，不打印 API key、鉴权头或原始 provider 异常。
- [ ] CLI 端到端测试使用真实临时 session 目录、输入/输出替身和会记录请求的假 model，覆盖启动发现、恢复、再次中断、退出、EOF/键盘中断、恢复成功后接收下一行动，以及隐藏内容过滤；明确断言发现和退出时 model request 数为零。

**Not in this ticket:** 默认入口切换、一般 session browser、自动恢复、删除存档、正常历史会话续玩、thinking transport、真实网络验证或 Increment 4 内容整合。
