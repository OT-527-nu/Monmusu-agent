"""显式运行 DeepSeek 协议契约并生成脱敏评估记录。"""

from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping
from uuid import uuid4

from dotenv import load_dotenv

from monmusu_agent.agentic_harness import AgenticHarness, TurnResult
from monmusu_agent.agentic_model import (
    DeepSeekGameMasterModel,
    GameMasterModel,
    ModelCallError,
    ModelRequest,
    ModelResponse,
    deepseek_model_profile,
)
from monmusu_agent.agentic_session import (
    AgenticSessionStore,
    CreatedSession,
    NewSessionRequest,
)
from monmusu_agent.config import PROJECT_ROOT


@dataclass(frozen=True)
class ContractRunResult:
    """区分未启用、协议通过与协议失败，不把 skip 记作通过。"""

    status: Literal["skipped", "passed", "failed"]
    reason: str
    records: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class _ContractScenario:
    version: str
    player_input: str
    expected_path: Literal["direct_final", "tool_then_final"]


@dataclass(frozen=True)
class _RecoveryContractScenario:
    version: str
    player_input: str
    thinking: bool


_SCENARIOS = (
    _ContractScenario(
        version="ticket-04-direct-final-v1",
        player_input="我拿起眼前无人看守、伸手就能够到的铜钥匙。",
        expected_path="direct_final",
    ),
    _ContractScenario(
        version="ticket-04-tool-then-final-v2",
        player_input=(
            "我用肩膀猛撞锈蚀的牢门，想把锁扣撞断；失败会发出巨响，"
            "引来正在远去的船工。请用一次公开力量检定结算这项有真实"
            "不确定性的行动。"
        ),
        expected_path="tool_then_final",
    ),
)

_RECOVERY_SCENARIOS = (
    _RecoveryContractScenario(
        version="ticket-11-non-thinking-recovery-v1",
        player_input=(
            "我用肩膀猛撞锈蚀的牢门，想把锁扣撞断；失败会发出巨响，"
            "引来正在远去的船工。请先用一次公开力量检定结算这项有真实"
            "不确定性的行动，再根据结果继续。"
        ),
        thinking=False,
    ),
    _RecoveryContractScenario(
        version="ticket-11-thinking-recovery-v1",
        player_input=(
            "我用肩膀猛撞锈蚀的牢门，想把锁扣撞断；失败会发出巨响，"
            "引来正在远去的船工。请先用一次公开力量检定结算这项有真实"
            "不确定性的行动，再根据结果继续。"
        ),
        thinking=True,
    ),
)


class _EvaluationRecordingModel:
    """只记录 Evaluation 所需的脱敏 provider 契约字段。"""

    def __init__(
        self,
        delegate: GameMasterModel,
        sdk_requests: list[Mapping[str, Any]],
        *,
        phase: str | None = None,
    ) -> None:
        self.delegate = delegate
        self.sdk_requests = sdk_requests
        self.phase = phase
        self.requests: list[dict[str, Any]] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        record: dict[str, Any] = {
            "function_tools": [],
            "response_format": None,
            "stream": None,
            "tool_result_ids": _tool_result_ids(request.messages),
            "finish_reason": None,
            "usage": None,
            "latency_ms": None,
            "local_error_category": None,
            "structure_repairs": 0,
            "tool_calls": [],
        }
        if self.phase is not None:
            record["phase"] = self.phase
            record["message_projection"] = _message_projection(request.messages)
        sdk_request_count = len(self.sdk_requests)
        try:
            response = self.delegate.complete(request)
        except ModelCallError as error:
            record.update(
                _sdk_evidence_for_call(self.sdk_requests, sdk_request_count)
            )
            record["local_error_category"] = error.code
            self.requests.append(record)
            raise
        record.update(_sdk_evidence_for_call(self.sdk_requests, sdk_request_count))
        record.update(
            {
                "finish_reason": response.finish_reason,
                "usage": copy.deepcopy(response.usage),
                "latency_ms": response.latency_ms,
                "tool_calls": _sanitized_tool_calls(
                    response.assistant_message
                ),
            }
        )
        if self.phase is not None:
            record["assistant_reasoning"] = _reasoning_projection(
                response.assistant_message
            )
        self.requests.append(record)
        return response


class _InterruptAfterToolModel:
    """让真实首个工具答复先完成，再在下一次 provider 调用前制造中断。"""

    def __init__(self, delegate: GameMasterModel) -> None:
        self.delegate = delegate
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 2:
            raise ModelCallError(
                "request_timeout",
                "contract runner interrupted after committed tool",
                retryable=True,
            )
        return self.delegate.complete(request)


