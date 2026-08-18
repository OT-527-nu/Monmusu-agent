# 真实 DeepSeek provider 重试观察证据

- 证据日期：2026-08-18
- 代码基线：`32a58e4`（`feat/agentic-retry`）
- 运行方式：`MONMUSU_RUN_DEEPSEEK_RETRY_CONTRACT=1 .venv/bin/python -m monmusu_agent.agentic_contract`
- 结果：`passed`

## 结论

在真实 `api.deepseek.com` 上完成一次 **provider 重试观察契约**：

1. 第一跳在本地 `GameMasterModel` seam 注入一次可重试 `provider_server_error`，携带 `status=503` 与 `provider_retry_after_ms=1000`。
2. Harness 在 sleep 前把 `IncompleteTurn.provider_retry` 落盘。
3. 等待 1 秒后，第二跳通过真实 `DeepSeekGameMasterModel` 发送同一请求。
4. DeepSeek 返回合法 direct-final；Harness 本地校验并原子提交完整回合。
5. 最终 `incomplete_turn` 清空，回合记录数量为 1。

## 观测记录

```json
{
  "status": "passed",
  "reason": "real DeepSeek turn committed after one locally injected transient failure, one observed retry, and zero or more real continuation requests",
  "records": [
    {
      "version": "retry-live-observation-v1",
      "scenario": "ticket-04-direct-final-v1",
      "expected_path": "direct_final",
      "provider": "deepseek",
      "model_id": "deepseek-v4-flash",
      "retry_policy": {
        "mode": "normal",
        "max_retries": 2,
        "retryable_codes": [
          "provider_empty_response",
          "provider_network_error",
          "provider_rate_limited",
          "provider_server_error",
          "request_timeout"
        ],
        "backoff": {
          "initial_delay_ms": 500,
          "max_delay_ms": 10000,
          "jitter_ratio": 0.0
        }
      },
      "turn_status": "committed",
      "turn_id": "turn_f7ceb02b3abc437d9decabe45e476bd3",
      "request_attempts": 2,
      "sdk_requests": 1,
      "first_attempt_local_error_category": "provider_server_error",
      "second_attempt_finish_reason": "stop",
      "last_attempt_finish_reason": "stop",
      "scheduled_retry_snapshots": [
        {
          "retries_used": 1,
          "total_retries": 1,
          "last_retry": {
            "code": "provider_server_error",
            "message": "retry observation injected transient server failure",
            "delay_ms": 1000.0,
            "scheduled_at": "2026-08-18T04:12:04Z",
            "status": 503,
            "request_id": null
          }
        }
      ],
      "sleeps": [
        {
          "scheduled_seconds": 1.0,
          "observed_seconds": 1.0
        }
      ],
      "final_incomplete_turn_is_none": true,
      "committed_turns": 1,
      "first_scheduled_retry": {
        "code": "provider_server_error",
        "delay_ms": 1000.0,
        "status": 503
      }
    }
  ]
}
```

## 验证要点

- 第一次失败请求不计入 `round_trips_used`；最终仍只提交一个回合。
- 重试状态在 sleep 前持久化，`retries_used=1`、`total_retries=1`，`last_retry.delay_ms` 采用 provider `Retry-After`。
- sleep 实际耗时与调度值一致（1.0s）。
- 重试后的真实 SDK 请求只有一条 evidence；注入失败发生在 SDK 边界之前，不产生 provider 请求。
- 记录不含 API key、Authorization、模型 reasoning 正文或 provider 诊断原文。

## 边界说明

- 本契约在 **本地模型 seam** 注入故障，用来稳定观察真实 provider 的恢复路径；它不能制造 DeepSeek 真实 429/5xx。
- adapter 对真实 HTTP 状态码、`Retry-After` 和空响应的分类由 fake SDK 确定性测试覆盖（`tests/test_agentic_deepseek.py`、`tests/test_agentic_retry_policy.py`）。
- 重试后的真实响应是 direct-final；同一 runner 也接受真实模型先调用工具、再继续并提交的路径。
