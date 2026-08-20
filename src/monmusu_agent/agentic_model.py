"""定义 Agentic MVP 唯一的外部 GM 模型 seam。"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import monotonic as _monotonic
from typing import Any, Callable, Mapping, Protocol, Sequence

from monmusu_agent.agentic_retry import (
    RetryPolicyValidationError,
    resolve_retry_policy,
)

PROMPT_REVISION = "gm-capability-charter-agentic-mvp-3"
TOOL_SCHEMA_VERSION = "coc-tools-agentic-mvp-1"
DEFAULT_COC_TOOL_NAMES = (
    "make_check",
    "push_check",
    "spend_luck",
    "deal_damage",
    "make_sanity_check",
)
MODEL_PROFILE_FIELDS = (
    "provider",
    "model_id",
    "thinking",
    "stream",
    "response_format",
    "temperature",
    "top_p",
    "max_tokens",
    "prompt_revision",
    "tool_schema_version",
    "enabled_tools",
    "retry_policy",
    "base_url",
)
_OPTIONAL_LEGACY_PROFILE_FIELDS = frozenset({"retry_policy", "base_url"})
LEGACY_MODEL_PROFILE_FIELDS = tuple(
    field
    for field in MODEL_PROFILE_FIELDS
    if field not in _OPTIONAL_LEGACY_PROFILE_FIELDS
)
DEFAULT_DEEPSEEK_MODEL_ID = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
SUPPORTED_PROVIDERS = ("deepseek", "opencode-go", "custom")
_PROVIDER_DEFAULT_BASE_URLS = {
    "deepseek": DEFAULT_DEEPSEEK_BASE_URL,
    "opencode-go": DEFAULT_OPENCODE_GO_BASE_URL,
}
_USAGE_TOKEN_FIELDS = (
    "completion_tokens",
    "prompt_tokens",
    "total_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
)
_COMPLETION_TOKEN_DETAIL_FIELDS = (
    "accepted_prediction_tokens",
    "audio_tokens",
    "reasoning_tokens",
    "rejected_prediction_tokens",
)
_PROMPT_TOKEN_DETAIL_FIELDS = ("audio_tokens", "cached_tokens")


class ModelProfileValidationError(ValueError):
    """表示行为相关模型配置不能形成精确的非秘密持久化形状。"""


def default_base_url_for_provider(provider: object) -> str:
    """返回受支持 provider 的默认 base URL；custom 没有默认值。"""

    if not isinstance(provider, str) or provider not in _PROVIDER_DEFAULT_BASE_URLS:
        raise ModelProfileValidationError(
            f"model_profile.provider 不受支持：{provider!r}"
        )
    return _PROVIDER_DEFAULT_BASE_URLS[provider]


def validated_model_profile(
    profile: Mapping[str, Any],
    *,
    enabled_tools: Sequence[str],
) -> dict[str, Any]:
    """重建唯一受支持的非秘密模型配置，不保留未知字段。

    历史 profile 没有 ``base_url`` 字段；它们按 provider 默认值补齐，因此旧
    ``IncompleteTurn`` 仍可加载。custom provider 没有历史档案，不接受缺失。
    """

    if not isinstance(profile, Mapping):
        raise ModelProfileValidationError("model_profile 必须只包含已知非秘密字段")
    present_fields = set(profile)
    missing_optional_fields = {
        field
        for field in _OPTIONAL_LEGACY_PROFILE_FIELDS
        if field not in present_fields
    }
    expected_fields = tuple(
        field for field in MODEL_PROFILE_FIELDS if field not in missing_optional_fields
    )
    if present_fields != set(expected_fields):
        raise ModelProfileValidationError("model_profile 必须只包含已知非秘密字段")
    rebuilt = {
        field: copy.deepcopy(profile[field])
        for field in MODEL_PROFILE_FIELDS
        if field in profile
    }
    if "base_url" not in rebuilt:
        rebuilt["base_url"] = default_base_url_for_provider(rebuilt.get("provider"))
    if "retry_policy" not in rebuilt:
        rebuilt["retry_policy"] = resolve_retry_policy(None).as_profile()
    else:
        try:
            rebuilt["retry_policy"] = resolve_retry_policy(
                rebuilt["retry_policy"]
            ).as_profile()
        except RetryPolicyValidationError as error:
            raise ModelProfileValidationError(
                "model_profile.retry_policy 格式无效"
            ) from error
    for field in (
        "provider",
        "model_id",
        "response_format",
        "prompt_revision",
        "tool_schema_version",
    ):
        value = rebuilt[field]
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ModelProfileValidationError(f"model_profile.{field} 格式无效")
    max_tokens = rebuilt["max_tokens"]
    profile_tools = rebuilt["enabled_tools"]
    if (
        not isinstance(rebuilt["base_url"], str)
        or not rebuilt["base_url"].strip()
        or rebuilt["base_url"] != rebuilt["base_url"].strip()
        or not rebuilt["base_url"].startswith(("http://", "https://"))
    ):
        raise ModelProfileValidationError("model_profile.base_url 格式无效")
    if rebuilt["provider"] == "opencode-go":
        if rebuilt["thinking"]:
            raise ModelProfileValidationError(
                "opencode-go 首版只支持 thinking=false"
            )
        if rebuilt["model_id"] != DEFAULT_DEEPSEEK_MODEL_ID:
            raise ModelProfileValidationError(
                "opencode-go 首版只支持 deepseek-v4-flash"
            )
    if (
        not isinstance(rebuilt["thinking"], bool)
        or rebuilt["stream"] is not False
        or rebuilt["response_format"] != "json_object"
        or rebuilt["temperature"] is not None
        or rebuilt["top_p"] is not None
        or not isinstance(max_tokens, int)
        or isinstance(max_tokens, bool)
        or not 1 <= max_tokens <= 1_000_000
        or rebuilt["prompt_revision"] != PROMPT_REVISION
        or rebuilt["tool_schema_version"] != TOOL_SCHEMA_VERSION
        or not isinstance(profile_tools, list)
        or not profile_tools
        or any(
            not isinstance(name, str)
            or not name
            or name != name.strip()
            for name in profile_tools
        )
        or len(set(profile_tools)) != len(profile_tools)
        or profile_tools != list(enabled_tools)
    ):
        raise ModelProfileValidationError("model_profile 格式无效")
    return rebuilt


def deepseek_model_profile(
    *,
    model_id: str = DEFAULT_DEEPSEEK_MODEL_ID,
    thinking: bool = False,
    enabled_tools: Sequence[str] = DEFAULT_COC_TOOL_NAMES,
    provider: str = "deepseek",
    base_url: str | None = None,
    retry_policy: Mapping[str, Any] | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """生成当前纵向切片唯一的非秘密模型运行配置。

    thinking 模式的 reasoning_content 与最终 JSON 共享 completion 预算；
    pilot 实测 thinking=true + 4096 时约 5/12 局被截断（provider_response_error），
    因此缺省预算按 thinking 分流：false 时 4096，true 时 16384。显式传入优先。
    """

    if provider not in SUPPORTED_PROVIDERS:
        raise ModelProfileValidationError(
            f"model_profile.provider 不受支持：{provider!r}"
        )
    effective_base_url = base_url
    if effective_base_url is None:
        effective_base_url = default_base_url_for_provider(provider)
    try:
        resolved_retry_policy = resolve_retry_policy(retry_policy).as_profile()
    except RetryPolicyValidationError as error:
        raise ModelProfileValidationError(str(error)) from error
    return validated_model_profile(
        {
            "provider": provider,
            "model_id": model_id,
            "thinking": thinking,
            "stream": False,
            "response_format": "json_object",
            "temperature": None,
            "top_p": None,
            "max_tokens": (
                16384 if thinking else 4096
            ) if max_tokens is None else max_tokens,
            "prompt_revision": PROMPT_REVISION,
            "tool_schema_version": TOOL_SCHEMA_VERSION,
            "enabled_tools": list(enabled_tools),
            "retry_policy": resolved_retry_policy,
            "base_url": effective_base_url,
        },
        enabled_tools=tuple(enabled_tools),
    )


@dataclass(frozen=True, repr=False)
class ModelRequest:
    """保存一次非流式 GM 请求所需的完整 provider 无关材料。"""

    messages: tuple[Mapping[str, Any], ...]
    tools: tuple[Mapping[str, Any], ...]
    request_timeout_seconds: float
    model_profile: Mapping[str, Any]

    def __repr__(self) -> str:
        """避免 GM 上下文和恢复协议材料通过调试表示外泄。"""

        return "ModelRequest(<restricted model context>)"


@dataclass(frozen=True, repr=False)
class ModelResponse:
    """保留 adapter 收到的完整 assistant 协议消息。"""

    assistant_message: Mapping[str, Any]
    finish_reason: str | None
    usage: Mapping[str, Any] | None
    latency_ms: int | None

    def __repr__(self) -> str:
        """避免未分类 provider envelope 通过调试表示泄露受限内容。"""

        return "ModelResponse(<restricted provider envelope>)"


class ModelCallError(RuntimeError):
    """以稳定且不含凭据的字段报告一次 provider 调用失败。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        status: int | None = None,
        provider_retry_after_ms: int | float | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status
        self.provider_retry_after_ms = provider_retry_after_ms
        self.request_id = request_id


