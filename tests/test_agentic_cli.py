import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from monmusu_agent.agent import GameMasterAgent
from monmusu_agent.agentic_cli import (
    CliInputEncodingError,
    NewSessionCliResult,
    main,
    run_game_cli,
    run_new_session_cli,
    run_turn_cli,
)
from monmusu_agent.agentic_harness import AgenticHarness
from monmusu_agent.agentic_model import (
    GameMasterModel,
    ModelCallError,
    ModelRequest,
    ModelResponse,
    ScriptedGameMasterModel,
)
from monmusu_agent.agentic_session import (
    AgenticSessionStore,
    CreatedSession,
    NewSessionRequest,
)


class ForbiddenModelLoop:
    """记录任何不该发生的旧 GM 模型循环启动。"""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        raise AssertionError("会话初始化不得调用 GM 模型")


class FixedRandom:
    def __init__(self) -> None:
        self.values = iter((3, 4))

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
            "公开检定 | 行动：检查牢门铰链 | 能力：spot_hidden（70） | "
            "难度：regular（目标 70） | 奖励/惩罚骰：none 0 | "
            "事前风险：失败会错过新鲜刮痕 | 骰点：43 | 结果：regular_success"
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


class AgenticCliTest(unittest.TestCase):
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
        result = NewSessionCliResult(
            created=CreatedSession(
                game_id="game_test_0001",
                session_directory=session_file.parent,
                session_file=session_file,
                opening_narration="开场文本",
                session={},
            ),
            first_action="我倾听走廊。",
        )
        output = io.StringIO()

        store = object()
        harness = object()
        with (
            patch.dict(
                os.environ,
                {
                    "DEEPSEEK_API_KEY": "sk-cli-secret-fragment",
                    "MONMUSU_DEEPSEEK_MODEL_ID": "deepseek-v4-flash",
                    "MONMUSU_DEEPSEEK_THINKING": "false",
                },
                clear=True,
            ),
            patch(
                "monmusu_agent.agentic_cli.AgenticSessionStore",
                return_value=store,
            ),
            patch(
                "monmusu_agent.agentic_cli.run_new_session_cli",
                return_value=result,
            ),
            patch(
                "monmusu_agent.agentic_cli.compose_deepseek_harness",
                return_value=harness,
            ) as compose,
            patch("monmusu_agent.agentic_cli.run_game_cli") as run_game,
            redirect_stdout(output),
        ):
            self.assertEqual(main(), 0)

        compose.assert_called_once_with(
            store,
            api_key="sk-cli-secret-fragment",
            model_id="deepseek-v4-flash",
            thinking=False,
        )
        run_game.assert_called_once_with(
            harness,
            store,
            "game_test_0001",
            "我倾听走廊。",
        )
        self.assertNotIn(str(session_file), output.getvalue())
        self.assertNotIn("sk-cli-secret-fragment", output.getvalue())

    def test_main_requires_external_key_before_creating_session(self) -> None:
        output = io.StringIO()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("monmusu_agent.agentic_cli.AgenticSessionStore") as store,
            redirect_stdout(output),
        ):
            self.assertEqual(main(), 2)

        store.assert_not_called()
        self.assertIn("DEEPSEEK_API_KEY", output.getvalue())

    def test_main_reports_terminal_encoding_error_without_traceback(self) -> None:
        output = io.StringIO()
        with (
            patch.dict(
                os.environ,
                {
                    "DEEPSEEK_API_KEY": "sk-cli-secret-fragment",
                    "MONMUSU_DEEPSEEK_MODEL_ID": "deepseek-v4-flash",
                    "MONMUSU_DEEPSEEK_THINKING": "false",
                },
                clear=True,
            ),
            patch("monmusu_agent.agentic_cli.AgenticSessionStore"),
            patch("monmusu_agent.agentic_cli.compose_deepseek_harness"),
            patch(
                "monmusu_agent.agentic_cli.run_new_session_cli",
                side_effect=CliInputEncodingError("终端输入不是有效的 UTF-8"),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(main(), 2)

        self.assertEqual(output.getvalue(), "输入错误：终端输入不是有效的 UTF-8\n")

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
                store,
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
            self.assertEqual(len(output), 1)
            rendered = "\n".join(output)
            self.assertIn("技术中断（request_timeout）", rendered)
            self.assertNotIn(secret_text, rendered)
            self.assertNotIn("隐藏推理材料", rendered)
            self.assertNotIn("骰点", rendered)


if __name__ == "__main__":
    unittest.main()
