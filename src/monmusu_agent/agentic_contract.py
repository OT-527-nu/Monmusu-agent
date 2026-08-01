"""显式运行 DeepSeek 协议契约并生成脱敏评估记录。"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping
from uuid import uuid4

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


class _EvaluationRecordingModel:
    """只记录 Evaluation 所需的脱敏 provider 契约字段。"""

    def __init__(
        self,
        delegate: GameMasterModel,
        sdk_requests: list[Mapping[str, Any]],
    ) -> None:
        self.delegate = delegate
        self.sdk_requests = sdk_requests
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
        self.requests.append(record)
        return response


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
            "function_tools": [],
            "response_format": None,
            "stream": None,
        }
    return copy.deepcopy(dict(sdk_requests[-1]))


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

    enabled = os.environ.get("MONMUSU_RUN_DEEPSEEK_CONTRACT") == "1"
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    try:
        with tempfile.TemporaryDirectory(
            prefix="monmusu-agent-deepseek-contract-"
        ) as directory:
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
