"""定义 Agentic MVP 唯一的外部 GM 模型 seam。"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from time import monotonic as _monotonic
from typing import Any, Callable, Mapping, Protocol, Sequence

PROMPT_REVISION = "gm-capability-charter-agentic-mvp-2"
TOOL_SCHEMA_VERSION = "coc-tools-agentic-mvp-1"
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
)
DEFAULT_DEEPSEEK_MODEL_ID = "deepseek-v4-flash"
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


def validated_model_profile(
    profile: Mapping[str, Any],
    *,
    enabled_tools: Sequence[str],
) -> dict[str, Any]:
    """重建唯一受支持的非秘密模型配置，不保留未知字段。"""

    if not isinstance(profile, Mapping) or set(profile) != set(MODEL_PROFILE_FIELDS):
        raise ModelProfileValidationError("model_profile 必须只包含已知非秘密字段")
    rebuilt = {
        field: copy.deepcopy(profile[field]) for field in MODEL_PROFILE_FIELDS
    }
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
) -> dict[str, Any]:
    """生成当前纵向切片唯一的非秘密 DeepSeek 运行配置。"""

    return validated_model_profile(
        {
            "provider": "deepseek",
            "model_id": model_id,
            "thinking": thinking,
            "stream": False,
            "response_format": "json_object",
            "temperature": None,
            "top_p": None,
            "max_tokens": 4096,
            "prompt_revision": PROMPT_REVISION,
            "tool_schema_version": TOOL_SCHEMA_VERSION,
            "enabled_tools": ["make_check"],
        },
        enabled_tools=("make_check",),
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

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


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
        client: Any | None = None,
        monotonic: Callable[[], float] = _monotonic,
        request_evidence_sink: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                api_key=api_key,
                base_url="https://api.deepseek.com",
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
        if validated_profile["provider"] != "deepseek":
            raise ModelCallError(
                "unsupported_model_profile",
                "DeepSeek adapter requires provider=deepseek",
                retryable=False,
            )

        sdk_request = {
            "model": validated_profile["model_id"],
            "messages": [
                copy.deepcopy(dict(message)) for message in request.messages
            ],
            "tools": [copy.deepcopy(dict(tool)) for tool in request.tools],
            "response_format": {"type": "json_object"},
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
            choice = response.choices[0]
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
            usage = None
            if response.usage is not None:
                usage = _sanitized_usage(
                    response.usage.model_dump(mode="json")
                )
            return ModelResponse(
                assistant_message={
                    "role": message.get("role"),
                    "content": content,
                    "reasoning_content": message.get("reasoning_content"),
                    "tool_calls": tool_calls,
                },
                finish_reason=choice.finish_reason,
                usage=usage,
                latency_ms=latency_ms,
            )
        except Exception as error:
            raise ModelCallError(
                "provider_response_error",
                "DeepSeek response could not form an assistant message",
                retryable=False,
            ) from error


def _mapped_openai_error(error: Exception) -> ModelCallError | None:
    from openai import (
        APIConnectionError,
        APITimeoutError,
        AuthenticationError,
        InternalServerError,
        RateLimitError,
    )

    if isinstance(error, AuthenticationError):
        return ModelCallError(
            "provider_authentication_failed",
            "DeepSeek authentication failed",
            retryable=False,
        )
    if isinstance(error, RateLimitError):
        return ModelCallError(
            "provider_rate_limited",
            "DeepSeek rate limit reached",
            retryable=True,
        )
    if isinstance(error, APITimeoutError):
        return ModelCallError(
            "request_timeout",
            "DeepSeek request timed out",
            retryable=True,
        )
    if isinstance(error, APIConnectionError):
        return ModelCallError(
            "provider_network_error",
            "DeepSeek network request failed",
            retryable=True,
        )
    if isinstance(error, InternalServerError):
        return ModelCallError(
            "provider_server_error",
            "DeepSeek service failed",
            retryable=True,
        )
    return None


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
