import tempfile
import unittest
from pathlib import Path
from random import Random
from typing import cast
from unittest.mock import Mock

from monmusu_agent.config import AppPaths
from monmusu_agent.engine import GameEngine
from monmusu_agent.rules import (
    CheckContext,
    CheckLedger,
    CheckRule,
    ModifierReason,
    ModifierSource,
    RequestCheckArgs,
    RuleEngine,
    RuleValidationError,
    build_check_context,
    clamp,
    clamp_context_modifier,
)
from monmusu_agent.storage import read_json


class RuleEngineTest(unittest.TestCase):
    def test_check_context_is_deeply_read_only(self) -> None:
        """可信检定上下文的嵌套映射也不能在创建后修改。"""

        context = CheckContext(
            game_id="game_0001",
            turn_id="turn_0001",
            module_id="escape_thalarion",
            scene_id="stone_cell",
            input_text="我观察石牢门。",
            user_id="user",
            character_ids=frozenset(),
            actor_skills={"user": {"willpower": 60}},
            rules_by_target={},
            modifier_sources={},
        )

        actor_skills = cast(dict[str, int], context.actor_skills["user"])
        rules_by_target = cast(dict[str, CheckRule], context.rules_by_target)
        modifier_sources = cast(
            dict[str, ModifierSource],
            context.modifier_sources,
        )

        with self.assertRaises(TypeError):
            actor_skills["willpower"] = 99
        with self.assertRaises(TypeError):
            rules_by_target["fake"] = CheckRule(
                rule_id="fake",
                target_id="fake",
                allowed_skills=frozenset({"willpower"}),
                difficulty_modifier=0,
            )
        with self.assertRaises(TypeError):
            modifier_sources["fake"] = ModifierSource(
                source_id="fake",
                allowed_reason_tags=frozenset({"good_position"}),
            )

    def test_clamp_limits_value(self) -> None:
        self.assertEqual(clamp(1, 5, 95), 5)
        self.assertEqual(clamp(100, 5, 95), 95)
        self.assertEqual(clamp(50, 5, 95), 50)

    def test_clamp_context_modifier_uses_actor_specific_bounds(self) -> None:
        self.assertEqual(clamp_context_modifier("user", 10), 10)
        self.assertEqual(clamp_context_modifier("user", -10), -10)
        self.assertEqual(clamp_context_modifier("character", 10), 5)
        self.assertEqual(clamp_context_modifier("character", -10), -5)
        self.assertEqual(clamp_context_modifier("user", 3), 3)
        self.assertEqual(clamp_context_modifier("character", -2), -2)

    def test_clamp_context_modifier_rejects_unknown_actor_type(self) -> None:
        with self.assertRaises(ValueError):
            clamp_context_modifier("unknown", 0)

    def test_roll_check_clamps_target_after_applying_modifiers(self) -> None:
        engine = RuleEngine(random=Random(1))

        upper_bound_result = engine.roll_check(base_skill=90, difficulty_modifier=10)
        lower_bound_result = engine.roll_check(base_skill=10, context_modifier=-10)

        self.assertEqual(upper_bound_result.target, 95)
        self.assertEqual(lower_bound_result.target, 5)

    def test_roll_check_uses_low_roll_success_rule(self) -> None:
        result = RuleEngine(random=Random(1)).roll_check(base_skill=60)

        self.assertEqual(result.roll, 18)
        self.assertEqual(result.target, 60)
        self.assertEqual(result.outcome, "success")

    def test_roll_check_returns_critical_success_for_roll_at_most_five(self) -> None:
        """边界测试"""
        result = RuleEngine(random=Random(43)).roll_check(base_skill=60)

        self.assertEqual(result.roll, 5)
        self.assertEqual(result.outcome, "critical_success")

    def test_roll_check_returns_fumble_for_roll_above_ninety_five(self) -> None:
        """边界测试"""
        result = RuleEngine(random=Random(26)).roll_check(base_skill=60)

        self.assertEqual(result.roll, 96)
        self.assertEqual(result.outcome, "fumble")

    def test_roll_check_returns_failure_when_roll_exceeds_target(self) -> None:
        result = RuleEngine(random=Random(1)).roll_check(base_skill=10)

        self.assertGreater(result.roll, 5)
        self.assertLessEqual(result.roll, 95)
        self.assertEqual(result.target, 10)
        self.assertGreater(result.roll, result.target)
        self.assertEqual(result.outcome, "failure")

    def test_resolve_check_persists_authorized_static_check(self) -> None:
        """合法检定会生成唯一结果，并立即保存到独立账本。"""

        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "check_records.json"
            ledger = CheckLedger(ledger_path)
            engine = RuleEngine(random=Random(1), ledger=ledger)
            request = RequestCheckArgs(
                actor_id="user",
                actor_type="user",
                action="凭意志推开石门",
                target="stone_cell_door",
                suggested_skill="willpower",
                suggested_context_modifier=10,
                modifier_reasons=(
                    ModifierReason(
                        reason_tag="relevant_clue",
                        source_id="clue_loose_hinge",
                    ),
                ),
                authorization="user_declared",
                authorization_evidence="我凭意志推开石门",
            )
            context = CheckContext(
                game_id="game_0001",
                turn_id="turn_0001",
                module_id="escape_thalarion",
                scene_id="stone_cell",
                input_text="我凭意志推开石门。",
                user_id="user",
                character_ids=frozenset(),
                actor_skills={"user": {"willpower": 60}},
                rules_by_target={
                    "stone_cell_door": CheckRule(
                        rule_id="force_stone_cell_door",
                        target_id="stone_cell_door",
                        allowed_skills=frozenset({"willpower"}),
                        difficulty_modifier=-5,
                    ),
                },
                modifier_sources={
                    "clue_loose_hinge": ModifierSource(
                        source_id="clue_loose_hinge",
                        allowed_reason_tags=frozenset({"relevant_clue"}),
                    ),
                },
            )

            result = engine.resolve_check(request, context)

            self.assertEqual(result.check_id, "check_game_0001_0001")
            self.assertEqual(result.skill, "willpower")
            self.assertEqual(result.base_skill, 60)
            self.assertEqual(result.difficulty_modifier, -5)
            self.assertEqual(result.context_modifier, 10)
            self.assertEqual(result.target, 65)
            self.assertEqual(result.roll, 18)
            self.assertEqual(result.outcome, "success")
            self.assertEqual(ledger.get(result.check_id), result)
            self.assertEqual(CheckLedger(ledger_path).get(result.check_id), result)

    def test_resolve_check_snapshots_allowed_effects_for_outcome(self) -> None:
        """正式检定会冻结实际 outcome 对应的效果授权。"""

        with tempfile.TemporaryDirectory() as directory:
            ledger = CheckLedger(Path(directory) / "check_records.json")
            engine = RuleEngine(random=Random(1), ledger=ledger)
            request = RequestCheckArgs(
                actor_id="user",
                actor_type="user",
                action="撬开石牢门锁",
                target="stone_cell_lock",
                suggested_skill="improvisation",
                suggested_context_modifier=0,
                modifier_reasons=(),
                authorization="user_declared",
                authorization_evidence="撬开石牢门锁",
            )
            context = CheckContext(
                game_id="game_0001",
                turn_id="turn_0001",
                module_id="escape_thalarion",
                scene_id="stone_cell",
                input_text="我尝试撬开石牢门锁。",
                user_id="user",
                character_ids=frozenset(),
                actor_skills={"user": {"improvisation": 45}},
                rules_by_target={
                    "stone_cell_lock": CheckRule(
                        rule_id="pry_stone_cell_lock",
                        target_id="stone_cell_lock",
                        allowed_skills=frozenset({"improvisation"}),
                        difficulty_modifier=-10,
                        effects_by_outcome={
                            "critical_success": ("unlock_stone_cell_lock",),
                            "success": ("unlock_stone_cell_lock",),
                            "failure": (),
                            "fumble": ("raise_threat_from_lock_noise",),
                        },
                    ),
                },
                modifier_sources={},
            )

            result = engine.resolve_check(request, context)

            self.assertEqual(result.outcome, "success")
            self.assertEqual(
                result.allowed_effect_ids,
                ("unlock_stone_cell_lock",),
            )
            self.assertEqual(ledger.get(result.check_id), result)

    def test_resolve_check_rejects_modifier_without_reason_or_ledger_record(
        self,
    ) -> None:
        """非零语境修正必须有可信理由，拒绝时不得创建检定记录。"""

        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "check_records.json"
            engine = RuleEngine(random=Random(1), ledger=CheckLedger(ledger_path))
            request = RequestCheckArgs(
                actor_id="user",
                actor_type="user",
                action="凭意志推开石门",
                target="stone_cell_door",
                suggested_skill="willpower",
                suggested_context_modifier=5,
                modifier_reasons=(),
                authorization="user_declared",
                authorization_evidence="我凭意志推开石门",
            )
            context = CheckContext(
                game_id="game_0001",
                turn_id="turn_0001",
                module_id="escape_thalarion",
                scene_id="stone_cell",
                input_text="我凭意志推开石门。",
                user_id="user",
                character_ids=frozenset(),
                actor_skills={"user": {"willpower": 60}},
                rules_by_target={
                    "stone_cell_door": CheckRule(
                        rule_id="force_stone_cell_door",
                        target_id="stone_cell_door",
                        allowed_skills=frozenset({"willpower"}),
                        difficulty_modifier=-5,
                    ),
                },
                modifier_sources={},
            )

            with self.assertRaises(RuleValidationError):
                engine.resolve_check(request, context)

            self.assertFalse(ledger_path.exists())

    def test_resolve_check_allows_delegated_character_action_with_clamped_modifier(
        self,
    ) -> None:
        """已授权队友可检定，但建议修正会被限制在角色上限。"""

        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "check_records.json"
            ledger = CheckLedger(ledger_path)
            engine = RuleEngine(random=Random(1), ledger=ledger)
            request = RequestCheckArgs(
                actor_id="vespera",
                actor_type="character",
                action="撑住落下的横梁",
                target="fallen_beam",
                suggested_skill="athletics",
                suggested_context_modifier=10,
                modifier_reasons=(
                    ModifierReason(
                        reason_tag="good_position",
                        source_id="scene_fallen_beam",
                    ),
                ),
                authorization="user_delegated",
                authorization_evidence="薇斯佩拉，撑住横梁",
            )
            context = CheckContext(
                game_id="game_0001",
                turn_id="turn_0001",
                module_id="escape_thalarion",
                scene_id="stone_cell",
                input_text="薇斯佩拉，撑住横梁。",
                user_id="user",
                character_ids=frozenset({"vespera"}),
                actor_skills={"vespera": {"athletics": 55}},
                rules_by_target={
                    "fallen_beam": CheckRule(
                        rule_id="hold_fallen_beam",
                        target_id="fallen_beam",
                        allowed_skills=frozenset({"athletics"}),
                        difficulty_modifier=0,
                    ),
                },
                modifier_sources={
                    "scene_fallen_beam": ModifierSource(
                        source_id="scene_fallen_beam",
                        allowed_reason_tags=frozenset({"good_position"}),
                    ),
                },
            )

            result = engine.resolve_check(request, context)

            self.assertEqual(result.actor_id, "vespera")
            self.assertEqual(result.context_modifier, 5)
            self.assertEqual(result.target, 60)
            self.assertEqual(result.outcome, "success")
            self.assertEqual(ledger.get(result.check_id), result)

    def test_resolve_check_allows_ruleless_user_action(self) -> None:
        """没有静态目标的玩家行动仍可使用可信角色技能检定。"""

        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "check_records.json"
            ledger = CheckLedger(ledger_path)
            engine = RuleEngine(random=Random(1), ledger=ledger)
            request = RequestCheckArgs(
                actor_id="user",
                actor_type="user",
                action="集中意志抵抗梦中低语",
                target=None,
                suggested_skill="willpower",
                suggested_context_modifier=0,
                modifier_reasons=(),
                authorization="user_declared",
                authorization_evidence="我集中意志抵抗梦中低语",
            )
            context = CheckContext(
                game_id="game_0001",
                turn_id="turn_0001",
                module_id="escape_thalarion",
                scene_id="stone_cell",
                input_text="我集中意志抵抗梦中低语。",
                user_id="user",
                character_ids=frozenset(),
                actor_skills={"user": {"willpower": 60}},
                rules_by_target={},
                modifier_sources={},
            )

            result = engine.resolve_check(request, context)

            self.assertIsNone(result.rule_id)
            self.assertIsNone(result.target_id)
            self.assertEqual(result.allowed_effect_ids, ())
            self.assertEqual(result.difficulty_modifier, 0)
            self.assertEqual(result.target, 60)
            self.assertEqual(ledger.get(result.check_id), result)

    def test_resolve_check_uses_initialized_thalarion_rule_data(self) -> None:
        """石牢门锁检定从静态角色和模组数据解析技能与难度。"""

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths(runtime_dir=Path(directory))
            state = GameEngine(paths).initialize()
            context = build_check_context(
                game_state=state,
                module=read_json(paths.module_file),
                character_profiles=read_json(paths.characters_file),
                turn_id="turn_0001",
                input_text="我试着撬开石牢门锁。",
            )
            request = RequestCheckArgs(
                actor_id="user",
                actor_type="user",
                action="撬开石牢门锁",
                target="stone_cell_lock",
                suggested_skill="improvisation",
                suggested_context_modifier=0,
                modifier_reasons=(),
                authorization="user_declared",
                authorization_evidence="我试着撬开石牢门锁",
            )
            engine = RuleEngine(
                random=Random(1),
                ledger=CheckLedger(paths.check_records_file),
            )

            result = engine.resolve_check(request, context)

            self.assertEqual(result.skill, "improvisation")
            self.assertEqual(result.base_skill, 45)
            self.assertEqual(result.difficulty_modifier, -10)
            self.assertEqual(result.target, 35)
            self.assertEqual(result.outcome, "success")
            self.assertEqual(
                result.allowed_effect_ids,
                ("unlock_stone_cell_lock",),
            )

    def test_resolve_check_does_not_roll_without_ledger(self) -> None:
        """无法保存权威记录时，规则引擎不能提前掷骰。"""

        random_source = Mock(spec=Random)
        random_source.randint.return_value = 18
        engine = RuleEngine(random=random_source)
        request = RequestCheckArgs(
            actor_id="user",
            actor_type="user",
            action="集中意志抵抗梦中低语",
            target=None,
            suggested_skill="willpower",
            suggested_context_modifier=0,
            modifier_reasons=(),
            authorization="user_declared",
            authorization_evidence="我集中意志抵抗梦中低语",
        )
        context = CheckContext(
            game_id="game_0001",
            turn_id="turn_0001",
            module_id="escape_thalarion",
            scene_id="stone_cell",
            input_text="我集中意志抵抗梦中低语。",
            user_id="user",
            character_ids=frozenset(),
            actor_skills={"user": {"willpower": 60}},
            rules_by_target={},
            modifier_sources={},
        )

        with self.assertRaises(RuleValidationError):
            engine.resolve_check(request, context)

        random_source.randint.assert_not_called()


if __name__ == "__main__":
    unittest.main()
