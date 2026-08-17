import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from monmusu_agent.agentic_harness import AgenticHarness
from monmusu_agent.agentic_model import (
    ModelCallError,
    ModelResponse,
    ScriptedGameMasterModel,
    deepseek_model_profile,
)
from monmusu_agent.agentic_session import (
    AgenticSessionStore,
    NewSessionRequest,
)
from monmusu_agent.storage import read_json, write_json_atomic


class FixedRandom:
    def __init__(self) -> None:
        self._values = iter((3, 4))

    def randint(self, minimum: int, maximum: int) -> int:
        if (minimum, maximum) != (0, 9):
            raise AssertionError("unexpected random range")
        return next(self._values)


def final_response() -> ModelResponse:
    final = {
        "narration": "重试后仍由同一 GM 完成主持。",
        "establish": [],
        "retire": [],
        "session_status": "ongoing",
    }
    return ModelResponse(
        assistant_message={
            "role": "assistant",
            "content": json.dumps(final, ensure_ascii=False),
            "reasoning_content": None,
            "tool_calls": [],
        },
        finish_reason="stop",
        usage=None,
        latency_ms=10,
    )


def tool_response() -> ModelResponse:
    arguments = {
        "actor_id": "investigator_tracker",
        "ability": "spot_hidden",
        "difficulty": "regular",
        "dice_adjustment": {"kind": "none", "count": 0},
        "action": "检查牢门",
        "stakes": "失败会错过痕迹",
        "visibility": "public",
    }
    return ModelResponse(
        assistant_message={
            "role": "assistant",
            "content": None,
            "reasoning_content": None,
            "tool_calls": [
                {
                    "id": "call_retry_test",
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
        },
        finish_reason="tool_calls",
        usage=None,
        latency_ms=10,
    )


class HarnessRetryTest(unittest.TestCase):
    @staticmethod
    def _create_session(root: Path) -> tuple[AgenticSessionStore, str]:
        store = AgenticSessionStore(
            session_root=root / "sessions",
            game_id_factory=lambda: "game_retry_0001",
            clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
        )
        created = store.create_session(
            NewSessionRequest(
                investigator_id="investigator_tracker",
                display_name="林雁",
                honorific="林女士",
                pronouns="她",
                occupation="档案员",
                appearance="短发，穿旧防水外套",
                background_hook="来梦中寻找失踪的弟弟",
                keepsake="一枚裂了边的铜怀表",
            )
        )
        return store, created.game_id

    @staticmethod
    def _fast_policy() -> dict[str, Any]:
        return {
            "mode": "normal",
            "max_retries": 2,
            "backoff": {
                "initial_delay_ms": 1,
                "max_delay_ms": 1,
                "jitter_ratio": 0,
            },
        }

    def test_each_request_chain_gets_its_own_retry_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, game_id = self._create_session(root)
            sleeps: list[float] = []
            model = ScriptedGameMasterModel(
                [
                    ModelCallError("request_timeout", "stop", retryable=True),
                    ModelCallError("provider_network_error", "stop", retryable=True),
                    tool_response(),
                    ModelCallError("provider_server_error", "stop", retryable=True),
                    final_response(),
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                model_profile=deepseek_model_profile(
                    retry_policy=self._fast_policy()
                ),
                retry_sleep=sleeps.append,
                retry_random=lambda: 0.5,
                random_source=FixedRandom(),
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我先检查牢门。")

            self.assertEqual(result.status, "committed")
            self.assertEqual(len(model.requests), 5)
            self.assertEqual(sleeps, [0.001, 0.001, 0.001])

    def test_retry_exhaustion_preserves_last_provider_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, game_id = self._create_session(root)
            sleeps: list[float] = []
            model = ScriptedGameMasterModel(
                [
                    ModelCallError("request_timeout", "stop", retryable=True),
                    ModelCallError("request_timeout", "stop", retryable=True),
                    ModelCallError("request_timeout", "stop", retryable=True),
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                model_profile=deepseek_model_profile(
                    retry_policy=self._fast_policy()
                ),
                retry_sleep=sleeps.append,
                retry_random=lambda: 0.5,
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查门锁。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "request_timeout")
            self.assertEqual(len(model.requests), 3)
            self.assertEqual(sleeps, [0.001, 0.001])
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(
                incomplete["provider_retry"],
                {
                    "retries_used": 2,
                    "total_retries": 2,
                    "last_retry": {
                        "code": "request_timeout",
                        "message": "stop",
                        "delay_ms": 1,
                        "scheduled_at": "2026-07-27T00:00:00Z",
                        "status": None,
                        "request_id": None,
                    },
                },
            )

    def test_default_policy_does_not_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, game_id = self._create_session(root)
            sleeps: list[float] = []
            model = ScriptedGameMasterModel(
                [ModelCallError("request_timeout", "stop", retryable=True)]
            )
            harness = AgenticHarness(
                store,
                model,
                model_profile=deepseek_model_profile(),
                retry_sleep=sleeps.append,
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查门锁。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "request_timeout")
            self.assertEqual(len(model.requests), 1)
            self.assertEqual(sleeps, [])

    def test_non_retryable_error_stops_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, game_id = self._create_session(root)
            sleeps: list[float] = []
            model = ScriptedGameMasterModel(
                [ModelCallError("provider_authentication_failed", "stop", retryable=False)]
            )
            harness = AgenticHarness(
                store,
                model,
                model_profile=deepseek_model_profile(
                    retry_policy=self._fast_policy()
                ),
                retry_sleep=sleeps.append,
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查门锁。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "provider_authentication_failed")
            self.assertEqual(len(model.requests), 1)
            self.assertEqual(sleeps, [])

    def test_retry_is_persisted_before_sleep(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, game_id = self._create_session(root)
            writes: list[Mapping[str, Any]] = []

            def recording_writer(path: Path, payload: Any) -> None:
                writes.append(copy.deepcopy(payload))
                write_json_atomic(path, payload)

            sleeps: list[float] = []
            model = ScriptedGameMasterModel(
                [
                    ModelCallError("request_timeout", "stop", retryable=True),
                    final_response(),
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                model_profile=deepseek_model_profile(
                    retry_policy=self._fast_policy()
                ),
                session_writer=recording_writer,
                retry_sleep=sleeps.append,
                retry_random=lambda: 0.5,
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查门锁。")

            self.assertEqual(result.status, "committed")
            self.assertEqual(len(writes), 3)
            scheduled = writes[1]["incomplete_turn"]["provider_retry"]
            self.assertEqual(scheduled["retries_used"], 1)
            self.assertEqual(scheduled["total_retries"], 1)
            self.assertEqual(scheduled["last_retry"]["code"], "request_timeout")
            self.assertEqual(scheduled["last_retry"]["delay_ms"], 1)
            self.assertEqual(sleeps, [0.001])

    def test_retry_state_persistence_failure_is_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, game_id = self._create_session(root)
            calls = 0

            def failing_writer(path: Path, payload: Any) -> None:
                nonlocal calls
                calls += 1
                if calls == 1:
                    write_json_atomic(path, payload)
                    return
                raise OSError("retry state persistence failed")

            sleeps: list[float] = []
            model = ScriptedGameMasterModel(
                [ModelCallError("request_timeout", "stop", retryable=True)]
            )
            harness = AgenticHarness(
                store,
                model,
                model_profile=deepseek_model_profile(
                    retry_policy=self._fast_policy()
                ),
                session_writer=failing_writer,
                retry_sleep=sleeps.append,
                retry_random=lambda: 0.5,
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查门锁。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "retry_state_persistence_failed")
            self.assertEqual(len(model.requests), 1)
            self.assertEqual(sleeps, [])
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(incomplete["last_failure"]["code"], "retry_state_persistence_failed")

    def test_deadline_prevents_sleep_that_would_exceed_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, game_id = self._create_session(root)
            sleeps: list[float] = []
            model = ScriptedGameMasterModel(
                [ModelCallError("request_timeout", "stop", retryable=True)]
            )
            harness = AgenticHarness(
                store,
                model,
                model_profile=deepseek_model_profile(
                    retry_policy={
                        "mode": "normal",
                        "max_retries": 2,
                        "backoff": {
                            "initial_delay_ms": 1_000,
                            "max_delay_ms": 1_000,
                            "jitter_ratio": 0,
                        },
                    }
                ),
                attempt_limits={
                    "max_round_trips": 8,
                    "request_timeout_seconds": 60,
                    "attempt_timeout_seconds": 1,
                    "max_structure_repairs": 1,
                },
                retry_sleep=sleeps.append,
                retry_random=lambda: 0.5,
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查门锁。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "attempt_timeout")
            self.assertEqual(len(model.requests), 1)
            self.assertEqual(sleeps, [])

    def test_legacy_incomplete_turn_without_provider_retry_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, game_id = self._create_session(root)
            interrupted = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [ModelCallError("request_timeout", "stop", retryable=True)]
                ),
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            ).start_turn(game_id, "我检查门锁。")
            self.assertEqual(interrupted.status, "interrupted")

            session_file = store.load_session(game_id).session_directory / "session.json"
            legacy = read_json(session_file)
            del legacy["incomplete_turn"]["provider_retry"]
            write_json_atomic(session_file, legacy)

            resumed = AgenticHarness(
                store,
                ScriptedGameMasterModel([final_response()]),
                model_profile=deepseek_model_profile(),
                clock=lambda: datetime(2026, 7, 27, 0, 1, tzinfo=timezone.utc),
            ).resume_turn(game_id, interrupted.turn_id)

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(
                store.load_session(game_id).session["incomplete_turn"],
                None,
            )


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
