import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

from monmusu_agent.agent import GameMasterAgent
from monmusu_agent.agentic_cli import (
    CliInputEncodingError,
    CliPlayerInterrupt,
    main,
    run_agentic_cli,
    run_game_cli,
    run_new_session_cli,
    run_turn_cli,
)
from monmusu_agent.agentic_harness import AgenticHarness, PublicMechanic, TurnResult
from monmusu_agent.agentic_model import (
    GameMasterModel,
    ModelCallError,
    ModelRequest,
    ModelResponse,
    ScriptedGameMasterModel,
    deepseek_model_profile,
)
from monmusu_agent.agentic_session import (
    AgenticSessionStore,
    NewSessionRequest,
)
from monmusu_agent.storage import write_json_atomic


class ForbiddenModelLoop:
    """记录任何不该发生的旧 GM 模型循环启动。"""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        raise AssertionError("会话初始化不得调用 GM 模型")


class FixedRandom:
    def __init__(self) -> None:
        self.values = iter((3, 4, 5, 6))

    def randint(self, minimum: int, maximum: int) -> int:
        if (minimum, maximum) != (0, 9):
            raise AssertionError("make_check 应只读取 d10")
        return next(self.values)


class PersistedMechanicThenFinalModel(GameMasterModel):
    """在第二次请求时核对公开机械已经写盘并送达 CLI。"""

    def __init__(
        self,
        store: AgenticSessionStore,
        game_id: str,
        output: list[str],
    ) -> None:
        self.store = store
        self.game_id = game_id
        self.output = output
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                assistant_message={
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": None,
                    "tool_calls": [
                        {
                            "id": "call_001",
                            "type": "function",
                            "function": {
                                "name": "make_check",
                                "arguments": json.dumps(
                                    {
                                        "actor_id": "investigator_tracker",
                                        "ability": "spot_hidden",
                                        "difficulty": "regular",
                                        "dice_adjustment": {
                                            "kind": "none",
                                            "count": 0,
                                        },
                                        "action": "检查牢门铰链",
                                        "stakes": "失败会错过新鲜刮痕",
                                        "visibility": "public",
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ],
                },
                finish_reason="tool_calls",
                usage=None,
                latency_ms=10,
            )
        incomplete = self.store.load_session(self.game_id).session["incomplete_turn"]
        assert incomplete is not None
        if [item["mechanic_id"] for item in incomplete["mechanics"]] != [
            "mechanic_0001"
        ]:
            raise AssertionError("第二次 GM 请求前机械尚未持久化")
        if self.output != [
            "公开机械 | 类型：check | 角色：investigator_tracker | 详情："
            '{"ability": "spot_hidden", "ability_value": 70, '
            '"action": "检查牢门铰链", '
            '"dice_adjustment": {"count": 0, "kind": "none"}, '
            '"difficulty": "regular", "roll": 43, '
            '"stakes": "失败会错过新鲜刮痕", '
            '"success_level": "regular_success", "target": 70}'
        ]:
            raise AssertionError("第二次 GM 请求前 CLI 尚未收到公开机械")
        return ModelResponse(
            assistant_message={
                "role": "assistant",
                "content": json.dumps(
                    {
                        "narration": "你发现了新鲜的工具刮痕。",
                        "establish": [],
                        "retire": [],
                        "session_status": "ongoing",
                    },
                    ensure_ascii=False,
                ),
                "reasoning_content": None,
                "tool_calls": [],
            },
            finish_reason="stop",
            usage=None,
            latency_ms=10,
        )


def zero_retry_profile(**kwargs: Any) -> dict[str, Any]:
    """构造默认关闭重试的测试 profile，保留终端错误语义。"""

    kwargs.setdefault(
        "retry_policy",
        {"mode": "normal", "max_retries": 0},
    )
    return deepseek_model_profile(**kwargs)


class AgenticCliTest(unittest.TestCase):
    def test_turn_cli_preserves_all_tool_owned_public_details(self) -> None:
        """同 kind 的不同工具仍完整显示各自选择的公开字段。"""

        class PushProjectionHarness:
            def start_turn(
                self,
                game_id: str,
                player_input: str,
                *,
                public_mechanic_sink: object,
            ) -> TurnResult:
                del game_id, player_input
                assert callable(public_mechanic_sink)
                public_mechanic_sink(
                    PublicMechanic(
                        mechanic_id="mechanic_push_0001",
                        kind="check",
                        actor_id="investigator_tracker",
                        details={
                            "ability": "locksmith",
                            "ability_value": 55,
                            "difficulty": "regular",
                            "target": 55,
                            "dice_adjustment": {"kind": "none", "count": 0},
                            "roll": 42,
                            "success_level": "regular_success",
                            "action": "从铰链侧卸门",
                            "stakes": "门轴会断裂并夹伤手掌",
                            "pushed_from": "mechanic_check_0001",
                            "is_pushed": True,
                        },
                    )
                )
                return TurnResult(
                    status="committed",
                    turn_id="turn_0001",
                    narration="门轴应声脱落。",
                    public_mechanics=(),
                    public_fact_changes=(),
                    error_code=None,
                    error_message=None,
                )

        output: list[str] = []

        run_turn_cli(
            PushProjectionHarness(),  # type: ignore[arg-type]
            "game_0001",
            "我从铰链侧卸门。",
            write_line=output.append,
        )

        self.assertIn('"pushed_from": "mechanic_check_0001"', output[0])
        self.assertIn('"is_pushed": true', output[0])
        self.assertEqual(output[1], "门轴应声脱落。")

    def test_startup_discovers_incomplete_session_and_exit_preserves_it(
        self,
    ) -> None:
        """启动只展示安全恢复入口，退出不调用模型或改变存档。"""

        with tempfile.TemporaryDirectory() as directory:
            game_ids = iter(
                ("game_ready", "game_interrupted_a", "game_interrupted_b")
            )
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=game_ids.__next__,
                clock=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
            )
            ready = store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_tracker",
                    display_name="林雁",
                )
            )
            interrupted_a = store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_tracker",
                    display_name="林雁",
                )
            )
            interrupted_b = store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_tracker",
                    display_name="林雁",
                )
            )
            for created, turn_id in (
                (interrupted_a, "turn_interrupted_a"),
                (interrupted_b, "turn_interrupted_b"),
            ):
                AgenticHarness(
                    store,
                    ScriptedGameMasterModel(
                        [
                            ModelCallError(
                                "request_timeout",
                                "private",
                                retryable=True,
                            )
                        ]
                    ),
                    turn_id_factory=lambda turn_id=turn_id: turn_id,
                    model_profile=zero_retry_profile(),
                    clock=lambda: datetime(
                        2026,
                        8,
                        7,
                        0,
                        1,
                        tzinfo=timezone.utc,
                    ),
                ).start_turn(created.game_id, "我检查门锁。")
            ready_bytes = ready.session_file.read_bytes()
            interrupted_a_bytes = interrupted_a.session_file.read_bytes()
            interrupted_b_bytes = interrupted_b.session_file.read_bytes()
            recovery_model = ScriptedGameMasterModel([])
            harness = AgenticHarness(store, recovery_model)
            answers = iter(("2", "退出"))
            prompts: list[str] = []
            output: list[str] = []

            def read_line(prompt: str) -> str:
                prompts.append(prompt)
                return next(answers)

            result = run_agentic_cli(
                harness,
                store,
                read_line=read_line,
                write_line=output.append,
            )

            self.assertIsNone(result)
            self.assertEqual(recovery_model.requests, [])
            self.assertEqual(ready.session_file.read_bytes(), ready_bytes)
            self.assertEqual(
                interrupted_a.session_file.read_bytes(),
                interrupted_a_bytes,
            )
            self.assertEqual(
                interrupted_b.session_file.read_bytes(),
                interrupted_b_bytes,
            )
            self.assertEqual(
                prompts,
                [
                    "请选择要处理的未完成会话编号：",
                    "输入“恢复”继续原回合，或输入“退出”结束：",
                ],
            )
            self.assertEqual(
                output,
                [
                    "检测到未完成回合：",
                    "1. 会话 game_interrupted_a",
                    "2. 会话 game_interrupted_b",
                    "未完成回合：turn_interrupted_b",
                    "技术中断（request_timeout）：回合因技术问题中断，需要显式恢复",
                    "已退出；未完成回合已保留。",
                ],
            )
            self.assertNotIn("game_ready", "\n".join(output))

    def test_recovery_repeats_gate_then_accepts_next_action_after_commit(
        self,
    ) -> None:
        """再次中断仍门控，原回合提交后才接受下一条行动。"""

        reasoning_canary = "THINKING_CLI_RECOVERY_CANARY_10"
        profile = zero_retry_profile(thinking=True)
        tool_response = ModelResponse(
            assistant_message={
                "role": "assistant",
                "content": None,
                "reasoning_content": reasoning_canary,
                "tool_calls": [
                    {
                        "id": "call_recovery_check",
                        "type": "function",
                        "function": {
                            "name": "make_check",
                            "arguments": json.dumps(
                                {
                                    "actor_id": "investigator_tracker",
                                    "ability": "spot_hidden",
                                    "difficulty": "regular",
                                    "dice_adjustment": {
                                        "kind": "none",
                                        "count": 0,
                                    },
                                    "action": "检查门锁上的刮痕",
                                    "stakes": "失败会错过开锁痕迹",
                                    "visibility": "public",
                                },
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            },
            finish_reason="tool_calls",
            usage=None,
            latency_ms=10,
        )
        recovered_final = ModelResponse(
            assistant_message={
                "role": "assistant",
                "content": json.dumps(
                    {
                        "narration": "你确认门锁刚被人从外侧打开过。",
                        "establish": [],
                        "retire": [],
                        "session_status": "ongoing",
                    },
                    ensure_ascii=False,
                ),
                "reasoning_content": "THINKING_CLI_FINAL_CANARY_10",
                "tool_calls": [],
            },
            finish_reason="stop",
            usage=None,
            latency_ms=10,
        )
        next_turn_final = ModelResponse(
            assistant_message={
                "role": "assistant",
                "content": json.dumps(
                    {
                        "narration": "你推门离开石牢。",
                        "establish": [],
                        "retire": [],
                        "session_status": "complete",
                    },
                    ensure_ascii=False,
                ),
                "reasoning_content": "THINKING_CLI_NEXT_CANARY_10",
                "tool_calls": [],
            },
            finish_reason="stop",
            usage=None,
            latency_ms=10,
        )

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_recovery_loop",
                clock=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
            )
            created = store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_tracker",
                    display_name="林雁",
                )
            )
            AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [ModelCallError("request_timeout", "private", retryable=True)]
                ),
                turn_id_factory=lambda: "turn_interrupted",
                model_profile=profile,
                clock=lambda: datetime(2026, 8, 7, 0, 1, tzinfo=timezone.utc),
            ).start_turn(created.game_id, "我检查门锁。")
            recovery_model = ScriptedGameMasterModel(
                [
                    tool_response,
                    ModelCallError("request_timeout", "private", retryable=True),
                    recovered_final,
                    next_turn_final,
                ]
            )
            harness = AgenticHarness(
                store,
                recovery_model,
                turn_id_factory=lambda: "turn_next",
                mechanic_id_factory=lambda: "mechanic_recovery",
                random_source=FixedRandom(),
                model_profile=profile,
                clock=lambda: datetime(2026, 8, 7, 0, 2, tzinfo=timezone.utc),
            )
            answers = iter(
                ("1", "我先踢门。", "恢复", "恢复", "我推门离开。")
            )
            prompts: list[str] = []
            output: list[str] = []

            def read_line(prompt: str) -> str:
                value = next(answers)
                if value == "我先踢门。":
                    self.assertEqual(recovery_model.requests, [])
                prompts.append(prompt)
                return value

            result = run_agentic_cli(
                harness,
                store,
                read_line=read_line,
                write_line=output.append,
            )
            saved = store.load_session(created.game_id).session

        assert result is not None
        self.assertEqual(result.status, "committed")
        self.assertEqual(result.turn_id, "turn_next")
        self.assertEqual(len(recovery_model.requests), 4)
        self.assertEqual(
            [turn["turn_id"] for turn in saved["turns"]],
            ["turn_interrupted", "turn_next"],
        )
        self.assertEqual(
            [turn["player_input"] for turn in saved["turns"]],
            ["我检查门锁。", "我推门离开。"],
        )
        self.assertIsNone(saved["incomplete_turn"])
        self.assertEqual(saved["session_status"], "complete")
        self.assertEqual(
            prompts,
            [
                "请选择要处理的未完成会话编号：",
                "输入“恢复”继续原回合，或输入“退出”结束：",
                "输入“恢复”继续原回合，或输入“退出”结束：",
                "输入“恢复”继续原回合，或输入“退出”结束：",
                "你的行动：",
            ],
        )
        rendered = "\n".join(output)
        self.assertIn("只能输入“恢复”或“退出”。", output)
        self.assertEqual(rendered.count('"action": "检查门锁上的刮痕"'), 1)
        self.assertEqual(rendered.count("你确认门锁刚被人从外侧打开过。"), 1)
        self.assertEqual(rendered.count("你推门离开石牢。"), 1)
        self.assertNotIn(reasoning_canary, rendered)
        self.assertNotIn(
            "THINKING_CLI_",
            json.dumps(saved, ensure_ascii=False, sort_keys=True),
        )

    def test_recovery_gate_rejects_new_action_and_preserves_state_on_exit_input(
        self,
    ) -> None:
        """无效选择、EOF 与键盘中断都不能越过未完成回合门。"""

        cases: tuple[tuple[str, tuple[object, ...]], ...] = (
            ("action-like", ("1", "我踢开牢门。", "退出")),
            ("malformed-selection", ("not-a-number", "1", "退出")),
            ("eof", ("1", EOFError())),
            ("keyboard-interrupt", ("1", KeyboardInterrupt())),
        )
        for label, scripted_inputs in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                store = AgenticSessionStore(
                    session_root=Path(directory) / "sessions",
                    game_id_factory=lambda: "game_preserved",
                    clock=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
                )
                created = store.create_session(
                    NewSessionRequest(
                        investigator_id="investigator_tracker",
                        display_name="林雁",
                    )
                )
                AgenticHarness(
                    store,
                    ScriptedGameMasterModel(
                        [
                            ModelCallError(
                                "request_timeout",
                                "private",
                                retryable=True,
                            )
                        ]
                    ),
                    turn_id_factory=lambda: "turn_preserved",
                    clock=lambda: datetime(
                        2026,
                        8,
                        7,
                        0,
                        1,
                        tzinfo=timezone.utc,
                    ),
                ).start_turn(created.game_id, "我检查门锁。")
                before = created.session_file.read_bytes()
                recovery_model = ScriptedGameMasterModel([])
                inputs = iter(scripted_inputs)
                output: list[str] = []

                def read_line(prompt: str, _inputs=inputs) -> str:
                    value = next(_inputs)
                    if isinstance(value, BaseException):
                        raise value
                    assert isinstance(value, str)
                    return value

                result = run_agentic_cli(
                    AgenticHarness(store, recovery_model),
                    store,
                    read_line=read_line,
                    write_line=output.append,
                )

                self.assertIsNone(result)
                self.assertEqual(recovery_model.requests, [])
                self.assertEqual(created.session_file.read_bytes(), before)
                self.assertEqual(
                    store.find_incomplete_session_ids(),
                    ("game_preserved",),
                )
                self.assertNotIn("你的行动：", "\n".join(output))
                if label == "action-like":
                    self.assertIn("只能输入“恢复”或“退出”。", output)
                if label == "malformed-selection":
                    self.assertIn("请输入有效的未完成会话编号。", output)

    def test_unavailable_frozen_profile_is_a_safe_recovery_interruption(
        self,
    ) -> None:
        """冻结 profile 不可用时不泄露异常，也不调用模型或改档。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_profile_mismatch",
                clock=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
            )
            created = store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_tracker",
                    display_name="林雁",
                )
            )
            AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [ModelCallError("request_timeout", "private", retryable=True)]
                ),
                turn_id_factory=lambda: "turn_profile_mismatch",
                clock=lambda: datetime(2026, 8, 7, 0, 1, tzinfo=timezone.utc),
            ).start_turn(created.game_id, "我检查门锁。")
            interrupted = json.loads(created.session_file.read_text(encoding="utf-8"))
            interrupted["incomplete_turn"]["model_profile"]["enabled_tools"] = [
                "removed_coc_tool"
            ]
            write_json_atomic(created.session_file, interrupted)
            before = created.session_file.read_bytes()
            recovery_model = ScriptedGameMasterModel([])
            harness = AgenticHarness(
                store,
                recovery_model,
            )
            answers = iter(("1", "恢复", "退出"))
            output: list[str] = []

            result = run_agentic_cli(
                harness,
                store,
                read_line=lambda prompt: next(answers),
                write_line=output.append,
            )

            self.assertIsNone(result)
            self.assertEqual(recovery_model.requests, [])
            self.assertEqual(created.session_file.read_bytes(), before)

        rendered = "\n".join(output)
        self.assertIn(
            "技术中断（recovery_unavailable）：当前运行配置无法恢复该回合；未完成回合已保留",
            rendered,
        )
        self.assertNotIn("removed_coc_tool", rendered)
        self.assertNotIn("Traceback", rendered)

    def test_recovery_startup_filters_all_restricted_incomplete_turn_content(
        self,
    ) -> None:
        """恢复页只投影公开机械与稳定技术状态。"""

        hidden_fact = "门后的守卫已经认出调查员"
        hidden_mechanic = "守卫暗中判断是否听见墙缝声"
        invalid_final = "这段未通过校验的叙事不能显示"
        reasoning = "private reasoning content"
        provider_detail = "sk-private-fragment Authorization: Bearer secret"

        def final_response(payload: object) -> ModelResponse:
            return ModelResponse(
                assistant_message={
                    "role": "assistant",
                    "content": json.dumps(payload, ensure_ascii=False),
                    "reasoning_content": reasoning,
                    "tool_calls": [],
                },
                finish_reason="stop",
                usage=None,
                latency_ms=10,
            )

        def check_response(
            call_id: str,
            action: str,
            visibility: str,
        ) -> ModelResponse:
            return ModelResponse(
                assistant_message={
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": reasoning,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": "make_check",
                                "arguments": json.dumps(
                                    {
                                        "actor_id": "investigator_tracker",
                                        "ability": "spot_hidden",
                                        "difficulty": "regular",
                                        "dice_adjustment": {
                                            "kind": "none",
                                            "count": 0,
                                        },
                                        "action": action,
                                        "stakes": "失败会错过痕迹",
                                        "visibility": visibility,
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ],
                },
                finish_reason="tool_calls",
                usage=None,
                latency_ms=10,
            )

        with tempfile.TemporaryDirectory() as directory:
            profile = deepseek_model_profile(thinking=True)
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_private_projection",
                clock=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
            )
            created = store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_tracker",
                    display_name="林雁",
                )
            )
            model = ScriptedGameMasterModel(
                [
                    final_response(
                        {
                            "narration": "你听见门外的脚步停了一瞬。",
                            "establish": [
                                {"visibility": "hidden", "text": hidden_fact}
                            ],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                    check_response(
                        "call_public",
                        "检查墙缝上的新鲜刮痕",
                        "public",
                    ),
                    check_response("call_hidden", hidden_mechanic, "hidden"),
                    ModelResponse(
                        assistant_message={
                            "role": "assistant",
                            "content": json.dumps(
                                {"narration": invalid_final},
                                ensure_ascii=False,
                            ),
                            "reasoning_content": reasoning,
                            "tool_calls": [],
                        },
                        finish_reason="stop",
                        usage=None,
                        latency_ms=10,
                    ),
                    ModelCallError(
                        "request_timeout",
                        provider_detail,
                        retryable=True,
                    ),
                ]
            )
            turn_ids = iter(("turn_hidden_fact", "turn_interrupted"))
            mechanic_ids = iter(("mechanic_public", "mechanic_hidden"))
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=turn_ids.__next__,
                fact_id_factory=lambda: "fact_hidden",
                mechanic_id_factory=mechanic_ids.__next__,
                random_source=FixedRandom(),
                model_profile=profile,
                clock=lambda: datetime(2026, 8, 7, 0, 1, tzinfo=timezone.utc),
            )
            harness.start_turn(created.game_id, "我侧耳听门外。")
            interrupted = harness.start_turn(created.game_id, "我检查墙缝。")
            self.assertEqual(interrupted.status, "interrupted")
            recovery_model = ScriptedGameMasterModel([])
            output: list[str] = []
            answers = iter(("1", "退出"))

            run_agentic_cli(
                AgenticHarness(store, recovery_model, model_profile=profile),
                store,
                read_line=lambda prompt: next(answers),
                write_line=output.append,
            )

        rendered = "\n".join(output)
        self.assertEqual(recovery_model.requests, [])
        self.assertEqual(
            rendered.count('"action": "检查墙缝上的新鲜刮痕"'),
            1,
        )
        for restricted in (
            hidden_fact,
            hidden_mechanic,
            invalid_final,
            reasoning,
            provider_detail,
            "sk-private-fragment",
            "Authorization",
        ):
            self.assertNotIn(restricted, rendered)

    def test_agentic_cli_starts_new_game_when_no_incomplete_session_exists(
        self,
    ) -> None:
        """没有恢复 blocker 时保留现有 opt-in 新游戏流程。"""

        response = ModelResponse(
            assistant_message={
                "role": "assistant",
                "content": json.dumps(
                    {
                        "narration": "你推开潮湿的牢门，短篇在此收束。",
                        "establish": [],
                        "retire": [],
                        "session_status": "complete",
                    },
                    ensure_ascii=False,
                ),
                "reasoning_content": None,
                "tool_calls": [],
            },
            finish_reason="stop",
            usage=None,
            latency_ms=10,
        )
        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_new_flow",
                clock=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
            )
            model = ScriptedGameMasterModel([response])
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_new_flow",
                clock=lambda: datetime(2026, 8, 7, 0, 1, tzinfo=timezone.utc),
            )
            answers = iter(("1", "", "", "", "", "", "", "", "我推门。"))
            output: list[str] = []

            result = run_agentic_cli(
                harness,
                store,
                read_line=lambda prompt: next(answers),
                write_line=output.append,
            )
            saved = store.load_session("game_new_flow").session

        assert result is not None
        self.assertEqual(result.status, "committed")
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(saved["turns"][0]["player_input"], "我推门。")
        self.assertEqual(saved["session_status"], "complete")
        self.assertLess(
            output.index(saved["setup"]["opening_narration"]),
            output.index("你推开潮湿的牢门，短篇在此收束。"),
        )

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux IUTF8")
    def test_cli_configures_utf8_backspace_for_terminal_input(self) -> None:
        """真实终端应把一次退格视为删除一个完整的 UTF-8 字符。"""

        import pty
        import select
        import termios

        master_fd, slave_fd = pty.openpty()
        process: subprocess.Popen[bytes] | None = None
        try:
            attributes = termios.tcgetattr(slave_fd)
            attributes[0] &= ~0x4000  # Linux termios 的 IUTF8 标志。
            termios.tcsetattr(slave_fd, termios.TCSANOW, attributes)
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "\n".join(
                        (
                            "from datetime import UTC, datetime",
                            "from pathlib import Path",
                            "from tempfile import TemporaryDirectory",
                            "import json",
                            "import termios",
                            "from monmusu_agent.agentic_cli import (",
                            "    _configure_terminal_input,",
                            "    run_new_session_cli,",
                            ")",
                            "from monmusu_agent.agentic_session import AgenticSessionStore",
                            "IUTF8 = 0x4000",
                            "restore = _configure_terminal_input()",
                            "print('READY', flush=True)",
                            "try:",
                            "    with TemporaryDirectory() as directory:",
                            "        store = AgenticSessionStore(",
                            "            session_root=Path(directory) / 'sessions',",
                            "            game_id_factory=lambda: 'game_utf8_backspace',",
                            "            clock=lambda: datetime(2026, 8, 17, tzinfo=UTC),",
                            "        )",
                            "        result = run_new_session_cli(",
                            "            store,",
                            "            write_line=lambda line: None,",
                            "        )",
                            "        profile = result.created.session['investigator_profile']",
                            "        during = bool(termios.tcgetattr(0)[0] & IUTF8)",
                            "finally:",
                            "    if restore is not None:",
                            "        restore()",
                            "restored = bool(termios.tcgetattr(0)[0] & IUTF8)",
                            "print('RESULT=' + json.dumps({",
                            "    'display_name': profile['display_name'],",
                            "    'honorific': profile['honorific'],",
                            "    'first_action': result.first_action,",
                            "    'iutf8_during': during,",
                            "    'iutf8_after_restore': restored,",
                            "}, ensure_ascii=True), flush=True)",
                        )
                    ),
                ],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=environment,
                close_fds=True,
            )
            os.close(slave_fd)
            slave_fd = -1

            output = bytearray()
            deadline = time.monotonic() + 5
            while b"READY" not in output:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.fail("PTY 子进程未在五秒内完成 CLI 输入配置")
                readable, _, _ = select.select([master_fd], [], [], remaining)
                if readable:
                    output.extend(os.read(master_fd, 4096))

            os.write(
                master_fd,
                b"1\nOT-527-nu\n"
                + "它".encode("utf-8")
                + b"\x7f\n\n\n\n\n\n\n"
                + "我观察石牢里其他人的情况\n".encode("utf-8"),
            )
            process.wait(timeout=5)

            while True:
                readable, _, _ = select.select([master_fd], [], [], 0)
                if not readable:
                    break
                try:
                    output.extend(os.read(master_fd, 4096))
                except OSError:
                    break

            self.assertEqual(
                process.returncode,
                0,
                output.decode("utf-8", errors="backslashreplace"),
            )
            result_line = output.split(b"RESULT=", maxsplit=1)[1].splitlines()[0]
            self.assertEqual(
                json.loads(result_line.decode("ascii")),
                {
                    "display_name": "OT-527-nu",
                    "honorific": None,
                    "first_action": "我观察石牢里其他人的情况",
                    "iutf8_during": True,
                    "iutf8_after_restore": False,
                },
            )
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            if slave_fd != -1:
                os.close(slave_fd)
            os.close(master_fd)

    def test_cli_repairs_utf8_bytes_preserved_by_surrogateescape(self) -> None:
        """终端以兼容编码解码时，可无损还原原始 UTF-8 输入。"""

        def surrogateescaped_utf8(value: str) -> str:
            return value.encode("utf-8").decode("ascii", "surrogateescape")

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_encoding_0001",
                clock=lambda: datetime(2026, 7, 31, tzinfo=UTC),
            )
            answers = iter(
                [
                    "1",
                    surrogateescaped_utf8("林雁"),
                    surrogateescaped_utf8("林先生"),
                    surrogateescaped_utf8("他"),
                    surrogateescaped_utf8("调查记者"),
                    surrogateescaped_utf8("戴着眼镜的高瘦青年"),
                    surrogateescaped_utf8("寻找失踪的搭档"),
                    surrogateescaped_utf8("一本笔记本"),
                    surrogateescaped_utf8("我观察石牢里其他人的情况"),
                ]
            )

            result = run_new_session_cli(
                store,
                read_line=lambda prompt: next(answers),
                write_line=lambda line: None,
            )

        self.assertEqual(result.first_action, "我观察石牢里其他人的情况")
        self.assertEqual(
            result.created.session["investigator_profile"]["display_name"],
            "林雁",
        )

    def test_cli_rejects_bytes_that_cannot_be_recovered_as_utf8(self) -> None:
        """无法确认编码时，CLI 报稳定错误而不是在 JSON 写入阶段崩溃。"""

        invalid_utf8 = b"\xe7".decode("utf-8", "surrogateescape")
        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_encoding_0002",
                clock=lambda: datetime(2026, 7, 31, tzinfo=UTC),
            )
            answers = iter(
                [
                    "1",
                    invalid_utf8,
                ]
            )

            with self.assertRaisesRegex(CliInputEncodingError, "UTF-8"):
                run_new_session_cli(
                    store,
                    read_line=lambda prompt: next(answers),
                    write_line=lambda line: None,
                )

    def test_cli_publishes_opening_before_accepting_first_action(self) -> None:
        """CLI 先完成冻结会话并展示开场，再接收玩家自由文本。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = AgenticSessionStore(
                session_root=root / "agentic_sessions",
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            answers = iter(
                [
                    "1",
                    "许舟",
                    "许先生",
                    "他",
                    "调查记者",
                    "总把袖口卷到手肘",
                    "寻找失踪在梦中的搭档",
                    "一支没有墨水的钢笔",
                    "我先贴近牢门，听门外还有没有脚步声。",
                ]
            )
            output: list[str] = []

            def read_line(prompt: str) -> str:
                if prompt == "你的行动：":
                    loaded = store.load_session("game_test_0001")
                    self.assertEqual(
                        output[-1],
                        loaded.session["setup"]["opening_narration"],
                    )
                    self.assertEqual(loaded.session["turns"], [])
                    self.assertIsNone(loaded.session["incomplete_turn"])
                return next(answers)

            model_loop = ForbiddenModelLoop()
            with patch.object(GameMasterAgent, "run", model_loop):
                result = run_new_session_cli(
                    store,
                    read_line=read_line,
                    write_line=output.append,
                )

            self.assertEqual(result.created.game_id, "game_test_0001")
            self.assertEqual(model_loop.calls, 0)
            self.assertEqual(
                result.first_action,
                "我先贴近牢门，听门外还有没有脚步声。",
            )
            self.assertEqual(
                result.created.session["investigator_profile"],
                {
                    "actor_id": "investigator_tracker",
                    "display_name": "许舟",
                    "honorific": "许先生",
                    "pronouns": "他",
                    "occupation": "调查记者",
                    "appearance": "总把袖口卷到手肘",
                    "background_hook": "寻找失踪在梦中的搭档",
                    "keepsake": "一支没有墨水的钢笔",
                },
            )
            self.assertFalse((root / "game_state.json").exists())
            self.assertFalse((root / "memory.json").exists())

    def test_main_composes_deepseek_and_runs_game_without_private_output(
        self,
    ) -> None:
        """新版入口只在组合边界读取 key，并把首条行动交给同一 Harness。"""

        session_file = Path("/private/session/session.json")
        output = io.StringIO()

        store = object()
        harness = object()
        with (
            patch.dict(
                os.environ,
                {
                    "MONMUSU_PROVIDER": "deepseek",
                    "DEEPSEEK_API_KEY": "sk-cli-secret-fragment",
                    "MONMUSU_DEEPSEEK_MODEL_ID": "deepseek-v4-flash",
                    "MONMUSU_DEEPSEEK_THINKING": "false",
                },
                clear=True,
            ),
            patch("monmusu_agent.agentic_cli.load_dotenv"),
            patch(
                "monmusu_agent.agentic_cli.AgenticSessionStore",
                return_value=store,
            ),
            patch(
                "monmusu_agent.agentic_cli.compose_deepseek_harness",
                return_value=harness,
            ) as compose,
            patch("monmusu_agent.agentic_cli.run_agentic_cli") as run_cli,
            redirect_stdout(output),
        ):
            self.assertEqual(main([]), 0)

        compose.assert_called_once_with(
            store,
            api_key="sk-cli-secret-fragment",
            model_id="deepseek-v4-flash",
            thinking=False,
            provider="deepseek",
            base_url="https://api.deepseek.com",
        )
        run_cli.assert_called_once_with(harness, store)
        self.assertNotIn(str(session_file), output.getvalue())
        self.assertNotIn("sk-cli-secret-fragment", output.getvalue())

    def test_main_requires_external_key_before_creating_session(self) -> None:
        output = io.StringIO()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("monmusu_agent.agentic_cli.load_dotenv"),
            patch(
                "monmusu_agent.agentic_cli._stdin_is_interactive",
                return_value=False,
            ),
            patch("monmusu_agent.agentic_cli.AgenticSessionStore") as store,
            redirect_stdout(output),
        ):
            self.assertEqual(main([]), 2)

        store.assert_not_called()
        self.assertIn("--configure", output.getvalue())
        self.assertIn("MONMUSU_PROVIDER", output.getvalue())

    def test_main_reports_terminal_encoding_error_without_traceback(self) -> None:
        output = io.StringIO()
        with (
            patch.dict(
                os.environ,
                {
                    "MONMUSU_PROVIDER": "deepseek",
                    "DEEPSEEK_API_KEY": "sk-cli-secret-fragment",
                    "MONMUSU_DEEPSEEK_MODEL_ID": "deepseek-v4-flash",
                    "MONMUSU_DEEPSEEK_THINKING": "false",
                },
                clear=True,
            ),
            patch("monmusu_agent.agentic_cli.load_dotenv"),
            patch("monmusu_agent.agentic_cli.AgenticSessionStore"),
            patch("monmusu_agent.agentic_cli.compose_deepseek_harness"),
            patch(
                "monmusu_agent.agentic_cli.run_agentic_cli",
                side_effect=CliInputEncodingError("终端输入不是有效的 UTF-8"),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(main([]), 2)

        self.assertEqual(
            output.getvalue(),
            "模型提供商：DeepSeek 官方\n输入错误：终端输入不是有效的 UTF-8\n",
        )

    def test_game_cli_accepts_consecutive_actions_until_session_complete(
        self,
    ) -> None:
        responses = [
            ModelResponse(
                assistant_message={
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "narration": "你在墙缝后摸到一条排水道。",
                            "establish": [
                                {
                                    "visibility": "public",
                                    "text": "墙缝后有一条可通行的排水道。",
                                }
                            ],
                            "retire": [],
                            "session_status": "ongoing",
                        },
                        ensure_ascii=False,
                    ),
                    "reasoning_content": None,
                    "tool_calls": [],
                },
                finish_reason="stop",
                usage=None,
                latency_ms=10,
            ),
            ModelResponse(
                assistant_message={
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "narration": "你沿排水道离开石牢。",
                            "establish": [],
                            "retire": [],
                            "session_status": "complete",
                        },
                        ensure_ascii=False,
                    ),
                    "reasoning_content": None,
                    "tool_calls": [],
                },
                finish_reason="stop",
                usage=None,
                latency_ms=10,
            ),
        ]
        model = ScriptedGameMasterModel(responses)

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_two_turns",
                clock=lambda: datetime(2026, 7, 29, tzinfo=timezone.utc),
            )
            created = store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_tracker",
                    display_name="林雁",
                )
            )
            turn_ids = iter(("turn_0001", "turn_0002"))
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=turn_ids.__next__,
                fact_id_factory=lambda: "fact_drainage",
                clock=lambda: datetime(2026, 7, 29, 0, 1, tzinfo=timezone.utc),
            )
            prompts: list[str] = []
            output: list[str] = []

            def read_line(prompt: str) -> str:
                prompts.append(prompt)
                return "我立刻沿刚发现的排水道爬出去。"

            result = run_game_cli(
                harness,
                created.game_id,
                "我探查潮水涌入的墙缝。",
                read_line=read_line,
                write_line=output.append,
            )

            saved = store.load_session(created.game_id).session

        self.assertEqual(result.status, "committed")
        self.assertEqual(prompts, ["你的行动："])
        self.assertEqual(len(model.requests), 2)
        second_package = json.loads(model.requests[1].messages[1]["content"])
        self.assertIn(
            "fact_drainage",
            [fact["fact_id"] for fact in second_package["ACTIVE_FACTS"]],
        )
        self.assertEqual(
            [turn["player_input"] for turn in saved["turns"]],
            [
                "我探查潮水涌入的墙缝。",
                "我立刻沿刚发现的排水道爬出去。",
            ],
        )
        self.assertEqual(saved["session_status"], "complete")
        self.assertIn("你在墙缝后摸到一条排水道。", output)
        self.assertIn("你沿排水道离开石牢。", output)

    def test_turn_cli_only_prints_committed_public_projection(self) -> None:
        """CLI 不展示隐藏事实、provider 推理或尚未提交的模型外壳。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            created = store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_tracker",
                    display_name="林雁",
                )
            )
            response = ModelResponse(
                assistant_message={
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "narration": "墙后传来潮湿的回声。",
                            "establish": [
                                {
                                    "visibility": "public",
                                    "text": "排水沟通往旧蓄水池。",
                                },
                                {
                                    "visibility": "hidden",
                                    "text": "蓄水池里有人守望。",
                                },
                            ],
                            "retire": [],
                            "session_status": "ongoing",
                        },
                        ensure_ascii=False,
                    ),
                    "reasoning_content": "不能显示的推理",
                    "tool_calls": [],
                },
                finish_reason="stop",
                usage=None,
                latency_ms=12,
            )
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel([response]),
                turn_id_factory=lambda: "turn_0001",
                fact_id_factory=iter(("fact_1001", "fact_1002")).__next__,
                clock=lambda: datetime(2026, 7, 27, 0, 8, tzinfo=timezone.utc),
            )
            output: list[str] = []

            result = run_turn_cli(
                harness,
                created.game_id,
                "我敲探排水沟。",
                write_line=output.append,
            )

            self.assertEqual(result.status, "committed")
            self.assertEqual(
                output,
                [
                    "墙后传来潮湿的回声。",
                    "公开事实已确立：排水沟通往旧蓄水池。",
                ],
            )
            rendered = "\n".join(output)
            self.assertNotIn("蓄水池里有人守望", rendered)
            self.assertNotIn("不能显示的推理", rendered)

    def test_turn_cli_publishes_committed_mechanic_before_gm_final(self) -> None:
        """公开机械写盘后立即显示，final 叙事仍等待独立原子提交。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 28, tzinfo=timezone.utc),
            )
            created = store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_tracker",
                    display_name="林雁",
                )
            )
            output: list[str] = []
            model = PersistedMechanicThenFinalModel(
                store,
                created.game_id,
                output,
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_0001",
                mechanic_id_factory=lambda: "mechanic_0001",
                random_source=FixedRandom(),
                clock=lambda: datetime(2026, 7, 28, 0, 1, tzinfo=timezone.utc),
            )

            result = run_turn_cli(
                harness,
                created.game_id,
                "我检查牢门铰链。",
                write_line=output.append,
            )

            self.assertEqual(result.status, "committed")
            self.assertEqual(model.calls, 2)
            self.assertEqual(output[-1], "你发现了新鲜的工具刮痕。")
            self.assertEqual(len(output), 2)

    def test_turn_cli_never_publishes_hidden_mechanic(self) -> None:
        """隐藏检定只进入 GM 协议与存档，技术中断也不泄露其内容。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 28, tzinfo=timezone.utc),
            )
            created = store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_tracker",
                    display_name="林雁",
                )
            )
            secret_text = "守卫暗中判断是否听见排水沟的声音"
            model = ScriptedGameMasterModel(
                [
                    ModelResponse(
                        assistant_message={
                            "role": "assistant",
                            "content": None,
                            "reasoning_content": "隐藏推理材料",
                            "tool_calls": [
                                {
                                    "id": "call_001",
                                    "type": "function",
                                    "function": {
                                        "name": "make_check",
                                        "arguments": json.dumps(
                                            {
                                                "actor_id": "investigator_tracker",
                                                "ability": "listen",
                                                "difficulty": "regular",
                                                "dice_adjustment": {
                                                    "kind": "none",
                                                    "count": 0,
                                                },
                                                "action": secret_text,
                                                "stakes": "失败意味着守卫尚未察觉",
                                                "visibility": "hidden",
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                }
                            ],
                        },
                        finish_reason="tool_calls",
                        usage=None,
                        latency_ms=10,
                    ),
                    ModelCallError(
                        "request_timeout",
                        "private provider detail",
                        retryable=True,
                    ),
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_0001",
                mechanic_id_factory=lambda: "mechanic_0001",
                random_source=FixedRandom(),
                model_profile=zero_retry_profile(),
                clock=lambda: datetime(2026, 7, 28, 0, 2, tzinfo=timezone.utc),
            )
            output: list[str] = []

            result = run_turn_cli(
                harness,
                created.game_id,
                "我轻轻拨动排水沟盖。",
                write_line=output.append,
            )

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(len(output), 2)
            self.assertEqual(output[0], "未完成回合：turn_0001")
            rendered = "\n".join(output)
            self.assertIn("技术中断（request_timeout）", rendered)
            self.assertNotIn(secret_text, rendered)
            self.assertNotIn("隐藏推理材料", rendered)
            self.assertNotIn("骰点", rendered)

    def test_game_cli_interrupt_during_turn_preserves_incomplete_turn(
        self,
    ) -> None:
        """回合执行中 Ctrl+C 优雅退出，未完成回合保留且重启可发现。"""

        class InterruptModel(GameMasterModel):
            def __init__(self) -> None:
                self.requests = 0

            def complete(self, request: ModelRequest) -> ModelResponse:
                self.requests += 1
                raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_interrupt_turn",
                clock=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
            )
            created = store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_tracker",
                    display_name="林雁",
                )
            )
            model = InterruptModel()
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_interrupted",
                model_profile=zero_retry_profile(),
                clock=lambda: datetime(2026, 8, 7, 0, 1, tzinfo=timezone.utc),
            )
            output: list[str] = []

            def read_line(prompt: str) -> str:
                raise AssertionError("回合执行中不应读取新行动")

            with self.assertRaises(CliPlayerInterrupt):
                run_game_cli(
                    harness,
                    created.game_id,
                    "我检查门锁。",
                    read_line=read_line,
                    write_line=output.append,
                )

            saved = store.load_session(created.game_id).session
            self.assertEqual(model.requests, 1)
            self.assertEqual(saved["turns"], [])
            self.assertIsNotNone(saved["incomplete_turn"])
            self.assertEqual(
                saved["incomplete_turn"]["turn_id"],
                "turn_interrupted",
            )
            self.assertEqual(
                saved["incomplete_turn"]["player_input"],
                "我检查门锁。",
            )
            self.assertEqual(
                store.find_incomplete_session_ids(),
                ("game_interrupt_turn",),
            )
            self.assertEqual(
                output,
                ["已退出；未完成回合已保留，下次启动选择恢复即可继续。"],
            )

    def test_game_cli_interrupt_at_input_preserves_committed_turns(
        self,
    ) -> None:
        """回合之间的输入处 Ctrl+C 优雅退出，已提交回合保持完整。"""

        final_response = ModelResponse(
            assistant_message={
                "role": "assistant",
                "content": json.dumps(
                    {
                        "narration": "墙后传来潮湿的回声。",
                        "establish": [],
                        "retire": [],
                        "session_status": "ongoing",
                    },
                    ensure_ascii=False,
                ),
                "reasoning_content": None,
                "tool_calls": [],
            },
            finish_reason="stop",
            usage=None,
            latency_ms=10,
        )
        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_interrupt_input",
                clock=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
            )
            created = store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_tracker",
                    display_name="林雁",
                )
            )
            model = ScriptedGameMasterModel([final_response])
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_committed",
                clock=lambda: datetime(2026, 8, 7, 0, 1, tzinfo=timezone.utc),
            )
            prompts: list[str] = []
            output: list[str] = []

            def read_line(prompt: str) -> str:
                prompts.append(prompt)
                raise KeyboardInterrupt

            with self.assertRaises(CliPlayerInterrupt):
                run_game_cli(
                    harness,
                    created.game_id,
                    "我敲探排水沟。",
                    read_line=read_line,
                    write_line=output.append,
                )

            saved = store.load_session(created.game_id).session
            self.assertEqual(len(model.requests), 1)
            self.assertEqual(prompts, ["你的行动："])
            self.assertEqual(len(saved["turns"]), 1)
            self.assertEqual(
                saved["turns"][0]["player_input"],
                "我敲探排水沟。",
            )
            self.assertIsNone(saved["incomplete_turn"])
            self.assertEqual(saved["session_status"], "ongoing")
            self.assertEqual(store.find_incomplete_session_ids(), ())
            self.assertEqual(
                output,
                [
                    "墙后传来潮湿的回声。",
                    "已退出；本局已提交回合均已保存。",
                ],
            )

    def test_new_session_cli_interrupt_creates_no_session_and_no_model_calls(
        self,
    ) -> None:
        """建局问答中 Ctrl+C 优雅退出，不创建会话、不调用模型。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_never_created",
                clock=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
            )
            prompts: list[str] = []
            output: list[str] = []

            def read_line(prompt: str) -> str:
                prompts.append(prompt)
                raise KeyboardInterrupt

            with self.assertRaises(CliPlayerInterrupt):
                run_new_session_cli(
                    store,
                    read_line=read_line,
                    write_line=output.append,
                )

            self.assertEqual(prompts, ["请选择调查员编号："])
            self.assertFalse((Path(directory) / "sessions").exists())
            self.assertEqual(output[0], "选择调查员：")
            self.assertEqual(output[-1], "已退出。")
            self.assertNotIn("Traceback", "\n".join(output))

    def test_main_returns_130_for_player_interrupt(self) -> None:
        output = io.StringIO()
        with (
            patch.dict(
                os.environ,
                {
                    "MONMUSU_PROVIDER": "deepseek",
                    "DEEPSEEK_API_KEY": "sk-cli-secret-fragment",
                    "MONMUSU_DEEPSEEK_MODEL_ID": "deepseek-v4-flash",
                    "MONMUSU_DEEPSEEK_THINKING": "false",
                },
                clear=True,
            ),
            patch("monmusu_agent.agentic_cli.load_dotenv"),
            patch("monmusu_agent.agentic_cli.AgenticSessionStore"),
            patch("monmusu_agent.agentic_cli.compose_deepseek_harness"),
            patch(
                "monmusu_agent.agentic_cli.run_agentic_cli",
                side_effect=CliPlayerInterrupt,
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(main([]), 130)

        self.assertEqual(output.getvalue(), "模型提供商：DeepSeek 官方\n")

    def test_main_returns_130_for_uncaught_keyboard_interrupt(self) -> None:
        output = io.StringIO()
        with (
            patch.dict(
                os.environ,
                {
                    "MONMUSU_PROVIDER": "deepseek",
                    "DEEPSEEK_API_KEY": "sk-cli-secret-fragment",
                    "MONMUSU_DEEPSEEK_MODEL_ID": "deepseek-v4-flash",
                    "MONMUSU_DEEPSEEK_THINKING": "false",
                },
                clear=True,
            ),
            patch("monmusu_agent.agentic_cli.load_dotenv"),
            patch("monmusu_agent.agentic_cli.AgenticSessionStore"),
            patch("monmusu_agent.agentic_cli.compose_deepseek_harness"),
            patch(
                "monmusu_agent.agentic_cli.run_agentic_cli",
                side_effect=KeyboardInterrupt,
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(main([]), 130)

        self.assertEqual(
            output.getvalue(),
            "模型提供商：DeepSeek 官方\n已退出。\n",
        )


if __name__ == "__main__":
    unittest.main()