def run_deepseek_contract(
    *,
    enabled: bool,
    api_key: str | None,
    session_root: Path,
    client: Any | None = None,
) -> ContractRunResult:
    """只在外部明确启用并提供 key 后执行真实协议场景。"""

    if not enabled:
        return ContractRunResult(
            status="skipped",
            reason="DeepSeek contract runner was not explicitly enabled",
            records=(),
        )
    if api_key is None or not api_key.strip():
        return ContractRunResult(
            status="skipped",
            reason="DEEPSEEK_API_KEY is not set",
            records=(),
        )

    profile = deepseek_model_profile(
        model_id="deepseek-v4-flash",
        thinking=False,
    )
    store = AgenticSessionStore(session_root=session_root)
    records: list[Mapping[str, Any]] = []
    for scenario in _SCENARIOS:
        created = store.create_session(
            NewSessionRequest(
                investigator_id="investigator_tracker",
                display_name="契约测试调查员",
            )
        )
        sdk_requests: list[Mapping[str, Any]] = []
        recording_model = _EvaluationRecordingModel(
            DeepSeekGameMasterModel(
                api_key,
                client=client,
                request_evidence_sink=sdk_requests.append,
            ),
            sdk_requests,
        )
        harness = AgenticHarness(
            store,
            recording_model,
            model_profile=profile,
        )
        result = harness.start_turn(created.game_id, scenario.player_input)
        records.append(
            _evaluation_record(
                scenario,
                created,
                result,
                recording_model.requests,
                profile,
            )
        )

    passed = all(record["passed"] is True for record in records)
    return ContractRunResult(
        status="passed" if passed else "failed",
        reason=(
            "DeepSeek direct-final and tool-then-final contracts passed"
            if passed
            else "one or more DeepSeek contract paths failed"
        ),
        records=tuple(records),
    )


def run_deepseek_recovery_contract(
    *,
    enabled: bool,
    api_key: str | None,
    session_root: Path,
    client: Any | None = None,
) -> ContractRunResult:
    """运行 Ticket 11 的真实 non-thinking/thinking 恢复契约。

    首次 provider 响应和恢复后的最终响应来自真实 adapter；中断点只在
    本地 wrapper 中注入，用来稳定制造“工具已提交、后续请求失败”的边界。
    """

    if not enabled:
        return ContractRunResult(
            status="skipped",
            reason="DeepSeek recovery contract runner was not explicitly enabled",
            records=(),
        )
    if api_key is None or not api_key.strip():
        return ContractRunResult(
            status="skipped",
            reason="DEEPSEEK_API_KEY is not set",
            records=(),
        )

    store = AgenticSessionStore(session_root=session_root)
    records: list[Mapping[str, Any]] = []
    for scenario in _RECOVERY_SCENARIOS:
        records.append(
            _run_recovery_scenario(
                scenario,
                api_key=api_key,
                store=store,
                client=client,
            )
        )
    passed = all(record["passed"] is True for record in records)
    return ContractRunResult(
        status="passed" if passed else "failed",
        reason=(
            "DeepSeek non-thinking and thinking recovery contracts passed"
            if passed
            else "one or more DeepSeek recovery contract paths failed"
        ),
        records=tuple(records),
    )