class GameMasterModel(Protocol):
    """把 provider 变化隔离在一次请求/响应边界之后。"""

    def complete(self, request: ModelRequest) -> ModelResponse:
        """返回一次完整响应，或抛出稳定的 ``ModelCallError``。"""


class DeepSeekGameMasterModel:
    """通过 OpenAI SDK 调用 DeepSeek Chat Completions。"""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
        client: Any | None = None,
        monotonic: Callable[[], float] = _monotonic,
        request_evidence_sink: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        self._base_url = base_url
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                max_retries=0,
            )
        self._client: Any = client
        self._monotonic = monotonic
        self._request_evidence_sink = request_evidence_sink

    def complete(self, request: ModelRequest) -> ModelResponse:
        profile = request.model_profile
        if profile.get("stream") is not False:
            raise ModelCallError(
                "unsupported_streaming",
                "Increment 1 does not support streaming",
                retryable=False,
            )
        try:
            profile_tools = profile.get("enabled_tools")
            validated_profile = validated_model_profile(
                profile,
                enabled_tools=(
                    tuple(profile_tools) if isinstance(profile_tools, list) else ()
                ),
            )
        except ModelProfileValidationError as error:
            raise ModelCallError(
                "unsupported_model_profile",
                "DeepSeek model profile is unsupported",
                retryable=False,
            ) from error
        if validated_profile["provider"] not in SUPPORTED_PROVIDERS:
            raise ModelCallError(
                "unsupported_model_profile",
                "DeepSeek adapter received an unsupported provider",
                retryable=False,
            )
        if validated_profile["base_url"] != self._base_url:
            raise ModelCallError(
                "unsupported_model_profile",
                "adapter base_url does not match the frozen model profile",
                retryable=False,
            )

        # 回放兼容：DeepSeek 拒绝 assistant 消息里的空 tool_calls 数组
        # （"empty array. Expected an array with minimum length 1"），
        # 而 Harness 持久化格式用 [] 表示无工具调用；发送前去掉该键。
        sdk_messages = normalized_sdk_messages(request.messages)
        sdk_request: dict[str, Any] = {
            "model": validated_profile["model_id"],
            "messages": sdk_messages,
            "stream": False,
            "max_tokens": validated_profile["max_tokens"],
            "timeout": request.request_timeout_seconds,
            "extra_body": {
                "thinking": {
                    "type": (
                        "enabled" if validated_profile["thinking"] else "disabled"
                    )
                }
            },
        }
        # 结构修正相位不携带工具：DeepSeek 拒绝空 tools 数组（HTTP 400），
        # 因此工具为空时必须整体省略该字段，而不是发送 "tools": []。
        if request.tools:
            sdk_request["tools"] = [
                copy.deepcopy(dict(tool)) for tool in request.tools
            ]
        # opencode-go 在 response_format=json_object 下不会返回原生
        # tool_calls，而是把工具调用写进 JSON content；省略该字段才能走
        # Chat Completions function tools。最终答复仍由 Harness 本地校验 JSON。
        if validated_profile["provider"] != "opencode-go":
            sdk_request["response_format"] = {"type": "json_object"}
        started_at = self._monotonic()
        try:
            if self._request_evidence_sink is not None:
                self._request_evidence_sink(_sdk_request_evidence(sdk_request))
            response = self._client.chat.completions.create(**sdk_request)
        except Exception as error:
            mapped = _mapped_openai_error(error)
            if mapped is not None:
                raise mapped from error
            raise ModelCallError(
                "provider_error",
                "DeepSeek provider request failed",
                retryable=False,
            ) from error
        latency_ms = round((self._monotonic() - started_at) * 1000)
        try:
            choices = response.choices
            if not isinstance(choices, list) or not choices:
                raise ModelCallError(
                    "provider_empty_response",
                    "DeepSeek completed without any response choices",
                    retryable=True,
                    request_id=_response_request_id(response),
                )
            choice = choices[0]
            message = choice.message.model_dump(mode="json")
            tool_calls = _normalized_sdk_tool_calls(message.get("tool_calls"))
            content = message.get("content")
            if (
                isinstance(tool_calls, list)
                and tool_calls
                and isinstance(content, str)
                and not content.strip()
            ):
                content = None
            finish_reason = choice.finish_reason
            if (
                finish_reason in {None, "stop"}
                and (content is None or isinstance(content, str) and not content.strip())
                and (not isinstance(tool_calls, list) or not tool_calls)
            ):
                raise ModelCallError(
                    "provider_empty_response",
                    "DeepSeek completed without replayable content",
                    retryable=True,
                    request_id=_response_request_id(response),
                )
            usage = None
            if response.usage is not None:
                usage = _sanitized_usage(
                    response.usage.model_dump(mode="json")
                )
            reasoning_content = message.get("reasoning_content")
            if validated_profile["provider"] == "opencode-go":
                if not isinstance(reasoning_content, str):
                    reasoning_content = message.get("reasoning")
                if not isinstance(reasoning_content, str):
                    reasoning_content = ""
            return ModelResponse(
                assistant_message={
                    "role": message.get("role"),
                    "content": content,
                    "reasoning_content": reasoning_content,
                    "tool_calls": tool_calls,
                },
                finish_reason=finish_reason,
                usage=usage,
                latency_ms=latency_ms,
            )
        except ModelCallError:
            raise
        except Exception as error:
            raise ModelCallError(
                "provider_response_error",
                "DeepSeek response could not form an assistant message",
                retryable=False,
            ) from error


