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
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence
from uuid import uuid4

from dotenv import load_dotenv

from monmusu_agent.agentic_harness import AgenticHarness, TurnResult
from monmusu_agent.agentic_model import (
    DEFAULT_COC_TOOL_NAMES,
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
from monmusu_agent.storage import write_json_atomic


@dataclass(frozen=True)
class ContractRunResult:
    """区分未启用、协议通过与协议失败，不把 skip 记作通过。"""

    status: Literal["skipped", "passed", "failed", "pending_human"]
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

_OPENCODE_GO_SCENARIOS = (
    _ContractScenario(
        version="ticket-20-direct-final-v1",
        player_input=(
            "直接裁定下面的行动，不要调用任何工具，也不要先写检定说明："
            "我拿起眼前无人看守、伸手就能够到的铜钥匙。请直接提交最终答复。"
        ),
        expected_path="direct_final",
    ),
    _ContractScenario(
        version="ticket-20-tool-then-final-v1",
        player_input=(
            "我必须用一次 make_check 工具结算“用肩膀猛撞锈蚀牢门，试图撞断锁扣”；"
            "在工具结果返回前不要提交最终答复。失败风险是巨响可能引来正在远去的船工。"
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
            "structure_repairs": _structure_repair_count(request.messages),
            "structure_repair_request": not request.tools,
            "model_request_messages_sha256": _canonical_json_sha256(
                request.messages
            ),
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


class _InjectRetryableProviderErrorModel:
    """真实 retry 观察契约的本地注入边界：第一跳失败，随后走真实 adapter。"""

    def __init__(self, delegate: GameMasterModel) -> None:
        self.delegate = delegate
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            raise ModelCallError(
                "provider_server_error",
                "retry observation injected transient server failure",
                retryable=True,
                status=503,
                provider_retry_after_ms=1_000,
            )
        return self.delegate.complete(request)


class _FixtureModel:
    """用公开 Harness seam 建立可追溯的场景前置事实。"""

    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = iter(responses)

    def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        return next(self.responses)


class _FixtureRandom:
    """为真实场景固定必要骰序，不把规则期望交给 provider。"""

    def __init__(self, values: tuple[int, ...]) -> None:
        self.values = iter(values)

    def randint(self, minimum: int, maximum: int) -> int:
        value = next(self.values)
        if not minimum <= value <= maximum:
            raise ValueError("fixture random value outside requested range")
        return value


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
        enabled_tools=("make_check",),
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


def run_opencode_go_contract(
    *,
    enabled: bool,
    api_key: str | None,
    session_root: Path,
    client: Any | None = None,
) -> ContractRunResult:
    """只在外部显式启用并提供 opencode-go key 后执行真实协议场景。"""

    if not enabled:
        return ContractRunResult(
            status="skipped",
            reason="OpenCode Go contract runner was not explicitly enabled",
            records=(),
        )
    if api_key is None or not api_key.strip():
        return ContractRunResult(
            status="skipped",
            reason="OPENCODE_GO_API_KEY is not set",
            records=(),
        )

    profile = deepseek_model_profile(
        provider="opencode-go",
        model_id="deepseek-v4-flash",
        thinking=False,
        enabled_tools=("make_check",),
    )
    store = AgenticSessionStore(session_root=session_root)
    records: list[Mapping[str, Any]] = []
    for scenario in _OPENCODE_GO_SCENARIOS:
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
                base_url=profile["base_url"],
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
        record = dict(
            _evaluation_record(
                scenario,
                created,
                result,
                recording_model.requests,
                profile,
            )
        )
        record["provider"] = profile["provider"]
        record["base_url"] = profile["base_url"]
        record["rationale"] = (
            "Ticket 20 evaluates OpenCode Go protocol only; it does not "
            "score GM quality or select a default provider."
        )
        records.append(record)

    passed = all(record["passed"] is True for record in records)
    return ContractRunResult(
        status="passed" if passed else "failed",
        reason=(
            "OpenCode Go direct-final and tool-then-final contracts passed"
            if passed
            else "one or more OpenCode Go contract paths failed"
        ),
        records=tuple(records),
    )


def run_increment_three_evaluation(
    *,
    enabled: bool,
    api_key: str | None,
    session_root: Path,
    client: Any | None = None,
) -> ContractRunResult:
    """运行 Ticket 18 场景二、三，并保留人工判断边界。"""

    if not enabled:
        return ContractRunResult(
            status="skipped",
            reason="Increment 3 evaluation was not explicitly enabled",
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
        enabled_tools=DEFAULT_COC_TOOL_NAMES,
    )
    records: list[Mapping[str, Any]] = []
    records.append(
        _run_increment3_scenario_two(
            api_key=api_key,
            client=client,
            profile=profile,
            session_root=session_root / "scenario-two",
        )
    )
    records.append(
        _run_increment3_scenario_three(
            api_key=api_key,
            client=client,
            profile=profile,
            session_root=session_root / "scenario-three",
        )
    )
    automated_passed = all(record["automated_passed"] is True for record in records)
    return ContractRunResult(
        status="pending_human" if automated_passed else "failed",
        reason=(
            "Increment 3 automated gates passed; human GM judgment is pending"
            if automated_passed
            else "Increment 3 automated gates failed"
        ),
        records=tuple(records),
    )


def _run_increment3_scenario_two(
    *,
    api_key: str,
    client: Any | None,
    profile: Mapping[str, Any],
    session_root: Path,
) -> dict[str, Any]:
    """场景二先用确定性回合建立仓库、钥匙和断桥的公开正典。"""

    store = AgenticSessionStore(session_root=session_root)
    created = store.create_session(
        NewSessionRequest(
            investigator_id="investigator_tracker",
            display_name="场景二调查员",
        )
    )
    locked_door_fact_id = _active_fact_id(
        created.session,
        "石牢的牢门仍然锁着。",
    )
    fixture = AgenticHarness(
        store,
        _FixtureModel(
            [
                _fixture_final_response(
                    "调查员已经离开石牢，来到无人看守的仓库。桌上放着一把触手可及的铜钥匙；仓库外的断桥正被海浪间歇冲刷。",
                    establish=(
                        {
                            "visibility": "public",
                            "text": "调查员已经在无人看守的仓库内。",
                        },
                        {
                            "visibility": "public",
                            "text": "一把已经看见且能够直接够到的铜钥匙放在仓库桌上。",
                        },
                        {
                            "visibility": "public",
                            "text": "仓库外是一段被海浪间歇冲刷的断桥。",
                        },
                        {
                            "visibility": "public",
                            "text": "从断桥跌落会受伤并引来注意。",
                        },
                    ),
                    retire=(
                        {
                            "fact_id": locked_door_fact_id,
                            "reason": "调查员已经离开石牢并进入仓库，牢门不再构成当前阻挡。",
                        },
                    ),
                )
            ]
        ),
        model_profile=profile,
    )
    fixture_result = fixture.start_turn(
        created.game_id,
        "我检查仓库和通往外侧平台的路。",
    )

    sdk_requests: list[Mapping[str, Any]] = []
    model = _EvaluationRecordingModel(
        DeepSeekGameMasterModel(
            api_key,
            client=client,
            request_evidence_sink=sdk_requests.append,
        ),
        sdk_requests,
        phase="scenario-two",
    )
    harness = AgenticHarness(
        store,
        model,
        model_profile=profile,
        mechanic_id_factory=iter(("mechanic_ticket18_scenario2_jump",)).__next__,
        random_source=_FixtureRandom((0, 1)),
    )
    player_inputs = (
        "我拿起桌上的铜钥匙收好。",
        "趁下一股浪还没打来，我助跑跳过断桥，去对面的门楼。",
    )
    results = tuple(
        harness.start_turn(created.game_id, player_input)
        for player_input in player_inputs
    )
    final_session = store.load_session(created.game_id).session
    return _increment3_evaluation_record(
        scenario_version="ticket-18-focused-scenario-2-v1",
        fixture_version="ticket-18-scenario-2-fixture-v2",
        created=created,
        fixture_result=fixture_result,
        results=results,
        requests=model.requests,
        profile=profile,
        player_inputs=player_inputs,
        final_session=final_session,
        api_key=api_key,
        expected_tool_path=("final", "make_check", "final"),
        attempt_limits=harness.attempt_limits,
    )


def _run_increment3_scenario_three(
    *,
    api_key: str,
    client: Any | None,
    profile: Mapping[str, Any],
    session_root: Path,
) -> dict[str, Any]:
    """场景三用确定性失败检定启动玩家明确选择的 Push 链。"""

    store = AgenticSessionStore(session_root=session_root)
    created = store.create_session(
        NewSessionRequest(
            investigator_id="investigator_mender",
            display_name="场景三调查员",
        )
    )
    departing_boatman_fact_id = _active_fact_id(
        created.session,
        "唯一还在看守的船工已经提灯离开，脚步与灯光正在远去。",
    )
    unattended_fact_id = _active_fact_id(
        created.session,
        "石牢附近暂时无人看守。",
    )
    base_arguments = {
        "actor_id": "investigator_mender",
        "ability": "locksmith",
        "difficulty": "regular",
        "dice_adjustment": {"kind": "none", "count": 0},
        "action": "撬开牢门上的生锈锁扣",
        "stakes": "失败会耽搁时间，让逼近的脚步更清晰",
        "visibility": "public",
    }
    fixture = AgenticHarness(
        store,
        _FixtureModel(
            [
                _fixture_tool_response(
                    "make_check",
                    base_arguments,
                    "call_ticket18_scenario3_base",
                ),
                _fixture_final_response(
                    "撬棍滑开，门仍未打开；先前远去的脚步已经转向并逼近，成为再次失败时的更严重风险。",
                    establish=(
                        {
                            "visibility": "public",
                            "text": "牢门仍未打开。",
                        },
                        {
                            "visibility": "public",
                            "text": "逼近的脚步已经更清晰，并构成孤注一掷失败时的更严重风险。",
                        },
                    ),
                    retire=(
                        {
                            "fact_id": departing_boatman_fact_id,
                            "reason": "先前远去的脚步已经转向，正朝石牢逼近。",
                        },
                        {
                            "fact_id": unattended_fact_id,
                            "reason": "逼近的脚步意味着石牢附近已不再无人看守。",
                        },
                    ),
                ),
            ]
        ),
        model_profile=profile,
        mechanic_id_factory=iter(("mechanic_ticket18_scenario3_base",)).__next__,
        random_source=_FixtureRandom((0, 6)),
    )
    fixture_result = fixture.start_turn(
        created.game_id,
        "我用撬棍撬开牢门上的生锈锁扣。",
    )

    sdk_requests: list[Mapping[str, Any]] = []
    model = _EvaluationRecordingModel(
        DeepSeekGameMasterModel(
            api_key,
            client=client,
            request_evidence_sink=sdk_requests.append,
        ),
        sdk_requests,
        phase="scenario-three",
    )
    harness = AgenticHarness(
        store,
        model,
        model_profile=profile,
        mechanic_id_factory=iter(("mechanic_ticket18_scenario3_push",)).__next__,
        random_source=_FixtureRandom((0, 1)),
    )
    player_inputs = (
        "先别替我花幸运，也不要自动重掷。告诉我门没开以后现在发生了什么；如果换一种办法孤注一掷，我要承担什么更严重的风险？",
        "我不花幸运。我拆门轴，从铰链这边强行卸门；我接受你刚才说的更严重后果，孤注一掷。",
    )
    results = tuple(
        harness.start_turn(created.game_id, player_input)
        for player_input in player_inputs
    )
    final_session = store.load_session(created.game_id).session
    return _increment3_evaluation_record(
        scenario_version="ticket-18-focused-scenario-3-v1",
        fixture_version="ticket-18-scenario-3-fixture-v2",
        created=created,
        fixture_result=fixture_result,
        results=results,
        requests=model.requests,
        profile=profile,
        player_inputs=player_inputs,
        final_session=final_session,
        api_key=api_key,
        expected_tool_path=("final", "push_check", "final"),
        attempt_limits=harness.attempt_limits,
    )


def _fixture_final_response(
    narration: str,
    *,
    establish: tuple[Mapping[str, str], ...] = (),
    retire: tuple[Mapping[str, str], ...] = (),
) -> ModelResponse:
    return ModelResponse(
        assistant_message={
            "role": "assistant",
            "content": json.dumps(
                {
                    "narration": narration,
                    "establish": list(establish),
                    "retire": list(retire),
                    "session_status": "ongoing",
                },
                ensure_ascii=False,
            ),
            "reasoning_content": None,
            "tool_calls": [],
        },
        finish_reason="stop",
        usage=None,
        latency_ms=0,
    )


def _active_fact_id(session: Mapping[str, Any], text: str) -> str:
    """按权威开场事实原文定位场景夹具需要结束的当前事实。"""

    matches = [
        fact["fact_id"]
        for fact in session.get("facts", [])
        if isinstance(fact, Mapping)
        and fact.get("status") == "active"
        and fact.get("text") == text
        and isinstance(fact.get("fact_id"), str)
    ]
    if len(matches) != 1:
        raise ValueError("focused scenario fixture fact is missing or ambiguous")
    return matches[0]


def _fixture_tool_response(
    name: str,
    arguments: Mapping[str, Any],
    call_id: str,
) -> ModelResponse:
    return ModelResponse(
        assistant_message={
            "role": "assistant",
            "content": None,
            "reasoning_content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(
                            dict(arguments),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                }
            ],
        },
        finish_reason="tool_calls",
        usage=None,
        latency_ms=0,
    )


def _increment3_evaluation_record(
    *,
    scenario_version: str,
    fixture_version: str,
    created: CreatedSession,
    fixture_result: TurnResult,
    results: tuple[TurnResult, ...],
    requests: list[dict[str, Any]],
    profile: Mapping[str, Any],
    player_inputs: tuple[str, ...],
    final_session: Mapping[str, Any],
    api_key: str,
    expected_tool_path: tuple[str, ...],
    attempt_limits: Mapping[str, int],
) -> dict[str, Any]:
    public_mechanics = [
        _public_mechanic(item)
        for result in results
        for item in result.public_mechanics
    ]
    public_fact_changes = [
        {
            "kind": item.kind,
            "fact_id": item.fact_id,
            "text": item.text,
        }
        for result in results
        for item in result.public_fact_changes
    ]
    fixture_mechanics = [
        _public_mechanic(item) for item in fixture_result.public_mechanics
    ]
    player_visible_outputs = [
        {
            "status": result.status,
            "turn_id": result.turn_id,
            "narration": result.narration,
            "public_mechanics": [
                _public_mechanic(item) for item in result.public_mechanics
            ],
            "public_fact_changes": [
                {
                    "kind": item.kind,
                    "fact_id": item.fact_id,
                    "text": item.text,
                }
                for item in result.public_fact_changes
            ],
            "error_code": result.error_code,
            "error_message": result.error_message,
        }
        for result in results
    ]
    initial_resources = _actor_resource_projection(created.session)
    final_resources = _actor_resource_projection(final_session)
    investigator_id = _investigator_actor_id(created.session)
    investigator_before = initial_resources.get(investigator_id, {})
    investigator_after = final_resources.get(investigator_id, {})
    resource_changes = {
        "luck": {
            "before": investigator_before.get("luck", {}).get("current"),
            "after": investigator_after.get("luck", {}).get("current"),
        }
    }
    record: dict[str, Any] = {
        "run_id": f"increment3_{uuid4().hex}",
        "scenario_version": scenario_version,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "evaluator": "agentic-increment3-runner",
        "git_revision": _git_revision(),
        "prompt_version": profile["prompt_revision"],
        "module_revision": created.session["setup"]["module_reference_revision"],
        "character_revision": created.session["setup"]["character_reference_revision"],
        "dependency_versions": _dependency_versions(),
        "model_id": profile["model_id"],
        "thinking": profile["thinking"],
        "provider_parameters": {
            "response_format": profile["response_format"],
            "stream": profile["stream"],
            "temperature": profile["temperature"],
            "top_p": profile["top_p"],
            "max_tokens": profile["max_tokens"],
        },
        "attempt_limits": copy.deepcopy(dict(attempt_limits)),
        "tool_schema_version": profile["tool_schema_version"],
        "fixture": {
            "kind": "deterministic_harness",
            "version": fixture_version,
            "status": fixture_result.status,
            "mechanics": fixture_mechanics,
            "public_fact_changes": [
                {
                    "kind": item.kind,
                    "fact_id": item.fact_id,
                    "text": item.text,
                }
                for item in fixture_result.public_fact_changes
            ],
        },
        "player_inputs": list(player_inputs),
        "player_visible_outputs": player_visible_outputs,
        "player_input": player_inputs[-1],
        "player_visible_output": player_visible_outputs[-1],
        "tool_calls": [
            copy.deepcopy(tool_call)
            for request in requests
            for tool_call in request["tool_calls"]
        ],
        "mechanics": public_mechanics,
        "fact_changes": public_fact_changes,
        "resource_changes": resource_changes,
        "requests": copy.deepcopy(requests),
        "hard_gates": _increment3_hard_gates(
            requests,
            profile,
            results,
            expected_tool_path,
            resource_changes,
            public_mechanics,
            fixture_result,
            player_inputs,
            attempt_limits,
            final_session,
        ),
        "quality_scores": _pending_quality_scores(),
        "human_judgment": {
            "status": "pending_user",
            "quality_scores": _pending_quality_scores(),
            "notes": None,
        },
        "rationale": (
            "Deterministic fixture and automated protocol/mechanics gates are "
            "recorded separately; human GM behavior judgment remains pending."
        ),
        "passed": False,
    }
    gates = record["hard_gates"]
    record["automated_passed"] = all(
        gate["status"] == "passed"
        for gate in gates.values()
        if gate["status"] != "pending_human"
    )
    rendered = json.dumps(record, ensure_ascii=False)
    if api_key in rendered or "reasoning_content" in rendered:
        record["automated_passed"] = False
        record["hard_gates"]["hidden_content_control"] = {
            "status": "failed",
            "evidence": "secret or reasoning material appeared in the record",
        }
    return record


def _increment3_hard_gates(
    requests: list[dict[str, Any]],
    profile: Mapping[str, Any],
    results: tuple[TurnResult, ...],
    expected_tool_path: tuple[str, ...],
    resource_changes: Mapping[str, Any],
    public_mechanics: Sequence[Mapping[str, Any]],
    fixture_result: TurnResult,
    player_inputs: tuple[str, ...],
    attempt_limits: Mapping[str, int],
    final_session: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    configured = (
        profile["model_id"] == "deepseek-v4-flash"
        and profile["thinking"] is False
        and profile["stream"] is False
        and profile["response_format"] == "json_object"
        and profile["enabled_tools"] == list(DEFAULT_COC_TOOL_NAMES)
        and attempt_limits
        == {
            "max_round_trips": 8,
            "request_timeout_seconds": 60,
            "attempt_timeout_seconds": 180,
            "max_structure_repairs": 1,
        }
        and all(
            request["function_tools"]
            == (
                []
                if request["structure_repair_request"]
                else list(DEFAULT_COC_TOOL_NAMES)
            )
            and request["response_format"] == {"type": "json_object"}
            and request["stream"] is False
            and request["thinking"] == "disabled"
            and request["timeout"] == 60.0
            and request["messages_sha256"]
            == request["model_request_messages_sha256"]
            for request in requests
        )
    )
    observed_path = tuple(
        "final" if not request["tool_calls"] else request["tool_calls"][0]["tool_name"]
        for request in requests
        if not request["structure_repair_request"]
    )
    repairs_by_player_input: dict[str, int] = {}
    for request in requests:
        if not request["structure_repair_request"]:
            continue
        input_hash = request["message_projection"].get(
            "current_player_input_sha256"
        )
        if isinstance(input_hash, str):
            repairs_by_player_input[input_hash] = (
                repairs_by_player_input.get(input_hash, 0) + 1
            )
    protocol = (
        configured
        and observed_path == expected_tool_path
        and all(len(request["tool_calls"]) <= 1 for request in requests)
        and all(count <= 1 for count in repairs_by_player_input.values())
        and all(result.status == "committed" for result in results)
    )
    expected_push_source = next(
        (
            item.mechanic_id
            for item in fixture_result.public_mechanics
            if item.kind == "check"
        ),
        None,
    )
    mechanics = (
        expected_tool_path == ("final", "make_check", "final")
        and len(public_mechanics) == 1
        and public_mechanics[0]["kind"] == "check"
    ) or (
        expected_tool_path == ("final", "push_check", "final")
        and len(public_mechanics) == 1
        and public_mechanics[0]["kind"] == "check"
        and expected_push_source is not None
        and public_mechanics[0]["details"].get("pushed_from")
        == expected_push_source
    )
    hidden = all(
        "reasoning_content" not in json.dumps(request, ensure_ascii=False)
        for request in requests
    )
    player_choice = (
        expected_tool_path == ("final", "push_check", "final")
        and observed_path == expected_tool_path
        and resource_changes["luck"]["before"]
        == resource_changes["luck"]["after"]
    )
    context_continuity = _increment3_context_continuity(
        requests=requests,
        fixture_result=fixture_result,
        results=results,
        player_inputs=player_inputs,
        final_session=final_session,
    )
    return {
        "protocol_legality": {
            "status": "passed" if protocol else "failed",
            "evidence": (
                "provider profile, tool order and final responses matched the scenario"
                if protocol
                else (
                    f"observed provider path {observed_path!r} or committed turn "
                    "status did not match the scenario"
                )
            ),
        },
        "mechanical_truth": {
            "status": "passed" if mechanics else "failed",
            "evidence": (
                "public mechanic kind and source matched the expected committed mechanic"
                if mechanics
                else "public mechanic kind, count or source did not match the scenario"
            ),
        },
        "hidden_content_control": {
            "status": "passed" if hidden else "failed",
            "evidence": "recorded requests contain no reasoning body",
        },
        "investigator_ownership": {
            "status": "passed" if player_choice else "pending_human",
            "evidence": "Push was requested only after the player selected a different approach"
            if player_choice
            else (
                "scenario-two ownership remains part of human review"
                if expected_tool_path == ("final", "make_check", "final")
                else "automated tool path did not isolate the player's Push choice"
            ),
        },
        "canon_continuity": {
            "status": "passed" if context_continuity else "failed",
            "evidence": (
                (
                    "redacted context fingerprints matched the fixture, prior turn, "
                    "current input and committed tool result"
                )
                if context_continuity
                else (
                    "redacted context, input or committed result fingerprints did "
                    "not match the persisted scenario"
                )
            ),
        },
        "open_action_validity": {
            "status": "pending_human",
            "evidence": "human reviewer must judge causal fit of the open action",
        },
    }


def _increment3_context_continuity(
    *,
    requests: Sequence[Mapping[str, Any]],
    fixture_result: TurnResult,
    results: tuple[TurnResult, ...],
    player_inputs: tuple[str, ...],
    final_session: Mapping[str, Any],
) -> bool:
    """用脱敏指纹证明跨回合上下文，而不复制隐藏正文。"""

    if len(results) != 2 or len(player_inputs) != 2:
        return False
    raw_projections = [request.get("message_projection") for request in requests]
    if not all(isinstance(item, Mapping) for item in raw_projections) or any(
        request.get("messages_sha256")
        != request.get("model_request_messages_sha256")
        for request in requests
    ):
        return False
    projections = [
        item for item in raw_projections if isinstance(item, Mapping)
    ]
    input_hashes = tuple(_text_sha256(item) for item in player_inputs)
    observed_input_hashes = tuple(
        projection.get("current_player_input_sha256") for projection in projections
    )
    if (
        not projections
        or any(item not in input_hashes for item in observed_input_hashes)
        or input_hashes[0] not in observed_input_hashes
        or input_hashes[1] not in observed_input_hashes
        or list(observed_input_hashes) != sorted(
            observed_input_hashes,
            key=input_hashes.index,
        )
    ):
        return False

    fixture_turn_id = fixture_result.turn_id
    prior_turn_id = results[0].turn_id
    if not isinstance(fixture_turn_id, str) or not isinstance(prior_turn_id, str):
        return False
    raw_contexts = [projection.get("context") for projection in projections]
    if not all(isinstance(item, Mapping) for item in raw_contexts):
        return False
    contexts = [item for item in raw_contexts if isinstance(item, Mapping)]
    persisted_turns = {
        turn["turn_id"]: turn
        for turn in final_session.get("turns", [])
        if isinstance(turn, Mapping) and isinstance(turn.get("turn_id"), str)
    }
    facts_by_id = {
        fact["fact_id"]: fact
        for fact in final_session.get("facts", [])
        if isinstance(fact, Mapping) and isinstance(fact.get("fact_id"), str)
    }
    fixture_turn = persisted_turns.get(fixture_turn_id)
    prior_turn = persisted_turns.get(prior_turn_id)
    if fixture_turn is None or prior_turn is None:
        return False
    expected_fixture = _committed_turn_projection(fixture_turn, facts_by_id)
    expected_prior = _committed_turn_projection(prior_turn, facts_by_id)
    fixture_established_ids = {
        item.fact_id
        for item in fixture_result.public_fact_changes
        if item.kind == "established"
    }
    fixture_retired_ids = {
        item.fact_id
        for item in fixture_result.public_fact_changes
        if item.kind == "retired"
    }
    fixture_mechanic_ids = {
        item.mechanic_id for item in fixture_result.public_mechanics
    }
    for projection, context in zip(projections, contexts, strict=True):
        first_player_turn = (
            projection.get("current_player_input_sha256") == input_hashes[0]
        )
        projected_turns = _projected_turns_by_id(context)
        if not _projected_fixture_matches(
                context,
                fixture_turn_id=fixture_turn_id,
                established_fact_ids=fixture_established_ids,
                retired_fact_ids=fixture_retired_ids,
                mechanic_ids=fixture_mechanic_ids,
                require_initial_active_state=first_player_turn,
            ) or projected_turns.get(fixture_turn_id) != expected_fixture:
            return False
        projected_prior = projected_turns.get(prior_turn_id)
        if first_player_turn and projected_prior is not None:
            return False
        if not first_player_turn and projected_prior != expected_prior:
            return False

    final_messages = projections[-1].get("messages")
    final_turn = persisted_turns.get(results[-1].turn_id)
    if final_turn is None:
        return False
    expected_results = {
        item["mechanic_id"]: _canonical_json_sha256(item)
        for item in final_turn.get("mechanics", [])
        if isinstance(item, Mapping) and isinstance(item.get("mechanic_id"), str)
    }
    projected_results = {
        item.get("result_mechanic_id"): item.get("result_sha256")
        for item in final_messages
        if isinstance(item, Mapping) and item.get("role") == "tool"
    } if isinstance(final_messages, list) else {}
    return bool(expected_results) and all(
        projected_results.get(mechanic_id) == result_sha256
        for mechanic_id, result_sha256 in expected_results.items()
    )


def _projected_fixture_matches(
    context: Mapping[str, Any],
    *,
    fixture_turn_id: str,
    established_fact_ids: set[str],
    retired_fact_ids: set[str],
    mechanic_ids: set[str],
    require_initial_active_state: bool,
) -> bool:
    turn = _projected_turns_by_id(context).get(fixture_turn_id)
    active_fact_ids = context.get("active_fact_ids")
    if turn is None or not isinstance(active_fact_ids, list):
        return False
    history_matches = (
        set(turn.get("established_fact_ids", [])) == established_fact_ids
        and set(turn.get("retired_fact_ids", [])) == retired_fact_ids
        and set(turn.get("mechanic_ids", [])) == mechanic_ids
    )
    return history_matches and (
        not require_initial_active_state
        or (
            established_fact_ids <= set(active_fact_ids)
            and retired_fact_ids.isdisjoint(active_fact_ids)
        )
    )


def _projected_turns_by_id(
    context: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    turns = context.get("committed_turns")
    if not isinstance(turns, list):
        return {}
    return {
        item["turn_id"]: item
        for item in turns
        if isinstance(item, Mapping) and isinstance(item.get("turn_id"), str)
    }


def _pending_quality_scores() -> dict[str, None]:
    return {
        "fictional_causality": None,
        "improvisation": None,
        "cross_turn_continuity": None,
        "npc_performance": None,
        "pacing": None,
        "atmosphere": None,
    }


def _investigator_actor_id(session: Mapping[str, Any]) -> str:
    actors = session.get("actors")
    if not isinstance(actors, list):
        raise ValueError("evaluation session actors are unavailable")
    matches = [
        actor["actor_id"]
        for actor in actors
        if isinstance(actor, Mapping)
        and actor.get("role") == "investigator"
        and isinstance(actor.get("actor_id"), str)
    ]
    if len(matches) != 1:
        raise ValueError("evaluation session must contain one investigator")
    return matches[0]


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


def run_deepseek_retry_contract(
    *,
    enabled: bool,
    api_key: str | None,
    session_root: Path,
    client: Any | None = None,
    retry_sleep: Callable[[float], None] | None = None,
) -> ContractRunResult:
    """真实 provider 的 retry 观察契约。

    第一跳在本地模型 seam 注入一个可重试 provider_server_error，并携带
    Retry-After=1s；Harness 按生产 retry_policy 落盘、等待并重试，第二跳
    通过真实 DeepSeek adapter 完成 direct-final 并提交。该契约验证真实
    provider 恢复路径与重试观测证据，而不要求 provider 真的发生故障。
    """

    if not enabled:
        return ContractRunResult(
            status="skipped",
            reason="DeepSeek retry observation contract was not explicitly enabled",
            records=(),
        )
    if api_key is None or not api_key.strip():
        return ContractRunResult(
            status="skipped",
            reason="DEEPSEEK_API_KEY is not set",
            records=(),
        )

    scenario = _SCENARIOS[0]
    profile = deepseek_model_profile(
        model_id="deepseek-v4-flash",
        thinking=False,
        enabled_tools=("make_check",),
        retry_policy={
            "mode": "normal",
            "max_retries": 2,
            "backoff": {
                "initial_delay_ms": 500,
                "max_delay_ms": 10_000,
                "jitter_ratio": 0,
            },
        },
    )
    store = AgenticSessionStore(session_root=session_root)
    created = store.create_session(
        NewSessionRequest(
            investigator_id="investigator_tracker",
            display_name="重试观察调查员",
        )
    )

    sdk_requests: list[Mapping[str, Any]] = []
    retry_state_snapshots: list[dict[str, Any]] = []
    sleeps: list[dict[str, Any]] = []

    def session_writer(path: Path, payload: Any) -> None:
        incomplete = payload.get("incomplete_turn")
        provider_retry = (
            incomplete.get("provider_retry")
            if isinstance(incomplete, Mapping)
            else None
        )
        if isinstance(provider_retry, Mapping):
            last_retry = provider_retry.get("last_retry")
            if last_retry is not None:
                retry_state_snapshots.append(copy.deepcopy(dict(provider_retry)))
        write_json_atomic(path, payload)

    def observed_retry_sleep(seconds: float) -> None:
        started = time.monotonic()
        if retry_sleep is None:
            time.sleep(seconds)
        else:
            retry_sleep(seconds)
        sleeps.append(
            {
                "scheduled_seconds": seconds,
                "observed_seconds": round(time.monotonic() - started, 3),
            }
        )

    recording_model = _EvaluationRecordingModel(
        _InjectRetryableProviderErrorModel(
            DeepSeekGameMasterModel(
                api_key,
                client=client,
                request_evidence_sink=sdk_requests.append,
            )
        ),
        sdk_requests,
        phase="retry-observation",
    )
    harness = AgenticHarness(
        store,
        recording_model,
        model_profile=profile,
        session_writer=session_writer,
        retry_sleep=observed_retry_sleep,
        retry_random=lambda: 0.5,
    )
    result = harness.start_turn(created.game_id, scenario.player_input)
    final_session = store.load_session(created.game_id).session
    request_records = list(recording_model.requests)

    first_attempt = request_records[0] if request_records else {}
    second_attempt = request_records[1] if len(request_records) > 1 else {}
    last_attempt = request_records[-1] if request_records else {}
    first_retry_snapshot = retry_state_snapshots[0] if retry_state_snapshots else {}
    observed = {
        "version": "retry-live-observation-v1",
        "scenario": scenario.version,
        "expected_path": scenario.expected_path,
        "provider": profile["provider"],
        "model_id": profile["model_id"],
        "retry_policy": copy.deepcopy(profile["retry_policy"]),
        "turn_status": result.status,
        "turn_id": result.turn_id,
        "request_attempts": len(request_records),
        "sdk_requests": len(sdk_requests),
        "first_attempt_local_error_category": first_attempt.get("local_error_category"),
        "second_attempt_finish_reason": second_attempt.get("finish_reason"),
        "last_attempt_finish_reason": last_attempt.get("finish_reason"),
        "scheduled_retry_snapshots": retry_state_snapshots,
        "sleeps": sleeps,
        "final_incomplete_turn_is_none": final_session.get("incomplete_turn") is None,
        "committed_turns": len(final_session.get("turns", [])),
        "first_scheduled_retry": {
            "code": first_retry_snapshot.get("last_retry", {}).get("code"),
            "delay_ms": first_retry_snapshot.get("last_retry", {}).get("delay_ms"),
            "status": first_retry_snapshot.get("last_retry", {}).get("status"),
        },
    }
    passed = (
        result.status == "committed"
        and len(request_records) >= 2
        and len(sdk_requests) == len(request_records) - 1
        and observed["first_attempt_local_error_category"] == "provider_server_error"
        and observed["last_attempt_finish_reason"] == "stop"
        and observed["final_incomplete_turn_is_none"] is True
        and len(retry_state_snapshots) >= 1
        and len(sleeps) >= 1
        and observed["first_scheduled_retry"]["code"] == "provider_server_error"
        and observed["first_scheduled_retry"]["delay_ms"] == 1_000
        and observed["first_scheduled_retry"]["status"] == 503
        and observed["committed_turns"] == 1
    )
    return ContractRunResult(
        status="passed" if passed else "failed",
        reason=(
            "real DeepSeek turn committed after one locally injected transient "
            "failure, one observed retry, and zero or more real continuation "
            "requests"
            if passed
            else "retry observation contract failed"
        ),
        records=(observed,),
    )


def _run_recovery_scenario(
    scenario: _RecoveryContractScenario,
    *,
    api_key: str,
    store: AgenticSessionStore,
    client: Any | None,
) -> dict[str, Any]:
    # Recovery contract intentionally disables retries: its purpose is to prove
    # the committed-tool recovery boundary, not provider retry policy.
    profile = deepseek_model_profile(
        model_id="deepseek-v4-flash",
        thinking=scenario.thinking,
        enabled_tools=("make_check",),
        retry_policy={"mode": "normal", "max_retries": 0},
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
    expected_sdk_response_format = (
        None
        if profile["provider"] == "opencode-go"
        else {"type": "json_object"}
    )
    configured = (
        profile["model_id"] == "deepseek-v4-flash"
        and profile["thinking"] is False
        and profile["stream"] is False
        and profile["response_format"] == "json_object"
        and all(
            (
                item["function_tools"] == ["make_check"]
                or (
                    profile["provider"] == "opencode-go"
                    and item["structure_repair_request"] is True
                    and item["function_tools"] == []
                )
            )
            and item["response_format"] == expected_sdk_response_format
            and item["stream"] is False
            for item in requests
        )
    )
    if not configured or result.status != "committed":
        return False
    if scenario.expected_path == "direct_final":
        return len(requests) == 1 and requests[0]["tool_calls"] == []
    tool_requests = [
        request for request in requests if request["tool_calls"]
    ]
    if not tool_requests or len(tool_requests[0]["tool_calls"]) != 1:
        return False
    tool_call = tool_requests[0]["tool_calls"][0]
    tool_call_id = tool_call["tool_call_id"]
    matched = tool_call["tool_name"] == "make_check" and isinstance(
        tool_call_id,
        str,
    ) and any(
        tool_call_id in request["tool_result_ids"] for request in requests
    )
    if not matched:
        return False
    # opencode-go 偶发会在最终答复前重复请求同一工具；协议契约只要求
    # 首次 tool_call_id 得到匹配回放并最终提交，不把模型是否多绕一轮
    # 当成 provider 传输失败。
    if profile["provider"] != "opencode-go" and len(requests) != 2:
        return False
    return (
        requests[-1]["tool_calls"] == []
        and len(result.public_mechanics) == 1
    )


def _sdk_evidence_for_call(
    sdk_requests: list[Mapping[str, Any]],
    previous_count: int,
) -> dict[str, Any]:
    if len(sdk_requests) != previous_count + 1:
        return {
            "model_id": None,
            "messages_sha256": None,
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
    """记录协议 ID 和上下文指纹，不复制事实、叙事或 reasoning 正文。"""

    projection: list[dict[str, Any]] = []
    context: dict[str, Any] | None = None
    current_player_input_sha256: str | None = None
    for message in messages:
        item: dict[str, Any] = {"role": message.get("role")}
        content = message.get("content")
        if message.get("role") == "user" and isinstance(content, str):
            package = _context_package_projection(content)
            if package is not None:
                context = package
            player_input = _projected_player_input(content)
            if player_input is not None:
                current_player_input_sha256 = _text_sha256(player_input)
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
            item.update(_tool_result_projection(content))
        projection.append(item)
    return {
        "messages": projection,
        "context": context,
        "current_player_input_sha256": current_player_input_sha256,
    }


def _context_package_projection(content: str) -> dict[str, Any] | None:
    try:
        package = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(package, Mapping):
        return None
    turns = package.get("COMMITTED_TURNS")
    active_facts = package.get("ACTIVE_FACTS")
    if not isinstance(turns, list) or not isinstance(active_facts, list):
        return None
    projected_turns: list[dict[str, Any]] = []
    for turn in turns:
        if not isinstance(turn, Mapping) or not isinstance(turn.get("turn_id"), str):
            continue
        projected_turns.append(_committed_turn_projection(turn))
    return {
        "committed_turns": projected_turns,
        "active_fact_ids": [
            fact["fact_id"]
            for fact in active_facts
            if isinstance(fact, Mapping)
            and isinstance(fact.get("fact_id"), str)
        ],
    }


def _projected_player_input(content: str) -> str | None:
    prefix = "<PLAYER_INPUT>\n"
    suffix = "\n</PLAYER_INPUT>"
    if not content.startswith(prefix) or not content.endswith(suffix):
        return None
    return content[len(prefix) : -len(suffix)]


def _tool_result_projection(content: object) -> dict[str, Any]:
    if not isinstance(content, str):
        return {
            "result_mechanic_id": None,
            "result_sha256": None,
        }
    try:
        envelope = json.loads(content)
    except json.JSONDecodeError:
        return {
            "result_mechanic_id": None,
            "result_sha256": None,
        }
    if not isinstance(envelope, Mapping):
        return {
            "result_mechanic_id": None,
            "result_sha256": None,
        }
    result = envelope.get("result")
    identifier = result.get("mechanic_id") if isinstance(result, Mapping) else None
    if not isinstance(identifier, str) or not isinstance(result, Mapping):
        return {
            "result_mechanic_id": None,
            "result_sha256": None,
        }
    return {
        "result_mechanic_id": identifier,
        "result_sha256": _canonical_json_sha256(result),
    }


def _committed_turn_projection(
    turn: Mapping[str, Any],
    facts_by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    mechanics = turn.get("mechanics")
    projected_mechanics = [
        {
            "mechanic_id": mechanic["mechanic_id"],
            "result_sha256": _canonical_json_sha256(mechanic),
        }
        for mechanic in mechanics
        if isinstance(mechanic, Mapping)
        and isinstance(mechanic.get("mechanic_id"), str)
    ] if isinstance(mechanics, list) else []
    established_facts = turn.get("established_facts")
    if not isinstance(established_facts, list) and facts_by_id is not None:
        established_facts = [
            facts_by_id[fact_id]
            for fact_id in turn.get("established_fact_ids", [])
            if isinstance(fact_id, str) and fact_id in facts_by_id
        ]
    projected_facts = [
        {
            "fact_id": fact["fact_id"],
            "text_sha256": _text_sha256(fact["text"]),
        }
        for fact in established_facts
        if isinstance(fact, Mapping)
        and isinstance(fact.get("fact_id"), str)
        and isinstance(fact.get("text"), str)
    ] if isinstance(established_facts, list) else []
    retirements = turn.get("retirements")
    projected_retirements = [
        {
            "fact_id": retirement["fact_id"],
            "reason_sha256": _text_sha256(retirement["reason"]),
        }
        for retirement in retirements
        if isinstance(retirement, Mapping)
        and isinstance(retirement.get("fact_id"), str)
        and isinstance(retirement.get("reason"), str)
    ] if isinstance(retirements, list) else []
    return {
        "turn_id": turn["turn_id"],
        "player_input_sha256": (
            _text_sha256(turn["player_input"])
            if isinstance(turn.get("player_input"), str)
            else None
        ),
        "narration_sha256": (
            _text_sha256(turn["narration"])
            if isinstance(turn.get("narration"), str)
            else None
        ),
        "mechanic_ids": [item["mechanic_id"] for item in projected_mechanics],
        "mechanics": projected_mechanics,
        "established_fact_ids": [item["fact_id"] for item in projected_facts],
        "established_facts": projected_facts,
        "retired_fact_ids": [item["fact_id"] for item in projected_retirements],
        "retirements": projected_retirements,
    }


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _structure_repair_count(messages: Sequence[Mapping[str, Any]]) -> int:
    return sum(
        1
        for message in messages
        if message.get("role") == "user"
        and isinstance(message.get("content"), str)
        and "本地校验提示：" in message["content"]
    )


def _canonical_json_sha256(value: object) -> str:
    return _text_sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


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
            function.get("name")
            if isinstance(function, Mapping)
            and function.get("name") in DEFAULT_COC_TOOL_NAMES
            else "unsupported"
        )
        sanitized.append(
            {"tool_call_id": identifier, "tool_name": name}
        )
    return sanitized


def _public_mechanic(mechanic: Any) -> dict[str, Any]:
    if isinstance(mechanic, Mapping):
        details = mechanic.get("details")
        if not isinstance(details, Mapping):
            details = {
                key: copy.deepcopy(mechanic[key])
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
                if key in mechanic
            }
        return {
            "mechanic_id": mechanic["mechanic_id"],
            "kind": mechanic["kind"],
            "actor_id": mechanic["actor_id"],
            "details": copy.deepcopy(dict(details)),
        }
    return {
        "mechanic_id": mechanic.mechanic_id,
        "kind": mechanic.kind,
        "actor_id": mechanic.actor_id,
        "details": mechanic.details_as_json(),
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
    retry_enabled = (
        os.environ.get("MONMUSU_RUN_DEEPSEEK_RETRY_CONTRACT") == "1"
    )
    increment3_enabled = (
        os.environ.get("MONMUSU_RUN_INCREMENT3_EVALUATION") == "1"
    )
    opencode_go_enabled = (
        os.environ.get("MONMUSU_RUN_OPENCODE_GO_CONTRACT") == "1"
    )
    enabled = (
        recovery_enabled
        or retry_enabled
        or increment3_enabled
        or opencode_go_enabled
        or os.environ.get("MONMUSU_RUN_DEEPSEEK_CONTRACT") == "1"
    )
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    opencode_go_api_key = os.environ.get("OPENCODE_GO_API_KEY")
    try:
        with tempfile.TemporaryDirectory(
            prefix="monmusu-agent-deepseek-contract-"
        ) as directory:
            if retry_enabled:
                result = run_deepseek_retry_contract(
                    enabled=enabled,
                    api_key=api_key,
                    session_root=Path(directory) / "sessions",
                )
            elif opencode_go_enabled:
                result = run_opencode_go_contract(
                    enabled=enabled,
                    api_key=opencode_go_api_key,
                    session_root=Path(directory) / "sessions",
                )
            elif increment3_enabled:
                result = run_increment_three_evaluation(
                    enabled=enabled,
                    api_key=api_key,
                    session_root=Path(directory) / "sessions",
                )
            elif recovery_enabled:
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
        print("FAIL: contract runner could not produce evidence")
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
