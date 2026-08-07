import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from monmusu_agent.agentic_harness import (
    AgenticHarness,
    AgenticSessionCompleteError,
    AgenticTurnBlockedError,
    AgenticTurnInputError,
    PublicMechanic,
)
from monmusu_agent.agentic_model import (
    ModelCallError,
    ModelResponse,
    ScriptedGameMasterModel,
    deepseek_model_profile,
)
from monmusu_agent.agentic_session import (
    AgenticSessionLoadError,
    AgenticSessionStore,
    NewSessionRequest,
)
from monmusu_agent.storage import read_json, write_json_atomic


class ScriptedRandom:
    """按独立 d10 序列提供随机边界，并记录是否被调用。"""

    def __init__(self, values: tuple[int, ...]) -> None:
        self.values = iter(values)
        self.calls: list[tuple[int, int]] = []

    def randint(self, minimum: int, maximum: int) -> int:
        self.calls.append((minimum, maximum))
        return next(self.values)


class AgenticHarnessTest(unittest.TestCase):
    def _create_session(self, root: Path) -> tuple[AgenticSessionStore, str]:
        store = AgenticSessionStore(
            session_root=root / "sessions",
            game_id_factory=lambda: "game_test_0001",
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
    def _response(final: object, *, reasoning: str | None = None) -> ModelResponse:
        content = final if isinstance(final, str) else json.dumps(
            final,
            ensure_ascii=False,
        )
        return ModelResponse(
            assistant_message={
                "role": "assistant",
                "content": content,
                "reasoning_content": reasoning,
                "tool_calls": [],
            },
            finish_reason="stop",
            usage=None,
            latency_ms=10,
        )

    @staticmethod
    def _tool_response(
        arguments: object,
        *,
        tool_call_id: object = "call_001",
        name: object = "make_check",
        reasoning: str | None = None,
        content: str | None = None,
    ) -> ModelResponse:
        arguments_raw = arguments if isinstance(arguments, str) else json.dumps(
            arguments,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return ModelResponse(
            assistant_message={
                "role": "assistant",
                "content": content,
                "reasoning_content": reasoning,
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": arguments_raw},
                    }
                ],
            },
            finish_reason="tool_calls",
            usage=None,
            latency_ms=10,
        )

    @staticmethod
    def _multi_tool_response(*tool_call_ids: str) -> ModelResponse:
        return ModelResponse(
            assistant_message={
                "role": "assistant",
                "content": None,
                "reasoning_content": None,
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": "make_check",
                            "arguments": "{}",
                        },
                    }
                    for tool_call_id in tool_call_ids
                ],
            },
            finish_reason="tool_calls",
            usage=None,
            latency_ms=10,
        )

    @staticmethod
    def _valid_check_arguments() -> dict[str, object]:
        return {
            "actor_id": "investigator_tracker",
            "ability": "spot_hidden",
            "difficulty": "regular",
            "dice_adjustment": {"kind": "none", "count": 0},
            "action": "检查牢门",
            "stakes": "失败会错过痕迹",
            "visibility": "public",
        }

    @staticmethod
    def _tool_message_count(messages: object) -> int:
        if not isinstance(messages, (list, tuple)):
            return 0
        return sum(
            1
            for message in messages
            if isinstance(message, dict) and message.get("role") == "tool"
        )

    def test_make_check_commits_before_same_gm_final(self) -> None:
        """公开检定先形成可信机械，再以匹配协议 ID 返回同一个 GM。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            arguments = {
                "actor_id": "investigator_tracker",
                "ability": "spot_hidden",
                "difficulty": "regular",
                "dice_adjustment": {"kind": "none", "count": 0},
                "action": "检查牢门铰链附近的刮痕",
                "stakes": "失败会错过守卫留下的关键痕迹",
                "visibility": "public",
            }
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(arguments),
                    self._response(
                        {
                            "narration": "你从铰链旁发现了新鲜的工具刮痕。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                    self._response(
                        {
                            "narration": "那道刮痕仍清晰地留在铰链旁。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            random_source = ScriptedRandom((3, 4))
            turn_ids = iter(("turn_0001", "turn_0002"))
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: next(turn_ids),
                mechanic_id_factory=lambda: "mechanic_0001",
                random_source=random_source,
                clock=lambda: datetime(2026, 7, 28, 0, 1, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我仔细查看牢门的铰链。")

            expected_mechanic = PublicMechanic(
                mechanic_id="mechanic_0001",
                actor_id="investigator_tracker",
                ability="spot_hidden",
                ability_value=70,
                difficulty="regular",
                target=70,
                dice_adjustment={"kind": "none", "count": 0},
                roll=43,
                success_level="regular_success",
                action="检查牢门铰链附近的刮痕",
                stakes="失败会错过守卫留下的关键痕迹",
            )
            self.assertEqual(result.status, "committed")
            self.assertEqual(result.public_mechanics, (expected_mechanic,))
            self.assertEqual(random_source.calls, [(0, 9), (0, 9)])
            self.assertEqual(len(model.requests), 2)
            self.assertEqual(
                [tool["function"]["name"] for tool in model.requests[0].tools],
                ["make_check"],
            )
            tool_parameters = model.requests[0].tools[0]["function"]["parameters"]
            self.assertEqual(
                set(tool_parameters["properties"]),
                {
                    "actor_id",
                    "ability",
                    "difficulty",
                    "dice_adjustment",
                    "action",
                    "stakes",
                    "visibility",
                },
            )
            self.assertEqual(
                model.requests[0].model_profile["enabled_tools"],
                ["make_check"],
            )
            initial_package = json.loads(model.requests[0].messages[1]["content"])
            self.assertEqual(
                [
                    tool["function"]["name"]
                    for tool in initial_package["AVAILABLE_TOOLS"]
                ],
                ["make_check"],
            )
            self.assertEqual(model.requests[1].messages[-2]["role"], "assistant")
            tool_message = model.requests[1].messages[-1]
            self.assertEqual(tool_message["role"], "tool")
            self.assertEqual(tool_message["tool_call_id"], "call_001")
            tool_result = json.loads(tool_message["content"])
            self.assertTrue(tool_result["ok"])
            self.assertEqual(tool_result["result"]["roll"], 43)
            self.assertEqual(tool_result["result"]["target"], 70)

            session = store.load_session(game_id).session
            self.assertIsNone(session["incomplete_turn"])
            mechanic = session["turns"][0]["mechanics"][0]
            self.assertEqual(mechanic["mechanic_id"], "mechanic_0001")
            self.assertEqual(mechanic["roll"], 43)
            self.assertEqual(mechanic["success_level"], "regular_success")

            second = harness.start_turn(game_id, "我记住这道刮痕，继续观察。")

            self.assertEqual(second.status, "committed")
            second_package = json.loads(model.requests[2].messages[1]["content"])
            historical_mechanic = second_package["COMMITTED_TURNS"][0][
                "mechanics"
            ][0]
            self.assertEqual(historical_mechanic["mechanic_id"], "mechanic_0001")
            self.assertEqual(historical_mechanic["roll"], 43)

    def test_direct_final_commits_turn_and_public_projection(self) -> None:
        """合法 final 同时提交回合与正典，隐藏事实不进入玩家投影。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            response = ModelResponse(
                assistant_message={
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "narration": "排水沟后传来空洞的水声，墙内确实另有空间。",
                            "establish": [
                                {
                                    "visibility": "public",
                                    "text": "牢房后墙的排水沟与旧蓄水池相通。",
                                },
                                {
                                    "visibility": "hidden",
                                    "text": "蓄水池里潜伏着一名拾骨者。",
                                },
                            ],
                            "retire": [],
                            "session_status": "ongoing",
                        },
                        ensure_ascii=False,
                    ),
                    "reasoning_content": "不得进入玩家记录的隐藏推理",
                    "tool_calls": [],
                },
                finish_reason="stop",
                usage={"prompt_tokens": 100, "completion_tokens": 40},
                latency_ms=15,
            )
            model = ScriptedGameMasterModel([response])
            fact_ids = iter(("fact_1001", "fact_1002"))
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_0001",
                fact_id_factory=lambda: next(fact_ids),
                clock=lambda: datetime(2026, 7, 27, 0, 1, tzinfo=timezone.utc),
            )

            result = harness.start_turn(
                game_id,
                "我拆下水槽的铜管，敲探牢房后墙的排水沟。",
            )

            self.assertEqual(result.status, "committed")
            self.assertEqual(result.turn_id, "turn_0001")
            self.assertEqual(
                result.narration,
                "排水沟后传来空洞的水声，墙内确实另有空间。",
            )
            self.assertEqual(
                [change.text for change in result.public_fact_changes],
                ["牢房后墙的排水沟与旧蓄水池相通。"],
            )
            self.assertIsNone(result.error_code)

            loaded = store.load_session(game_id)
            self.assertIsNone(loaded.session["incomplete_turn"])
            self.assertEqual(loaded.session["session_status"], "ongoing")
            self.assertEqual(
                loaded.session["turns"],
                [
                    {
                        "turn_id": "turn_0001",
                        "player_input": "我拆下水槽的铜管，敲探牢房后墙的排水沟。",
                        "mechanics": [],
                        "narration": "排水沟后传来空洞的水声，墙内确实另有空间。",
                        "established_fact_ids": ["fact_1001", "fact_1002"],
                        "retirements": [],
                        "session_status": "ongoing",
                        "committed_at": "2026-07-27T00:01:00Z",
                    }
                ],
            )
            committed_facts = loaded.session["facts"][-2:]
            self.assertEqual(
                [fact["fact_id"] for fact in committed_facts],
                ["fact_1001", "fact_1002"],
            )
            self.assertEqual(
                [fact["visibility"] for fact in committed_facts],
                ["public", "hidden"],
            )
            self.assertTrue(
                all(
                    fact["established_turn_id"] == "turn_0001"
                    and fact["origin"] == {"kind": "gm_turn", "source_ref": None}
                    for fact in committed_facts
                )
            )
            persisted_text = (loaded.session_directory / "session.json").read_text(
                encoding="utf-8"
            )
            lifecycle = harness.get_session_state(game_id)
            self.assertNotIn("不得进入玩家记录的隐藏推理", persisted_text)
            self.assertNotIn("蓄水池里潜伏着一名拾骨者。", repr(lifecycle))

    def test_model_profile_rejects_unknown_secret_before_turn_allocation(self) -> None:
        """provider 配置只冻结已知非秘密字段，凭据不能触及存档。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            loaded = store.load_session(game_id)
            before = read_json(loaded.session_directory / "session.json")
            allocated_turn_ids: list[str] = []
            model = ScriptedGameMasterModel([])

            def forbidden_turn_id() -> str:
                allocated_turn_ids.append("turn_forbidden")
                return "turn_forbidden"

            with self.assertRaisesRegex(AgenticTurnInputError, "model_profile"):
                AgenticHarness(
                    store,
                    model,
                    model_profile={
                        "provider": "deepseek",
                        "model_id": "deepseek-v4-flash",
                        "thinking": False,
                        "stream": False,
                        "response_format": "json_object",
                        "temperature": None,
                        "top_p": None,
                        "max_tokens": 4096,
                        "prompt_revision": "gm-capability-charter-agentic-mvp-2",
                        "tool_schema_version": "coc-tools-agentic-mvp-1",
                        "enabled_tools": [],
                        "api_key": "must-not-persist",
                    },
                    turn_id_factory=forbidden_turn_id,
                )

            after = read_json(loaded.session_directory / "session.json")
            self.assertEqual(after, before)
            self.assertEqual(allocated_turn_ids, [])
            self.assertEqual(model.requests, [])
            self.assertNotIn("must-not-persist", json.dumps(after))

    def test_system_message_uses_authoritative_capability_charter(self) -> None:
        """运行时 System 正文与权威文档中的章程代码块保持单一来源。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "你仍站在牢门内侧。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_0001",
                clock=lambda: datetime(2026, 7, 27, 0, 1, tzinfo=timezone.utc),
            )

            harness.start_turn(game_id, "我先观察牢门。")

            prompt_document = (
                Path(__file__).parents[1]
                / "docs"
                / "agentic_mvp"
                / "gm_prompt.md"
            ).read_text(encoding="utf-8")
            charter_section = prompt_document.split("## 主持能力章程", 1)[1]
            authoritative = charter_section.split("```text", 1)[1].split("```", 1)[0]
            self.assertEqual(
                model.requests[0].messages[0],
                {"role": "system", "content": authoritative.strip()},
            )
            system_text = model.requests[0].messages[0]["content"]
            self.assertIn("玩家拥有其调查员的主动意志", system_text)
            self.assertIn("隐藏事实可以影响你的主持", system_text)
            self.assertIn("每次回答只能二选一", system_text)
            self.assertIn('"session_status": "ongoing"', system_text)
            self.assertIn("不得返回空字符串或纯空白", system_text)
            self.assertIn("绝不能包含 fact_id", system_text)

    def test_second_turn_receives_complete_canon_and_retires_active_fact(self) -> None:
        """后续请求携带隐藏正典与完整记录，结束事实仍保留历史。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            opening_fact_id = store.load_session(game_id).session["facts"][0][
                "fact_id"
            ]
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "石墙后传来潮湿的回声。",
                            "establish": [
                                {
                                    "visibility": "public",
                                    "text": "排水沟通往一处旧蓄水池。",
                                },
                                {
                                    "visibility": "hidden",
                                    "text": "蓄水池上方有人守望牢房。",
                                },
                            ],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                    self._response(
                        {
                            "narration": "你钻进排水沟，牢房已被留在身后。",
                            "establish": [],
                            "retire": [
                                {
                                    "fact_id": opening_fact_id,
                                    "reason": "调查员已经离开石牢。",
                                }
                            ],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            turn_ids = iter(("turn_0001", "turn_0002"))
            fact_ids = iter(("fact_1001", "fact_1002"))
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: next(turn_ids),
                fact_id_factory=lambda: next(fact_ids),
                clock=lambda: datetime(2026, 7, 27, 0, 2, tzinfo=timezone.utc),
            )

            first = harness.start_turn(game_id, "我探查排水沟。")
            second = harness.start_turn(game_id, "我钻进去，离开这间牢房。")

            self.assertEqual(first.status, "committed")
            self.assertEqual(second.status, "committed")
            self.assertEqual(
                [(change.kind, change.fact_id, change.text) for change in second.public_fact_changes],
                [("retired", opening_fact_id, "调查员已经离开石牢。")],
            )
            self.assertEqual(len(model.requests), 2)
            second_package = json.loads(model.requests[1].messages[1]["content"])
            active_by_id = {
                fact["fact_id"]: fact for fact in second_package["ACTIVE_FACTS"]
            }
            self.assertEqual(
                active_by_id["fact_1001"]["text"],
                "排水沟通往一处旧蓄水池。",
            )
            self.assertEqual(
                active_by_id["fact_1002"]["text"],
                "蓄水池上方有人守望牢房。",
            )
            self.assertEqual(active_by_id["fact_1002"]["visibility"], "hidden")
            self.assertEqual(
                second_package["COMMITTED_TURNS"][0]["established_facts"][1][
                    "text"
                ],
                "蓄水池上方有人守望牢房。",
            )
            self.assertEqual(
                second_package["COMMITTED_TURNS"][0]["player_input"],
                "我探查排水沟。",
            )
            self.assertEqual(
                second_package["MODULE_REFERENCE"],
                store.load_session(game_id).module_reference,
            )
            self.assertEqual(
                second_package["CHARACTER_REFERENCE"],
                store.load_session(game_id).character_reference,
            )
            self.assertEqual(
                [tool["function"]["name"] for tool in model.requests[1].tools],
                ["make_check"],
            )
            self.assertIn(
                "<PLAYER_INPUT>\n我钻进去，离开这间牢房。\n</PLAYER_INPUT>",
                model.requests[1].messages[2]["content"],
            )

            loaded = store.load_session(game_id)
            retired = next(
                fact
                for fact in loaded.session["facts"]
                if fact["fact_id"] == opening_fact_id
            )
            self.assertEqual(retired["status"], "retired")
            self.assertEqual(retired["retired_turn_id"], "turn_0002")
            self.assertEqual(retired["retire_reason"], "调查员已经离开石牢。")

    def test_retired_opening_fact_remains_in_complete_setup_history(self) -> None:
        """开场事实退出 ACTIVE_FACTS 后仍以原文与来源进入完整历史。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            opening_fact = store.load_session(game_id).session["facts"][0]
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "牢门已经松开。",
                            "establish": [],
                            "retire": [
                                {
                                    "fact_id": opening_fact["fact_id"],
                                    "reason": "调查员已经打开牢门。",
                                }
                            ],
                            "session_status": "ongoing",
                        }
                    ),
                    self._response(
                        {
                            "narration": "你越过已经打开的牢门。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            turn_ids = iter(("turn_0001", "turn_0002"))
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: next(turn_ids),
                clock=lambda: datetime(2026, 7, 27, 0, 2, tzinfo=timezone.utc),
            )

            harness.start_turn(game_id, "我打开牢门。")
            harness.start_turn(game_id, "我走出去。")

            package = json.loads(model.requests[1].messages[1]["content"])
            self.assertNotIn(
                opening_fact["fact_id"],
                {fact["fact_id"] for fact in package["ACTIVE_FACTS"]},
            )
            history_by_id = {
                fact["fact_id"]: fact
                for fact in package["OPENING_FACT_HISTORY"]
            }
            historical = history_by_id[opening_fact["fact_id"]]
            self.assertEqual(historical["text"], opening_fact["text"])
            self.assertEqual(historical["origin"], opening_fact["origin"])
            self.assertEqual(historical["status"], "retired")

    def test_loader_rejects_broken_turn_fact_bidirectional_integrity(self) -> None:
        """重启前校验事实确立与结束都能反向追溯到同一已提交回合。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            opening_fact_id = store.load_session(game_id).session["facts"][0][
                "fact_id"
            ]
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "一条新通道露了出来。",
                            "establish": [
                                {
                                    "visibility": "public",
                                    "text": "旧蓄水池有一条通道。",
                                }
                            ],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                    self._response(
                        {
                            "narration": "牢门已被留在身后。",
                            "establish": [],
                            "retire": [
                                {
                                    "fact_id": opening_fact_id,
                                    "reason": "调查员离开了石牢。",
                                }
                            ],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            turn_ids = iter(("turn_0001", "turn_0002"))
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: next(turn_ids),
                fact_id_factory=lambda: "fact_1001",
                clock=lambda: datetime(2026, 7, 27, 0, 3, tzinfo=timezone.utc),
            )
            harness.start_turn(game_id, "我寻找另一条通道。")
            harness.start_turn(game_id, "我离开石牢。")
            session_file = store.load_session(game_id).session_directory / "session.json"
            valid = read_json(session_file)

            missing_declaration = json.loads(json.dumps(valid, ensure_ascii=False))
            missing_declaration["turns"][0]["established_fact_ids"] = []
            write_json_atomic(session_file, missing_declaration)
            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "回合与事实引用不一致",
            ):
                store.load_session(game_id)

            mismatched_retirement = json.loads(json.dumps(valid, ensure_ascii=False))
            mismatched_retirement["facts"][0]["retire_reason"] = "被篡改的原因。"
            write_json_atomic(session_file, mismatched_retirement)
            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "FactRecord 结束历史不一致",
            ):
                store.load_session(game_id)

    def test_invalid_final_shapes_and_references_interrupt_without_commit(self) -> None:
        """代表性 schema 与引用错误都不能让未提交文本成为正典。"""

        cases = {
            "unknown top field": {
                "narration": "不会提交的叙事",
                "establish": [],
                "retire": [],
                "session_status": "ongoing",
                "diagnostic": "forged",
            },
            "wrong narration type": {
                "narration": ["不会提交的叙事"],
                "establish": [],
                "retire": [],
                "session_status": "ongoing",
            },
            "unknown establish field": {
                "narration": "不会提交的叙事",
                "establish": [
                    {
                        "visibility": "public",
                        "text": "不会提交的事实",
                        "fact_id": "fact_forged",
                    }
                ],
                "retire": [],
                "session_status": "ongoing",
            },
            "blank establish text": {
                "narration": "不会提交的叙事",
                "establish": [{"visibility": "public", "text": "   "}],
                "retire": [],
                "session_status": "ongoing",
            },
            "invalid visibility": {
                "narration": "不会提交的叙事",
                "establish": [{"visibility": "private", "text": "秘密"}],
                "retire": [],
                "session_status": "ongoing",
            },
            "unhashable visibility": {
                "narration": "不会提交的叙事",
                "establish": [{"visibility": {}, "text": "秘密"}],
                "retire": [],
                "session_status": "ongoing",
            },
            "unhashable session status": {
                "narration": "不会提交的叙事",
                "establish": [],
                "retire": [],
                "session_status": [],
            },
            "unknown retirement": {
                "narration": "不会提交的叙事",
                "establish": [],
                "retire": [{"fact_id": "fact_missing", "reason": "不存在"}],
                "session_status": "ongoing",
            },
            "duplicate retirement": {
                "narration": "不会提交的叙事",
                "establish": [],
                "retire": [
                    {"fact_id": "fact_0001", "reason": "第一次"},
                    {"fact_id": "fact_0001", "reason": "第二次"},
                ],
                "session_status": "ongoing",
            },
            "blank retirement reason": {
                "narration": "不会提交的叙事",
                "establish": [],
                "retire": [{"fact_id": "fact_0001", "reason": " "}],
                "session_status": "ongoing",
            },
        }
        for label, final in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                before = store.load_session(game_id).session
                model = ScriptedGameMasterModel(
                    [self._response(final), self._response(final)]
                )
                harness = AgenticHarness(
                    store,
                    model,
                    turn_id_factory=lambda: "turn_invalid",
                    clock=lambda: datetime(
                        2026,
                        7,
                        27,
                        0,
                        3,
                        tzinfo=timezone.utc,
                    ),
                )

                result = harness.start_turn(game_id, "我尝试一项行动。")

                self.assertEqual(result.status, "interrupted")
                self.assertEqual(result.error_code, "invalid_final_response")
                self.assertIsNone(result.narration)
                self.assertEqual(result.public_fact_changes, ())
                loaded = store.load_session(game_id).session
                self.assertEqual(loaded["turns"], before["turns"])
                self.assertEqual(loaded["facts"], before["facts"])
                self.assertEqual(
                    loaded["incomplete_turn"]["turn_id"],
                    "turn_invalid",
                )
                self.assertEqual(
                    loaded["incomplete_turn"]["last_failure"]["code"],
                    "invalid_final_response",
                )

    def test_invalid_final_receives_one_structure_repair_without_tools(self) -> None:
        """首次业务 schema 失败只给同一 GM 一次无工具修正机会。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "首份结构不完整。",
                            "establish": [],
                            "retire": [],
                        }
                    ),
                    self._response(
                        {
                            "narration": "修正后的叙事。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_repair",
                clock=lambda: datetime(2026, 7, 28, 0, 9, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查牢门。")

            self.assertEqual(result.status, "committed")
            self.assertEqual(result.narration, "修正后的叙事。")
            self.assertEqual(len(model.requests), 2)
            self.assertEqual(model.requests[0].tools[0]["function"]["name"], "make_check")
            self.assertEqual(model.requests[1].tools, ())
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            self.assertIsNone(incomplete)
            committed = store.load_session(game_id).session["turns"][0]
            self.assertEqual(committed["narration"], "修正后的叙事。")

    def test_eighth_tool_response_commits_tool_and_stops_without_ninth_request(self) -> None:
        """第八次合法工具响应先持久化，再以 step limit 中断。"""

        arguments = {
            "actor_id": "investigator_tracker",
            "ability": "spot_hidden",
            "difficulty": "regular",
            "dice_adjustment": {"kind": "none", "count": 0},
            "action": "检查牢门",
            "stakes": "失败会错过痕迹",
            "visibility": "public",
        }
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            random_source = ScriptedRandom((3, 4) * 8)
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        "{}",
                        tool_call_id=f"call_{index}",
                        name="unknown_tool",
                    )
                    for index in range(1, 8)
                ]
                + [self._tool_response(arguments, tool_call_id="call_8")]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_eighth_tool",
                mechanic_id_factory=lambda: f"mechanic_{len(model.requests) + 1}",
                random_source=random_source,
                clock=lambda: datetime(2026, 7, 28, 0, 10, tzinfo=timezone.utc),
            )

            published: list[PublicMechanic] = []
            result = harness.start_turn(
                game_id,
                "我检查牢门。",
                public_mechanic_sink=published.append,
            )

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "step_limit_exceeded")
            self.assertEqual(len(model.requests), 8)
            self.assertEqual(len(result.public_mechanics), 1)
            self.assertEqual(published, list(result.public_mechanics))
            self.assertEqual(random_source.calls, [(0, 9), (0, 9)])
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            self.assertIsNotNone(incomplete)
            assert incomplete is not None
            self.assertEqual(incomplete["round_trips_used"], 8)
            self.assertEqual(len(incomplete["mechanics"]), 1)

    def test_eighth_response_variants_are_processed_without_a_ninth_request(self) -> None:
        """第八次 final、工具错误和多工具分支都保存可恢复状态后停止。"""

        valid_final = {
            "narration": "第八次答复仍可正常提交。",
            "establish": [],
            "retire": [],
            "session_status": "ongoing",
        }
        invalid_final = {
            "narration": "第八次无效答复不应成为正典。",
            "establish": [],
            "retire": [],
        }
        variants = (
            ("final", self._response(valid_final), "committed", None, 0, 0),
            (
                "invalid_final",
                self._response(invalid_final),
                "interrupted",
                "step_limit_exceeded",
                7,
                0,
            ),
            (
                "tool_error",
                self._tool_response("{}", tool_call_id="call_8", name="unknown_tool"),
                "interrupted",
                "step_limit_exceeded",
                8,
                0,
            ),
            (
                "multiple_tool_error",
                self._multi_tool_response("call_8a", "call_8b"),
                "interrupted",
                "step_limit_exceeded",
                9,
                0,
            ),
            (
                "unpairable_protocol",
                self._multi_tool_response("call_8", "call_8"),
                "interrupted",
                "provider_protocol_error",
                7,
                1,
            ),
            (
                "unpairable_single",
                self._tool_response("{}", tool_call_id=None),
                "interrupted",
                "provider_protocol_error",
                7,
                1,
            ),
            (
                "empty_content",
                self._response(""),
                "interrupted",
                "invalid_model_response",
                7,
                1,
            ),
            (
                "malformed_json",
                self._response("{"),
                "interrupted",
                "step_limit_exceeded",
                7,
                0,
            ),
            (
                "truncated",
                ModelResponse(
                    assistant_message={
                        "role": "assistant",
                        "content": None,
                        "reasoning_content": None,
                        "tool_calls": [],
                    },
                    finish_reason="length",
                    usage=None,
                    latency_ms=10,
                ),
                "interrupted",
                "provider_response_error",
                7,
                1,
            ),
        )

        for (
            label,
            eighth_response,
            expected_status,
            expected_error,
            expected_interactions,
            expected_protocol_errors,
        ) in variants:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                prefix = [
                    self._tool_response(
                        "{}",
                        tool_call_id=f"call_{index}",
                        name="unknown_tool",
                    )
                    for index in range(1, 8)
                ]
                model = ScriptedGameMasterModel(prefix + [eighth_response])
                harness = AgenticHarness(
                    store,
                    model,
                    turn_id_factory=lambda label=label: f"turn_eighth_{label}",
                    clock=lambda: datetime(
                        2026,
                        7,
                        28,
                        0,
                        10,
                        tzinfo=timezone.utc,
                    ),
                )

                result = harness.start_turn(game_id, "我检查牢门。")

                self.assertEqual(result.status, expected_status)
                self.assertEqual(result.error_code, expected_error)
                self.assertEqual(len(model.requests), 8)
                session = store.load_session(game_id).session
                if expected_status == "committed":
                    self.assertEqual(session["incomplete_turn"], None)
                    self.assertEqual(len(session["turns"]), 1)
                else:
                    incomplete = session["incomplete_turn"]
                    assert incomplete is not None
                    self.assertEqual(incomplete["round_trips_used"], 8)
                    self.assertEqual(
                        len(incomplete["tool_interactions"]),
                        expected_interactions,
                    )
                    self.assertEqual(
                        len(incomplete["provider_protocol_errors"]),
                        expected_protocol_errors,
                    )

    def test_multiple_usable_tool_calls_are_persisted_as_errors_and_continue(self) -> None:
        """可关联多工具响应不执行机械，而是逐 ID 返回协议错误。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._multi_tool_response("call_a", "call_b"),
                    self._response(
                        {
                            "narration": "我重新选择一个工具。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_multiple",
                clock=lambda: datetime(2026, 7, 28, 0, 11, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查牢门。")

            self.assertEqual(result.status, "committed")
            self.assertEqual(model.requests[1].tools[0]["function"]["name"], "make_check")
            tool_result_messages = [
                message
                for message in model.requests[1].messages
                if message.get("role") == "tool"
            ]
            self.assertEqual(len(tool_result_messages), 2)
            self.assertTrue(
                all(
                    "multiple_tool_calls_not_allowed" in message["content"]
                    for message in tool_result_messages
                )
            )

    def test_request_timeout_is_clamped_to_remaining_attempt_budget(self) -> None:
        """单次请求 timeout 不能超过当前执行尝试的剩余时间。"""

        class ControlledClock:
            def __init__(self) -> None:
                self.current = datetime(2026, 7, 28, 0, 12, tzinfo=timezone.utc)
                self.calls = 0

            def __call__(self) -> datetime:
                self.calls += 1
                if self.calls == 2:
                    self.current += timedelta(seconds=175)
                return self.current

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            clock = ControlledClock()
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "在剩余时间内完成。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_request_budget",
                clock=clock,
            )

            result = harness.start_turn(game_id, "我观察周围。")

            self.assertEqual(result.status, "committed")
            self.assertEqual(model.requests[0].request_timeout_seconds, 5.0)

    def test_attempt_timeout_before_final_commit_keeps_turn_incomplete(self) -> None:
        """响应返回后若整次尝试已过期，不提交最终叙事。"""

        class AdvancingModel:
            def __init__(self, clock: object) -> None:
                self.clock = clock
                self.requests: list[object] = []

            def complete(self, request: object) -> ModelResponse:
                self.requests.append(request)
                assert isinstance(self.clock, ControlledClock)
                self.clock.current += timedelta(seconds=181)
                return AgenticHarnessTest._response(
                    {
                        "narration": "这段叙事不能提交。",
                        "establish": [],
                        "retire": [],
                        "session_status": "ongoing",
                    }
                )

        class ControlledClock:
            def __init__(self) -> None:
                self.current = datetime(2026, 7, 28, 0, 13, tzinfo=timezone.utc)

            def __call__(self) -> datetime:
                return self.current

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            clock = ControlledClock()
            model = AdvancingModel(clock)
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_attempt_timeout",
                clock=clock,
            )

            result = harness.start_turn(game_id, "我观察周围。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "attempt_timeout")
            loaded = store.load_session(game_id).session
            self.assertEqual(loaded["turns"], [])
            self.assertEqual(
                loaded["incomplete_turn"]["last_failure"]["code"],
                "attempt_timeout",
            )

    def test_attempt_timeout_during_final_build_precedes_atomic_write(self) -> None:
        """最终聚合构造期间过期时，写入前的截止检查仍阻止提交。"""

        class ControlledClock:
            def __init__(self) -> None:
                self.current = datetime(2026, 7, 28, 0, 13, tzinfo=timezone.utc)
                self.calls = 0

            def __call__(self) -> datetime:
                self.calls += 1
                if self.calls == 4:
                    self.current += timedelta(seconds=181)
                return self.current

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            clock = ControlledClock()
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "构造期间过期的叙事不能提交。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_build_timeout",
                clock=clock,
            )

            result = harness.start_turn(game_id, "我观察周围。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "attempt_timeout")
            session = store.load_session(game_id).session
            self.assertEqual(session["turns"], [])
            self.assertEqual(
                session["incomplete_turn"]["last_failure"]["code"],
                "attempt_timeout",
            )

    def test_unpairable_multiple_tool_response_keeps_only_raw_protocol_error(self) -> None:
        """多工具 ID 不可关联时不伪造任何 assistant/tool 配对。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [self._multi_tool_response("call_same", "call_same")]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_unpairable_multiple",
                clock=lambda: datetime(2026, 7, 28, 0, 14, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查牢门。")

            self.assertEqual(result.error_code, "provider_protocol_error")
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            assert incomplete is not None
            self.assertEqual(len(incomplete["deepseek_messages"]), 3)
            self.assertEqual(incomplete["tool_interactions"], [])
            self.assertEqual(len(incomplete["provider_protocol_errors"]), 1)

    def test_second_invalid_final_does_not_receive_a_second_repair(self) -> None:
        """一次修正仍失败时保持中断，不进入第三次模型请求。"""

        invalid = {
            "narration": "结构错误。",
            "establish": [],
            "retire": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [self._response(invalid), self._response(invalid)]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_repair_failed",
                clock=lambda: datetime(2026, 7, 28, 0, 15, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查牢门。")

            self.assertEqual(result.error_code, "invalid_final_response")
            self.assertEqual(len(model.requests), 2)
            self.assertEqual(model.requests[1].tools, ())
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            assert incomplete is not None
            self.assertEqual(incomplete["structure_repairs_used"], 1)
            self.assertEqual(incomplete["total_structure_repairs"], 1)

    def test_truncated_provider_response_is_an_interruption_without_fallback(self) -> None:
        """provider 截断不会被 Harness 伪装成可提交叙事。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    ModelResponse(
                        assistant_message={
                            "role": "assistant",
                            "content": None,
                            "reasoning_content": None,
                            "tool_calls": [],
                        },
                        finish_reason="length",
                        usage=None,
                        latency_ms=10,
                    )
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_truncated",
                clock=lambda: datetime(2026, 7, 28, 0, 16, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查牢门。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "provider_response_error")
            self.assertIsNone(result.narration)
            self.assertEqual(store.load_session(game_id).session["turns"], [])

    def test_unknown_finish_reason_is_a_provider_protocol_interruption(self) -> None:
        """未知模型步骤不能伪装成合法 final。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            final = {
                "narration": "未知步骤不能提交。",
                "establish": [],
                "retire": [],
                "session_status": "ongoing",
            }
            model = ScriptedGameMasterModel(
                [
                    ModelResponse(
                        assistant_message={
                            "role": "assistant",
                            "content": json.dumps(final, ensure_ascii=False),
                            "reasoning_content": None,
                            "tool_calls": [],
                        },
                        finish_reason="mystery",
                        usage=None,
                        latency_ms=10,
                    )
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_unknown_step",
                clock=lambda: datetime(2026, 7, 28, 0, 17, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查牢门。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "invalid_model_response")
            self.assertEqual(store.load_session(game_id).session["turns"], [])


    def test_retired_fact_cannot_be_retired_again(self) -> None:
        """已结束事实不是后续 final 可再次结束的有效引用。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            opening_fact_id = store.load_session(game_id).session["facts"][0][
                "fact_id"
            ]
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "牢门已经打开。",
                            "establish": [],
                            "retire": [
                                {
                                    "fact_id": opening_fact_id,
                                    "reason": "牢门已经打开。",
                                }
                            ],
                            "session_status": "ongoing",
                        }
                    ),
                    self._response(
                        {
                            "narration": "不会提交的第二轮叙事。",
                            "establish": [],
                            "retire": [
                                {
                                    "fact_id": opening_fact_id,
                                    "reason": "重复结束。",
                                }
                            ],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            turn_ids = iter(("turn_0001", "turn_0002"))
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: next(turn_ids),
                clock=lambda: datetime(2026, 7, 27, 0, 4, tzinfo=timezone.utc),
            )

            first = harness.start_turn(game_id, "我打开牢门。")
            second = harness.start_turn(game_id, "我再次打开同一扇门。")

            self.assertEqual(first.status, "committed")
            self.assertEqual(second.status, "interrupted")
            session = store.load_session(game_id).session
            self.assertEqual(len(session["turns"]), 1)
            self.assertEqual(session["incomplete_turn"]["turn_id"], "turn_0002")

    def test_provider_failure_keeps_original_turn_and_blocks_new_action(self) -> None:
        """provider 失败保留原输入与稳定 turn_id，并阻塞覆盖性新行动。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    ModelCallError(
                        "request_timeout",
                        "provider detail must not reach player",
                        retryable=True,
                    )
                ]
            )
            allocated_turn_ids: list[str] = []

            def new_turn_id() -> str:
                value = f"turn_{len(allocated_turn_ids) + 1:04d}"
                allocated_turn_ids.append(value)
                return value

            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=new_turn_id,
                clock=lambda: datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我贴近门缝倾听。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.turn_id, "turn_0001")
            self.assertEqual(result.error_code, "request_timeout")
            self.assertNotIn("provider detail", result.error_message or "")
            interrupted = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(interrupted["turn_id"], "turn_0001")
            self.assertEqual(interrupted["player_input"], "我贴近门缝倾听。")
            self.assertEqual(interrupted["last_failure"]["code"], "request_timeout")

            with self.assertRaises(AgenticTurnBlockedError):
                harness.start_turn(game_id, "我改做另一件事。")
            self.assertEqual(allocated_turn_ids, ["turn_0001"])
            self.assertEqual(len(model.requests), 1)

    def test_interrupted_turn_is_discoverable_and_resumes_after_restart(self) -> None:
        """调用者只读发现中断后，可由新 Harness 显式恢复同一回合。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            interrupted_model = ScriptedGameMasterModel(
                [
                    ModelCallError(
                        "request_timeout",
                        "private provider detail",
                        retryable=True,
                    )
                ]
            )
            first_harness = AgenticHarness(
                store,
                interrupted_model,
                turn_id_factory=lambda: "turn_0001",
                clock=lambda: datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc),
            )
            interrupted = first_harness.start_turn(
                game_id,
                "我贴近门缝倾听。",
            )
            session_file = store.load_session(game_id).session_directory / "session.json"
            before_projection = session_file.read_bytes()

            lifecycle = first_harness.get_session_state(game_id)

            self.assertEqual(lifecycle.session_status, "ongoing")
            self.assertEqual(lifecycle.technical_status, "interrupted")
            self.assertTrue(lifecycle.has_incomplete_turn)
            self.assertEqual(lifecycle.turn_id, "turn_0001")
            self.assertEqual(lifecycle.error_code, "request_timeout")
            self.assertNotIn("private provider detail", lifecycle.error_message or "")
            self.assertEqual(lifecycle.public_mechanics, ())
            self.assertEqual(len(interrupted_model.requests), 1)
            self.assertEqual(session_file.read_bytes(), before_projection)

            allocated_turn_ids: list[str] = []
            resumed_model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "门外只有潮水拍击石墙的回声。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            restarted_harness = AgenticHarness(
                store,
                resumed_model,
                turn_id_factory=lambda: allocated_turn_ids.append("unexpected")
                or "turn_unexpected",
                clock=lambda: datetime(2026, 7, 27, 0, 6, tzinfo=timezone.utc),
            )

            resumed = restarted_harness.resume_turn(game_id, interrupted.turn_id)

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(resumed.turn_id, "turn_0001")
            self.assertEqual(allocated_turn_ids, [])
            self.assertEqual(len(resumed_model.requests), 1)
            committed = store.load_session(game_id).session
            self.assertIsNone(committed["incomplete_turn"])
            self.assertEqual(len(committed["turns"]), 1)
            self.assertEqual(committed["turns"][0]["turn_id"], "turn_0001")
            self.assertEqual(
                committed["turns"][0]["player_input"],
                "我贴近门缝倾听。",
            )

    def test_thinking_reasoning_round_trips_only_in_incomplete_recovery_state(
        self,
    ) -> None:
        canary_marker = "THINKING_RECOVERY_CANARY_10_EXACT"
        reasoning_canary = f"  {canary_marker}\nsecond line  "
        content_canary = "THINKING_TOOL_CONTENT_CANARY_10"
        profile = deepseek_model_profile(thinking=True)
        arguments = self._valid_check_arguments()
        tool_response = self._tool_response(
            arguments,
            tool_call_id="call_thinking_recovery",
            reasoning=reasoning_canary,
            content=content_canary,
        )

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            first_model = ScriptedGameMasterModel(
                [
                    tool_response,
                    ModelCallError(
                        "request_timeout",
                        "private provider detail",
                        retryable=True,
                    ),
                ]
            )
            first_harness = AgenticHarness(
                store,
                first_model,
                turn_id_factory=lambda: "turn_thinking_recovery",
                mechanic_id_factory=lambda: "mechanic_thinking_recovery",
                random_source=ScriptedRandom((3, 4)),
                model_profile=profile,
                clock=lambda: datetime(2026, 8, 7, 0, 10, tzinfo=timezone.utc),
            )

            interrupted = first_harness.start_turn(game_id, "我检查牢门。")
            lifecycle = first_harness.get_session_state(game_id)
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            assert incomplete is not None
            replay_prefix = incomplete["deepseek_messages"]

            self.assertEqual(interrupted.status, "interrupted")
            self.assertEqual(interrupted.error_code, "request_timeout")
            self.assertEqual(incomplete["model_profile"], profile)
            self.assertEqual(replay_prefix[-2], tool_response.assistant_message)
            self.assertEqual(
                replay_prefix[-2]["reasoning_content"],
                reasoning_canary,
            )
            self.assertEqual(replay_prefix[-2]["content"], content_canary)
            self.assertEqual(replay_prefix[-1]["role"], "tool")
            self.assertNotIn(canary_marker, repr(tool_response))
            self.assertNotIn(content_canary, repr(tool_response))
            self.assertNotIn(canary_marker, repr(first_harness))
            self.assertNotIn(content_canary, repr(first_harness))
            self.assertNotIn(canary_marker, repr(interrupted))
            self.assertNotIn(content_canary, repr(interrupted))
            self.assertNotIn(canary_marker, repr(lifecycle))
            self.assertNotIn(content_canary, repr(lifecycle))

            final_response = self._response(
                {
                    "narration": "牢门铰链附近留着一道新鲜刮痕。",
                    "establish": [],
                    "retire": [],
                    "session_status": "ongoing",
                },
                reasoning="THINKING_FINAL_CANARY_10",
            )
            resumed_model = ScriptedGameMasterModel([final_response])
            resumed_random = ScriptedRandom(())
            resumed_harness = AgenticHarness(
                store,
                resumed_model,
                random_source=resumed_random,
                model_profile=profile,
                clock=lambda: datetime(2026, 8, 7, 0, 11, tzinfo=timezone.utc),
            )

            resumed = resumed_harness.resume_turn(
                game_id,
                "turn_thinking_recovery",
            )

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(resumed_model.requests[0].messages, tuple(replay_prefix))
            self.assertEqual(resumed_model.requests[0].model_profile, profile)
            self.assertNotIn(canary_marker, repr(resumed_model.requests[0]))
            self.assertEqual(resumed_random.calls, [])
            committed = store.load_session(game_id).session
            self.assertIsNone(committed["incomplete_turn"])
            committed_json = json.dumps(committed, ensure_ascii=False, sort_keys=True)
            self.assertNotIn(canary_marker, committed_json)
            self.assertNotIn(content_canary, committed_json)
            self.assertNotIn("THINKING_FINAL_CANARY_10", committed_json)
            self.assertNotIn(canary_marker, repr(resumed))

    def test_thinking_direct_final_allows_nullable_reasoning_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "我先把手收回，观察锁扣的受力方向。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            result = AgenticHarness(
                store,
                model,
                model_profile=deepseek_model_profile(thinking=True),
                turn_id_factory=lambda: "turn_thinking_nullable_final",
                clock=lambda: datetime(2026, 8, 7, 0, 14, tzinfo=timezone.utc),
            ).start_turn(game_id, "我观察锁扣。")

            self.assertEqual(result.status, "committed")
            self.assertIsNone(store.load_session(game_id).session["incomplete_turn"])

    def test_thinking_response_without_reasoning_preserves_last_legal_prefix(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            random_source = ScriptedRandom(())
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        self._valid_check_arguments(),
                        tool_call_id="call_missing_reasoning",
                        reasoning=None,
                    )
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_missing_reasoning",
                mechanic_id_factory=lambda: "mechanic_must_not_exist",
                random_source=random_source,
                model_profile=deepseek_model_profile(thinking=True),
                clock=lambda: datetime(2026, 8, 7, 0, 12, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查牢门。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "provider_protocol_error")
            self.assertEqual(random_source.calls, [])
            self.assertEqual(len(model.requests), 1)
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            assert incomplete is not None
            self.assertEqual(len(incomplete["deepseek_messages"]), 3)
            self.assertEqual(incomplete["mechanics"], [])
            self.assertEqual(incomplete["tool_interactions"], [])
            self.assertEqual(len(incomplete["provider_protocol_errors"]), 1)
            self.assertEqual(
                incomplete["last_failure"]["code"],
                "provider_protocol_error",
            )

    def test_loader_rejects_thinking_replay_with_missing_reasoning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            self._valid_check_arguments(),
                            reasoning="THINKING_PERSISTENCE_CANARY_10",
                        ),
                        ModelCallError(
                            "request_timeout",
                            "stop after legal tool pair",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_thinking_tamper",
                mechanic_id_factory=lambda: "mechanic_thinking_tamper",
                random_source=ScriptedRandom((3, 4)),
                model_profile=deepseek_model_profile(thinking=True),
                clock=lambda: datetime(2026, 8, 7, 0, 13, tzinfo=timezone.utc),
            )
            harness.start_turn(game_id, "我检查牢门。")
            loaded = store.load_session(game_id)
            session_file = loaded.session_directory / "session.json"
            tampered = json.loads(session_file.read_text(encoding="utf-8"))
            tampered["incomplete_turn"]["deepseek_messages"][-2].pop(
                "reasoning_content"
            )
            write_json_atomic(session_file, tampered)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "deepseek_messages",
            ):
                store.load_session(game_id)

    def test_resume_replays_committed_tools_without_reroll_or_hidden_leak(self) -> None:
        """恢复沿用合法工具前缀，公开投影不泄露隐藏机械。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            public_arguments = {
                "actor_id": "investigator_tracker",
                "ability": "spot_hidden",
                "difficulty": "regular",
                "dice_adjustment": {"kind": "none", "count": 0},
                "action": "检查门锁上的新鲜刮痕",
                "stakes": "失败会错过守卫留下的痕迹",
                "visibility": "public",
            }
            hidden_arguments = {
                **public_arguments,
                "actor_id": "investigator_tracker",
                "action": "判断走廊远处的暗影是否正在靠近",
                "stakes": "失败会误判暗影的位置",
                "visibility": "hidden",
            }
            interrupted_model = ScriptedGameMasterModel(
                [
                    self._tool_response(public_arguments, tool_call_id="call_public"),
                    self._tool_response(hidden_arguments, tool_call_id="call_hidden"),
                    ModelCallError("request_timeout", "private", retryable=True),
                ]
            )
            mechanic_ids = iter(("mechanic_public", "mechanic_hidden"))
            first_random = ScriptedRandom((3, 4, 5, 6))
            first_harness = AgenticHarness(
                store,
                interrupted_model,
                turn_id_factory=lambda: "turn_0001",
                mechanic_id_factory=lambda: next(mechanic_ids),
                random_source=first_random,
                clock=lambda: datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc),
            )

            interrupted = first_harness.start_turn(game_id, "我仔细观察牢门。")
            lifecycle = first_harness.get_session_state(game_id)
            saved_incomplete = store.load_session(game_id).session["incomplete_turn"]
            replay_prefix = saved_incomplete["deepseek_messages"]

            self.assertEqual(interrupted.status, "interrupted")
            self.assertEqual(
                [item.mechanic_id for item in lifecycle.public_mechanics],
                ["mechanic_public"],
            )
            self.assertNotIn("mechanic_hidden", repr(lifecycle))
            self.assertNotIn("private", repr(lifecycle))
            self.assertEqual(len(saved_incomplete["mechanics"]), 2)

            resumed_model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "刮痕很新，远处的暗影却没有继续靠近。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            resumed_random = ScriptedRandom(())
            restarted_harness = AgenticHarness(
                store,
                resumed_model,
                random_source=resumed_random,
                clock=lambda: datetime(2026, 7, 27, 0, 6, tzinfo=timezone.utc),
            )

            resumed = restarted_harness.resume_turn(game_id, "turn_0001")

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(resumed_model.requests[0].messages, tuple(replay_prefix))
            self.assertEqual(resumed_random.calls, [])
            committed = store.load_session(game_id).session
            self.assertEqual(len(committed["turns"]), 1)
            self.assertEqual(
                [item["mechanic_id"] for item in committed["turns"][0]["mechanics"]],
                ["mechanic_public", "mechanic_hidden"],
            )

    def test_resume_replays_same_successful_tool_call_idempotently(self) -> None:
        """GM 重发同一成功调用时只复用已提交结果，不重复机械。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            actors_before = store.load_session(game_id).session["actors"]
            arguments = self._valid_check_arguments()
            first = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments, tool_call_id="call_replay"),
                        ModelCallError(
                            "request_timeout",
                            "stop after tool",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_replay_success",
                mechanic_id_factory=lambda: "mechanic_replay",
                random_source=ScriptedRandom((3, 4)),
                clock=lambda: datetime(2026, 7, 28, 0, 20, tzinfo=timezone.utc),
            )

            interrupted = first.start_turn(game_id, "我检查牢门。")
            self.assertEqual(interrupted.error_code, "request_timeout")
            saved = store.load_session(game_id).session["incomplete_turn"]
            assert saved is not None
            replay_prefix = saved["deepseek_messages"]

            replay_random = ScriptedRandom(())
            resumed_model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        json.dumps(arguments, ensure_ascii=False, indent=2),
                        tool_call_id="call_replay",
                    ),
                    ModelCallError(
                        "request_timeout",
                        "stop after replay",
                        retryable=True,
                    ),
                ]
            )
            published: list[PublicMechanic] = []
            allocated_mechanic_ids: list[str] = []

            def forbidden_mechanic_id() -> str:
                allocated_mechanic_ids.append("mechanic_forbidden")
                return "mechanic_forbidden"

            resumed_harness = AgenticHarness(
                store,
                resumed_model,
                mechanic_id_factory=forbidden_mechanic_id,
                random_source=replay_random,
                clock=lambda: datetime(2026, 7, 28, 0, 21, tzinfo=timezone.utc),
            )
            replayed = resumed_harness.resume_turn(
                game_id,
                "turn_replay_success",
                public_mechanic_sink=published.append,
            )

            self.assertEqual(replayed.error_code, "request_timeout")
            self.assertEqual(replayed.public_mechanics, ())
            self.assertEqual(published, [])
            self.assertEqual(replay_random.calls, [])
            self.assertEqual(allocated_mechanic_ids, [])
            self.assertEqual(len(resumed_model.requests), 2)
            self.assertEqual(resumed_model.requests[0].messages, tuple(replay_prefix))
            after_replay = store.load_session(game_id).session["incomplete_turn"]
            assert after_replay is not None
            self.assertEqual(len(after_replay["tool_interactions"]), 1)
            self.assertEqual(len(after_replay["mechanics"]), 1)
            self.assertEqual(len(after_replay["deepseek_messages"]), 7)
            self.assertEqual(
                self._tool_message_count(after_replay["deepseek_messages"]),
                2,
            )

            final_model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        json.dumps(arguments, ensure_ascii=False, indent=2),
                        tool_call_id="call_replay",
                    ),
                    self._response(
                        {
                            "narration": "门锁附近没有新的动静。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            final_harness = AgenticHarness(
                store,
                final_model,
                mechanic_id_factory=forbidden_mechanic_id,
                random_source=replay_random,
                clock=lambda: datetime(2026, 7, 28, 0, 22, tzinfo=timezone.utc),
            )
            resumed = final_harness.resume_turn(
                game_id,
                "turn_replay_success",
                public_mechanic_sink=published.append,
            )

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(resumed.public_mechanics, ())
            self.assertEqual(published, [])
            self.assertEqual(replay_random.calls, [])
            self.assertEqual(allocated_mechanic_ids, [])
            self.assertEqual(
                final_model.requests[0].messages,
                tuple(after_replay["deepseek_messages"]),
            )
            committed = store.load_session(game_id).session
            self.assertEqual(committed["actors"], actors_before)
            self.assertEqual(len(committed["turns"]), 1)
            self.assertEqual(
                committed["turns"][0]["mechanics"][0]["mechanic_id"],
                "mechanic_replay",
            )
            self.assertEqual(committed["turns"][0]["mechanics"][0]["roll"], 43)

            self.assertEqual(
                self._tool_message_count(final_model.requests[1].messages),
                3,
            )

    def test_resume_replays_same_failed_tool_call_idempotently(self) -> None:
        """GM 重发同一结构化失败时复用原错误，不生成第二条交互。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            arguments_raw = "{"
            first_model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        arguments_raw,
                        tool_call_id="call_failed",
                    ),
                    ModelCallError(
                        "request_timeout",
                        "stop after tool",
                        retryable=True,
                    ),
                ]
            )
            first = AgenticHarness(
                store,
                first_model,
                turn_id_factory=lambda: "turn_replay_failure",
                random_source=ScriptedRandom(()),
                clock=lambda: datetime(2026, 7, 28, 0, 22, tzinfo=timezone.utc),
            )
            first.start_turn(game_id, "我检查牢门。")

            resumed_model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        arguments_raw,
                        tool_call_id="call_failed",
                    ),
                    self._response(
                        {
                            "narration": "你暂时没有可靠的检定工具。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            resumed = AgenticHarness(
                store,
                resumed_model,
                random_source=ScriptedRandom(()),
                clock=lambda: datetime(2026, 7, 28, 0, 23, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_replay_failure")

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(
                self._tool_message_count(resumed_model.requests[1].messages),
                2,
            )
            committed = store.load_session(game_id).session
            self.assertEqual(len(committed["turns"]), 1)
            self.assertEqual(committed["turns"][0]["mechanics"], [])

    def test_resume_replays_multiple_tool_errors_without_new_interactions(self) -> None:
        """多工具协议错误重发时逐 ID 复用失败结果，不追加交互。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            first = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._multi_tool_response("call_a", "call_b"),
                        ModelCallError(
                            "request_timeout",
                            "stop after multiple-tool error",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_multiple_replay",
                random_source=ScriptedRandom(()),
                clock=lambda: datetime(2026, 7, 28, 0, 36, tzinfo=timezone.utc),
            )
            first.start_turn(game_id, "我检查牢门。")

            replay_model = ScriptedGameMasterModel(
                [
                    self._multi_tool_response("call_a", "call_b"),
                    ModelCallError(
                        "request_timeout",
                        "stop after replayed multiple-tool error",
                        retryable=True,
                    ),
                ]
            )
            replayed = AgenticHarness(
                store,
                replay_model,
                random_source=ScriptedRandom(()),
                clock=lambda: datetime(2026, 7, 28, 0, 37, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_multiple_replay")

            self.assertEqual(replayed.error_code, "request_timeout")
            after_replay = store.load_session(game_id).session["incomplete_turn"]
            assert after_replay is not None
            self.assertEqual(len(after_replay["tool_interactions"]), 2)
            self.assertEqual(len(after_replay["mechanics"]), 0)
            self.assertEqual(len(after_replay["deepseek_messages"]), 9)
            self.assertEqual(len(after_replay["provider_protocol_errors"]), 0)

            final_model = ScriptedGameMasterModel(
                [
                    self._multi_tool_response("call_a", "call_b"),
                    self._response(
                        {
                            "narration": "我改为一次只选择一个工具。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            result = AgenticHarness(
                store,
                final_model,
                random_source=ScriptedRandom(()),
                clock=lambda: datetime(2026, 7, 28, 0, 38, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_multiple_replay")

            self.assertEqual(result.status, "committed")
            self.assertEqual(result.public_mechanics, ())
            committed = store.load_session(game_id).session
            self.assertEqual(len(committed["turns"]), 1)
            self.assertEqual(committed["turns"][0]["mechanics"], [])
            self.assertEqual(
                self._tool_message_count(final_model.requests[1].messages),
                6,
            )

    def test_replay_mismatch_keeps_original_interaction_and_interrupts(self) -> None:
        """同 ID 异规范参数不能覆盖原结果或再次执行工具。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            arguments = self._valid_check_arguments()
            changed = {**arguments, "action": "改看走廊暗影"}
            first_random = ScriptedRandom((3, 4))
            first = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            arguments,
                            tool_call_id="call_mismatch",
                        ),
                        ModelCallError(
                            "request_timeout",
                            "stop after tool",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_replay_mismatch",
                mechanic_id_factory=lambda: "mechanic_mismatch",
                random_source=first_random,
                clock=lambda: datetime(2026, 7, 28, 0, 26, tzinfo=timezone.utc),
            )
            first.start_turn(game_id, "我检查牢门。")
            saved = store.load_session(game_id).session["incomplete_turn"]
            assert saved is not None
            original_messages = saved["deepseek_messages"]
            original_interaction = saved["tool_interactions"][0]

            replay_random = ScriptedRandom(())
            result = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            changed,
                            tool_call_id="call_mismatch",
                        )
                    ]
                ),
                random_source=replay_random,
                clock=lambda: datetime(2026, 7, 28, 0, 27, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_replay_mismatch")

            self.assertEqual(result.error_code, "provider_protocol_error")
            self.assertEqual(replay_random.calls, [])
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            assert incomplete is not None
            self.assertEqual(incomplete["deepseek_messages"], original_messages)
            self.assertEqual(
                incomplete["tool_interactions"],
                [original_interaction],
            )
            self.assertEqual(len(incomplete["mechanics"]), 1)
            self.assertEqual(len(incomplete["provider_protocol_errors"]), 1)

    def test_changed_raw_after_failed_normalization_interrupts(self) -> None:
        """规范化失败时只要 raw 改变就必须中断。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            first = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            "{",
                            tool_call_id="call_raw_changed",
                        ),
                        ModelCallError(
                            "request_timeout",
                            "stop after tool",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_raw_changed",
                random_source=ScriptedRandom(()),
                clock=lambda: datetime(2026, 7, 28, 0, 30, tzinfo=timezone.utc),
            )
            first.start_turn(game_id, "我检查牢门。")

            replay_random = ScriptedRandom(())
            result = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            "{ ",
                            tool_call_id="call_raw_changed",
                        )
                    ]
                ),
                random_source=replay_random,
                clock=lambda: datetime(2026, 7, 28, 0, 31, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_raw_changed")

            self.assertEqual(result.error_code, "provider_protocol_error")
            self.assertEqual(replay_random.calls, [])
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            assert incomplete is not None
            self.assertEqual(len(incomplete["tool_interactions"]), 1)
            self.assertEqual(len(incomplete["mechanics"]), 0)
            self.assertEqual(len(incomplete["provider_protocol_errors"]), 1)

    def test_same_provider_tool_id_is_independent_across_turns(self) -> None:
        """provider ID 只在当前 turn_id 内构成幂等键。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            arguments = self._valid_check_arguments()
            final = self._response(
                {
                    "narration": "检查已经完成。",
                    "establish": [],
                    "retire": [],
                    "session_status": "ongoing",
                }
            )
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(arguments, tool_call_id="call_reused"),
                    final,
                    self._tool_response(arguments, tool_call_id="call_reused"),
                    final,
                ]
            )
            turn_ids = iter(("turn_first", "turn_second"))
            mechanic_ids = iter(("mechanic_first", "mechanic_second"))
            random_source = ScriptedRandom((3, 4, 5, 6))
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: next(turn_ids),
                mechanic_id_factory=lambda: next(mechanic_ids),
                random_source=random_source,
                clock=lambda: datetime(2026, 7, 28, 0, 35, tzinfo=timezone.utc),
            )

            first = harness.start_turn(game_id, "我第一次检查牢门。")
            second = harness.start_turn(game_id, "我第二次检查牢门。")

            self.assertEqual(first.status, "committed")
            self.assertEqual(second.status, "committed")
            self.assertEqual(random_source.calls, [(0, 9)] * 4)
            turns = store.load_session(game_id).session["turns"]
            self.assertEqual(len(turns), 2)
            self.assertEqual(
                [turn["mechanics"][0]["mechanic_id"] for turn in turns],
                ["mechanic_first", "mechanic_second"],
            )
            self.assertEqual(
                [turn["mechanics"][0]["roll"] for turn in turns],
                [43, 65],
            )

    def test_repeated_resume_resets_attempt_budget_and_preserves_totals(self) -> None:
        """每次恢复获得冻结预算，同时保留同一回合的累计诊断。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            first_harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [ModelCallError("request_timeout", "private", retryable=True)]
                ),
                turn_id_factory=lambda: "turn_0001",
                clock=lambda: datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc),
            )
            first_harness.start_turn(game_id, "我继续倾听门外。")
            invalid_final = self._response(
                {
                    "narration": "不会提交的叙事。",
                    "establish": [],
                    "retire": [],
                    "session_status": [],
                }
            )
            second_model = ScriptedGameMasterModel([invalid_final, invalid_final])
            local_one_response_limits = {
                "max_round_trips": 1,
                "request_timeout_seconds": 1,
                "attempt_timeout_seconds": 1,
                "max_structure_repairs": 0,
            }
            second_harness = AgenticHarness(
                store,
                second_model,
                attempt_limits=local_one_response_limits,
                clock=lambda: datetime(2026, 7, 27, 0, 6, tzinfo=timezone.utc),
            )

            second = second_harness.resume_turn(game_id, "turn_0001")

            self.assertEqual(second.error_code, "invalid_final_response")
            self.assertEqual(len(second_model.requests), 2)
            after_second = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(after_second["turn_id"], "turn_0001")
            self.assertEqual(after_second["attempt_number"], 2)
            self.assertEqual(after_second["round_trips_used"], 2)
            self.assertEqual(after_second["total_round_trips"], 2)
            self.assertEqual(after_second["structure_repairs_used"], 1)
            self.assertEqual(after_second["total_structure_repairs"], 1)
            self.assertEqual(
                after_second["attempt_limits"],
                {
                    "max_round_trips": 8,
                    "request_timeout_seconds": 60,
                    "attempt_timeout_seconds": 180,
                    "max_structure_repairs": 1,
                },
            )

            third_model = ScriptedGameMasterModel(
                [ModelCallError("request_timeout", "private", retryable=True)]
            )
            third_harness = AgenticHarness(
                store,
                third_model,
                clock=lambda: datetime(2026, 7, 27, 0, 7, tzinfo=timezone.utc),
            )
            third = third_harness.resume_turn(game_id, "turn_0001")

            self.assertEqual(third.error_code, "request_timeout")
            after_third = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(after_third["turn_id"], "turn_0001")
            self.assertEqual(after_third["attempt_number"], 3)
            self.assertEqual(after_third["round_trips_used"], 0)
            self.assertEqual(after_third["total_round_trips"], 2)
            self.assertEqual(after_third["structure_repairs_used"], 0)
            self.assertEqual(after_third["total_structure_repairs"], 1)
            self.assertEqual(store.load_session(game_id).session["turns"], [])

    def test_resume_preserves_pending_structure_repair_phase(self) -> None:
        """恢复先完成已保存的无工具修正，并仍拥有本次修正预算。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            invalid_final = self._response(
                {
                    "narration": "不会提交的叙事。",
                    "establish": [],
                    "retire": [],
                    "session_status": [],
                }
            )
            first_harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        invalid_final,
                        ModelCallError("request_timeout", "private", retryable=True),
                    ]
                ),
                turn_id_factory=lambda: "turn_0001",
                clock=lambda: datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc),
            )
            interrupted = first_harness.start_turn(game_id, "我倾听门外。")
            saved = store.load_session(game_id).session["incomplete_turn"]
            replay_prefix = saved["deepseek_messages"]

            self.assertEqual(interrupted.error_code, "request_timeout")
            self.assertEqual(replay_prefix[-1]["role"], "user")
            self.assertIn("本地校验提示", replay_prefix[-1]["content"])

            resumed_model = ScriptedGameMasterModel(
                [
                    invalid_final,
                    self._response(
                        {
                            "narration": "门外只有潮水拍击石墙。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            resumed = AgenticHarness(
                store,
                resumed_model,
                clock=lambda: datetime(2026, 7, 27, 0, 6, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_0001")

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(len(resumed_model.requests), 2)
            self.assertEqual(resumed_model.requests[0].messages, tuple(replay_prefix))
            self.assertEqual(resumed_model.requests[0].tools, ())
            self.assertEqual(resumed_model.requests[1].tools, ())

    def test_structure_repair_budget_is_hard_capped_at_one(self) -> None:
        """即使配置误设更大，单次执行也不能连续修正两次。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            invalid_final = self._response(
                {
                    "narration": "不会提交的叙事。",
                    "establish": [],
                    "retire": [],
                    "session_status": [],
                }
            )
            model = ScriptedGameMasterModel(
                [invalid_final, invalid_final, invalid_final]
            )
            harness = AgenticHarness(
                store,
                model,
                attempt_limits={
                    "max_round_trips": 8,
                    "request_timeout_seconds": 60,
                    "attempt_timeout_seconds": 180,
                    "max_structure_repairs": 2,
                },
                turn_id_factory=lambda: "turn_0001",
                clock=lambda: datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我倾听门外。")

            self.assertEqual(result.error_code, "invalid_final_response")
            self.assertEqual(len(model.requests), 2)
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(incomplete["structure_repairs_used"], 1)
            self.assertEqual(incomplete["total_structure_repairs"], 1)

    def test_invalid_recovery_requests_fail_before_model_call_or_write(self) -> None:
        """恢复 ID 或冻结 profile 不可用时，不得改变原恢复记录。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, game_id = self._create_session(root)
            AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [ModelCallError("request_timeout", "private", retryable=True)]
                ),
                turn_id_factory=lambda: "turn_0001",
                clock=lambda: datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc),
            ).start_turn(game_id, "我贴近门缝倾听。")
            session_file = store.load_session(game_id).session_directory / "session.json"
            interrupted_bytes = session_file.read_bytes()

            wrong_id_model = ScriptedGameMasterModel(
                [self._response({"unreachable": True})]
            )
            wrong_id_harness = AgenticHarness(store, wrong_id_model)
            with self.assertRaisesRegex(
                AgenticTurnBlockedError,
                "指定回合当前不可恢复",
            ):
                wrong_id_harness.resume_turn(game_id, "turn_wrong")
            self.assertEqual(wrong_id_model.requests, [])
            self.assertEqual(session_file.read_bytes(), interrupted_bytes)

            wrong_profile_model = ScriptedGameMasterModel(
                [self._response({"unreachable": True})]
            )
            wrong_profile_harness = AgenticHarness(
                store,
                wrong_profile_model,
                model_profile=deepseek_model_profile(model_id="deepseek-v4-pro"),
            )
            with self.assertRaisesRegex(
                AgenticTurnBlockedError,
                "冻结的模型运行配置当前不可用",
            ):
                wrong_profile_harness.resume_turn(game_id, "turn_0001")
            self.assertEqual(wrong_profile_model.requests, [])
            self.assertEqual(session_file.read_bytes(), interrupted_bytes)

            fresh_store = AgenticSessionStore(
                session_root=root / "fresh-sessions",
                game_id_factory=lambda: "game_fresh_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            fresh = fresh_store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_tracker",
                    display_name="林雁",
                )
            )
            unavailable_model = ScriptedGameMasterModel(
                [self._response({"unreachable": True})]
            )
            fresh_harness = AgenticHarness(fresh_store, unavailable_model)
            ready = fresh_harness.get_session_state(fresh.game_id)
            self.assertEqual(ready.technical_status, "ready")
            self.assertFalse(ready.has_incomplete_turn)
            with self.assertRaisesRegex(
                AgenticTurnBlockedError,
                "指定回合当前不可恢复",
            ):
                fresh_harness.resume_turn(fresh.game_id, "turn_unknown")
            self.assertEqual(unavailable_model.requests, [])

            completed_model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "你登上引潮舟，驶离沉城。",
                            "establish": [],
                            "retire": [],
                            "session_status": "complete",
                        }
                    )
                ]
            )
            completed_harness = AgenticHarness(
                fresh_store,
                completed_model,
                turn_id_factory=lambda: "turn_complete",
            )
            completed_harness.start_turn(fresh.game_id, "我驶离港口。")
            completed = completed_harness.get_session_state(fresh.game_id)
            self.assertEqual(completed.session_status, "complete")
            self.assertEqual(completed.technical_status, "complete")
            self.assertFalse(completed.has_incomplete_turn)
            model_calls_before_resume = len(completed_model.requests)
            with self.assertRaisesRegex(
                AgenticTurnBlockedError,
                "指定回合当前不可恢复",
            ):
                completed_harness.resume_turn(fresh.game_id, "turn_complete")
            self.assertEqual(len(completed_model.requests), model_calls_before_resume)

    def test_provider_failure_code_is_allowlisted_before_persistence_or_output(self) -> None:
        """provider 失败码不是玩家输出协议，未知值必须收敛为稳定码。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    ModelCallError(
                        "provider-secret\nforged-output",
                        "provider message must remain private",
                        retryable=False,
                    )
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_0001",
                clock=lambda: datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我先观察牢门。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "provider_error")
            self.assertNotIn("provider-secret", result.error_message or "")
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(incomplete["last_failure"]["code"], "provider_error")

    def test_make_check_resolves_coc_difficulty_dice_and_success_boundaries(
        self,
    ) -> None:
        """独立骰例覆盖原生目标、奖励惩罚选择及六档 COC 成功等级。"""

        cases = (
            ("critical", "spot_hidden", "regular", "none", 0, (1, 0), 70, 1, "critical_success"),
            ("extreme boundary", "spot_hidden", "extreme", "none", 0, (4, 1), 14, 14, "extreme_success"),
            ("hard boundary", "spot_hidden", "hard", "none", 0, (5, 3), 35, 35, "hard_success"),
            ("regular boundary", "spot_hidden", "regular", "none", 0, (0, 7), 70, 70, "regular_success"),
            ("ordinary failure", "spot_hidden", "regular", "none", 0, (1, 7), 70, 71, "failure"),
            ("high ability fumble", "spot_hidden", "regular", "none", 0, (0, 0), 70, 100, "fumble"),
            ("low ability fumble", "locksmith", "regular", "none", 0, (6, 9), 1, 96, "fumble"),
            ("attribute hard target", "strength", "hard", "none", 0, (0, 2), 20, 20, "hard_success"),
            ("bonus chooses lower", "spot_hidden", "regular", "bonus", 1, (7, 8, 2), 70, 27, "hard_success"),
            ("penalty chooses higher", "spot_hidden", "regular", "penalty", 1, (7, 8, 2), 70, 87, "failure"),
            ("bonus treats zero zero as 100", "spot_hidden", "regular", "bonus", 1, (0, 0, 5), 70, 50, "regular_success"),
            ("penalty treats zero zero as 100", "spot_hidden", "regular", "penalty", 1, (0, 0, 5), 70, 100, "fumble"),
            ("two bonus dice", "spot_hidden", "regular", "bonus", 2, (9, 8, 4, 1), 70, 19, "hard_success"),
        )
        for (
            label,
            ability,
            difficulty,
            kind,
            count,
            dice,
            target,
            roll,
            success_level,
        ) in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                arguments = {
                    "actor_id": "investigator_tracker",
                    "ability": ability,
                    "difficulty": difficulty,
                    "dice_adjustment": {"kind": kind, "count": count},
                    "action": "执行边界检定",
                    "stakes": "失败会产生明确代价",
                    "visibility": "public",
                }
                model = ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments),
                        self._response(
                            {
                                "narration": "检定已经得到解释。",
                                "establish": [],
                                "retire": [],
                                "session_status": "ongoing",
                            }
                        ),
                    ]
                )
                random_source = ScriptedRandom(dice)
                harness = AgenticHarness(
                    store,
                    model,
                    turn_id_factory=lambda: "turn_0001",
                    mechanic_id_factory=lambda: "mechanic_0001",
                    random_source=random_source,
                    clock=lambda: datetime(
                        2026,
                        7,
                        28,
                        0,
                        2,
                        tzinfo=timezone.utc,
                    ),
                )

                result = harness.start_turn(game_id, "我执行这个行动。")

                public = result.public_mechanics[0]
                self.assertEqual(public.target, target)
                self.assertEqual(public.roll, roll)
                self.assertEqual(public.success_level, success_level)
                self.assertEqual(
                    random_source.calls,
                    [(0, 9)] * (count + 2),
                )

    def test_invalid_make_check_is_persisted_before_same_gm_continues(self) -> None:
        """可关联的工具错误不掷骰，并以完整交互反馈同一个 GM。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            response = ModelResponse(
                assistant_message={
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "受限 provider 材料",
                    "tool_calls": [
                        {
                            "id": "call_001",
                            "type": "function",
                            "function": {
                                "name": "make_check",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                finish_reason="tool_calls",
                usage=None,
                latency_ms=11,
            )
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        response,
                        ModelCallError(
                            "request_timeout",
                            "stop after persisted tool error",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_0001",
                clock=lambda: datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我试着撬开牢门。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "request_timeout")
            self.assertIsNone(result.narration)
            session = store.load_session(game_id).session
            incomplete = session["incomplete_turn"]
            self.assertEqual(len(incomplete["deepseek_messages"]), 5)
            self.assertEqual(incomplete["provider_protocol_errors"], [])
            self.assertEqual(incomplete["mechanics"], [])
            interaction = incomplete["tool_interactions"][0]
            self.assertFalse(interaction["ok"])
            self.assertEqual(interaction["arguments_raw"], "{}")
            self.assertIsNone(interaction["arguments"])
            self.assertEqual(interaction["error"]["code"], "invalid_arguments")
            tool_result = json.loads(incomplete["deepseek_messages"][-1]["content"])
            self.assertFalse(tool_result["ok"])
            self.assertEqual(tool_result["tool_call_id"], "call_001")
            self.assertNotIn(
                "受限 provider 材料",
                result.error_message or "",
            )

    def test_invalid_make_check_inputs_never_draw_or_allocate_mechanic(self) -> None:
        """schema、领域与工具名反例都在 RNG 和 mechanic_id 之前被拒绝。"""

        valid = {
            "actor_id": "investigator_tracker",
            "ability": "spot_hidden",
            "difficulty": "regular",
            "dice_adjustment": {"kind": "none", "count": 0},
            "action": "检查牢门",
            "stakes": "失败会错过痕迹",
            "visibility": "public",
        }
        cases = (
            ("unknown actor", {**valid, "actor_id": "npc_missing"}, "make_check", "unknown_actor", True),
            ("unknown ability", {**valid, "ability": "locksmithing"}, "make_check", "unknown_ability", True),
            ("forged target", {**valid, "target": 99}, "make_check", "invalid_arguments", False),
            ("bad difficulty", {**valid, "difficulty": []}, "make_check", "invalid_difficulty", False),
            ("bad adjustment", {**valid, "dice_adjustment": {"kind": "none", "count": 1}}, "make_check", "invalid_dice_adjustment", False),
            ("missing visibility", {key: value for key, value in valid.items() if key != "visibility"}, "make_check", "invalid_arguments", False),
            ("unknown tool", valid, "apply_effect", "unknown_tool", False),
        )
        for label, arguments, tool_name, code, normalized in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                model = ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments, name=tool_name),
                        ModelCallError(
                            "request_timeout",
                            "stop after error",
                            retryable=True,
                        ),
                    ]
                )
                random_source = ScriptedRandom(())
                allocated_mechanic_ids: list[str] = []

                def forbidden_mechanic_id(
                    allocated: list[str] = allocated_mechanic_ids,
                ) -> str:
                    allocated.append("mechanic_forbidden")
                    return "mechanic_forbidden"

                harness = AgenticHarness(
                    store,
                    model,
                    turn_id_factory=lambda: "turn_0001",
                    mechanic_id_factory=forbidden_mechanic_id,
                    random_source=random_source,
                    clock=lambda: datetime(2026, 7, 28, 0, 3, tzinfo=timezone.utc),
                )

                result = harness.start_turn(game_id, "我尝试行动。")

                self.assertEqual(result.error_code, "request_timeout")
                self.assertEqual(random_source.calls, [])
                self.assertEqual(allocated_mechanic_ids, [])
                incomplete = store.load_session(game_id).session["incomplete_turn"]
                self.assertEqual(incomplete["mechanics"], [])
                interaction = incomplete["tool_interactions"][0]
                self.assertEqual(interaction["error"]["code"], code)
                self.assertEqual(interaction["arguments"] is not None, normalized)

    def test_unusable_tool_call_id_interrupts_without_synthetic_protocol_pair(self) -> None:
        """无法可靠关联的 ID 只保留受限原始 envelope 并停止。"""

        for unusable_id in (None, "", " call_001", "call_001 "):
            with self.subTest(tool_call_id=unusable_id), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                model = ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            {
                                "actor_id": "investigator_tracker",
                                "ability": "spot_hidden",
                                "difficulty": "regular",
                                "dice_adjustment": {"kind": "none", "count": 0},
                                "action": "检查牢门",
                                "stakes": "失败会错过痕迹",
                                "visibility": "public",
                            },
                            tool_call_id=unusable_id,
                            reasoning="受限协议材料",
                        )
                    ]
                )
                random_source = ScriptedRandom(())
                harness = AgenticHarness(
                    store,
                    model,
                    turn_id_factory=lambda: "turn_0001",
                    random_source=random_source,
                    clock=lambda: datetime(2026, 7, 28, 0, 4, tzinfo=timezone.utc),
                )

                result = harness.start_turn(game_id, "我检查牢门。")

                self.assertEqual(result.error_code, "provider_protocol_error")
                self.assertNotIn("受限协议材料", result.error_message or "")
                incomplete = store.load_session(game_id).session["incomplete_turn"]
                self.assertEqual(incomplete["mechanics"], [])
                self.assertEqual(incomplete["tool_interactions"], [])
                self.assertEqual(len(incomplete["deepseek_messages"]), 3)
                self.assertEqual(len(incomplete["provider_protocol_errors"]), 1)
                self.assertIn(
                    "受限协议材料",
                    incomplete["provider_protocol_errors"][0]["model_response_json"],
                )
                self.assertEqual(random_source.calls, [])
                self.assertEqual(len(model.requests), 1)

    def test_resume_omits_unpairable_provider_response_from_replay_prefix(self) -> None:
        """不可配对响应只留在受限诊断，恢复请求仅发送最后合法前缀。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            first_harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            {
                                "actor_id": "investigator_tracker",
                                "ability": "spot_hidden",
                                "difficulty": "regular",
                                "dice_adjustment": {"kind": "none", "count": 0},
                                "action": "检查牢门",
                                "stakes": "失败会错过痕迹",
                                "visibility": "public",
                            },
                            tool_call_id=None,
                            reasoning="受限协议材料",
                        )
                    ]
                ),
                turn_id_factory=lambda: "turn_0001",
                random_source=ScriptedRandom(()),
                clock=lambda: datetime(2026, 7, 28, 0, 4, tzinfo=timezone.utc),
            )
            first_harness.start_turn(game_id, "我检查牢门。")
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            replay_prefix = incomplete["deepseek_messages"]
            self.assertIn(
                "受限协议材料",
                incomplete["provider_protocol_errors"][0]["model_response_json"],
            )

            resumed_model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "牢门没有显出更多痕迹。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            resumed = AgenticHarness(
                store,
                resumed_model,
                clock=lambda: datetime(2026, 7, 28, 0, 5, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_0001")

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(resumed_model.requests[0].messages, tuple(replay_prefix))
            self.assertNotIn(
                "受限协议材料",
                json.dumps(
                    resumed_model.requests[0].messages,
                    ensure_ascii=False,
                ),
            )

    def test_tool_write_failure_reports_no_mechanic_and_stops_same_gm(self) -> None:
        """工具事务写失败时不向 GM 或玩家报告未提交的随机结果。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        {
                            "actor_id": "investigator_tracker",
                            "ability": "spot_hidden",
                            "difficulty": "regular",
                            "dice_adjustment": {"kind": "none", "count": 0},
                            "action": "检查牢门",
                            "stakes": "失败会错过痕迹",
                            "visibility": "public",
                        }
                    )
                ]
            )
            writes = 0

            def fail_tool_write(path: Path, value: object) -> None:
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("injected tool write failure")
                write_json_atomic(path, value)

            random_source = ScriptedRandom((3, 4))
            published: list[PublicMechanic] = []
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_0001",
                mechanic_id_factory=lambda: "mechanic_0001",
                random_source=random_source,
                session_writer=fail_tool_write,
                clock=lambda: datetime(2026, 7, 28, 0, 5, tzinfo=timezone.utc),
            )

            result = harness.start_turn(
                game_id,
                "我检查牢门。",
                public_mechanic_sink=published.append,
            )

            self.assertEqual(result.error_code, "tool_commit_failed")
            self.assertEqual(result.public_mechanics, ())
            self.assertEqual(published, [])
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(incomplete["mechanics"], [])
            self.assertEqual(incomplete["tool_interactions"], [])
            self.assertEqual(len(incomplete["deepseek_messages"]), 3)
            self.assertEqual(len(incomplete["provider_protocol_errors"]), 1)
            self.assertEqual(random_source.calls, [(0, 9), (0, 9)])
            self.assertEqual(len(model.requests), 1)

    def test_restart_after_tool_write_failure_executes_then_replays_exactly_once(
        self,
    ) -> None:
        """写失败不留幂等映射，重启后提交一次且后续重放不再执行。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            arguments = self._valid_check_arguments()
            writes = 0

            def fail_first_tool_write(path: Path, value: object) -> None:
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("injected tool write failure")
                write_json_atomic(path, value)

            first_random = ScriptedRandom((3, 4))
            first = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [self._tool_response(arguments, tool_call_id="call_atomic")]
                ),
                turn_id_factory=lambda: "turn_atomic_replay",
                mechanic_id_factory=lambda: "mechanic_uncommitted",
                random_source=first_random,
                session_writer=fail_first_tool_write,
                clock=lambda: datetime(2026, 7, 28, 0, 32, tzinfo=timezone.utc),
            )

            failed = first.start_turn(game_id, "我检查牢门。")

            self.assertEqual(failed.error_code, "tool_commit_failed")
            after_failure = store.load_session(game_id).session["incomplete_turn"]
            assert after_failure is not None
            self.assertEqual(after_failure["mechanics"], [])
            self.assertEqual(after_failure["tool_interactions"], [])
            self.assertEqual(len(after_failure["deepseek_messages"]), 3)
            self.assertEqual(first_random.calls, [(0, 9), (0, 9)])

            committed_random = ScriptedRandom((5, 6))
            second = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments, tool_call_id="call_atomic"),
                        ModelCallError(
                            "request_timeout",
                            "stop after committed retry",
                            retryable=True,
                        ),
                    ]
                ),
                mechanic_id_factory=lambda: "mechanic_committed",
                random_source=committed_random,
                clock=lambda: datetime(2026, 7, 28, 0, 33, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_atomic_replay")

            self.assertEqual(second.error_code, "request_timeout")
            self.assertEqual(committed_random.calls, [(0, 9), (0, 9)])
            after_commit = store.load_session(game_id).session["incomplete_turn"]
            assert after_commit is not None
            self.assertEqual(len(after_commit["mechanics"]), 1)
            self.assertEqual(len(after_commit["tool_interactions"]), 1)
            self.assertEqual(
                after_commit["mechanics"][0]["mechanic_id"],
                "mechanic_committed",
            )
            self.assertEqual(after_commit["mechanics"][0]["roll"], 65)

            replay_random = ScriptedRandom(())
            third = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments, tool_call_id="call_atomic"),
                        self._response(
                            {
                                "narration": "牢门仍然紧闭。",
                                "establish": [],
                                "retire": [],
                                "session_status": "ongoing",
                            }
                        ),
                    ]
                ),
                random_source=replay_random,
                clock=lambda: datetime(2026, 7, 28, 0, 34, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_atomic_replay")

            self.assertEqual(third.status, "committed")
            self.assertEqual(replay_random.calls, [])
            session = store.load_session(game_id).session
            self.assertEqual(len(session["turns"]), 1)
            self.assertEqual(len(session["turns"][0]["mechanics"]), 1)
            self.assertEqual(
                session["turns"][0]["mechanics"][0]["mechanic_id"],
                "mechanic_committed",
            )
            self.assertEqual(session["turns"][0]["mechanics"][0]["roll"], 65)

    def test_committed_check_survives_provider_and_final_failures_exactly_once(self) -> None:
        """工具提交后的 provider/final 故障不回滚、不重掷也不重复机械。"""

        valid_arguments = {
            "actor_id": "investigator_tracker",
            "ability": "spot_hidden",
            "difficulty": "regular",
            "dice_adjustment": {"kind": "none", "count": 0},
            "action": "检查牢门",
            "stakes": "失败会错过痕迹",
            "visibility": "public",
        }
        later_failures = (
            (
                "provider",
                ModelCallError("request_timeout", "private", retryable=True),
                "request_timeout",
                None,
            ),
            (
                "invalid final",
                self._response(
                    {
                        "narration": "不能提交",
                        "establish": [],
                        "retire": [],
                        "session_status": [],
                    }
                ),
                "invalid_final_response",
                None,
            ),
            (
                "final write",
                self._response(
                    {
                        "narration": "不能写入",
                        "establish": [],
                        "retire": [],
                        "session_status": "ongoing",
                    }
                ),
                "final_commit_failed",
                3,
            ),
        )
        for label, later_step, error_code, failed_write in later_failures:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                later_steps = [later_step]
                if error_code == "invalid_final_response":
                    later_steps.append(later_step)
                model = ScriptedGameMasterModel(
                    [self._tool_response(valid_arguments), *later_steps]
                )
                writes = 0

                def controlled_writer(
                    path: Path,
                    value: object,
                    fail_at: int | None = failed_write,
                ) -> None:
                    nonlocal writes
                    writes += 1
                    if fail_at is not None and writes == fail_at:
                        raise OSError("injected final write failure")
                    write_json_atomic(path, value)

                random_source = ScriptedRandom((3, 4))
                harness = AgenticHarness(
                    store,
                    model,
                    turn_id_factory=lambda: "turn_0001",
                    mechanic_id_factory=lambda: "mechanic_0001",
                    random_source=random_source,
                    session_writer=controlled_writer,
                    clock=lambda: datetime(2026, 7, 28, 0, 6, tzinfo=timezone.utc),
                )

                result = harness.start_turn(game_id, "我检查牢门。")

                self.assertEqual(result.error_code, error_code)
                self.assertEqual(len(result.public_mechanics), 1)
                incomplete = store.load_session(game_id).session["incomplete_turn"]
                self.assertEqual(
                    [item["mechanic_id"] for item in incomplete["mechanics"]],
                    ["mechanic_0001"],
                )
                self.assertEqual(len(incomplete["tool_interactions"]), 1)
                self.assertEqual(random_source.calls, [(0, 9), (0, 9)])

    def test_load_rejects_successful_check_without_normalized_arguments(self) -> None:
        """成功交互不能丢失用于幂等与机械追溯的规范化事前参数。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            arguments = {
                "actor_id": "investigator_tracker",
                "ability": "spot_hidden",
                "difficulty": "regular",
                "dice_adjustment": {"kind": "none", "count": 0},
                "action": "检查牢门",
                "stakes": "失败会错过痕迹",
                "visibility": "public",
            }
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments),
                        ModelCallError(
                            "request_timeout",
                            "private provider detail",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_0001",
                mechanic_id_factory=lambda: "mechanic_0001",
                random_source=ScriptedRandom((3, 4)),
                clock=lambda: datetime(2026, 7, 28, 0, 6, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查牢门。")
            self.assertEqual(result.error_code, "request_timeout")
            loaded = store.load_session(game_id).session
            self.assertEqual(
                loaded["incomplete_turn"]["tool_interactions"][0]["arguments"],
                arguments,
            )

            tampered = read_json(
                store.session_root / game_id / "session.json"
            )
            tampered["incomplete_turn"]["tool_interactions"][0]["arguments"] = None
            write_json_atomic(store.session_root / game_id / "session.json", tampered)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "ToolInteraction 格式无效",
            ):
                store.load_session(game_id)

    def test_load_rejects_successful_check_with_arguments_changed_after_roll(
        self,
    ) -> None:
        """协议材料彼此自洽时也不能改写已经冻结并结算的事前参数。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            arguments = {
                "actor_id": "investigator_tracker",
                "ability": "spot_hidden",
                "difficulty": "regular",
                "dice_adjustment": {"kind": "none", "count": 0},
                "action": "检查牢门",
                "stakes": "失败会错过痕迹",
                "visibility": "public",
            }
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments),
                        ModelCallError(
                            "request_timeout",
                            "private provider detail",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_0001",
                mechanic_id_factory=lambda: "mechanic_0001",
                random_source=ScriptedRandom((3, 4)),
                clock=lambda: datetime(2026, 7, 28, 0, 6, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查牢门。")
            self.assertEqual(result.error_code, "request_timeout")
            tampered = read_json(store.session_root / game_id / "session.json")
            interaction = tampered["incomplete_turn"]["tool_interactions"][0]
            interaction["arguments"]["action"] = "骰后改写的行动"
            changed_raw = json.dumps(
                interaction["arguments"],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            interaction["arguments_raw"] = changed_raw
            assistant_message = next(
                message
                for message in tampered["incomplete_turn"]["deepseek_messages"]
                if message.get("role") == "assistant"
                and message.get("tool_calls")
            )
            assistant_message["tool_calls"][0]["function"]["arguments"] = (
                changed_raw
            )
            write_json_atomic(store.session_root / game_id / "session.json", tampered)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "ToolInteraction 格式无效",
            ):
                store.load_session(game_id)

    def test_load_rejects_boolean_check_target(self) -> None:
        """JSON 布尔值不能冒充能力值为 1 的可信检定目标。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            {
                                "actor_id": "investigator_tracker",
                                "ability": "locksmith",
                                "difficulty": "regular",
                                "dice_adjustment": {"kind": "none", "count": 0},
                                "action": "尝试拨动锁芯",
                                "stakes": "失败会制造金属声",
                                "visibility": "public",
                            }
                        ),
                        self._response(
                            {
                                "narration": "锁芯没有转动。",
                                "establish": [],
                                "retire": [],
                                "session_status": "ongoing",
                            }
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_0001",
                mechanic_id_factory=lambda: "mechanic_0001",
                random_source=ScriptedRandom((2, 2)),
                clock=lambda: datetime(2026, 7, 28, 0, 7, tzinfo=timezone.utc),
            )
            result = harness.start_turn(game_id, "我用发卡试着撬锁。")
            self.assertEqual(result.status, "committed")

            session_file = store.session_root / game_id / "session.json"
            tampered = read_json(session_file)
            tampered["turns"][0]["mechanics"][0]["target"] = True
            write_json_atomic(session_file, tampered)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "CommittedTurn 格式无效",
            ):
                store.load_session(game_id)

    def test_load_rejects_boolean_in_normalized_failed_tool_arguments(
        self,
    ) -> None:
        """失败交互的规范参数也必须保留 JSON 整数类型。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            arguments = {
                "actor_id": "npc_missing",
                "ability": "spot_hidden",
                "difficulty": "regular",
                "dice_adjustment": {"kind": "bonus", "count": 1},
                "action": "检查牢门",
                "stakes": "失败会错过痕迹",
                "visibility": "public",
            }
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments),
                        ModelCallError(
                            "request_timeout",
                            "stop after persisted tool error",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_0001",
                random_source=ScriptedRandom(()),
                clock=lambda: datetime(2026, 7, 28, 0, 7, tzinfo=timezone.utc),
            )
            result = harness.start_turn(game_id, "我检查牢门。")
            self.assertEqual(result.error_code, "request_timeout")

            session_file = store.session_root / game_id / "session.json"
            tampered = read_json(session_file)
            tampered["incomplete_turn"]["tool_interactions"][0]["arguments"][
                "dice_adjustment"
            ]["count"] = True
            write_json_atomic(session_file, tampered)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "ToolInteraction 格式无效",
            ):
                store.load_session(game_id)

    def test_hidden_check_is_persisted_for_gm_without_public_projection(self) -> None:
        """隐藏检定完整进入恢复材料与 GM tool result，但不产生玩家事件。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            secret_action = "守卫暗中判断是否听见排水沟的声音"
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        {
                            "actor_id": "investigator_tracker",
                            "ability": "listen",
                            "difficulty": "hard",
                            "dice_adjustment": {"kind": "penalty", "count": 1},
                            "action": secret_action,
                            "stakes": "失败意味着守卫尚未察觉",
                            "visibility": "hidden",
                        }
                    ),
                    ModelCallError(
                        "request_timeout",
                        "private provider detail",
                        retryable=True,
                    ),
                ]
            )
            published: list[PublicMechanic] = []
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_0001",
                mechanic_id_factory=lambda: "mechanic_0001",
                random_source=ScriptedRandom((2, 1, 8)),
                clock=lambda: datetime(2026, 7, 28, 0, 7, tzinfo=timezone.utc),
            )

            result = harness.start_turn(
                game_id,
                "我悄悄拨动排水沟盖。",
                public_mechanic_sink=published.append,
            )
            lifecycle = harness.get_session_state(game_id)

            self.assertEqual(result.error_code, "request_timeout")
            self.assertEqual(result.public_mechanics, ())
            self.assertEqual(published, [])
            self.assertEqual(lifecycle.public_mechanics, ())
            self.assertNotIn(secret_action, repr(lifecycle))
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            mechanic = incomplete["mechanics"][0]
            self.assertEqual(mechanic["visibility"], "hidden")
            self.assertEqual(mechanic["roll"], 82)
            self.assertIn(secret_action, model.requests[1].messages[-1]["content"])
            self.assertNotIn(secret_action, result.error_message or "")

    def test_loader_rejects_mechanic_detached_from_frozen_actor_sheet(self) -> None:
        """交互内部即使自洽，也不能把冻结角色能力值改成另一目标。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        {
                            "actor_id": "investigator_tracker",
                            "ability": "spot_hidden",
                            "difficulty": "regular",
                            "dice_adjustment": {"kind": "none", "count": 0},
                            "action": "检查牢门",
                            "stakes": "失败会错过痕迹",
                            "visibility": "public",
                        }
                    ),
                    ModelCallError(
                        "request_timeout",
                        "stop with incomplete turn",
                        retryable=True,
                    ),
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_0001",
                mechanic_id_factory=lambda: "mechanic_0001",
                random_source=ScriptedRandom((3, 4)),
                clock=lambda: datetime(2026, 7, 28, 0, 8, tzinfo=timezone.utc),
            )
            harness.start_turn(game_id, "我检查牢门。")
            session_file = store.load_session(game_id).session_directory / "session.json"
            tampered = read_json(session_file)
            incomplete = tampered["incomplete_turn"]
            mechanic = incomplete["mechanics"][0]
            interaction_result = incomplete["tool_interactions"][0]["result"]
            envelope = json.loads(incomplete["deepseek_messages"][-1]["content"])
            envelope_result = envelope["result"]
            for result in (mechanic, interaction_result, envelope_result):
                result["ability_value"] = 99
                result["target"] = 99
                result["success_level"] = "hard_success"
            incomplete["deepseek_messages"][-1]["content"] = json.dumps(
                envelope,
                ensure_ascii=False,
                sort_keys=True,
            )
            write_json_atomic(session_file, tampered)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "机械与冻结角色卡不一致",
            ):
                store.load_session(game_id)

    def test_final_write_failure_recovers_to_one_committed_turn(self) -> None:
        """最终写失败不留部分事实，恢复后只提交一份回合和事实。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "这段叙事不能被提交。",
                            "establish": [
                                {
                                    "visibility": "public",
                                    "text": "这条事实不能被提交。",
                                }
                            ],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            writes = 0

            def fail_final_write(path: Path, value: object) -> None:
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("injected final write failure")
                write_json_atomic(path, value)

            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_0001",
                fact_id_factory=lambda: "fact_1001",
                clock=lambda: datetime(2026, 7, 27, 0, 6, tzinfo=timezone.utc),
                session_writer=fail_final_write,
            )

            result = harness.start_turn(game_id, "我敲击墙壁。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "final_commit_failed")
            session = store.load_session(game_id).session
            self.assertEqual(session["turns"], [])
            self.assertFalse(
                any(fact["fact_id"] == "fact_1001" for fact in session["facts"])
            )
            self.assertEqual(session["incomplete_turn"]["turn_id"], "turn_0001")
            self.assertEqual(
                session["incomplete_turn"]["last_failure"]["code"],
                "final_commit_failed",
            )

            resumed_model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "敲击声表明墙后存在空腔。",
                            "establish": [
                                {
                                    "visibility": "public",
                                    "text": "牢房墙后存在空腔。",
                                }
                            ],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            resumed = AgenticHarness(
                store,
                resumed_model,
                fact_id_factory=lambda: "fact_1001",
                clock=lambda: datetime(2026, 7, 27, 0, 7, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_0001")

            self.assertEqual(resumed.status, "committed")
            committed = store.load_session(game_id).session
            self.assertIsNone(committed["incomplete_turn"])
            self.assertEqual(len(committed["turns"]), 1)
            self.assertEqual(committed["turns"][0]["turn_id"], "turn_0001")
            recovered_facts = [
                fact for fact in committed["facts"] if fact["fact_id"] == "fact_1001"
            ]
            self.assertEqual(len(recovered_facts), 1)
            self.assertEqual(recovered_facts[0]["text"], "牢房墙后存在空腔。")

    def test_repeated_injected_final_write_failure_still_returns_interruption(self) -> None:
        """写入器持续失败时，Harness 仍用原子恢复写回稳定中断而非抛异常。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "这段叙事不能被提交。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            writes = 0

            def fail_after_initial_write(path: Path, value: object) -> None:
                nonlocal writes
                writes += 1
                if writes > 1:
                    raise OSError("injected repeated write failure")
                write_json_atomic(path, value)

            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_0001",
                clock=lambda: datetime(2026, 7, 27, 0, 6, tzinfo=timezone.utc),
                session_writer=fail_after_initial_write,
            )

            result = harness.start_turn(game_id, "我敲击墙壁。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "final_commit_failed")
            session = store.load_session(game_id).session
            self.assertEqual(session["turns"], [])
            self.assertEqual(
                session["incomplete_turn"]["last_failure"]["code"],
                "final_commit_failed",
            )

    def test_complete_session_rejects_input_before_id_or_model_call(self) -> None:
        """本局收束后，新输入不能分配回合、改变聚合或产生模型费用。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "你们登上引潮舟，塔纳里昂沉入雾后。",
                            "establish": [],
                            "retire": [],
                            "session_status": "complete",
                        }
                    )
                ]
            )
            allocated_turn_ids: list[str] = []

            def new_turn_id() -> str:
                value = f"turn_{len(allocated_turn_ids) + 1:04d}"
                allocated_turn_ids.append(value)
                return value

            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=new_turn_id,
                clock=lambda: datetime(2026, 7, 27, 0, 7, tzinfo=timezone.utc),
            )
            committed = harness.start_turn(game_id, "我解开缆绳，驶离港口。")
            before = read_json(
                store.load_session(game_id).session_directory / "session.json"
            )

            with self.assertRaises(AgenticSessionCompleteError):
                harness.start_turn(game_id, "我还要返回城中。")

            after = read_json(
                store.load_session(game_id).session_directory / "session.json"
            )
            self.assertEqual(committed.status, "committed")
            self.assertEqual(before, after)
            self.assertEqual(allocated_turn_ids, ["turn_0001"])
            self.assertEqual(len(model.requests), 1)


if __name__ == "__main__":
    unittest.main()
