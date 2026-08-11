"""实现 Agentic MVP 由 Harness 拥有的可信 COC 检定。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

MAKE_CHECK_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "make_check",
        "description": "从冻结角色卡执行一次 COC 7e 技能或属性检定。",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "actor_id",
                "ability",
                "difficulty",
                "dice_adjustment",
                "action",
                "stakes",
                "visibility",
            ],
            "properties": {
                "actor_id": {"type": "string", "minLength": 1},
                "ability": {"type": "string", "minLength": 1},
                "difficulty": {
                    "type": "string",
                    "enum": ["regular", "hard", "extreme"],
                },
                "dice_adjustment": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "count"],
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["none", "bonus", "penalty"],
                        },
                        "count": {"type": "integer", "minimum": 0, "maximum": 2},
                    },
                },
                "action": {"type": "string", "minLength": 1},
                "stakes": {"type": "string", "minLength": 1},
                "visibility": {
                    "type": "string",
                    "enum": ["public", "hidden"],
                },
            },
        },
    },
}

PUSH_CHECK_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "push_check",
        "description": "关联一次失败检定并执行玩家选择的孤注一掷。",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["check_id", "new_approach", "failure_stakes"],
            "properties": {
                "check_id": {"type": "string", "minLength": 1},
                "new_approach": {"type": "string", "minLength": 1},
                "failure_stakes": {"type": "string", "minLength": 1},
            },
        },
    },
}

SPEND_LUCK_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "spend_luck",
        "description": "按玩家明确选择为一次失败检定花费幸运。",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["check_id", "points"],
            "properties": {
                "check_id": {"type": "string", "minLength": 1},
                "points": {"type": "integer", "minimum": 1},
            },
        },
    },
}

DEAL_DAMAGE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "deal_damage",
        "description": "按受限伤害表达式结算一次伤害与 HP 变化。",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "actor_id",
                "damage_expression",
                "cause",
                "armor_applies",
                "visibility",
            ],
            "properties": {
                "actor_id": {"type": "string", "minLength": 1},
                "damage_expression": {"type": "string", "minLength": 1},
                "cause": {"type": "string", "minLength": 1},
                "armor_applies": {"type": "boolean"},
                "visibility": {
                    "type": "string",
                    "enum": ["public", "hidden"],
                },
            },
        },
    },
}

MAKE_SANITY_CHECK_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "make_sanity_check",
        "description": "按恐怖来源及成功/失败损失表达式结算一次 SAN 检定。",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "actor_id",
                "source",
                "success_loss",
                "failure_loss",
                "visibility",
            ],
            "properties": {
                "actor_id": {"type": "string", "minLength": 1},
                "source": {"type": "string", "minLength": 1},
                "success_loss": {"type": "string", "minLength": 1},
                "failure_loss": {"type": "string", "minLength": 1},
                "visibility": {
                    "type": "string",
                    "enum": ["public", "hidden"],
                },
            },
        },
    },
}

_ARGUMENT_FIELDS = frozenset(
    {
        "actor_id",
        "ability",
        "difficulty",
        "dice_adjustment",
        "action",
        "stakes",
        "visibility",
    }
)
_DICE_ADJUSTMENT_FIELDS = frozenset({"kind", "count"})
_CHECK_RESULT_FIELDS = frozenset(
    {
        "mechanic_id",
        "kind",
        "actor_id",
        "ability",
        "ability_value",
        "difficulty",
        "target",
        "dice_adjustment",
        "roll",
        "success_level",
        "action",
        "stakes",
        "visibility",
        "push_eligible",
        "luck_eligible",
        "committed_at",
    }
)
_DIFFICULTIES = frozenset({"regular", "hard", "extreme"})
_SUCCESS_LEVELS = frozenset(
    {
        "critical_success",
        "extreme_success",
        "hard_success",
        "regular_success",
        "failure",
        "fumble",
    }
)
class RandomSource(Protocol):
    """声明 COC d10 所需的最小可注入随机边界。"""

    def randint(self, minimum: int, maximum: int) -> int: ...


@dataclass(frozen=True)
class ToolExecution:
    """工具在一次原子提交中交付的机械与角色卡新值。"""

    mechanic: Mapping[str, Any]
    actors: list[dict[str, Any]]


class CocTool(Protocol):
    """其余 COC 工具接入同一 Harness 生命周期所需的窄接口。"""

    @property
    def definition(self) -> Mapping[str, Any]: ...

    @property
    def mechanic_kind(self) -> str: ...

    def normalize(self, arguments_raw: str) -> dict[str, Any]: ...

    def preflight(
        self,
        arguments: Mapping[str, Any],
        *,
        actors: object,
        mechanics: tuple[Mapping[str, Any], ...],
    ) -> object: ...

    def execute(
        self,
        prepared: object,
        *,
        mechanic_id: str,
        random_source: RandomSource,
        committed_at: str,
    ) -> ToolExecution: ...

    def validate_result(self, value: object) -> None: ...

    def validate_result_arguments(
        self,
        arguments: Mapping[str, Any],
        value: Mapping[str, Any],
    ) -> None: ...

    def validate_persistence(
        self,
        value: Mapping[str, Any],
        *,
        actors: object,
        mechanics: tuple[Mapping[str, Any], ...],
    ) -> None: ...

    def public_details(self, value: Mapping[str, Any]) -> Mapping[str, Any]: ...


class CocToolError(ValueError):
    """携带可安全返回同一个 GM 的稳定工具错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


