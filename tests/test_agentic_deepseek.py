from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping
from unittest.mock import patch

import httpx
import openai

from monmusu_agent.agentic_harness import AgenticHarness
from monmusu_agent.agentic_model import (
    DeepSeekGameMasterModel,
    ModelCallError,
    ModelRequest,
    ModelResponse,
)
from monmusu_agent.agentic_session import (
    AgenticSessionStore,
    NewSessionRequest,
)


class Dumpable:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        if mode != "json":
            raise AssertionError("adapter 必须请求可序列化的 SDK 数据")
        return copy.deepcopy(self.payload)


class RecordingCompletions:
    def __init__(self, response: object) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> object:
        self.requests.append(copy.deepcopy(kwargs))
        return self.response


class RaisingCompletions:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def create(self, **kwargs: Any) -> object:
        self.calls += 1
        raise self.error


class ScriptedCompletions:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> object:
        self.requests.append(copy.deepcopy(kwargs))
        if not self.responses:
            raise AssertionError("mocked SDK 没有剩余响应")
        response = self.responses.pop(0)
        return response(copy.deepcopy(kwargs)) if callable(response) else response


class FixedRandom:
    def __init__(self) -> None:
        self._values = iter((3, 4))

    def randint(self, minimum: int, maximum: int) -> int:
        if (minimum, maximum) != (0, 9):
            raise AssertionError("unexpected random range")
        return next(self._values)


class ForbiddenClient:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"skip path touched SDK client attribute {name}")


