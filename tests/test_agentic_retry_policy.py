import unittest
from typing import Any, Mapping

from monmusu_agent.agentic_retry import (
    DEFAULT_INITIAL_DELAY_MS,
    DEFAULT_JITTER_RATIO,
    DEFAULT_MAX_DELAY_MS,
    DEFAULT_MAX_RETRIES,
    RetryPolicyValidationError,
    local_retry_delay_ms,
    resolve_retry_policy,
    scheduled_retry_delay_ms,
)


class RetryPolicyTest(unittest.TestCase):
    def test_default_policy_is_normal_with_two_retries_and_backoff_defaults(self) -> None:
        policy = resolve_retry_policy(None)

        self.assertEqual(DEFAULT_MAX_RETRIES, 2)
        self.assertEqual(policy.mode, "normal")
        self.assertEqual(policy.max_retries, 2)
        self.assertEqual(
            policy.retryable_codes,
            (
                "provider_empty_response",
                "provider_network_error",
                "provider_rate_limited",
                "provider_server_error",
                "request_timeout",
            ),
        )
        self.assertEqual(policy.initial_delay_ms, DEFAULT_INITIAL_DELAY_MS)
        self.assertEqual(policy.max_delay_ms, DEFAULT_MAX_DELAY_MS)
        self.assertEqual(policy.jitter_ratio, float(DEFAULT_JITTER_RATIO))

    def test_config_requires_normal_mode_and_resolves_defaults(self) -> None:
        policy = resolve_retry_policy({"mode": "normal", "max_retries": 4})

        self.assertEqual(policy.max_retries, 4)
        self.assertEqual(policy.initial_delay_ms, DEFAULT_INITIAL_DELAY_MS)

    def test_config_rejects_missing_or_unknown_mode(self) -> None:
        with self.assertRaises(RetryPolicyValidationError):
            resolve_retry_policy({"max_retries": 1})
        with self.assertRaises(RetryPolicyValidationError):
            resolve_retry_policy({"mode": "always"})

    def test_config_rejects_unknown_keys_codes_and_bounds(self) -> None:
        invalid_configs: tuple[Mapping[str, Any], ...] = (
            {"mode": "normal", "unknown": True},
            {"mode": "normal", "retryable_codes": []},
            {"mode": "normal", "retryable_codes": ["provider_error"]},
            {"mode": "normal", "retryable_codes": ["request_timeout", "request_timeout"]},
            {"mode": "normal", "max_retries": -1},
            {
                "mode": "normal",
                "backoff": {"initial_delay_ms": 10_000, "max_delay_ms": 10},
            },
            {
                "mode": "normal",
                "backoff": {"initial_delay_ms": 500, "jitter_ratio": 2},
            },
        )
        for config in invalid_configs:
            with self.subTest(config=config):
                with self.assertRaises(RetryPolicyValidationError):
                    resolve_retry_policy(config)

    def test_local_backoff_is_exponential_bounded_and_jittered(self) -> None:
        policy = resolve_retry_policy(
            {
                "mode": "normal",
                "backoff": {
                    "initial_delay_ms": 500,
                    "max_delay_ms": 600,
                    "jitter_ratio": 0,
                },
            }
        )

        self.assertEqual(local_retry_delay_ms(policy, 1, lambda: 0.5), 500)
        self.assertEqual(local_retry_delay_ms(policy, 2, lambda: 0.5), 600)
        jittered = resolve_retry_policy(
            {
                "mode": "normal",
                "backoff": {
                    "initial_delay_ms": 500,
                    "max_delay_ms": 10_000,
                    "jitter_ratio": 0.1,
                },
            }
        )
        self.assertEqual(local_retry_delay_ms(jittered, 1, lambda: 0.0), 450)
        self.assertEqual(local_retry_delay_ms(jittered, 1, lambda: 1.0), 550)

    def test_scheduled_delay_honors_or_rejects_retry_after(self) -> None:
        policy = resolve_retry_policy(
            {
                "mode": "normal",
                "backoff": {
                    "initial_delay_ms": 500,
                    "max_delay_ms": 10_000,
                    "jitter_ratio": 0,
                },
            }
        )

        self.assertEqual(scheduled_retry_delay_ms(policy, 1, 2_000, lambda: 0.0), 2_000)
        self.assertIsNone(scheduled_retry_delay_ms(policy, 1, 10_001, lambda: 0.0))
        self.assertEqual(scheduled_retry_delay_ms(policy, 1, None, lambda: 0.0), 500)


if __name__ == "__main__":
    unittest.main()