MakeCheckError = CocToolError


@dataclass(frozen=True)
class PreparedCheck:
    """冻结所有事前输入，之后才允许读取随机源。"""

    arguments: Mapping[str, Any]
    ability_value: int
    target: int


@dataclass(frozen=True)
class PreparedMakeCheck:
    """保存 make_check 的领域快照，执行阶段不再读取会话状态。"""

    check: PreparedCheck
    actors: list[dict[str, Any]]


def normalize_make_check_arguments(arguments_raw: str) -> dict[str, Any]:
    """解析并规范化完整参数；失败时绝不触及随机源。"""

    try:
        value = json.loads(arguments_raw)
    except json.JSONDecodeError as error:
        raise MakeCheckError("invalid_arguments", "make_check 参数不是合法 JSON") from error
    if not isinstance(value, dict) or set(value) != _ARGUMENT_FIELDS:
        raise MakeCheckError("invalid_arguments", "make_check 参数字段无效")

    normalized: dict[str, Any] = {}
    for field in ("actor_id", "ability", "action", "stakes"):
        normalized[field] = _required_string(value.get(field), field)
    difficulty = value.get("difficulty")
    if not isinstance(difficulty, str) or difficulty not in _DIFFICULTIES:
        raise MakeCheckError("invalid_difficulty", "difficulty 必须是原生 COC 难度")
    normalized["difficulty"] = difficulty

    adjustment = value.get("dice_adjustment")
    if not isinstance(adjustment, dict) or set(adjustment) != _DICE_ADJUSTMENT_FIELDS:
        raise MakeCheckError("invalid_dice_adjustment", "dice_adjustment 格式无效")
    kind = adjustment.get("kind")
    count = adjustment.get("count")
    if (
        not isinstance(kind, str)
        or kind not in {"none", "bonus", "penalty"}
        or not isinstance(count, int)
        or isinstance(count, bool)
        or kind == "none"
        and count != 0
        or kind != "none"
        and count not in {1, 2}
    ):
        raise MakeCheckError("invalid_dice_adjustment", "dice_adjustment 格式无效")
    normalized["dice_adjustment"] = {"kind": kind, "count": count}

    visibility = value.get("visibility")
    if not isinstance(visibility, str) or visibility not in {"public", "hidden"}:
        raise MakeCheckError("invalid_visibility", "visibility 必须是 public 或 hidden")
    normalized["visibility"] = visibility
    return normalized


def prepare_make_check(
    arguments: Mapping[str, Any],
    actors: object,
) -> PreparedCheck:
    """只从本局冻结角色卡读取能力值并计算原生难度目标。"""

    if not isinstance(actors, list):
        raise MakeCheckError("actor_data_unavailable", "冻结角色卡不可用")
    actor_id = arguments["actor_id"]
    actor = next(
        (
            candidate
            for candidate in actors
            if isinstance(candidate, dict) and candidate.get("actor_id") == actor_id
        ),
        None,
    )
    if actor is None:
        raise MakeCheckError("unknown_actor", f"未找到角色 {actor_id}")

    ability = arguments["ability"]
    attributes = actor.get("attributes")
    skills = actor.get("skills")
    ability_value: object = None
    if isinstance(attributes, dict) and ability in attributes:
        ability_value = attributes[ability]
    elif isinstance(skills, dict) and ability in skills:
        ability_value = skills[ability]
    if (
        not isinstance(ability_value, int)
        or isinstance(ability_value, bool)
        or not 0 <= ability_value <= 100
    ):
        raise MakeCheckError(
            "unknown_ability",
            f"角色 {actor_id} 没有能力 {ability}",
        )

    difficulty = arguments["difficulty"]
    target = _target_for_difficulty(ability_value, difficulty)
    return PreparedCheck(
        arguments=dict(arguments),
        ability_value=ability_value,
        target=target,
    )


