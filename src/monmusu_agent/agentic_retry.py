"""Provider 请求重试策略的纯函数模块。

策略定义、解析和退避计算不接触 session、模型或线程；Harness 负责执行与落盘。
"""

from __future__ import annotations

import random as _random
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping

DEFAULT_MAX_RETRIES = 2
DEFAULT_INITIAL_DELAY_MS = 500
DEFAULT_MAX_DELAY_MS = 10_000
DEFAULT_JITTER_RATIO = 0.1
MAX_RETRY_BOUND = 1_000_000
MAX_DELAY_BOUND_MS = 1_000_000

KNOWN_RETRYABLE_CODES = frozenset(
    {
        "provider_empty_response",
        "provider_network_error",
        "provider_rate_limited",
        "provider_server_error",
        "request_timeout",
    }
)
DEFAULT_RETRYABLE_CODES = tuple(sorted(KNOWN_RETRYABLE_CODES))

_NORMAL_KEYS = frozenset({"mode", "max_retries", "retryable_codes", "backoff"})
_BACKOFF_KEYS = frozenset(
    {"initial_delay_ms", "max_delay_ms", "jitter_ratio"}
)


class RetryPolicyValidationError(ValueError):
    """表示 retry_policy 不能形成精确、安全的运行配置。"""


@dataclass(frozen=True)
class ResolvedRetryPolicy:
    """解析后、可安全冻结进 model_profile 的重试策略。"""

    mode: Literal["normal"]
    max_retries: int
    retryable_codes: tuple[str, ...]
    initial_delay_ms: int
    max_delay_ms: int
    jitter_ratio: float

    def as_profile(self) -> dict[str, Any]:
        """返回严格 JSON 形状，供 model_profile 持久化。"""

        return {
            "mode": self.mode,
            "max_retries": self.max_retries,
            "retryable_codes": list(self.retryable_codes),
            "backoff": {
                "initial_delay_ms": self.initial_delay_ms,
                "max_delay_ms": self.max_delay_ms,
                "jitter_ratio": self.jitter_ratio,
            },
        }


def _validated_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RetryPolicyValidationError(f"{label} 必须是映射")
    return value


