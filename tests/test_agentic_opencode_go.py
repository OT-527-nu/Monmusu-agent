from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from monmusu_agent.agentic_contract import ContractRunResult, run_opencode_go_contract
from monmusu_agent.agentic_harness import AgenticHarness
from monmusu_agent.agentic_model import (
    DeepSeekGameMasterModel,
    ModelProfileValidationError,
    ModelRequest,
    deepseek_model_profile,
)
from monmusu_agent.agentic_session import AgenticSessionStore, NewSessionRequest

OPENCODE_GO_BASE = "https://opencode.ai/zen/go/v1"
MAKE_CHECK_ARGUMENTS = json.dumps(
    {
        "actor_id": "investigator_tracker",
        "ability": "strength",
        "difficulty": "regular",
        "dice_adjustment": {"kind": "none", "count": 0},
        "action": "用肩膀猛撞锈蚀的牢门，试图撞断锁扣",
        "stakes": "失败会发出巨响，引来正在远去的船工",
        "visibility": "public",
    },
    ensure_ascii=False,
    separators=(",", ":"),
)


def _tool_assistant(reasoning_content: object) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "reasoning_content": reasoning_content,
        "tool_calls": [
            {
                "id": "call_opencode_go_001",
                "type": "function",
                "function": {
                    "name": "make_check",
                    "arguments": MAKE_CHECK_ARGUMENTS,
                },
            }
        ],
    }


def _final_assistant() -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": json.dumps(
            {
                "narration": "锁扣在撞击下断裂，牢门向外弹开。",
                "establish": [],
                "retire": [],
                "session_status": "ongoing",
            },
            ensure_ascii=False,
        ),
        "reasoning_content": None,
        "tool_calls": [],
    }


class Dumpable:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        if mode != "json":
            raise AssertionError("adapter 必须请求可序列化 SDK 数据")
        return copy.deepcopy(self.payload)


class ScriptedCompletions:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> object:
        self.requests.append(copy.deepcopy(kwargs))
        if not self.responses:
            raise AssertionError("mock SDK 没有剩余响应")
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, completions: ScriptedCompletions) -> None:
        self.completions = completions
        self.chat = SimpleNamespace(completions=completions)


class OpencodeGoProfileTest(unittest.TestCase):
    def test_default_profile_uses_opencode_go_endpoint_and_flash(self) -> None:
        profile = deepseek_model_profile(
            provider="opencode-go",
            enabled_tools=("make_check",),
        )
        self.assertEqual(profile["provider"], "opencode-go")
        self.assertEqual(profile["base_url"], OPENCODE_GO_BASE)
        self.assertEqual(profile["model_id"], "deepseek-v4-flash")
        self.assertFalse(profile["thinking"])

    def test_thinking_true_is_rejected_before_model_call(self) -> None:
        with self.assertRaises(ModelProfileValidationError):
            deepseek_model_profile(provider="opencode-go", thinking=True)

    def test_non_flash_model_is_rejected_before_model_call(self) -> None:
        with self.assertRaises(ModelProfileValidationError):
            deepseek_model_profile(
                provider="opencode-go",
                model_id="deepseek-v4-pro",
            )

    def test_harness_rejects_thinking_profile_from_caller(self) -> None:
        profile = deepseek_model_profile()
        profile["provider"] = "opencode-go"
        profile["base_url"] = OPENCODE_GO_BASE
        profile["thinking"] = True
        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions"
            )
            with self.assertRaisesRegex(Exception, "provider|thinking"):
                AgenticHarness(store, object(), model_profile=profile)