_PROVIDER_FAILURE_MESSAGES = {
    "provider_authentication_failed": "DeepSeek authentication failed",
    "provider_quota_exceeded": "DeepSeek quota was exhausted",
    "provider_rate_limited": "DeepSeek rate limit reached",
    "provider_context_exceeded": "DeepSeek request exceeded the context window",
    "provider_bad_request": "DeepSeek request was rejected",
    "provider_not_found": "DeepSeek request target was not found",
    "provider_conflict": "DeepSeek request conflicted with provider state",
    "provider_unprocessable": "DeepSeek request could not be processed",
    "provider_server_error": "DeepSeek service failed",
    "request_timeout": "DeepSeek request timed out",
    "provider_network_error": "DeepSeek network request failed",
}

_QUOTA_ERROR_PATTERNS = (
    re.compile(r"\binsufficient[\s_-]+(?:quota|balance|credits?)\b", re.I),
    re.compile(
        r"\b(?:quota|usage[\s_-]+limit)[\s_-]+"
        r"(?:exceeded|exhausted|reached)\b",
        re.I,
    ),
    re.compile(
        r"\bexceed(?:ed|s)?[\s_-]+(?:(?:your|the)[\s_-]+)?"
        r"(?:current[\s_-]+)?quota\b",
        re.I,
    ),
    re.compile(r"\b(?:balance|credits?)[\s_-]+(?:exhausted|depleted)\b", re.I),
    re.compile(r"\bout[\s_-]+of[\s_-]+(?:credits?|budget)\b", re.I),
)