def _unknown_keys(
    value: Mapping[str, Any],
    allowed: frozenset[str],
    label: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RetryPolicyValidationError(
            f"{label} 包含未知字段 {unknown[0]!r}"
        )


def _bounded_int(
    value: object,
    *,
    minimum: int,
    maximum: int,
    label: str,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        raise RetryPolicyValidationError(f"{label} 格式无效")
    return value


def _bounded_delay_ms(value: object, label: str) -> int:
    return _bounded_int(
        value,
        minimum=1,
        maximum=MAX_DELAY_BOUND_MS,
        label=label,
    )


def _validated_jitter(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RetryPolicyValidationError(f"{label} 格式无效")
    ratio = float(value)
    if ratio != ratio or ratio < 0.0 or ratio > 1.0:
        raise RetryPolicyValidationError(f"{label} 格式无效")
    return ratio


def resolve_retry_policy(
    config: Mapping[str, Any] | None,
) -> ResolvedRetryPolicy:
    """校验并解析一个 retry_policy 原始配置。

    省略整个配置时使用默认策略；提供配置时 ``mode`` 必须显式为 normal。
    """

    if config is None:
        return ResolvedRetryPolicy(
            mode="normal",
            max_retries=DEFAULT_MAX_RETRIES,
            retryable_codes=DEFAULT_RETRYABLE_CODES,
            initial_delay_ms=DEFAULT_INITIAL_DELAY_MS,
            max_delay_ms=DEFAULT_MAX_DELAY_MS,
            jitter_ratio=float(DEFAULT_JITTER_RATIO),
        )

    policy = _validated_mapping(config, "retry_policy")
    _unknown_keys(policy, _NORMAL_KEYS, "retry_policy")
    if "mode" not in policy:
        raise RetryPolicyValidationError("retry_policy.mode 必须显式提供")
    if policy.get("mode") != "normal":
        raise RetryPolicyValidationError(
            'retry_policy.mode 当前只支持 "normal"'
        )

    max_retries = _bounded_int(
        policy.get("max_retries", DEFAULT_MAX_RETRIES),
        minimum=0,
        maximum=MAX_RETRY_BOUND,
        label="retry_policy.max_retries",
    )

    retryable_codes_value = policy.get(
        "retryable_codes",
        list(DEFAULT_RETRYABLE_CODES),
    )
    if not isinstance(retryable_codes_value, list):
        raise RetryPolicyValidationError(
            "retry_policy.retryable_codes 必须是数组"
        )
    if not retryable_codes_value:
        raise RetryPolicyValidationError(
            "retry_policy.retryable_codes 不能为空"
        )
    retryable_codes: list[str] = []
    for code in retryable_codes_value:
        if not isinstance(code, str) or not code or code != code.strip():
            raise RetryPolicyValidationError(
                "retry_policy.retryable_codes 只能包含非空字符串"
            )
        if code not in KNOWN_RETRYABLE_CODES:
            raise RetryPolicyValidationError(
                f"retry_policy.retryable_codes 不支持 {code!r}"
            )
        retryable_codes.append(code)
    if len(set(retryable_codes)) != len(retryable_codes):
        raise RetryPolicyValidationError(
            "retry_policy.retryable_codes 不能包含重复项"
        )

    raw_backoff = policy.get("backoff", {})
    backoff = _validated_mapping(raw_backoff, "retry_policy.backoff")
    _unknown_keys(backoff, _BACKOFF_KEYS, "retry_policy.backoff")
    initial_delay_ms = _bounded_delay_ms(
        backoff.get("initial_delay_ms", DEFAULT_INITIAL_DELAY_MS),
        "retry_policy.backoff.initial_delay_ms",
    )
    max_delay_ms = _bounded_delay_ms(
        backoff.get("max_delay_ms", DEFAULT_MAX_DELAY_MS),
        "retry_policy.backoff.max_delay_ms",
    )
    if initial_delay_ms > max_delay_ms:
        raise RetryPolicyValidationError(
            "retry_policy.backoff.initial_delay_ms 不能大于 max_delay_ms"
        )
    jitter_ratio = _validated_jitter(
        backoff.get("jitter_ratio", DEFAULT_JITTER_RATIO),
        "retry_policy.backoff.jitter_ratio",
    )

    return ResolvedRetryPolicy(
        mode="normal",
        max_retries=max_retries,
        retryable_codes=tuple(sorted(retryable_codes)),
        initial_delay_ms=initial_delay_ms,
        max_delay_ms=max_delay_ms,
        jitter_ratio=jitter_ratio,
    )


def local_retry_delay_ms(
    policy: ResolvedRetryPolicy,
    retry_number: int,
    random_value: Callable[[], float],
) -> float:
    """计算第 ``retry_number`` 次重试的本地有界指数退避毫秒数。"""

    if retry_number < 1:
        raise ValueError("retry_number 必须从 1 开始")
    exponent = min(retry_number - 1, 1024)
    exponential = min(
        policy.initial_delay_ms * 2**exponent,
        policy.max_delay_ms,
    )
    jitter = (
        1
        - policy.jitter_ratio
        + 2 * policy.jitter_ratio * random_value()
    )
    return min(exponential * jitter, policy.max_delay_ms)


def scheduled_retry_delay_ms(
    policy: ResolvedRetryPolicy,
    retry_number: int,
    provider_retry_after_ms: int | float | None,
    random_value: Callable[[], float],
) -> float | None:
    """返回下一次重试应等待的毫秒数；normal 模式不接受超上限 Retry-After。

    返回 ``None`` 表示 provider 要求的延迟超过策略上限，调用方不应继续重试。
    """

    if (
        provider_retry_after_ms is not None
        and provider_retry_after_ms > 0
        and provider_retry_after_ms == provider_retry_after_ms
    ):
        if provider_retry_after_ms > policy.max_delay_ms:
            return None
        return float(provider_retry_after_ms)
    return local_retry_delay_ms(policy, retry_number, random_value)


def random_01() -> float:
    """生产环境的默认随机源，与 CoC 骰子随机源隔离。"""

    return _random.random()
