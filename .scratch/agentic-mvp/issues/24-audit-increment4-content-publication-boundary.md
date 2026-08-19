# 24 — 审计并冻结增量四内容发布与验收边界

**What to build:** 在 session 浏览和普通续玩完成后，审查 Agentic MVP 的参考资料 provenance、快照、上下文组装和 Prompt revision 边界，并把项目文档同步到已经确认的增量四范围。若审计发现真实缺口，只增加最小确定性回归测试或必要修复；不重复建设已有快照能力。

**Blocked by:** 23 — 实现 Agentic CLI 的 session 选择与普通续玩

**Status:** ready-for-human

- [x] 审计并用确定性测试证明：新 session 保存模组/角色 revision 与实际 SHA-256，session-local 快照保持不变，工作树后续修改不会改变已有 session 的 GM 上下文。
- [x] 审计并用确定性测试证明：完整已提交回合、当前事实索引、模组参考书和角色资料进入 ongoing 新回合上下文；Prompt 使用运行级 `PROMPT_REVISION`，不新增 per-session Prompt 快照或内容 manifest。
- [x] 记录内容发布纪律：模组、角色或 GM Prompt 发生实质修改时，维护者主动递增相应 revision；hash 负责精确内容识别，revision 负责人工可读的发布标识。
- [x] 更新权威迁移说明，使六个聚焦场景和真实场景 runner 统一留给增量五；增量四的工程完成条件与项目所有者后续人工试玩的项目验收条件明确分开，同时不改写 ADR-041/ADR-042 对增量五评估矩阵的要求。
- [x] 文档和测试明确：Ticket 22–24 通过不等于真实 GM 质量通过；本票不运行聚焦场景、开放试玩、真实 provider、72 次矩阵或模型选择。
- [x] Agent 不修改模组参考书、角色资料或 GM Prompt 的语义内容；内容打磨、revision 发布和最终人工试玩由项目所有者完成。
- [x] 运行全量确定性测试、编译检查、目标静态检查和差异检查，并在结果中列出任何未验证的真实试玩风险。

**Not in this ticket:** 新建模组加载器、Prompt per-session 持久化、内容 manifest、真实场景证据 runner、模型评估、默认入口切换或旧路径清理。

## Comments

- 2026-08-19：完成增量四内容发布边界审计。新增 session provenance 回归测试，独立计算源文件 SHA-256 并逐字节核对 session-local 快照；新增 ongoing 上下文回归断言，覆盖完整 `COMMITTED_TURNS`、`ACTIVE_FACTS`、模组/角色快照和运行级 `PROMPT_REVISION`，并确认 session 不保存 per-session Prompt 快照或 content manifest。未修改模组参考书、角色资料或 GM Prompt 语义内容。
- 2026-08-19：更新 `docs/agentic_mvp/migration.md`，明确 revision/hash 发布纪律，区分增量四工程证据与增量五真实质量验收，并保留 ADR-041/ADR-042 的六场景、72 次矩阵和开放试玩要求。Ticket 22–24 通过不等于真实 GM 质量通过。
- 2026-08-19：验证环境为项目 `.venv`（Python 3.12.3）。`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`：340 passed；`PYTHONPATH=src .venv/bin/python -m compileall -q src tests`：通过；`PYTHONPATH=src .venv/bin/python -m mypy src/monmusu_agent/agentic_cli.py src/monmusu_agent/agentic_session.py`：通过；`PYTHONPATH=src .venv/bin/python -m ruff check src/monmusu_agent/agentic_cli.py src/monmusu_agent/agentic_session.py tests/test_agentic_cli.py tests/test_agentic_session.py tests/test_agentic_harness.py`：通过；`git diff --check`：通过。`tests.test_agentic_session tests.test_agentic_harness`：155 passed。未验证风险仍是 Ticket 24 明确延期的真实 provider、六场景 runner、开放人工试玩、72 次矩阵和模型选择；因此本票不宣称真实 GM 质量通过。
