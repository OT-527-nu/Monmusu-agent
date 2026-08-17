# 20 — 接入 OpenCode Go provider 并证明真实协议

**What to build:** 在 Ticket 19 的 provider 配置 seam 上，允许 `MONMUSU_PROVIDER=opencode-go` 使用 `https://opencode.ai/zen/go/v1` 调用 `deepseek-v4-flash`。首版只支持 non-thinking，并沿用 DeepSeek 式 `thinking` 请求字段和 `reasoning_content` 回放要求。确定性测试证明 SDK 请求形状与脱敏，一次真实契约证明 direct final 与 `make_check` 工具往返在 opencode-go 网关上成立。

**Blocked by:** 19 — 配置 Agentic CLI 的模型提供商

**Status:** ready-for-human

## References

- [Parent spec](../spec.md): User Stories 42 and 50; Implementation Decisions “Provider adapter” and “Security and privacy”; deterministic/live evidence separation.
- [Migration](../../../docs/agentic_mvp/migration.md): `agentic_model.py` 的薄 provider seam 与旧路径边界。
- [Data contracts](../../../docs/agentic_mvp/contracts.md): `model_profile`、`IncompleteTurn` 与 GM 上下文回放。
- [Agent Loop](../../../docs/agentic_mvp/agent_loop.md): “模型响应协议”和“显式恢复时序”。
- [Evaluation](../../../docs/agentic_mvp/evaluation.md): “真实 DeepSeek 契约测试”与脱敏评估记录格式。
- [ADR-031](../../../docs/adr/0031-deepseek-through-openai-sdk.md), [ADR-035](../../../docs/adr/0035-first-cli-does-not-stream-model-output.md), and [ADR-038](../../../docs/adr/0038-player-explicitly-resumes-incomplete-turn.md).

- [x] `DeepSeekGameMasterModel` 接受 provider 对应的 base_url，并在 opencode-go profile 下构造 OpenAI SDK 请求：`base_url=https://opencode.ai/zen/go/v1`、`model=deepseek-v4-flash`、`stream=false`、`max_tokens` 与现有 profile 一致。真实协议发现 opencode-go 在 SDK `response_format=json_object` 下不返回原生 `tool_calls`，因此对 opencode-go 省略 SDK `response_format`；`model_profile.response_format=json_object` 仍保留为本地最终答复契约。
- [x] opencode-go 首版只允许 `thinking=false`。基于 dsh/pi-ai catalog 中 opencode-go `deepseek-v4-flash` 的 `thinkingFormat: deepseek` 事实，请求继续发送 `extra_body={"thinking":{"type":"disabled"}}`；设置 `thinking=true` 时在模型调用前返回稳定配置错误。
- [x] opencode-go 的 assistant 回放消息必须携带 `reasoning_content` 字段：provider 返回 `null`、缺失或使用 wire 字段 `reasoning` 时，adapter 统一归一为 `reasoning_content`，非字符串时补空字符串后再回传；不把 reasoning 正文写入玩家记录、事实账本、普通日志或公开证据。该处理只适用于 opencode-go；DeepSeek 官方路径保持现有行为。
- [x] mocked SDK 确定性测试覆盖 opencode-go 请求 URL、`model`、tools、省略 SDK `response_format`、`thinking: disabled`、`reasoning`/`reasoning_content` 归一与回放、usage 脱敏、鉴权/网络/限流错误映射，以及 key 不进入任何投影。
- [x] opencode-go profile 的新回合默认只暴露 `deepseek-v4-flash`；未知或非 DeepSeek 模型在 Harness 调用前被拒绝，不建设模型能力表或自动选型。恢复继续使用冻结 profile 的 provider、model_id 与 base_url。
- [x] 一次真实 opencode-go 契约由项目所有者提供 key 并显式启用时运行：direct final 与一次匹配 `tool_call_id` 的 `make_check` 往返都非跳过完成；无 key 时明确 skip，不得视为通过。
- [x] 真实契约按现有评估格式记录 provider、model、thinking、stream、工具 schema、prompt/profile、timeout、usage、latency、脱敏请求投影和硬门结论；不保存 key、鉴权头、reasoning 正文、隐藏事实或原始 provider envelope。
- [x] live 证据写入 `docs/agentic_mvp/evidence/`，与 DeepSeek 官方证据分开；本票只证明 opencode-go 的 SDK/协议可用，不评分 GM 质量、不进入模型评估矩阵、不宣称 opencode-go 成为默认 provider。
- [x] 现有 DeepSeek 官方确定性测试、恢复测试和静态门禁保持通过；默认 provider 仍为 `deepseek`，不增加运行时 fallback 或自动路由。

**Not in this ticket:** opencode-go thinking=true、其他模型、其他 OpenAI-compatible 网关的质量评估、六场景矩阵、完整试玩、默认入口切换、旧路径清理、一般 session browser。

## Comments

- 2026-08-17：dsh 本身未硬编码 opencode-go 请求细节；其依赖 `@earendil-works/pi-ai@0.82.1` 的 catalog 将 opencode-go `deepseek-v4-flash` 标为 `api=openai-completions`、`baseUrl=https://opencode.ai/zen/go/v1`、`thinkingFormat=deepseek`、`requiresReasoningContentOnAssistantMessages=true`。本票据此恢复发送 DeepSeek 式 `thinking` 字段，并要求 assistant 回放补空 `reasoning_content`。
- 2026-08-17：按 TDD 实现。先新增 `tests/test_agentic_opencode_go.py` 并在实现前确认失败；随后加入 opencode-go profile 限制、省略 SDK `response_format`、`reasoning -> reasoning_content` wire 归一、适配 contract runner 的 opencode-go 场景与宽松单工具门。真实运行发现 `response_format=json_object` 会抑制 opencode-go 原生 `tool_calls`，故按证据调整 SDK 请求形状。
- 2026-08-17：验证通过：`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`（311 tests OK）；`uv run ruff check` 通过；`uv run mypy src` 通过；`git diff --check` 通过。真实 opencode-go 契约 `MONMUSU_RUN_OPENCODE_GO_CONTRACT=1` 为 `passed`，direct-final 与 tool-then-final 均非跳过完成；脱敏证据见 `docs/agentic_mvp/evidence/ticket-20-opencode-go-live-2026-08-17.md`。
