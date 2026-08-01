import tempfile
import unittest
from pathlib import Path
from random import Random
from typing import Callable, cast

from monmusu_agent.agent import (
    FinalModelStep,
    GameMasterAgent,
    GameMasterAgentError,
    GameMasterDraft,
    GameMasterStateView,
    ModelRequest,
    ModelStep,
    ToolCallModelStep,
)
from monmusu_agent.config import AppPaths
from monmusu_agent.engine import GameEngine
from monmusu_agent.rules import CheckLedger, RuleEngine
from monmusu_agent.state import StateCommitter
from monmusu_agent.storage import read_json
from monmusu_agent.tools import ToolExecutor, ToolSession, TurnContext


class ScriptedModel:
    """用固定响应代替尚未接入的外部模型。"""

    def __init__(
        self,
        steps: list[ModelStep | Callable[[ModelRequest], ModelStep]],
    ) -> None:
        self.steps = list(steps)
        self.requests: list[ModelRequest] = []

    def next_step(self, request: ModelRequest) -> ModelStep:
        self.requests.append(request)
        step = self.steps.pop(0)
        if callable(step):
            return step(request)
        return step


class FailingModel:
    """模拟外部模型调用超时。"""

    def next_step(self, request: ModelRequest) -> ModelStep:
        raise TimeoutError("模型响应超时")


class InvalidStepModel:
    """模拟 adapter 返回了不属于当前协议的对象。"""

    def next_step(self, request: ModelRequest) -> object:
        return object()


def _start_session(
    directory: str,
    *,
    input_text: str = "门外现在安全吗？",
    tool_limits: dict[str, int] | None = None,
) -> tuple[AppPaths, TurnContext, ToolSession]:
    paths = AppPaths(runtime_dir=Path(directory))
    state = GameEngine(paths).initialize()
    ledger = CheckLedger(paths.check_records_file)
    executor = ToolExecutor(
        paths=paths,
        rule_engine=RuleEngine(random=Random(1), ledger=ledger),
        state_committer=StateCommitter(paths=paths, check_ledger=ledger),
    )
    context = TurnContext(
        turn_id="turn_0001",
        input_text=input_text,
        initial_game_state=state,
        max_tool_steps=8,
        tool_limits=tool_limits or {"request_check": 2, "apply_effect": 2},
    )
    return paths, context, executor.start_turn(context)


def _state_view() -> GameMasterStateView:
    return GameMasterStateView(
        state_version=0,
        current_scene="stone_cell",
        user_public_state={},
        character_public_states={},
        clues_found=(),
        accessible_locations=("stone_cell",),
        threat_clock={"value": 0, "maximum": 6},
        gm_visible_flags={},
    )