def resolve_prepared_check(
    prepared: PreparedCheck,
    *,
    mechanic_id: str,
    random_source: RandomSource,
    committed_at: str,
) -> dict[str, Any]:
    """在事前参数已冻结后掷奖励/惩罚 d100 并形成不可变记录。"""

    adjustment = prepared.arguments["dice_adjustment"]
    roll = _roll_d100(random_source, adjustment["kind"], adjustment["count"])
    success_level = _success_level(roll, prepared.ability_value)
    succeeded = _meets_difficulty(success_level, prepared.arguments["difficulty"])
    return {
        "mechanic_id": mechanic_id,
        "kind": "check",
        "actor_id": prepared.arguments["actor_id"],
        "ability": prepared.arguments["ability"],
        "ability_value": prepared.ability_value,
        "difficulty": prepared.arguments["difficulty"],
        "target": prepared.target,
        "dice_adjustment": dict(adjustment),
        "roll": roll,
        "success_level": success_level,
        "action": prepared.arguments["action"],
        "stakes": prepared.arguments["stakes"],
        "visibility": prepared.arguments["visibility"],
        "push_eligible": not succeeded,
        "luck_eligible": not succeeded and success_level != "fumble",
        "committed_at": committed_at,
    }


class MakeCheckTool:
    """把既有 make_check 规则适配到共同工具生命周期。"""

    definition = MAKE_CHECK_TOOL
    mechanic_kind = "check"

    def normalize(self, arguments_raw: str) -> dict[str, Any]:
        return normalize_make_check_arguments(arguments_raw)

    def preflight(
        self,
        arguments: Mapping[str, Any],
        *,
        actors: object,
        mechanics: tuple[Mapping[str, Any], ...],
    ) -> PreparedMakeCheck:
        del mechanics
        check = prepare_make_check(arguments, actors)
        if not isinstance(actors, list):
            raise CocToolError("actor_data_unavailable", "冻结角色卡不可用")
        return PreparedMakeCheck(
            check=check,
            actors=json.loads(json.dumps(actors)),
        )

    def execute(
        self,
        prepared: object,
        *,
        mechanic_id: str,
        random_source: RandomSource,
        committed_at: str,
    ) -> ToolExecution:
        if not isinstance(prepared, PreparedMakeCheck):
            raise ValueError("make_check 冻结输入无效")
        mechanic = resolve_prepared_check(
            prepared.check,
            mechanic_id=mechanic_id,
            random_source=random_source,
            committed_at=committed_at,
        )
        return ToolExecution(
            mechanic=mechanic,
            actors=json.loads(json.dumps(prepared.actors)),
        )

    def validate_result(self, value: object) -> None:
        validate_check_result(value)

    @staticmethod
    def validate_result_arguments(
        arguments: Mapping[str, Any],
        value: Mapping[str, Any],
    ) -> None:
        if any(
            arguments.get(field) != value.get(field)
            for field in _ARGUMENT_FIELDS
        ):
            raise ValueError("check mechanic 与规范参数不一致")

    @staticmethod
    def validate_persistence(
        value: Mapping[str, Any],
        *,
        actors: object,
        mechanics: tuple[Mapping[str, Any], ...],
    ) -> None:
        """证明检定保存的能力值来自本局冻结角色卡。"""

        del mechanics
        prepared = prepare_make_check(value, actors)
        if (
            value.get("ability_value") != prepared.ability_value
            or value.get("target") != prepared.target
        ):
            raise ValueError("check mechanic 与冻结角色卡不一致")

    @staticmethod
    def public_details(value: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            key: value[key]
            for key in (
                "ability",
                "ability_value",
                "difficulty",
                "target",
                "dice_adjustment",
                "roll",
                "success_level",
                "action",
                "stakes",
            )
        }


def _parse_tool_arguments(
    arguments_raw: str,
    *,
    fields: frozenset[str],
    tool_name: str,
) -> dict[str, Any]:
    try:
        value = json.loads(arguments_raw)
    except json.JSONDecodeError as error:
        raise CocToolError(
            "invalid_arguments",
            f"{tool_name} 参数不是合法 JSON",
        ) from error
    if not isinstance(value, dict) or set(value) != fields:
        raise CocToolError("invalid_arguments", f"{tool_name} 参数字段无效")
    return value


