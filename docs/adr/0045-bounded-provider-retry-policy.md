# ADR-045：GM 请求采用 provider 有界重试策略

- 状态：Accepted
- 日期：2026-08-17
- 决策关系：依赖 [ADR-031](0031-deepseek-through-openai-sdk.md)、[ADR-036](0036-agent-loop-has-eight-round-trip-safety-fuse.md) 与 [ADR-037](0037-agent-loop-has-request-and-turn-deadlines.md)

## 背景

`ModelCallError.retryable` 从引入起从未被 Harness 消费；`_execute_attempt` 对限流、超时、断连、5xx 等可重试错误也立即中断当前执行尝试。场景三失败分析确认这是瞬时 provider 故障会直接终结 attempt 的结构性原因之一。

## 决策

Harness 在 `GameMasterModel.complete()` 调用边界执行 provider 有界重试，不依赖 OpenAI SDK 内置重试：

- SDK 客户端固定 `max_retries=0`，重试、退避与计数全部由 Harness 拥有。
- 重试策略属于 `model_profile` 的 `retry_policy` 字段，按回合冻结并随未完成回合恢复。
- 首版只支持 `mode: "normal"`；`max_retries` 表示首次请求之后的额外尝试上限。
- 默认策略为 normal 模式；为兼容既有测试与契约，第一阶段默认 `max_retries=0`，随后单独切换为 `2`。
- 可重试 code 为 `provider_rate_limited`、`provider_server_error`、`provider_network_error`、`request_timeout` 与 `provider_empty_response`。
- 本地退避采用有界指数退避：初始 500ms、上限 10s、10% 对称 jitter。合法且不超过上限的 provider `Retry-After` 直接采用。
- 失败请求不消耗 `round_trips_used`；重试等待与下一次请求必须落入当前 attempt 的 180 秒 deadline。
- 每次重试 sleep 前把调度状态写入 `IncompleteTurn.provider_retry`；落盘失败时以 `retry_state_persistence_failed` 中断，不再继续重试。
- 重试耗尽后保留最后一次 provider 错误码；只有 deadline 耗尽时转 `attempt_timeout`。
- 重试过程与 `provider_retry` 诊断对玩家不可见。

## 后果

- 单次执行尝试最多产生 `max_round_trips * (1 + max_retries)` 次 provider 请求，仍受 attempt deadline 约束。
- 旧 `agentic-mvp-2` 未完成回合在加载时补齐默认 `provider_retry`；不升级 schema version。
- 新错误码进入公开技术中断码集合，但仍不向玩家展示 provider 正文。