class GameMasterAgentTest(unittest.TestCase):
    def test_state_view_is_deeply_read_only(self) -> None:
        """模型不能通过嵌套容器改写本轮固定状态投影。"""

        view = GameMasterStateView(
            state_version=0,
            current_scene="stone_cell",
            user_public_state={"hp": 10, "conditions": []},
            character_public_states={"vespera": {"hp": 11}},
            clues_found=(),
            accessible_locations=("stone_cell",),
            threat_clock={"value": 0, "maximum": 6},
            gm_visible_flags={"lock_visible": True},
        )

        with self.assertRaises(TypeError):
            cast(dict[str, object], view.user_public_state)["hp"] = 0
        with self.assertRaises(TypeError):
            cast(
                dict[str, object],
                view.character_public_states["vespera"],
            )["hp"] = 0
        with self.assertRaises(TypeError):
            cast(dict[str, object], view.threat_clock)["value"] = 6
        with self.assertRaises(TypeError):
            cast(dict[str, object], view.gm_visible_flags)["lock_visible"] = False

    def test_returns_final_result_without_calling_tools(self) -> None:
        """模型直接完成回合时，不应产生任何工具调用。"""

        draft = GameMasterDraft(
            strategy="fast",
            narration="门外只有潮水拍击石墙的回声。",
            suggested_actions=("继续听门外的动静",),
        )
        model = ScriptedModel([FinalModelStep(draft=draft)])

        with tempfile.TemporaryDirectory() as directory:
            _, context, session = _start_session(directory)

            result = GameMasterAgent(model=model, max_iterations=8).run(
                context,
                session,
                state_view=_state_view(),
                scene_context={},
                public_memory=(),
            )

            self.assertEqual(result, draft)
            self.assertEqual(session.trace, ())

    def test_returns_check_result_to_model_with_refreshed_tools(self) -> None:
        """检定结果会进入下一步，耗尽配额的工具会从目录移除。"""

        def finish_after_check(request: ModelRequest) -> FinalModelStep:
            self.assertEqual(len(request.tool_interactions), 1)
            interaction = request.tool_interactions[0]
            self.assertEqual(interaction.call.tool_name, "request_check")
            self.assertTrue(interaction.result.ok)
            self.assertIsNotNone(interaction.result.data)
            assert interaction.result.data is not None
            self.assertEqual(interaction.result.data["kind"], "check_result")
            self.assertEqual(interaction.result.data["outcome"], "success")
            self.assertEqual(
                tuple(tool.name for tool in request.available_tools),
                ("apply_effect",),
            )
            return FinalModelStep(
                draft=GameMasterDraft(
                    strategy="fast",
                    narration="锁芯发出一声轻响。",
                    suggested_actions=("推开牢门",),
                )
            )

        model = ScriptedModel(
            [
                ToolCallModelStep(
                    tool_name="request_check",
                    arguments={
                        "actor_id": "user",
                        "actor_type": "user",
                        "action": "撬开石牢门锁",
                        "target": "stone_cell_lock",
                        "suggested_skill": "improvisation",
                        "suggested_context_modifier": 0,
                        "modifier_reasons": [],
                        "authorization": "user_declared",
                        "authorization_evidence": "我尝试撬开石牢门锁",
                    },
                ),
                finish_after_check,
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            _, context, session = _start_session(
                directory,
                input_text="我尝试撬开石牢门锁。",
                tool_limits={"request_check": 1, "apply_effect": 2},
            )

            result = GameMasterAgent(model=model, max_iterations=8).run(
                context,
                session,
                state_view=_state_view(),
                scene_context={},
                public_memory=(),
            )

            self.assertEqual(result.narration, "锁芯发出一声轻响。")
            self.assertEqual(len(session.trace), 1)

    def test_applies_authorized_effect_after_check_result(self) -> None:
        """模型可使用可信 check_id 申请效果，并收到提交结果。"""

        def apply_unlock(request: ModelRequest) -> ToolCallModelStep:
            interaction = request.tool_interactions[-1]
            self.assertEqual(interaction.call.tool_name, "request_check")
            self.assertTrue(interaction.result.ok)
            self.assertIsNotNone(interaction.result.data)
            assert interaction.result.data is not None
            return ToolCallModelStep(
                tool_name="apply_effect",
                arguments={
                    "expected_state_version": 0,
                    "source_type": "check",
                    "source_id": interaction.result.data["check_id"],
                    "effect_id": "unlock_stone_cell_lock",
                    "reason": "门锁检定成功",
                },
            )

        def finish_after_commit(request: ModelRequest) -> FinalModelStep:
            interaction = request.tool_interactions[-1]
            self.assertEqual(interaction.call.tool_name, "apply_effect")
            self.assertTrue(interaction.result.ok)
            self.assertIsNotNone(interaction.result.data)
            assert interaction.result.data is not None
            self.assertEqual(interaction.result.data["status"], "applied")
            self.assertEqual(interaction.result.data["state_version"], 1)
            return FinalModelStep(
                draft=GameMasterDraft(
                    strategy="fast",
                    narration="石牢门锁脱开了。",
                    suggested_actions=("推开牢门",),
                )
            )

        model = ScriptedModel(
            [
                ToolCallModelStep(
                    tool_name="request_check",
                    arguments={
                        "actor_id": "user",
                        "actor_type": "user",
                        "action": "撬开石牢门锁",
                        "target": "stone_cell_lock",
                        "suggested_skill": "improvisation",
                        "suggested_context_modifier": 0,
                        "modifier_reasons": [],
                        "authorization": "user_declared",
                        "authorization_evidence": "我尝试撬开石牢门锁",
                    },
                ),
                apply_unlock,
                finish_after_commit,
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            paths, context, session = _start_session(
                directory,
                input_text="我尝试撬开石牢门锁。",
                tool_limits={"request_check": 1, "apply_effect": 1},
            )

            result = GameMasterAgent(model=model, max_iterations=8).run(
                context,
                session,
                state_view=_state_view(),
                scene_context={},
                public_memory=(),
            )

            self.assertEqual(result.narration, "石牢门锁脱开了。")
            self.assertEqual(session.current_state_version, 1)
            self.assertTrue(
                read_json(paths.game_state_file)["flags"]["stone_cell_lock_unlocked"],
            )

    def test_returns_tool_error_to_model_without_state_change(self) -> None:
        """目录外工具会被可信层拒绝，错误仍可供模型决定如何收尾。"""

        def finish_after_error(request: ModelRequest) -> FinalModelStep:
            interaction = request.tool_interactions[-1]
            self.assertEqual(interaction.call.tool_name, "invented_tool")
            self.assertFalse(interaction.result.ok)
            self.assertIsNotNone(interaction.result.error)
            assert interaction.result.error is not None
            self.assertEqual(interaction.result.error.code, "tool_not_allowed")
            return FinalModelStep(
                draft=GameMasterDraft(
                    strategy="fast",
                    narration="你需要先说明想做什么。",
                    suggested_actions=("观察牢门",),
                )
            )

        model = ScriptedModel(
            [
                ToolCallModelStep(tool_name="invented_tool", arguments={}),
                finish_after_error,
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            paths, context, session = _start_session(directory)

            result = GameMasterAgent(model=model, max_iterations=8).run(
                context,
                session,
                state_view=_state_view(),
                scene_context={},
                public_memory=(),
            )

            self.assertEqual(result.narration, "你需要先说明想做什么。")
            self.assertEqual(read_json(paths.game_state_file)["state_version"], 0)

    def test_returns_version_conflict_commit_result_to_model(self) -> None:
        """旧版本效果申请的拒绝结果不能被误作工具调用失败或状态成功。"""

        def apply_with_stale_version(request: ModelRequest) -> ToolCallModelStep:
            interaction = request.tool_interactions[-1]
            self.assertTrue(interaction.result.ok)
            self.assertIsNotNone(interaction.result.data)
            assert interaction.result.data is not None
            return ToolCallModelStep(
                tool_name="apply_effect",
                arguments={
                    "expected_state_version": 1,
                    "source_type": "check",
                    "source_id": interaction.result.data["check_id"],
                    "effect_id": "unlock_stone_cell_lock",
                    "reason": "使用了过期的版本",
                },
            )

        def finish_after_conflict(request: ModelRequest) -> FinalModelStep:
            interaction = request.tool_interactions[-1]
            self.assertTrue(interaction.result.ok)
            self.assertIsNotNone(interaction.result.data)
            assert interaction.result.data is not None
            self.assertEqual(interaction.result.data["kind"], "commit_result")
            self.assertEqual(interaction.result.data["status"], "rejected")
            self.assertEqual(
                interaction.result.data["error_code"],
                "state_version_conflict",
            )
            self.assertEqual(request.available_tools, ())
            return FinalModelStep(
                draft=GameMasterDraft(
                    strategy="fast",
                    narration="锁芯松动，却还没有真正打开牢门。",
                    suggested_actions=("重新确认门锁状态",),
                )
            )

        model = ScriptedModel(
            [
                ToolCallModelStep(
                    tool_name="request_check",
                    arguments={
                        "actor_id": "user",
                        "actor_type": "user",
                        "action": "撬开石牢门锁",
                        "target": "stone_cell_lock",
                        "suggested_skill": "improvisation",
                        "suggested_context_modifier": 0,
                        "modifier_reasons": [],
                        "authorization": "user_declared",
                        "authorization_evidence": "我尝试撬开石牢门锁",
                    },
                ),
                apply_with_stale_version,
                finish_after_conflict,
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            paths, context, session = _start_session(
                directory,
                input_text="我尝试撬开石牢门锁。",
                tool_limits={"request_check": 1, "apply_effect": 1},
            )

            result = GameMasterAgent(model=model, max_iterations=8).run(
                context,
                session,
                state_view=_state_view(),
                scene_context={},
                public_memory=(),
            )

            self.assertEqual(
                result.narration,
                "锁芯松动，却还没有真正打开牢门。",
            )
            self.assertEqual(session.current_state_version, 0)
            self.assertNotIn(
                "stone_cell_lock_unlocked",
                read_json(paths.game_state_file)["flags"],
            )

    def test_stops_at_iteration_limit_when_model_never_finishes(self) -> None:
        """有限循环必须在模型持续请求工具时受控终止。"""

        model = ScriptedModel(
            [
                ToolCallModelStep(tool_name="invented_tool", arguments={}),
                ToolCallModelStep(tool_name="invented_tool", arguments={}),
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            _, context, session = _start_session(directory)

            with self.assertRaises(GameMasterAgentError) as raised:
                GameMasterAgent(model=model, max_iterations=2).run(
                    context,
                    session,
                    state_view=_state_view(),
                    scene_context={},
                    public_memory=(),
                )

            self.assertEqual(raised.exception.code, "iteration_limit_exceeded")
            self.assertEqual(len(session.trace), 2)

    def test_wraps_model_failure_without_executing_tools(self) -> None:
        """模型失败必须转换为受控错误，且不能凭空产生工具结果。"""

        with tempfile.TemporaryDirectory() as directory:
            _, context, session = _start_session(directory)

            with self.assertRaises(GameMasterAgentError) as raised:
                GameMasterAgent(model=FailingModel(), max_iterations=8).run(
                    context,
                    session,
                    state_view=_state_view(),
                    scene_context={},
                    public_memory=(),
                )

            self.assertEqual(raised.exception.code, "model_failure")
            self.assertIsInstance(raised.exception.__cause__, TimeoutError)
            self.assertEqual(session.trace, ())

    def test_rejects_invalid_model_step_without_executing_tools(self) -> None:
        """模型 adapter 返回未知步骤时，循环必须受控停止。"""

        with tempfile.TemporaryDirectory() as directory:
            _, context, session = _start_session(directory)

            with self.assertRaises(GameMasterAgentError) as raised:
                GameMasterAgent(  # type: ignore[arg-type]
                    model=InvalidStepModel(),
                    max_iterations=8,
                ).run(
                    context,
                    session,
                    state_view=_state_view(),
                    scene_context={},
                    public_memory=(),
                )

            self.assertEqual(raised.exception.code, "invalid_model_step")
            self.assertEqual(session.trace, ())


if __name__ == "__main__":
    unittest.main()