def _run_recovery_scenario(
    scenario: _RecoveryContractScenario,
    *,
    api_key: str,
    store: AgenticSessionStore,
    client: Any | None,
) -> dict[str, Any]:
    profile = deepseek_model_profile(
        model_id="deepseek-v4-flash",
        thinking=scenario.thinking,
    )
    created = store.create_session(
        NewSessionRequest(
            investigator_id="investigator_tracker",
            display_name="恢复契约调查员",
        )
    )
    initial_actor_resources = _actor_resource_projection(created.session)
    initial_sdk_requests: list[Mapping[str, Any]] = []
    initial_model = _EvaluationRecordingModel(
        _InterruptAfterToolModel(
            DeepSeekGameMasterModel(
                api_key,
                client=client,
                request_evidence_sink=initial_sdk_requests.append,
            )
        ),
        initial_sdk_requests,
        phase="initial",
    )
    initial_harness = AgenticHarness(
        store,
        initial_model,
        model_profile=profile,
    )
    initial_result = initial_harness.start_turn(
        created.game_id,
        scenario.player_input,
    )
    interrupted_session = store.load_session(created.game_id).session
    interrupted_turn = interrupted_session.get("incomplete_turn")
    attempt_limits = (
        copy.deepcopy(interrupted_turn.get("attempt_limits"))
        if isinstance(interrupted_turn, Mapping)
        else None
    )

    recovery_sdk_requests: list[Mapping[str, Any]] = []
    recovery_requests: list[dict[str, Any]] = []
    recovery_result: TurnResult | None = None
    resume_gate_observed = False
    resume_choice: str | None = None
    if initial_result.status == "interrupted":
        # 用新 store、model 和 Harness 重新打开同一个目录，模拟进程退出后恢复。
        recovered_store = AgenticSessionStore(session_root=store.session_root)
        recovery_model = _EvaluationRecordingModel(
            DeepSeekGameMasterModel(
                api_key,
                client=client,
                request_evidence_sink=recovery_sdk_requests.append,
            ),
            recovery_sdk_requests,
            phase="recovery",
        )
        recovery_harness = AgenticHarness(
            recovered_store,
            recovery_model,
            model_profile=profile,
        )
        lifecycle = recovery_harness.get_session_state(created.game_id)
        resume_gate_observed = (
            lifecycle.has_incomplete_turn
            and lifecycle.turn_id == initial_result.turn_id
        )
        if resume_gate_observed:
            resume_choice = "resume"
            recovery_result = recovery_harness.resume_turn(
                created.game_id,
                initial_result.turn_id,
            )
        recovery_requests = recovery_model.requests

    all_requests = initial_model.requests + recovery_requests
    final_store = AgenticSessionStore(session_root=store.session_root)
    final_session = final_store.load_session(created.game_id).session
    record = _recovery_evaluation_record(
        scenario,
        created,
        initial_result,
        recovery_result,
        final_session,
        all_requests,
        profile,
        attempt_limits,
        initial_actor_resources,
        resume_gate_observed,
        resume_choice,
    )
    return record


def _recovery_evaluation_record(
    scenario: _RecoveryContractScenario,
    created: CreatedSession,
    initial_result: TurnResult,
    recovery_result: TurnResult | None,
    final_session: Mapping[str, Any],
    requests: list[dict[str, Any]],
    profile: Mapping[str, Any],
    attempt_limits: Mapping[str, Any] | None,
    initial_actor_resources: Mapping[str, Mapping[str, Any]],
    resume_gate_observed: bool,
    resume_choice: str | None,
) -> dict[str, Any]:
    final_turns = final_session.get("turns")
    final_turn = (
        final_turns[-1]
        if isinstance(final_turns, list) and final_turns
        else None
    )
    final_mechanics = (
        final_turn.get("mechanics", [])
        if isinstance(final_turn, Mapping)
        else []
    )
    mechanics = [
        _public_mechanic(item)
        for item in final_mechanics
        if isinstance(item, Mapping)
    ]
    public_fact_changes = (
        [
            {
                "kind": item.kind,
                "fact_id": item.fact_id,
                "text": item.text,
            }
            for item in recovery_result.public_fact_changes
        ]
        if recovery_result is not None
        else []
    )
    recovery_projection = _recovery_projection(
        initial_result,
        recovery_result,
        final_session,
        requests,
        thinking=bool(profile["thinking"]),
        initial_actor_resources=initial_actor_resources,
        resume_gate_observed=resume_gate_observed,
        resume_choice=resume_choice,
    )
    passed = _recovery_passed(
        scenario,
        initial_result,
        recovery_result,
        final_session,
        requests,
        profile,
        recovery_projection,
    )
    setup = created.session["setup"]
    return {
        "run_id": f"contract_{uuid4().hex}",
        "scenario_version": scenario.version,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "evaluator": "agentic-recovery-contract-runner",
        "git_revision": _git_revision(),
        "game_id": created.game_id,
        "fixture_version": setup["setup_id"],
        "dependency_versions": _dependency_versions(),
        "prompt_version": profile["prompt_revision"],
        "module_revision": setup["module_reference_revision"],
        "character_revision": setup["character_reference_revision"],
        "model_id": profile["model_id"],
        "thinking": profile["thinking"],
        "provider_parameters": {
            "response_format": profile["response_format"],
            "stream": profile["stream"],
            "thinking": profile["thinking"],
            "temperature": profile["temperature"],
            "top_p": profile["top_p"],
            "max_tokens": profile["max_tokens"],
            "attempt_limits": copy.deepcopy(attempt_limits),
        },
        "tool_schema_version": profile["tool_schema_version"],
        "player_input": scenario.player_input,
        "player_visible_output": {
            "status": (
                recovery_result.status
                if recovery_result is not None
                else initial_result.status
            ),
            "turn_id": (
                recovery_result.turn_id
                if recovery_result is not None
                else initial_result.turn_id
            ),
            "narration": (
                recovery_result.narration
                if recovery_result is not None
                else None
            ),
            "public_mechanics": mechanics,
            "public_fact_changes": public_fact_changes,
            "error_code": (
                recovery_result.error_code
                if recovery_result is not None
                else initial_result.error_code
            ),
            "error_message": (
                recovery_result.error_message
                if recovery_result is not None
                else initial_result.error_message
            ),
        },
        "tool_calls": [
            copy.deepcopy(tool_call)
            for request in requests
            for tool_call in request["tool_calls"]
        ],
        "mechanics": mechanics,
        "fact_changes": public_fact_changes,
        "requests": copy.deepcopy(requests),
        "recovery": recovery_projection,
        "hard_gates": _recovery_hard_gates(
            scenario,
            requests,
            profile,
            recovery_projection,
        ),
        "quality_scores": {
            "fictional_causality": None,
            "improvisation": None,
            "cross_turn_continuity": None,
            "npc_performance": None,
            "pacing": None,
            "atmosphere": None,
        },
        "rationale": (
            "Ticket 11 proves real SDK/provider recovery transport only; "
            "deterministic Harness failures and GM quality remain separate."
        ),
        "passed": passed,
    }