_CONTEXT_ERROR_PATTERNS = (
    re.compile(
        r"(?:^|[^a-z0-9])context[\s_-](?:length|window)[\s_-]"
        r"(?:exceed(?:ed|s)?|overflow(?:ed)?|limit[\s_-]exceeded)"
        r"(?:$|[^a-z0-9])",
        re.I,
    ),
    re.compile(
        r"\b(?:request|prompt|input|messages?)\s+(?:is\s+|are\s+)?"
        r"too\s+(?:large|long)\s+for\s+(?:(?:this|the)\s+)?"
        r"(?:model(?:'s)?\s+)?context(?:\s+window)?\b",
        re.I,
    ),
    re.compile(
        r"\b(?:input|prompt|request|messages?)\b.{0,40}"
        r"\b(?:exceed(?:s|ed)?|overflows?|is\s+larger\s+than)\b.{0,40}"
        r"\b(?:the\s+)?(?:model(?:'s)?\s+)?context(?:\s+(?:length|window))?\b",
        re.I,
    ),
    re.compile(
        r"\b(?:maximum|max)(?:\s+(?:allowed|supported))?\s+context"
        r"\s+(?:length|window)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:input|prompt|request)\s+(?:is\s+)?too\s+(?:long|large)"
        r"\s+for\s+(?:this|the)\s+model\b",
        re.I,
    ),
)