def normalize_push_check_arguments(arguments_raw: str) -> dict[str, Any]:
    value = _parse_tool_arguments(
        arguments_raw,
        fields=frozenset({"check_id", "new_approach", "failure_stakes"}),
        tool_name="push_check",
    )
    return {
        field: _required_string(value.get(field), field)
        for field in ("check_id", "new_approach", "failure_stakes")
    }


def normalize_spend_luck_arguments(arguments_raw: str) -> dict[str, Any]:
    value = _parse_tool_arguments(
        arguments_raw,
        fields=frozenset({"check_id", "points"}),
        tool_name="spend_luck",
    )
    points = value.get("points")
    if not isinstance(points, int) or isinstance(points, bool) or points < 1:
        raise CocToolError("invalid_arguments", "points 必须是正整数")
    return {
        "check_id": _required_string(value.get("check_id"), "check_id"),
        "points": points,
    }


def normalize_deal_damage_arguments(arguments_raw: str) -> dict[str, Any]:
    value = _parse_tool_arguments(
        arguments_raw,
        fields=frozenset(
            {
                "actor_id",
                "damage_expression",
                "cause",
                "armor_applies",
                "visibility",
            }
        ),
        tool_name="deal_damage",
    )
    armor_applies = value.get("armor_applies")
    visibility = value.get("visibility")
    if not isinstance(armor_applies, bool):
        raise CocToolError("invalid_arguments", "armor_applies 必须是布尔值")
    if visibility not in {"public", "hidden"}:
        raise CocToolError("invalid_arguments", "visibility 必须是 public 或 hidden")
    return {
        "actor_id": _required_string(value.get("actor_id"), "actor_id"),
        "damage_expression": _required_string(
            value.get("damage_expression"),
            "damage_expression",
        ),
        "cause": _required_string(value.get("cause"), "cause"),
        "armor_applies": armor_applies,
        "visibility": visibility,
    }


def normalize_make_sanity_check_arguments(arguments_raw: str) -> dict[str, Any]:
    value = _parse_tool_arguments(
        arguments_raw,
        fields=frozenset(
            {"actor_id", "source", "success_loss", "failure_loss", "visibility"}
        ),
        tool_name="make_sanity_check",
    )
    visibility = value.get("visibility")
    if visibility not in {"public", "hidden"}:
        raise CocToolError("invalid_arguments", "visibility 必须是 public 或 hidden")
    return {
        field: _required_string(value.get(field), field)
        for field in ("actor_id", "source", "success_loss", "failure_loss")
    } | {"visibility": visibility}


@dataclass(frozen=True)
class UnimplementedCocTool:
    """为后续票保留规范目录项，但不提前实现具体 COC 规则。"""

    definition: Mapping[str, Any]
    mechanic_kind: str
    argument_normalizer: Callable[[str], dict[str, Any]]

    @property
    def name(self) -> str:
        return str(self.definition["function"]["name"])

    def normalize(self, arguments_raw: str) -> dict[str, Any]:
        return self.argument_normalizer(arguments_raw)

    def preflight(
        self,
        arguments: Mapping[str, Any],
        *,
        actors: object,
        mechanics: tuple[Mapping[str, Any], ...],
    ) -> object:
        del arguments, actors, mechanics
        raise CocToolError("tool_not_implemented", f"工具 {self.name} 尚未实现")

    def execute(
        self,
        prepared: object,
        *,
        mechanic_id: str,
        random_source: RandomSource,
        committed_at: str,
    ) -> ToolExecution:
        del prepared, mechanic_id, random_source, committed_at
        raise ValueError("未实现工具不应进入 execute")

    def validate_result(self, value: object) -> None:
        del value
        raise ValueError(f"工具 {self.name} 尚未实现")

    def validate_result_arguments(
        self,
        arguments: Mapping[str, Any],
        value: Mapping[str, Any],
    ) -> None:
        del arguments, value
        raise ValueError(f"工具 {self.name} 尚未实现")

    def validate_persistence(
        self,
        value: Mapping[str, Any],
        *,
        actors: object,
        mechanics: tuple[Mapping[str, Any], ...],
    ) -> None:
        del value, actors, mechanics
        raise ValueError(f"工具 {self.name} 尚未实现")

    def public_details(self, value: Mapping[str, Any]) -> Mapping[str, Any]:
        del value
        raise ValueError(f"工具 {self.name} 尚未实现")