def _recovery_projection(
    initial_result: TurnResult,
    recovery_result: TurnResult | None,
    final_session: Mapping[str, Any],
    requests: list[dict[str, Any]],
    *,
    thinking: bool,
    initial_actor_resources: Mapping[str, Mapping[str, Any]],
    resume_gate_observed: bool,
    resume_choice: str | None,
) -> dict[str, Any]:
    incomplete = final_session.get("incomplete_turn")
    turns = final_session.get("turns")
    final_turn = turns[-1] if isinstance(turns, list) and turns else None
    initial_mechanics = list(initial_result.public_mechanics)
    final_mechanics = (
        final_turn.get("mechanics", [])
        if isinstance(final_turn, Mapping)
        else []
    )
    initial_mechanic = (
        _public_mechanic(initial_mechanics[0])
        if initial_mechanics
        else None
    )
    final_mechanic = (
        _public_mechanic(final_mechanics[0])
        if final_mechanics and isinstance(final_mechanics[0], Mapping)
        else None
    )
    final_turn_ids = [
        turn.get("turn_id")
        for turn in turns
        if isinstance(turn, Mapping)
    ] if isinstance(turns, list) else []
    established_fact_ids = (
        final_turn.get("established_fact_ids", [])
        if isinstance(final_turn, Mapping)
        else []
    )
    final_actor_resources = _actor_resource_projection(final_session)
    character_changes = [
        actor_id
        for actor_id, before in initial_actor_resources.items()
        if final_actor_resources.get(actor_id) != before
    ]
    tool_call_id = (
        requests[0]["tool_calls"][0]["tool_call_id"]
        if requests
        and requests[0]["tool_calls"]
        and isinstance(requests[0]["tool_calls"][0], Mapping)
        else None
    )
    replay_request = next(
        (
            request
            for request in requests
            if request.get("phase") == "recovery"
        ),
        None,
    )
    initial_response = next(
        (
            request
            for request in requests
            if request.get("phase") == "initial"
            and request.get("assistant_reasoning") is not None
            and request.get("tool_calls")
        ),
        None,
    )
    initial_reasoning = (
        initial_response.get("assistant_reasoning")
        if isinstance(initial_response, Mapping)
        else None
    )
    if (
        isinstance(initial_reasoning, Mapping)
        and initial_reasoning.get("present") is not True
    ):
        initial_reasoning = None
    replay_projection = (
        replay_request.get("message_projection")
        if isinstance(replay_request, Mapping)
        else None
    )
    replay_messages = (
        replay_projection.get("messages")
        if isinstance(replay_projection, Mapping)
        else None
    )
    replay_reasoning = next(
        (
            item.get("assistant_reasoning")
            for item in replay_messages
            if isinstance(item, Mapping)
            and item.get("role") == "assistant"
            and isinstance(item.get("assistant_reasoning"), Mapping)
        ),
        None,
    ) if isinstance(replay_messages, list) else None
    if (
        isinstance(replay_reasoning, Mapping)
        and replay_reasoning.get("present") is not True
    ):
        replay_reasoning = None
    reasoning_replay_exact = (
        isinstance(initial_reasoning, Mapping)
        and isinstance(replay_reasoning, Mapping)
        and initial_reasoning == replay_reasoning
        if thinking
        else not bool(initial_reasoning) and not bool(replay_reasoning)
    )
    return {
        "initial_turn_id": initial_result.turn_id,
        "recovered_turn_id": (
            recovery_result.turn_id if recovery_result is not None else None
        ),
        "final_turn_id": (
            final_turn.get("turn_id") if isinstance(final_turn, Mapping) else None
        ),
        "same_turn_id": (
            recovery_result is not None
            and initial_result.turn_id == recovery_result.turn_id
            and isinstance(final_turn, Mapping)
            and final_turn.get("turn_id") == initial_result.turn_id
        ),
        "resume_gate_observed": resume_gate_observed,
        "resume_choice": resume_choice,
        "resume_seam": "AgenticHarness.resume_turn",
        "tool_call_id": tool_call_id,
        "tool_result_replayed": (
            isinstance(replay_request, Mapping)
            and replay_request.get("tool_result_ids") == [tool_call_id]
        ),
        "mechanic_id": (
            initial_mechanic.get("mechanic_id")
            if isinstance(initial_mechanic, Mapping)
            else None
        ),
        "roll": (
            initial_mechanic.get("roll")
            if isinstance(initial_mechanic, Mapping)
            else None
        ),
        "character_changes": character_changes,
        "same_mechanic": initial_mechanic == final_mechanic,
        "turn_count": len(turns) if isinstance(turns, list) else 0,
        "mechanic_count": len(final_mechanics) if isinstance(final_mechanics, list) else 0,
        "turn_ids_unique": len(final_turn_ids) == len(set(final_turn_ids)),
        "established_fact_count": (
            len(established_fact_ids)
            if isinstance(established_fact_ids, list)
            else 0
        ),
        "established_fact_ids_unique": (
            isinstance(established_fact_ids, list)
            and len(established_fact_ids) == len(set(established_fact_ids))
        ),
        "fact_ids_unique": _fact_ids_unique(final_session),
        "initial_interruption": initial_result.error_code,
        "reasoning_present": bool(initial_reasoning),
        "reasoning_length": (
            initial_reasoning.get("length")
            if isinstance(initial_reasoning, Mapping)
            else 0
        ),
        "reasoning_sha256": (
            initial_reasoning.get("sha256")
            if isinstance(initial_reasoning, Mapping)
            else None
        ),
        "reasoning_replay_exact": reasoning_replay_exact,
        "reasoning_body_recorded": _contains_reasoning_body(requests),
        "final_state_clean": (
            incomplete is None
            and "reasoning_content" not in json.dumps(
                final_session,
                ensure_ascii=False,
            )
            and "provider_protocol_errors" not in json.dumps(
                final_session,
                ensure_ascii=False,
            )
        ),
    }


