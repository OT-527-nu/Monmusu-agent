import tempfile
import unittest
from pathlib import Path
from random import Random
from typing import Callable, Mapping, cast

from monmusu_agent.agent import (
    FinalModelStep,
    GameMasterAgent,
    GameMasterDraft,
    GameMasterModel,
    ModelRequest,
    ModelStep,
    ToolCallModelStep,
)
from monmusu_agent.config import AppPaths
from monmusu_agent.engine import (
    GameEndedError,
    GameEngine,
    GameEngineConfigurationError,
    GameInputError,
    GameMasterTurnResult,
    GameMemoryError,
    GameStateError,
    GameStaticDataError,
)
from monmusu_agent.rules import CheckLedger, RuleEngine
from monmusu_agent.state import StateCommitter
from monmusu_agent.storage import read_json, write_json
from monmusu_agent.tools import ToolExecutor


class FinalDraftModel:
    """在系统边界返回固定 GM 草稿。"""

    def __init__(self, draft: GameMasterDraft) -> None:
        self.draft = draft
        self.requests: list[ModelRequest] = []

    def next_step(self, request: ModelRequest) -> ModelStep:
        self.requests.append(request)
        return FinalModelStep(draft=self.draft)


class ProjectionInspectingModel(FinalDraftModel):
    """记录模型投影，并尝试篡改本轮固定公开输入。"""

    def __init__(self, draft: GameMasterDraft) -> None:
        super().__init__(draft)
        self.scene_context_was_mutable = False
        self.public_memory_was_mutable = False

    def next_step(self, request: ModelRequest) -> ModelStep:
        self.requests.append(request)
        try:
            cast(dict[str, object], request.scene_context)["scene_id"] = (
                "invented_scene"
            )
            self.scene_context_was_mutable = True
        except TypeError:
            pass
        try:
            cast(dict[str, object], request.public_memory[0])["fact"] = (
                "被模型篡改"
            )
            self.public_memory_was_mutable = True
        except TypeError:
            pass
        return FinalModelStep(draft=self.draft)


class ScriptedTurnModel:
    """按顺序返回工具请求、回调或最终草稿。"""

    def __init__(
        self,
        steps: list[ModelStep | Callable[[ModelRequest], ModelStep]],
    ) -> None:
        self.steps = list(steps)
        self.requests: list[ModelRequest] = []

    def next_step(self, request: ModelRequest) -> ModelStep:
        self.requests.append(request)
        step = self.steps.pop(0)
        return step(request) if callable(step) else step


def _running_engine(
    directory: str,
    model: GameMasterModel,
    *,
    paths: AppPaths | None = None,
    turn_id_factory: Callable[[], str] | None = None,
) -> tuple[AppPaths, GameEngine]:
    """装配一次外层回合所需的真实可信模块。"""

    paths = paths or AppPaths(runtime_dir=Path(directory))
    ledger = CheckLedger(paths.check_records_file)
    tool_executor = ToolExecutor(
        paths=paths,
        rule_engine=RuleEngine(random=Random(1), ledger=ledger),
        state_committer=StateCommitter(paths=paths, check_ledger=ledger),
    )
    engine = GameEngine(
        paths=paths,
        agent=GameMasterAgent(model=model, max_iterations=8),
        tool_executor=tool_executor,
        turn_id_factory=turn_id_factory or (lambda: "turn_fixed_001"),
    )
    engine.initialize()
    return paths, engine


