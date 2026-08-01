"""提供 GM 与可信规则模块之间的受限工具边界。"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from monmusu_agent.config import AppPaths
from monmusu_agent.rules import (
    CheckRequestRejected,
    CheckResult,
    ModifierReason,
    RequestCheckArgs,
    RuleEngine,
    build_check_context,
)
from monmusu_agent.state import (
    ApplyEffectArgs,
    CheckEffectSource,
    CommitResult,
    ModuleEventSource,
    StateCommitter,
)
from monmusu_agent.storage import read_json


_MODIFIER_REASON_TAGS = (
    "relevant_clue",
    "useful_equipment",
    "good_position",
    "poor_position",
    "active_condition",
    "time_pressure",
    "unsupported_approach",
)
_AUTHORIZATION_TYPES = (
    "user_declared",
    "user_delegated",
)
_SUPPORTED_TOOL_NAMES = frozenset({"request_check", "apply_effect"})


def _freeze_snapshot(value: Any) -> Any:
    """递归复制并冻结 ToolSession 对外暴露的 JSON 快照。"""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_snapshot(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_snapshot(item) for item in value)
    return copy.deepcopy(value)


@dataclass(frozen=True)
class TurnContext:
    """保存由 GameEngine 在回合开始时确定的可信输入。"""

    turn_id: str
    input_text: str
    initial_game_state: Mapping[str, Any]
    max_tool_steps: int
    tool_limits: Mapping[str, int]


@dataclass(frozen=True)
class ToolError:
    """表示可安全返回给 GM 的工具调用错误。"""

    code: str
    message: str
    retryable: bool


@dataclass(frozen=True)
class ToolResult:
    """统一包装一次 GM 工具调用的结果。"""

    tool_call_id: str
    tool_name: str
    ok: bool
    data: Mapping[str, Any] | None
    error: ToolError | None


@dataclass(frozen=True)
class ToolDefinition:
    """描述可安全暴露给 GM 的单个工具及其参数 schema。"""

    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True)
class ToolTraceEntry:
    """记录一次工具调用的规范化输入、分发状态与返回结果。"""

    sequence: int
    tool_call_id: str
    tool_name: str
    normalized_arguments: RequestCheckArgs | ApplyEffectArgs | None
    dispatched: bool
    tool_result: ToolResult


class _InvalidToolArguments(ValueError):
    """表示工具参数未通过结构预检。"""


class ToolExecutor:
    """装配静态配置与可信模块，并为每回合创建工具会话。"""

    def __init__(
        self,
        *,
        paths: AppPaths,
        rule_engine: RuleEngine,
        state_committer: StateCommitter,
    ) -> None:
        self.paths = paths
        self.rule_engine = rule_engine
        self.state_committer = state_committer
        self.module = read_json(paths.module_file)
        self.character_profiles = read_json(paths.characters_file)

    def start_turn(self, context: TurnContext) -> ToolSession:
        """创建只服务于当前回合的工具调用会话。"""

        return ToolSession(executor=self, context=context)


class ToolSession:
    """执行单个回合内的受限 GM 工具调用。"""

    def __init__(self, *, executor: ToolExecutor, context: TurnContext) -> None:
        self.executor = executor
        self.context = context
        self._call_sequence = 0
        self._current_game_state = copy.deepcopy(context.initial_game_state)
        self._remaining_steps = context.max_tool_steps
        self._remaining_tool_calls = dict(context.tool_limits)
        self._trace: list[ToolTraceEntry] = []

    @property
    def current_state_version(self) -> int:
        """返回本会话当前确认的正式状态版本。"""

        return self._current_game_state["state_version"]

    @property
    def final_state_snapshot(self) -> Mapping[str, Any]:
        """返回随效果提交刷新、且调用方无法改写的最终状态快照。"""

        return _freeze_snapshot(self._current_game_state)

    @property
    def trace(self) -> tuple[ToolTraceEntry, ...]:
        """返回本回合已记录的工具调用轨迹。"""

        return tuple(self._trace)

    def available_tool_definitions(self) -> tuple[ToolDefinition, ...]:
        """返回本会话此刻仍可供 GM 调用的工具定义。"""

        if self._remaining_steps <= 0:
            return ()

        definitions: list[ToolDefinition] = []
        for tool_name in ("request_check", "apply_effect"):
            if self._remaining_tool_calls.get(tool_name, 0) > 0:
                definitions.append(_tool_definition(tool_name))
        return tuple(definitions)

    def _preflight_failure(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        code: str,
        message: str,
        retryable: bool,
    ) -> ToolResult:
        """记录尚未分发给可信模块的工具调用失败。"""

        result = ToolResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            ok=False,
            data=None,
            error=ToolError(
                code=code,
                message=message,
                retryable=retryable,
            ),
        )
        self._trace.append(
            ToolTraceEntry(
                sequence=self._call_sequence,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                normalized_arguments=None,
                dispatched=False,
                tool_result=result,
            )
        )
        return result

    def _record_dispatched_result(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        normalized_arguments: RequestCheckArgs | ApplyEffectArgs,
        result: ToolResult,
    ) -> ToolResult:
        """记录已经进入可信模块的工具调用，并返回原始结果。"""

        self._trace.append(
            ToolTraceEntry(
                sequence=self._call_sequence,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                normalized_arguments=normalized_arguments,
                dispatched=True,
                tool_result=result,
            )
        )
        return result

    def execute(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        """执行当前切片支持的 GM 工具。"""

        self._call_sequence += 1
        tool_call_id = f"tool_{self.context.turn_id}_{self._call_sequence:02d}"
        if self._remaining_steps <= 0:
            return self._preflight_failure(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                code="budget_exceeded",
                message="当前回合的工具预算已经耗尽",
                retryable=False,
            )
        self._remaining_steps -= 1
        if (
            tool_name not in _SUPPORTED_TOOL_NAMES
            or tool_name not in self._remaining_tool_calls
        ):
            return self._preflight_failure(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                code="tool_not_allowed",
                message="该工具不在当前回合的允许范围内",
                retryable=False,
            )
        if self._remaining_tool_calls.get(tool_name, 0) <= 0:
            return self._preflight_failure(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                code="budget_exceeded",
                message="当前回合的工具预算已经耗尽",
                retryable=False,
            )
        if not isinstance(arguments, Mapping):
            return self._preflight_failure(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                code="invalid_arguments",
                message="工具参数必须是对象",
                retryable=True,
            )
        if tool_name == "apply_effect":
            try:
                apply_effect = _parse_apply_effect_args(arguments)
            except (KeyError, _InvalidToolArguments) as error:
                return self._preflight_failure(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    code="invalid_arguments",
                    message=_invalid_argument_message(error),
                    retryable=True,
                )
            return self._execute_apply_effect(tool_call_id, apply_effect)

        try:
            request = _parse_request_check_args(arguments)
        except (KeyError, _InvalidToolArguments) as error:
            return self._preflight_failure(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                code="invalid_arguments",
                message=_invalid_argument_message(error),
                retryable=True,
            )
        check_context = build_check_context(
            game_state=self._current_game_state,
            module=self.executor.module,
            character_profiles=self.executor.character_profiles,
            turn_id=self.context.turn_id,
            input_text=self.context.input_text,
        )
        self._remaining_tool_calls[tool_name] -= 1
        try:
            check_result = self.executor.rule_engine.resolve_check(
                request,
                check_context,
            )
        except CheckRequestRejected as error:
            result = ToolResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                ok=False,
                data=None,
                error=ToolError(
                    code="rule_rejected",
                    message=str(error),
                    retryable=True,
                ),
            )
            return self._record_dispatched_result(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                normalized_arguments=request,
                result=result,
            )
        result = ToolResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            ok=True,
            data=_serialize_check_result(check_result),
            error=None,
        )
        return self._record_dispatched_result(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            normalized_arguments=request,
            result=result,
        )

    def _execute_apply_effect(
        self,
        tool_call_id: str,
        arguments: ApplyEffectArgs,
    ) -> ToolResult:
        self._remaining_tool_calls["apply_effect"] -= 1
        commit_result = self.executor.state_committer.apply_effect(
            arguments,
            turn_id=self.context.turn_id,
        )
        self._current_game_state = read_json(self.executor.paths.game_state_file)
        result = ToolResult(
            tool_call_id=tool_call_id,
            tool_name="apply_effect",
            ok=True,
            data=_serialize_commit_result(
                commit_result,
                context_delta=_derive_context_delta(
                    commit_result,
                    self.executor.module,
                ),
            ),
            error=None,
        )
        return self._record_dispatched_result(
            tool_call_id=tool_call_id,
            tool_name="apply_effect",
            normalized_arguments=arguments,
            result=result,
        )


def _serialize_check_result(result: CheckResult) -> dict[str, Any]:
    """把可信检定结果转换为可传给 GM 的 JSON 对象。"""

    return {
        "kind": "check_result",
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


def _tool_definition(tool_name: str) -> ToolDefinition:
    """构造一份独立的工具定义，避免调用方改写共享 schema。"""

    if tool_name == "request_check":
        return ToolDefinition(
            name=tool_name,
            description="请求可信规则引擎执行一次 d100 检定。",
            input_schema={
                "type": "object",
                "required": [
                    "actor_id",
                    "actor_type",
                    "action",
                    "target",
                    "suggested_skill",
                    "suggested_context_modifier",
                    "modifier_reasons",
                    "authorization",
                    "authorization_evidence",
                ],
                "properties": {
                    "actor_id": {"type": "string", "minLength": 1},
                    "actor_type": {
                        "type": "string",
                        "enum": ["user", "character"],
                    },
                    "action": {"type": "string", "minLength": 1},
                    "target": {"type": ["string", "null"]},
                    "suggested_skill": {"type": "string", "minLength": 1},
                    "suggested_context_modifier": {
                        "type": "integer",
                        "minimum": -10,
                        "maximum": 10,
                    },
                    "modifier_reasons": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["reason_tag", "source_id"],
                            "properties": {
                                "reason_tag": {
                                    "type": "string",
                                    "enum": list(_MODIFIER_REASON_TAGS),
                                },
                                "source_id": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "authorization": {
                        "type": "string",
                        "enum": list(_AUTHORIZATION_TYPES),
                    },
                    "authorization_evidence": {
                        "type": "string",
                        "minLength": 1,
                    },
                },
                "additionalProperties": False,
            },
        )
    if tool_name == "apply_effect":
        return ToolDefinition(
            name=tool_name,
            description="申请提交一项由检定或模组事件授权的固定效果。",
            input_schema={
                "type": "object",
                "required": [
                    "expected_state_version",
                    "source_type",
                    "source_id",
                    "effect_id",
                    "reason",
                ],
                "properties": {
                    "expected_state_version": {
                        "type": "integer",
                        "minimum": 0,
                    },
                    "source_type": {
                        "type": "string",
                        "enum": ["check", "module_event"],
                    },
                    "source_id": {"type": "string", "minLength": 1},
                    "effect_id": {"type": "string", "minLength": 1},
                    "reason": {"type": "string", "minLength": 1},
                },
                "additionalProperties": False,
            },
        )
    raise ValueError(f"未知的工具定义：{tool_name}")


def _parse_request_check_args(
    arguments: Mapping[str, Any],
) -> RequestCheckArgs:
    """把工具参数转换为规则引擎需要的检定候选。"""

    allowed_fields = {
        "actor_id",
        "actor_type",
        "action",
        "target",
        "suggested_skill",
        "suggested_context_modifier",
        "modifier_reasons",
        "authorization",
        "authorization_evidence",
    }
    unknown_fields = set(arguments) - allowed_fields
    if unknown_fields:
        fields = "、".join(sorted(str(field) for field in unknown_fields))
        raise _InvalidToolArguments(f"包含未知字段：{fields}")

    actor_id = _require_non_empty_string(arguments, "actor_id")
    action = _require_non_empty_string(arguments, "action")
    suggested_skill = _require_non_empty_string(arguments, "suggested_skill")
    authorization_evidence = _require_non_empty_string(
        arguments,
        "authorization_evidence",
    )
    target = arguments["target"]
    if target is not None and not isinstance(target, str):
        raise _InvalidToolArguments("target 必须是字符串或 null")

    actor_type = arguments["actor_type"]
    if (
        not isinstance(actor_type, str)
        or actor_type not in {"user", "character"}
    ):
        raise _InvalidToolArguments("actor_type 必须是 user 或 character")

    authorization = arguments["authorization"]
    if (
        not isinstance(authorization, str)
        or authorization not in _AUTHORIZATION_TYPES
    ):
        raise _InvalidToolArguments("authorization 无效")

    suggested_context_modifier = arguments["suggested_context_modifier"]
    if (
        not isinstance(suggested_context_modifier, int)
        or isinstance(suggested_context_modifier, bool)
        or not -10 <= suggested_context_modifier <= 10
    ):
        raise _InvalidToolArguments(
            "suggested_context_modifier 必须是 -10 到 10 的整数",
        )

    raw_modifier_reasons = arguments["modifier_reasons"]
    if not isinstance(raw_modifier_reasons, list):
        raise _InvalidToolArguments("modifier_reasons 必须是数组")
    modifier_reasons: list[ModifierReason] = []
    for index, reason in enumerate(raw_modifier_reasons):
        if not isinstance(reason, Mapping):
            raise _InvalidToolArguments(
                f"modifier_reasons[{index}] 必须是对象",
            )
        unknown_reason_fields = set(reason) - {"reason_tag", "source_id"}
        if unknown_reason_fields:
            fields = "、".join(
                sorted(str(field) for field in unknown_reason_fields),
            )
            raise _InvalidToolArguments(
                f"modifier_reasons[{index}] 包含未知字段：{fields}",
            )
        reason_tag = reason["reason_tag"]
        if (
            not isinstance(reason_tag, str)
            or reason_tag not in _MODIFIER_REASON_TAGS
        ):
            raise _InvalidToolArguments(
                f"modifier_reasons[{index}].reason_tag 无效",
            )
        source_id = reason["source_id"]
        if not isinstance(source_id, str) or not source_id:
            raise _InvalidToolArguments(
                f"modifier_reasons[{index}].source_id 必须是非空字符串",
            )
        modifier_reasons.append(
            ModifierReason(
                reason_tag=reason_tag,
                source_id=source_id,
            )
        )

    return RequestCheckArgs(
        actor_id=actor_id,
        actor_type=actor_type,
        action=action,
        target=target,
        suggested_skill=suggested_skill,
        suggested_context_modifier=suggested_context_modifier,
        modifier_reasons=tuple(modifier_reasons),
        authorization=authorization,
        authorization_evidence=authorization_evidence,
    )


def _parse_apply_effect_args(
    arguments: Mapping[str, Any],
) -> ApplyEffectArgs:
    """把工具参数转换为 StateCommitter 的单效果申请。"""

    allowed_fields = {
        "expected_state_version",
        "source_type",
        "source_id",
        "effect_id",
        "reason",
    }
    unknown_fields = set(arguments) - allowed_fields
    if unknown_fields:
        fields = "、".join(sorted(str(field) for field in unknown_fields))
        raise _InvalidToolArguments(f"包含未知字段：{fields}")

    expected_state_version = arguments["expected_state_version"]
    if (
        not isinstance(expected_state_version, int)
        or isinstance(expected_state_version, bool)
        or expected_state_version < 0
    ):
        raise _InvalidToolArguments("expected_state_version 必须是非负整数")
    source_type = arguments["source_type"]
    if source_type == "check":
        source = CheckEffectSource(
            check_id=_require_non_empty_string(arguments, "source_id"),
        )
    elif source_type == "module_event":
        source = ModuleEventSource(
            event_rule_id=_require_non_empty_string(arguments, "source_id"),
        )
    else:
        raise _InvalidToolArguments(
            "source_type 必须是 check 或 module_event",
        )

    return ApplyEffectArgs(
        expected_state_version=expected_state_version,
        source=source,
        effect_id=_require_non_empty_string(arguments, "effect_id"),
        reason=_require_non_empty_string(arguments, "reason"),
    )


def _require_non_empty_string(
    arguments: Mapping[str, Any],
    field_name: str,
) -> str:
    """读取一个必填字符串字段，并拒绝空值与非字符串。"""

    value = arguments[field_name]
    if not isinstance(value, str) or not value:
        raise _InvalidToolArguments(f"{field_name} 必须是非空字符串")
    return value


def _invalid_argument_message(
    error: KeyError | _InvalidToolArguments,
) -> str:
    """将解析错误转成可安全返回给 GM 的说明。"""

    if isinstance(error, KeyError):
        return f"缺少必填字段：{error.args[0]}"
    return str(error)


def _derive_context_delta(
    result: CommitResult,
    module: Mapping[str, Any],
) -> dict[str, Any] | None:
    """从可信提交变化与静态定义派生模型可见的新上下文。"""

    if result.status not in {"applied", "already_applied"}:
        return None

    revealed_clue_ids: list[str] = []
    entered_scene_id: str | None = None
    for change in result.changes:
        if (
            change.path == "clues_found"
            and isinstance(change.before, list)
            and isinstance(change.after, list)
        ):
            revealed_clue_ids.extend(
                clue_id
                for clue_id in change.after
                if isinstance(clue_id, str) and clue_id not in change.before
            )
        if change.path == "current_scene" and isinstance(change.after, str):
            entered_scene_id = change.after

    clue_definitions = module.get("clue_definitions", {})
    revealed_clues = [
        {
            "clue_id": clue_id,
            **copy.deepcopy(clue_definitions[clue_id]),
        }
        for clue_id in revealed_clue_ids
    ]
    entered_scene = None
    if entered_scene_id is not None:
        raw_scenes = module.get("scenes", [])
        scene = next(
            item
            for item in raw_scenes
            if item.get("scene_id") == entered_scene_id
        )
        entered_scene = {
            "scene_id": entered_scene_id,
            "public_facts": copy.deepcopy(scene["public_facts"]),
            "interactions": copy.deepcopy(scene["interactions"]),
            "boundaries": copy.deepcopy(scene["boundaries"]),
            "discovery_opportunities": copy.deepcopy(
                scene["discovery_opportunities"]
            ),
        }

    if not revealed_clues and entered_scene is None:
        return None
    return {
        "revealed_clues": revealed_clues,
        "entered_scene": entered_scene,
    }


def _serialize_commit_result(
    result: CommitResult,
    *,
    context_delta: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """把状态提交裁定转换为可传给 GM 的 JSON 对象。"""

    return {
        "kind": "commit_result",
        "status": result.status,
        "effect_id": result.effect_id,
        "commit_id": result.commit_id,
        "state_version": result.state_version,
        "changes": [
            {
                "path": change.path,
                "before": change.before,
                "after": change.after,
            }
            for change in result.changes
        ],
        "error_code": result.error_code,
        "message": result.message,
        "context_delta": copy.deepcopy(context_delta),
    }
