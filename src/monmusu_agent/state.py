"""验证并提交由模组授权的 GameState 效果。"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from monmusu_agent.config import AppPaths
from monmusu_agent.rules import CheckLedger, CheckResult
from monmusu_agent.storage import read_json, write_json_atomic


@dataclass(frozen=True)
class CheckEffectSource:
    """引用一次已经发生的正式检定。"""

    check_id: str


@dataclass(frozen=True)
class ModuleEventSource:
    """引用模组中的静态事件规则。"""

    event_rule_id: str


@dataclass(frozen=True)
class ApplyEffectArgs:
    """承载 GM 对单个固定效果的申请。"""

    expected_state_version: int
    source: CheckEffectSource | ModuleEventSource
    effect_id: str
    reason: str


@dataclass(frozen=True)
class StateChange:
    """描述一次已提交的机械状态变化。"""

    path: str
    before: Any
    after: Any


@dataclass(frozen=True)
class CommitResult:
    """StateCommitter 对一次效果申请返回的稳定结果。"""

    status: str
    effect_id: str
    commit_id: str | None
    state_version: int
    changes: tuple[StateChange, ...]
    error_code: str | None = None
    message: str = ""


class StateCommitError(RuntimeError):
    """表示运行时状态或持久化基础设施无法继续工作。"""


class ModuleDefinitionError(ValueError):
    """表示模组效果定义在加载时不符合契约。"""


class _EffectRejected(Exception):
    """表示效果在预演阶段不适用于当前状态。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _NoStateChange(Exception):
    """表示事件合法，但当前状态没有可提交的变化。"""


@dataclass(frozen=True)
class _EventRule:
    """保存经过加载校验的静态模组事件。"""

    event_rule_id: str
    scene_id: str
    repeat_policy: str
    requirements: Mapping[str, Any]
    effect_id: str


