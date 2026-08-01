# 04 — 通过显式运行配置接入 DeepSeek Chat Completions

**What to build:** 新版组合入口可以把同一个 GM Loop 从可编程假 adapter 切换到一个薄的 `DeepSeekGameMasterModel`。本票完成真实 SDK 请求转换、响应 envelope 保留、错误映射、离线 adapter 验证和可显式启用的真实契约 runner；真实 key 的非跳过执行及验收证据由 Ticket 05 完成，从而保持本票可由 agent 独立交付。

Blocked by: 03 — 在同一 GM Loop 中执行并持久化 `make_check`

Status: done

## References

- [Parent spec](../spec.md): User Stories 39, 41-42 and 50; Implementation Decisions “Highest stable seams”, “Provider adapter”, “Context assembly”, “Security and privacy”, and the vertical-slice/testing decisions for provider evidence.
- [Migration](../../../docs/agentic_mvp/migration.md): “增量 1：真实 DeepSeek 最小纵向切片”的 adapter/配置范围、验收门和凭据边界。
- [Data contracts](../../../docs/agentic_mvp/contracts.md): “`GameMasterModel` seam”, “运行配置”, “CLI 回合结果”和“纵向交付子集”。
- [Evaluation](../../../docs/agentic_mvp/evaluation.md): “真实 DeepSeek 契约测试”中的凭据与网络边界及必测协议；证据输出统一使用“评估记录格式”。
- [ADR-031](../../../docs/adr/0031-deepseek-through-openai-sdk.md), [ADR-033](../../../docs/adr/0033-evaluate-deepseek-model-profile.md), [ADR-035](../../../docs/adr/0035-first-cli-does-not-stream-model-output.md), and [ADR-039](../../../docs/adr/0039-validation-has-deterministic-and-live-deepseek-lanes.md).

- [x] `DeepSeekGameMasterModel` 使用 OpenAI Python SDK 的 Chat Completions 与 DeepSeek 官方 base URL；不使用 Responses API、Beta strict mode、多 provider 注册表、自动路由、模型自动切换或逐回合选型。
- [x] 模型 ID 与 thinking 来自显式运行配置；首个协议基线为 `deepseek-v4-flash`、`thinking=false`，Increment 1 在发起请求前明确拒绝 `thinking=true`。MVP 固定 `stream=false`，不提供 streaming 配置能力，也不向 CLI 流式转发未验证模型内容。
- [x] adapter 只把已组装请求转换为 provider 消息、保留可序列化的原始 assistant response envelope 和顺序，并把鉴权、限流、网络及 provider 故障映射为稳定错误；它不判断 final/tool call、不校验 `tool_call_id`、不执行工具，也不提交机械、事实或回合。
- [x] Harness 对真实 adapter 与假 adapter 使用同一个 `GameMasterModel` seam，并继续负责 final、单工具和协议错误分类、ID 校验、本地业务 schema 校验、工具执行以及所有状态提交。
- [x] API key 只由组合入口注入 adapter；外部运行环境可以把 key 交给组合入口，但核心与 adapter 都不自行决定如何获取或保存。key 不属于运行配置或核心领域数据，不被持久化、记录、快照化或包含在普通/失败输出中，错误映射也不会暴露鉴权头、完整客户端对象或凭据片段。
- [x] provider envelope、finish reason 和私有诊断只用于受限协议/中断材料及脱敏证据，不进入玩家输出、事实账本、`CommittedTurn` 或可信 GM 游戏记录；已提交游戏记录和后续 GM 正典上下文不携带 provider 诊断。
- [x] 提供显式启用的真实契约 runner：在外部 key 可用时，它能验证同一 `deepseek-v4-flash` non-thinking 请求配置同时携带 function tools 与 JSON Object response format，并分别覆盖通过本地校验的直接 final，以及 tool call、匹配 `tool_call_id` 的 `role="tool"` 回传和后续 final；runner 不对模型固定措辞作断言。
- [x] 契约 runner 只生成 [Evaluation 的“评估记录格式”](../../../docs/agentic_mvp/evaluation.md)定义的脱敏证据，不在本票另建字段 schema；usage 缺失时明确记为缺失，不猜测 token 或成本，且输出过滤 key、鉴权头、隐藏事实正文、provider hidden reasoning 和私有诊断。
- [x] 无 key 的日常运行明确 skip 真实调用；本票的完成证据是 adapter/组合接线、mocked-SDK 边界测试和 runner 自身的离线测试。真实 key 下的非跳过契约运行及其证据是 Ticket 05 的验收前置，不把 skip 视为真实协议通过。

**Not in this ticket:** thinking 模式支持、streaming、请求/尝试超时、结构 repair、完整 provider 异常矩阵、模型质量选择或通用 provider 框架。

## Comments

- 2026-07-29：提交 `d1ac0df` 接入 OpenAI SDK 1.109.1、DeepSeek Chat Completions adapter、显式模型配置与 key 组合边界、mocked-SDK/Harness 生命周期测试及可跳过的脱敏契约 runner。提交 `901b666` 修复复审问题：白名单过滤官方 usage 及嵌套明细、未知 SDK 故障稳定映射、从实际 SDK 参数记录 tools/JSON Object/stream 证据，并让新版 CLI 在同一 Harness/会话中持续运行到 `complete` 或技术中断；确定性测试证明第二回合请求收到第一回合新建事实。
- 双轴复审：以 `2fb09c7` 为固定点审查实现提交与最终 worktree。Standards 与 Spec 均无剩余硬性问题；保留的非阻塞判断项是 runner 与 Harness 各自需要一份不同权限的 tool-call 脱敏/验证逻辑，后续若收敛应继续由 Harness 保有分类权威。
- 验证：`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_agentic_*.py'`（63 passed）；`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`（148 passed）；`PYTHONPATH=src .venv/bin/python -m compileall -q src tests`、4 个目标源码 mypy、变更文件 Ruff 和 `git diff --check` 均通过。解释器为 `.venv/bin/python` 3.12.3，OpenAI SDK 为 1.109.1。
- 离线入口 `monmusu-agent-deepseek-contract` 在未显式启用时输出 `SKIP: DeepSeek contract runner was not explicitly enabled`。本票没有读取或运行真实 DeepSeek key，也没有产生真实 provider 或人工因果证据；这些仍是 Ticket 05 的验收前置。
