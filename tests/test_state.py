import tempfile
import unittest
from pathlib import Path
from random import Random

from monmusu_agent.config import AppPaths
from monmusu_agent.engine import GameEngine
from monmusu_agent.rules import (
    CheckLedger,
    RequestCheckArgs,
    RuleEngine,
    build_check_context,
)
from monmusu_agent.state import (
    ApplyEffectArgs,
    CheckEffectSource,
    ModuleDefinitionError,
    ModuleEventSource,
    StateChange,
    StateCommitter,
)
from monmusu_agent.storage import read_json, write_json


class StateCommitterTest(unittest.TestCase):
    @staticmethod
    def _successful_lock_check(
        directory: str,
    ) -> tuple[AppPaths, StateCommitter, str]:
        paths = AppPaths(runtime_dir=Path(directory))
        state = GameEngine(paths).initialize()
        module = read_json(paths.module_file)
        characters = read_json(paths.characters_file)
        ledger = CheckLedger(paths.check_records_file)
        context = build_check_context(
            game_state=state,
            module=module,
            character_profiles=characters,
            turn_id="turn_0001",
            input_text="我尝试撬开石牢门锁。",
        )
        check = RuleEngine(random=Random(1), ledger=ledger).resolve_check(
            RequestCheckArgs(
                actor_id="user",
                actor_type="user",
                action="撬开石牢门锁",
                target="stone_cell_lock",
                suggested_skill="improvisation",
                suggested_context_modifier=0,
                modifier_reasons=(),
                authorization="user_declared",
                authorization_evidence="我尝试撬开石牢门锁",
            ),
            context,
        )
        return paths, StateCommitter(paths=paths, check_ledger=ledger), check.check_id

    @staticmethod
    def _committer_with_transition_event(
        directory: str,
    ) -> tuple[AppPaths, StateCommitter]:
        root = Path(directory)
        data_dir = root / "data"
        runtime_dir = root / "var"
        source_paths = AppPaths()
        module = read_json(source_paths.module_file)
        module["scene_threat_floors"] = {
            "stone_cell": 0,
            "bone_market": 2,
        }
        module["effect_definitions"]["transition_to_bone_market"] = {
            "operations": [
                {
                    "path": "current_scene",
                    "operation": "set",
                    "value": "bone_market",
                },
                {
                    "path": "accessible_locations",
                    "operation": "add_unique",
                    "value": "bone_market",
                },
                {
                    "path": "threat_clock.value",
                    "operation": "ensure_at_least",
                    "value": 2,
                },
            ]
        }
        module["event_rules"] = [
            {
                "event_rule_id": "enter_bone_market",
                "scene_id": "stone_cell",
                "repeat_policy": "once_per_game",
                "requirements": {
                    "required_flags": {
                        "stone_cell_lock_unlocked": True,
                    }
                },
                "effect_id": "transition_to_bone_market",
            }
        ]
        paths = AppPaths(data_dir=data_dir, runtime_dir=runtime_dir)
        write_json(paths.module_file, module)
        write_json(
            paths.characters_file,
            read_json(source_paths.characters_file),
        )
        state = GameEngine(paths).initialize()
        state["flags"]["stone_cell_lock_unlocked"] = True
        write_json(paths.game_state_file, state)
        ledger = CheckLedger(paths.check_records_file)
        return paths, StateCommitter(paths=paths, check_ledger=ledger)

    def test_authorized_check_effect_updates_game_state(self) -> None:
        """检定授权的固定效果会原子写入状态并返回机械变化。"""

        with tempfile.TemporaryDirectory() as directory:
            paths, committer, check_id = self._successful_lock_check(directory)

            result = committer.apply_effect(
                ApplyEffectArgs(
                    expected_state_version=0,
                    source=CheckEffectSource(check_id=check_id),
                    effect_id="unlock_stone_cell_lock",
                    reason="开锁检定成功",
                ),
                turn_id="turn_0001",
            )

            self.assertEqual(result.status, "applied")
            self.assertEqual(result.commit_id, "commit_game_0001_0001")
            self.assertEqual(result.state_version, 1)
            self.assertEqual(
                result.changes,
                (
                    StateChange(
                        path="flags.stone_cell_lock_unlocked",
                        before=None,
                        after=True,
                    ),
                ),
            )
            persisted = read_json(paths.game_state_file)
            self.assertTrue(persisted["flags"]["stone_cell_lock_unlocked"])
            self.assertEqual(persisted["state_version"], 1)
            self.assertEqual(
                persisted["commit_metadata"]["applied_effects"]
                ["check:check_game_0001_0001:unlock_stone_cell_lock"]
                ["commit_id"],
                "commit_game_0001_0001",
            )

    def test_identical_retry_returns_original_commit_without_second_write(self) -> None:
        """响应丢失后的相同效果重试不会重复结算。"""

        with tempfile.TemporaryDirectory() as directory:
            paths, committer, check_id = self._successful_lock_check(directory)
            args = ApplyEffectArgs(
                expected_state_version=0,
                source=CheckEffectSource(check_id=check_id),
                effect_id="unlock_stone_cell_lock",
                reason="开锁检定成功",
            )
            committer.apply_effect(args, turn_id="turn_0001")

            retry = committer.apply_effect(args, turn_id="turn_0001")

            self.assertEqual(retry.status, "already_applied")
            self.assertEqual(retry.commit_id, "commit_game_0001_0001")
            self.assertEqual(retry.state_version, 1)
            self.assertEqual(
                retry.changes,
                (
                    StateChange(
                        path="flags.stone_cell_lock_unlocked",
                        before=None,
                        after=True,
                    ),
                ),
            )
            self.assertEqual(read_json(paths.game_state_file)["state_version"], 1)

    def test_stale_check_returns_structured_rejection(self) -> None:
        """离开原回合或场景后，旧检定不能再提交状态效果。"""

        with tempfile.TemporaryDirectory() as directory:
            paths, committer, check_id = self._successful_lock_check(directory)

            result = committer.apply_effect(
                ApplyEffectArgs(
                    expected_state_version=0,
                    source=CheckEffectSource(check_id=check_id),
                    effect_id="unlock_stone_cell_lock",
                    reason="延后提交旧检定",
                ),
                turn_id="turn_0002",
            )

            self.assertEqual(result.status, "rejected")
            self.assertEqual(result.error_code, "stale_source")
            self.assertEqual(read_json(paths.game_state_file)["state_version"], 0)

    def test_module_event_applies_transition_when_requirements_are_met(self) -> None:
        """静态模组事件会验证条件并原子提交场景推进。"""

        with tempfile.TemporaryDirectory() as directory:
            paths, committer = self._committer_with_transition_event(directory)

            result = committer.apply_effect(
                ApplyEffectArgs(
                    expected_state_version=0,
                    source=ModuleEventSource(event_rule_id="enter_bone_market"),
                    effect_id="transition_to_bone_market",
                    reason="队伍已经打开石牢门锁",
                ),
                turn_id="turn_0001",
            )

            self.assertEqual(result.status, "applied")
            self.assertEqual(
                result.changes,
                (
                    StateChange("current_scene", "stone_cell", "bone_market"),
                    StateChange(
                        "accessible_locations",
                        ["stone_cell"],
                        ["stone_cell", "bone_market"],
                    ),
                    StateChange("threat_clock.value", 0, 2),
                ),
            )
            persisted = read_json(paths.game_state_file)
            self.assertEqual(persisted["current_scene"], "bone_market")
            self.assertEqual(
                persisted["accessible_locations"],
                ["stone_cell", "bone_market"],
            )
            self.assertEqual(persisted["threat_clock"]["value"], 2)

    def test_once_per_turn_event_can_apply_again_in_a_later_turn(self) -> None:
        """每回合事件同轮幂等，但会在下一回合形成新来源。"""

        with tempfile.TemporaryDirectory() as directory:
            paths, _ = self._committer_with_transition_event(directory)
            module = read_json(paths.module_file)
            module["effect_definitions"]["raise_threat_from_delay"] = {
                "operations": [
                    {
                        "path": "threat_clock.value",
                        "operation": "increment",
                        "value": 1,
                    }
                ]
            }
            module["event_rules"].append(
                {
                    "event_rule_id": "linger_in_bone_market",
                    "scene_id": "bone_market",
                    "repeat_policy": "once_per_turn",
                    "requirements": {},
                    "effect_id": "raise_threat_from_delay",
                }
            )
            write_json(paths.module_file, module)
            state = read_json(paths.game_state_file)
            state["current_scene"] = "bone_market"
            state["threat_clock"]["value"] = 2
            write_json(paths.game_state_file, state)
            committer = StateCommitter(
                paths=paths,
                check_ledger=CheckLedger(paths.check_records_file),
            )
            args = ApplyEffectArgs(
                expected_state_version=0,
                source=ModuleEventSource(
                    event_rule_id="linger_in_bone_market",
                ),
                effect_id="raise_threat_from_delay",
                reason="队伍在危险灯光下停留过久",
            )

            first = committer.apply_effect(args, turn_id="turn_0001")
            retry = committer.apply_effect(args, turn_id="turn_0001")
            second = committer.apply_effect(
                ApplyEffectArgs(
                    expected_state_version=1,
                    source=args.source,
                    effect_id=args.effect_id,
                    reason=args.reason,
                ),
                turn_id="turn_0002",
            )

            self.assertEqual(first.status, "applied")
            self.assertEqual(retry.status, "already_applied")
            self.assertEqual(second.status, "applied")
            self.assertEqual(second.commit_id, "commit_game_0001_0002")
            persisted = read_json(paths.game_state_file)
            self.assertEqual(persisted["state_version"], 2)
            self.assertEqual(persisted["threat_clock"]["value"], 4)

    def test_threat_reduction_at_scene_floor_preserves_opportunity(self) -> None:
        """处于场景下限时不消耗整局一次的危机降低机会。"""

        with tempfile.TemporaryDirectory() as directory:
            paths, _ = self._committer_with_transition_event(directory)
            module = read_json(paths.module_file)
            module["effect_definitions"].update(
                {
                    "raise_threat_from_delay": {
                        "operations": [
                            {
                                "path": "threat_clock.value",
                                "operation": "increment",
                                "value": 1,
                            }
                        ]
                    },
                    "reduce_threat_attention": {
                        "operations": [
                            {
                                "path": "threat_clock.value",
                                "operation": "increment",
                                "value": -1,
                            }
                        ]
                    },
                }
            )
            module["event_rules"].extend(
                [
                    {
                        "event_rule_id": "linger_in_bone_market",
                        "scene_id": "bone_market",
                        "repeat_policy": "once_per_turn",
                        "requirements": {},
                        "effect_id": "raise_threat_from_delay",
                    },
                    {
                        "event_rule_id": "reduce_threat_once",
                        "scene_id": "bone_market",
                        "repeat_policy": "once_per_game",
                        "requirements": {
                            "threat_above_scene_floor": True,
                        },
                        "effect_id": "reduce_threat_attention",
                    },
                ]
            )
            write_json(paths.module_file, module)
            state = read_json(paths.game_state_file)
            state["current_scene"] = "bone_market"
            state["threat_clock"]["value"] = 2
            write_json(paths.game_state_file, state)
            committer = StateCommitter(
                paths=paths,
                check_ledger=CheckLedger(paths.check_records_file),
            )
            reduce_args = ApplyEffectArgs(
                expected_state_version=0,
                source=ModuleEventSource(event_rule_id="reduce_threat_once"),
                effect_id="reduce_threat_attention",
                reason="主动放弃无关财宝，延缓拉提的注意",
            )

            no_change = committer.apply_effect(
                reduce_args,
                turn_id="turn_0001",
            )
            raised = committer.apply_effect(
                ApplyEffectArgs(
                    expected_state_version=0,
                    source=ModuleEventSource(
                        event_rule_id="linger_in_bone_market",
                    ),
                    effect_id="raise_threat_from_delay",
                    reason="队伍停留过久",
                ),
                turn_id="turn_0001",
            )
            reduced = committer.apply_effect(
                ApplyEffectArgs(
                    expected_state_version=1,
                    source=reduce_args.source,
                    effect_id=reduce_args.effect_id,
                    reason=reduce_args.reason,
                ),
                turn_id="turn_0001",
            )

            self.assertEqual(no_change.status, "no_state_change")
            self.assertIsNone(no_change.commit_id)
            self.assertEqual(no_change.state_version, 0)
            self.assertEqual(raised.status, "applied")
            self.assertEqual(reduced.status, "applied")
            persisted = read_json(paths.game_state_file)
            self.assertEqual(persisted["state_version"], 2)
            self.assertEqual(persisted["threat_clock"]["value"], 2)

    def test_compound_effect_is_rejected_atomically_when_remove_is_missing(
        self,
    ) -> None:
        """复合效果不能跳过缺失的 remove 并部分提交其他操作。"""

        with tempfile.TemporaryDirectory() as directory:
            paths, _ = self._committer_with_transition_event(directory)
            module = read_json(paths.module_file)
            module["effect_definitions"]["invalid_condition_exchange"] = {
                "operations": [
                    {
                        "path": "flags.partial_write",
                        "operation": "set",
                        "value": True,
                    },
                    {
                        "path": "user_character.conditions",
                        "operation": "remove",
                        "value": "nonexistent_condition",
                    },
                ]
            }
            module["event_rules"].append(
                {
                    "event_rule_id": "invalid_condition_exchange",
                    "scene_id": "stone_cell",
                    "repeat_policy": "once_per_game",
                    "requirements": {},
                    "effect_id": "invalid_condition_exchange",
                }
            )
            write_json(paths.module_file, module)
            committer = StateCommitter(
                paths=paths,
                check_ledger=CheckLedger(paths.check_records_file),
            )

            result = committer.apply_effect(
                ApplyEffectArgs(
                    expected_state_version=0,
                    source=ModuleEventSource(
                        event_rule_id="invalid_condition_exchange",
                    ),
                    effect_id="invalid_condition_exchange",
                    reason="测试复合效果原子性",
                ),
                turn_id="turn_0001",
            )

            self.assertEqual(result.status, "rejected")
            self.assertEqual(
                result.error_code,
                "operation_precondition_failed",
            )
            persisted = read_json(paths.game_state_file)
            self.assertNotIn("partial_write", persisted["flags"])
            self.assertEqual(persisted["state_version"], 0)

    def test_module_load_rejects_relationship_stage_effect_path(self) -> None:
        """世界效果不能把关系阶段重新写回 GameState。"""

        with tempfile.TemporaryDirectory() as directory:
            paths, _ = self._committer_with_transition_event(directory)
            module = read_json(paths.module_file)
            module["effect_definitions"]["rewrite_relationship"] = {
                "operations": [
                    {
                        "path": "characters.vespera.relationship_stage",
                        "operation": "set",
                        "value": "forced_stage",
                    }
                ]
            }
            write_json(paths.module_file, module)

            with self.assertRaises(ModuleDefinitionError):
                StateCommitter(
                    paths=paths,
                    check_ledger=CheckLedger(paths.check_records_file),
                )

    def test_module_load_rejects_zero_increment(self) -> None:
        """数值增量为零的效果不是合法模组配置。"""

        with tempfile.TemporaryDirectory() as directory:
            paths, _ = self._committer_with_transition_event(directory)
            module = read_json(paths.module_file)
            module["effect_definitions"]["empty_increment"] = {
                "operations": [
                    {
                        "path": "threat_clock.value",
                        "operation": "increment",
                        "value": 0,
                    }
                ]
            }
            write_json(paths.module_file, module)

            with self.assertRaises(ModuleDefinitionError):
                StateCommitter(
                    paths=paths,
                    check_ledger=CheckLedger(paths.check_records_file),
                )

    def test_state_version_conflict_rejects_without_writing(self) -> None:
        """非幂等旧版本请求不能自动套用到新状态。"""

        with tempfile.TemporaryDirectory() as directory:
            paths, committer, check_id = self._successful_lock_check(directory)

            result = committer.apply_effect(
                ApplyEffectArgs(
                    expected_state_version=1,
                    source=CheckEffectSource(check_id=check_id),
                    effect_id="unlock_stone_cell_lock",
                    reason="使用了过期状态版本",
                ),
                turn_id="turn_0001",
            )

            self.assertEqual(result.status, "rejected")
            self.assertEqual(result.error_code, "state_version_conflict")
            state = read_json(paths.game_state_file)
            self.assertEqual(state["state_version"], 0)
            self.assertNotIn("stone_cell_lock_unlocked", state["flags"])

    def test_check_cannot_apply_effect_not_allowed_by_outcome(self) -> None:
        """成功检定不能借用同一规则中大失败的状态效果。"""

        with tempfile.TemporaryDirectory() as directory:
            paths, committer, check_id = self._successful_lock_check(directory)

            result = committer.apply_effect(
                ApplyEffectArgs(
                    expected_state_version=0,
                    source=CheckEffectSource(check_id=check_id),
                    effect_id="raise_threat_from_lock_noise",
                    reason="试图把成功结果改成大失败后果",
                ),
                turn_id="turn_0001",
            )

            self.assertEqual(result.status, "rejected")
            self.assertEqual(result.error_code, "effect_not_allowed")
            state = read_json(paths.game_state_file)
            self.assertEqual(state["threat_clock"]["value"], 0)
            self.assertEqual(state["state_version"], 0)

    def test_consumed_check_cannot_choose_a_second_effect(self) -> None:
        """同一 check_id 实际提交一次后不能再选择其他后果。"""

        with tempfile.TemporaryDirectory() as directory:
            paths, committer, check_id = self._successful_lock_check(directory)
            source = CheckEffectSource(check_id=check_id)
            committer.apply_effect(
                ApplyEffectArgs(
                    expected_state_version=0,
                    source=source,
                    effect_id="unlock_stone_cell_lock",
                    reason="先提交成功效果",
                ),
                turn_id="turn_0001",
            )

            result = committer.apply_effect(
                ApplyEffectArgs(
                    expected_state_version=1,
                    source=source,
                    effect_id="raise_threat_from_lock_noise",
                    reason="随后尝试追加第二个后果",
                ),
                turn_id="turn_0001",
            )

            self.assertEqual(result.status, "rejected")
            self.assertEqual(result.error_code, "source_already_consumed")
            state = read_json(paths.game_state_file)
            self.assertEqual(state["state_version"], 1)
            self.assertEqual(state["threat_clock"]["value"], 0)

    def test_adding_an_existing_clue_returns_no_state_change(self) -> None:
        """add_unique 不会重复加入已经发现的线索。"""

        with tempfile.TemporaryDirectory() as directory:
            paths, _ = self._committer_with_transition_event(directory)
            module = read_json(paths.module_file)
            module["effect_definitions"]["discover_known_clue"] = {
                "operations": [
                    {
                        "path": "clues_found",
                        "operation": "add_unique",
                        "value": "akalir_seal",
                    }
                ]
            }
            module["event_rules"].append(
                {
                    "event_rule_id": "discover_known_clue",
                    "scene_id": "stone_cell",
                    "repeat_policy": "once_per_game",
                    "requirements": {},
                    "effect_id": "discover_known_clue",
                }
            )
            write_json(paths.module_file, module)
            state = read_json(paths.game_state_file)
            state["clues_found"] = ["akalir_seal"]
            write_json(paths.game_state_file, state)
            committer = StateCommitter(
                paths=paths,
                check_ledger=CheckLedger(paths.check_records_file),
            )

            result = committer.apply_effect(
                ApplyEffectArgs(
                    expected_state_version=0,
                    source=ModuleEventSource(
                        event_rule_id="discover_known_clue",
                    ),
                    effect_id="discover_known_clue",
                    reason="重复发现已经记录的门印",
                ),
                turn_id="turn_0001",
            )

            self.assertEqual(result.status, "no_state_change")
            self.assertIsNone(result.commit_id)
            persisted = read_json(paths.game_state_file)
            self.assertEqual(persisted["clues_found"], ["akalir_seal"])
            self.assertEqual(persisted["state_version"], 0)
            self.assertNotIn("commit_metadata", persisted)

    def test_character_injury_effect_updates_hp_and_condition_together(self) -> None:
        """角色数值与条件可以作为一个固定效果原子提交。"""

        with tempfile.TemporaryDirectory() as directory:
            paths, _ = self._committer_with_transition_event(directory)
            module = read_json(paths.module_file)
            module["effect_definitions"]["injure_vespera"] = {
                "operations": [
                    {
                        "path": "characters.vespera.hp",
                        "operation": "increment",
                        "value": -2,
                    },
                    {
                        "path": "characters.vespera.conditions",
                        "operation": "add_unique",
                        "value": "bruised",
                    },
                ]
            }
            module["event_rules"].append(
                {
                    "event_rule_id": "injure_vespera",
                    "scene_id": "stone_cell",
                    "repeat_policy": "once_per_game",
                    "requirements": {},
                    "effect_id": "injure_vespera",
                }
            )
            write_json(paths.module_file, module)
            committer = StateCommitter(
                paths=paths,
                check_ledger=CheckLedger(paths.check_records_file),
            )

            result = committer.apply_effect(
                ApplyEffectArgs(
                    expected_state_version=0,
                    source=ModuleEventSource(event_rule_id="injure_vespera"),
                    effect_id="injure_vespera",
                    reason="维斯佩拉替队伍挡下坠落碎石",
                ),
                turn_id="turn_0001",
            )

            self.assertEqual(result.status, "applied")
            self.assertEqual(
                result.changes,
                (
                    StateChange("characters.vespera.hp", 11, 9),
                    StateChange(
                        "characters.vespera.conditions",
                        ["injured_wing"],
                        ["injured_wing", "bruised"],
                    ),
                ),
            )
            state = read_json(paths.game_state_file)
            self.assertEqual(state["characters"]["vespera"]["hp"], 9)
            self.assertEqual(
                state["characters"]["vespera"]["conditions"],
                ["injured_wing", "bruised"],
            )
            self.assertEqual(state["state_version"], 1)

    def test_module_event_can_set_a_declared_ending(self) -> None:
        """结局效果只能写入模组明确声明的 ending_id。"""

        with tempfile.TemporaryDirectory() as directory:
            paths, _ = self._committer_with_transition_event(directory)
            module = read_json(paths.module_file)
            module["ending_ids"] = ["escape_clean"]
            module["effect_definitions"]["finish_escape_clean"] = {
                "operations": [
                    {
                        "path": "ending_id",
                        "operation": "set",
                        "value": "escape_clean",
                    }
                ]
            }
            module["event_rules"].append(
                {
                    "event_rule_id": "finish_escape_clean",
                    "scene_id": "stone_cell",
                    "repeat_policy": "once_per_game",
                    "requirements": {},
                    "effect_id": "finish_escape_clean",
                }
            )
            write_json(paths.module_file, module)
            committer = StateCommitter(
                paths=paths,
                check_ledger=CheckLedger(paths.check_records_file),
            )

            result = committer.apply_effect(
                ApplyEffectArgs(
                    expected_state_version=0,
                    source=ModuleEventSource(
                        event_rule_id="finish_escape_clean",
                    ),
                    effect_id="finish_escape_clean",
                    reason="满足顺利逃离条件",
                ),
                turn_id="turn_0001",
            )

            self.assertEqual(result.status, "applied")
            self.assertEqual(
                result.changes,
                (StateChange("ending_id", None, "escape_clean"),),
            )
            self.assertEqual(
                read_json(paths.game_state_file)["ending_id"],
                "escape_clean",
            )

    def test_numeric_effect_rejects_result_above_field_maximum(self) -> None:
        """数值效果越界时拒绝提交，不静默 clamp。"""

        with tempfile.TemporaryDirectory() as directory:
            paths, _ = self._committer_with_transition_event(directory)
            module = read_json(paths.module_file)
            module["effect_definitions"]["overflow_threat"] = {
                "operations": [
                    {
                        "path": "threat_clock.value",
                        "operation": "increment",
                        "value": 1,
                    }
                ]
            }
            module["event_rules"].append(
                {
                    "event_rule_id": "overflow_threat",
                    "scene_id": "stone_cell",
                    "repeat_policy": "once_per_game",
                    "requirements": {},
                    "effect_id": "overflow_threat",
                }
            )
            write_json(paths.module_file, module)
            state = read_json(paths.game_state_file)
            state["threat_clock"]["value"] = 6
            write_json(paths.game_state_file, state)
            committer = StateCommitter(
                paths=paths,
                check_ledger=CheckLedger(paths.check_records_file),
            )

            result = committer.apply_effect(
                ApplyEffectArgs(
                    expected_state_version=0,
                    source=ModuleEventSource(event_rule_id="overflow_threat"),
                    effect_id="overflow_threat",
                    reason="危机时钟已经达到上限",
                ),
                turn_id="turn_0001",
            )

            self.assertEqual(result.status, "rejected")
            self.assertEqual(result.error_code, "value_out_of_range")
            persisted = read_json(paths.game_state_file)
            self.assertEqual(persisted["threat_clock"]["value"], 6)
            self.assertEqual(persisted["state_version"], 0)


if __name__ == "__main__":
    unittest.main()
