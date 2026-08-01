import tempfile
import unittest
from pathlib import Path
from random import Random
from typing import cast

from monmusu_agent.config import AppPaths
from monmusu_agent.engine import GameEngine
from monmusu_agent.rules import CheckLedger, RequestCheckArgs, RuleEngine
from monmusu_agent.state import ApplyEffectArgs, StateCommitter
from monmusu_agent.storage import read_json, write_json
from monmusu_agent.tools import ToolDefinition, ToolExecutor, TurnContext


class ToolExecutorTest(unittest.TestCase):
    def test_request_check_returns_persisted_check_result(self) -> None:
        """合法工具调用会创建检定记录并返回完整的检定结果。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={
                        "request_check": 2,
                        "apply_effect": 2,
                    },
                ),
            )

            result = session.execute(
                "request_check",
                {
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
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.tool_call_id, "tool_turn_0001_01")
            self.assertEqual(result.tool_name, "request_check")
            self.assertIsNone(result.error)
            self.assertEqual(result.data["kind"], "check_result")
            self.assertEqual(result.data["target"], 35)
            self.assertEqual(result.data["roll"], 18)
            self.assertEqual(result.data["outcome"], "success")
            self.assertEqual(
                result.data["allowed_effect_ids"],
                ["unlock_stone_cell_lock"],
            )
            self.assertEqual(
                ledger.get(result.data["check_id"]).outcome,
                "success",
            )

    def test_apply_effect_commits_authorized_check_and_updates_session_snapshot(
        self,
    ) -> None:
        """效果工具会提交变化，并刷新会话的只读最终状态快照。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={
                        "request_check": 2,
                        "apply_effect": 2,
                    },
                ),
            )
            check = session.execute(
                "request_check",
                {
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
            )

            result = session.execute(
                "apply_effect",
                {
                    "expected_state_version": 0,
                    "source_type": "check",
                    "source_id": check.data["check_id"],
                    "effect_id": "unlock_stone_cell_lock",
                    "reason": "门锁检定成功",
                },
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.tool_call_id, "tool_turn_0001_02")
            self.assertEqual(result.tool_name, "apply_effect")
            self.assertIsNone(result.error)
            self.assertEqual(result.data["kind"], "commit_result")
            self.assertEqual(result.data["status"], "applied")
            self.assertEqual(result.data["commit_id"], "commit_game_0001_0001")
            self.assertEqual(result.data["state_version"], 1)
            self.assertEqual(
                result.data["changes"],
                [
                    {
                        "path": "flags.stone_cell_lock_unlocked",
                        "before": None,
                        "after": True,
                    }
                ],
            )
            self.assertEqual(session.current_state_version, 1)
            snapshot = session.final_state_snapshot
            self.assertEqual(snapshot["state_version"], 1)
            self.assertTrue(snapshot["flags"]["stone_cell_lock_unlocked"])
            with self.assertRaises(TypeError):
                cast(dict[str, object], snapshot)["state_version"] = 99
            with self.assertRaises(TypeError):
                cast(dict[str, object], snapshot["flags"])[
                    "stone_cell_lock_unlocked"
                ] = False
            persisted = read_json(paths.game_state_file)
            self.assertTrue(persisted["flags"]["stone_cell_lock_unlocked"])
            self.assertEqual(persisted["state_version"], 1)

    def test_invalid_request_check_arguments_return_error_without_record(
        self,
    ) -> None:
        """缺少必填字段时，工具拒绝请求且不创建检定记录。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={
                        "request_check": 2,
                        "apply_effect": 2,
                    },
                ),
            )

            result = session.execute(
                "request_check",
                {
                    "actor_type": "user",
                    "action": "撬开石牢门锁",
                    "target": "stone_cell_lock",
                    "suggested_skill": "improvisation",
                    "suggested_context_modifier": 0,
                    "modifier_reasons": [],
                    "authorization": "user_declared",
                    "authorization_evidence": "我尝试撬开石牢门锁",
                },
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.tool_call_id, "tool_turn_0001_01")
            self.assertIsNone(result.data)
            self.assertEqual(result.error.code, "invalid_arguments")
            self.assertTrue(result.error.retryable)
            self.assertEqual(read_json(paths.check_records_file)["records"], [])

    def test_rule_rejected_request_returns_error_without_record(self) -> None:
        """规则拒绝的候选检定不会被伪装成系统故障。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={
                        "request_check": 2,
                        "apply_effect": 2,
                    },
                ),
            )

            result = session.execute(
                "request_check",
                {
                    "actor_id": "vespera",
                    "actor_type": "user",
                    "action": "撬开石牢门锁",
                    "target": "stone_cell_lock",
                    "suggested_skill": "improvisation",
                    "suggested_context_modifier": 0,
                    "modifier_reasons": [],
                    "authorization": "user_declared",
                    "authorization_evidence": "我尝试撬开石牢门锁",
                },
            )

            self.assertFalse(result.ok)
            self.assertIsNone(result.data)
            self.assertEqual(result.error.code, "rule_rejected")
            self.assertTrue(result.error.retryable)
            self.assertEqual(read_json(paths.check_records_file)["records"], [])
            self.assertEqual(len(session.trace), 1)
            entry = session.trace[0]
            self.assertIsInstance(entry.normalized_arguments, RequestCheckArgs)
            self.assertTrue(entry.dispatched)
            self.assertEqual(entry.tool_result, result)

    def test_invalid_arguments_do_not_consume_request_check_quota(self) -> None:
        """预检失败后，仍可使用本轮唯一一次检定配额。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={
                        "request_check": 1,
                        "apply_effect": 2,
                    },
                ),
            )
            invalid = session.execute(
                "request_check",
                {
                    "actor_type": "user",
                    "action": "撬开石牢门锁",
                    "target": "stone_cell_lock",
                    "suggested_skill": "improvisation",
                    "suggested_context_modifier": 0,
                    "modifier_reasons": [],
                    "authorization": "user_declared",
                    "authorization_evidence": "我尝试撬开石牢门锁",
                },
            )
            accepted = session.execute(
                "request_check",
                {
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
            )
            exhausted = session.execute(
                "request_check",
                {
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
            )

            self.assertFalse(invalid.ok)
            self.assertEqual(invalid.error.code, "invalid_arguments")
            self.assertTrue(accepted.ok)
            self.assertFalse(exhausted.ok)
            self.assertEqual(exhausted.error.code, "budget_exceeded")
            self.assertFalse(exhausted.error.retryable)
            self.assertEqual(len(read_json(paths.check_records_file)["records"]), 1)

    def test_every_attempt_consumes_the_total_tool_step_budget(self) -> None:
        """参数预检失败也会消耗回合总步数，避免无限试错。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=1,
                    tool_limits={"request_check": 2},
                ),
            )
            invalid = session.execute(
                "request_check",
                {
                    "actor_type": "user",
                    "action": "撬开石牢门锁",
                    "target": "stone_cell_lock",
                    "suggested_skill": "improvisation",
                    "suggested_context_modifier": 0,
                    "modifier_reasons": [],
                    "authorization": "user_declared",
                    "authorization_evidence": "我尝试撬开石牢门锁",
                },
            )
            exhausted = session.execute(
                "request_check",
                {
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
            )

            self.assertEqual(invalid.error.code, "invalid_arguments")
            self.assertEqual(exhausted.error.code, "budget_exceeded")
            self.assertEqual(read_json(paths.check_records_file)["records"], [])

    def test_tool_outside_context_whitelist_is_not_allowed(self) -> None:
        """ToolSession 只允许 TurnContext 明确列出的工具。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={"request_check": 2},
                ),
            )

            result = session.execute("apply_effect", {})

            self.assertFalse(result.ok)
            self.assertIsNone(result.data)
            self.assertEqual(result.error.code, "tool_not_allowed")
            self.assertFalse(result.error.retryable)
            self.assertEqual(read_json(paths.game_state_file)["state_version"], 0)
            self.assertEqual(len(session.trace), 1)
            entry = session.trace[0]
            self.assertIsNone(entry.normalized_arguments)
            self.assertFalse(entry.dispatched)
            self.assertEqual(entry.tool_result, result)

    def test_context_cannot_enable_an_unimplemented_tool(self) -> None:
        """TurnContext 只能从执行器支持的工具中缩小权限范围。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={"generate_character_turn": 1},
                ),
            )

            result = session.execute("generate_character_turn", {})

            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, "tool_not_allowed")
            self.assertEqual(len(session.trace), 1)
            self.assertFalse(session.trace[0].dispatched)

    def test_successful_request_check_is_recorded_in_session_trace(self) -> None:
        """成功调用会留下供 GameEngine 校验的本回合轨迹。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={
                        "request_check": 2,
                        "apply_effect": 2,
                    },
                ),
            )

            result = session.execute(
                "request_check",
                {
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
            )

            self.assertEqual(len(session.trace), 1)
            entry = session.trace[0]
            self.assertEqual(entry.sequence, 1)
            self.assertEqual(entry.tool_call_id, result.tool_call_id)
            self.assertEqual(entry.tool_name, "request_check")
            self.assertIsInstance(entry.normalized_arguments, RequestCheckArgs)
            self.assertTrue(entry.dispatched)
            self.assertEqual(entry.tool_result, result)

    def test_preflight_failure_is_recorded_without_normalized_arguments(
        self,
    ) -> None:
        """未抵达规则引擎的调用仍会留下可区分的轨迹。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={
                        "request_check": 2,
                        "apply_effect": 2,
                    },
                ),
            )

            result = session.execute(
                "request_check",
                {
                    "actor_type": "user",
                    "action": "撬开石牢门锁",
                    "target": "stone_cell_lock",
                    "suggested_skill": "improvisation",
                    "suggested_context_modifier": 0,
                    "modifier_reasons": [],
                    "authorization": "user_declared",
                    "authorization_evidence": "我尝试撬开石牢门锁",
                },
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, "invalid_arguments")
            self.assertEqual(len(session.trace), 1)
            entry = session.trace[0]
            self.assertEqual(entry.sequence, 1)
            self.assertEqual(entry.tool_call_id, result.tool_call_id)
            self.assertEqual(entry.tool_name, "request_check")
            self.assertIsNone(entry.normalized_arguments)
            self.assertFalse(entry.dispatched)
            self.assertEqual(entry.tool_result, result)

    def test_available_tool_definitions_only_expose_context_whitelist(
        self,
    ) -> None:
        """GM 只能看见当前回合可调用的已实现工具。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={
                        "request_check": 1,
                        "generate_character_turn": 1,
                    },
                ),
            )

            definitions = session.available_tool_definitions()

            self.assertEqual(len(definitions), 1)
            definition = definitions[0]
            self.assertIsInstance(definition, ToolDefinition)
            self.assertEqual(definition.name, "request_check")
            self.assertEqual(definition.input_schema["type"], "object")
            self.assertIn("actor_id", definition.input_schema["required"])

    def test_available_tool_definitions_shrink_after_quota_is_used(
        self,
    ) -> None:
        """会话目录会反映每项工具的剩余调用配额。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={
                        "request_check": 1,
                        "apply_effect": 1,
                    },
                ),
            )

            before = session.available_tool_definitions()
            result = session.execute(
                "request_check",
                {
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
            )
            after = session.available_tool_definitions()

            self.assertTrue(result.ok)
            self.assertEqual(
                [definition.name for definition in before],
                ["request_check", "apply_effect"],
            )
            self.assertEqual(
                [definition.name for definition in after],
                ["apply_effect"],
            )

    def test_request_check_rejects_unknown_argument_without_record(self) -> None:
        """GM 不能在检定候选中夹带由可信代码拥有的字段。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={"request_check": 1},
                ),
            )

            result = session.execute(
                "request_check",
                {
                    "actor_id": "user",
                    "actor_type": "user",
                    "action": "撬开石牢门锁",
                    "target": "stone_cell_lock",
                    "suggested_skill": "improvisation",
                    "suggested_context_modifier": 0,
                    "modifier_reasons": [],
                    "authorization": "user_declared",
                    "authorization_evidence": "我尝试撬开石牢门锁",
                    "roll": 1,
                },
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, "invalid_arguments")
            self.assertEqual(read_json(paths.check_records_file)["records"], [])

    def test_tool_rejects_non_object_arguments(self) -> None:
        """公开工具边界不依赖 Python 类型标注来验证 JSON 对象。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={"request_check": 1},
                ),
            )

            result = session.execute("request_check", [])

            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, "invalid_arguments")
            self.assertEqual(read_json(paths.check_records_file)["records"], [])

    def test_request_check_rejects_non_integer_context_modifier(self) -> None:
        """类型错误必须在调用规则引擎前转化为工具错误。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={"request_check": 1},
                ),
            )

            result = session.execute(
                "request_check",
                {
                    "actor_id": "user",
                    "actor_type": "user",
                    "action": "撬开石牢门锁",
                    "target": "stone_cell_lock",
                    "suggested_skill": "improvisation",
                    "suggested_context_modifier": "很多",
                    "modifier_reasons": [],
                    "authorization": "user_declared",
                    "authorization_evidence": "我尝试撬开石牢门锁",
                },
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, "invalid_arguments")
            self.assertEqual(read_json(paths.check_records_file)["records"], [])

    def test_request_check_rejects_unknown_actor_type(self) -> None:
        """行动者类型只能使用公开工具 schema 中的枚举值。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={"request_check": 1},
                ),
            )

            result = session.execute(
                "request_check",
                {
                    "actor_id": "user",
                    "actor_type": "npc",
                    "action": "撬开石牢门锁",
                    "target": "stone_cell_lock",
                    "suggested_skill": "improvisation",
                    "suggested_context_modifier": 0,
                    "modifier_reasons": [],
                    "authorization": "user_declared",
                    "authorization_evidence": "我尝试撬开石牢门锁",
                },
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, "invalid_arguments")
            self.assertEqual(read_json(paths.check_records_file)["records"], [])

    def test_request_check_rejects_unknown_modifier_reason_field(self) -> None:
        """嵌套理由对象同样不接受 schema 之外的字段。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={"request_check": 1},
                ),
            )

            result = session.execute(
                "request_check",
                {
                    "actor_id": "user",
                    "actor_type": "user",
                    "action": "撬开石牢门锁",
                    "target": "stone_cell_lock",
                    "suggested_skill": "improvisation",
                    "suggested_context_modifier": -5,
                    "modifier_reasons": [
                        {
                            "reason_tag": "poor_position",
                            "source_id": "scene_stone_cell_lock",
                            "hidden_weight": 100,
                        }
                    ],
                    "authorization": "user_declared",
                    "authorization_evidence": "我尝试撬开石牢门锁",
                },
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, "invalid_arguments")
            self.assertEqual(read_json(paths.check_records_file)["records"], [])

    def test_request_check_rejects_unknown_modifier_reason_tag(self) -> None:
        """理由标签必须来自工具 schema 的固定枚举。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={"request_check": 1},
                ),
            )

            result = session.execute(
                "request_check",
                {
                    "actor_id": "user",
                    "actor_type": "user",
                    "action": "撬开石牢门锁",
                    "target": "stone_cell_lock",
                    "suggested_skill": "improvisation",
                    "suggested_context_modifier": -5,
                    "modifier_reasons": [
                        {
                            "reason_tag": "dramatic_bonus",
                            "source_id": "scene_stone_cell_lock",
                        }
                    ],
                    "authorization": "user_declared",
                    "authorization_evidence": "我尝试撬开石牢门锁",
                },
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, "invalid_arguments")
            self.assertEqual(read_json(paths.check_records_file)["records"], [])

    def test_request_check_rejects_removed_authorization(self) -> None:
        """已移出 MVP 的既有任务授权不能再进入检定。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={"request_check": 1},
                ),
            )

            result = session.execute(
                "request_check",
                {
                    "actor_id": "user",
                    "actor_type": "user",
                    "action": "撬开石牢门锁",
                    "target": "stone_cell_lock",
                    "suggested_skill": "improvisation",
                    "suggested_context_modifier": 0,
                    "modifier_reasons": [],
                    "authorization": "standing_assignment",
                    "authorization_evidence": "我尝试撬开石牢门锁",
                },
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, "invalid_arguments")
            self.assertEqual(read_json(paths.check_records_file)["records"], [])

    def test_request_check_rejects_invalid_scalar_argument_types(self) -> None:
        """所有顶层标量字段都必须符合公开 schema 的基本类型。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={"request_check": 1},
                ),
            )
            valid_arguments = {
                "actor_id": "user",
                "actor_type": "user",
                "action": "撬开石牢门锁",
                "target": "stone_cell_lock",
                "suggested_skill": "improvisation",
                "suggested_context_modifier": 0,
                "modifier_reasons": [],
                "authorization": "user_declared",
                "authorization_evidence": "我尝试撬开石牢门锁",
            }

            for field, invalid_value in (
                ("actor_id", 1),
                ("action", ["撬开石牢门锁"]),
                ("target", {"id": "stone_cell_lock"}),
                ("suggested_skill", 1),
                ("authorization_evidence", None),
            ):
                with self.subTest(field=field):
                    result = session.execute(
                        "request_check",
                        {**valid_arguments, field: invalid_value},
                    )

                    self.assertFalse(result.ok)
                    self.assertEqual(result.error.code, "invalid_arguments")

            self.assertEqual(read_json(paths.check_records_file)["records"], [])

    def test_apply_effect_rejects_malformed_arguments_without_consuming_quota(
        self,
    ) -> None:
        """提交工具的参数预检失败不会调用 StateCommitter。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={"apply_effect": 1},
                ),
            )

            invalid = session.execute("apply_effect", {})
            dispatched = session.execute(
                "apply_effect",
                {
                    "expected_state_version": 0,
                    "source_type": "module_event",
                    "source_id": "missing_event",
                    "effect_id": "unlock_stone_cell_lock",
                    "reason": "测试未知事件来源",
                },
            )

            self.assertFalse(invalid.ok)
            self.assertEqual(invalid.error.code, "invalid_arguments")
            self.assertTrue(invalid.error.retryable)
            self.assertTrue(dispatched.ok)
            self.assertEqual(dispatched.data["status"], "rejected")
            self.assertEqual(dispatched.data["error_code"], "unknown_source")
            self.assertEqual(read_json(paths.game_state_file)["state_version"], 0)

    def test_apply_effect_rejects_tool_call_as_an_effect_source(self) -> None:
        """工具调用标识只能追踪执行，不能授权正式状态效果。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={"apply_effect": 1},
                ),
            )

            result = session.execute(
                "apply_effect",
                {
                    "expected_state_version": 0,
                    "source_type": "tool_call",
                    "source_id": "tool_turn_0001_01",
                    "effect_id": "unlock_stone_cell_lock",
                    "reason": "不能用轨迹代替效果来源",
                },
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, "invalid_arguments")
            self.assertEqual(read_json(paths.game_state_file)["state_version"], 0)

    def test_apply_effect_idempotent_retry_reaches_state_committer(self) -> None:
        """旧版本的同一效果重试应返回 already_applied，而非被会话拦截。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={
                        "request_check": 1,
                        "apply_effect": 2,
                    },
                ),
            )
            check = session.execute(
                "request_check",
                {
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
            )
            arguments = {
                "expected_state_version": 0,
                "source_type": "check",
                "source_id": check.data["check_id"],
                "effect_id": "unlock_stone_cell_lock",
                "reason": "门锁检定成功",
            }

            first = session.execute("apply_effect", arguments)
            retry = session.execute("apply_effect", arguments)

            self.assertTrue(first.ok)
            self.assertEqual(first.data["status"], "applied")
            self.assertTrue(retry.ok)
            self.assertEqual(retry.data["status"], "already_applied")
            self.assertEqual(retry.data["commit_id"], "commit_game_0001_0001")
            self.assertEqual(retry.data["state_version"], 1)
            self.assertEqual(session.current_state_version, 1)

    def test_successful_apply_effect_is_recorded_in_session_trace(self) -> None:
        """提交工具会保留规范化申请和返回结果以供 GameEngine 校验。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={
                        "request_check": 1,
                        "apply_effect": 1,
                    },
                ),
            )
            check = session.execute(
                "request_check",
                {
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
            )
            effect = session.execute(
                "apply_effect",
                {
                    "expected_state_version": 0,
                    "source_type": "check",
                    "source_id": check.data["check_id"],
                    "effect_id": "unlock_stone_cell_lock",
                    "reason": "门锁检定成功",
                },
            )

            self.assertEqual(len(session.trace), 2)
            entry = session.trace[1]
            self.assertEqual(entry.sequence, 2)
            self.assertEqual(entry.tool_call_id, effect.tool_call_id)
            self.assertEqual(entry.tool_name, "apply_effect")
            self.assertIsInstance(entry.normalized_arguments, ApplyEffectArgs)
            self.assertTrue(entry.dispatched)
            self.assertEqual(entry.tool_result, effect)

    def test_turn_session_uses_an_isolated_initial_state_snapshot(self) -> None:
        """调用方后续改写原始状态对象不能改变本回合的检定上下文。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={"request_check": 1},
                ),
            )
            state["current_scene"] = "bone_market"

            result = session.execute(
                "request_check",
                {
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
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.data["scene_id"], "stone_cell")

    def test_request_check_rejects_out_of_range_context_modifier(self) -> None:
        """工具层只接受当前公开的建议修正输入范围。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我尝试撬开石牢门锁。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={"request_check": 1},
                ),
            )

            result = session.execute(
                "request_check",
                {
                    "actor_id": "user",
                    "actor_type": "user",
                    "action": "撬开石牢门锁",
                    "target": "stone_cell_lock",
                    "suggested_skill": "improvisation",
                    "suggested_context_modifier": 11,
                    "modifier_reasons": [],
                    "authorization": "user_declared",
                    "authorization_evidence": "我尝试撬开石牢门锁",
                },
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, "invalid_arguments")
            self.assertEqual(read_json(paths.check_records_file)["records"], [])

    def test_apply_effect_returns_no_state_change_as_success(self) -> None:
        """合法但未改变状态的模组效果仍由工具成功返回。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = AppPaths(
                data_dir=root / "data",
                runtime_dir=root / "var",
            )
            source_paths = AppPaths()
            module = read_json(source_paths.module_file)
            module["effect_definitions"]["discover_known_clue"] = {
                "operations": [
                    {
                        "path": "clues_found",
                        "operation": "add_unique",
                        "value": "akalir_seal",
                    }
                ]
            }
            module["event_rules"] = [
                {
                    "event_rule_id": "discover_known_clue",
                    "scene_id": "stone_cell",
                    "repeat_policy": "once_per_game",
                    "requirements": {},
                    "effect_id": "discover_known_clue",
                }
            ]
            write_json(paths.module_file, module)
            write_json(
                paths.characters_file,
                read_json(source_paths.characters_file),
            )
            state = GameEngine(paths).initialize()
            state["clues_found"] = ["akalir_seal"]
            write_json(paths.game_state_file, state)
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(
                    paths=paths,
                    check_ledger=ledger,
                ),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我重新查看已经发现的门印。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={"apply_effect": 1},
                ),
            )

            result = session.execute(
                "apply_effect",
                {
                    "expected_state_version": 0,
                    "source_type": "module_event",
                    "source_id": "discover_known_clue",
                    "effect_id": "discover_known_clue",
                    "reason": "重复确认已经获得的门印",
                },
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.data["status"], "no_state_change")
            self.assertIsNone(result.data["commit_id"])
            self.assertEqual(result.data["state_version"], 0)
            self.assertIsNone(result.data["context_delta"])
            self.assertEqual(session.current_state_version, 0)

    def test_apply_effect_derives_context_delta_from_committed_changes(self) -> None:
        """已提交的线索和场景变化应生成可信模型上下文增量。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = AppPaths(
                data_dir=root / "data",
                runtime_dir=root / "var",
            )
            source_paths = AppPaths()
            module = read_json(source_paths.module_file)
            module["scenes"].append(
                {
                    "scene_id": "bone_market",
                    "public_facts": ["白骨街的摊棚挤在狭窄水道两侧。"],
                    "interactions": [],
                    "boundaries": ["拉提的耳目正在附近巡查。"],
                    "discovery_opportunities": [],
                }
            )
            module["clue_definitions"]["akalir_seal"] = {
                "title": "阿卡利尔门印",
                "public_text": "石门上的旧印记指向白骨街。",
            }
            module["scene_threat_floors"] = {"bone_market": 0}
            module["effect_definitions"]["enter_bone_market_with_clue"] = {
                "operations": [
                    {
                        "path": "clues_found",
                        "operation": "add_unique",
                        "value": "akalir_seal",
                    },
                    {
                        "path": "current_scene",
                        "operation": "set",
                        "value": "bone_market",
                    },
                ]
            }
            module["event_rules"] = [
                {
                    "event_rule_id": "follow_drain_route",
                    "scene_id": "stone_cell",
                    "repeat_policy": "once_per_game",
                    "requirements": {},
                    "effect_id": "enter_bone_market_with_clue",
                }
            ]
            write_json(paths.module_file, module)
            write_json(
                paths.characters_file,
                read_json(source_paths.characters_file),
            )
            state = GameEngine(paths).initialize()
            ledger = CheckLedger(paths.check_records_file)
            executor = ToolExecutor(
                paths=paths,
                rule_engine=RuleEngine(random=Random(1), ledger=ledger),
                state_committer=StateCommitter(paths=paths, check_ledger=ledger),
            )
            session = executor.start_turn(
                TurnContext(
                    turn_id="turn_0001",
                    input_text="我们沿排水痕迹离开石牢。",
                    initial_game_state=state,
                    max_tool_steps=8,
                    tool_limits={"apply_effect": 1},
                )
            )

            result = session.execute(
                "apply_effect",
                {
                    "expected_state_version": 0,
                    "source_type": "module_event",
                    "source_id": "follow_drain_route",
                    "effect_id": "enter_bone_market_with_clue",
                    "reason": "沿已经确认的排水路线前进",
                },
            )

            self.assertTrue(result.ok)
            assert result.data is not None
            self.assertEqual(
                result.data["context_delta"],
                {
                    "revealed_clues": [
                        {
                            "clue_id": "akalir_seal",
                            "title": "阿卡利尔门印",
                            "public_text": "石门上的旧印记指向白骨街。",
                        }
                    ],
                    "entered_scene": {
                        "scene_id": "bone_market",
                        "public_facts": ["白骨街的摊棚挤在狭窄水道两侧。"],
                        "interactions": [],
                        "boundaries": ["拉提的耳目正在附近巡查。"],
                        "discovery_opportunities": [],
                    },
                },
            )


if __name__ == "__main__":
    unittest.main()
