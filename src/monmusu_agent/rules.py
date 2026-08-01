"""实现 MVP 使用的 d100 技能检定规则。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from random import Random
from types import MappingProxyType
from typing import Mapping, Sequence

from monmusu_agent.storage import read_json, write_json


def clamp(value: int, minimum: int, maximum: int) -> int:
    """将数值限制在包含上下界的闭区间内。"""

    return max(minimum, min(value, maximum))


def clamp_context_modifier(actor_type: str, suggested_modifier: int) -> int:
    """按照行动者类型限制语境修正。"""

    if actor_type == "user":
        limit = 10
    elif actor_type == "character":
        limit = 5
    else:
        raise ValueError(f"未知的行动者类型：{actor_type}")

    return clamp(suggested_modifier, -limit, limit)


@dataclass(frozen=True)
class RollResult:
    """记录一次尚未持久化的 d100 掷骰结果。"""

    roll: int
    target: int
    outcome: str


@dataclass(frozen=True)
class ModifierReason:
    """记录 GM 为语境修正提供的可追溯理由。"""

    reason_tag: str
    source_id: str


@dataclass(frozen=True)
class ModifierSource:
    """定义一个可信来源能支持哪些语境理由。"""

    source_id: str
    allowed_reason_tags: frozenset[str]


@dataclass(frozen=True)
class CheckRule:
    """定义静态目标可使用的技能及其难度修正。"""

    rule_id: str
    target_id: str
    allowed_skills: frozenset[str]
    difficulty_modifier: int
    effects_by_outcome: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict,
    )

    def __post_init__(self) -> None:
        """冻结各结果允许的效果，防止检定后授权被改写。"""

        object.__setattr__(
            self,
            "effects_by_outcome",
            MappingProxyType(
                {
                    outcome: tuple(effect_ids)
                    for outcome, effect_ids in self.effects_by_outcome.items()
                }
            ),
        )


@dataclass(frozen=True)
class RequestCheckArgs:
    """承载 GM 可建议、但尚未成为正式事实的检定请求。"""

    actor_id: str
    actor_type: str
    action: str
    target: str | None
    suggested_skill: str
    suggested_context_modifier: int
    modifier_reasons: tuple[ModifierReason, ...]
    authorization: str
    authorization_evidence: str


@dataclass(frozen=True)
class CheckContext:
    """承载 ToolExecutor 提供给规则引擎的可信只读快照。"""

    game_id: str
    turn_id: str
    module_id: str
    scene_id: str
    input_text: str
    user_id: str
    character_ids: frozenset[str]
    actor_skills: Mapping[str, Mapping[str, int]]
    rules_by_target: Mapping[str, CheckRule]
    modifier_sources: Mapping[str, ModifierSource]

    def __post_init__(self) -> None:
        """复制并冻结嵌套容器，防止本轮可信事实被外部改写。"""

        immutable_skills = MappingProxyType(
            {
                actor_id: MappingProxyType(dict(skills))
                for actor_id, skills in self.actor_skills.items()
            }
        )
        object.__setattr__(self, "actor_skills", immutable_skills)
        object.__setattr__(
            self,
            "rules_by_target",
            MappingProxyType(dict(self.rules_by_target)),
        )
        object.__setattr__(
            self,
            "modifier_sources",
            MappingProxyType(dict(self.modifier_sources)),
        )
        object.__setattr__(self, "character_ids", frozenset(self.character_ids))


@dataclass(frozen=True)
class CheckResult:
    """记录一次可供状态提交引用的不可变正式检定。"""

    check_id: str
    game_id: str
    turn_id: str
    module_id: str
    scene_id: str
    rule_id: str | None
    target_id: str | None
    actor_id: str
    actor_type: str
    skill: str
    base_skill: int
    difficulty_modifier: int
    context_modifier: int
    target: int
    roll: int
    outcome: str
    allowed_effect_ids: tuple[str, ...]
    reason_tags: tuple[str, ...]


def build_check_context(
    game_state: Mapping[str, object],
    module: Mapping[str, object],
    character_profiles: Sequence[Mapping[str, object]],
    *,
    turn_id: str,
    input_text: str,
) -> CheckContext:
    """把静态配置和当前状态组装成 RuleEngine 的可信只读上下文。"""

    game_id = _required_string(game_state, "game_id")
    module_id = _required_string(module, "module_id")
    if _required_string(game_state, "module_id") != module_id:
        raise RuleValidationError("游戏状态与模组配置不属于同一模组")

    scene_id = _required_string(game_state, "current_scene")
    user_character = _required_mapping(game_state, "user_character")
    user_id = _required_string(user_character, "character_id")
    actor_skills = {
        user_id: _parse_skills(user_character.get("skills"), user_id),
    }

    characters_value = game_state.get("characters")
    characters = _required_mapping_value(characters_value, "characters")
    profiles_by_id = {
        _required_string(profile, "character_id"): profile
        for profile in character_profiles
    }
    character_ids = frozenset(characters)
    for character_id in character_ids:
        profile = profiles_by_id.get(character_id)
        if profile is None:
            raise RuleValidationError(f"缺少角色的静态技能配置：{character_id}")
        actor_skills[character_id] = _parse_skills(
            profile.get("skills"),
            character_id,
        )

    effect_ids = _parse_effect_definition_ids(module.get("effect_definitions", {}))
    rules_by_target: dict[str, CheckRule] = {}
    raw_rules = module.get("check_rules", [])
    if not isinstance(raw_rules, list):
        raise RuleValidationError("模组 check_rules 必须是数组")
    for raw_rule in raw_rules:
        rule_data = _required_mapping_value(raw_rule, "check_rule")
        rule_scene_id = _required_string(rule_data, "scene_id")
        if rule_scene_id != scene_id:
            continue
        rule = CheckRule(
            rule_id=_required_string(rule_data, "rule_id"),
            target_id=_required_string(rule_data, "target_id"),
            allowed_skills=_parse_string_set(
                rule_data.get("allowed_skills"),
                "allowed_skills",
            ),
            difficulty_modifier=_required_int(
                rule_data,
                "difficulty_modifier",
            ),
            effects_by_outcome=_parse_effects_by_outcome(
                rule_data.get("effects_by_outcome"),
                effect_ids,
            ),
        )
        if rule.target_id in rules_by_target:
            raise RuleValidationError(f"模组重复定义静态目标：{rule.target_id}")
        rules_by_target[rule.target_id] = rule

    modifier_sources = _parse_modifier_sources(module.get("modifier_sources", []))
    raw_clues = game_state.get("clues_found", [])
    if not isinstance(raw_clues, list):
        raise RuleValidationError("游戏状态 clues_found 必须是数组")
    for clue_id in raw_clues:
        if not isinstance(clue_id, str) or not clue_id:
            raise RuleValidationError("已发现线索标识无效")
        modifier_sources.setdefault(
            clue_id,
            ModifierSource(
                source_id=clue_id,
                allowed_reason_tags=frozenset({"relevant_clue"}),
            ),
        )

    return CheckContext(
        game_id=game_id,
        turn_id=turn_id,
        module_id=module_id,
        scene_id=scene_id,
        input_text=input_text,
        user_id=user_id,
        character_ids=character_ids,
        actor_skills=actor_skills,
        rules_by_target=rules_by_target,
        modifier_sources=modifier_sources,
    )


def _required_mapping(source: Mapping[str, object], key: str) -> Mapping[str, object]:
    return _required_mapping_value(source.get(key), key)


def _required_mapping_value(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise RuleValidationError(f"{label} 必须是对象")
    return value


def _required_string(source: Mapping[str, object], key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value:
        raise RuleValidationError(f"{key} 必须是非空字符串")
    return value


def _required_int(source: Mapping[str, object], key: str) -> int:
    value = source.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RuleValidationError(f"{key} 必须是整数")
    return value


def _parse_skills(value: object, actor_id: str) -> dict[str, int]:
    skills = _required_mapping_value(value, f"{actor_id}.skills")
    parsed: dict[str, int] = {}
    for skill, score in skills.items():
        if (
            not isinstance(skill, str)
            or not skill
            or not isinstance(score, int)
            or isinstance(score, bool)
            or not 1 <= score <= 99
        ):
            raise RuleValidationError(f"角色技能配置无效：{actor_id}.{skill}")
        parsed[skill] = score
    return parsed


def _parse_string_set(value: object, label: str) -> frozenset[str]:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        raise RuleValidationError(f"{label} 必须是非空字符串数组")
    return frozenset(value)


def _parse_modifier_sources(value: object) -> dict[str, ModifierSource]:
    if not isinstance(value, list):
        raise RuleValidationError("modifier_sources 必须是数组")
    parsed: dict[str, ModifierSource] = {}
    for raw_source in value:
        source = _required_mapping_value(raw_source, "modifier_source")
        source_id = _required_string(source, "source_id")
        if source_id in parsed:
            raise RuleValidationError(f"重复定义语境来源：{source_id}")
        parsed[source_id] = ModifierSource(
            source_id=source_id,
            allowed_reason_tags=_parse_string_set(
                source.get("allowed_reason_tags"),
                "allowed_reason_tags",
            ),
        )
    return parsed


def _parse_effect_definition_ids(value: object) -> frozenset[str]:
    if not isinstance(value, dict):
        raise RuleValidationError("模组 effect_definitions 必须是对象")
    if not all(isinstance(effect_id, str) and effect_id for effect_id in value):
        raise RuleValidationError("模组包含无效的 effect_id")
    return frozenset(value)


def _parse_effects_by_outcome(
    value: object,
    effect_ids: frozenset[str],
) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RuleValidationError("effects_by_outcome 必须是对象")

    allowed_outcomes = {"critical_success", "success", "failure", "fumble"}
    if set(value) != allowed_outcomes:
        raise RuleValidationError("effects_by_outcome 必须定义全部检定结果")

    parsed: dict[str, tuple[str, ...]] = {}
    for outcome, raw_effect_ids in value.items():
        if not isinstance(raw_effect_ids, list) or not all(
            isinstance(effect_id, str) and effect_id
            for effect_id in raw_effect_ids
        ):
            raise RuleValidationError(f"{outcome} 的效果授权必须是字符串数组")
        if len(raw_effect_ids) != len(set(raw_effect_ids)):
            raise RuleValidationError(f"{outcome} 重复引用同一效果")
        if any(effect_id not in effect_ids for effect_id in raw_effect_ids):
            raise RuleValidationError(f"{outcome} 引用了未知效果")
        parsed[outcome] = tuple(raw_effect_ids)

    return parsed


class RuleValidationError(ValueError):
    """表示可信规则验证拒绝了一项检定请求。"""


class CheckRequestRejected(RuleValidationError):
    """表示 GM 的检定候选未通过规则验证。"""


class CheckLedger:
    """保存单局内不可重掷、可供后续查询的正式检定记录。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def get(self, check_id: str) -> CheckResult:
        """按检定标识返回唯一的权威记录。"""

        for result in self._read_results():
            if result.check_id == check_id:
                return result

        raise KeyError(f"未找到检定记录：{check_id}")

    def append(self, result: CheckResult) -> None:
        """追加一条新记录，拒绝覆盖或重用既有检定标识。"""

        results = self._read_results()
        if any(existing.check_id == result.check_id for existing in results):
            raise RuleValidationError(f"检定标识已存在：{result.check_id}")

        results.append(result)
        write_json(
            self.path,
            {
                "schema_version": "1.0",
                "records": [self._serialize(item) for item in results],
            },
        )

    def reset(self) -> None:
        """为一局新游戏创建空账本，清除上一局的检定记录。"""

        write_json(
            self.path,
            {
                "schema_version": "1.0",
                "records": [],
            },
        )

    def next_sequence(self, game_id: str) -> int:
        """返回当前游戏下一条检定记录应使用的单调序号。"""

        prefix = f"check_{game_id}_"
        sequences: list[int] = []
        for result in self._read_results():
            if result.game_id != game_id:
                continue

            suffix = result.check_id.removeprefix(prefix)
            if suffix == result.check_id or not suffix.isdecimal():
                raise RuleValidationError(
                    f"检定记录使用了不符合规则的标识：{result.check_id}"
                )
            sequences.append(int(suffix))

        return max(sequences, default=0) + 1

    def _read_results(self) -> list[CheckResult]:
        if not self.path.exists():
            return []

        payload = read_json(self.path)
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            raise RuleValidationError("检定账本格式无效")

        return [self._deserialize(record) for record in payload["records"]]

    @staticmethod
    def _serialize(result: CheckResult) -> dict[str, object]:
        return {
            "check_id": result.check_id,
            "game_id": result.game_id,
            "turn_id": result.turn_id,
            "module_id": result.module_id,
            "scene_id": result.scene_id,
            "rule_id": result.rule_id,
            "target_id": result.target_id,
            "actor_id": result.actor_id,
            "actor_type": result.actor_type,
            "skill": result.skill,
            "base_skill": result.base_skill,
            "difficulty_modifier": result.difficulty_modifier,
            "context_modifier": result.context_modifier,
            "target": result.target,
            "roll": result.roll,
            "outcome": result.outcome,
            "allowed_effect_ids": list(result.allowed_effect_ids),
            "reason_tags": list(result.reason_tags),
        }

    @staticmethod
    def _deserialize(record: object) -> CheckResult:
        if not isinstance(record, dict):
            raise RuleValidationError("检定账本包含无效记录")

        try:
            return CheckResult(
                check_id=record["check_id"],
                game_id=record["game_id"],
                turn_id=record["turn_id"],
                module_id=record["module_id"],
                scene_id=record["scene_id"],
                rule_id=record["rule_id"],
                target_id=record["target_id"],
                actor_id=record["actor_id"],
                actor_type=record["actor_type"],
                skill=record["skill"],
                base_skill=record["base_skill"],
                difficulty_modifier=record["difficulty_modifier"],
                context_modifier=record["context_modifier"],
                target=record["target"],
                roll=record["roll"],
                outcome=record["outcome"],
                allowed_effect_ids=tuple(record.get("allowed_effect_ids", [])),
                reason_tags=tuple(record["reason_tags"]),
            )
        except (KeyError, TypeError) as error:
            raise RuleValidationError("检定账本包含不完整记录") from error