class DeepSeekGameMasterModelTest(unittest.TestCase):
    def test_constructor_uses_official_deepseek_base_url(self) -> None:
        client = SimpleNamespace()
        with patch("openai.OpenAI", return_value=client) as constructor:
            DeepSeekGameMasterModel("sk-constructor-secret")

        constructor.assert_called_once_with(
            api_key="sk-constructor-secret",
            base_url="https://api.deepseek.com",
            max_retries=0,
        )

    def test_complete_converts_one_request_and_preserves_response_envelope(
        self,
    ) -> None:
        assistant_message = {
            "role": "assistant",
            "content": None,
            "reasoning_content": None,
            "tool_calls": [
                {
                    "id": "call_first",
                    "type": "function",
                    "function": {
                        "name": "make_check",
                        "arguments": '{"ability":"listen"}',
                    },
                },
                {
                    "id": "call_second",
                    "type": "function",
                    "function": {
                        "name": "make_check",
                        "arguments": '{"ability":"spot_hidden"}',
                    },
                },
            ],
        }
        usage = {
            "prompt_tokens": 123,
            "completion_tokens": 45,
            "total_tokens": 168,
            "prompt_cache_hit_tokens": 80,
            "completion_tokens_details": {
                "reasoning_tokens": 12,
            },
            "prompt_tokens_details": {
                "cached_tokens": 80,
            },
        }
        sdk_usage = {
            **usage,
            "completion_tokens_details": {
                "reasoning_tokens": 12,
                "private_diagnostic": "nested provider diagnostic",
            },
            "prompt_tokens_details": {
                "cached_tokens": 80,
                "Authorization": "Bearer nested-secret-fragment",
            },
            "private_diagnostic": "provider private diagnostic",
            "Authorization": "Bearer sdk-secret-fragment",
        }
        sdk_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=Dumpable(assistant_message),
                    finish_reason="tool_calls",
                )
            ],
            usage=Dumpable(sdk_usage),
        )
        completions = RecordingCompletions(sdk_response)
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        times = iter((10.0, 10.125))
        model = DeepSeekGameMasterModel(
            "not-a-real-key",
            client=client,
            monotonic=times.__next__,
            request_evidence_sink=(evidence := []).append,
        )
        request = ModelRequest(
            messages=(
                {"role": "system", "content": "Return JSON."},
                {"role": "user", "content": "I listen."},
            ),
            tools=(
                {
                    "type": "function",
                    "function": {
                        "name": "make_check",
                        "description": "Resolve one COC check.",
                        "parameters": {"type": "object"},
                    },
                },
            ),
            request_timeout_seconds=60,
            model_profile={
                "provider": "deepseek",
                "model_id": "deepseek-v4-flash",
                "thinking": False,
                "stream": False,
                "response_format": "json_object",
                "temperature": None,
                "top_p": None,
                "max_tokens": 4096,
                "prompt_revision": "gm-capability-charter-agentic-mvp-3",
                "tool_schema_version": "coc-tools-agentic-mvp-1",
                "enabled_tools": ["make_check"],
            },
        )

        response = model.complete(request)

        self.assertEqual(
            completions.requests,
            [
                {
                    "model": "deepseek-v4-flash",
                    "messages": [
                        {"role": "system", "content": "Return JSON."},
                        {"role": "user", "content": "I listen."},
                    ],
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "make_check",
                                "description": "Resolve one COC check.",
                                "parameters": {"type": "object"},
                            },
                        }
                    ],
                    "response_format": {"type": "json_object"},
                    "stream": False,
                    "max_tokens": 4096,
                    "timeout": 60,
                    "extra_body": {
                        "thinking": {"type": "disabled"}
                    },
                }
            ],
        )
        self.assertEqual(
            response,
            ModelResponse(
                assistant_message=assistant_message,
                finish_reason="tool_calls",
                usage=usage,
                latency_ms=125,
            ),
        )
        self.assertEqual(len(evidence), 1)
        self.assertEqual(
            evidence[0]["messages_sha256"],
            hashlib.sha256(
                json.dumps(
                    completions.requests[0]["messages"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        )

    def test_complete_omits_tools_key_when_tools_are_empty(self) -> None:
        """结构修正相位不携带工具；空工具必须整体省略 tools 键。

        DeepSeek 对 "tools": [] 返回 HTTP 400，之前结构修正请求因此
        全部中断为 provider_bad_request（见 zen-ab-pilot 实测记录）。
        """

        assistant_message = {
            "role": "assistant",
            "content": '{"narration":"门纹丝不动。","establish":[],'
            '"retire":[],"session_status":"ongoing"}',
            "reasoning_content": None,
            "tool_calls": [],
        }
        completions = RecordingCompletions(
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=Dumpable(assistant_message),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            )
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        model = DeepSeekGameMasterModel(
            "not-a-real-key",
            client=client,
        )
        request = ModelRequest(
            messages=(
                {"role": "system", "content": "Return JSON."},
                {"role": "user", "content": "Retry the final JSON only."},
            ),
            tools=(),
            request_timeout_seconds=60,
            model_profile={
                "provider": "deepseek",
                "model_id": "deepseek-v4-flash",
                "thinking": False,
                "stream": False,
                "response_format": "json_object",
                "temperature": None,
                "top_p": None,
                "max_tokens": 4096,
                "prompt_revision": "gm-capability-charter-agentic-mvp-3",
                "tool_schema_version": "coc-tools-agentic-mvp-1",
                "enabled_tools": ["make_check"],
            },
        )

        model.complete(request)

        self.assertEqual(len(completions.requests), 1)
        self.assertNotIn("tools", completions.requests[0])
        self.assertEqual(
            completions.requests[0]["response_format"],
            {"type": "json_object"},
        )

    def test_complete_strips_empty_tool_calls_from_assistant_messages(
        self,
    ) -> None:
        """回放时 assistant 消息的空 tool_calls 数组必须被去掉。

        DeepSeek 对 messages[i].tool_calls=[] 返回 HTTP 400，而 Harness
        持久化格式用 [] 表示无工具调用（见 zen-ab-pilot 实测记录）。
        """

        assistant_message = {
            "role": "assistant",
            "content": '{"narration":"门纹丝不动。","establish":[],'
            '"retire":[],"session_status":"ongoing"}',
            "reasoning_content": None,
            "tool_calls": [],
        }
        completions = RecordingCompletions(
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=Dumpable(assistant_message),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            )
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        model = DeepSeekGameMasterModel(
            "not-a-real-key",
            client=client,
        )
        request = ModelRequest(
            messages=(
                {"role": "system", "content": "Return JSON."},
                {
                    "role": "assistant",
                    "content": '{"narration":"坏掉的答复","establish":[],'
                    '"retire":[],"session_status":"ongoing"}',
                    "reasoning_content": None,
                    "tool_calls": [],
                },
                {"role": "user", "content": "Retry the final JSON only."},
            ),
            tools=(),
            request_timeout_seconds=60,
            model_profile={
                "provider": "deepseek",
                "model_id": "deepseek-v4-flash",
                "thinking": False,
                "stream": False,
                "response_format": "json_object",
                "temperature": None,
                "top_p": None,
                "max_tokens": 4096,
                "prompt_revision": "gm-capability-charter-agentic-mvp-3",
                "tool_schema_version": "coc-tools-agentic-mvp-1",
                "enabled_tools": ["make_check"],
            },
        )

        model.complete(request)

        self.assertEqual(len(completions.requests), 1)
        sent_assistant = completions.requests[0]["messages"][1]
        self.assertEqual(sent_assistant["role"], "assistant")
        self.assertNotIn("tool_calls", sent_assistant)

    def test_complete_enables_thinking_and_preserves_reasoning_envelope(
        self,
    ) -> None:
        reasoning_canary = "THINKING_RECOVERY_CANARY_10"
        assistant_message = {
            "role": "assistant",
            "content": "The lock needs a careful listening check.",
            "reasoning_content": reasoning_canary,
            "tool_calls": [
                {
                    "id": "call_thinking",
                    "type": "function",
                    "function": {
                        "name": "make_check",
                        "arguments": '{"ability":"listen"}',
                    },
                }
            ],
        }
        sdk_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=Dumpable(assistant_message),
                    finish_reason="tool_calls",
                )
            ],
            usage=None,
        )
        completions = RecordingCompletions(sdk_response)
        model = DeepSeekGameMasterModel(
            "not-a-real-key",
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=completions)
            ),
        )
        tool_message = {
            "role": "tool",
            "tool_call_id": "call_thinking",
            "name": "make_check",
            "content": '{"ok":true}',
        }
        replay_messages = (
            {"role": "user", "content": "I listen."},
            assistant_message,
            tool_message,
        )

        response = model.complete(
            ModelRequest(
                messages=replay_messages,
                tools=(
                    {
                        "type": "function",
                        "function": {
                            "name": "make_check",
                            "description": "Resolve one COC check.",
                            "parameters": {"type": "object"},
                        },
                    },
                ),
                request_timeout_seconds=60,
                model_profile={
                    "provider": "deepseek",
                    "model_id": "deepseek-v4-flash",
                    "thinking": True,
                    "stream": False,
                    "response_format": "json_object",
                    "temperature": None,
                    "top_p": None,
                    "max_tokens": 4096,
                    "prompt_revision": "gm-capability-charter-agentic-mvp-3",
                    "tool_schema_version": "coc-tools-agentic-mvp-1",
                    "enabled_tools": ["make_check"],
                },
            )
        )

        sdk_request = completions.requests[0]
        self.assertEqual(
            sdk_request["extra_body"],
            {"thinking": {"type": "enabled"}},
        )
        self.assertIs(sdk_request["stream"], False)
        self.assertEqual(sdk_request["response_format"], {"type": "json_object"})
        self.assertEqual(sdk_request["messages"], list(replay_messages))
        self.assertEqual(
            sdk_request["messages"][-2]["reasoning_content"],
            reasoning_canary,
        )
        self.assertEqual(response.assistant_message, assistant_message)

    def test_complete_rejects_streaming_before_sdk(
        self,
    ) -> None:
        completions = RecordingCompletions(object())
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        model = DeepSeekGameMasterModel(
            "secret-key-must-not-appear",
            client=client,
        )
        baseline_profile = {
            "provider": "deepseek",
            "model_id": "deepseek-v4-flash",
            "thinking": False,
            "stream": False,
            "response_format": "json_object",
            "temperature": None,
            "top_p": None,
            "max_tokens": 4096,
            "prompt_revision": "gm-capability-charter-agentic-mvp-3",
            "tool_schema_version": "coc-tools-agentic-mvp-1",
            "enabled_tools": ["make_check"],
        }

        profile = copy.deepcopy(baseline_profile)
        profile["stream"] = True
        request = ModelRequest(
            messages=({"role": "user", "content": "Test."},),
            tools=(),
            request_timeout_seconds=60,
            model_profile=profile,
        )

        with self.assertRaises(ModelCallError) as caught:
            model.complete(request)

        self.assertEqual(caught.exception.code, "unsupported_streaming")
        self.assertFalse(caught.exception.retryable)
        self.assertNotIn(
            "secret-key-must-not-appear",
            caught.exception.message,
        )
        self.assertEqual(completions.requests, [])

    def test_complete_normalizes_sdk_tool_call_transport_fields(self) -> None:
        sdk_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=Dumpable(
                        {
                            "role": "assistant",
                            "content": " \n  ",
                            "reasoning_content": None,
                            "tool_calls": [
                                {
                                    "id": "call_live_shape",
                                    "type": "function",
                                    "function": {
                                        "name": "make_check",
                                        "arguments": '{"ability":"strength"}',
                                    },
                                    "index": 0,
                                }
                            ],
                        }
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=None,
        )
        model = DeepSeekGameMasterModel(
            "not-a-real-key",
            client=SimpleNamespace(
                chat=SimpleNamespace(
                    completions=RecordingCompletions(sdk_response)
                )
            ),
        )

        response = model.complete(
            ModelRequest(
                messages=({"role": "user", "content": "Test JSON."},),
                tools=(
                    {
                        "type": "function",
                        "function": {
                            "name": "make_check",
                            "description": "Resolve one COC check.",
                            "parameters": {"type": "object"},
                        },
                    },
                ),
                request_timeout_seconds=60,
                model_profile={
                    "provider": "deepseek",
                    "model_id": "deepseek-v4-flash",
                    "thinking": False,
                    "stream": False,
                    "response_format": "json_object",
                    "temperature": None,
                    "top_p": None,
                    "max_tokens": 4096,
                    "prompt_revision": "gm-capability-charter-agentic-mvp-3",
                    "tool_schema_version": "coc-tools-agentic-mvp-1",
                    "enabled_tools": ["make_check"],
                },
            )
        )

        self.assertEqual(
            response.assistant_message,
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": None,
                "tool_calls": [
                    {
                        "id": "call_live_shape",
                        "type": "function",
                        "function": {
                            "name": "make_check",
                            "arguments": '{"ability":"strength"}',
                        },
                    }
                ],
            },
        )

    def test_complete_maps_sdk_failures_without_provider_details(self) -> None:
        sdk_request = httpx.Request(
            "POST",
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": "Bearer sk-live-secret-fragment"},
        )
        errors: tuple[tuple[Exception, str, bool], ...] = (
            (
                openai.AuthenticationError(
                    "bad key sk-live-secret-fragment",
                    response=httpx.Response(401, request=sdk_request),
                    body={"private": "authentication diagnostic"},
                ),
                "provider_authentication_failed",
                False,
            ),
            (
                openai.RateLimitError(
                    "quota for sk-live-secret-fragment",
                    response=httpx.Response(429, request=sdk_request),
                    body={"private": "rate-limit diagnostic"},
                ),
                "provider_rate_limited",
                True,
            ),
            (
                openai.APITimeoutError(sdk_request),
                "request_timeout",
                True,
            ),
            (
                openai.APIConnectionError(
                    message="network sk-live-secret-fragment",
                    request=sdk_request,
                ),
                "provider_network_error",
                True,
            ),
            (
                openai.InternalServerError(
                    "provider sk-live-secret-fragment",
                    response=httpx.Response(503, request=sdk_request),
                    body={"private": "server diagnostic"},
                ),
                "provider_server_error",
                True,
            ),
            (
                openai.BadRequestError(
                    "bad request sk-live-secret-fragment",
                    response=httpx.Response(400, request=sdk_request),
                    body={"private": "unmapped provider diagnostic"},
                ),
                "provider_bad_request",
                False,
            ),
        )
        profile = {
            "provider": "deepseek",
            "model_id": "deepseek-v4-flash",
            "thinking": False,
            "stream": False,
            "response_format": "json_object",
            "temperature": None,
            "top_p": None,
            "max_tokens": 4096,
            "prompt_revision": "gm-capability-charter-agentic-mvp-3",
            "tool_schema_version": "coc-tools-agentic-mvp-1",
            "enabled_tools": ["make_check"],
        }
        request = ModelRequest(
            messages=({"role": "user", "content": "Test."},),
            tools=(),
            request_timeout_seconds=60,
            model_profile=profile,
        )

        for sdk_error, expected_code, retryable in errors:
            with self.subTest(expected_code=expected_code):
                completions = RaisingCompletions(sdk_error)
                client = SimpleNamespace(
                    chat=SimpleNamespace(completions=completions)
                )
                model = DeepSeekGameMasterModel(
                    "sk-live-secret-fragment",
                    client=client,
                )

                with self.assertRaises(ModelCallError) as caught:
                    model.complete(request)

                self.assertEqual(caught.exception.code, expected_code)
                self.assertEqual(caught.exception.retryable, retryable)
                rendered = str(caught.exception)
                self.assertNotIn("sk-live-secret-fragment", rendered)
                self.assertNotIn("diagnostic", rendered)
                self.assertNotIn("Authorization", rendered)
                self.assertEqual(completions.calls, 1)

    def test_complete_maps_unusable_sdk_response_to_stable_error(self) -> None:
        completions = RecordingCompletions(
            SimpleNamespace(choices=[], usage=None)
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        model = DeepSeekGameMasterModel(
            "secret-response-key",
            client=client,
        )
        request = ModelRequest(
            messages=({"role": "user", "content": "Test."},),
            tools=(),
            request_timeout_seconds=60,
            model_profile={
                "provider": "deepseek",
                "model_id": "deepseek-v4-flash",
                "thinking": False,
                "stream": False,
                "response_format": "json_object",
                "temperature": None,
                "top_p": None,
                "max_tokens": 4096,
                "prompt_revision": "gm-capability-charter-agentic-mvp-3",
                "tool_schema_version": "coc-tools-agentic-mvp-1",
                "enabled_tools": ["make_check"],
            },
        )

        with self.assertRaises(ModelCallError) as caught:
            model.complete(request)

        self.assertEqual(caught.exception.code, "provider_empty_response")
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn("secret-response-key", str(caught.exception))

    def test_real_adapter_class_commits_direct_final_through_harness(
        self,
    ) -> None:
        final = {
            "narration": "你拿起无人看守的铜钥匙。",
            "establish": [
                {"visibility": "public", "text": "调查员持有铜钥匙。"}
            ],
            "retire": [],
            "session_status": "ongoing",
        }
        completions = ScriptedCompletions(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(
                                {
                                    "role": "assistant",
                                    "content": json.dumps(
                                        final,
                                        ensure_ascii=False,
                                    ),
                                    "reasoning_content": None,
                                    "tool_calls": [],
                                }
                            ),
                            finish_reason="stop",
                        )
                    ],
                    usage=None,
                )
            ]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_adapter_direct",
                clock=lambda: datetime(2026, 7, 29, tzinfo=timezone.utc),
            )
            created = store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_tracker",
                    display_name="林雁",
                )
            )
            model = DeepSeekGameMasterModel(
                "not-a-real-key",
                client=client,
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_direct",
                fact_id_factory=lambda: "fact_direct",
                clock=lambda: datetime(
                    2026,
                    7,
                    29,
                    0,
                    1,
                    tzinfo=timezone.utc,
                ),
            )

            result = harness.start_turn(created.game_id, "我拿起铜钥匙。")

            self.assertEqual(result.status, "committed")
            self.assertEqual(result.narration, final["narration"])
            self.assertEqual(len(completions.requests), 1)
            saved = store.load_session(created.game_id).session
            self.assertIsNone(saved["incomplete_turn"])
            self.assertEqual(saved["turns"][0]["turn_id"], "turn_direct")
            self.assertEqual(saved["facts"][-1]["fact_id"], "fact_direct")

    def test_real_adapter_class_returns_matching_tool_result_before_final(
        self,
    ) -> None:
        arguments = {
            "actor_id": "investigator_tracker",
            "ability": "listen",
            "difficulty": "regular",
            "dice_adjustment": {"kind": "none", "count": 0},
            "action": "分辨门外脚步声",
            "stakes": "失败会错过守卫接近的方向",
            "visibility": "public",
        }
        assistant_tool_call = {
            "role": "assistant",
            "content": None,
            "reasoning_content": None,
            "tool_calls": [
                {
                    "id": "call_listen",
                    "type": "function",
                    "function": {
                        "name": "make_check",
                        "arguments": json.dumps(
                            arguments,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                }
            ],
        }
        final = {
            "narration": "你听出脚步正从右侧回廊接近。",
            "establish": [],
            "retire": [],
            "session_status": "ongoing",
        }
        completions = ScriptedCompletions(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(assistant_tool_call),
                            finish_reason="tool_calls",
                        )
                    ],
                    usage=Dumpable(
                        {
                            "prompt_tokens": 100,
                            "completion_tokens": 40,
                            "total_tokens": 140,
                        }
                    ),
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(
                                {
                                    "role": "assistant",
                                    "content": json.dumps(
                                        final,
                                        ensure_ascii=False,
                                    ),
                                    "reasoning_content": None,
                                    "tool_calls": [],
                                }
                            ),
                            finish_reason="stop",
                        )
                    ],
                    usage=None,
                ),
            ]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_adapter_tool",
                clock=lambda: datetime(2026, 7, 29, tzinfo=timezone.utc),
            )
            created = store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_tracker",
                    display_name="林雁",
                )
            )
            harness = AgenticHarness(
                store,
                DeepSeekGameMasterModel("not-a-real-key", client=client),
                turn_id_factory=lambda: "turn_tool",
                mechanic_id_factory=lambda: "mechanic_listen",
                random_source=FixedRandom(),
                clock=lambda: datetime(
                    2026,
                    7,
                    29,
                    0,
                    2,
                    tzinfo=timezone.utc,
                ),
            )

            result = harness.start_turn(
                created.game_id,
                "我贴近门缝，分辨外面的脚步。",
            )

            self.assertEqual(result.status, "committed")
            self.assertEqual(result.narration, final["narration"])
            self.assertEqual(len(result.public_mechanics), 1)
            self.assertEqual(result.public_mechanics[0].details["roll"], 43)
            self.assertEqual(len(completions.requests), 2)
            continuation = completions.requests[1]
            self.assertEqual(continuation["messages"][-2], assistant_tool_call)
            self.assertEqual(
                continuation["messages"][-1]["role"],
                "tool",
            )
            self.assertEqual(
                continuation["messages"][-1]["tool_call_id"],
                "call_listen",
            )
            tool_result = json.loads(continuation["messages"][-1]["content"])
            self.assertTrue(tool_result["ok"])
            self.assertEqual(tool_result["tool_call_id"], "call_listen")
            saved = store.load_session(created.game_id).session
            self.assertEqual(
                saved["turns"][0]["mechanics"][0]["mechanic_id"],
                "mechanic_listen",
            )

    def test_explicit_composition_keeps_api_key_out_of_runtime_data(self) -> None:
        from monmusu_agent.agentic_cli import compose_deepseek_harness

        final = {
            "narration": "门外暂时没有脚步声。",
            "establish": [],
            "retire": [],
            "session_status": "ongoing",
        }
        completions = ScriptedCompletions(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(
                                {
                                    "role": "assistant",
                                    "content": json.dumps(
                                        final,
                                        ensure_ascii=False,
                                    ),
                                    "reasoning_content": None,
                                    "tool_calls": [],
                                }
                            ),
                            finish_reason="stop",
                        )
                    ],
                    usage=None,
                )
            ]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        secret = "sk-composition-secret-fragment"

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_composed",
                clock=lambda: datetime(2026, 7, 29, tzinfo=timezone.utc),
            )
            created = store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_tracker",
                    display_name="林雁",
                )
            )
            harness = compose_deepseek_harness(
                store,
                api_key=secret,
                model_id="deepseek-v4-flash",
                thinking=False,
                client=client,
            )

            result = harness.start_turn(created.game_id, "我倾听门外。")

            self.assertEqual(result.status, "committed")
            saved = store.load_session(created.game_id).session
            visible_material = json.dumps(
                {
                    "session": saved,
                    "sdk_requests": completions.requests,
                    "turn_result": result.__dict__,
                },
                ensure_ascii=False,
                default=dict,
            )
            self.assertNotIn(secret, visible_material)
            self.assertEqual(
                completions.requests[0]["model"],
                "deepseek-v4-flash",
            )

    def test_explicit_composition_accepts_thinking_without_provider_call(
        self,
    ) -> None:
        from monmusu_agent.agentic_cli import compose_deepseek_harness

        completions = RecordingCompletions(object())
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions"
            )

            harness = compose_deepseek_harness(
                store,
                api_key="not-a-real-key",
                model_id="deepseek-v4-flash",
                thinking=True,
                client=client,
            )

        self.assertIs(harness.model_profile["thinking"], True)
        self.assertIs(harness.model_profile["stream"], False)
        self.assertEqual(completions.requests, [])

    def test_explicit_composition_rejects_invalid_model_id_stably(self) -> None:
        from monmusu_agent.agentic_cli import compose_deepseek_harness

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions"
            )

            with self.assertRaises(ModelCallError) as caught:
                compose_deepseek_harness(
                    store,
                    api_key="not-a-real-key",
                    model_id=" ",
                    thinking=False,
                    client=ForbiddenClient(),
                )

        self.assertEqual(caught.exception.code, "unsupported_model_profile")
        self.assertNotIn("not-a-real-key", str(caught.exception))