class OpencodeGoAdapterTest(unittest.TestCase):
    def _model(self, completions: ScriptedCompletions) -> DeepSeekGameMasterModel:
        return DeepSeekGameMasterModel(
            "sk-opencode-go-secret",
            base_url=OPENCODE_GO_BASE,
            client=FakeClient(completions),
        )

    def _request(self) -> ModelRequest:
        profile = deepseek_model_profile(
            provider="opencode-go",
            enabled_tools=("make_check",),
        )
        return ModelRequest(
            messages=(
                {"role": "user", "content": "测试行动"},
            ),
            tools=(),
            request_timeout_seconds=30,
            model_profile=profile,
        )

    def test_sdk_request_uses_opencode_go_shape_and_thinking_disabled(self) -> None:
        completions = ScriptedCompletions(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(_final_assistant()),
                            finish_reason="stop",
                        )
                    ],
                    usage=None,
                )
            ]
        )
        model = self._model(completions)
        model.complete(self._request())
        request = completions.requests[0]
        self.assertEqual(request["model"], "deepseek-v4-flash")
        self.assertNotIn("response_format", request)
        self.assertFalse(request["stream"])
        self.assertEqual(request["timeout"], 30)
        self.assertEqual(
            request["extra_body"],
            {"thinking": {"type": "disabled"}},
        )

    def test_null_or_missing_reasoning_becomes_empty_for_opencode_go(self) -> None:
        for message in (_tool_assistant(None), _tool_assistant("")):
            completions = ScriptedCompletions(
                [
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                message=Dumpable(message),
                                finish_reason="tool_calls",
                            )
                        ],
                        usage=None,
                    )
                ]
            )
            model = self._model(completions)
            response = model.complete(self._request())
            self.assertEqual(
                response.assistant_message["reasoning_content"],
                "",
            )

    def test_missing_reasoning_key_is_added_for_opencode_go(self) -> None:
        message = _tool_assistant(None)
        del message["reasoning_content"]
        completions = ScriptedCompletions(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(message),
                            finish_reason="tool_calls",
                        )
                    ],
                    usage=None,
                )
            ]
        )
        response = self._model(completions).complete(self._request())
        self.assertEqual(response.assistant_message["reasoning_content"], "")

    def test_opencode_go_reasoning_field_is_normalized_to_reasoning_content(
        self,
    ) -> None:
        message = _tool_assistant(None)
        del message["reasoning_content"]
        message["reasoning"] = "opencode-go wire reasoning field"
        completions = ScriptedCompletions(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(message),
                            finish_reason="tool_calls",
                        )
                    ],
                    usage=None,
                )
            ]
        )
        response = self._model(completions).complete(self._request())
        self.assertEqual(
            response.assistant_message["reasoning_content"],
            "opencode-go wire reasoning field",
        )
        self.assertNotIn("reasoning", response.assistant_message)

    def test_deepseek_official_keeps_null_reasoning_content(self) -> None:
        profile = deepseek_model_profile(enabled_tools=("make_check",))
        completions = ScriptedCompletions(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(_tool_assistant(None)),
                            finish_reason="tool_calls",
                        )
                    ],
                    usage=None,
                )
            ]
        )
        model = DeepSeekGameMasterModel(
            "sk-deepseek-secret",
            client=FakeClient(completions),
        )
        response = model.complete(
            ModelRequest(
                messages=(),
                tools=(),
                request_timeout_seconds=30,
                model_profile=profile,
            )
        )
        self.assertIsNone(response.assistant_message["reasoning_content"])

    def test_replayed_assistant_message_carries_empty_reasoning_content(self) -> None:
        completions = ScriptedCompletions(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(_tool_assistant(None)),
                            finish_reason="tool_calls",
                        )
                    ],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(_final_assistant()),
                            finish_reason="stop",
                        )
                    ],
                    usage=None,
                ),
            ]
        )
        profile = deepseek_model_profile(
            provider="opencode-go",
            enabled_tools=("make_check",),
        )
        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_opencode_go",
            )
            created = store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_tracker",
                    display_name="契约测试调查员",
                )
            )
            model = DeepSeekGameMasterModel(
                "sk-opencode-go-secret",
                base_url=OPENCODE_GO_BASE,
                client=FakeClient(completions),
            )
            harness = AgenticHarness(
                store,
                model,
                model_profile=profile,
                turn_id_factory=lambda: "turn_opencode_go",
            )
            harness.start_turn(created.game_id, "我猛撞牢门。")

        second_request = completions.requests[1]
        assistant_messages = [
            message
            for message in second_request["messages"]
            if message.get("role") == "assistant"
        ]
        self.assertEqual(len(assistant_messages), 1)
        self.assertEqual(assistant_messages[0]["reasoning_content"], "")