class RuleEngine:
    """执行可注入随机数生成器的 d100 检定。"""

    def __init__(
        self,
        random: Random | None = None,
        ledger: CheckLedger | None = None,
    ) -> None:
        """创建规则引擎；传入固定随机源可复现检定结果。"""

        self.random = random or Random()
        self.ledger = ledger

    def roll_check(
        self,
        base_skill: int,
        difficulty_modifier: int = 0,
        context_modifier: int = 0,
    ) -> RollResult:
        """合并技能和修正值后掷骰，并返回四档检定结果。"""

        # 目标值限制在 5～95，确保始终保留自动成功和大失败区间。
        target = clamp(base_skill + difficulty_modifier + context_modifier, 5, 95)
        roll = self.random.randint(1, 100)

        # 极值规则优先于普通的目标值比较。
        if roll <= 5:
            outcome = "critical_success"
        elif roll >= 96:
            outcome = "fumble"
        elif roll <= target:
            outcome = "success"
        else:
            outcome = "failure"

        return RollResult(roll=roll, target=target, outcome=outcome)

    def resolve_check(
        self,
        request: RequestCheckArgs,
        context: CheckContext,
    ) -> CheckResult:
        """验证请求、创建唯一检定，并将结果立即写入权威账本。"""

        try:
            self._validate_actor_and_authorization(request, context)
            rule = self._resolve_static_rule(request.target, context)
            self._validate_skill(request, context, rule)
            self._validate_modifier_reasons(request, context)
        except RuleValidationError as error:
            raise CheckRequestRejected(str(error)) from error

        ledger = self._require_ledger()
        sequence = ledger.next_sequence(context.game_id)
        base_skill = context.actor_skills[request.actor_id][request.suggested_skill]
        difficulty_modifier = 0 if rule is None else rule.difficulty_modifier
        context_modifier = clamp_context_modifier(
            request.actor_type,
            request.suggested_context_modifier,
        )
        roll_result = self.roll_check(
            base_skill=base_skill,
            difficulty_modifier=difficulty_modifier,
            context_modifier=context_modifier,
        )
        result = CheckResult(
            check_id=f"check_{context.game_id}_{sequence:04d}",
            game_id=context.game_id,
            turn_id=context.turn_id,
            module_id=context.module_id,
            scene_id=context.scene_id,
            rule_id=None if rule is None else rule.rule_id,
            target_id=None if rule is None else rule.target_id,
            actor_id=request.actor_id,
            actor_type=request.actor_type,
            skill=request.suggested_skill,
            base_skill=base_skill,
            difficulty_modifier=difficulty_modifier,
            context_modifier=context_modifier,
            target=roll_result.target,
            roll=roll_result.roll,
            outcome=roll_result.outcome,
            allowed_effect_ids=(
                ()
                if rule is None
                else rule.effects_by_outcome.get(roll_result.outcome, ())
            ),
            reason_tags=tuple(reason.reason_tag for reason in request.modifier_reasons),
        )
        ledger.append(result)
        return result

    def _require_ledger(self) -> CheckLedger:
        if self.ledger is None:
            raise RuleValidationError("正式检定需要配置 CheckLedger")
        return self.ledger

    @staticmethod
    def _validate_actor_and_authorization(
        request: RequestCheckArgs,
        context: CheckContext,
    ) -> None:
        if request.actor_type == "user":
            if request.actor_id != context.user_id:
                raise RuleValidationError("用户行动者与当前玩家不一致")
            if request.authorization != "user_declared":
                raise RuleValidationError("用户行动必须使用 user_declared 授权")
            if request.authorization_evidence not in context.input_text:
                raise RuleValidationError("用户行动授权证据未出现在本轮输入中")
            return

        if request.actor_type != "character":
            raise RuleValidationError("未知的行动者类型")
        if request.actor_id not in context.character_ids:
            raise RuleValidationError("角色行动者不属于当前队伍")
        if request.authorization == "user_declared":
            raise RuleValidationError("角色行动不能使用 user_declared 授权")
        if request.authorization == "user_delegated":
            if request.authorization_evidence not in context.input_text:
                raise RuleValidationError("角色授权证据未出现在本轮输入中")
            return

        raise RuleValidationError("角色行动使用了未知授权方式")

    @staticmethod
    def _resolve_static_rule(
        target: str | None,
        context: CheckContext,
    ) -> CheckRule | None:
        if target is None:
            return None
        if target not in context.rules_by_target:
            raise RuleValidationError("检定目标未对应当前场景中的静态规则")
        return context.rules_by_target[target]

    @staticmethod
    def _validate_skill(
        request: RequestCheckArgs,
        context: CheckContext,
        rule: CheckRule | None,
    ) -> None:
        if rule is not None and request.suggested_skill not in rule.allowed_skills:
            raise RuleValidationError("建议技能不适用于当前静态规则")
        if request.actor_id not in context.actor_skills:
            raise RuleValidationError("行动者没有可信技能配置")
        if request.suggested_skill not in context.actor_skills[request.actor_id]:
            raise RuleValidationError("行动者不具备建议技能")

    @staticmethod
    def _validate_modifier_reasons(
        request: RequestCheckArgs,
        context: CheckContext,
    ) -> None:
        if request.suggested_context_modifier != 0 and not request.modifier_reasons:
            raise RuleValidationError("非零语境修正必须提供可信理由")

        for reason in request.modifier_reasons:
            source = context.modifier_sources.get(reason.source_id)
            if source is None or reason.reason_tag not in source.allowed_reason_tags:
                raise RuleValidationError("语境修正理由没有可信来源")
