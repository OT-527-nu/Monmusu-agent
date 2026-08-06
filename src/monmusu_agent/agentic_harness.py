"""驱动 Agentic MVP 的单 GM 回合并提交自然语言正典。"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping, cast
from uuid import uuid4

from monmusu_agent.agentic_coc import (
    MAKE_CHECK_TOOL,
    MakeCheckError,
    RandomSource,
    normalize_make_check_arguments,
    prepare_make_check,
    resolve_prepared_check,
)
from monmusu_agent.agentic_model import (
    GameMasterModel,
    ModelCallError,
    ModelProfileValidationError,
    ModelRequest,
    ModelResponse,
    deepseek_model_profile,
    validated_model_profile,
)
from monmusu_agent.agentic_session import AgenticSessionStore, LoadedSession
from monmusu_agent.config import PROJECT_ROOT
from monmusu_agent.storage import write_json_atomic


def _load_capability_charter() -> str:
    """从权威设计文档读取唯一的运行时 System Prompt 正文。"""

    document = (
        PROJECT_ROOT / "docs" / "agentic_mvp" / "gm_prompt.md"
    ).read_text(encoding="utf-8")
    try:
        section = document.split("## 主持能力章程", 1)[1]
        code_block = section.split("```text", 1)[1].split("```", 1)[0]
    except IndexError as error:
        raise RuntimeError("权威 GM 能力章程代码块缺失") from error
    charter = code_block.strip()
    if not charter:
        raise RuntimeError("权威 GM 能力章程为空")
    return charter


_GM_CAPABILITY_CHARTER = _load_capability_charter()

_ATTEMPT_LIMITS = {
    "max_round_trips": 8,
    "request_timeout_seconds": 60,
    "attempt_timeout_seconds": 180,
    "max_structure_repairs": 1,
}
_FINAL_FIELDS = frozenset({"narration", "establish", "retire", "session_status"})
_ASSISTANT_FIELDS = frozenset(
    {"role", "content", "reasoning_content", "tool_calls"}
)
_PROVIDER_FAILURE_CODES = frozenset(
    {
        "request_timeout",
        "provider_authentication_failed",
        "provider_rate_limited",
        "provider_response_error",
        "provider_server_error",
        "provider_network_error",
        "unsupported_model_profile",
        "unsupported_streaming",
        "unsupported_thinking_mode",
    }
)
_PUBLIC_INTERRUPTION_CODES = _PROVIDER_FAILURE_CODES | frozenset(
    {
        "provider_error",
        "attempt_timeout",
        "step_limit_exceeded",
        "invalid_model_response",
        "invalid_final_response",
        "provider_protocol_error",
        "tool_commit_failed",
        "authority_id_error",
        "structure_repair_failed",
        "final_commit_failed",
    }
)
_TRUNCATED_FINISH_REASONS = frozenset({"length", "max_tokens", "content_filter"})
_REPAIR_PROMPT = (
    "你的上一份答复未通过本地最终答复结构校验。"
    "请只返回完整、合法的 JSON Object，顶层只能包含 narration、establish、retire、session_status；"
    "不要调用工具，不要加入 fact_id、mechanic_id、诊断或其他字段。"
)
_REPAIR_PROMPT_PREFIX = f"{_REPAIR_PROMPT}\n本地校验提示："


class AgenticTurnError(RuntimeError):
    """表示回合无法在可信生命周期内开始或完成。"""


class AgenticTurnInputError(AgenticTurnError):
    """表示玩家输入不符合公开生命周期契约。"""


class AgenticTurnBlockedError(AgenticTurnError):
    """表示已有未完成回合阻塞了新的虚构行动。"""


class AgenticSessionCompleteError(AgenticTurnError):
    """表示已收束会话拒绝新玩家输入。"""


class AgenticTurnPersistenceError(AgenticTurnError):
    """表示回合状态无法可靠写入本地聚合。"""


@dataclass(frozen=True)
class PublicFactChange:
    """只携带允许 CLI 展示的公开事实变化。"""

    kind: Literal["established", "retired"]
    fact_id: str
    text: str


@dataclass(frozen=True)
class PublicMechanic:
    """只携带允许 CLI 展示的已提交公开检定。"""

    mechanic_id: str
    actor_id: str
    ability: str
    ability_value: int
    difficulty: str
    target: int
    dice_adjustment: Mapping[str, Any]
    roll: int
    success_level: str
    action: str
    stakes: str


@dataclass(frozen=True)
class TurnResult:
    """公开生命周期只返回已提交投影或技术中断。"""

    status: Literal["committed", "interrupted"]
    turn_id: str
    narration: str | None
    public_mechanics: tuple[PublicMechanic, ...]
    public_fact_changes: tuple[PublicFactChange, ...]
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True)
class SessionLifecycleView:
    """向玩家调用层公开会话状态，不泄露受限恢复材料。"""

    session_status: Literal["ongoing", "complete"]
    technical_status: Literal["ready", "interrupted", "complete"]
    has_incomplete_turn: bool
    turn_id: str | None
    public_mechanics: tuple[PublicMechanic, ...]
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True)
class _ValidatedFinal:
    narration: str
    establish: tuple[Mapping[str, str], ...]
    retire: tuple[Mapping[str, str], ...]
    session_status: Literal["ongoing", "complete"]


class _FinalValidationError(ValueError):
    pass


class AgenticHarness:
    """隐藏上下文组装、模型分类和最终原子提交。"""

    def __init__(
        self,
        store: AgenticSessionStore,
        model: GameMasterModel,
        *,
        turn_id_factory: Callable[[], str] | None = None,
        fact_id_factory: Callable[[], str] | None = None,
        mechanic_id_factory: Callable[[], str] | None = None,
        random_source: RandomSource | None = None,
        clock: Callable[[], datetime] | None = None,
        session_writer: Callable[[Path, Any], None] = write_json_atomic,
        model_profile: Mapping[str, Any] | None = None,
        attempt_limits: Mapping[str, int] | None = None,
    ) -> None:
        self.store = store
        self.model = model
        self.turn_id_factory = turn_id_factory or (
            lambda: f"turn_{uuid4().hex}"
        )
        self.fact_id_factory = fact_id_factory or (
            lambda: f"fact_{uuid4().hex}"
        )
        self.mechanic_id_factory = mechanic_id_factory or (
            lambda: f"mechanic_{uuid4().hex}"
        )
        self.random_source = random_source or Random()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.session_writer = session_writer
        self.attempt_limits = self._validated_attempt_limits(
            _ATTEMPT_LIMITS if attempt_limits is None else attempt_limits
        )
        try:
            self.model_profile = validated_model_profile(
                (
                    deepseek_model_profile()
                    if model_profile is None
                    else model_profile
                ),
                enabled_tools=("make_check",),
            )
        except ModelProfileValidationError as error:
            raise AgenticTurnInputError(str(error)) from error

    def start_turn(
        self,
        game_id: str,
        player_input: str,
        *,
        public_mechanic_sink: Callable[[PublicMechanic], None] | None = None,
    ) -> TurnResult:
        """开始新回合，并委托一个有界的单次 GM 执行尝试。"""

        action = self._required_string(player_input, "player_input")
        loaded = self.store.load_session(game_id)
        current = loaded.session
        if current["session_status"] == "complete":
            raise AgenticSessionCompleteError("本局已经结束，不能接受新的行动")
        if current["incomplete_turn"] is not None:
            raise AgenticTurnBlockedError("存在未完成回合，不能接受新的行动")

        turn_id = self._new_identifier(self.turn_id_factory(), "turn_id")
        if any(turn.get("turn_id") == turn_id for turn in current["turns"]):
            raise AgenticTurnError("turn_id 与既有回合冲突")
        attempt_started = self.clock()
        started_at = self._timestamp(attempt_started)
        messages = self._assemble_messages(loaded, action)
        working = copy.deepcopy(dict(current))
        working["incomplete_turn"] = {
            "turn_id": turn_id,
            "player_input": action,
            "started_at": started_at,
            "attempt_number": 1,
            "attempt_started_at": started_at,
            "round_trips_used": 0,
            "total_round_trips": 0,
            "structure_repairs_used": 0,
            "total_structure_repairs": 0,
            "model_profile": copy.deepcopy(self.model_profile),
            "attempt_limits": copy.deepcopy(self.attempt_limits),
            "mechanics": [],
            "tool_interactions": [],
            "deepseek_messages": copy.deepcopy(messages),
            "provider_protocol_errors": [],
            "last_failure": None,
        }
        working["updated_at"] = started_at
        self._write_initial_state(loaded, working)
        return self._execute_attempt(
            loaded,
            working,
            attempt_started,
            public_mechanic_sink=public_mechanic_sink,
        )

    def get_session_state(self, game_id: str) -> SessionLifecycleView:
        """只读返回玩家安全的会话生命周期投影。"""

        current = self.store.load_session(game_id).session
        incomplete = current["incomplete_turn"]
        if incomplete is None:
            status = cast(Literal["ongoing", "complete"], current["session_status"])
            return SessionLifecycleView(
                session_status=status,
                technical_status="complete" if status == "complete" else "ready",
                has_incomplete_turn=False,
                turn_id=None,
                public_mechanics=(),
                error_code=None,
                error_message=None,
            )

        assert isinstance(incomplete, dict)
        public_mechanics = tuple(
            self._public_mechanic(mechanic)
            for mechanic in incomplete["mechanics"]
            if mechanic["visibility"] == "public"
        )
        last_failure = incomplete["last_failure"]
        raw_code = last_failure.get("code") if isinstance(last_failure, dict) else None
        error_code = (
            raw_code
            if isinstance(raw_code, str) and raw_code in _PUBLIC_INTERRUPTION_CODES
            else "technical_interruption"
        )
        return SessionLifecycleView(
            session_status="ongoing",
            technical_status="interrupted",
            has_incomplete_turn=True,
            turn_id=cast(str, incomplete["turn_id"]),
            public_mechanics=public_mechanics,
            error_code=error_code,
            error_message="回合因技术问题中断，需要显式恢复",
        )

    def resume_turn(
        self,
        game_id: str,
        turn_id: str,
        *,
        public_mechanic_sink: Callable[[PublicMechanic], None] | None = None,
    ) -> TurnResult:
        """以明确 ID 恢复同一未完成回合，并开启新的有界尝试。"""

        recovery_turn_id = self._required_string(turn_id, "turn_id")
        loaded = self.store.load_session(game_id)
        current = loaded.session
        incomplete = current["incomplete_turn"]
        if incomplete is None or incomplete.get("turn_id") != recovery_turn_id:
            raise AgenticTurnBlockedError("指定回合当前不可恢复")
        if incomplete["model_profile"] != self.model_profile:
            raise AgenticTurnBlockedError("冻结的模型运行配置当前不可用")

        attempt_started = self.clock()
        attempt_started_at = self._timestamp(attempt_started)
        working = copy.deepcopy(dict(current))
        resumed = working["incomplete_turn"]
        assert isinstance(resumed, dict)
        resumed["attempt_number"] += 1
        resumed["attempt_started_at"] = attempt_started_at
        resumed["round_trips_used"] = 0
        resumed["structure_repairs_used"] = 0
        resumed["last_failure"] = None
        working["updated_at"] = attempt_started_at
        self._write_initial_state(loaded, working)
        return self._execute_attempt(
            loaded,
            working,
            attempt_started,
            public_mechanic_sink=public_mechanic_sink,
        )

    def _execute_attempt(
        self,
        loaded: LoadedSession,
        working: dict[str, Any],
        attempt_started: datetime,
        *,
        public_mechanic_sink: Callable[[PublicMechanic], None] | None = None,
    ) -> TurnResult:
        """执行已准备的新回合或恢复尝试。"""

        incomplete = working["incomplete_turn"]
        assert isinstance(incomplete, dict)
        turn_id = cast(str, incomplete["turn_id"])
        action = cast(str, incomplete["player_input"])
        profile = cast(dict[str, Any], incomplete["model_profile"])
        limits = cast(dict[str, int], incomplete["attempt_limits"])
        public_mechanics: list[PublicMechanic] = []
        attempt_deadline = attempt_started + timedelta(
            seconds=limits["attempt_timeout_seconds"]
        )
        repair_pending = self._repair_request_pending(
            incomplete["deepseek_messages"]
        )
        repair_pending_from_previous_attempt = repair_pending
        while True:
            incomplete = working["incomplete_turn"]
            assert isinstance(incomplete, dict)
            remaining = self._remaining_seconds(attempt_deadline)
            if remaining <= 0:
                return self._interrupt(
                    loaded,
                    working,
                    turn_id,
                    "attempt_timeout",
                    "GM 执行尝试超过时间限制",
                    public_mechanics=tuple(public_mechanics),
                )
            if incomplete["round_trips_used"] >= limits["max_round_trips"]:
                return self._interrupt(
                    loaded,
                    working,
                    turn_id,
                    "step_limit_exceeded",
                    "GM 执行尝试达到往返上限",
                    public_mechanics=tuple(public_mechanics),
                )
            request = ModelRequest(
                messages=tuple(copy.deepcopy(incomplete["deepseek_messages"])),
                tools=()
                if repair_pending
                else (copy.deepcopy(MAKE_CHECK_TOOL),),
                request_timeout_seconds=min(
                    float(limits["request_timeout_seconds"]),
                    remaining,
                ),
                model_profile=copy.deepcopy(profile),
            )
            try:
                response = self.model.complete(request)
            except ModelCallError as error:
                code = self._safe_provider_failure_code(error.code)
                if self._remaining_seconds(attempt_deadline) <= 0:
                    code = "attempt_timeout"
                return self._interrupt(
                    loaded,
                    working,
                    turn_id,
                    code,
                    "GM 服务调用中断",
                    public_mechanics=tuple(public_mechanics),
                )
            except Exception:
                code = (
                    "attempt_timeout"
                    if self._remaining_seconds(attempt_deadline) <= 0
                    else "provider_error"
                )
                return self._interrupt(
                    loaded,
                    working,
                    turn_id,
                    code,
                    "GM 服务调用中断",
                    public_mechanics=tuple(public_mechanics),
                )

            incomplete["round_trips_used"] += 1
            incomplete["total_round_trips"] += 1
            if (
                isinstance(response, ModelResponse)
                and response.finish_reason in _TRUNCATED_FINISH_REASONS
            ):
                self._record_protocol_error(
                    incomplete,
                    response,
                    code="provider_response_error",
                    message="provider response was truncated",
                )
                return self._interrupt(
                    loaded,
                    working,
                    turn_id,
                    "provider_response_error",
                    "GM 响应不完整",
                    public_mechanics=tuple(public_mechanics),
                )
            try:
                assistant_message = self._validated_assistant_message(response)
            except _FinalValidationError:
                self._record_protocol_error(incomplete, response)
                return self._interrupt(
                    loaded,
                    working,
                    turn_id,
                    "invalid_model_response",
                    "GM 响应协议无效",
                    public_mechanics=tuple(public_mechanics),
                )

            tool_calls = assistant_message["tool_calls"]
            if tool_calls:
                if repair_pending:
                    self._record_protocol_error(
                        incomplete,
                        response,
                        message="structure repair response cannot call tools",
                    )
                    return self._interrupt(
                        loaded,
                        working,
                        turn_id,
                        "invalid_final_response",
                        "GM 结构修正仍然无效",
                        public_mechanics=tuple(public_mechanics),
                    )
                if len(tool_calls) == 1:
                    try:
                        working, public_mechanic = self._commit_tool_response(
                            loaded,
                            working,
                            assistant_message,
                        )
                    except _FinalValidationError:
                        self._record_protocol_error(incomplete, response)
                        return self._interrupt(
                            loaded,
                            working,
                            turn_id,
                            "provider_protocol_error",
                            "GM 工具调用协议无效",
                            public_mechanics=tuple(public_mechanics),
                        )
                    except AgenticTurnPersistenceError:
                        self._record_protocol_error(
                            incomplete,
                            response,
                            message="tool interaction commit failed",
                        )
                        return self._interrupt(
                            loaded,
                            working,
                            turn_id,
                            "tool_commit_failed",
                            "工具交互提交失败",
                            public_mechanics=tuple(public_mechanics),
                        )
                    except AgenticTurnError:
                        self._record_protocol_error(
                            incomplete,
                            response,
                            message="mechanic authority id allocation failed",
                        )
                        return self._interrupt(
                            loaded,
                            working,
                            turn_id,
                            "authority_id_error",
                            "Harness 无法分配稳定标识符",
                            public_mechanics=tuple(public_mechanics),
                        )
                    if public_mechanic is not None:
                        public_mechanics.append(public_mechanic)
                        if public_mechanic_sink is not None:
                            public_mechanic_sink(public_mechanic)
                elif self._tool_calls_are_pairable(tool_calls) and self._tool_calls_have_new_ids(
                    tool_calls,
                    incomplete["tool_interactions"],
                ):
                    try:
                        working = self._commit_multiple_tool_errors(
                            loaded,
                            working,
                            assistant_message,
                        )
                    except AgenticTurnPersistenceError:
                        self._record_protocol_error(
                            incomplete,
                            response,
                            message="multiple tool error commit failed",
                        )
                        return self._interrupt(
                            loaded,
                            working,
                            turn_id,
                            "tool_commit_failed",
                            "工具交互提交失败",
                            public_mechanics=tuple(public_mechanics),
                        )
                else:
                    self._record_protocol_error(incomplete, response)
                    return self._interrupt(
                        loaded,
                        working,
                        turn_id,
                        "provider_protocol_error",
                        "GM 工具调用协议无效",
                        public_mechanics=tuple(public_mechanics),
                    )
                if incomplete["round_trips_used"] >= limits["max_round_trips"]:
                    return self._interrupt(
                        loaded,
                        working,
                        turn_id,
                        "step_limit_exceeded",
                        "GM 执行尝试达到往返上限",
                        public_mechanics=tuple(public_mechanics),
                    )
                continue

            try:
                direct_message = self._classify_direct_final(response)
            except _FinalValidationError:
                self._record_protocol_error(incomplete, response)
                return self._interrupt(
                    loaded,
                    working,
                    turn_id,
                    "invalid_final_response" if repair_pending else "invalid_model_response",
                    "GM 结构修正仍然无效" if repair_pending else "GM 响应协议无效",
                    public_mechanics=tuple(public_mechanics),
                )

            incomplete["deepseek_messages"].append(copy.deepcopy(direct_message))
            try:
                final = self._validate_final(direct_message["content"], working)
            except _FinalValidationError:
                if repair_pending and not repair_pending_from_previous_attempt:
                    return self._interrupt(
                        loaded,
                        working,
                        turn_id,
                        "invalid_final_response",
                        "GM 结构修正仍然无效",
                        public_mechanics=tuple(public_mechanics),
                    )
                if (
                    incomplete["structure_repairs_used"] < limits["max_structure_repairs"]
                    and incomplete["round_trips_used"] < limits["max_round_trips"]
                    and self._remaining_seconds(attempt_deadline) > 0
                ):
                    incomplete["structure_repairs_used"] += 1
                    incomplete["total_structure_repairs"] += 1
                    incomplete["deepseek_messages"].append(
                        {
                            "role": "user",
                            "content": (
                                f"{_REPAIR_PROMPT_PREFIX}"
                                f"{self._final_validation_summary(direct_message['content'], working)}"
                            ),
                        }
                    )
                    try:
                        self.session_writer(
                            loaded.session_directory / "session.json",
                            working,
                        )
                    except Exception:
                        return self._interrupt(
                            loaded,
                            working,
                            turn_id,
                            "structure_repair_failed",
                            "GM 结构修正状态无法保存",
                            public_mechanics=tuple(public_mechanics),
                        )
                    repair_pending = True
                    repair_pending_from_previous_attempt = False
                    continue
                code = (
                    "step_limit_exceeded"
                    if incomplete["round_trips_used"] >= limits["max_round_trips"]
                    else "invalid_final_response"
                )
                return self._interrupt(
                    loaded,
                    working,
                    turn_id,
                    code,
                    (
                        "GM 执行尝试达到往返上限"
                        if code == "step_limit_exceeded"
                        else (
                            "GM 结构修正仍然无效"
                            if repair_pending
                            else "GM 最终答复结构或事实引用无效"
                        )
                    ),
                    public_mechanics=tuple(public_mechanics),
                )

            return self._commit_final_or_interrupt(
                loaded,
                working,
                turn_id,
                action,
                final,
                attempt_deadline,
                tuple(public_mechanics),
            )

    def _write_initial_state(
        self,
        loaded: LoadedSession,
        session: Mapping[str, Any],
    ) -> None:
        try:
            self.session_writer(loaded.session_directory / "session.json", session)
        except Exception as error:
            raise AgenticTurnPersistenceError(
                "未完成回合无法持久化，模型未被调用"
            ) from error

    @staticmethod
    def _repair_request_pending(messages: object) -> bool:
        """从 Harness 自己持久化的末条消息恢复无工具修正相位。"""

        if not isinstance(messages, list) or not messages:
            return False
        last_message = messages[-1]
        return (
            isinstance(last_message, dict)
            and last_message.get("role") == "user"
            and isinstance(last_message.get("content"), str)
            and last_message["content"].startswith(_REPAIR_PROMPT_PREFIX)
        )

    @staticmethod
    def _validated_attempt_limits(
        limits: Mapping[str, int],
    ) -> dict[str, int]:
        if not isinstance(limits, Mapping) or set(limits) != set(_ATTEMPT_LIMITS):
            raise AgenticTurnInputError("attempt_limits 必须只包含已知运行限制")
        rebuilt: dict[str, int] = {}
        for field in _ATTEMPT_LIMITS:
            value = limits[field]
            minimum = 0 if field == "max_structure_repairs" else 1
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < minimum
                or value > 1_000_000
            ):
                raise AgenticTurnInputError("attempt_limits 数值无效")
            rebuilt[field] = value
        return rebuilt

    def _remaining_seconds(self, deadline: datetime) -> float:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise AgenticTurnError("clock 必须返回带时区的时间")
        return (deadline - now.astimezone(timezone.utc)).total_seconds()

    @staticmethod
    def _tool_call_parts(call: object) -> tuple[str, str, str] | None:
        if not isinstance(call, dict) or set(call) != {"id", "type", "function"}:
            return None
        tool_call_id = call.get("id")
        function = call.get("function")
        if (
            not isinstance(tool_call_id, str)
            or not tool_call_id
            or tool_call_id != tool_call_id.strip()
            or call.get("type") != "function"
            or not isinstance(function, dict)
            or set(function) != {"name", "arguments"}
            or not isinstance(function.get("name"), str)
            or not function["name"]
            or function["name"] != function["name"].strip()
            or not isinstance(function.get("arguments"), str)
        ):
            return None
        return tool_call_id, function["name"], function["arguments"]

    @classmethod
    def _tool_calls_are_pairable(cls, tool_calls: object) -> bool:
        if not isinstance(tool_calls, list) or len(tool_calls) < 2:
            return False
        parts = [cls._tool_call_parts(call) for call in tool_calls]
        return all(part is not None for part in parts) and len(
            {part[0] for part in parts if part is not None}
        ) == len(parts)

    @classmethod
    def _tool_calls_have_new_ids(
        cls,
        tool_calls: object,
        interactions: object,
    ) -> bool:
        if not isinstance(tool_calls, list) or not isinstance(interactions, list):
            return False
        existing_ids = {
            interaction.get("tool_call_id")
            for interaction in interactions
            if isinstance(interaction, dict)
        }
        return all(
            parts is not None and parts[0] not in existing_ids
            for parts in (cls._tool_call_parts(call) for call in tool_calls)
        )

    def _commit_multiple_tool_errors(
        self,
        loaded: LoadedSession,
        working: dict[str, Any],
        assistant_message: Mapping[str, Any],
    ) -> dict[str, Any]:
        tool_calls = assistant_message["tool_calls"]
        assert isinstance(tool_calls, list)
        candidate = copy.deepcopy(working)
        incomplete = candidate["incomplete_turn"]
        assert isinstance(incomplete, dict)
        incomplete["deepseek_messages"].append(copy.deepcopy(dict(assistant_message)))
        error = {
            "code": "multiple_tool_calls_not_allowed",
            "message": "每次 GM 响应只能调用一个工具",
        }
        for call in tool_calls:
            parts = self._tool_call_parts(call)
            assert parts is not None
            tool_call_id, tool_name, arguments_raw = parts
            interaction = {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments_raw": arguments_raw,
                "arguments": None,
                "ok": False,
                "result": None,
                "error": copy.deepcopy(error),
            }
            incomplete["tool_interactions"].append(interaction)
            envelope = {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "ok": False,
                "result": None,
                "error": copy.deepcopy(error),
            }
            incomplete["deepseek_messages"].append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                    "content": json.dumps(
                        envelope,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                }
            )
        candidate["updated_at"] = self._timestamp(self.clock())
        try:
            self.session_writer(
                loaded.session_directory / "session.json",
                candidate,
            )
        except Exception as error:
            raise AgenticTurnPersistenceError(
                "多个工具错误无法原子提交"
            ) from error
        return candidate

    def _commit_final_or_interrupt(
        self,
        loaded: LoadedSession,
        working: dict[str, Any],
        turn_id: str,
        player_input: str,
        final: _ValidatedFinal,
        attempt_deadline: datetime,
        public_mechanics: tuple[PublicMechanic, ...],
    ) -> TurnResult:
        if self._remaining_seconds(attempt_deadline) <= 0:
            return self._interrupt(
                loaded,
                working,
                turn_id,
                "attempt_timeout",
                "GM 执行尝试超过时间限制",
                public_mechanics=public_mechanics,
            )
        try:
            committed, public_changes = self._build_final_commit(
                working,
                turn_id,
                player_input,
                final,
            )
        except AgenticTurnError:
            return self._interrupt(
                loaded,
                working,
                turn_id,
                "authority_id_error",
                "Harness 无法分配稳定标识符",
                public_mechanics=public_mechanics,
            )

        if self._remaining_seconds(attempt_deadline) <= 0:
            return self._interrupt(
                loaded,
                working,
                turn_id,
                "attempt_timeout",
                "GM 执行尝试超过时间限制",
                public_mechanics=public_mechanics,
            )

        try:
            self.session_writer(
                loaded.session_directory / "session.json",
                committed,
            )
        except Exception:
            return self._interrupt(
                loaded,
                working,
                turn_id,
                "final_commit_failed",
                "GM 最终答复提交失败",
                public_mechanics=public_mechanics,
            )
        return TurnResult(
            status="committed",
            turn_id=turn_id,
            narration=final.narration,
            public_mechanics=public_mechanics,
            public_fact_changes=public_changes,
            error_code=None,
            error_message=None,
        )

    def _interrupt(
        self,
        loaded: LoadedSession,
        working: dict[str, Any],
        turn_id: str,
        code: str,
        message: str,
        *,
        public_mechanics: tuple[PublicMechanic, ...] = (),
    ) -> TurnResult:
        incomplete = working["incomplete_turn"]
        assert isinstance(incomplete, dict)
        incomplete["last_failure"] = {"code": code, "message": message}
        working["updated_at"] = self._timestamp(self.clock())
        try:
            self.session_writer(loaded.session_directory / "session.json", working)
        except Exception:
            # 最终提交失败后，未完成回合仍须保存稳定的中断状态。
            try:
                write_json_atomic(loaded.session_directory / "session.json", working)
            except Exception:
                return TurnResult(
                    status="interrupted",
                    turn_id=turn_id,
                    narration=None,
                    public_mechanics=public_mechanics,
                    public_fact_changes=(),
                    error_code="interruption_persistence_failed",
                    error_message="技术中断状态无法持久化",
                )
        return TurnResult(
            status="interrupted",
            turn_id=turn_id,
            narration=None,
            public_mechanics=public_mechanics,
            public_fact_changes=(),
            error_code=code,
            error_message=message,
        )

    def _commit_tool_response(
        self,
        loaded: LoadedSession,
        working: dict[str, Any],
        assistant_message: Mapping[str, Any],
    ) -> tuple[dict[str, Any], PublicMechanic | None]:
        tool_calls = assistant_message["tool_calls"]
        if (
            assistant_message.get("content") is not None
            or not isinstance(tool_calls, list)
            or len(tool_calls) != 1
        ):
            raise _FinalValidationError
        call = tool_calls[0]
        if not isinstance(call, dict) or set(call) != {"id", "type", "function"}:
            raise _FinalValidationError
        tool_call_id = call.get("id")
        function = call.get("function")
        if (
            not isinstance(tool_call_id, str)
            or not tool_call_id
            or tool_call_id != tool_call_id.strip()
            or call.get("type") != "function"
            or not isinstance(function, dict)
            or set(function) != {"name", "arguments"}
        ):
            raise _FinalValidationError

        incomplete = working["incomplete_turn"]
        assert isinstance(incomplete, dict)
        if any(
            interaction.get("tool_call_id") == tool_call_id
            for interaction in incomplete["tool_interactions"]
            if isinstance(interaction, dict)
        ):
            # Increment 2 才恢复同 ID 重放；当前切片先安全停止，绝不重复结算。
            raise _FinalValidationError
        tool_name = function.get("name")
        arguments_raw = function.get("arguments")
        if (
            not isinstance(tool_name, str)
            or not tool_name
            or tool_name != tool_name.strip()
            or not isinstance(arguments_raw, str)
        ):
            raise _FinalValidationError

        arguments: dict[str, Any] | None = None
        mechanic: dict[str, Any] | None = None
        error: dict[str, str] | None = None
        if tool_name != "make_check":
            error = {
                "code": "unknown_tool",
                "message": f"工具 {tool_name} 当前不可用",
            }
        else:
            try:
                arguments = normalize_make_check_arguments(arguments_raw)
                prepared = prepare_make_check(
                    arguments,
                    loaded.session["actors"],
                )
                mechanic_id = self._new_identifier(
                    self.mechanic_id_factory(),
                    "mechanic_id",
                )
                existing_ids = {
                    item["mechanic_id"]
                    for turn in loaded.session["turns"]
                    for item in turn["mechanics"]
                }
                existing_ids.update(
                    item["mechanic_id"] for item in incomplete["mechanics"]
                )
                if mechanic_id in existing_ids:
                    raise AgenticTurnError("mechanic_id 与既有机械冲突")
                mechanic = resolve_prepared_check(
                    prepared,
                    mechanic_id=mechanic_id,
                    random_source=self.random_source,
                    committed_at=self._timestamp(self.clock()),
                )
            except MakeCheckError as tool_error:
                error = {"code": tool_error.code, "message": tool_error.message}

        envelope = {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "ok": mechanic is not None,
            "result": copy.deepcopy(mechanic),
            "error": copy.deepcopy(error),
        }
        tool_message = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": json.dumps(envelope, ensure_ascii=False, sort_keys=True),
        }
        interaction = {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "arguments_raw": arguments_raw,
            "arguments": copy.deepcopy(arguments),
            "ok": mechanic is not None,
            "result": copy.deepcopy(mechanic),
            "error": copy.deepcopy(error),
        }
        candidate = copy.deepcopy(working)
        incomplete_candidate = candidate["incomplete_turn"]
        assert isinstance(incomplete_candidate, dict)
        incomplete_candidate["deepseek_messages"].extend(
            [copy.deepcopy(dict(assistant_message)), tool_message]
        )
        incomplete_candidate["tool_interactions"].append(interaction)
        if mechanic is not None:
            incomplete_candidate["mechanics"].append(copy.deepcopy(mechanic))
        candidate["updated_at"] = self._timestamp(self.clock())
        try:
            self.session_writer(
                loaded.session_directory / "session.json",
                candidate,
            )
        except Exception as commit_error:
            raise AgenticTurnPersistenceError(
                "工具交互无法原子提交"
            ) from commit_error

        public = None
        if mechanic is not None and mechanic["visibility"] == "public":
            public = self._public_mechanic(mechanic)
        return candidate, public

    @staticmethod
    def _public_mechanic(mechanic: Mapping[str, Any]) -> PublicMechanic:
        return PublicMechanic(
            mechanic_id=mechanic["mechanic_id"],
            actor_id=mechanic["actor_id"],
            ability=mechanic["ability"],
            ability_value=mechanic["ability_value"],
            difficulty=mechanic["difficulty"],
            target=mechanic["target"],
            dice_adjustment=MappingProxyType(
                copy.deepcopy(mechanic["dice_adjustment"])
            ),
            roll=mechanic["roll"],
            success_level=mechanic["success_level"],
            action=mechanic["action"],
            stakes=mechanic["stakes"],
        )

    def _assemble_messages(
        self,
        loaded: LoadedSession,
        player_input: str,
    ) -> list[dict[str, str]]:
        session = loaded.session
        active_facts = [
            copy.deepcopy(fact)
            for fact in session["facts"]
            if fact["status"] == "active"
        ]
        facts_by_id = {fact["fact_id"]: fact for fact in session["facts"]}
        committed_record = []
        for turn in session["turns"]:
            record = copy.deepcopy(turn)
            record["established_facts"] = [
                copy.deepcopy(facts_by_id[fact_id])
                for fact_id in turn["established_fact_ids"]
            ]
            committed_record.append(record)
        package = {
            "SESSION_SETUP": copy.deepcopy(session["setup"]),
            "OPENING_FACT_HISTORY": [
                copy.deepcopy(facts_by_id[fact_id])
                for fact_id in session["setup"]["opening_fact_ids"]
            ],
            "INVESTIGATOR_PROFILE": copy.deepcopy(session["investigator_profile"]),
            "ACTOR_DISPLAY_NAMES": copy.deepcopy(session["actor_display_names"]),
            "ACTOR_SHEETS": copy.deepcopy(session["actors"]),
            "ACTIVE_FACTS": active_facts,
            "COMMITTED_TURNS": committed_record,
            "MODULE_REFERENCE": loaded.module_reference,
            "CHARACTER_REFERENCE": loaded.character_reference,
            "AVAILABLE_TOOLS": [copy.deepcopy(MAKE_CHECK_TOOL)],
        }
        return [
            {"role": "system", "content": _GM_CAPABILITY_CHARTER},
            {
                "role": "user",
                "content": json.dumps(package, ensure_ascii=False, sort_keys=True),
            },
            {
                "role": "user",
                "content": (
                    "<PLAYER_INPUT>\n"
                    f"{player_input}\n"
                    "</PLAYER_INPUT>"
                ),
            },
        ]

    @classmethod
    def _validated_assistant_message(
        cls,
        response: object,
    ) -> dict[str, Any]:
        if not isinstance(response, ModelResponse):
            raise _FinalValidationError
        message = response.assistant_message
        if (
            not isinstance(message, Mapping)
            or set(message) != _ASSISTANT_FIELDS
            or response.finish_reason is not None
            and not isinstance(response.finish_reason, str)
            or response.usage is not None
            and not isinstance(response.usage, Mapping)
            or response.latency_ms is not None
            and (
                not isinstance(response.latency_ms, int)
                or isinstance(response.latency_ms, bool)
                or response.latency_ms < 0
            )
        ):
            raise _FinalValidationError
        content = message.get("content")
        reasoning = message.get("reasoning_content")
        if (
            message.get("role") != "assistant"
            or not isinstance(message.get("tool_calls"), list)
            or content is not None
            and not isinstance(content, str)
            or reasoning is not None
            and not isinstance(reasoning, str)
        ):
            raise _FinalValidationError
        finish_reason = response.finish_reason
        if finish_reason not in {None, "stop", "tool_calls"}:
            raise _FinalValidationError
        tool_calls = message["tool_calls"]
        if (
            tool_calls
            and finish_reason not in {None, "tool_calls"}
            or not tool_calls
            and finish_reason == "tool_calls"
        ):
            raise _FinalValidationError
        return dict(message)

    @classmethod
    def _classify_direct_final(
        cls,
        response: object,
    ) -> dict[str, Any]:
        message = cls._validated_assistant_message(response)
        content = message["content"]
        if (
            message["tool_calls"] != []
            or not isinstance(content, str)
            or not content.strip()
        ):
            raise _FinalValidationError
        return message

    @classmethod
    def _validate_final(
        cls,
        content: object,
        session: Mapping[str, Any],
    ) -> _ValidatedFinal:
        if not isinstance(content, str):
            raise _FinalValidationError
        try:
            candidate = json.loads(content)
        except (TypeError, json.JSONDecodeError) as error:
            raise _FinalValidationError from error
        if not isinstance(candidate, dict) or set(candidate) != _FINAL_FIELDS:
            raise _FinalValidationError
        narration = cls._final_string(candidate.get("narration"))
        status = candidate.get("session_status")
        if not isinstance(status, str) or status not in {"ongoing", "complete"}:
            raise _FinalValidationError

        establish_raw = candidate.get("establish")
        if not isinstance(establish_raw, list):
            raise _FinalValidationError
        establish: list[Mapping[str, str]] = []
        for item in establish_raw:
            if (
                not isinstance(item, dict)
                or set(item) != {"visibility", "text"}
                or not isinstance(item.get("visibility"), str)
                or item.get("visibility") not in {"public", "hidden"}
            ):
                raise _FinalValidationError
            establish.append(
                {
                    "visibility": item["visibility"],
                    "text": cls._final_string(item.get("text")),
                }
            )

        retire_raw = candidate.get("retire")
        if not isinstance(retire_raw, list):
            raise _FinalValidationError
        active_ids = {
            fact["fact_id"]
            for fact in session["facts"]
            if fact["status"] == "active"
        }
        seen_retirements: set[str] = set()
        retire: list[Mapping[str, str]] = []
        for item in retire_raw:
            if not isinstance(item, dict) or set(item) != {"fact_id", "reason"}:
                raise _FinalValidationError
            fact_id = cls._final_string(item.get("fact_id"))
            reason = cls._final_string(item.get("reason"))
            if fact_id not in active_ids or fact_id in seen_retirements:
                raise _FinalValidationError
            seen_retirements.add(fact_id)
            retire.append({"fact_id": fact_id, "reason": reason})
        return _ValidatedFinal(
            narration=narration,
            establish=tuple(establish),
            retire=tuple(retire),
            session_status=cast(Literal["ongoing", "complete"], status),
        )

    @classmethod
    def _final_validation_summary(
        cls,
        content: object,
        session: Mapping[str, Any],
    ) -> str:
        """生成不含 provider 细节的短校验提示，供同一 GM 修正。"""

        if not isinstance(content, str):
            return "content 必须是 JSON 文本"
        try:
            candidate = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            return "content 必须是合法 JSON Object"
        if not isinstance(candidate, dict):
            return "顶层必须是 JSON Object"
        expected = _FINAL_FIELDS
        unknown = sorted(set(candidate) - expected)
        missing = sorted(expected - set(candidate))
        if unknown:
            return f"不能包含未知字段：{', '.join(unknown)}"
        if missing:
            return f"缺少字段：{', '.join(missing)}"
        status = candidate.get("session_status")
        if not isinstance(status, str) or status not in {"ongoing", "complete"}:
            return "session_status 必须是 ongoing 或 complete"
        if not isinstance(candidate.get("narration"), str):
            return "narration 必须是非空字符串"
        retire = candidate.get("retire")
        if isinstance(retire, list):
            active_ids = {
                fact["fact_id"]
                for fact in session["facts"]
                if fact["status"] == "active"
            }
            for item in retire:
                if (
                    isinstance(item, dict)
                    and (
                        not isinstance(item.get("fact_id"), str)
                        or item["fact_id"] not in active_ids
                    )
                ):
                    return "retire.fact_id 必须引用当前有效事实"
        return "establish、retire 的元素必须符合最终答复契约"

    def _build_final_commit(
        self,
        working: Mapping[str, Any],
        turn_id: str,
        player_input: str,
        final: _ValidatedFinal,
    ) -> tuple[dict[str, Any], tuple[PublicFactChange, ...]]:
        committed = copy.deepcopy(dict(working))
        facts = committed["facts"]
        existing_fact_ids = {fact["fact_id"] for fact in facts}
        established_ids: list[str] = []
        public_changes: list[PublicFactChange] = []
        for proposal in final.establish:
            fact_id = self._new_identifier(self.fact_id_factory(), "fact_id")
            if fact_id in existing_fact_ids:
                raise AgenticTurnError("fact_id 与既有事实冲突")
            existing_fact_ids.add(fact_id)
            established_ids.append(fact_id)
            facts.append(
                {
                    "fact_id": fact_id,
                    "text": proposal["text"],
                    "visibility": proposal["visibility"],
                    "status": "active",
                    "established_turn_id": turn_id,
                    "origin": {"kind": "gm_turn", "source_ref": None},
                    "retired_turn_id": None,
                    "retire_reason": None,
                }
            )
            if proposal["visibility"] == "public":
                public_changes.append(
                    PublicFactChange(
                        kind="established",
                        fact_id=fact_id,
                        text=proposal["text"],
                    )
                )

        facts_by_id = {fact["fact_id"]: fact for fact in facts}
        retirements = [dict(retirement) for retirement in final.retire]
        for retirement in retirements:
            fact = facts_by_id[retirement["fact_id"]]
            fact["status"] = "retired"
            fact["retired_turn_id"] = turn_id
            fact["retire_reason"] = retirement["reason"]
            if fact["visibility"] == "public":
                public_changes.append(
                    PublicFactChange(
                        kind="retired",
                        fact_id=fact["fact_id"],
                        text=retirement["reason"],
                    )
                )

        committed_at = self._timestamp(self.clock())
        incomplete = committed["incomplete_turn"]
        assert isinstance(incomplete, dict)
        committed["turns"].append(
            {
                "turn_id": turn_id,
                "player_input": player_input,
                "mechanics": copy.deepcopy(incomplete["mechanics"]),
                "narration": final.narration,
                "established_fact_ids": established_ids,
                "retirements": retirements,
                "session_status": final.session_status,
                "committed_at": committed_at,
            }
        )
        committed["session_status"] = final.session_status
        committed["incomplete_turn"] = None
        committed["updated_at"] = committed_at
        return committed, tuple(public_changes)

    def _record_protocol_error(
        self,
        incomplete: dict[str, Any],
        response: object,
        *,
        code: str = "provider_protocol_error",
        message: str = "response envelope cannot form a valid model step",
    ) -> None:
        if isinstance(response, ModelResponse):
            envelope: object = {
                "assistant_message": response.assistant_message,
                "finish_reason": response.finish_reason,
                "usage": response.usage,
                "latency_ms": response.latency_ms,
            }
        else:
            envelope = {"unrecognized_response_type": type(response).__name__}
        try:
            serialized = json.dumps(envelope, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            serialized = json.dumps(
                {"unserializable_response_type": type(response).__name__},
                ensure_ascii=False,
                sort_keys=True,
            )
        incomplete["provider_protocol_errors"].append(
            {
                "code": code,
                "message": message,
                "model_response_json": serialized,
                "recorded_at": self._timestamp(self.clock()),
            }
        )

    @staticmethod
    def _required_string(value: object, label: str) -> str:
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise AgenticTurnInputError(f"{label} 必须是去除首尾空白的非空字符串")
        return value

    @staticmethod
    def _final_string(value: object) -> str:
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise _FinalValidationError
        return value

    @staticmethod
    def _new_identifier(value: object, label: str) -> str:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None
        ):
            raise AgenticTurnError(f"{label} 格式无效")
        return value

    @staticmethod
    def _safe_provider_failure_code(code: object) -> str:
        if isinstance(code, str) and code in _PROVIDER_FAILURE_CODES:
            return code
        return "provider_error"

    @staticmethod
    def _timestamp(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            raise AgenticTurnError("clock 必须返回带时区的时间")
        utc_value = value.astimezone(timezone.utc)
        return utc_value.isoformat(timespec="seconds").replace("+00:00", "Z")