def _mapped_openai_error(error: Exception) -> ModelCallError | None:
    """把 OpenAI SDK 异常归一化为稳定、脱敏的 ModelCallError。"""

    from openai import APIConnectionError, APIStatusError, APITimeoutError

    if isinstance(error, APITimeoutError):
        return ModelCallError(
            "request_timeout",
            _PROVIDER_FAILURE_MESSAGES["request_timeout"],
            retryable=True,
        )
    if isinstance(error, APIConnectionError):
        return ModelCallError(
            "provider_network_error",
            _PROVIDER_FAILURE_MESSAGES["provider_network_error"],
            retryable=True,
        )
    if not isinstance(error, APIStatusError):
        return None

    status = error.status_code
    detail = _openai_error_detail(error)
    retry_after_ms = _provider_retry_after_ms(error)
    request_id = _response_request_id(error)
    if status in {401, 403}:
        return ModelCallError(
            "provider_authentication_failed",
            _PROVIDER_FAILURE_MESSAGES["provider_authentication_failed"],
            retryable=False,
            status=status,
            provider_retry_after_ms=retry_after_ms,
            request_id=request_id,
        )
    if _is_quota_exceeded_error(detail):
        return ModelCallError(
            "provider_quota_exceeded",
            _PROVIDER_FAILURE_MESSAGES["provider_quota_exceeded"],
            retryable=False,
            status=status,
            provider_retry_after_ms=retry_after_ms,
            request_id=request_id,
        )
    if status == 429:
        return ModelCallError(
            "provider_rate_limited",
            _PROVIDER_FAILURE_MESSAGES["provider_rate_limited"],
            retryable=True,
            status=status,
            provider_retry_after_ms=retry_after_ms,
            request_id=request_id,
        )
    if status == 400:
        if _is_context_window_exceeded_error(detail):
            return ModelCallError(
                "provider_context_exceeded",
                _PROVIDER_FAILURE_MESSAGES["provider_context_exceeded"],
                retryable=False,
                status=status,
                provider_retry_after_ms=retry_after_ms,
                request_id=request_id,
            )
        return ModelCallError(
            "provider_bad_request",
            _PROVIDER_FAILURE_MESSAGES["provider_bad_request"],
            retryable=False,
            status=status,
            provider_retry_after_ms=retry_after_ms,
            request_id=request_id,
        )
    if status == 404:
        return ModelCallError(
            "provider_not_found",
            _PROVIDER_FAILURE_MESSAGES["provider_not_found"],
            retryable=False,
            status=status,
            provider_retry_after_ms=retry_after_ms,
            request_id=request_id,
        )
    if status == 409:
        return ModelCallError(
            "provider_conflict",
            _PROVIDER_FAILURE_MESSAGES["provider_conflict"],
            retryable=False,
            status=status,
            provider_retry_after_ms=retry_after_ms,
            request_id=request_id,
        )
    if status == 422:
        return ModelCallError(
            "provider_unprocessable",
            _PROVIDER_FAILURE_MESSAGES["provider_unprocessable"],
            retryable=False,
            status=status,
            provider_retry_after_ms=retry_after_ms,
            request_id=request_id,
        )
    if status >= 500:
        return ModelCallError(
            "provider_server_error",
            _PROVIDER_FAILURE_MESSAGES["provider_server_error"],
            retryable=True,
            status=status,
            provider_retry_after_ms=retry_after_ms,
            request_id=request_id,
        )
    return None


def _openai_error_detail(error: Exception) -> str:
    body = getattr(error, "body", None)
    if isinstance(body, Mapping):
        nested = body.get("error")
        if isinstance(nested, Mapping):
            return " ".join(
                str(part)
                for part in (nested.get("code"), nested.get("type"), nested.get("message"))
                if part is not None
            )
    return ""


def _is_quota_exceeded_error(detail: str) -> bool:
    return any(pattern.search(detail) is not None for pattern in _QUOTA_ERROR_PATTERNS)


def _is_context_window_exceeded_error(detail: str) -> bool:
    return any(pattern.search(detail) is not None for pattern in _CONTEXT_ERROR_PATTERNS)


def _response_headers(source: object) -> object:
    response = getattr(source, "response", None)
    return getattr(response, "headers", None)