def _contains_reasoning_body(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "reasoning_content" and isinstance(item, str):
                return True
            if _contains_reasoning_body(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_reasoning_body(item) for item in value)
    return False


def _actor_resource_projection(
    session: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    actors = session.get("actors")
    if not isinstance(actors, list):
        return {}
    projection: dict[str, Mapping[str, Any]] = {}
    for actor in actors:
        if not isinstance(actor, Mapping) or not isinstance(actor.get("actor_id"), str):
            continue
        projection[actor["actor_id"]] = {
            "hp": copy.deepcopy(actor.get("hp")),
            "san": copy.deepcopy(actor.get("san")),
            "luck": copy.deepcopy(actor.get("luck")),
        }
    return projection


def _fact_ids_unique(session: Mapping[str, Any]) -> bool:
    facts = session.get("facts")
    if not isinstance(facts, list):
        return False
    fact_ids = [
        fact.get("fact_id")
        for fact in facts
        if isinstance(fact, Mapping)
    ]
    return all(isinstance(fact_id, str) for fact_id in fact_ids) and len(
        fact_ids
    ) == len(set(fact_ids))


def _dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {"python": sys.version.split()[0]}
    for distribution, label in (
        ("openai", "openai"),
        ("python-dotenv", "python-dotenv"),
    ):
        try:
            versions[label] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[label] = "missing"
    return versions


def _recovery_passed(
    scenario: _RecoveryContractScenario,
    initial_result: TurnResult,
    recovery_result: TurnResult | None,
    final_session: Mapping[str, Any],
    requests: list[dict[str, Any]],
    profile: Mapping[str, Any],
    recovery: Mapping[str, Any],
) -> bool:
    if not _recovery_provider_configured(scenario, requests, profile):
        return False
    if len(requests) != 3:
        return False
    if (
        initial_result.status != "interrupted"
        or initial_result.error_code != "request_timeout"
        or recovery_result is None
        or recovery_result.status != "committed"
        or final_session.get("incomplete_turn") is not None
        or not recovery["resume_gate_observed"]
        or recovery["resume_choice"] != "resume"
    ):
        return False
    if not recovery["same_turn_id"] or not recovery["same_mechanic"]:
        return False
    if (
        not recovery["tool_result_replayed"]
        or not recovery["final_state_clean"]
        or recovery["reasoning_body_recorded"]
        or recovery["turn_count"] != 1
        or recovery["mechanic_count"] != 1
        or not recovery["turn_ids_unique"]
        or not recovery["established_fact_ids_unique"]
        or not recovery["fact_ids_unique"]
        or recovery["character_changes"]
    ):
        return False
    if scenario.thinking and not recovery["reasoning_replay_exact"]:
        return False
    if not scenario.thinking and recovery["reasoning_present"]:
        return False
    return True


def _recovery_provider_configured(
    scenario: _RecoveryContractScenario,
    requests: list[dict[str, Any]],
    profile: Mapping[str, Any],
) -> bool:
    provider_requests = [
        request
        for request in requests
        if request.get("response_format") is not None
    ]
    return (
        len(provider_requests) == 2
        and profile["model_id"] == "deepseek-v4-flash"
        and profile["thinking"] is scenario.thinking
        and profile["stream"] is False
        and profile["response_format"] == "json_object"
        and all(
            request["model_id"] == "deepseek-v4-flash"
            and request["function_tools"] == ["make_check"]
            and request["response_format"] == {"type": "json_object"}
            and request["stream"] is False
            and request["max_tokens"] == 4096
            and isinstance(request["timeout"], (int, float))
            and request["timeout"] > 0
            and request["thinking"]
            == ("enabled" if scenario.thinking else "disabled")
            for request in provider_requests
        )
    )


def _recovery_hard_gates(
    scenario: _RecoveryContractScenario,
    requests: list[dict[str, Any]],
    profile: Mapping[str, Any],
    recovery: Mapping[str, Any],
) -> dict[str, Mapping[str, str]]:
    protocol_passed = (
        _recovery_provider_configured(scenario, requests, profile)
        and recovery["resume_gate_observed"]
        and recovery["resume_choice"] == "resume"
        and recovery["same_turn_id"]
        and recovery["tool_result_replayed"]
    )
    mechanics_passed = (
        recovery["same_mechanic"]
        and recovery["turn_count"] == 1
        and recovery["mechanic_count"] == 1
        and recovery["turn_ids_unique"]
        and recovery["established_fact_ids_unique"]
        and recovery["fact_ids_unique"]
        and not recovery["character_changes"]
    )
    hidden_control_passed = (
        recovery["final_state_clean"]
        and not recovery["reasoning_body_recorded"]
        and (
            not scenario.thinking
            or recovery["reasoning_replay_exact"]
        )
    )
    return {
        "protocol_legality": {
            "status": "passed" if protocol_passed else "failed",
            "evidence": "real SDK tool result replay used a matching tool_call_id",
        },
        "mechanical_truth": {
            "status": "passed" if mechanics_passed else "failed",
            "evidence": (
                f"mechanic_id={recovery.get('mechanic_id')}, "
                f"roll={recovery.get('roll')}, count={recovery.get('mechanic_count')}"
            ),
        },
        "hidden_content_control": {
            "status": "passed" if hidden_control_passed else "failed",
            "evidence": "reasoning body and provider protocol material are absent after commit",
        },
        "investigator_ownership": {
            "status": "not_evaluated",
            "evidence": "Ticket 11 does not score GM quality or player agency",
        },
        "canon_continuity": {
            "status": "not_evaluated",
            "evidence": "Ticket 11 proves transport and recovery only",
        },
        "open_action_validity": {
            "status": "not_evaluated",
            "evidence": "Ticket 11 does not re-evaluate open-action causality",
        },
    }


def _evaluation_record(
    scenario: _ContractScenario,
    created: CreatedSession,
    result: TurnResult,
    requests: list[dict[str, Any]],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    passed = _scenario_passed(scenario, result, requests, profile)
    public_mechanics = [_public_mechanic(item) for item in result.public_mechanics]
    public_fact_changes = [
        {
            "kind": item.kind,
            "fact_id": item.fact_id,
            "text": item.text,
        }
        for item in result.public_fact_changes
    ]
    setup = created.session["setup"]
    return {
        "run_id": f"contract_{uuid4().hex}",
        "scenario_version": scenario.version,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "evaluator": "agentic-contract-runner",
        "git_revision": _git_revision(),
        "prompt_version": profile["prompt_revision"],
        "module_revision": setup["module_reference_revision"],
        "character_revision": setup["character_reference_revision"],
        "model_id": profile["model_id"],
        "thinking": profile["thinking"],
        "provider_parameters": {
            "response_format": profile["response_format"],
            "stream": profile["stream"],
            "temperature": profile["temperature"],
            "top_p": profile["top_p"],
            "max_tokens": profile["max_tokens"],
        },
        "tool_schema_version": profile["tool_schema_version"],
        "player_input": scenario.player_input,
        "player_visible_output": {
            "status": result.status,
            "turn_id": result.turn_id,
            "narration": result.narration,
            "public_mechanics": public_mechanics,
            "public_fact_changes": public_fact_changes,
            "error_code": result.error_code,
            "error_message": result.error_message,
        },
        "tool_calls": [
            copy.deepcopy(tool_call)
            for request in requests
            for tool_call in request["tool_calls"]
        ],
        "mechanics": public_mechanics,
        "fact_changes": public_fact_changes,
        "requests": copy.deepcopy(requests),
        "hard_gates": _hard_gate_conclusions(passed, scenario),
        "quality_scores": {
            "fictional_causality": None,
            "improvisation": None,
            "cross_turn_continuity": None,
            "npc_performance": None,
            "pacing": None,
            "atmosphere": None,
        },
        "rationale": (
            "Ticket 04 evaluates provider protocol only; Ticket 05 owns "
            "real-key causal and human judgments."
        ),
        "passed": passed,
    }


def _scenario_passed(
    scenario: _ContractScenario,
    result: TurnResult,
    requests: list[dict[str, Any]],
    profile: Mapping[str, Any],
) -> bool:
    configured = (
        profile["model_id"] == "deepseek-v4-flash"
        and profile["thinking"] is False
        and profile["stream"] is False
        and profile["response_format"] == "json_object"
        and all(
            item["function_tools"] == ["make_check"]
            and item["response_format"] == {"type": "json_object"}
            and item["stream"] is False
            for item in requests
        )
    )
    if not configured or result.status != "committed":
        return False
    if scenario.expected_path == "direct_final":
        return len(requests) == 1 and requests[0]["tool_calls"] == []
    if len(requests) != 2 or len(requests[0]["tool_calls"]) != 1:
        return False
    tool_call = requests[0]["tool_calls"][0]
    tool_call_id = tool_call["tool_call_id"]
    return (
        tool_call["tool_name"] == "make_check"
        and isinstance(tool_call_id, str)
        and requests[1]["tool_result_ids"] == [tool_call_id]
        and requests[1]["tool_calls"] == []
        and len(result.public_mechanics) == 1
    )


def _sdk_evidence_for_call(
    sdk_requests: list[Mapping[str, Any]],
    previous_count: int,
) -> dict[str, Any]:
    if len(sdk_requests) != previous_count + 1:
        return {
            "model_id": None,
            "function_tools": [],
            "response_format": None,
            "stream": None,
            "max_tokens": None,
            "timeout": None,
            "thinking": None,
        }
    return copy.deepcopy(dict(sdk_requests[-1]))


def _reasoning_projection(message: Mapping[str, Any]) -> dict[str, Any]:
    reasoning = message.get("reasoning_content")
    if not isinstance(reasoning, str):
        return {"present": False, "length": 0, "sha256": None}
    return {
        "present": True,
        "length": len(reasoning),
        "sha256": hashlib.sha256(reasoning.encode("utf-8")).hexdigest(),
    }


def _message_projection(
    messages: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    """记录消息角色和协议 ID，不复制上下文、事实或 reasoning 正文。"""

    projection: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {"role": message.get("role")}
        if message.get("role") == "assistant":
            tool_calls = message.get("tool_calls")
            item["tool_call_ids"] = (
                [
                    call.get("id")
                    for call in tool_calls
                    if isinstance(call, Mapping)
                    and isinstance(call.get("id"), str)
                ]
                if isinstance(tool_calls, list)
                else []
            )
            item["assistant_reasoning"] = _reasoning_projection(message)
        elif message.get("role") == "tool":
            item["tool_call_id"] = message.get("tool_call_id")
            item["name"] = message.get("name")
        projection.append(item)
    return {"messages": projection}


def _tool_result_ids(
    messages: tuple[Mapping[str, Any], ...],
) -> list[str]:
    return [
        identifier
        for message in messages
        if message.get("role") == "tool"
        and isinstance((identifier := message.get("tool_call_id")), str)
    ]


def _sanitized_tool_calls(
    assistant_message: Mapping[str, Any],
) -> list[dict[str, str | None]]:
    tool_calls = assistant_message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return []
    sanitized: list[dict[str, str | None]] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, Mapping):
            continue
        raw_identifier = tool_call.get("id")
        identifier = (
            raw_identifier
            if isinstance(raw_identifier, str)
            and raw_identifier
            and raw_identifier == raw_identifier.strip()
            else None
        )
        function = tool_call.get("function")
        name = (
            "make_check"
            if isinstance(function, Mapping)
            and function.get("name") == "make_check"
            else "unsupported"
        )
        sanitized.append(
            {"tool_call_id": identifier, "tool_name": name}
        )
    return sanitized


def _public_mechanic(mechanic: Any) -> dict[str, Any]:
    if isinstance(mechanic, Mapping):
        return {
            "mechanic_id": mechanic["mechanic_id"],
            "actor_id": mechanic["actor_id"],
            "ability": mechanic["ability"],
            "ability_value": mechanic["ability_value"],
            "difficulty": mechanic["difficulty"],
            "target": mechanic["target"],
            "dice_adjustment": copy.deepcopy(dict(mechanic["dice_adjustment"])),
            "roll": mechanic["roll"],
            "success_level": mechanic["success_level"],
            "action": mechanic["action"],
            "stakes": mechanic["stakes"],
        }
    return {
        "mechanic_id": mechanic.mechanic_id,
        "actor_id": mechanic.actor_id,
        "ability": mechanic.ability,
        "ability_value": mechanic.ability_value,
        "difficulty": mechanic.difficulty,
        "target": mechanic.target,
        "dice_adjustment": copy.deepcopy(dict(mechanic.dice_adjustment)),
        "roll": mechanic.roll,
        "success_level": mechanic.success_level,
        "action": mechanic.action,
        "stakes": mechanic.stakes,
    }


def _hard_gate_conclusions(
    protocol_passed: bool,
    scenario: _ContractScenario,
) -> dict[str, Mapping[str, str]]:
    protocol_status = "passed" if protocol_passed else "failed"
    not_evaluated = {
        "status": "not_evaluated",
        "evidence": "Ticket 05 requires a real key and human judgment",
    }
    return {
        "protocol_legality": {
            "status": protocol_status,
            "evidence": f"checked {scenario.expected_path} provider contract",
        },
        "mechanical_truth": {
            "status": protocol_status,
            "evidence": "Harness-owned mechanic projection checked when applicable",
        },
        "hidden_content_control": {
            "status": protocol_status,
            "evidence": "record contains only player-visible projections",
        },
        "investigator_ownership": copy.deepcopy(not_evaluated),
        "canon_continuity": copy.deepcopy(not_evaluated),
        "open_action_validity": copy.deepcopy(not_evaluated),
    }


def _git_revision() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    revision = completed.stdout.strip()
    return revision if completed.returncode == 0 and revision else "unknown"


def main() -> int:
    """运行显式启用的契约套件，stdout 只输出脱敏结果。"""

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    recovery_enabled = (
        os.environ.get("MONMUSU_RUN_DEEPSEEK_RECOVERY_CONTRACT") == "1"
    )
    enabled = (
        recovery_enabled
        or os.environ.get("MONMUSU_RUN_DEEPSEEK_CONTRACT") == "1"
    )
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    try:
        with tempfile.TemporaryDirectory(
            prefix="monmusu-agent-deepseek-contract-"
        ) as directory:
            if recovery_enabled:
                result = run_deepseek_recovery_contract(
                    enabled=enabled,
                    api_key=api_key,
                    session_root=Path(directory) / "sessions",
                )
            else:
                result = run_deepseek_contract(
                    enabled=enabled,
                    api_key=api_key,
                    session_root=Path(directory) / "sessions",
                )
    except Exception:
        print("FAIL: DeepSeek contract runner could not produce evidence")
        return 1
    if result.status == "skipped":
        print(f"SKIP: {result.reason}")
        return 0
    print(
        json.dumps(
            {
                "status": result.status,
                "reason": result.reason,
                "records": result.records,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