class GameEngineTest(unittest.TestCase):
    def test_initialize_writes_escape_thalarion_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            engine = GameEngine(paths=paths)
            state = engine.initialize()

            expected_state = {
                "schema_version": "1.0",
                "game_id": "game_0001",
                "module_id": "escape_thalarion",
                "state_version": 0,
                "current_scene": "stone_cell",
                "user_character": {
                    "character_id": "user",
                    "background_hook": "一直被远海的浪声召回梦中",
                    "specialty": "willpower",
                    "skills": {
                        "willpower": 60,
                        "improvisation": 45
                    },
                    "dream_omen_used_in_scenes": [],
                    "hp": 10,
                    "sanity": 50,
                    "pressure": 0,
                    "conditions": []
                },
                "characters": {
                    "vespera": {
                        "hp": 11,
                        "sanity": 62,
                        "pressure": 0,
                        "conditions": ["injured_wing"],
                        "speech_register": "duty_bound"
                    },
                    "saphra_iskaran": {
                        "hp": 12,
                        "sanity": 58,
                        "pressure": 0,
                        "conditions": ["tail_abraded", "chilled"],
                        "speech_register": "formal_confident"
                    },
                    "aranis": {
                        "hp": 9,
                        "sanity": 55,
                        "pressure": 0,
                        "conditions": ["dehydrated"],
                        "speech_register": "practical_coworker"
                    }
                },
                "clues_found": [],
                "accessible_locations": ["stone_cell"],
                "flags": {},
                "threat_clock": {
                    "clock_id": "rathi_gaze",
                    "value": 0,
                    "maximum": 6
                },
                "ending_id": None
            }
            expected_memory = {
                "schema_version": "1.0",
                "game_id": "game_0001",
                "public_memory": [],
                "private_memory_by_character": {
                    "vespera": [],
                    "saphra_iskaran": [],
                    "aranis": []
                },
                "relationship_state": {
                    "vespera": {
                        "stage": "unnamed_choice",
                        "events": [],
                        "pending_echo": None
                    },
                    "saphra_iskaran": {
                        "stage": "family_name_is_certainty",
                        "events": [],
                        "pending_echo": None
                    },
                    "aranis": {
                        "stage": "temporary_same_rope",
                        "events": [],
                        "pending_echo": None
                    }
                },
                "unresolved_questions": [],
                "turn_log": []
            }

            self.assertEqual(state, expected_state)
            self.assertTrue(paths.game_state_file.exists())
            self.assertTrue(paths.memory_file.exists())
            self.assertTrue(paths.check_records_file.exists())
            self.assertEqual(read_json(paths.game_state_file), expected_state)
            self.assertEqual(read_json(paths.memory_file), expected_memory)
            self.assertEqual(
                read_json(paths.check_records_file),
                {"schema_version": "1.0", "records": []},
            )
            self.assertIn("塔纳里昂", engine.opening_text())

    def test_run_turn_requires_runtime_dependencies(self) -> None:
        """初始化无需运行依赖，但外层回合必须完成有限依赖注入。"""

        with tempfile.TemporaryDirectory() as directory:
            engine = GameEngine(
                paths=AppPaths(runtime_dir=Path(directory)),
            )
            engine.initialize()

            with self.assertRaises(GameEngineConfigurationError):
                engine.run_turn("我观察牢门。")

    def test_run_turn_returns_trusted_result_without_tools(self) -> None:
        """无工具回合应把模型草稿组装为外层可信结果。"""

        draft = GameMasterDraft(
            strategy="fast",
            narration="门外只剩潮声，守卫暂时没有回来。",
            suggested_actions=("检查牢门", "询问同伴"),
        )
        model = FinalDraftModel(draft)

        with tempfile.TemporaryDirectory() as directory:
            _, engine = _running_engine(directory, model)

            outcome = engine.run_turn("我先听听门外的动静。")

            self.assertEqual(
                outcome.result,
                GameMasterTurnResult(
                    turn_id="turn_fixed_001",
                    strategy="fast",
                    narration="门外只剩潮声，守卫暂时没有回来。",
                    character_turns=(),
                    checks=(),
                    committed_effects=(),
                    suggested_actions=("检查牢门", "询问同伴"),
                    ending_id=None,
                ),
            )
            self.assertEqual(outcome.tool_trace, ())
            self.assertFalse(outcome.degraded)
            self.assertIsNone(outcome.failure_code)
            self.assertEqual(len(model.requests), 1)

    def test_run_turn_reads_ending_from_session_final_snapshot(self) -> None:
        """本轮提交的结局必须出现在同一外层回合的最终结果中。"""

        def apply_ending(request: ModelRequest) -> ModelStep:
            check_data = request.tool_interactions[-1].result.data
            assert check_data is not None
            return ToolCallModelStep(
                tool_name="apply_effect",
                arguments={
                    "expected_state_version": 0,
                    "source_type": "check",
                    "source_id": check_data["check_id"],
                    "effect_id": "finish_escape",
                    "reason": "牢门后的路线直接通向城外",
                },
            )

        model = ScriptedTurnModel(
            [
                ToolCallModelStep(
                    tool_name="request_check",
                    arguments={
                        "actor_id": "user",
                        "actor_type": "user",
                        "action": "撬开石牢门锁并逃离",
                        "target": "stone_cell_lock",
                        "suggested_skill": "improvisation",
                        "suggested_context_modifier": 0,
                        "modifier_reasons": [],
                        "authorization": "user_declared",
                        "authorization_evidence": "我撬开牢门后立刻带大家逃走",
                    },
                ),
                apply_ending,
                FinalModelStep(
                    draft=GameMasterDraft(
                        "urgent",
                        "牢门弹开，你们赶在守卫回来前冲出了城墙。",
                        (),
                    )
                ),
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = AppPaths(
                data_dir=root / "data",
                runtime_dir=root / "var",
            )
            source_paths = AppPaths()
            module = read_json(source_paths.module_file)
            module["ending_ids"] = ["escape_clean"]
            module["effect_definitions"]["finish_escape"] = {
                "operations": [
                    {
                        "path": "ending_id",
                        "operation": "set",
                        "value": "escape_clean",
                    }
                ]
            }
            for outcome in ("critical_success", "success"):
                module["check_rules"][0]["effects_by_outcome"][outcome] = [
                    "finish_escape"
                ]
            write_json(paths.module_file, module)
            write_json(
                paths.characters_file,
                read_json(source_paths.characters_file),
            )
            _, engine = _running_engine(directory, model, paths=paths)

            outcome = engine.run_turn("我撬开牢门后立刻带大家逃走。")

            self.assertEqual(outcome.result.ending_id, "escape_clean")
            self.assertEqual(
                read_json(paths.game_state_file)["ending_id"],
                "escape_clean",
            )

    def test_run_turn_aggregates_untampered_check_and_commit_results(self) -> None:
        """外层结果只收集可信轨迹中的正式检定与已提交效果。"""

        model_could_mutate_check = False

        def apply_unlock(request: ModelRequest) -> ModelStep:
            nonlocal model_could_mutate_check
            check_data = request.tool_interactions[-1].result.data
            assert check_data is not None
            try:
                cast(dict[str, object], check_data)["outcome"] = (
                    "critical_success"
                )
                model_could_mutate_check = True
            except TypeError:
                pass
            return ToolCallModelStep(
                tool_name="apply_effect",
                arguments={
                    "expected_state_version": 0,
                    "source_type": "check",
                    "source_id": check_data["check_id"],
                    "effect_id": "unlock_stone_cell_lock",
                    "reason": "开锁检定成功",
                },
            )

        model = ScriptedTurnModel(
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
                FinalModelStep(
                    draft=GameMasterDraft(
                        "dramatic",
                        "锁舌擦过锈槽，牢门终于向外松开。",
                        ("推开牢门",),
                    )
                ),
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            paths, engine = _running_engine(directory, model)
            memory_before = read_json(paths.memory_file)

            outcome = engine.run_turn("我尝试撬开石牢门锁。")

            self.assertFalse(model_could_mutate_check)
            self.assertEqual(len(outcome.result.checks), 1)
            self.assertEqual(outcome.result.checks[0]["outcome"], "success")
            self.assertEqual(outcome.result.checks[0]["roll"], 18)
            self.assertEqual(len(outcome.result.committed_effects), 1)
            self.assertEqual(
                outcome.result.committed_effects[0]["status"],
                "applied",
            )
            self.assertEqual(
                outcome.result.committed_effects[0]["effect_id"],
                "unlock_stone_cell_lock",
            )
            self.assertEqual(
                tuple(entry.tool_name for entry in outcome.tool_trace),
                ("request_check", "apply_effect"),
            )
            self.assertTrue(
                read_json(paths.game_state_file)["flags"][
                    "stone_cell_lock_unlocked"
                ]
            )
            self.assertEqual(read_json(paths.memory_file), memory_before)

    def test_run_turn_deduplicates_idempotent_commit_retry(self) -> None:
        """响应丢失后的同效果重试应保留轨迹，但只汇总一次提交。"""

        def apply_unlock(request: ModelRequest) -> ModelStep:
            check_data = request.tool_interactions[-1].result.data
            assert check_data is not None
            return ToolCallModelStep(
                tool_name="apply_effect",
                arguments={
                    "expected_state_version": 0,
                    "source_type": "check",
                    "source_id": check_data["check_id"],
                    "effect_id": "unlock_stone_cell_lock",
                    "reason": "首次提交的响应可能丢失",
                },
            )

        def retry_same_effect(request: ModelRequest) -> ModelStep:
            check_data = request.tool_interactions[0].result.data
            assert check_data is not None
            return ToolCallModelStep(
                tool_name="apply_effect",
                arguments={
                    "expected_state_version": 0,
                    "source_type": "check",
                    "source_id": check_data["check_id"],
                    "effect_id": "unlock_stone_cell_lock",
                    "reason": "未收到确认，因此按同一幂等键重试",
                },
            )

        model = ScriptedTurnModel(
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
                retry_same_effect,
                FinalModelStep(
                    draft=GameMasterDraft(
                        "fast",
                        "牢门已经解锁。",
                        ("推开牢门",),
                    )
                ),
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            _, engine = _running_engine(directory, model)

            outcome = engine.run_turn("我尝试撬开石牢门锁。")

            commit_results = []
            for entry in outcome.tool_trace:
                if entry.tool_name != "apply_effect":
                    continue
                data = entry.tool_result.data
                self.assertIsNotNone(data)
                assert data is not None
                commit_results.append(data)
            self.assertEqual(
                tuple(result["status"] for result in commit_results),
                ("applied", "already_applied"),
            )
            self.assertEqual(
                commit_results[0]["commit_id"],
                commit_results[1]["commit_id"],
            )
            self.assertEqual(len(outcome.result.committed_effects), 1)
            self.assertEqual(
                outcome.result.committed_effects[0]["commit_id"],
                commit_results[0]["commit_id"],
            )

    def test_run_turn_degrades_invalid_model_draft_without_repair_call(self) -> None:
        """非法 Draft 应确定性降级，且不额外调用模型尝试修复。"""

        model = FinalDraftModel(
            GameMasterDraft(
                strategy="invented_strategy",
                narration="这段候选内容不应成为正式叙事。",
                suggested_actions=("不存在的操作",),
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            _, engine = _running_engine(directory, model)

            outcome = engine.run_turn("我观察牢门。")

            self.assertTrue(outcome.degraded)
            self.assertEqual(outcome.failure_code, "invalid_draft")
            self.assertEqual(outcome.result.strategy, "degraded")
            self.assertNotEqual(
                outcome.result.narration,
                "这段候选内容不应成为正式叙事。",
            )
            self.assertIn("暂时无法完成", outcome.result.narration)
            self.assertEqual(outcome.result.suggested_actions, ())
            self.assertEqual(outcome.tool_trace, ())
            self.assertEqual(len(model.requests), 1)

    def test_run_turn_preserves_mechanical_results_when_agent_fails(self) -> None:
        """模型收尾失败不能回滚已经确认的检定和状态提交。"""

        def apply_unlock(request: ModelRequest) -> ModelStep:
            check_data = request.tool_interactions[-1].result.data
            assert check_data is not None
            return ToolCallModelStep(
                tool_name="apply_effect",
                arguments={
                    "expected_state_version": 0,
                    "source_type": "check",
                    "source_id": check_data["check_id"],
                    "effect_id": "unlock_stone_cell_lock",
                    "reason": "开锁检定成功",
                },
            )

        def fail_during_narration(request: ModelRequest) -> ModelStep:
            raise TimeoutError("模型收尾超时")

        model = ScriptedTurnModel(
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
                fail_during_narration,
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            paths, engine = _running_engine(directory, model)

            outcome = engine.run_turn("我尝试撬开石牢门锁。")

            self.assertTrue(outcome.degraded)
            self.assertEqual(outcome.failure_code, "model_failure")
            self.assertEqual(outcome.result.strategy, "degraded")
            self.assertIn("暂时无法完成", outcome.result.narration)
            self.assertEqual(len(outcome.result.checks), 1)
            self.assertEqual(outcome.result.checks[0]["outcome"], "success")
            self.assertEqual(len(outcome.result.committed_effects), 1)
            self.assertEqual(len(outcome.tool_trace), 2)
            self.assertTrue(
                read_json(paths.game_state_file)["flags"][
                    "stone_cell_lock_unlocked"
                ]
            )

    def test_model_receives_only_safe_read_only_turn_projection(self) -> None:
        """模型输入排除私密状态，且不能改写固定场景与公开记忆。"""

        model = ProjectionInspectingModel(
            GameMasterDraft("fast", "潮声掩住了你们的低语。", ("检查牢门",)),
        )
        with tempfile.TemporaryDirectory() as directory:
            paths, engine = _running_engine(directory, model)
            state = read_json(paths.game_state_file)
            state["flags"] = {
                "stone_cell_lock_unlocked": True,
                "hidden_keeper_route": "北侧暗门",
            }
            state["user_character"]["hidden_omen"] = "海底王座"
            state["characters"]["vespera"]["hidden_motive"] = "认出旧日仇敌"
            write_json(paths.game_state_file, state)
            memory = read_json(paths.memory_file)
            memory["public_memory"] = [{"fact": "守卫刚刚离开石牢。"}]
            memory["private_memory_by_character"]["vespera"] = [
                {"fact": "她认出了守卫。"}
            ]
            memory["relationship_state"]["vespera"]["stage"] = (
                "hidden_relationship_stage"
            )
            write_json(paths.memory_file, memory)

            engine.run_turn("我压低声音询问大家。")

            self.assertFalse(model.scene_context_was_mutable)
            self.assertFalse(model.public_memory_was_mutable)
            request = model.requests[0]
            self.assertFalse(hasattr(request, "context"))
            self.assertNotIn(
                "dream_omen_used_in_scenes",
                request.state_view.user_public_state,
            )
            self.assertEqual(
                request.state_view.gm_visible_flags,
                {"stone_cell_lock_unlocked": True},
            )
            self.assertEqual(
                request.public_memory[0]["fact"],
                "守卫刚刚离开石牢。",
            )
            serialized_request = repr(request)
            self.assertNotIn("北侧暗门", serialized_request)
            self.assertNotIn("海底王座", serialized_request)
            self.assertNotIn("认出旧日仇敌", serialized_request)
            self.assertNotIn("她认出了守卫", serialized_request)
            self.assertNotIn("hidden_relationship_stage", serialized_request)
            self.assertNotIn("石缝里的排水痕迹", serialized_request)

    def test_tool_interactions_do_not_reveal_turn_id_to_model(self) -> None:
        """模型工具结果应脱敏，可信轨迹仍保留完整回合关联。"""

        model_visible_result: dict[str, object] = {}

        def finish_after_check(request: ModelRequest) -> ModelStep:
            interaction = request.tool_interactions[-1]
            data = interaction.result.data
            assert data is not None
            model_visible_result["tool_call_id"] = interaction.result.tool_call_id
            model_visible_result["data"] = data
            model_visible_result["serialized"] = repr(interaction)
            return FinalModelStep(
                draft=GameMasterDraft(
                    "fast",
                    "你试了试锁芯，它仍然可以被撬动。",
                    ("继续开锁",),
                )
            )

        model = ScriptedTurnModel(
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
            _, engine = _running_engine(directory, model)

            outcome = engine.run_turn("我尝试撬开石牢门锁。")

            serialized_result = cast(str, model_visible_result["serialized"])
            self.assertNotIn("turn_fixed_001", serialized_result)
            self.assertEqual(model_visible_result["tool_call_id"], "tool_01")
            visible_data = cast(Mapping[str, object], model_visible_result["data"])
            self.assertNotIn("game_id", visible_data)
            self.assertNotIn("turn_id", visible_data)
            self.assertNotIn("module_id", visible_data)
            self.assertEqual(
                outcome.tool_trace[0].tool_call_id,
                "tool_turn_fixed_001_01",
            )
            self.assertEqual(
                outcome.result.checks[0]["turn_id"],
                "turn_fixed_001",
            )

    def test_run_turn_rejects_invalid_input_before_allocating_turn(self) -> None:
        """空白或过长输入不能启动模型、工具会话或回合编号。"""

        model = FinalDraftModel(
            GameMasterDraft("fast", "不应生成这段叙事。", ()),
        )
        allocated_turn_ids: list[str] = []

        def allocate_turn_id() -> str:
            allocated_turn_ids.append("turn_unexpected")
            return allocated_turn_ids[-1]

        with tempfile.TemporaryDirectory() as directory:
            _, engine = _running_engine(
                directory,
                model,
                turn_id_factory=allocate_turn_id,
            )

            for invalid_input in (" \n\t ", "潮" * 4001):
                with self.subTest(length=len(invalid_input)):
                    with self.assertRaises(GameInputError):
                        engine.run_turn(invalid_input)

            self.assertEqual(allocated_turn_ids, [])
            self.assertEqual(model.requests, [])

    def test_run_turn_rejects_already_ended_game_before_allocating_turn(self) -> None:
        """结局一旦写入，外层引擎不能再启动新回合。"""

        model = FinalDraftModel(GameMasterDraft("fast", "不应继续。", ()))
        allocated_turn_ids: list[str] = []

        def allocate_turn_id() -> str:
            allocated_turn_ids.append("turn_unexpected")
            return allocated_turn_ids[-1]

        with tempfile.TemporaryDirectory() as directory:
            paths, engine = _running_engine(
                directory,
                model,
                turn_id_factory=allocate_turn_id,
            )
            state = read_json(paths.game_state_file)
            state["ending_id"] = "escape_clean"
            write_json(paths.game_state_file, state)

            with self.assertRaises(GameEndedError):
                engine.run_turn("我们离开这里。")

            self.assertEqual(allocated_turn_ids, [])
            self.assertEqual(model.requests, [])

    def test_run_turn_rejects_invalid_game_state_before_allocating_turn(self) -> None:
        """无法形成可信投影的正式状态必须在创建回合前失败。"""

        def invalid_version(state: dict) -> None:
            state["state_version"] = "zero"

        def unknown_scene(state: dict) -> None:
            state["current_scene"] = "invented_scene"

        def invalid_clock(state: dict) -> None:
            state["threat_clock"]["value"] = 7

        def unknown_clue(state: dict) -> None:
            state["clues_found"] = ["invented_clue"]

        cases = (
            ("invalid_version", invalid_version),
            ("unknown_scene", unknown_scene),
            ("invalid_clock", invalid_clock),
            ("unknown_clue", unknown_clue),
        )
        for label, corrupt in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                model = FinalDraftModel(GameMasterDraft("fast", "不应继续。", ()))
                allocated_turn_ids: list[str] = []
                paths, engine = _running_engine(
                    directory,
                    model,
                    turn_id_factory=lambda: allocated_turn_ids.append(
                        "turn_unexpected"
                    )
                    or "turn_unexpected",
                )
                state = read_json(paths.game_state_file)
                corrupt(state)
                write_json(paths.game_state_file, state)

                with self.assertRaises(GameStateError):
                    engine.run_turn("我观察牢房。")

                self.assertEqual(allocated_turn_ids, [])
                self.assertEqual(model.requests, [])

    def test_run_turn_reports_missing_or_damaged_game_state(self) -> None:
        """GameState 存储不可读时应返回稳定的外层错误类型。"""

        def remove_file(paths: AppPaths) -> None:
            paths.game_state_file.unlink()

        def damage_json(paths: AppPaths) -> None:
            paths.game_state_file.write_text("{", encoding="utf-8")

        for label, corrupt in (("missing", remove_file), ("damaged", damage_json)):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                model = FinalDraftModel(GameMasterDraft("fast", "不应继续。", ()))
                allocated_turn_ids: list[str] = []
                paths, engine = _running_engine(
                    directory,
                    model,
                    turn_id_factory=lambda: allocated_turn_ids.append(
                        "turn_unexpected"
                    )
                    or "turn_unexpected",
                )
                corrupt(paths)

                with self.assertRaises(GameStateError):
                    engine.run_turn("我观察牢房。")

                self.assertEqual(allocated_turn_ids, [])
                self.assertEqual(model.requests, [])

    def test_run_turn_rejects_invalid_memory_before_allocating_turn(self) -> None:
        """缺失、损坏或不属于本局的 Memory 不能进入模型上下文。"""

        def remove_file(paths: AppPaths) -> None:
            paths.memory_file.unlink()

        def damage_json(paths: AppPaths) -> None:
            paths.memory_file.write_text("{", encoding="utf-8")

        def mismatch_game(paths: AppPaths) -> None:
            memory = read_json(paths.memory_file)
            memory["game_id"] = "game_other"
            write_json(paths.memory_file, memory)

        def invalidate_public_memory(paths: AppPaths) -> None:
            memory = read_json(paths.memory_file)
            memory["public_memory"] = {"fact": "不应接受对象"}
            write_json(paths.memory_file, memory)

        cases = (
            ("missing", remove_file),
            ("damaged", damage_json),
            ("wrong_game", mismatch_game),
            ("invalid_public_memory", invalidate_public_memory),
        )
        for label, corrupt in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                model = FinalDraftModel(GameMasterDraft("fast", "不应继续。", ()))
                allocated_turn_ids: list[str] = []
                paths, engine = _running_engine(
                    directory,
                    model,
                    turn_id_factory=lambda: allocated_turn_ids.append(
                        "turn_unexpected"
                    )
                    or "turn_unexpected",
                )
                corrupt(paths)

                with self.assertRaises(GameMemoryError):
                    engine.run_turn("我回想刚才发生的事。")

                self.assertEqual(allocated_turn_ids, [])
                self.assertEqual(model.requests, [])

    def test_run_turn_rejects_invalid_static_data_before_allocating_turn(self) -> None:
        """无效模组或角色配置不能进入使用旧缓存的工具与模型。"""

        def invalidate_module(paths: AppPaths) -> None:
            module = read_json(paths.module_file)
            module["scenes"] = []
            write_json(paths.module_file, module)

        def invalidate_characters(paths: AppPaths) -> None:
            write_json(paths.characters_file, {"vespera": "不是角色数组"})

        def remove_character_profile(paths: AppPaths) -> None:
            characters = read_json(paths.characters_file)
            write_json(paths.characters_file, characters[:1])

        for label, corrupt in (
            ("module", invalidate_module),
            ("characters", invalidate_characters),
            ("missing_character_profile", remove_character_profile),
        ):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                paths = AppPaths(
                    data_dir=root / "data",
                    runtime_dir=root / "var",
                )
                source_paths = AppPaths()
                write_json(paths.module_file, read_json(source_paths.module_file))
                write_json(
                    paths.characters_file,
                    read_json(source_paths.characters_file),
                )
                model = FinalDraftModel(GameMasterDraft("fast", "不应继续。", ()))
                allocated_turn_ids: list[str] = []
                _, engine = _running_engine(
                    directory,
                    model,
                    paths=paths,
                    turn_id_factory=lambda: allocated_turn_ids.append(
                        "turn_unexpected"
                    )
                    or "turn_unexpected",
                )
                corrupt(paths)

                with self.assertRaises(GameStaticDataError):
                    engine.run_turn("我观察周围。")

                self.assertEqual(allocated_turn_ids, [])
                self.assertEqual(model.requests, [])


if __name__ == "__main__":
    unittest.main()