def _provider_retry_after_ms(error: Exception) -> int | None:
    headers = _response_headers(error)
    get = getattr(headers, "get", None)
    if not callable(get):
        return None
    value = get("retry-after")
    if not isinstance(value, str):
        return None
    if re.fullmatch(r"\d+", value.strip()) is not None:
        delay_ms = int(value.strip()) * 1000
        return delay_ms if delay_ms > 0 else None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed_delay_ms = (
        parsed - datetime.now(timezone.utc)
    ).total_seconds() * 1000
    if parsed_delay_ms != parsed_delay_ms or parsed_delay_ms <= 0:
        return None
    return round(parsed_delay_ms)


def _response_request_id(source: object) -> str | None:
    headers = _response_headers(source)
    get = getattr(headers, "get", None)
    if not callable(get):
        return None
    for name in ("x-request-id", "x-deepseek-request-id"):
        value = get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def normalized_sdk_messages(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """把持久化消息规范化成可安全回放给 DeepSeek 的 SDK 消息。

    DeepSeek 拒绝 assistant 消息里的空 tool_calls 数组（HTTP 400：
    "empty array. Expected an array with minimum length 1"），而 Harness
    持久化格式用 [] 表示无工具调用；回放前必须整体去掉该键。契约 runner
    的回放保真指纹也必须对模型侧消息应用同一变换（见 agentic_contract）。
    """

    normalized: list[dict[str, Any]] = []
    for message in messages:
        copied = copy.deepcopy(dict(message))
        if (
            copied.get("role") == "assistant"
            and isinstance(copied.get("tool_calls"), list)
            and not copied["tool_calls"]
        ):
            copied.pop("tool_calls")
        normalized.append(copied)
    return normalized


def _sdk_request_evidence(request: Mapping[str, Any]) -> dict[str, Any]:
    """只投影契约 runner 所需的实际 SDK 参数，不复制消息正文。"""

    function_tools: list[str] = []
    tools = request.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            function = tool.get("function") if isinstance(tool, Mapping) else None
            name = function.get("name") if isinstance(function, Mapping) else None
            function_tools.append(name if isinstance(name, str) else "unsupported")
    return {
        "model_id": request.get("model"),
        "messages_sha256": _canonical_json_sha256(request.get("messages")),
        "function_tools": function_tools,
        "response_format": copy.deepcopy(request.get("response_format")),
        "stream": request.get("stream"),
        "max_tokens": request.get("max_tokens"),
        "timeout": request.get("timeout"),
        "thinking": (
            request.get("extra_body", {})
            .get("thinking", {})
            .get("type")
            if isinstance(request.get("extra_body"), Mapping)
            and isinstance(request["extra_body"].get("thinking"), Mapping)
            else None
        ),
    }


def _canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_sdk_tool_calls(raw_tool_calls: object) -> object:
    """移除 SDK 的流式索引字段，保留其余未知 provider 形状供 Harness 拒绝。"""

    if raw_tool_calls is None:
        return []
    if not isinstance(raw_tool_calls, list):
        return copy.deepcopy(raw_tool_calls)
    normalized: list[object] = []
    for raw_call in raw_tool_calls:
        if not isinstance(raw_call, Mapping):
            normalized.append(copy.deepcopy(raw_call))
            continue
        call = copy.deepcopy(dict(raw_call))
        call.pop("index", None)
        normalized.append(call)
    return normalized


def _sanitized_usage(raw_usage: object) -> dict[str, Any]:
    """白名单保留官方 token 计数字段，丢弃 SDK 额外诊断。"""

    if not isinstance(raw_usage, Mapping):
        raise ValueError("usage is not a mapping")
    usage: dict[str, Any] = {}
    for field in _USAGE_TOKEN_FIELDS:
        value = raw_usage.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            usage[field] = value
    for field, allowed_fields in (
        ("completion_tokens_details", _COMPLETION_TOKEN_DETAIL_FIELDS),
        ("prompt_tokens_details", _PROMPT_TOKEN_DETAIL_FIELDS),
    ):
        raw_details = raw_usage.get(field)
        if not isinstance(raw_details, Mapping):
            continue
        details = {
            name: value
            for name in allowed_fields
            if isinstance((value := raw_details.get(name)), int)
            and not isinstance(value, bool)
            and value >= 0
        }
        if details:
            usage[field] = details
    return usage


class ScriptedGameMasterModel:
    """按脚本返回响应并捕获请求的确定性测试 adapter。"""

    def __init__(self, steps: Sequence[ModelResponse | ModelCallError]) -> None:
        self._steps = list(steps)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._steps:
            raise AssertionError("可编程 GM 模型没有剩余响应")
        step = self._steps.pop(0)
        if isinstance(step, ModelCallError):
            raise step
        return step