class DeepSeekContractRunnerTest(unittest.TestCase):
    def test_runner_skips_before_sdk_without_enable_or_key(self) -> None:
        from monmusu_agent.agentic_contract import run_deepseek_contract

        with tempfile.TemporaryDirectory() as directory:
            disabled = run_deepseek_contract(
                enabled=False,
                api_key="not-a-real-key",
                session_root=Path(directory) / "disabled",
                client=ForbiddenClient(),
            )
            missing_key = run_deepseek_contract(
                enabled=True,
                api_key=None,
                session_root=Path(directory) / "missing-key",
                client=ForbiddenClient(),
            )

        self.assertEqual(disabled.status, "skipped")
        self.assertIn("not explicitly enabled", disabled.reason)
        self.assertEqual(disabled.records, ())
        self.assertEqual(missing_key.status, "skipped")
        self.assertIn("DEEPSEEK_API_KEY", missing_key.reason)
        self.assertEqual(missing_key.records, ())

    def test_increment_three_runner_requires_explicit_enable_and_key(self) -> None:
        from monmusu_agent.agentic_contract import (
            run_increment_three_evaluation,
        )

        with tempfile.TemporaryDirectory() as directory:
            disabled = run_increment_three_evaluation(
                enabled=False,
                api_key="not-a-real-key",
                session_root=Path(directory) / "disabled",
                client=ForbiddenClient(),
            )
            missing_key = run_increment_three_evaluation(
                enabled=True,
                api_key=None,
                session_root=Path(directory) / "missing-key",
                client=ForbiddenClient(),
            )

        self.assertEqual(disabled.status, "skipped")
        self.assertIn("not explicitly enabled", disabled.reason)
        self.assertEqual(disabled.records, ())
        self.assertEqual(missing_key.status, "skipped")
        self.assertIn("DEEPSEEK_API_KEY", missing_key.reason)
        self.assertEqual(missing_key.records, ())

    def test_increment_three_runner_reports_automated_failure(
        self,
    ) -> None:
        from monmusu_agent.agentic_contract import (
            run_increment_three_evaluation,
        )

        failed_record = {"automated_passed": False}

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "monmusu_agent.agentic_contract._run_increment3_scenario_two",
                    return_value=failed_record,
                ),
                patch(
                    "monmusu_agent.agentic_contract._run_increment3_scenario_three",
                    return_value=failed_record,
                ),
            ):
                result = run_increment_three_evaluation(
                    enabled=True,
                    api_key="test-only",
                    session_root=Path(directory),
                    client=ForbiddenClient(),
                )

        self.assertEqual(result.status, "failed")
        self.assertEqual(
            result.reason,
            "Increment 3 automated gates failed",
        )

    def test_increment_three_runner_records_scenarios_without_human_pass(
        self,
    ) -> None:
        from monmusu_agent.agentic_contract import (
            run_increment_three_evaluation,
        )

        reasoning_canary = "TICKET_18_REASONING_MUST_NOT_ESCAPE"
        hidden_canary = "隐藏事实：断桥下的东西正在跟随"

        def sdk_response(
            message: dict[str, Any],
            finish_reason: str,
            *,
            usage: dict[str, int] | None = None,
        ) -> object:
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=Dumpable(message),
                        finish_reason=finish_reason,
                    )
                ],
                usage=Dumpable(usage) if usage is not None else None,
            )

        def final_message(
            narration: str,
            *,
            establish: list[dict[str, str]] | None = None,
            retire: list[dict[str, str]] | None = None,
        ) -> dict[str, Any]:
            return {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "narration": narration,
                        "establish": establish or [],
                        "retire": retire or [],
                        "session_status": "ongoing",
                    },
                    ensure_ascii=False,
                ),
                "reasoning_content": reasoning_canary,
                "tool_calls": [],
            }

        def take_key_response(kwargs: Mapping[str, Any]) -> object:
            context_message = next(
                message
                for message in kwargs["messages"]
                if message.get("role") == "user"
                and isinstance(message.get("content"), str)
                and "COMMITTED_TURNS" in message["content"]
            )
            package = json.loads(context_message["content"])
            key_fact = next(
                fact
                for fact in package["ACTIVE_FACTS"]
                if fact["text"]
                == "一把已经看见且能够直接够到的铜钥匙放在仓库桌上。"
            )
            return sdk_response(
                final_message(
                    "你拿起触手可及的铜钥匙并收好。",
                    establish=[
                        {
                            "visibility": "public",
                            "text": "调查员已经拿到仓库桌上的铜钥匙。",
                        }
                    ],
                    retire=[
                        {
                            "fact_id": key_fact["fact_id"],
                            "reason": "铜钥匙已经由调查员拿走，不再放在仓库桌上。",
                        }
                    ],
                ),
                "stop",
            )

        def tool_message(
            name: str,
            arguments: Mapping[str, object],
            call_id: str,
        ) -> dict[str, Any]:
            return {
                "role": "assistant",
                "content": None,
                "reasoning_content": reasoning_canary,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(
                                arguments,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    }
                ],
            }

        completions = ScriptedCompletions(
            [
                take_key_response,
                sdk_response(
                    tool_message(
                        "make_check",
                        {
                            "actor_id": "investigator_tracker",
                            "ability": "dexterity",
                            "difficulty": "regular",
                            "dice_adjustment": {"kind": "none", "count": 0},
                            "action": "助跑跳过被海浪冲刷的断桥",
                            "stakes": "失败会跌落受伤并引来注意",
                            "visibility": "public",
                        },
                        "call_ticket18_jump",
                    ),
                    "tool_calls",
                    usage={"prompt_tokens": 300, "completion_tokens": 60},
                ),
                sdk_response(
                    final_message(
                        "你落在断桥对面，浪花在身后砸碎。",
                        establish=[
                            {"visibility": "hidden", "text": hidden_canary}
                        ],
                    ),
                    "stop",
                ),
                sdk_response(
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "narration": "这次答复故意缺少 session_status。",
                                "establish": [],
                                "retire": [],
                            },
                            ensure_ascii=False,
                        ),
                        "reasoning_content": reasoning_canary,
                        "tool_calls": [],
                    },
                    "stop",
                ),
                sdk_response(
                    final_message(
                        "门仍未打开，逼近的脚步更清晰；拆门轴会冒着卡死门的风险。"
                    ),
                    "stop",
                ),
                sdk_response(
                    tool_message(
                        "push_check",
                        {
                            "check_id": "mechanic_ticket18_scenario3_base",
                            "new_approach": "改从铰链侧拆卸门轴",
                            "failure_stakes": "失败会卡死门轴并让逼近者听见断裂声",
                        },
                        "call_ticket18_push",
                    ),
                    "tool_calls",
                ),
                sdk_response(
                    final_message("门轴被卸下，原来的失败骰和幸运余额都没有改变。"),
                    "stop",
                ),
            ]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        api_key = "sk-ticket-18-test-secret"

        with tempfile.TemporaryDirectory() as directory:
            result = run_increment_three_evaluation(
                enabled=True,
                api_key=api_key,
                session_root=Path(directory) / "sessions",
                client=client,
            )

        self.assertEqual(result.status, "pending_human")
        self.assertIn("human", result.reason.lower())
        self.assertEqual(len(result.records), 2)
        scenario_two, scenario_three = result.records
        self.assertEqual(
            [record["scenario_version"] for record in result.records],
            [
                "ticket-18-focused-scenario-2-v1",
                "ticket-18-focused-scenario-3-v1",
            ],
        )
        expected_tools = [
            "make_check",
            "push_check",
            "spend_luck",
            "deal_damage",
            "make_sanity_check",
        ]
        self.assertEqual(
            scenario_two["player_inputs"],
            [
                "我拿起桌上的铜钥匙收好。",
                "趁下一股浪还没打来，我助跑跳过断桥，去对面的门楼。",
            ],
        )
        self.assertEqual(
            scenario_three["player_inputs"],
            [
                "先别替我花幸运，也不要自动重掷。告诉我门没开以后现在发生了什么；如果换一种办法孤注一掷，我要承担什么更严重的风险？",
                "我不花幸运。我拆门轴，从铰链这边强行卸门；我接受你刚才说的更严重后果，孤注一掷。",
            ],
        )
        for record in result.records:
            self.assertTrue(record["automated_passed"])
            self.assertFalse(record["passed"])
            self.assertEqual(record["human_judgment"]["status"], "pending_user")
            self.assertEqual(
                record["fixture"]["version"],
                {
                    "ticket-18-focused-scenario-2-v1": "ticket-18-scenario-2-fixture-v2",
                    "ticket-18-focused-scenario-3-v1": "ticket-18-scenario-3-fixture-v2",
                }[record["scenario_version"]],
            )
            self.assertIn("python", record["dependency_versions"])
            self.assertEqual(
                record["attempt_limits"],
                {
                    "max_round_trips": 8,
                    "request_timeout_seconds": 60,
                    "attempt_timeout_seconds": 180,
                    "max_structure_repairs": 1,
                },
            )
            self.assertEqual(record["quality_scores"], {
                "fictional_causality": None,
                "improvisation": None,
                "cross_turn_continuity": None,
                "npc_performance": None,
                "pacing": None,
                "atmosphere": None,
            })
            for request in record["requests"]:
                self.assertEqual(
                    request["function_tools"],
                    [] if request["structure_repair_request"] else expected_tools,
                )
                self.assertEqual(
                    request["response_format"],
                    {"type": "json_object"},
                )
                self.assertFalse(request["stream"])
                self.assertEqual(request["timeout"], 60.0)
                self.assertEqual(
                    request["messages_sha256"],
                    request["model_request_messages_sha256"],
                )

        self.assertEqual(len(scenario_two["requests"]), 3)
        self.assertEqual(len(scenario_three["requests"]), 4)
        self.assertEqual(
            [
                request["structure_repair_request"]
                for request in scenario_three["requests"]
            ],
            [False, True, False, False],
        )
        self.assertEqual(
            [
                request["structure_repairs"]
                for request in scenario_three["requests"]
            ],
            [0, 1, 0, 0],
        )

        self.assertEqual(
            [call["tool_name"] for call in scenario_two["tool_calls"]],
            ["make_check"],
        )
        self.assertEqual(
            [call["tool_name"] for call in scenario_three["tool_calls"]],
            ["push_check"],
        )
        self.assertEqual(
            scenario_three["mechanics"][0]["details"]["pushed_from"],
            "mechanic_ticket18_scenario3_base",
        )
        fixture_narrations = {
            "ticket-18-focused-scenario-2-v1": (
                "调查员已经离开石牢，来到无人看守的仓库。桌上放着一把"
                "触手可及的铜钥匙；仓库外的断桥正被海浪间歇冲刷。"
            ),
            "ticket-18-focused-scenario-3-v1": (
                "撬棍滑开，门仍未打开；先前远去的脚步已经转向并逼近，"
                "成为再次失败时的更严重风险。"
            ),
        }
        for record in result.records:
            first_request = record["requests"][0]
            second_request = next(
                request
                for request in record["requests"][1:]
                if not request["structure_repair_request"]
            )
            tool_result_request = record["requests"][-1]
            first_projection = first_request["message_projection"]
            second_projection = second_request["message_projection"]
            tool_result_projection = tool_result_request["message_projection"]
            self.assertEqual(
                first_projection["current_player_input_sha256"],
                hashlib.sha256(
                    record["player_inputs"][0].encode("utf-8")
                ).hexdigest(),
            )
            self.assertEqual(
                second_projection["current_player_input_sha256"],
                hashlib.sha256(
                    record["player_inputs"][1].encode("utf-8")
                ).hexdigest(),
            )
            self.assertEqual(
                tool_result_projection["current_player_input_sha256"],
                second_projection["current_player_input_sha256"],
            )
            first_turns = first_projection["context"]["committed_turns"]
            later_turns = second_projection["context"]["committed_turns"]
            self.assertGreaterEqual(len(first_turns), 1)
            self.assertEqual(len(later_turns), len(first_turns) + 1)
            self.assertEqual(
                tool_result_projection["context"],
                second_projection["context"],
            )
            expected_mechanic_id = record["mechanics"][0]["mechanic_id"]
            projected_tool_result = next(
                message
                for message in tool_result_projection["messages"]
                if message["role"] == "tool"
                and message["result_mechanic_id"] == expected_mechanic_id
            )
            self.assertRegex(projected_tool_result["result_sha256"], r"^[0-9a-f]{64}$")
            fixture_turn = first_turns[-1]
            fixture_fact_ids = {
                change["fact_id"]
                for change in record["fixture"]["public_fact_changes"]
                if change["kind"] == "established"
            }
            retired_fact_ids = {
                change["fact_id"]
                for change in record["fixture"]["public_fact_changes"]
                if change["kind"] == "retired"
            }
            self.assertTrue(fixture_fact_ids)
            self.assertEqual(
                set(fixture_turn["established_fact_ids"]),
                fixture_fact_ids,
            )
            self.assertEqual(
                set(fixture_turn["retired_fact_ids"]),
                retired_fact_ids,
            )
            self.assertEqual(
                fixture_turn["narration_sha256"],
                hashlib.sha256(
                    fixture_narrations[record["scenario_version"]].encode("utf-8")
                ).hexdigest(),
            )
            self.assertEqual(
                {
                    item["fact_id"]: item["text_sha256"]
                    for item in fixture_turn["established_facts"]
                },
                {
                    change["fact_id"]: hashlib.sha256(
                        change["text"].encode("utf-8")
                    ).hexdigest()
                    for change in record["fixture"]["public_fact_changes"]
                    if change["kind"] == "established"
                },
            )
            for mechanic in fixture_turn["mechanics"]:
                self.assertRegex(mechanic["result_sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(
                fixture_fact_ids
                <= set(first_projection["context"]["active_fact_ids"])
            )
            self.assertTrue(
                retired_fact_ids.isdisjoint(
                    first_projection["context"]["active_fact_ids"]
                )
            )
        scenario_two_fixture_turn = scenario_two["requests"][0][
            "message_projection"
        ]["context"]["committed_turns"][-1]
        key_fact_id = next(
            item["fact_id"]
            for item in scenario_two_fixture_turn["established_facts"]
            if item["text_sha256"]
            == hashlib.sha256(
                "一把已经看见且能够直接够到的铜钥匙放在仓库桌上。".encode(
                    "utf-8"
                )
            ).hexdigest()
        )
        self.assertNotIn(
            key_fact_id,
            scenario_two["requests"][1]["message_projection"]["context"][
                "active_fact_ids"
            ],
        )
        scenario_two_fixture_texts = {
            change["text"]
            for change in scenario_two["fixture"]["public_fact_changes"]
            if change["kind"] == "established"
        }
        self.assertEqual(
            scenario_two_fixture_texts,
            {
                "调查员已经在无人看守的仓库内。",
                "一把已经看见且能够直接够到的铜钥匙放在仓库桌上。",
                "仓库外是一段被海浪间歇冲刷的断桥。",
                "从断桥跌落会受伤并引来注意。",
            },
        )
        scenario_three_fixture_texts = {
            change["text"]
            for change in scenario_three["fixture"]["public_fact_changes"]
            if change["kind"] == "established"
        }
        self.assertEqual(
            scenario_three_fixture_texts,
            {
                "牢门仍未打开。",
                "逼近的脚步已经更清晰，并构成孤注一掷失败时的更严重风险。",
            },
        )
        self.assertEqual(
            scenario_three["fixture"]["mechanics"][0]["mechanic_id"],
            "mechanic_ticket18_scenario3_base",
        )
        self.assertEqual(
            scenario_three["fixture"]["mechanics"][0]["details"],
            {
                "ability": "locksmith",
                "ability_value": 55,
                "difficulty": "regular",
                "target": 55,
                "dice_adjustment": {"kind": "none", "count": 0},
                "roll": 60,
                "success_level": "failure",
                "action": "撬开牢门上的生锈锁扣",
                "stakes": "失败会耽搁时间，让逼近的脚步更清晰",
            },
        )
        self.assertEqual(
            scenario_three["resource_changes"]["luck"],
            {"before": 50, "after": 50},
        )
        self.assertEqual(
            scenario_two["hard_gates"]["investigator_ownership"]["status"],
            "pending_human",
        )
        rendered = json.dumps(result.records, ensure_ascii=False)
        for forbidden in (
            api_key,
            reasoning_canary,
            hidden_canary,
            "Authorization",
            "Bearer",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_runner_records_both_paths_in_redacted_evaluation_format(
        self,
    ) -> None:
        from monmusu_agent.agentic_contract import run_deepseek_contract

        hidden_fact = "隐藏事实：守卫其实已经醒来"
        hidden_reasoning = "provider hidden reasoning must stay private"
        private_diagnostic = "private provider diagnostic"
        direct_final = {
            "narration": "你收好触手可及的铜钥匙。",
            "establish": [
                {"visibility": "hidden", "text": hidden_fact}
            ],
            "retire": [],
            "session_status": "ongoing",
        }
        tool_arguments = {
            "actor_id": "investigator_tracker",
            "ability": "strength",
            "difficulty": "regular",
            "dice_adjustment": {"kind": "none", "count": 0},
            "action": "用肩膀猛撞锈蚀牢门，试图撞断锁扣",
            "stakes": "失败会发出巨响，引来正在远去的船工",
            "visibility": "public",
        }
        tool_message = {
            "role": "assistant",
            "content": None,
            "reasoning_content": hidden_reasoning,
            "tool_calls": [
                {
                    "id": "call_contract_jump",
                    "type": "function",
                    "function": {
                        "name": "make_check",
                        "arguments": json.dumps(
                            tool_arguments,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                }
            ],
        }
        tool_final = {
            "narration": "锁扣在撞击下断裂，牢门向外弹开。",
            "establish": [
                {
                    "visibility": "hidden",
                    "text": "隐藏事实：船工听见了牢门方向的巨响",
                }
            ],
            "retire": [],
            "session_status": "ongoing",
        }
        completions = ScriptedCompletions(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(
                                {
                                    "role": "assistant",
                                    "content": json.dumps(
                                        direct_final,
                                        ensure_ascii=False,
                                    ),
                                    "reasoning_content": hidden_reasoning,
                                    "tool_calls": [],
                                }
                            ),
                            finish_reason="stop",
                        )
                    ],
                    usage=None,
                    private_diagnostic=private_diagnostic,
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(tool_message),
                            finish_reason="tool_calls",
                        )
                    ],
                    usage=Dumpable(
                        {
                            "prompt_tokens": 200,
                            "completion_tokens": 80,
                            "total_tokens": 280,
                            "prompt_cache_hit_tokens": 120,
                            "private_diagnostic": private_diagnostic,
                            "Authorization": "Bearer private-auth-material",
                        }
                    ),
                    private_diagnostic=private_diagnostic,
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(
                                {
                                    "role": "assistant",
                                    "content": json.dumps(
                                        tool_final,
                                        ensure_ascii=False,
                                    ),
                                    "reasoning_content": hidden_reasoning,
                                    "tool_calls": [],
                                }
                            ),
                            finish_reason="stop",
                        )
                    ],
                    usage=None,
                    private_diagnostic=private_diagnostic,
                ),
            ]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        api_key = "sk-contract-secret-fragment"

        with tempfile.TemporaryDirectory() as directory:
            result = run_deepseek_contract(
                enabled=True,
                api_key=api_key,
                session_root=Path(directory) / "sessions",
                client=client,
            )

        self.assertEqual(result.status, "passed")
        self.assertEqual(len(result.records), 2)
        direct_record, tool_record = result.records
        self.assertEqual(
            tool_record["player_input"],
            (
                "我用肩膀猛撞锈蚀的牢门，想把锁扣撞断；失败会发出巨响，"
                "引来正在远去的船工。请用一次公开力量检定结算这项有真实"
                "不确定性的行动。"
            ),
        )
        self.assertEqual(
            tool_record["scenario_version"],
            "ticket-04-tool-then-final-v2",
        )
        expected_fields = {
            "run_id",
            "scenario_version",
            "executed_at",
            "evaluator",
            "git_revision",
            "prompt_version",
            "module_revision",
            "character_revision",
            "model_id",
            "thinking",
            "provider_parameters",
            "tool_schema_version",
            "player_input",
            "player_visible_output",
            "tool_calls",
            "mechanics",
            "fact_changes",
            "requests",
            "hard_gates",
            "quality_scores",
            "rationale",
            "passed",
        }
        self.assertEqual(set(direct_record), expected_fields)
        self.assertEqual(set(tool_record), expected_fields)
        self.assertEqual(direct_record["requests"][0]["usage"], None)
        self.assertEqual(
            tool_record["tool_calls"],
            [
                {
                    "tool_call_id": "call_contract_jump",
                    "tool_name": "make_check",
                }
            ],
        )
        self.assertEqual(
            tool_record["requests"][1]["tool_result_ids"],
            ["call_contract_jump"],
        )
        for request in (
            direct_record["requests"] + tool_record["requests"]
        ):
            self.assertEqual(request["function_tools"], ["make_check"])
            self.assertEqual(
                request["response_format"],
                {"type": "json_object"},
            )
            self.assertFalse(request["stream"])
        self.assertTrue(direct_record["passed"])
        self.assertTrue(tool_record["passed"])
        self.assertEqual(len(direct_record["hard_gates"]), 6)
        self.assertEqual(len(direct_record["quality_scores"]), 6)
        rendered = json.dumps(result.records, ensure_ascii=False)
        for forbidden in (
            api_key,
            hidden_fact,
            "隐藏事实：桥下有东西跟随",
            hidden_reasoning,
            private_diagnostic,
            "Authorization",
            "Bearer",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_contract_main_prints_clear_skip_without_key(self) -> None:
        from monmusu_agent.agentic_contract import main

        output = io.StringIO()
        with (
            patch("monmusu_agent.agentic_contract.load_dotenv"),
            patch.dict(
                os.environ,
                {"MONMUSU_RUN_DEEPSEEK_CONTRACT": "1"},
                clear=True,
            ),
            redirect_stdout(output),
        ):
            exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertIn("SKIP", output.getvalue())
        self.assertIn("DEEPSEEK_API_KEY", output.getvalue())

    def test_increment_three_main_preserves_pending_human_boundary(self) -> None:
        from monmusu_agent.agentic_contract import ContractRunResult, main

        output = io.StringIO()
        pending = ContractRunResult(
            status="pending_human",
            reason="automated gates passed; human judgment is pending",
            records=(),
        )
        with (
            patch("monmusu_agent.agentic_contract.load_dotenv"),
            patch(
                "monmusu_agent.agentic_contract.run_increment_three_evaluation",
                return_value=pending,
            ),
            patch.dict(
                os.environ,
                {
                    "MONMUSU_RUN_INCREMENT3_EVALUATION": "1",
                    "DEEPSEEK_API_KEY": "sk-main-test-secret",
                },
                clear=True,
            ),
            redirect_stdout(output),
        ):
            exit_code = main()

        self.assertEqual(exit_code, 1)
        self.assertIn('"status": "pending_human"', output.getvalue())
        self.assertIn("human judgment is pending", output.getvalue())
        self.assertNotIn("sk-main-test-secret", output.getvalue())

    def test_recovery_runner_proves_both_thinking_profiles_without_private_material(
        self,
    ) -> None:
        from monmusu_agent.agentic_contract import run_deepseek_recovery_contract

        reasoning_canary = "TICKET_11_REASONING_CANARY_MUST_NOT_ESCAPE"
        tool_arguments = {
            "actor_id": "investigator_tracker",
            "ability": "strength",
            "difficulty": "regular",
            "dice_adjustment": {"kind": "none", "count": 0},
            "action": "撞击锈蚀牢门的锁扣",
            "stakes": "失败会发出巨响并引来船工",
            "visibility": "public",
        }

        def sdk_response(
            message: dict[str, Any],
            finish_reason: str,
            usage: dict[str, int] | None = None,
        ) -> object:
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=Dumpable(message),
                        finish_reason=finish_reason,
                    )
                ],
                usage=Dumpable(usage) if usage is not None else None,
            )

        def tool_message(
            *,
            thinking: bool,
            call_id: str = "call_ticket_11",
        ) -> dict[str, Any]:
            return {
                "role": "assistant",
                "content": "先确认锁扣的承重点。" if thinking else None,
                "reasoning_content": reasoning_canary if thinking else None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": "make_check",
                            "arguments": json.dumps(
                                tool_arguments,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    }
                ],
            }

        def final_message(*, thinking: bool) -> dict[str, Any]:
            return {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "narration": "锁扣在撞击下松脱，但巨响已经传开。",
                        "establish": [
                            {
                                "visibility": "hidden",
                                "text": "隐藏事实：船工正在回头",
                            }
                        ],
                        "retire": [],
                        "session_status": "ongoing",
                    },
                    ensure_ascii=False,
                ),
                "reasoning_content": reasoning_canary if thinking else None,
                "tool_calls": [],
            }

        completions = ScriptedCompletions(
            [
                sdk_response(
                    tool_message(thinking=False),
                    "tool_calls",
                    {"prompt_tokens": 100, "completion_tokens": 20},
                ),
                sdk_response(
                    final_message(thinking=False),
                    "stop",
                    {"prompt_tokens": 150, "completion_tokens": 30},
                ),
                sdk_response(
                    tool_message(thinking=True),
                    "tool_calls",
                    {"prompt_tokens": 200, "completion_tokens": 40},
                ),
                sdk_response(
                    final_message(thinking=True),
                    "stop",
                    {"prompt_tokens": 250, "completion_tokens": 50},
                ),
            ]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )

        with tempfile.TemporaryDirectory() as directory:
            result = run_deepseek_recovery_contract(
                enabled=True,
                api_key="sk-ticket-11-test-secret",
                session_root=Path(directory) / "sessions",
                client=client,
            )

        self.assertEqual(result.status, "passed")
        self.assertEqual(len(result.records), 2)
        for record, thinking in zip(
            result.records,
            (False, True),
            strict=True,
        ):
            self.assertIs(record["thinking"], thinking)
            recovery = record["recovery"]
            self.assertTrue(recovery["same_turn_id"])
            self.assertTrue(recovery["same_mechanic"])
            self.assertTrue(recovery["resume_gate_observed"])
            self.assertEqual(recovery["resume_choice"], "resume")
            self.assertEqual(recovery["turn_count"], 1)
            self.assertEqual(recovery["mechanic_count"], 1)
            self.assertTrue(recovery["turn_ids_unique"])
            self.assertTrue(recovery["established_fact_ids_unique"])
            self.assertTrue(recovery["fact_ids_unique"])
            self.assertEqual(recovery["character_changes"], [])
            self.assertEqual(
                recovery["initial_interruption"], "request_timeout"
            )
            self.assertTrue(recovery["tool_result_replayed"])
            self.assertTrue(recovery["reasoning_replay_exact"] if thinking else True)
            self.assertFalse(recovery["reasoning_body_recorded"])
            self.assertTrue(recovery["final_state_clean"])
            self.assertEqual(
                record["requests"][0]["tool_calls"],
                [{"tool_call_id": "call_ticket_11", "tool_name": "make_check"}],
            )
            self.assertEqual(
                record["requests"][-1]["tool_result_ids"],
                ["call_ticket_11"],
            )
            self.assertTrue(record["game_id"].startswith("game_"))
            self.assertTrue(record["fixture_version"].startswith("setup_game_"))
            self.assertEqual(
                set(record["dependency_versions"]),
                {"python", "openai", "python-dotenv"},
            )
            self.assertEqual(
                [
                    record["hard_gates"][gate]["status"]
                    for gate in (
                        "protocol_legality",
                        "mechanical_truth",
                        "hidden_content_control",
                    )
                ],
                ["passed", "passed", "passed"],
            )

        rendered = json.dumps(result.records, ensure_ascii=False)
        for forbidden in (
            "sk-ticket-11-test-secret",
            reasoning_canary,
            "隐藏事实：船工正在回头",
        ):
            self.assertNotIn(forbidden, rendered)

        duplicate_completions = ScriptedCompletions(
            [
                sdk_response(tool_message(thinking=False), "tool_calls"),
                sdk_response(
                    tool_message(
                        thinking=False,
                        call_id="call_ticket_11_duplicate",
                    ),
                    "tool_calls",
                ),
                sdk_response(final_message(thinking=False), "stop"),
                sdk_response(tool_message(thinking=True), "tool_calls"),
                sdk_response(final_message(thinking=True), "stop"),
            ]
        )
        duplicate_client = SimpleNamespace(
            chat=SimpleNamespace(completions=duplicate_completions)
        )
        with tempfile.TemporaryDirectory() as directory:
            duplicate_result = run_deepseek_recovery_contract(
                enabled=True,
                api_key="sk-ticket-11-test-secret",
                session_root=Path(directory) / "sessions",
                client=duplicate_client,
            )

        duplicate_record = duplicate_result.records[0]
        self.assertEqual(duplicate_result.status, "failed")
        self.assertFalse(duplicate_record["passed"])
        self.assertEqual(duplicate_record["recovery"]["mechanic_count"], 2)
        self.assertEqual(
            duplicate_record["hard_gates"]["mechanical_truth"]["status"],
            "failed",
        )


    def test_retry_contract_recovers_through_real_adapter_boundary(self) -> None:
        """注入一次可重试 provider 错误后，契约通过真实 adapter 边界恢复并提交。"""

        from monmusu_agent.agentic_contract import run_deepseek_retry_contract

        final = {
            "narration": "你拿起铜钥匙。",
            "establish": [],
            "retire": [],
            "session_status": "ongoing",
        }
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=Dumpable(
                        {
                            "role": "assistant",
                            "content": json.dumps(final, ensure_ascii=False),
                            "reasoning_content": None,
                            "tool_calls": [],
                        }
                    ),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
        completions = RecordingCompletions(response)
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )

        with tempfile.TemporaryDirectory() as directory:
            result = run_deepseek_retry_contract(
                enabled=True,
                api_key="sk-retry-observation-test",
                session_root=Path(directory) / "sessions",
                client=client,
                retry_sleep=lambda seconds: None,
            )

        self.assertEqual(result.status, "passed")
        self.assertEqual(len(completions.requests), 1)
        observed = result.records[0]
        self.assertEqual(observed["request_attempts"], 2)
        self.assertEqual(observed["sdk_requests"], 1)
        self.assertEqual(
            observed["first_attempt_local_error_category"],
            "provider_server_error",
        )
        self.assertEqual(observed["second_attempt_finish_reason"], "stop")
        self.assertEqual(
            observed["first_scheduled_retry"],
            {"code": "provider_server_error", "delay_ms": 1000, "status": 503},
        )


if __name__ == "__main__":
    unittest.main()