DEFAULT_COC_TOOLS: Mapping[str, CocTool] = {
    "make_check": MakeCheckTool(),
    "push_check": UnimplementedCocTool(
        PUSH_CHECK_TOOL,
        "check",
        normalize_push_check_arguments,
    ),
    "spend_luck": UnimplementedCocTool(
        SPEND_LUCK_TOOL,
        "luck_spend",
        normalize_spend_luck_arguments,
    ),
    "deal_damage": UnimplementedCocTool(
        DEAL_DAMAGE_TOOL,
        "damage",
        normalize_deal_damage_arguments,
    ),
    "make_sanity_check": UnimplementedCocTool(
        MAKE_SANITY_CHECK_TOOL,
        "sanity_check",
        normalize_make_sanity_check_arguments,
    ),
}


def validate_check_result(value: object) -> None:
    """严格校验持久化机械，并复算所有可推导字段。"""

    if not isinstance(value, dict) or set(value) != _CHECK_RESULT_FIELDS:
        raise ValueError("check mechanic 格式无效")
    for field in ("mechanic_id", "actor_id", "ability", "action", "stakes", "committed_at"):
        _required_string(value.get(field), field)
    ability_value = value.get("ability_value")
    roll = value.get("roll")
    difficulty = value.get("difficulty")
    target = value.get("target")
    if (
        value.get("kind") != "check"
        or not isinstance(ability_value, int)
        or isinstance(ability_value, bool)
        or not 0 <= ability_value <= 100
        or not isinstance(difficulty, str)
        or difficulty not in _DIFFICULTIES
        or not isinstance(roll, int)
        or isinstance(roll, bool)
        or not 1 <= roll <= 100
        or not isinstance(target, int)
        or isinstance(target, bool)
        or value.get("visibility") not in {"public", "hidden"}
        or value.get("success_level") not in _SUCCESS_LEVELS
    ):
        raise ValueError("check mechanic 格式无效")
    adjustment = value.get("dice_adjustment")
    if not isinstance(adjustment, dict):
        raise ValueError("check mechanic 格式无效")
    normalized_adjustment = normalize_make_check_arguments(
        json.dumps(
            {
                "actor_id": value["actor_id"],
                "ability": value["ability"],
                "difficulty": difficulty,
                "dice_adjustment": adjustment,
                "action": value["action"],
                "stakes": value["stakes"],
                "visibility": value["visibility"],
            }
        )
    )["dice_adjustment"]
    success_level = _success_level(roll, ability_value)
    succeeded = _meets_difficulty(success_level, difficulty)
    if (
        value.get("target") != _target_for_difficulty(ability_value, difficulty)
        or value.get("dice_adjustment") != normalized_adjustment
        or value.get("success_level") != success_level
        or value.get("push_eligible") is not (not succeeded)
        or value.get("luck_eligible") is not (
            not succeeded and success_level != "fumble"
        )
    ):
        raise ValueError("check mechanic 格式无效")


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise MakeCheckError("invalid_arguments", f"{label} 必须是非空无首尾空白字符串")
    return value


def _target_for_difficulty(ability_value: int, difficulty: str) -> int:
    if difficulty == "regular":
        return ability_value
    if difficulty == "hard":
        return ability_value // 2
    return ability_value // 5


def _roll_d100(random_source: RandomSource, kind: str, count: int) -> int:
    units = random_source.randint(0, 9)
    tens_values = [random_source.randint(0, 9) for _ in range(count + 1)]
    candidates = [100 if tens == 0 and units == 0 else tens * 10 + units for tens in tens_values]
    if kind == "bonus":
        return min(candidates)
    if kind == "penalty":
        return max(candidates)
    return candidates[0]


def _success_level(roll: int, ability_value: int) -> str:
    if roll == 1:
        return "critical_success"
    if roll == 100 or ability_value < 50 and roll >= 96:
        return "fumble"
    if roll <= ability_value // 5:
        return "extreme_success"
    if roll <= ability_value // 2:
        return "hard_success"
    if roll <= ability_value:
        return "regular_success"
    return "failure"


def _meets_difficulty(success_level: str, difficulty: str) -> bool:
    ranks = {
        "fumble": 0,
        "failure": 0,
        "regular_success": 1,
        "hard_success": 2,
        "extreme_success": 3,
        "critical_success": 4,
    }
    required = {"regular": 1, "hard": 2, "extreme": 3}
    return ranks[success_level] >= required[difficulty]