class StateCommitter:
    """GameState 的唯一效果写入边界。"""

    def __init__(self, paths: AppPaths, check_ledger: CheckLedger) -> None:
        self.paths = paths
        self.check_ledger = check_ledger
        self.module = read_json(paths.module_file)
        self.effect_definitions = self._parse_effect_definitions(
            self.module.get("effect_definitions")
        )
        self.scene_threat_floors = self._parse_scene_threat_floors(
            self.module.get("scene_threat_floors", {})
        )
        self.ending_ids = self._parse_ending_ids(
            self.module.get("ending_ids", [])
        )
        self.event_rules = self._parse_event_rules(
            self.module.get("event_rules", []),
            frozenset(self.effect_definitions),
        )

    def apply_effect(
        self,
        args: ApplyEffectArgs,
        *,
        turn_id: str,
    ) -> CommitResult:
        """验证单个效果，并在成功时原子提交完整状态副本。"""

        state = self._read_state()
        metadata = self._metadata(state)
        try:
            source_key, event_rule = self._resolve_source(
                args.source,
                turn_id,
            )
        except _EffectRejected as error:
            return self._reject(
                args.effect_id,
                state,
                error.code,
                error.message,
            )
        application_key = f"{source_key}:{args.effect_id}"

        existing = metadata["applied_effects"].get(application_key)
        if existing is not None:
            return self._already_applied(args.effect_id, state, existing)

        consumed = metadata["consumed_sources"].get(source_key)
        if consumed is not None:
            return self._reject(
                args.effect_id,
                state,
                "source_already_consumed",
                "该来源已经选择过其他效果",
            )

        current_version = state.get("state_version")
        if not isinstance(current_version, int) or isinstance(current_version, bool):
            raise StateCommitError("GameState state_version 格式无效")
        if args.expected_state_version != current_version:
            return self._reject(
                args.effect_id,
                state,
                "state_version_conflict",
                "提交所依据的状态版本已经过期",
            )

        if isinstance(args.source, CheckEffectSource):
            try:
                self._validate_check_source(
                    args.source.check_id,
                    args.effect_id,
                    state,
                    turn_id,
                )
            except _EffectRejected as error:
                return self._reject(
                    args.effect_id,
                    state,
                    error.code,
                    error.message,
                )
        else:
            assert event_rule is not None
            try:
                self._validate_event_source(event_rule, args.effect_id, state)
            except _NoStateChange as no_change:
                return self._no_state_change(
                    args.effect_id,
                    state,
                    str(no_change),
                )
            except _EffectRejected as error:
                return self._reject(
                    args.effect_id,
                    state,
                    error.code,
                    error.message,
                )

        effect = self.effect_definitions.get(args.effect_id)
        if effect is None:
            return self._reject(
                args.effect_id,
                state,
                "unknown_effect",
                "未找到效果定义",
            )

        candidate = copy.deepcopy(state)
        changes: list[StateChange] = []
        try:
            for operation in effect:
                self._apply_operation(candidate, operation, changes)
        except _EffectRejected as error:
            return self._reject(args.effect_id, state, error.code, error.message)

        if not changes:
            return self._no_state_change(
                args.effect_id,
                state,
                "效果在当前状态下没有产生变化",
            )

        commit_id = self._next_commit_id(state, metadata)
        next_version = current_version + 1
        candidate["state_version"] = next_version
        metadata_copy = copy.deepcopy(metadata)
        metadata_copy["last_commit_sequence"] += 1
        metadata_copy["applied_effects"][application_key] = {
            "commit_id": commit_id,
            "effect_id": args.effect_id,
            "previous_state_version": current_version,
            "state_version": next_version,
            "changes": [self._serialize_change(change) for change in changes],
        }
        metadata_copy["consumed_sources"][source_key] = {
            "effect_id": args.effect_id,
            "commit_id": commit_id,
        }
        candidate["commit_metadata"] = metadata_copy
        write_json_atomic(self.paths.game_state_file, candidate)

        return CommitResult(
            status="applied",
            effect_id=args.effect_id,
            commit_id=commit_id,
            state_version=next_version,
            changes=tuple(changes),
            message="效果已提交",
        )

    def _read_state(self) -> dict[str, Any]:
        value = read_json(self.paths.game_state_file)
        if not isinstance(value, dict):
            raise StateCommitError("GameState 格式无效")
        return value

    @staticmethod
    def _metadata(state: Mapping[str, Any]) -> dict[str, Any]:
        value = state.get("commit_metadata")
        if value is None:
            return {
                "last_commit_sequence": 0,
                "applied_effects": {},
                "consumed_sources": {},
            }
        if not isinstance(value, dict):
            raise StateCommitError("commit_metadata 格式无效")
        return copy.deepcopy(value)

    def _resolve_source(
        self,
        source: CheckEffectSource | ModuleEventSource,
        turn_id: str,
    ) -> tuple[str, _EventRule | None]:
        if isinstance(source, CheckEffectSource):
            return f"check:{source.check_id}", None

        rule = self.event_rules.get(source.event_rule_id)
        if rule is None:
            raise _EffectRejected("unknown_source", "未找到模组事件规则")
        key = f"module_event:{rule.event_rule_id}"
        if rule.repeat_policy == "once_per_turn":
            key = f"{key}:{turn_id}"
        return key, rule

    def _validate_check_source(
        self,
        check_id: str,
        effect_id: str,
        state: Mapping[str, Any],
        turn_id: str,
    ) -> CheckResult:
        try:
            result = self.check_ledger.get(check_id)
        except KeyError:
            raise _EffectRejected("unknown_source", "未找到检定来源")

        if result.game_id != state.get("game_id"):
            raise _EffectRejected("unknown_source", "检定不属于当前游戏")
        if result.module_id != state.get("module_id"):
            raise _EffectRejected("unknown_source", "检定不属于当前模组")
        if result.turn_id != turn_id or result.scene_id != state.get("current_scene"):
            raise _EffectRejected("stale_source", "检定来源已经不在当前回合或场景")
        if not result.allowed_effect_ids or effect_id not in result.allowed_effect_ids:
            raise _EffectRejected("effect_not_allowed", "检定结果没有授权该效果")
        return result

    def _validate_event_source(
        self,
        rule: _EventRule,
        effect_id: str,
        state: Mapping[str, Any],
    ) -> None:
        if state.get("module_id") != self.module.get("module_id"):
            raise _EffectRejected("unknown_source", "事件不属于当前模组")
        if state.get("current_scene") != rule.scene_id:
            raise _EffectRejected("event_not_applicable", "事件不属于当前场景")
        if effect_id != rule.effect_id:
            raise _EffectRejected("effect_not_allowed", "事件没有授权该效果")

        requirements = rule.requirements
        flags = state.get("flags")
        required_flags = requirements.get("required_flags", {})
        if not isinstance(flags, dict) or any(
            flags.get(flag_id) != expected
            for flag_id, expected in required_flags.items()
        ):
            raise _EffectRejected("event_not_applicable", "事件所需 flag 尚未满足")

        clues = state.get("clues_found")
        required_clues = requirements.get("required_clues", ())
        if not isinstance(clues, list) or any(
            clue_id not in clues for clue_id in required_clues
        ):
            raise _EffectRejected("event_not_applicable", "事件所需线索尚未发现")

        threat_clock = state.get("threat_clock")
        if not isinstance(threat_clock, dict):
            raise StateCommitError("GameState threat_clock 格式无效")
        threat_value = threat_clock.get("value")
        if not isinstance(threat_value, int) or isinstance(threat_value, bool):
            raise StateCommitError("GameState 危机时钟格式无效")
        minimum = requirements.get("min_threat_clock")
        maximum = requirements.get("max_threat_clock")
        if minimum is not None and threat_value < minimum:
            raise _EffectRejected("event_not_applicable", "危机时钟尚未达到事件下限")
        if maximum is not None and threat_value > maximum:
            raise _EffectRejected("event_not_applicable", "危机时钟已经超过事件上限")
        if requirements.get("threat_above_scene_floor"):
            floor = self.scene_threat_floors.get(rule.scene_id)
            if floor is None:
                raise _EffectRejected("event_not_applicable", "场景没有危机时钟下限")
            if threat_value <= floor:
                raise _NoStateChange("危机时钟已经位于当前场景下限")

    def _next_commit_id(
        self,
        state: Mapping[str, Any],
        metadata: Mapping[str, Any],
    ) -> str:
        sequence = metadata.get("last_commit_sequence", 0)
        if not isinstance(sequence, int) or isinstance(sequence, bool):
            raise StateCommitError("commit_metadata 序号格式无效")
        return f"commit_{state['game_id']}_{sequence + 1:04d}"

    @staticmethod
    def _already_applied(
        effect_id: str,
        state: Mapping[str, Any],
        receipt: Mapping[str, Any],
    ) -> CommitResult:
        changes = tuple(
            StateCommitter._deserialize_change(change)
            for change in receipt.get("changes", [])
        )
        return CommitResult(
            status="already_applied",
            effect_id=effect_id,
            commit_id=receipt.get("commit_id"),
            state_version=state["state_version"],
            changes=changes,
            message="效果已经提交过",
        )

    @staticmethod
    def _reject(
        effect_id: str,
        state: Mapping[str, Any],
        code: str,
        message: str,
    ) -> CommitResult:
        return CommitResult(
            status="rejected",
            effect_id=effect_id,
            commit_id=None,
            state_version=state["state_version"],
            changes=(),
            error_code=code,
            message=message,
        )

    @staticmethod
    def _no_state_change(
        effect_id: str,
        state: Mapping[str, Any],
        message: str,
    ) -> CommitResult:
        return CommitResult(
            status="no_state_change",
            effect_id=effect_id,
            commit_id=None,
            state_version=state["state_version"],
            changes=(),
            message=message,
        )

    @staticmethod
    def _parse_effect_definitions(value: object) -> dict[str, tuple[dict[str, Any], ...]]:
        if not isinstance(value, dict):
            raise ModuleDefinitionError("effect_definitions 必须是对象")
        parsed: dict[str, tuple[dict[str, Any], ...]] = {}
        for effect_id, raw_definition in value.items():
            if not isinstance(effect_id, str) or not effect_id:
                raise ModuleDefinitionError("effect_id 必须是非空字符串")
            if not isinstance(raw_definition, dict):
                raise ModuleDefinitionError(f"效果定义无效：{effect_id}")
            raw_operations = raw_definition.get("operations")
            if not isinstance(raw_operations, list) or not raw_operations:
                raise ModuleDefinitionError(f"效果必须包含 operations：{effect_id}")
            operations: list[dict[str, Any]] = []
            for raw_operation in raw_operations:
                if not isinstance(raw_operation, dict):
                    raise ModuleDefinitionError(f"效果操作无效：{effect_id}")
                if not isinstance(raw_operation.get("path"), str) or not raw_operation["path"]:
                    raise ModuleDefinitionError(f"效果路径无效：{effect_id}")
                if raw_operation.get("operation") not in {
                    "set",
                    "increment",
                    "add_unique",
                    "remove",
                    "ensure_at_least",
                }:
                    raise ModuleDefinitionError(f"效果操作类型无效：{effect_id}")
                if "value" not in raw_operation:
                    raise ModuleDefinitionError(f"效果操作缺少 value：{effect_id}")
                StateCommitter._validate_operation_definition(
                    effect_id,
                    raw_operation,
                )
                operations.append(dict(raw_operation))
            parsed[effect_id] = tuple(operations)
        return parsed

    @staticmethod
    def _validate_operation_definition(
        effect_id: str,
        operation: Mapping[str, Any],
    ) -> None:
        path = operation["path"]
        action = operation["operation"]
        value = operation["value"]

        if path.startswith("flags."):
            flag_id = path.removeprefix("flags.")
            if (
                flag_id
                and "." not in flag_id
                and action == "set"
                and (
                    value is None
                    or isinstance(value, (str, int, bool))
                )
            ):
                return

        if path == "current_scene":
            if action == "set" and isinstance(value, str) and value:
                return

        if path in {"accessible_locations", "clues_found"}:
            if action == "add_unique" and isinstance(value, str) and value:
                return

        if path == "threat_clock.value":
            if (
                action == "increment"
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value != 0
            ):
                return
            if (
                action == "ensure_at_least"
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            ):
                return

        path_parts = path.split(".")
        is_user_condition = path == "user_character.conditions"
        is_character_condition = (
            len(path_parts) == 3
            and path_parts[0] == "characters"
            and bool(path_parts[1])
            and path_parts[2] == "conditions"
        )
        if is_user_condition or is_character_condition:
            if action in {"add_unique", "remove"} and isinstance(value, str) and value:
                return

        is_user_stat = (
            len(path_parts) == 2
            and path_parts[0] == "user_character"
            and path_parts[1] in {"hp", "sanity", "pressure"}
        )
        is_character_stat = (
            len(path_parts) == 3
            and path_parts[0] == "characters"
            and bool(path_parts[1])
            and path_parts[2] in {"hp", "sanity", "pressure"}
        )
        if is_user_stat or is_character_stat:
            if (
                action == "increment"
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value != 0
            ):
                return

        if path == "ending_id":
            if action == "set" and isinstance(value, str) and value:
                return

        raise ModuleDefinitionError(
            f"效果路径与操作不在 GameState 写入策略中：{effect_id}.{path}"
        )

    @staticmethod
    def _parse_scene_threat_floors(value: object) -> dict[str, int]:
        if not isinstance(value, dict):
            raise ModuleDefinitionError("scene_threat_floors 必须是对象")
        parsed: dict[str, int] = {}
        for scene_id, floor in value.items():
            if (
                not isinstance(scene_id, str)
                or not scene_id
                or not isinstance(floor, int)
                or isinstance(floor, bool)
                or floor < 0
            ):
                raise ModuleDefinitionError("场景危机时钟下限无效")
            parsed[scene_id] = floor
        return parsed

    @staticmethod
    def _parse_ending_ids(value: object) -> frozenset[str]:
        if not isinstance(value, list) or not all(
            isinstance(ending_id, str) and ending_id for ending_id in value
        ):
            raise ModuleDefinitionError("ending_ids 必须是字符串数组")
        if len(value) != len(set(value)):
            raise ModuleDefinitionError("ending_ids 不能重复")
        return frozenset(value)

    @staticmethod
    def _parse_event_rules(
        value: object,
        effect_ids: frozenset[str],
    ) -> dict[str, _EventRule]:
        if not isinstance(value, list):
            raise ModuleDefinitionError("event_rules 必须是数组")
        parsed: dict[str, _EventRule] = {}
        allowed_requirements = {
            "required_flags",
            "required_clues",
            "min_threat_clock",
            "max_threat_clock",
            "threat_above_scene_floor",
        }
        for value_item in value:
            if not isinstance(value_item, dict):
                raise ModuleDefinitionError("event_rule 必须是对象")
            event_rule_id = value_item.get("event_rule_id")
            scene_id = value_item.get("scene_id")
            repeat_policy = value_item.get("repeat_policy")
            effect_id = value_item.get("effect_id")
            requirements = value_item.get("requirements", {})
            if not isinstance(event_rule_id, str) or not event_rule_id:
                raise ModuleDefinitionError("event_rule_id 必须是非空字符串")
            if event_rule_id in parsed:
                raise ModuleDefinitionError(f"重复定义模组事件：{event_rule_id}")
            if not isinstance(scene_id, str) or not scene_id:
                raise ModuleDefinitionError(f"事件场景无效：{event_rule_id}")
            if repeat_policy not in {"once_per_game", "once_per_turn"}:
                raise ModuleDefinitionError(f"事件重复策略无效：{event_rule_id}")
            if effect_id not in effect_ids:
                raise ModuleDefinitionError(f"事件引用未知效果：{event_rule_id}")
            if not isinstance(requirements, dict) or not set(requirements) <= allowed_requirements:
                raise ModuleDefinitionError(f"事件条件无效：{event_rule_id}")

            required_flags = requirements.get("required_flags", {})
            required_clues = requirements.get("required_clues", [])
            if not isinstance(required_flags, dict) or not all(
                isinstance(flag_id, str) and flag_id for flag_id in required_flags
            ):
                raise ModuleDefinitionError(f"事件 flag 条件无效：{event_rule_id}")
            if not isinstance(required_clues, list) or not all(
                isinstance(clue_id, str) and clue_id for clue_id in required_clues
            ):
                raise ModuleDefinitionError(f"事件线索条件无效：{event_rule_id}")
            for bound_name in ("min_threat_clock", "max_threat_clock"):
                bound = requirements.get(bound_name)
                if bound is not None and (
                    not isinstance(bound, int) or isinstance(bound, bool) or bound < 0
                ):
                    raise ModuleDefinitionError(f"事件时钟条件无效：{event_rule_id}")
            above_floor = requirements.get("threat_above_scene_floor", False)
            if not isinstance(above_floor, bool):
                raise ModuleDefinitionError(f"事件场景下限条件无效：{event_rule_id}")

            parsed[event_rule_id] = _EventRule(
                event_rule_id=event_rule_id,
                scene_id=scene_id,
                repeat_policy=repeat_policy,
                requirements=copy.deepcopy(requirements),
                effect_id=effect_id,
            )
        return parsed

    def _apply_operation(
        self,
        state: dict[str, Any],
        operation: Mapping[str, Any],
        changes: list[StateChange],
    ) -> None:
        path = operation["path"]
        action = operation["operation"]
        value = operation["value"]
        if path.startswith("flags.") and action == "set":
            flags = state.get("flags")
            if not isinstance(flags, dict):
                raise _EffectRejected("path_not_allowed", "flags 不是对象")
            key = path.removeprefix("flags.")
            before = flags.get(key)
            if before != value:
                flags[key] = copy.deepcopy(value)
                changes.append(StateChange(path, before, copy.deepcopy(value)))
            return

        if path == "current_scene" and action == "set":
            if not isinstance(value, str) or value not in self.scene_threat_floors:
                raise _EffectRejected("value_out_of_range", "目标场景没有可信定义")
            before = state.get("current_scene")
            if before != value:
                state["current_scene"] = value
                changes.append(StateChange(path, before, value))
            return

        if path == "ending_id" and action == "set":
            if value not in self.ending_ids:
                raise _EffectRejected("value_out_of_range", "模组没有声明该结局")
            before = state.get("ending_id")
            if before != value:
                state["ending_id"] = value
                changes.append(StateChange(path, before, value))
            return

        if path in {"accessible_locations", "clues_found"} and action == "add_unique":
            values = state.get(path)
            if not isinstance(values, list) or not isinstance(value, str) or not value:
                raise _EffectRejected("type_mismatch", "唯一列表操作类型无效")
            before = copy.deepcopy(values)
            if value not in values:
                values.append(value)
                changes.append(StateChange(path, before, copy.deepcopy(values)))
            return

        if path == "threat_clock.value" and action == "ensure_at_least":
            threat_clock = state.get("threat_clock")
            if not isinstance(threat_clock, dict):
                raise _EffectRejected("type_mismatch", "危机时钟格式无效")
            before = threat_clock.get("value")
            maximum = threat_clock.get("maximum")
            if (
                not isinstance(before, int)
                or isinstance(before, bool)
                or not isinstance(maximum, int)
                or isinstance(maximum, bool)
                or not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value <= maximum
            ):
                raise _EffectRejected("value_out_of_range", "危机时钟下限无效")
            after = max(before, value)
            if after != before:
                threat_clock["value"] = after
                changes.append(StateChange(path, before, after))
            return

        if path == "threat_clock.value" and action == "increment":
            threat_clock = state.get("threat_clock")
            if not isinstance(threat_clock, dict):
                raise _EffectRejected("type_mismatch", "危机时钟格式无效")
            before = threat_clock.get("value")
            maximum = threat_clock.get("maximum")
            if (
                not isinstance(before, int)
                or isinstance(before, bool)
                or not isinstance(maximum, int)
                or isinstance(maximum, bool)
                or not isinstance(value, int)
                or isinstance(value, bool)
            ):
                raise _EffectRejected("type_mismatch", "危机时钟增量类型无效")
            after = before + value
            if not 0 <= after <= maximum:
                raise _EffectRejected("value_out_of_range", "危机时钟结果越界")
            if after != before:
                threat_clock["value"] = after
                changes.append(StateChange(path, before, after))
            return

        if action == "increment":
            actor_stat = self._actor_stat_at_path(state, path)
            if actor_stat is not None:
                actor, stat_name = actor_stat
                before = actor.get(stat_name)
                if (
                    not isinstance(before, int)
                    or isinstance(before, bool)
                    or not isinstance(value, int)
                    or isinstance(value, bool)
                ):
                    raise _EffectRejected("type_mismatch", "角色数值增量类型无效")
                after = before + value
                if after < 0:
                    raise _EffectRejected("value_out_of_range", "角色数值结果不能小于 0")
                actor[stat_name] = after
                changes.append(StateChange(path, before, after))
                return

        if action in {"add_unique", "remove"}:
            conditions = self._conditions_at_path(state, path)
            if conditions is not None:
                if not isinstance(value, str) or not value:
                    raise _EffectRejected("type_mismatch", "角色状态标识无效")
                before = copy.deepcopy(conditions)
                if action == "add_unique":
                    if value not in conditions:
                        conditions.append(value)
                        changes.append(
                            StateChange(path, before, copy.deepcopy(conditions))
                        )
                    return
                if value not in conditions:
                    raise _EffectRejected(
                        "operation_precondition_failed",
                        "要移除的角色状态不存在",
                    )
                conditions.remove(value)
                changes.append(StateChange(path, before, copy.deepcopy(conditions)))
                return

        raise _EffectRejected("path_not_allowed", f"效果路径或操作不允许：{path}")

    @staticmethod
    def _conditions_at_path(
        state: Mapping[str, Any],
        path: str,
    ) -> list[Any] | None:
        if path == "user_character.conditions":
            actor = state.get("user_character")
        else:
            parts = path.split(".")
            if len(parts) != 3 or parts[0] != "characters" or parts[2] != "conditions":
                return None
            characters = state.get("characters")
            if not isinstance(characters, dict):
                raise _EffectRejected("type_mismatch", "角色状态映射格式无效")
            actor = characters.get(parts[1])

        if not isinstance(actor, dict) or not isinstance(actor.get("conditions"), list):
            raise _EffectRejected("path_not_allowed", "角色状态路径不存在")
        return actor["conditions"]

    @staticmethod
    def _actor_stat_at_path(
        state: Mapping[str, Any],
        path: str,
    ) -> tuple[dict[str, Any], str] | None:
        parts = path.split(".")
        if (
            len(parts) == 2
            and parts[0] == "user_character"
            and parts[1] in {"hp", "sanity", "pressure"}
        ):
            actor = state.get("user_character")
            stat_name = parts[1]
        elif (
            len(parts) == 3
            and parts[0] == "characters"
            and parts[2] in {"hp", "sanity", "pressure"}
        ):
            characters = state.get("characters")
            if not isinstance(characters, dict):
                raise _EffectRejected("type_mismatch", "角色状态映射格式无效")
            actor = characters.get(parts[1])
            stat_name = parts[2]
        else:
            return None

        if not isinstance(actor, dict):
            raise _EffectRejected("path_not_allowed", "角色数值路径不存在")
        return actor, stat_name

    @staticmethod
    def _serialize_change(change: StateChange) -> dict[str, Any]:
        return {
            "path": change.path,
            "before": copy.deepcopy(change.before),
            "after": copy.deepcopy(change.after),
        }

    @staticmethod
    def _deserialize_change(value: object) -> StateChange:
        if not isinstance(value, dict):
            raise StateCommitError("提交回执中的变化格式无效")
        return StateChange(
            path=value["path"],
            before=copy.deepcopy(value.get("before")),
            after=copy.deepcopy(value.get("after")),
        )