class OpencodeGoContractRunnerTest(unittest.TestCase):
    def test_runner_accepts_structure_repair_after_tool_result(self) -> None:
        invalid_final = {
            "role": "assistant",
            "content": "not-a-json-object",
            "reasoning_content": "",
            "tool_calls": [],
        }
        completions = ScriptedCompletions(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(_final_assistant()),
                            finish_reason="stop",
                        )
                    ],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(_tool_assistant(None)),
                            finish_reason="tool_calls",
                        )
                    ],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(invalid_final),
                            finish_reason="stop",
                        )
                    ],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(_final_assistant()),
                            finish_reason="stop",
                        )
                    ],
                    usage=None,
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            result = run_opencode_go_contract(
                enabled=True,
                api_key="sk-opencode-go-secret",
                session_root=Path(directory) / "sessions",
                client=FakeClient(completions),
            )
        self.assertEqual(result.status, "passed")
        self.assertTrue(result.records[1]["passed"])
        self.assertTrue(
            any(
                request["structure_repair_request"]
                for request in result.records[1]["requests"]
            )
        )

    def test_runner_accepts_extra_tool_round_when_first_tool_result_replays(
        self,
    ) -> None:
        completions = ScriptedCompletions(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(_final_assistant()),
                            finish_reason="stop",
                        )
                    ],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(_tool_assistant(None)),
                            finish_reason="tool_calls",
                        )
                    ],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(_tool_assistant(None)),
                            finish_reason="tool_calls",
                        )
                    ],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(_final_assistant()),
                            finish_reason="stop",
                        )
                    ],
                    usage=None,
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            result = run_opencode_go_contract(
                enabled=True,
                api_key="sk-opencode-go-secret",
                session_root=Path(directory) / "sessions",
                client=FakeClient(completions),
            )
        self.assertEqual(result.status, "passed")
        self.assertEqual(len(result.records), 2)
        tool_record = result.records[1]
        self.assertTrue(tool_record["passed"])
        self.assertEqual(len(tool_record["mechanics"]), 1)

    def test_runner_skips_without_enable_or_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            disabled = run_opencode_go_contract(
                enabled=False,
                api_key="sk-secret",
                session_root=Path(directory) / "sessions",
            )
            missing_key = run_opencode_go_contract(
                enabled=True,
                api_key=None,
                session_root=Path(directory) / "sessions",
            )
        self.assertEqual(disabled.status, "skipped")
        self.assertEqual(missing_key.status, "skipped")
        self.assertIn("OPENCODE_GO_API_KEY", missing_key.reason)

    def test_runner_records_redacted_opencode_go_contract_with_fake_sdk(self) -> None:
        hidden_reasoning = "provider hidden reasoning must stay private"
        completions = ScriptedCompletions(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(_final_assistant()),
                            finish_reason="stop",
                        )
                    ],
                    usage=None,
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(
                                {
                                    **_tool_assistant(hidden_reasoning),
                                    "reasoning_content": hidden_reasoning,
                                }
                            ),
                            finish_reason="tool_calls",
                        )
                    ],
                    usage=Dumpable(
                        {
                            "prompt_tokens": 200,
                            "completion_tokens": 80,
                            "total_tokens": 280,
                        }
                    ),
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=Dumpable(_final_assistant()),
                            finish_reason="stop",
                        )
                    ],
                    usage=None,
                ),
            ]
        )
        api_key = "sk-opencode-go-secret-fragment"
        with tempfile.TemporaryDirectory() as directory:
            result = run_opencode_go_contract(
                enabled=True,
                api_key=api_key,
                session_root=Path(directory) / "sessions",
                client=FakeClient(completions),
            )

        self.assertIsInstance(result, ContractRunResult)
        self.assertEqual(result.status, "passed")
        self.assertEqual(len(result.records), 2)
        direct_record, tool_record = result.records
        self.assertEqual(direct_record["provider"], "opencode-go")
        self.assertEqual(direct_record["base_url"], OPENCODE_GO_BASE)
        self.assertEqual(tool_record["provider"], "opencode-go")
        self.assertEqual(tool_record["tool_calls"], [
            {
                "tool_call_id": "call_opencode_go_001",
                "tool_name": "make_check",
            }
        ])
        rendered = json.dumps(result.records, ensure_ascii=False)
        for forbidden in (api_key, hidden_reasoning, "Authorization", "Bearer"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
