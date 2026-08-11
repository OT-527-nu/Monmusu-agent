import json
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from monmusu_agent.agentic_coc import (
    MakeCheckError,
    MakeCheckTool,
    PushCheckTool,
    RandomSource,
    SpendLuckTool,
    ToolExecution,
)
from monmusu_agent.agentic_harness import (
    AgenticHarness,
    AgenticSessionCompleteError,
    AgenticTurnBlockedError,
    AgenticTurnInputError,
    PublicMechanic,
    TurnResult,
)
from monmusu_agent.agentic_model import (
    ModelCallError,
    ModelResponse,
    ScriptedGameMasterModel,
    deepseek_model_profile,
)
from monmusu_agent.agentic_session import (
    AgenticSessionLoadError,
    AgenticSessionSources,
    AgenticSessionStore,
    NewSessionRequest,
)
from monmusu_agent.storage import read_json, write_json_atomic


class ScriptedRandom:
    """按独立 d10 序列提供随机边界，并记录是否被调用。"""

    def __init__(self, values: tuple[int, ...]) -> None:
        self.values = iter(values)
        self.calls: list[tuple[int, int]] = []

    def randint(self, minimum: int, maximum: int) -> int:
        self.calls.append((minimum, maximum))
        return next(self.values)


@dataclass(frozen=True)
class MechanicCaseRun:
    """保存普通单次机械 seam 用例需要观察的结果。"""

    store: AgenticSessionStore
    game_id: str
    model: ScriptedGameMasterModel
    result: TurnResult
    session: Mapping[str, Any]
    random_source: ScriptedRandom


def _lifecycle_tool_definition(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "测试共享工具生命周期。",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["actor_id", "amount", "visibility"],
                "properties": {
                    "actor_id": {"type": "string"},
                    "amount": {"type": "integer", "minimum": 1, "maximum": 10},
                    "visibility": {"type": "string", "enum": ["public", "hidden"]},
                },
            },
        },
    }


class LifecycleTestTool:
    """只用于证明非 make_check 工具共享提交、恢复与角色变化。"""

    result_kind = "lifecycle_test"
    result_amount_field = "amount"

    definition = _lifecycle_tool_definition("lifecycle_test")
    mechanic_kind = result_kind

    def normalize(self, arguments_raw: str) -> dict[str, Any]:
        try:
            value = json.loads(arguments_raw)
        except json.JSONDecodeError as error:
            raise MakeCheckError("invalid_arguments", "测试工具参数不是合法 JSON") from error
        if not isinstance(value, dict) or set(value) != {"actor_id", "amount", "visibility"}:
            raise MakeCheckError("invalid_arguments", "测试工具参数字段无效")
        if (
            not isinstance(value["actor_id"], str)
            or not value["actor_id"].strip()
            or not isinstance(value["amount"], int)
            or isinstance(value["amount"], bool)
            or not 1 <= value["amount"] <= 10
            or value["visibility"] not in {"public", "hidden"}
        ):
            raise MakeCheckError("invalid_arguments", "测试工具参数值无效")
        return dict(value)

    def preflight(
        self,
        arguments: Mapping[str, Any],
        *,
        actors: object,
        mechanics: tuple[Mapping[str, Any], ...],
        current_turn_mechanics: tuple[Mapping[str, Any], ...],
    ) -> object:
        del mechanics, current_turn_mechanics
        if not isinstance(actors, list):
            raise MakeCheckError("actor_data_unavailable", "测试角色卡不可用")
        frozen_actors = json.loads(json.dumps(actors))
        actor = next(
            (
                item
                for item in frozen_actors
                if item["actor_id"] == arguments["actor_id"]
            ),
            None,
        )
        if actor is None:
            raise MakeCheckError("unknown_actor", "测试角色不存在")
        if actor["luck"]["current"] < arguments["amount"]:
            raise MakeCheckError("insufficient_luck", "测试资源不足")
        if actor["role"] == "investigator" and arguments["visibility"] != "public":
            raise MakeCheckError("invalid_visibility", "调查员资源变化必须公开")
        return {"arguments": dict(arguments), "actors": frozen_actors}

    def execute(
        self,
        prepared: object,
        *,
        mechanic_id: str,
        random_source: RandomSource,
        committed_at: str,
    ) -> ToolExecution:
        del random_source
        if not isinstance(prepared, dict):
            raise ValueError("测试冻结输入无效")
        arguments = prepared["arguments"]
        updated = json.loads(json.dumps(prepared["actors"]))
        actor = next((item for item in updated if item["actor_id"] == arguments["actor_id"]), None)
        assert actor is not None
        before = actor["luck"]["current"]
        amount = arguments["amount"]
        actor["luck"]["current"] = before - amount
        return ToolExecution(
            mechanic={
                "mechanic_id": mechanic_id,
                "kind": self.result_kind,
                "actor_id": arguments["actor_id"],
                self.result_amount_field: amount,
                "luck_before": before,
                "luck_after": before - amount,
                "visibility": arguments["visibility"],
                "committed_at": committed_at,
            },
            actors=updated,
        )

    @classmethod
    def validate_result(cls, value: object) -> None:
        expected_fields = {
            "mechanic_id",
            "kind",
            "actor_id",
            cls.result_amount_field,
            "luck_before",
            "luck_after",
            "visibility",
            "committed_at",
        }
        if not isinstance(value, dict) or set(value) != expected_fields:
            raise ValueError("测试 mechanic 格式无效")
        amount = value.get(cls.result_amount_field)
        before = value.get("luck_before")
        after = value.get("luck_after")
        if (
            value.get("kind") != cls.result_kind
            or not isinstance(value.get("mechanic_id"), str)
            or not value["mechanic_id"]
            or not isinstance(value.get("actor_id"), str)
            or not value["actor_id"]
            or not isinstance(value.get("committed_at"), str)
            or not value["committed_at"]
            or not isinstance(amount, int)
            or isinstance(amount, bool)
            or not 1 <= amount <= 10
            or not isinstance(before, int)
            or isinstance(before, bool)
            or not isinstance(after, int)
            or isinstance(after, bool)
            or after != before - amount
            or not 0 <= after <= before <= 99
            or value.get("visibility") not in {"public", "hidden"}
        ):
            raise ValueError("测试 mechanic 格式无效")

    @classmethod
    def validate_result_arguments(
        cls,
        arguments: Mapping[str, Any],
        value: Mapping[str, Any],
        *,
        actors: object,
    ) -> None:
        del actors
        if (
            arguments.get("actor_id") != value.get("actor_id")
            or arguments.get("amount") != value.get(cls.result_amount_field)
            or arguments.get("visibility") != value.get("visibility")
        ):
            raise ValueError("测试 mechanic 与规范参数不一致")

    @classmethod
    def validate_persistence(
        cls,
        value: Mapping[str, Any],
        *,
        actors: object,
        mechanics: tuple[Mapping[str, Any], ...],
    ) -> None:
        cls.validate_result(value)
        if not isinstance(actors, list):
            raise ValueError("测试 mechanic 与冻结角色卡不一致")
        actor = next(
            (
                item
                for item in actors
                if isinstance(item, dict)
                and item.get("actor_id") == value.get("actor_id")
            ),
            None,
        )
        related = [
            mechanic
            for mechanic in mechanics
            if mechanic.get("kind") == cls.result_kind
            and mechanic.get("actor_id") == value.get("actor_id")
        ]
        if (
            not isinstance(actor, dict)
            or not related
            or any(
                previous["luck_after"] != current["luck_before"]
                for previous, current in zip(related, related[1:], strict=False)
            )
            or actor["luck"]["current"] != related[-1]["luck_after"]
        ):
            raise ValueError("测试 mechanic 与冻结角色卡不一致")

    @classmethod
    def public_details(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            cls.result_amount_field: value[cls.result_amount_field],
            "luck_before": value["luck_before"],
            "luck_after": value["luck_after"],
        }


class HistoricalLuckMarkerTool:
    """只为 Push 链测试提交一条既有 Luck 补救记录。"""

    definition = {
        "type": "function",
        "function": {
            "name": "historical_luck_marker",
            "description": "测试既有 Luck 补救链。",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["check_id"],
                "properties": {"check_id": {"type": "string"}},
            },
        },
    }
    mechanic_kind = "luck_spend"

    def normalize(self, arguments_raw: str) -> dict[str, Any]:
        try:
            value = json.loads(arguments_raw)
        except json.JSONDecodeError as error:
            raise MakeCheckError("invalid_arguments", "测试 Luck 参数不是合法 JSON") from error
        if (
            not isinstance(value, dict)
            or set(value) != {"check_id"}
            or not isinstance(value["check_id"], str)
            or not value["check_id"]
        ):
            raise MakeCheckError("invalid_arguments", "测试 Luck 参数无效")
        return {"check_id": value["check_id"]}

    def preflight(
        self,
        arguments: Mapping[str, Any],
        *,
        actors: object,
        mechanics: tuple[Mapping[str, Any], ...],
        current_turn_mechanics: tuple[Mapping[str, Any], ...],
    ) -> object:
        del current_turn_mechanics
        source = next(
            (
                mechanic
                for mechanic in mechanics
                if mechanic.get("mechanic_id") == arguments["check_id"]
                and mechanic.get("kind") == "check"
            ),
            None,
        )
        if source is None or not isinstance(actors, list):
            raise MakeCheckError("invalid_check_id", "测试 Luck 来源无效")
        return {
            "check_id": arguments["check_id"],
            "actor_id": source["actor_id"],
            "actors": json.loads(json.dumps(actors)),
        }

    def execute(
        self,
        prepared: object,
        *,
        mechanic_id: str,
        random_source: RandomSource,
        committed_at: str,
    ) -> ToolExecution:
        del random_source
        if not isinstance(prepared, dict):
            raise ValueError("测试 Luck 冻结输入无效")
        return ToolExecution(
            mechanic={
                "mechanic_id": mechanic_id,
                "kind": "luck_spend",
                "actor_id": prepared["actor_id"],
                "check_id": prepared["check_id"],
                "visibility": "public",
                "committed_at": committed_at,
            },
            actors=prepared["actors"],
        )

    @staticmethod
    def validate_result(value: object) -> None:
        fields = {
            "mechanic_id",
            "kind",
            "actor_id",
            "check_id",
            "visibility",
            "committed_at",
        }
        if (
            not isinstance(value, dict)
            or set(value) != fields
            or value.get("kind") != "luck_spend"
            or value.get("visibility") != "public"
            or any(
                not isinstance(value.get(field), str) or not value[field]
                for field in ("mechanic_id", "actor_id", "check_id", "committed_at")
            )
        ):
            raise ValueError("测试 Luck mechanic 格式无效")

    @staticmethod
    def validate_result_arguments(
        arguments: Mapping[str, Any],
        value: Mapping[str, Any],
        *,
        actors: object,
    ) -> None:
        del actors
        if arguments.get("check_id") != value.get("check_id"):
            raise ValueError("测试 Luck mechanic 与参数不一致")

    @staticmethod
    def validate_persistence(
        value: Mapping[str, Any],
        *,
        actors: object,
        mechanics: tuple[Mapping[str, Any], ...],
    ) -> None:
        HistoricalLuckMarkerTool.validate_result(value)
        if (
            not isinstance(actors, list)
            or not any(actor.get("actor_id") == value.get("actor_id") for actor in actors)
            or not any(
                mechanic.get("mechanic_id") == value.get("check_id")
                and mechanic.get("kind") == "check"
                for mechanic in mechanics
            )
        ):
            raise ValueError("测试 Luck mechanic 持久化无效")

    @staticmethod
    def public_details(value: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"check_id": value["check_id"]}


class MutatingFailureTool(LifecycleTestTool):
    definition = _lifecycle_tool_definition("mutating_failure")

    def execute(self, prepared: object, **kwargs: Any) -> ToolExecution:
        assert isinstance(prepared, dict)
        actors = prepared["actors"]
        actors[0]["luck"]["current"] = 0
        raise MakeCheckError("invalid_arguments", "工具故意失败")


class MalformedResultTool(LifecycleTestTool):
    definition = _lifecycle_tool_definition("malformed_result")

    def execute(self, prepared: object, **kwargs: Any) -> ToolExecution:
        assert isinstance(prepared, dict)
        return ToolExecution(
            mechanic={"mechanic_id": kwargs["mechanic_id"], "kind": "not_allowed"},
            actors=prepared["actors"],
        )


class AlternateLifecycleTestTool(LifecycleTestTool):
    """证明新增 mechanic kind 不需要修改 Session 装载器。"""

    definition = _lifecycle_tool_definition("alternate_lifecycle_test")
    result_kind = "alternate_lifecycle_test"
    result_amount_field = "points_spent"
    mechanic_kind = result_kind


class InconsistentLifecycleTestTool(LifecycleTestTool):
    """返回结构合法但与角色最终资源不一致的测试结果。"""

    definition = _lifecycle_tool_definition("inconsistent_lifecycle_test")
    result_kind = "inconsistent_lifecycle_test"
    mechanic_kind = result_kind

    def execute(self, prepared: object, **kwargs: Any) -> ToolExecution:
        execution = super().execute(prepared, **kwargs)
        mechanic = dict(execution.mechanic)
        mechanic["luck_before"] -= 5
        mechanic["luck_after"] -= 5
        return ToolExecution(mechanic=mechanic, actors=execution.actors)


class MutatingPersistenceValidatorTool(LifecycleTestTool):
    """模拟校验器在返回前篡改收到的可变快照。"""

    definition = _lifecycle_tool_definition("mutating_persistence_validator")
    result_kind = "mutating_persistence_validator"
    mechanic_kind = result_kind

    @classmethod
    def validate_persistence(
        cls,
        value: Mapping[str, Any],
        *,
        actors: object,
        mechanics: tuple[Mapping[str, Any], ...],
    ) -> None:
        super().validate_persistence(value, actors=actors, mechanics=mechanics)
        assert isinstance(value, dict)
        assert isinstance(actors, list)
        value["luck_after"] = 0
        actors[0]["luck"]["current"] = 0


class AgenticHarnessTest(unittest.TestCase):
    @staticmethod
    def _lifecycle_profile() -> dict[str, Any]:
        profile = deepseek_model_profile()
        profile["enabled_tools"] = ["make_check", "lifecycle_test"]
        return profile

    @staticmethod
    def _lifecycle_registry() -> dict[str, Any]:
        return {"make_check": MakeCheckTool(), "lifecycle_test": LifecycleTestTool()}

    def _create_session(
        self,
        root: Path,
        *,
        investigator_id: str = "investigator_tracker",
    ) -> tuple[AgenticSessionStore, str]:
        store = AgenticSessionStore(
            session_root=root / "sessions",
            game_id_factory=lambda: "game_test_0001",
            clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
        )
        created = store.create_session(
            NewSessionRequest(
                investigator_id=investigator_id,
                display_name="林雁",
                honorific="林女士",
                pronouns="她",
                occupation="档案员",
                appearance="短发，穿旧防水外套",
                background_hook="来梦中寻找失踪的弟弟",
                keepsake="一枚裂了边的铜怀表",
            )
        )
        return store, created.game_id

    def _create_session_with_actor_armor(
        self,
        root: Path,
        *,
        actor_id: str,
        armor: int,
    ) -> tuple[AgenticSessionStore, str]:
        defaults = AgenticSessionSources()
        templates = read_json(defaults.actor_templates)
        actor = next(
            candidate
            for candidate in templates["actors"]
            if candidate["actor_id"] == actor_id
        )
        actor["armor"] = armor
        actor_templates = root / "actor_templates.json"
        write_json_atomic(actor_templates, templates)
        store = AgenticSessionStore(
            session_root=root / "sessions",
            sources=AgenticSessionSources(actor_templates=actor_templates),
            game_id_factory=lambda: "game_damage_armor",
            clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
        )
        created = store.create_session(
            NewSessionRequest(
                investigator_id="investigator_tracker",
                display_name="林雁",
            )
        )
        return store, created.game_id

    @staticmethod
    def _response(final: object, *, reasoning: str | None = None) -> ModelResponse:
        content = final if isinstance(final, str) else json.dumps(
            final,
            ensure_ascii=False,
        )
        return ModelResponse(
            assistant_message={
                "role": "assistant",
                "content": content,
                "reasoning_content": reasoning,
                "tool_calls": [],
            },
            finish_reason="stop",
            usage=None,
            latency_ms=10,
        )

    @staticmethod
    def _tool_response(
        arguments: object,
        *,
        tool_call_id: object = "call_001",
        name: object = "make_check",
        reasoning: str | None = None,
        content: str | None = None,
    ) -> ModelResponse:
        arguments_raw = arguments if isinstance(arguments, str) else json.dumps(
            arguments,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return ModelResponse(
            assistant_message={
                "role": "assistant",
                "content": content,
                "reasoning_content": reasoning,
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": arguments_raw},
                    }
                ],
            },
            finish_reason="tool_calls",
            usage=None,
            latency_ms=10,
        )

    @staticmethod
    def _multi_tool_response(*tool_call_ids: str) -> ModelResponse:
        return ModelResponse(
            assistant_message={
                "role": "assistant",
                "content": None,
                "reasoning_content": None,
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": "make_check",
                            "arguments": "{}",
                        },
                    }
                    for tool_call_id in tool_call_ids
                ],
            },
            finish_reason="tool_calls",
            usage=None,
            latency_ms=10,
        )

    @staticmethod
    def _valid_check_arguments() -> dict[str, object]:
        return {
            "actor_id": "investigator_tracker",
            "ability": "spot_hidden",
            "difficulty": "regular",
            "dice_adjustment": {"kind": "none", "count": 0},
            "action": "检查牢门",
            "stakes": "失败会错过痕迹",
            "visibility": "public",
        }

    @staticmethod
    def _final_payload(narration: str = "机械结果已经解释。") -> dict[str, object]:
        return {
            "narration": narration,
            "establish": [],
            "retire": [],
            "session_status": "ongoing",
        }

    @staticmethod
    def _actor(session: Mapping[str, Any], actor_id: str) -> Mapping[str, Any]:
        actors = session["actors"]
        assert isinstance(actors, list)
        return next(actor for actor in actors if actor["actor_id"] == actor_id)

    def _run_damage_case(
        self,
        root: Path,
        *,
        arguments: Mapping[str, object],
        dice: tuple[int, ...],
        mechanic_id: str,
        investigator_id: str = "investigator_tracker",
    ) -> MechanicCaseRun:
        store, game_id = self._create_session(
            root,
            investigator_id=investigator_id,
        )
        random_source = ScriptedRandom(dice)
        model = ScriptedGameMasterModel(
            [
                self._tool_response(dict(arguments), name="deal_damage"),
                self._response(self._final_payload()),
            ]
        )
        result = AgenticHarness(
            store,
            model,
            mechanic_id_factory=lambda: mechanic_id,
            random_source=random_source,
        ).start_turn(game_id, "结算已经成立的伤害。")
        self.assertEqual(result.status, "committed")
        return MechanicCaseRun(
            store=store,
            game_id=game_id,
            model=model,
            result=result,
            session=store.load_session(game_id).session,
            random_source=random_source,
        )

    def _run_sanity_case(
        self,
        root: Path,
        *,
        arguments: Mapping[str, object],
        dice: tuple[int, ...],
        mechanic_id: str,
        investigator_id: str = "investigator_tracker",
        narration: str = "理智结果已经解释。",
    ) -> MechanicCaseRun:
        store, game_id = self._create_session(
            root,
            investigator_id=investigator_id,
        )
        random_source = ScriptedRandom(dice)
        model = ScriptedGameMasterModel(
            [
                self._tool_response(
                    dict(arguments),
                    name="make_sanity_check",
                ),
                self._response(self._final_payload(narration)),
            ]
        )
        result = AgenticHarness(
            store,
            model,
            mechanic_id_factory=lambda: mechanic_id,
            random_source=random_source,
        ).start_turn(game_id, "结算已经成立的恐怖来源。")
        self.assertEqual(result.status, "committed")
        return MechanicCaseRun(
            store=store,
            game_id=game_id,
            model=model,
            result=result,
            session=store.load_session(game_id).session,
            random_source=random_source,
        )

    def _base_and_luck_responses(
        self,
        *,
        base_arguments: dict[str, object] | None = None,
        check_id: str = "mechanic_base",
        points: int = 3,
        narration: str = "机械结果已经解释。",
    ) -> list[ModelResponse]:
        final = self._final_payload(narration)
        return [
            self._tool_response(base_arguments or self._valid_check_arguments()),
            self._response(final),
            self._tool_response(
                {"check_id": check_id, "points": points},
                name="spend_luck",
            ),
            self._response(final),
        ]

    def _assert_remediation_rejected_after_base(
        self,
        *,
        base_arguments: dict[str, object] | None,
        base_dice: tuple[int, ...],
        tool_name: str,
        tool_arguments: dict[str, object],
        player_input: str,
        expected_code: str,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            responses: list[ModelResponse | ModelCallError] = []
            if base_arguments is not None:
                responses.extend(
                    [
                        self._tool_response(base_arguments),
                        self._response(self._final_payload("基础检定已解释。")),
                    ]
                )
            responses.extend(
                [
                    self._tool_response(tool_arguments, name=tool_name),
                    ModelCallError(
                        "request_timeout",
                        "stop after remediation rejection",
                        retryable=True,
                    ),
                ]
            )
            allocated: list[str] = []

            def next_mechanic_id() -> str:
                value = (
                    "mechanic_base"
                    if base_arguments is not None and not allocated
                    else "mechanic_unexpected"
                )
                allocated.append(value)
                return value

            random_source = ScriptedRandom(base_dice)
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(responses),
                mechanic_id_factory=next_mechanic_id,
                random_source=random_source,
            )
            if base_arguments is not None:
                self.assertEqual(
                    harness.start_turn(game_id, "我先执行基础检定。").status,
                    "committed",
                )
            allocated_before = list(allocated)
            random_calls_before = list(random_source.calls)

            result = harness.start_turn(game_id, player_input)

            self.assertEqual(result.error_code, "request_timeout")
            self.assertEqual(allocated, allocated_before)
            self.assertEqual(random_source.calls, random_calls_before)
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(incomplete["mechanics"], [])
            self.assertEqual(
                incomplete["tool_interactions"][0]["error"]["code"],
                expected_code,
            )

    def _assert_push_rejected_after_base(
        self,
        *,
        base_arguments: dict[str, object] | None,
        base_dice: tuple[int, ...],
        check_id: str,
        expected_code: str,
    ) -> None:
        self._assert_remediation_rejected_after_base(
            base_arguments=base_arguments,
            base_dice=base_dice,
            tool_name="push_check",
            tool_arguments={
                "check_id": check_id,
                "new_approach": "改从门轴侧面拆卸",
                "failure_stakes": "门轴断裂并夹伤手掌",
            },
            player_input="我明确选择换一种做法孤注一掷。",
            expected_code=expected_code,
        )

    def _assert_luck_rejected_after_base(
        self,
        *,
        base_arguments: dict[str, object] | None,
        base_dice: tuple[int, ...],
        check_id: str,
        points: int,
        expected_code: str,
    ) -> None:
        self._assert_remediation_rejected_after_base(
            base_arguments=base_arguments,
            base_dice=base_dice,
            tool_name="spend_luck",
            tool_arguments={"check_id": check_id, "points": points},
            player_input="我明确选择花费幸运。",
            expected_code=expected_code,
        )

    def test_non_check_tool_commits_state_then_replays_exactly_once_after_restart(self) -> None:
        """非检定工具的角色变化先提交，重建进程恢复时按调用 ID 幂等重放。"""

        arguments = {
            "actor_id": "investigator_tracker",
            "amount": 3,
            "visibility": "public",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, game_id = self._create_session(root)
            first_model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        '{"visibility":"public","amount":3,"actor_id":"investigator_tracker"}',
                        name="lifecycle_test",
                    ),
                    ModelCallError("request_timeout", "timeout", retryable=True),
                ]
            )
            first = AgenticHarness(
                store,
                first_model,
                model_profile=self._lifecycle_profile(),
                tool_registry=self._lifecycle_registry(),
                mechanic_id_factory=lambda: "mechanic_lifecycle_001",
            ).start_turn(game_id, "我确认测试资源变化。")

            self.assertEqual(first.status, "interrupted")
            self.assertEqual(first.public_mechanics[0].kind, "lifecycle_test")
            interrupted = store.load_session(game_id).session
            self.assertEqual(interrupted["actors"][0]["luck"]["current"], 52)
            self.assertEqual(len(interrupted["incomplete_turn"]["mechanics"]), 1)

            resumed_model = ScriptedGameMasterModel(
                [
                    self._tool_response(arguments, name="lifecycle_test"),
                    self._response(
                        {
                            "narration": "已沿用此前提交的测试结果。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            rebuilt_store = AgenticSessionStore(session_root=root / "sessions")
            resumed = AgenticHarness(
                rebuilt_store,
                resumed_model,
                model_profile=self._lifecycle_profile(),
                tool_registry=self._lifecycle_registry(),
                mechanic_id_factory=lambda: "mechanic_must_not_allocate",
            ).resume_turn(game_id, first.turn_id)

            self.assertEqual(resumed.status, "committed")
            committed = rebuilt_store.load_session(game_id).session
            self.assertEqual(committed["actors"][0]["luck"]["current"], 52)
            self.assertEqual(len(committed["turns"][0]["mechanics"]), 1)
            self.assertEqual(committed["turns"][0]["mechanics"][0]["mechanic_id"], "mechanic_lifecycle_001")

    def test_all_public_tools_use_one_public_mechanic_projection(self) -> None:
        """不同工具的公开结果都通过同一个 PublicMechanic 外壳交付。"""

        arguments = {
            "actor_id": "investigator_tracker",
            "amount": 3,
            "visibility": "public",
        }
        with tempfile.TemporaryDirectory() as temporary:
            store, game_id = self._create_session(Path(temporary))
            result = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments, name="lifecycle_test"),
                        ModelCallError("request_timeout", "stop", retryable=True),
                    ]
                ),
                model_profile=self._lifecycle_profile(),
                tool_registry=self._lifecycle_registry(),
                mechanic_id_factory=lambda: "mechanic_public_projection",
            ).start_turn(game_id, "我确认资源变化。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(len(result.public_mechanics), 1)
            public = result.public_mechanics[0]
            self.assertIs(type(public), PublicMechanic)
            self.assertEqual(
                public,
                PublicMechanic(
                    mechanic_id="mechanic_public_projection",
                    kind="lifecycle_test",
                    actor_id="investigator_tracker",
                    details={
                        "amount": 3,
                        "luck_before": 55,
                        "luck_after": 52,
                    },
                ),
            )
            self.assertEqual(public.kind, "lifecycle_test")
            self.assertEqual(public.details["amount"], 3)

    def test_public_mechanic_details_are_deeply_immutable(self) -> None:
        """玩家投影中的嵌套 JSON 值也不能在发布后被调用者改写。"""

        arguments = {
            "actor_id": "investigator_tracker",
            "ability": "spot_hidden",
            "difficulty": "regular",
            "dice_adjustment": {"kind": "none", "count": 0},
            "action": "检查牢门铰链",
            "stakes": "失败会错过新鲜刮痕",
            "visibility": "public",
        }
        with tempfile.TemporaryDirectory() as temporary:
            store, game_id = self._create_session(Path(temporary))
            result = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments),
                        ModelCallError("request_timeout", "stop", retryable=True),
                    ]
                ),
                mechanic_id_factory=lambda: "mechanic_immutable_projection",
                random_source=ScriptedRandom((3, 4)),
            ).start_turn(game_id, "我检查牢门铰链。")

            public = result.public_mechanics[0]
            adjustment = public.details["dice_adjustment"]
            self.assertIsInstance(adjustment, Mapping)
            with self.assertRaises(TypeError):
                adjustment["count"] = 2

    def test_public_mechanic_constructor_deeply_freezes_details(self) -> None:
        """直接构造公开投影也不能绕过嵌套对象与数组的只读边界。"""

        public = PublicMechanic(
            mechanic_id="mechanic_direct_projection",
            kind="test",
            actor_id="investigator_tracker",
            details={
                "nested": {"value": 1},
                "items": [{"value": 2}],
            },
        )

        with self.assertRaises(TypeError):
            public.details["nested"]["value"] = 9
        items = public.details["items"]
        self.assertIsInstance(items, tuple)
        with self.assertRaises(TypeError):
            items[0]["value"] = 9

    def test_resume_uses_frozen_profile_and_registered_tool_subset(self) -> None:
        """重建 Harness 时沿用未完成回合冻结的工具 profile。"""

        arguments = {
            "actor_id": "investigator_tracker",
            "amount": 3,
            "visibility": "public",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, game_id = self._create_session(root)
            first = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments, name="lifecycle_test"),
                        ModelCallError("request_timeout", "stop", retryable=True),
                    ]
                ),
                model_profile=self._lifecycle_profile(),
                tool_registry=self._lifecycle_registry(),
                mechanic_id_factory=lambda: "mechanic_frozen_profile",
            ).start_turn(game_id, "我确认资源变化。")
            self.assertEqual(first.status, "interrupted")

            rebuilt_store = AgenticSessionStore(session_root=root / "sessions")
            resumed_model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "沿用冻结 profile 完成回合。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            default_profile = deepseek_model_profile(
                model_id="deepseek-v4-pro",
                enabled_tools=("make_check",),
            )
            resumed = AgenticHarness(
                rebuilt_store,
                resumed_model,
                model_profile=default_profile,
                tool_registry=self._lifecycle_registry(),
                mechanic_id_factory=lambda: "mechanic_must_not_allocate",
            ).resume_turn(game_id, first.turn_id)

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(rebuilt_store.load_session(game_id).session["actors"][0]["luck"]["current"], 52)
            self.assertEqual(resumed_model.requests[0].model_profile["enabled_tools"], ["make_check", "lifecycle_test"])

    def test_registered_tool_validates_new_mechanic_kind_after_restart(self) -> None:
        """注册工具拥有新 mechanic kind 的恢复校验，不要求 Session 新增分支。"""

        profile = deepseek_model_profile()
        profile["enabled_tools"] = ["alternate_lifecycle_test"]
        registry = {"alternate_lifecycle_test": AlternateLifecycleTestTool()}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, game_id = self._create_session(root)
            first = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            {
                                "actor_id": "investigator_tracker",
                                "amount": 3,
                                "visibility": "public",
                            },
                            name="alternate_lifecycle_test",
                        ),
                        ModelCallError("request_timeout", "timeout", retryable=True),
                    ]
                ),
                model_profile=profile,
                tool_registry=registry,
                mechanic_id_factory=lambda: "mechanic_alternate_001",
            ).start_turn(game_id, "我确认另一类测试资源变化。")

            self.assertEqual(first.status, "interrupted")
            rebuilt_store = AgenticSessionStore(session_root=root / "sessions")
            resumed = AgenticHarness(
                rebuilt_store,
                ScriptedGameMasterModel(
                    [
                        self._response(
                            {
                                "narration": "另一类已提交结果在恢复后仍然有效。",
                                "establish": [],
                                "retire": [],
                                "session_status": "ongoing",
                            }
                        )
                    ]
                ),
                model_profile=profile,
                tool_registry=registry,
            ).resume_turn(game_id, first.turn_id)

            self.assertEqual(resumed.status, "committed")
            committed = rebuilt_store.load_session(game_id).session
            self.assertEqual(committed["actors"][0]["luck"]["current"], 52)
            self.assertEqual(
                committed["turns"][0]["mechanics"][0]["kind"],
                "alternate_lifecycle_test",
            )

    def test_tool_persistence_mismatch_is_rejected_before_commit(self) -> None:
        """结构合法但脱离最终角色值的机械不能进入会话聚合。"""

        profile = deepseek_model_profile()
        profile["enabled_tools"] = ["inconsistent_lifecycle_test"]
        registry = {"inconsistent_lifecycle_test": InconsistentLifecycleTestTool()}
        with tempfile.TemporaryDirectory() as temporary:
            store, game_id = self._create_session(Path(temporary))
            result = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            {
                                "actor_id": "investigator_tracker",
                                "amount": 3,
                                "visibility": "public",
                            },
                            name="inconsistent_lifecycle_test",
                        ),
                        ModelCallError("request_timeout", "timeout", retryable=True),
                    ]
                ),
                model_profile=profile,
                tool_registry=registry,
            ).start_turn(game_id, "尝试提交与角色资源不一致的机械。")

            self.assertEqual(result.status, "interrupted")
            session = store.load_session(game_id).session
            incomplete = session["incomplete_turn"]
            self.assertEqual(session["actors"][0]["luck"]["current"], 55)
            self.assertEqual(incomplete["mechanics"], [])
            self.assertEqual(
                incomplete["tool_interactions"][0]["error"]["code"],
                "tool_execution_error",
            )

    def test_persistence_validator_cannot_mutate_committed_state(self) -> None:
        """校验器只能观察机械和角色快照，不能改写待提交对象。"""

        profile = deepseek_model_profile()
        profile["enabled_tools"] = ["mutating_persistence_validator"]
        registry = {
            "mutating_persistence_validator": MutatingPersistenceValidatorTool()
        }
        with tempfile.TemporaryDirectory() as temporary:
            store, game_id = self._create_session(Path(temporary))
            result = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            {
                                "actor_id": "investigator_tracker",
                                "amount": 3,
                                "visibility": "public",
                            },
                            name="mutating_persistence_validator",
                        ),
                        ModelCallError("request_timeout", "timeout", retryable=True),
                    ]
                ),
                model_profile=profile,
                tool_registry=registry,
            ).start_turn(game_id, "验证校验器不能改写待提交角色。")

            self.assertEqual(result.status, "interrupted")
            session = store.load_session(game_id).session
            self.assertEqual(session["actors"][0]["luck"]["current"], 52)
            self.assertEqual(
                session["incomplete_turn"]["mechanics"][0]["luck_after"],
                52,
            )

    def test_non_check_invalid_arguments_commit_error_without_state_change(self) -> None:
        """非检定参数错误保存 raw/error，但不分配机械或改变角色。"""

        with tempfile.TemporaryDirectory() as temporary:
            store, game_id = self._create_session(Path(temporary))
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        {"actor_id": "investigator_tracker", "amount": 0, "visibility": "public"},
                        name="lifecycle_test",
                    ),
                    ModelCallError("request_timeout", "timeout", retryable=True),
                ]
            )
            result = AgenticHarness(
                store,
                model,
                model_profile=self._lifecycle_profile(),
                tool_registry=self._lifecycle_registry(),
                mechanic_id_factory=lambda: self.fail("invalid call allocated mechanic ID"),
            ).start_turn(game_id, "我提交无效测试调用。")

            self.assertEqual(result.status, "interrupted")
            session = store.load_session(game_id).session
            interaction = session["incomplete_turn"]["tool_interactions"][0]
            self.assertEqual(interaction["error"]["code"], "invalid_arguments")
            self.assertIsNone(interaction["arguments"])
            self.assertEqual(session["actors"][0]["luck"]["current"], 55)

    def test_non_check_preflight_rejects_before_id_or_rng(self) -> None:
        """非检定领域拒绝与 make_check 一样发生在 Harness 权威分配前。"""

        with tempfile.TemporaryDirectory() as temporary:
            store, game_id = self._create_session(Path(temporary))
            random_source = ScriptedRandom(())
            result = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            {
                                "actor_id": "npc_missing",
                                "amount": 3,
                                "visibility": "public",
                            },
                            name="lifecycle_test",
                        ),
                        ModelCallError("request_timeout", "stop", retryable=True),
                    ]
                ),
                model_profile=self._lifecycle_profile(),
                tool_registry=self._lifecycle_registry(),
                mechanic_id_factory=lambda: self.fail(
                    "preflight rejection allocated mechanic ID"
                ),
                random_source=random_source,
            ).start_turn(game_id, "我尝试让不存在的角色消耗资源。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(random_source.calls, [])
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(incomplete["mechanics"], [])
            interaction = incomplete["tool_interactions"][0]
            self.assertEqual(interaction["arguments"]["actor_id"], "npc_missing")
            self.assertEqual(interaction["error"]["code"], "unknown_actor")

    def test_investigator_resource_change_rejects_hidden_visibility(self) -> None:
        """调查员幸运变化不能借由工具 visibility 隐藏。"""

        with tempfile.TemporaryDirectory() as temporary:
            store, game_id = self._create_session(Path(temporary))
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        {"actor_id": "investigator_tracker", "amount": 3, "visibility": "hidden"},
                        name="lifecycle_test",
                    ),
                    ModelCallError("request_timeout", "timeout", retryable=True),
                ]
            )
            result = AgenticHarness(
                store,
                model,
                model_profile=self._lifecycle_profile(),
                tool_registry=self._lifecycle_registry(),
            ).start_turn(game_id, "我确认测试资源变化，但不应隐藏。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "request_timeout")
            session = store.load_session(game_id).session
            self.assertEqual(session["actors"][0]["luck"]["current"], 55)
            self.assertEqual(session["incomplete_turn"]["mechanics"], [])
            self.assertEqual(
                session["incomplete_turn"]["tool_interactions"][0]["error"]["code"],
                "invalid_visibility",
            )

    def test_profile_can_select_make_check_subset_from_registered_tools(self) -> None:
        """扩大的注册目录不破坏旧的只启用 make_check profile。"""

        with tempfile.TemporaryDirectory() as temporary:
            store, game_id = self._create_session(Path(temporary))
            profile = deepseek_model_profile(enabled_tools=("make_check",))
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "只提供默认检定。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            result = AgenticHarness(
                store,
                model,
                model_profile=profile,
                tool_registry=self._lifecycle_registry(),
            ).start_turn(game_id, "我观察周围。")

            self.assertEqual(result.status, "committed")
            self.assertEqual(
                [tool["function"]["name"] for tool in model.requests[0].tools],
                ["make_check"],
            )

    def test_default_profile_exposes_five_registered_coc_tools(self) -> None:
        """新回合默认一次暴露 Increment 3 的五个规范工具名。"""

        with tempfile.TemporaryDirectory() as temporary:
            store, game_id = self._create_session(Path(temporary))
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "无需机械即可继续。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            result = AgenticHarness(store, model).start_turn(
                game_id,
                "我先观察周围。",
            )

            expected = [
                "make_check",
                "push_check",
                "spend_luck",
                "deal_damage",
                "make_sanity_check",
            ]
            self.assertEqual(result.status, "committed")
            self.assertEqual(model.requests[0].model_profile["enabled_tools"], expected)
            self.assertEqual(
                [tool["function"]["name"] for tool in model.requests[0].tools],
                expected,
            )

    def test_default_tools_reject_invalid_schema_before_preflight(
        self,
    ) -> None:
        """所有工具都必须在领域 preflight 前严格拒绝公开 schema 反例。"""

        cases = (
            (
                "push_check",
                {"check_id": "mechanic_check_0001", "new_approach": "卸下门轴"},
            ),
            (
                "spend_luck",
                {"check_id": "mechanic_check_0001", "points": True},
            ),
            (
                "spend_luck",
                {"check_id": "mechanic_check_0001", "points": 0},
            ),
            (
                "spend_luck",
                {"check_id": "mechanic_check_0001", "points": -1},
            ),
            (
                "spend_luck",
                {"check_id": "mechanic_check_0001", "points": 3.0},
            ),
            (
                "spend_luck",
                {
                    "check_id": "mechanic_check_0001",
                    "points": 3,
                    "visibility": "public",
                },
            ),
            (
                "deal_damage",
                {
                    "actor_id": "investigator_tracker",
                    "damage_expression": "1d6",
                    "cause": "从石阶跌落",
                    "armor_applies": 1,
                    "visibility": "public",
                },
            ),
            (
                "make_sanity_check",
                {
                    "actor_id": "investigator_tracker",
                    "source": "倒影中的苍白侧脸",
                    "success_loss": "0",
                    "failure_loss": "1d6",
                    "visibility": "secret",
                },
            ),
        )
        for tool_name, arguments in cases:
            with self.subTest(
                tool_name=tool_name,
                arguments=arguments,
            ), tempfile.TemporaryDirectory() as temporary:
                store, game_id = self._create_session(Path(temporary))
                random_source = ScriptedRandom(())
                result = AgenticHarness(
                    store,
                    ScriptedGameMasterModel(
                        [
                            self._tool_response(arguments, name=tool_name),
                            ModelCallError("request_timeout", "stop", retryable=True),
                        ]
                    ),
                    mechanic_id_factory=lambda: self.fail(
                        "schema rejection allocated mechanic ID"
                    ),
                    random_source=random_source,
                ).start_turn(game_id, "我提交一项无效机械调用。")

                self.assertEqual(result.error_code, "request_timeout")
                self.assertEqual(random_source.calls, [])
                incomplete = store.load_session(game_id).session["incomplete_turn"]
                interaction = incomplete["tool_interactions"][0]
                self.assertIsNone(interaction["arguments"])
                self.assertEqual(interaction["error"]["code"], "invalid_arguments")
                self.assertEqual(incomplete["mechanics"], [])

    def test_failed_tool_cannot_persist_in_place_actor_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store, game_id = self._create_session(Path(temporary))
            profile = deepseek_model_profile()
            profile["enabled_tools"] = ["mutating_failure"]
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        {"actor_id": "investigator_tracker", "amount": 1, "visibility": "public"},
                        name="mutating_failure",
                    ),
                    ModelCallError("request_timeout", "timeout", retryable=True),
                ]
            )
            result = AgenticHarness(
                store,
                model,
                model_profile=profile,
                tool_registry={"mutating_failure": MutatingFailureTool()},
            ).start_turn(game_id, "触发失败工具。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(store.load_session(game_id).session["actors"][0]["luck"]["current"], 55)

    def test_malformed_tool_result_is_rejected_before_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store, game_id = self._create_session(Path(temporary))
            profile = deepseek_model_profile()
            profile["enabled_tools"] = ["malformed_result"]
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        {"actor_id": "investigator_tracker", "amount": 1, "visibility": "public"},
                        name="malformed_result",
                    ),
                    ModelCallError("request_timeout", "timeout", retryable=True),
                ]
            )
            result = AgenticHarness(
                store,
                model,
                model_profile=profile,
                tool_registry={"malformed_result": MalformedResultTool()},
            ).start_turn(game_id, "触发坏结果工具。")

            self.assertEqual(result.status, "interrupted")
            session = store.load_session(game_id).session
            self.assertEqual(session["incomplete_turn"]["mechanics"], [])
            self.assertEqual(
                session["incomplete_turn"]["tool_interactions"][0]["error"]["code"],
                "tool_execution_error",
            )

    @staticmethod
    def _tool_message_count(messages: object) -> int:
        if not isinstance(messages, (list, tuple)):
            return 0
        return sum(
            1
            for message in messages
            if isinstance(message, dict) and message.get("role") == "tool"
        )

    def test_make_check_commits_before_same_gm_final(self) -> None:
        """公开检定先形成可信机械，再以匹配协议 ID 返回同一个 GM。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            arguments = {
                "actor_id": "investigator_tracker",
                "ability": "spot_hidden",
                "difficulty": "regular",
                "dice_adjustment": {"kind": "none", "count": 0},
                "action": "检查牢门铰链附近的刮痕",
                "stakes": "失败会错过守卫留下的关键痕迹",
                "visibility": "public",
            }
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(arguments),
                    self._response(
                        {
                            "narration": "你从铰链旁发现了新鲜的工具刮痕。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                    self._response(
                        {
                            "narration": "那道刮痕仍清晰地留在铰链旁。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            random_source = ScriptedRandom((3, 4))
            turn_ids = iter(("turn_0001", "turn_0002"))
            harness = AgenticHarness(
                store,
                model,
                model_profile=deepseek_model_profile(enabled_tools=("make_check",)),
                turn_id_factory=lambda: next(turn_ids),
                mechanic_id_factory=lambda: "mechanic_0001",
                random_source=random_source,
                clock=lambda: datetime(2026, 7, 28, 0, 1, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我仔细查看牢门的铰链。")

            expected_mechanic = PublicMechanic(
                mechanic_id="mechanic_0001",
                kind="check",
                actor_id="investigator_tracker",
                details={
                    "ability": "spot_hidden",
                    "ability_value": 70,
                    "difficulty": "regular",
                    "target": 70,
                    "dice_adjustment": {"kind": "none", "count": 0},
                    "roll": 43,
                    "success_level": "regular_success",
                    "action": "检查牢门铰链附近的刮痕",
                    "stakes": "失败会错过守卫留下的关键痕迹",
                },
            )
            self.assertEqual(result.status, "committed")
            self.assertEqual(result.public_mechanics, (expected_mechanic,))
            self.assertEqual(random_source.calls, [(0, 9), (0, 9)])
            self.assertEqual(len(model.requests), 2)
            self.assertEqual(
                [tool["function"]["name"] for tool in model.requests[0].tools],
                ["make_check"],
            )
            tool_parameters = model.requests[0].tools[0]["function"]["parameters"]
            self.assertEqual(
                set(tool_parameters["properties"]),
                {
                    "actor_id",
                    "ability",
                    "difficulty",
                    "dice_adjustment",
                    "action",
                    "stakes",
                    "visibility",
                },
            )
            self.assertEqual(
                model.requests[0].model_profile["enabled_tools"],
                ["make_check"],
            )
            initial_package = json.loads(model.requests[0].messages[1]["content"])
            self.assertEqual(
                [
                    tool["function"]["name"]
                    for tool in initial_package["AVAILABLE_TOOLS"]
                ],
                ["make_check"],
            )
            self.assertEqual(model.requests[1].messages[-2]["role"], "assistant")
            tool_message = model.requests[1].messages[-1]
            self.assertEqual(tool_message["role"], "tool")
            self.assertEqual(tool_message["tool_call_id"], "call_001")
            tool_result = json.loads(tool_message["content"])
            self.assertTrue(tool_result["ok"])
            self.assertEqual(tool_result["result"]["roll"], 43)
            self.assertEqual(tool_result["result"]["target"], 70)

            session = store.load_session(game_id).session
            self.assertIsNone(session["incomplete_turn"])
            mechanic = session["turns"][0]["mechanics"][0]
            self.assertEqual(mechanic["mechanic_id"], "mechanic_0001")
            self.assertEqual(mechanic["roll"], 43)
            self.assertEqual(mechanic["success_level"], "regular_success")

            second = harness.start_turn(game_id, "我记住这道刮痕，继续观察。")

            self.assertEqual(second.status, "committed")
            second_package = json.loads(model.requests[2].messages[1]["content"])
            historical_mechanic = second_package["COMMITTED_TURNS"][0][
                "mechanics"
            ][0]
            self.assertEqual(historical_mechanic["mechanic_id"], "mechanic_0001")
            self.assertEqual(historical_mechanic["roll"], 43)

    def test_direct_final_commits_turn_and_public_projection(self) -> None:
        """合法 final 同时提交回合与正典，隐藏事实不进入玩家投影。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            response = ModelResponse(
                assistant_message={
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "narration": "排水沟后传来空洞的水声，墙内确实另有空间。",
                            "establish": [
                                {
                                    "visibility": "public",
                                    "text": "牢房后墙的排水沟与旧蓄水池相通。",
                                },
                                {
                                    "visibility": "hidden",
                                    "text": "蓄水池里潜伏着一名拾骨者。",
                                },
                            ],
                            "retire": [],
                            "session_status": "ongoing",
                        },
                        ensure_ascii=False,
                    ),
                    "reasoning_content": "不得进入玩家记录的隐藏推理",
                    "tool_calls": [],
                },
                finish_reason="stop",
                usage={"prompt_tokens": 100, "completion_tokens": 40},
                latency_ms=15,
            )
            model = ScriptedGameMasterModel([response])
            fact_ids = iter(("fact_1001", "fact_1002"))
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_0001",
                fact_id_factory=lambda: next(fact_ids),
                clock=lambda: datetime(2026, 7, 27, 0, 1, tzinfo=timezone.utc),
            )

            result = harness.start_turn(
                game_id,
                "我拆下水槽的铜管，敲探牢房后墙的排水沟。",
            )

            self.assertEqual(result.status, "committed")
            self.assertEqual(result.turn_id, "turn_0001")
            self.assertEqual(
                result.narration,
                "排水沟后传来空洞的水声，墙内确实另有空间。",
            )
            self.assertEqual(
                [change.text for change in result.public_fact_changes],
                ["牢房后墙的排水沟与旧蓄水池相通。"],
            )
            self.assertIsNone(result.error_code)

            loaded = store.load_session(game_id)
            self.assertIsNone(loaded.session["incomplete_turn"])
            self.assertEqual(loaded.session["session_status"], "ongoing")
            self.assertEqual(
                loaded.session["turns"],
                [
                    {
                        "turn_id": "turn_0001",
                        "player_input": "我拆下水槽的铜管，敲探牢房后墙的排水沟。",
                        "mechanics": [],
                        "narration": "排水沟后传来空洞的水声，墙内确实另有空间。",
                        "established_fact_ids": ["fact_1001", "fact_1002"],
                        "retirements": [],
                        "session_status": "ongoing",
                        "committed_at": "2026-07-27T00:01:00Z",
                    }
                ],
            )
            committed_facts = loaded.session["facts"][-2:]
            self.assertEqual(
                [fact["fact_id"] for fact in committed_facts],
                ["fact_1001", "fact_1002"],
            )
            self.assertEqual(
                [fact["visibility"] for fact in committed_facts],
                ["public", "hidden"],
            )
            self.assertTrue(
                all(
                    fact["established_turn_id"] == "turn_0001"
                    and fact["origin"] == {"kind": "gm_turn", "source_ref": None}
                    for fact in committed_facts
                )
            )
            persisted_text = (loaded.session_directory / "session.json").read_text(
                encoding="utf-8"
            )
            lifecycle = harness.get_session_state(game_id)
            self.assertNotIn("不得进入玩家记录的隐藏推理", persisted_text)
            self.assertNotIn("蓄水池里潜伏着一名拾骨者。", repr(lifecycle))

    def test_model_profile_rejects_unknown_secret_before_turn_allocation(self) -> None:
        """provider 配置只冻结已知非秘密字段，凭据不能触及存档。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            loaded = store.load_session(game_id)
            before = read_json(loaded.session_directory / "session.json")
            allocated_turn_ids: list[str] = []
            model = ScriptedGameMasterModel([])

            def forbidden_turn_id() -> str:
                allocated_turn_ids.append("turn_forbidden")
                return "turn_forbidden"

            with self.assertRaisesRegex(AgenticTurnInputError, "model_profile"):
                AgenticHarness(
                    store,
                    model,
                    model_profile={
                        "provider": "deepseek",
                        "model_id": "deepseek-v4-flash",
                        "thinking": False,
                        "stream": False,
                        "response_format": "json_object",
                        "temperature": None,
                        "top_p": None,
                        "max_tokens": 4096,
                        "prompt_revision": "gm-capability-charter-agentic-mvp-2",
                        "tool_schema_version": "coc-tools-agentic-mvp-1",
                        "enabled_tools": [],
                        "api_key": "must-not-persist",
                    },
                    turn_id_factory=forbidden_turn_id,
                )

            after = read_json(loaded.session_directory / "session.json")
            self.assertEqual(after, before)
            self.assertEqual(allocated_turn_ids, [])
            self.assertEqual(model.requests, [])
            self.assertNotIn("must-not-persist", json.dumps(after))

    def test_system_message_uses_authoritative_capability_charter(self) -> None:
        """运行时 System 正文与权威文档中的章程代码块保持单一来源。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "你仍站在牢门内侧。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_0001",
                clock=lambda: datetime(2026, 7, 27, 0, 1, tzinfo=timezone.utc),
            )

            harness.start_turn(game_id, "我先观察牢门。")

            prompt_document = (
                Path(__file__).parents[1]
                / "docs"
                / "agentic_mvp"
                / "gm_prompt.md"
            ).read_text(encoding="utf-8")
            charter_section = prompt_document.split("## 主持能力章程", 1)[1]
            authoritative = charter_section.split("```text", 1)[1].split("```", 1)[0]
            self.assertEqual(
                model.requests[0].messages[0],
                {"role": "system", "content": authoritative.strip()},
            )
            system_text = model.requests[0].messages[0]["content"]
            self.assertIn("玩家拥有其调查员的主动意志", system_text)
            self.assertIn("隐藏事实可以影响你的主持", system_text)
            self.assertIn("每次回答只能二选一", system_text)
            self.assertIn('"session_status": "ongoing"', system_text)
            self.assertIn("不得返回空字符串或纯空白", system_text)
            self.assertIn("绝不能包含 fact_id", system_text)
            package = json.loads(model.requests[0].messages[1]["content"])
            expected_actor_ids = {
                "investigator_tracker",
                "npc_vespera",
                "npc_saphra",
                "npc_aranis",
            }
            self.assertEqual(
                {actor["actor_id"] for actor in package["ACTOR_SHEETS"]},
                expected_actor_ids,
            )
            self.assertEqual(
                set(package["ACTOR_DISPLAY_NAMES"]),
                expected_actor_ids,
            )

    def test_second_turn_receives_complete_canon_and_retires_active_fact(self) -> None:
        """后续请求携带隐藏正典与完整记录，结束事实仍保留历史。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            opening_fact_id = store.load_session(game_id).session["facts"][0][
                "fact_id"
            ]
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "石墙后传来潮湿的回声。",
                            "establish": [
                                {
                                    "visibility": "public",
                                    "text": "排水沟通往一处旧蓄水池。",
                                },
                                {
                                    "visibility": "hidden",
                                    "text": "蓄水池上方有人守望牢房。",
                                },
                            ],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                    self._response(
                        {
                            "narration": "你钻进排水沟，牢房已被留在身后。",
                            "establish": [],
                            "retire": [
                                {
                                    "fact_id": opening_fact_id,
                                    "reason": "调查员已经离开石牢。",
                                }
                            ],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            turn_ids = iter(("turn_0001", "turn_0002"))
            fact_ids = iter(("fact_1001", "fact_1002"))
            harness = AgenticHarness(
                store,
                model,
                model_profile=deepseek_model_profile(enabled_tools=("make_check",)),
                turn_id_factory=lambda: next(turn_ids),
                fact_id_factory=lambda: next(fact_ids),
                clock=lambda: datetime(2026, 7, 27, 0, 2, tzinfo=timezone.utc),
            )

            first = harness.start_turn(game_id, "我探查排水沟。")
            second = harness.start_turn(game_id, "我钻进去，离开这间牢房。")

            self.assertEqual(first.status, "committed")
            self.assertEqual(second.status, "committed")
            self.assertEqual(
                [(change.kind, change.fact_id, change.text) for change in second.public_fact_changes],
                [("retired", opening_fact_id, "调查员已经离开石牢。")],
            )
            self.assertEqual(len(model.requests), 2)
            second_package = json.loads(model.requests[1].messages[1]["content"])
            active_by_id = {
                fact["fact_id"]: fact for fact in second_package["ACTIVE_FACTS"]
            }
            self.assertEqual(
                active_by_id["fact_1001"]["text"],
                "排水沟通往一处旧蓄水池。",
            )
            self.assertEqual(
                active_by_id["fact_1002"]["text"],
                "蓄水池上方有人守望牢房。",
            )
            self.assertEqual(active_by_id["fact_1002"]["visibility"], "hidden")
            self.assertEqual(
                second_package["COMMITTED_TURNS"][0]["established_facts"][1][
                    "text"
                ],
                "蓄水池上方有人守望牢房。",
            )
            self.assertEqual(
                second_package["COMMITTED_TURNS"][0]["player_input"],
                "我探查排水沟。",
            )
            self.assertEqual(
                second_package["MODULE_REFERENCE"],
                store.load_session(game_id).module_reference,
            )
            self.assertEqual(
                second_package["CHARACTER_REFERENCE"],
                store.load_session(game_id).character_reference,
            )
            self.assertEqual(
                [tool["function"]["name"] for tool in model.requests[1].tools],
                ["make_check"],
            )
            self.assertIn(
                "<PLAYER_INPUT>\n我钻进去，离开这间牢房。\n</PLAYER_INPUT>",
                model.requests[1].messages[2]["content"],
            )

            loaded = store.load_session(game_id)
            retired = next(
                fact
                for fact in loaded.session["facts"]
                if fact["fact_id"] == opening_fact_id
            )
            self.assertEqual(retired["status"], "retired")
            self.assertEqual(retired["retired_turn_id"], "turn_0002")
            self.assertEqual(retired["retire_reason"], "调查员已经离开石牢。")

    def test_retired_opening_fact_remains_in_complete_setup_history(self) -> None:
        """开场事实退出 ACTIVE_FACTS 后仍以原文与来源进入完整历史。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            opening_fact = store.load_session(game_id).session["facts"][0]
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "牢门已经松开。",
                            "establish": [],
                            "retire": [
                                {
                                    "fact_id": opening_fact["fact_id"],
                                    "reason": "调查员已经打开牢门。",
                                }
                            ],
                            "session_status": "ongoing",
                        }
                    ),
                    self._response(
                        {
                            "narration": "你越过已经打开的牢门。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            turn_ids = iter(("turn_0001", "turn_0002"))
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: next(turn_ids),
                clock=lambda: datetime(2026, 7, 27, 0, 2, tzinfo=timezone.utc),
            )

            harness.start_turn(game_id, "我打开牢门。")
            harness.start_turn(game_id, "我走出去。")

            package = json.loads(model.requests[1].messages[1]["content"])
            self.assertNotIn(
                opening_fact["fact_id"],
                {fact["fact_id"] for fact in package["ACTIVE_FACTS"]},
            )
            history_by_id = {
                fact["fact_id"]: fact
                for fact in package["OPENING_FACT_HISTORY"]
            }
            historical = history_by_id[opening_fact["fact_id"]]
            self.assertEqual(historical["text"], opening_fact["text"])
            self.assertEqual(historical["origin"], opening_fact["origin"])
            self.assertEqual(historical["status"], "retired")

    def test_loader_rejects_broken_turn_fact_bidirectional_integrity(self) -> None:
        """重启前校验事实确立与结束都能反向追溯到同一已提交回合。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            opening_fact_id = store.load_session(game_id).session["facts"][0][
                "fact_id"
            ]
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "一条新通道露了出来。",
                            "establish": [
                                {
                                    "visibility": "public",
                                    "text": "旧蓄水池有一条通道。",
                                }
                            ],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                    self._response(
                        {
                            "narration": "牢门已被留在身后。",
                            "establish": [],
                            "retire": [
                                {
                                    "fact_id": opening_fact_id,
                                    "reason": "调查员离开了石牢。",
                                }
                            ],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            turn_ids = iter(("turn_0001", "turn_0002"))
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: next(turn_ids),
                fact_id_factory=lambda: "fact_1001",
                clock=lambda: datetime(2026, 7, 27, 0, 3, tzinfo=timezone.utc),
            )
            harness.start_turn(game_id, "我寻找另一条通道。")
            harness.start_turn(game_id, "我离开石牢。")
            session_file = store.load_session(game_id).session_directory / "session.json"
            valid = read_json(session_file)

            missing_declaration = json.loads(json.dumps(valid, ensure_ascii=False))
            missing_declaration["turns"][0]["established_fact_ids"] = []
            write_json_atomic(session_file, missing_declaration)
            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "回合与事实引用不一致",
            ):
                store.load_session(game_id)

            mismatched_retirement = json.loads(json.dumps(valid, ensure_ascii=False))
            mismatched_retirement["facts"][0]["retire_reason"] = "被篡改的原因。"
            write_json_atomic(session_file, mismatched_retirement)
            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "FactRecord 结束历史不一致",
            ):
                store.load_session(game_id)

    def test_invalid_final_shapes_and_references_interrupt_without_commit(self) -> None:
        """代表性 schema 与引用错误都不能让未提交文本成为正典。"""

        cases = {
            "unknown top field": {
                "narration": "不会提交的叙事",
                "establish": [],
                "retire": [],
                "session_status": "ongoing",
                "diagnostic": "forged",
            },
            "wrong narration type": {
                "narration": ["不会提交的叙事"],
                "establish": [],
                "retire": [],
                "session_status": "ongoing",
            },
            "unknown establish field": {
                "narration": "不会提交的叙事",
                "establish": [
                    {
                        "visibility": "public",
                        "text": "不会提交的事实",
                        "fact_id": "fact_forged",
                    }
                ],
                "retire": [],
                "session_status": "ongoing",
            },
            "blank establish text": {
                "narration": "不会提交的叙事",
                "establish": [{"visibility": "public", "text": "   "}],
                "retire": [],
                "session_status": "ongoing",
            },
            "invalid visibility": {
                "narration": "不会提交的叙事",
                "establish": [{"visibility": "private", "text": "秘密"}],
                "retire": [],
                "session_status": "ongoing",
            },
            "unhashable visibility": {
                "narration": "不会提交的叙事",
                "establish": [{"visibility": {}, "text": "秘密"}],
                "retire": [],
                "session_status": "ongoing",
            },
            "unhashable session status": {
                "narration": "不会提交的叙事",
                "establish": [],
                "retire": [],
                "session_status": [],
            },
            "unknown retirement": {
                "narration": "不会提交的叙事",
                "establish": [],
                "retire": [{"fact_id": "fact_missing", "reason": "不存在"}],
                "session_status": "ongoing",
            },
            "duplicate retirement": {
                "narration": "不会提交的叙事",
                "establish": [],
                "retire": [
                    {"fact_id": "fact_0001", "reason": "第一次"},
                    {"fact_id": "fact_0001", "reason": "第二次"},
                ],
                "session_status": "ongoing",
            },
            "blank retirement reason": {
                "narration": "不会提交的叙事",
                "establish": [],
                "retire": [{"fact_id": "fact_0001", "reason": " "}],
                "session_status": "ongoing",
            },
        }
        for label, final in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                before = store.load_session(game_id).session
                model = ScriptedGameMasterModel(
                    [self._response(final), self._response(final)]
                )
                harness = AgenticHarness(
                    store,
                    model,
                    turn_id_factory=lambda: "turn_invalid",
                    clock=lambda: datetime(
                        2026,
                        7,
                        27,
                        0,
                        3,
                        tzinfo=timezone.utc,
                    ),
                )

                result = harness.start_turn(game_id, "我尝试一项行动。")

                self.assertEqual(result.status, "interrupted")
                self.assertEqual(result.error_code, "invalid_final_response")
                self.assertIsNone(result.narration)
                self.assertEqual(result.public_fact_changes, ())
                loaded = store.load_session(game_id).session
                self.assertEqual(loaded["turns"], before["turns"])
                self.assertEqual(loaded["facts"], before["facts"])
                self.assertEqual(
                    loaded["incomplete_turn"]["turn_id"],
                    "turn_invalid",
                )
                self.assertEqual(
                    loaded["incomplete_turn"]["last_failure"]["code"],
                    "invalid_final_response",
                )

    def test_invalid_final_receives_one_structure_repair_without_tools(self) -> None:
        """首次业务 schema 失败只给同一 GM 一次无工具修正机会。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "首份结构不完整。",
                            "establish": [],
                            "retire": [],
                        }
                    ),
                    self._response(
                        {
                            "narration": "修正后的叙事。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_repair",
                clock=lambda: datetime(2026, 7, 28, 0, 9, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查牢门。")

            self.assertEqual(result.status, "committed")
            self.assertEqual(result.narration, "修正后的叙事。")
            self.assertEqual(len(model.requests), 2)
            self.assertEqual(model.requests[0].tools[0]["function"]["name"], "make_check")
            self.assertEqual(model.requests[1].tools, ())
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            self.assertIsNone(incomplete)
            committed = store.load_session(game_id).session["turns"][0]
            self.assertEqual(committed["narration"], "修正后的叙事。")

    def test_eighth_tool_response_commits_tool_and_stops_without_ninth_request(self) -> None:
        """第八次合法工具响应先持久化，再以 step limit 中断。"""

        arguments = {
            "actor_id": "investigator_tracker",
            "ability": "spot_hidden",
            "difficulty": "regular",
            "dice_adjustment": {"kind": "none", "count": 0},
            "action": "检查牢门",
            "stakes": "失败会错过痕迹",
            "visibility": "public",
        }
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            random_source = ScriptedRandom((3, 4) * 8)
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        "{}",
                        tool_call_id=f"call_{index}",
                        name="unknown_tool",
                    )
                    for index in range(1, 8)
                ]
                + [self._tool_response(arguments, tool_call_id="call_8")]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_eighth_tool",
                mechanic_id_factory=lambda: f"mechanic_{len(model.requests) + 1}",
                random_source=random_source,
                clock=lambda: datetime(2026, 7, 28, 0, 10, tzinfo=timezone.utc),
            )

            published: list[PublicMechanic] = []
            result = harness.start_turn(
                game_id,
                "我检查牢门。",
                public_mechanic_sink=published.append,
            )

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "step_limit_exceeded")
            self.assertEqual(len(model.requests), 8)
            self.assertEqual(len(result.public_mechanics), 1)
            self.assertEqual(published, list(result.public_mechanics))
            self.assertEqual(random_source.calls, [(0, 9), (0, 9)])
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            self.assertIsNotNone(incomplete)
            assert incomplete is not None
            self.assertEqual(incomplete["round_trips_used"], 8)
            self.assertEqual(len(incomplete["mechanics"]), 1)

    def test_eighth_response_variants_are_processed_without_a_ninth_request(self) -> None:
        """第八次 final、工具错误和多工具分支都保存可恢复状态后停止。"""

        valid_final = {
            "narration": "第八次答复仍可正常提交。",
            "establish": [],
            "retire": [],
            "session_status": "ongoing",
        }
        invalid_final = {
            "narration": "第八次无效答复不应成为正典。",
            "establish": [],
            "retire": [],
        }
        variants = (
            ("final", self._response(valid_final), "committed", None, 0, 0),
            (
                "invalid_final",
                self._response(invalid_final),
                "interrupted",
                "step_limit_exceeded",
                7,
                0,
            ),
            (
                "tool_error",
                self._tool_response("{}", tool_call_id="call_8", name="unknown_tool"),
                "interrupted",
                "step_limit_exceeded",
                8,
                0,
            ),
            (
                "multiple_tool_error",
                self._multi_tool_response("call_8a", "call_8b"),
                "interrupted",
                "step_limit_exceeded",
                9,
                0,
            ),
            (
                "unpairable_protocol",
                self._multi_tool_response("call_8", "call_8"),
                "interrupted",
                "provider_protocol_error",
                7,
                1,
            ),
            (
                "unpairable_single",
                self._tool_response("{}", tool_call_id=None),
                "interrupted",
                "provider_protocol_error",
                7,
                1,
            ),
            (
                "empty_content",
                self._response(""),
                "interrupted",
                "invalid_model_response",
                7,
                1,
            ),
            (
                "malformed_json",
                self._response("{"),
                "interrupted",
                "step_limit_exceeded",
                7,
                0,
            ),
            (
                "truncated",
                ModelResponse(
                    assistant_message={
                        "role": "assistant",
                        "content": None,
                        "reasoning_content": None,
                        "tool_calls": [],
                    },
                    finish_reason="length",
                    usage=None,
                    latency_ms=10,
                ),
                "interrupted",
                "provider_response_error",
                7,
                1,
            ),
        )

        for (
            label,
            eighth_response,
            expected_status,
            expected_error,
            expected_interactions,
            expected_protocol_errors,
        ) in variants:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                prefix = [
                    self._tool_response(
                        "{}",
                        tool_call_id=f"call_{index}",
                        name="unknown_tool",
                    )
                    for index in range(1, 8)
                ]
                model = ScriptedGameMasterModel(prefix + [eighth_response])
                harness = AgenticHarness(
                    store,
                    model,
                    turn_id_factory=lambda label=label: f"turn_eighth_{label}",
                    clock=lambda: datetime(
                        2026,
                        7,
                        28,
                        0,
                        10,
                        tzinfo=timezone.utc,
                    ),
                )

                result = harness.start_turn(game_id, "我检查牢门。")

                self.assertEqual(result.status, expected_status)
                self.assertEqual(result.error_code, expected_error)
                self.assertEqual(len(model.requests), 8)
                session = store.load_session(game_id).session
                if expected_status == "committed":
                    self.assertEqual(session["incomplete_turn"], None)
                    self.assertEqual(len(session["turns"]), 1)
                else:
                    incomplete = session["incomplete_turn"]
                    assert incomplete is not None
                    self.assertEqual(incomplete["round_trips_used"], 8)
                    self.assertEqual(
                        len(incomplete["tool_interactions"]),
                        expected_interactions,
                    )
                    self.assertEqual(
                        len(incomplete["provider_protocol_errors"]),
                        expected_protocol_errors,
                    )

    def test_multiple_usable_tool_calls_are_persisted_as_errors_and_continue(self) -> None:
        """可关联多工具响应不执行机械，而是逐 ID 返回协议错误。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._multi_tool_response("call_a", "call_b"),
                    self._response(
                        {
                            "narration": "我重新选择一个工具。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_multiple",
                clock=lambda: datetime(2026, 7, 28, 0, 11, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查牢门。")

            self.assertEqual(result.status, "committed")
            self.assertEqual(model.requests[1].tools[0]["function"]["name"], "make_check")
            tool_result_messages = [
                message
                for message in model.requests[1].messages
                if message.get("role") == "tool"
            ]
            self.assertEqual(len(tool_result_messages), 2)
            self.assertTrue(
                all(
                    "multiple_tool_calls_not_allowed" in message["content"]
                    for message in tool_result_messages
                )
            )

    def test_request_timeout_is_clamped_to_remaining_attempt_budget(self) -> None:
        """单次请求 timeout 不能超过当前执行尝试的剩余时间。"""

        class ControlledClock:
            def __init__(self) -> None:
                self.current = datetime(2026, 7, 28, 0, 12, tzinfo=timezone.utc)
                self.calls = 0

            def __call__(self) -> datetime:
                self.calls += 1
                if self.calls == 2:
                    self.current += timedelta(seconds=175)
                return self.current

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            clock = ControlledClock()
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "在剩余时间内完成。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_request_budget",
                clock=clock,
            )

            result = harness.start_turn(game_id, "我观察周围。")

            self.assertEqual(result.status, "committed")
            self.assertEqual(model.requests[0].request_timeout_seconds, 5.0)

    def test_attempt_timeout_before_final_commit_keeps_turn_incomplete(self) -> None:
        """响应返回后若整次尝试已过期，不提交最终叙事。"""

        class AdvancingModel:
            def __init__(self, clock: object) -> None:
                self.clock = clock
                self.requests: list[object] = []

            def complete(self, request: object) -> ModelResponse:
                self.requests.append(request)
                assert isinstance(self.clock, ControlledClock)
                self.clock.current += timedelta(seconds=181)
                return AgenticHarnessTest._response(
                    {
                        "narration": "这段叙事不能提交。",
                        "establish": [],
                        "retire": [],
                        "session_status": "ongoing",
                    }
                )

        class ControlledClock:
            def __init__(self) -> None:
                self.current = datetime(2026, 7, 28, 0, 13, tzinfo=timezone.utc)

            def __call__(self) -> datetime:
                return self.current

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            clock = ControlledClock()
            model = AdvancingModel(clock)
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_attempt_timeout",
                clock=clock,
            )

            result = harness.start_turn(game_id, "我观察周围。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "attempt_timeout")
            loaded = store.load_session(game_id).session
            self.assertEqual(loaded["turns"], [])
            self.assertEqual(
                loaded["incomplete_turn"]["last_failure"]["code"],
                "attempt_timeout",
            )

    def test_attempt_timeout_during_final_build_precedes_atomic_write(self) -> None:
        """最终聚合构造期间过期时，写入前的截止检查仍阻止提交。"""

        class ControlledClock:
            def __init__(self) -> None:
                self.current = datetime(2026, 7, 28, 0, 13, tzinfo=timezone.utc)
                self.calls = 0

            def __call__(self) -> datetime:
                self.calls += 1
                if self.calls == 4:
                    self.current += timedelta(seconds=181)
                return self.current

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            clock = ControlledClock()
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "构造期间过期的叙事不能提交。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_build_timeout",
                clock=clock,
            )

            result = harness.start_turn(game_id, "我观察周围。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "attempt_timeout")
            session = store.load_session(game_id).session
            self.assertEqual(session["turns"], [])
            self.assertEqual(
                session["incomplete_turn"]["last_failure"]["code"],
                "attempt_timeout",
            )

    def test_unpairable_multiple_tool_response_keeps_only_raw_protocol_error(self) -> None:
        """多工具 ID 不可关联时不伪造任何 assistant/tool 配对。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [self._multi_tool_response("call_same", "call_same")]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_unpairable_multiple",
                clock=lambda: datetime(2026, 7, 28, 0, 14, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查牢门。")

            self.assertEqual(result.error_code, "provider_protocol_error")
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            assert incomplete is not None
            self.assertEqual(len(incomplete["deepseek_messages"]), 3)
            self.assertEqual(incomplete["tool_interactions"], [])
            self.assertEqual(len(incomplete["provider_protocol_errors"]), 1)

    def test_second_invalid_final_does_not_receive_a_second_repair(self) -> None:
        """一次修正仍失败时保持中断，不进入第三次模型请求。"""

        invalid = {
            "narration": "结构错误。",
            "establish": [],
            "retire": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [self._response(invalid), self._response(invalid)]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_repair_failed",
                clock=lambda: datetime(2026, 7, 28, 0, 15, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查牢门。")

            self.assertEqual(result.error_code, "invalid_final_response")
            self.assertEqual(len(model.requests), 2)
            self.assertEqual(model.requests[1].tools, ())
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            assert incomplete is not None
            self.assertEqual(incomplete["structure_repairs_used"], 1)
            self.assertEqual(incomplete["total_structure_repairs"], 1)

    def test_truncated_provider_response_is_an_interruption_without_fallback(self) -> None:
        """provider 截断不会被 Harness 伪装成可提交叙事。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    ModelResponse(
                        assistant_message={
                            "role": "assistant",
                            "content": None,
                            "reasoning_content": None,
                            "tool_calls": [],
                        },
                        finish_reason="length",
                        usage=None,
                        latency_ms=10,
                    )
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_truncated",
                clock=lambda: datetime(2026, 7, 28, 0, 16, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查牢门。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "provider_response_error")
            self.assertIsNone(result.narration)
            self.assertEqual(store.load_session(game_id).session["turns"], [])

    def test_unknown_finish_reason_is_a_provider_protocol_interruption(self) -> None:
        """未知模型步骤不能伪装成合法 final。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            final = {
                "narration": "未知步骤不能提交。",
                "establish": [],
                "retire": [],
                "session_status": "ongoing",
            }
            model = ScriptedGameMasterModel(
                [
                    ModelResponse(
                        assistant_message={
                            "role": "assistant",
                            "content": json.dumps(final, ensure_ascii=False),
                            "reasoning_content": None,
                            "tool_calls": [],
                        },
                        finish_reason="mystery",
                        usage=None,
                        latency_ms=10,
                    )
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_unknown_step",
                clock=lambda: datetime(2026, 7, 28, 0, 17, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查牢门。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "invalid_model_response")
            self.assertEqual(store.load_session(game_id).session["turns"], [])


    def test_retired_fact_cannot_be_retired_again(self) -> None:
        """已结束事实不是后续 final 可再次结束的有效引用。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            opening_fact_id = store.load_session(game_id).session["facts"][0][
                "fact_id"
            ]
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "牢门已经打开。",
                            "establish": [],
                            "retire": [
                                {
                                    "fact_id": opening_fact_id,
                                    "reason": "牢门已经打开。",
                                }
                            ],
                            "session_status": "ongoing",
                        }
                    ),
                    self._response(
                        {
                            "narration": "不会提交的第二轮叙事。",
                            "establish": [],
                            "retire": [
                                {
                                    "fact_id": opening_fact_id,
                                    "reason": "重复结束。",
                                }
                            ],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            turn_ids = iter(("turn_0001", "turn_0002"))
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: next(turn_ids),
                clock=lambda: datetime(2026, 7, 27, 0, 4, tzinfo=timezone.utc),
            )

            first = harness.start_turn(game_id, "我打开牢门。")
            second = harness.start_turn(game_id, "我再次打开同一扇门。")

            self.assertEqual(first.status, "committed")
            self.assertEqual(second.status, "interrupted")
            session = store.load_session(game_id).session
            self.assertEqual(len(session["turns"]), 1)
            self.assertEqual(session["incomplete_turn"]["turn_id"], "turn_0002")

    def test_provider_failure_keeps_original_turn_and_blocks_new_action(self) -> None:
        """provider 失败保留原输入与稳定 turn_id，并阻塞覆盖性新行动。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    ModelCallError(
                        "request_timeout",
                        "provider detail must not reach player",
                        retryable=True,
                    )
                ]
            )
            allocated_turn_ids: list[str] = []

            def new_turn_id() -> str:
                value = f"turn_{len(allocated_turn_ids) + 1:04d}"
                allocated_turn_ids.append(value)
                return value

            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=new_turn_id,
                clock=lambda: datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我贴近门缝倾听。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.turn_id, "turn_0001")
            self.assertEqual(result.error_code, "request_timeout")
            self.assertNotIn("provider detail", result.error_message or "")
            interrupted = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(interrupted["turn_id"], "turn_0001")
            self.assertEqual(interrupted["player_input"], "我贴近门缝倾听。")
            self.assertEqual(interrupted["last_failure"]["code"], "request_timeout")

            with self.assertRaises(AgenticTurnBlockedError):
                harness.start_turn(game_id, "我改做另一件事。")
            self.assertEqual(allocated_turn_ids, ["turn_0001"])
            self.assertEqual(len(model.requests), 1)

    def test_interrupted_turn_is_discoverable_and_resumes_after_restart(self) -> None:
        """调用者只读发现中断后，可由新 Harness 显式恢复同一回合。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            interrupted_model = ScriptedGameMasterModel(
                [
                    ModelCallError(
                        "request_timeout",
                        "private provider detail",
                        retryable=True,
                    )
                ]
            )
            first_harness = AgenticHarness(
                store,
                interrupted_model,
                turn_id_factory=lambda: "turn_0001",
                clock=lambda: datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc),
            )
            interrupted = first_harness.start_turn(
                game_id,
                "我贴近门缝倾听。",
            )
            session_file = store.load_session(game_id).session_directory / "session.json"
            before_projection = session_file.read_bytes()

            lifecycle = first_harness.get_session_state(game_id)

            self.assertEqual(lifecycle.session_status, "ongoing")
            self.assertEqual(lifecycle.technical_status, "interrupted")
            self.assertTrue(lifecycle.has_incomplete_turn)
            self.assertEqual(lifecycle.turn_id, "turn_0001")
            self.assertEqual(lifecycle.error_code, "request_timeout")
            self.assertNotIn("private provider detail", lifecycle.error_message or "")
            self.assertEqual(lifecycle.public_mechanics, ())
            self.assertEqual(len(interrupted_model.requests), 1)
            self.assertEqual(session_file.read_bytes(), before_projection)

            allocated_turn_ids: list[str] = []
            resumed_model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "门外只有潮水拍击石墙的回声。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            restarted_harness = AgenticHarness(
                store,
                resumed_model,
                turn_id_factory=lambda: allocated_turn_ids.append("unexpected")
                or "turn_unexpected",
                clock=lambda: datetime(2026, 7, 27, 0, 6, tzinfo=timezone.utc),
            )

            resumed = restarted_harness.resume_turn(game_id, interrupted.turn_id)

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(resumed.turn_id, "turn_0001")
            self.assertEqual(allocated_turn_ids, [])
            self.assertEqual(len(resumed_model.requests), 1)
            committed = store.load_session(game_id).session
            self.assertIsNone(committed["incomplete_turn"])
            self.assertEqual(len(committed["turns"]), 1)
            self.assertEqual(committed["turns"][0]["turn_id"], "turn_0001")
            self.assertEqual(
                committed["turns"][0]["player_input"],
                "我贴近门缝倾听。",
            )

    def test_thinking_reasoning_round_trips_only_in_incomplete_recovery_state(
        self,
    ) -> None:
        canary_marker = "THINKING_RECOVERY_CANARY_10_EXACT"
        reasoning_canary = f"  {canary_marker}\nsecond line  "
        content_canary = "THINKING_TOOL_CONTENT_CANARY_10"
        profile = deepseek_model_profile(thinking=True)
        arguments = self._valid_check_arguments()
        tool_response = self._tool_response(
            arguments,
            tool_call_id="call_thinking_recovery",
            reasoning=reasoning_canary,
            content=content_canary,
        )

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            first_model = ScriptedGameMasterModel(
                [
                    tool_response,
                    ModelCallError(
                        "request_timeout",
                        "private provider detail",
                        retryable=True,
                    ),
                ]
            )
            first_harness = AgenticHarness(
                store,
                first_model,
                turn_id_factory=lambda: "turn_thinking_recovery",
                mechanic_id_factory=lambda: "mechanic_thinking_recovery",
                random_source=ScriptedRandom((3, 4)),
                model_profile=profile,
                clock=lambda: datetime(2026, 8, 7, 0, 10, tzinfo=timezone.utc),
            )

            interrupted = first_harness.start_turn(game_id, "我检查牢门。")
            lifecycle = first_harness.get_session_state(game_id)
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            assert incomplete is not None
            replay_prefix = incomplete["deepseek_messages"]

            self.assertEqual(interrupted.status, "interrupted")
            self.assertEqual(interrupted.error_code, "request_timeout")
            self.assertEqual(incomplete["model_profile"], profile)
            self.assertEqual(replay_prefix[-2], tool_response.assistant_message)
            self.assertEqual(
                replay_prefix[-2]["reasoning_content"],
                reasoning_canary,
            )
            self.assertEqual(replay_prefix[-2]["content"], content_canary)
            self.assertEqual(replay_prefix[-1]["role"], "tool")
            self.assertNotIn(canary_marker, repr(tool_response))
            self.assertNotIn(content_canary, repr(tool_response))
            self.assertNotIn(canary_marker, repr(first_harness))
            self.assertNotIn(content_canary, repr(first_harness))
            self.assertNotIn(canary_marker, repr(interrupted))
            self.assertNotIn(content_canary, repr(interrupted))
            self.assertNotIn(canary_marker, repr(lifecycle))
            self.assertNotIn(content_canary, repr(lifecycle))

            final_response = self._response(
                {
                    "narration": "牢门铰链附近留着一道新鲜刮痕。",
                    "establish": [],
                    "retire": [],
                    "session_status": "ongoing",
                },
                reasoning="THINKING_FINAL_CANARY_10",
            )
            resumed_model = ScriptedGameMasterModel([final_response])
            resumed_random = ScriptedRandom(())
            resumed_harness = AgenticHarness(
                store,
                resumed_model,
                random_source=resumed_random,
                model_profile=profile,
                clock=lambda: datetime(2026, 8, 7, 0, 11, tzinfo=timezone.utc),
            )

            resumed = resumed_harness.resume_turn(
                game_id,
                "turn_thinking_recovery",
            )

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(resumed_model.requests[0].messages, tuple(replay_prefix))
            self.assertEqual(resumed_model.requests[0].model_profile, profile)
            self.assertNotIn(canary_marker, repr(resumed_model.requests[0]))
            self.assertEqual(resumed_random.calls, [])
            committed = store.load_session(game_id).session
            self.assertIsNone(committed["incomplete_turn"])
            committed_json = json.dumps(committed, ensure_ascii=False, sort_keys=True)
            self.assertNotIn(canary_marker, committed_json)
            self.assertNotIn(content_canary, committed_json)
            self.assertNotIn("THINKING_FINAL_CANARY_10", committed_json)
            self.assertNotIn(canary_marker, repr(resumed))

    def test_thinking_direct_final_allows_nullable_reasoning_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "我先把手收回，观察锁扣的受力方向。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            result = AgenticHarness(
                store,
                model,
                model_profile=deepseek_model_profile(thinking=True),
                turn_id_factory=lambda: "turn_thinking_nullable_final",
                clock=lambda: datetime(2026, 8, 7, 0, 14, tzinfo=timezone.utc),
            ).start_turn(game_id, "我观察锁扣。")

            self.assertEqual(result.status, "committed")
            self.assertIsNone(store.load_session(game_id).session["incomplete_turn"])

    def test_thinking_response_without_reasoning_preserves_last_legal_prefix(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            random_source = ScriptedRandom(())
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        self._valid_check_arguments(),
                        tool_call_id="call_missing_reasoning",
                        reasoning=None,
                    )
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_missing_reasoning",
                mechanic_id_factory=lambda: "mechanic_must_not_exist",
                random_source=random_source,
                model_profile=deepseek_model_profile(thinking=True),
                clock=lambda: datetime(2026, 8, 7, 0, 12, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查牢门。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "provider_protocol_error")
            self.assertEqual(random_source.calls, [])
            self.assertEqual(len(model.requests), 1)
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            assert incomplete is not None
            self.assertEqual(len(incomplete["deepseek_messages"]), 3)
            self.assertEqual(incomplete["mechanics"], [])
            self.assertEqual(incomplete["tool_interactions"], [])
            self.assertEqual(len(incomplete["provider_protocol_errors"]), 1)
            self.assertEqual(
                incomplete["last_failure"]["code"],
                "provider_protocol_error",
            )

    def test_loader_rejects_thinking_replay_with_missing_reasoning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            self._valid_check_arguments(),
                            reasoning="THINKING_PERSISTENCE_CANARY_10",
                        ),
                        ModelCallError(
                            "request_timeout",
                            "stop after legal tool pair",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_thinking_tamper",
                mechanic_id_factory=lambda: "mechanic_thinking_tamper",
                random_source=ScriptedRandom((3, 4)),
                model_profile=deepseek_model_profile(thinking=True),
                clock=lambda: datetime(2026, 8, 7, 0, 13, tzinfo=timezone.utc),
            )
            harness.start_turn(game_id, "我检查牢门。")
            loaded = store.load_session(game_id)
            session_file = loaded.session_directory / "session.json"
            tampered = json.loads(session_file.read_text(encoding="utf-8"))
            tampered["incomplete_turn"]["deepseek_messages"][-2].pop(
                "reasoning_content"
            )
            write_json_atomic(session_file, tampered)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "deepseek_messages",
            ):
                store.load_session(game_id)

    def test_resume_replays_committed_tools_without_reroll_or_hidden_leak(self) -> None:
        """恢复沿用合法工具前缀，公开投影不泄露隐藏机械。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            public_arguments = {
                "actor_id": "investigator_tracker",
                "ability": "spot_hidden",
                "difficulty": "regular",
                "dice_adjustment": {"kind": "none", "count": 0},
                "action": "检查门锁上的新鲜刮痕",
                "stakes": "失败会错过守卫留下的痕迹",
                "visibility": "public",
            }
            hidden_arguments = {
                **public_arguments,
                "actor_id": "investigator_tracker",
                "action": "判断走廊远处的暗影是否正在靠近",
                "stakes": "失败会误判暗影的位置",
                "visibility": "hidden",
            }
            interrupted_model = ScriptedGameMasterModel(
                [
                    self._tool_response(public_arguments, tool_call_id="call_public"),
                    self._tool_response(hidden_arguments, tool_call_id="call_hidden"),
                    ModelCallError("request_timeout", "private", retryable=True),
                ]
            )
            mechanic_ids = iter(("mechanic_public", "mechanic_hidden"))
            first_random = ScriptedRandom((3, 4, 5, 6))
            first_harness = AgenticHarness(
                store,
                interrupted_model,
                turn_id_factory=lambda: "turn_0001",
                mechanic_id_factory=lambda: next(mechanic_ids),
                random_source=first_random,
                clock=lambda: datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc),
            )

            interrupted = first_harness.start_turn(game_id, "我仔细观察牢门。")
            lifecycle = first_harness.get_session_state(game_id)
            saved_incomplete = store.load_session(game_id).session["incomplete_turn"]
            replay_prefix = saved_incomplete["deepseek_messages"]

            self.assertEqual(interrupted.status, "interrupted")
            self.assertEqual(
                [item.mechanic_id for item in lifecycle.public_mechanics],
                ["mechanic_public"],
            )
            self.assertNotIn("mechanic_hidden", repr(lifecycle))
            self.assertNotIn("private", repr(lifecycle))
            self.assertEqual(len(saved_incomplete["mechanics"]), 2)

            resumed_model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "刮痕很新，远处的暗影却没有继续靠近。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            resumed_random = ScriptedRandom(())
            restarted_harness = AgenticHarness(
                store,
                resumed_model,
                random_source=resumed_random,
                clock=lambda: datetime(2026, 7, 27, 0, 6, tzinfo=timezone.utc),
            )

            resumed = restarted_harness.resume_turn(game_id, "turn_0001")

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(resumed_model.requests[0].messages, tuple(replay_prefix))
            self.assertEqual(resumed_random.calls, [])
            committed = store.load_session(game_id).session
            self.assertEqual(len(committed["turns"]), 1)
            self.assertEqual(
                [item["mechanic_id"] for item in committed["turns"][0]["mechanics"]],
                ["mechanic_public", "mechanic_hidden"],
            )

    def test_resume_replays_same_successful_tool_call_idempotently(self) -> None:
        """GM 重发同一成功调用时只复用已提交结果，不重复机械。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            actors_before = store.load_session(game_id).session["actors"]
            arguments = self._valid_check_arguments()
            first = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments, tool_call_id="call_replay"),
                        ModelCallError(
                            "request_timeout",
                            "stop after tool",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_replay_success",
                mechanic_id_factory=lambda: "mechanic_replay",
                random_source=ScriptedRandom((3, 4)),
                clock=lambda: datetime(2026, 7, 28, 0, 20, tzinfo=timezone.utc),
            )

            interrupted = first.start_turn(game_id, "我检查牢门。")
            self.assertEqual(interrupted.error_code, "request_timeout")
            saved = store.load_session(game_id).session["incomplete_turn"]
            assert saved is not None
            replay_prefix = saved["deepseek_messages"]

            replay_random = ScriptedRandom(())
            resumed_model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        json.dumps(arguments, ensure_ascii=False, indent=2),
                        tool_call_id="call_replay",
                    ),
                    ModelCallError(
                        "request_timeout",
                        "stop after replay",
                        retryable=True,
                    ),
                ]
            )
            published: list[PublicMechanic] = []
            allocated_mechanic_ids: list[str] = []

            def forbidden_mechanic_id() -> str:
                allocated_mechanic_ids.append("mechanic_forbidden")
                return "mechanic_forbidden"

            resumed_harness = AgenticHarness(
                store,
                resumed_model,
                mechanic_id_factory=forbidden_mechanic_id,
                random_source=replay_random,
                clock=lambda: datetime(2026, 7, 28, 0, 21, tzinfo=timezone.utc),
            )
            replayed = resumed_harness.resume_turn(
                game_id,
                "turn_replay_success",
                public_mechanic_sink=published.append,
            )

            self.assertEqual(replayed.error_code, "request_timeout")
            self.assertEqual(replayed.public_mechanics, ())
            self.assertEqual(published, [])
            self.assertEqual(replay_random.calls, [])
            self.assertEqual(allocated_mechanic_ids, [])
            self.assertEqual(len(resumed_model.requests), 2)
            self.assertEqual(resumed_model.requests[0].messages, tuple(replay_prefix))
            after_replay = store.load_session(game_id).session["incomplete_turn"]
            assert after_replay is not None
            self.assertEqual(len(after_replay["tool_interactions"]), 1)
            self.assertEqual(len(after_replay["mechanics"]), 1)
            self.assertEqual(len(after_replay["deepseek_messages"]), 7)
            self.assertEqual(
                self._tool_message_count(after_replay["deepseek_messages"]),
                2,
            )

            final_model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        json.dumps(arguments, ensure_ascii=False, indent=2),
                        tool_call_id="call_replay",
                    ),
                    self._response(
                        {
                            "narration": "门锁附近没有新的动静。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            final_harness = AgenticHarness(
                store,
                final_model,
                mechanic_id_factory=forbidden_mechanic_id,
                random_source=replay_random,
                clock=lambda: datetime(2026, 7, 28, 0, 22, tzinfo=timezone.utc),
            )
            resumed = final_harness.resume_turn(
                game_id,
                "turn_replay_success",
                public_mechanic_sink=published.append,
            )

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(resumed.public_mechanics, ())
            self.assertEqual(published, [])
            self.assertEqual(replay_random.calls, [])
            self.assertEqual(allocated_mechanic_ids, [])
            self.assertEqual(
                final_model.requests[0].messages,
                tuple(after_replay["deepseek_messages"]),
            )
            committed = store.load_session(game_id).session
            self.assertEqual(committed["actors"], actors_before)
            self.assertEqual(len(committed["turns"]), 1)
            self.assertEqual(
                committed["turns"][0]["mechanics"][0]["mechanic_id"],
                "mechanic_replay",
            )
            self.assertEqual(committed["turns"][0]["mechanics"][0]["roll"], 43)

            self.assertEqual(
                self._tool_message_count(final_model.requests[1].messages),
                3,
            )

    def test_resume_replays_same_failed_tool_call_idempotently(self) -> None:
        """GM 重发同一结构化失败时复用原错误，不生成第二条交互。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            arguments_raw = "{"
            first_model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        arguments_raw,
                        tool_call_id="call_failed",
                    ),
                    ModelCallError(
                        "request_timeout",
                        "stop after tool",
                        retryable=True,
                    ),
                ]
            )
            first = AgenticHarness(
                store,
                first_model,
                turn_id_factory=lambda: "turn_replay_failure",
                random_source=ScriptedRandom(()),
                clock=lambda: datetime(2026, 7, 28, 0, 22, tzinfo=timezone.utc),
            )
            first.start_turn(game_id, "我检查牢门。")

            resumed_model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        arguments_raw,
                        tool_call_id="call_failed",
                    ),
                    self._response(
                        {
                            "narration": "你暂时没有可靠的检定工具。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            resumed = AgenticHarness(
                store,
                resumed_model,
                random_source=ScriptedRandom(()),
                clock=lambda: datetime(2026, 7, 28, 0, 23, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_replay_failure")

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(
                self._tool_message_count(resumed_model.requests[1].messages),
                2,
            )
            committed = store.load_session(game_id).session
            self.assertEqual(len(committed["turns"]), 1)
            self.assertEqual(committed["turns"][0]["mechanics"], [])

    def test_resume_replays_multiple_tool_errors_without_new_interactions(self) -> None:
        """多工具协议错误重发时逐 ID 复用失败结果，不追加交互。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            first = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._multi_tool_response("call_a", "call_b"),
                        ModelCallError(
                            "request_timeout",
                            "stop after multiple-tool error",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_multiple_replay",
                random_source=ScriptedRandom(()),
                clock=lambda: datetime(2026, 7, 28, 0, 36, tzinfo=timezone.utc),
            )
            first.start_turn(game_id, "我检查牢门。")

            replay_model = ScriptedGameMasterModel(
                [
                    self._multi_tool_response("call_a", "call_b"),
                    ModelCallError(
                        "request_timeout",
                        "stop after replayed multiple-tool error",
                        retryable=True,
                    ),
                ]
            )
            replayed = AgenticHarness(
                store,
                replay_model,
                random_source=ScriptedRandom(()),
                clock=lambda: datetime(2026, 7, 28, 0, 37, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_multiple_replay")

            self.assertEqual(replayed.error_code, "request_timeout")
            after_replay = store.load_session(game_id).session["incomplete_turn"]
            assert after_replay is not None
            self.assertEqual(len(after_replay["tool_interactions"]), 2)
            self.assertEqual(len(after_replay["mechanics"]), 0)
            self.assertEqual(len(after_replay["deepseek_messages"]), 9)
            self.assertEqual(len(after_replay["provider_protocol_errors"]), 0)

            final_model = ScriptedGameMasterModel(
                [
                    self._multi_tool_response("call_a", "call_b"),
                    self._response(
                        {
                            "narration": "我改为一次只选择一个工具。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            result = AgenticHarness(
                store,
                final_model,
                random_source=ScriptedRandom(()),
                clock=lambda: datetime(2026, 7, 28, 0, 38, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_multiple_replay")

            self.assertEqual(result.status, "committed")
            self.assertEqual(result.public_mechanics, ())
            committed = store.load_session(game_id).session
            self.assertEqual(len(committed["turns"]), 1)
            self.assertEqual(committed["turns"][0]["mechanics"], [])
            self.assertEqual(
                self._tool_message_count(final_model.requests[1].messages),
                6,
            )

    def test_multiple_tool_errors_normalize_and_replay_semantic_arguments(self) -> None:
        """合法多工具参数保存规范值，并允许同义 JSON 按调用 ID 重放。"""

        def multiple_check_response(
            first_arguments: str,
            second_arguments: str,
        ) -> ModelResponse:
            return ModelResponse(
                assistant_message={
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": None,
                    "tool_calls": [
                        {
                            "id": "call_a",
                            "type": "function",
                            "function": {
                                "name": "make_check",
                                "arguments": first_arguments,
                            },
                        },
                        {
                            "id": "call_b",
                            "type": "function",
                            "function": {
                                "name": "make_check",
                                "arguments": second_arguments,
                            },
                        },
                    ],
                },
                finish_reason="tool_calls",
                usage=None,
                latency_ms=10,
            )

        first_arguments = self._valid_check_arguments()
        second_arguments = {
            **first_arguments,
            "action": "倾听门外脚步",
            "stakes": "失败会错过守卫接近的声音",
        }
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            first = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        multiple_check_response(
                            json.dumps(first_arguments, ensure_ascii=False),
                            json.dumps(second_arguments, ensure_ascii=False),
                        ),
                        ModelCallError("request_timeout", "stop", retryable=True),
                    ]
                ),
                turn_id_factory=lambda: "turn_multiple_semantic_replay",
                mechanic_id_factory=lambda: self.fail(
                    "multiple-tool rejection allocated mechanic ID"
                ),
                random_source=ScriptedRandom(()),
            ).start_turn(game_id, "我同时提出两项检定。")

            self.assertEqual(first.error_code, "request_timeout")
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(
                [item["arguments"] for item in incomplete["tool_interactions"]],
                [first_arguments, second_arguments],
            )

            reordered_first = dict(reversed(tuple(first_arguments.items())))
            reordered_second = dict(reversed(tuple(second_arguments.items())))
            replayed = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        multiple_check_response(
                            json.dumps(reordered_first, ensure_ascii=False, indent=2),
                            json.dumps(reordered_second, ensure_ascii=False, indent=2),
                        ),
                        ModelCallError("request_timeout", "stop", retryable=True),
                    ]
                ),
                mechanic_id_factory=lambda: self.fail(
                    "multiple-tool replay allocated mechanic ID"
                ),
                random_source=ScriptedRandom(()),
            ).resume_turn(game_id, "turn_multiple_semantic_replay")

            self.assertEqual(replayed.error_code, "request_timeout")
            after_replay = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(len(after_replay["tool_interactions"]), 2)
            self.assertEqual(len(after_replay["provider_protocol_errors"]), 0)

    def test_replay_mismatch_keeps_original_interaction_and_interrupts(self) -> None:
        """同 ID 异规范参数不能覆盖原结果或再次执行工具。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            arguments = self._valid_check_arguments()
            changed = {**arguments, "action": "改看走廊暗影"}
            first_random = ScriptedRandom((3, 4))
            first = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            arguments,
                            tool_call_id="call_mismatch",
                        ),
                        ModelCallError(
                            "request_timeout",
                            "stop after tool",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_replay_mismatch",
                mechanic_id_factory=lambda: "mechanic_mismatch",
                random_source=first_random,
                clock=lambda: datetime(2026, 7, 28, 0, 26, tzinfo=timezone.utc),
            )
            first.start_turn(game_id, "我检查牢门。")
            saved = store.load_session(game_id).session["incomplete_turn"]
            assert saved is not None
            original_messages = saved["deepseek_messages"]
            original_interaction = saved["tool_interactions"][0]

            replay_random = ScriptedRandom(())
            result = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            changed,
                            tool_call_id="call_mismatch",
                        )
                    ]
                ),
                random_source=replay_random,
                clock=lambda: datetime(2026, 7, 28, 0, 27, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_replay_mismatch")

            self.assertEqual(result.error_code, "provider_protocol_error")
            self.assertEqual(replay_random.calls, [])
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            assert incomplete is not None
            self.assertEqual(incomplete["deepseek_messages"], original_messages)
            self.assertEqual(
                incomplete["tool_interactions"],
                [original_interaction],
            )
            self.assertEqual(len(incomplete["mechanics"]), 1)
            self.assertEqual(len(incomplete["provider_protocol_errors"]), 1)

    def test_changed_raw_after_failed_normalization_interrupts(self) -> None:
        """规范化失败时只要 raw 改变就必须中断。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            first = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            "{",
                            tool_call_id="call_raw_changed",
                        ),
                        ModelCallError(
                            "request_timeout",
                            "stop after tool",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_raw_changed",
                random_source=ScriptedRandom(()),
                clock=lambda: datetime(2026, 7, 28, 0, 30, tzinfo=timezone.utc),
            )
            first.start_turn(game_id, "我检查牢门。")

            replay_random = ScriptedRandom(())
            result = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            "{ ",
                            tool_call_id="call_raw_changed",
                        )
                    ]
                ),
                random_source=replay_random,
                clock=lambda: datetime(2026, 7, 28, 0, 31, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_raw_changed")

            self.assertEqual(result.error_code, "provider_protocol_error")
            self.assertEqual(replay_random.calls, [])
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            assert incomplete is not None
            self.assertEqual(len(incomplete["tool_interactions"]), 1)
            self.assertEqual(len(incomplete["mechanics"]), 0)
            self.assertEqual(len(incomplete["provider_protocol_errors"]), 1)

    def test_same_provider_tool_id_is_independent_across_turns(self) -> None:
        """provider ID 只在当前 turn_id 内构成幂等键。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            arguments = self._valid_check_arguments()
            final = self._response(
                {
                    "narration": "检查已经完成。",
                    "establish": [],
                    "retire": [],
                    "session_status": "ongoing",
                }
            )
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(arguments, tool_call_id="call_reused"),
                    final,
                    self._tool_response(arguments, tool_call_id="call_reused"),
                    final,
                ]
            )
            turn_ids = iter(("turn_first", "turn_second"))
            mechanic_ids = iter(("mechanic_first", "mechanic_second"))
            random_source = ScriptedRandom((3, 4, 5, 6))
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: next(turn_ids),
                mechanic_id_factory=lambda: next(mechanic_ids),
                random_source=random_source,
                clock=lambda: datetime(2026, 7, 28, 0, 35, tzinfo=timezone.utc),
            )

            first = harness.start_turn(game_id, "我第一次检查牢门。")
            second = harness.start_turn(game_id, "我第二次检查牢门。")

            self.assertEqual(first.status, "committed")
            self.assertEqual(second.status, "committed")
            self.assertEqual(random_source.calls, [(0, 9)] * 4)
            turns = store.load_session(game_id).session["turns"]
            self.assertEqual(len(turns), 2)
            self.assertEqual(
                [turn["mechanics"][0]["mechanic_id"] for turn in turns],
                ["mechanic_first", "mechanic_second"],
            )
            self.assertEqual(
                [turn["mechanics"][0]["roll"] for turn in turns],
                [43, 65],
            )

    def test_repeated_resume_resets_attempt_budget_and_preserves_totals(self) -> None:
        """每次恢复获得冻结预算，同时保留同一回合的累计诊断。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            first_harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [ModelCallError("request_timeout", "private", retryable=True)]
                ),
                turn_id_factory=lambda: "turn_0001",
                clock=lambda: datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc),
            )
            first_harness.start_turn(game_id, "我继续倾听门外。")
            invalid_final = self._response(
                {
                    "narration": "不会提交的叙事。",
                    "establish": [],
                    "retire": [],
                    "session_status": [],
                }
            )
            second_model = ScriptedGameMasterModel([invalid_final, invalid_final])
            local_one_response_limits = {
                "max_round_trips": 1,
                "request_timeout_seconds": 1,
                "attempt_timeout_seconds": 1,
                "max_structure_repairs": 0,
            }
            second_harness = AgenticHarness(
                store,
                second_model,
                attempt_limits=local_one_response_limits,
                clock=lambda: datetime(2026, 7, 27, 0, 6, tzinfo=timezone.utc),
            )

            second = second_harness.resume_turn(game_id, "turn_0001")

            self.assertEqual(second.error_code, "invalid_final_response")
            self.assertEqual(len(second_model.requests), 2)
            after_second = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(after_second["turn_id"], "turn_0001")
            self.assertEqual(after_second["attempt_number"], 2)
            self.assertEqual(after_second["round_trips_used"], 2)
            self.assertEqual(after_second["total_round_trips"], 2)
            self.assertEqual(after_second["structure_repairs_used"], 1)
            self.assertEqual(after_second["total_structure_repairs"], 1)
            self.assertEqual(
                after_second["attempt_limits"],
                {
                    "max_round_trips": 8,
                    "request_timeout_seconds": 60,
                    "attempt_timeout_seconds": 180,
                    "max_structure_repairs": 1,
                },
            )

            third_model = ScriptedGameMasterModel(
                [ModelCallError("request_timeout", "private", retryable=True)]
            )
            third_harness = AgenticHarness(
                store,
                third_model,
                clock=lambda: datetime(2026, 7, 27, 0, 7, tzinfo=timezone.utc),
            )
            third = third_harness.resume_turn(game_id, "turn_0001")

            self.assertEqual(third.error_code, "request_timeout")
            after_third = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(after_third["turn_id"], "turn_0001")
            self.assertEqual(after_third["attempt_number"], 3)
            self.assertEqual(after_third["round_trips_used"], 0)
            self.assertEqual(after_third["total_round_trips"], 2)
            self.assertEqual(after_third["structure_repairs_used"], 0)
            self.assertEqual(after_third["total_structure_repairs"], 1)
            self.assertEqual(store.load_session(game_id).session["turns"], [])

    def test_resume_preserves_pending_structure_repair_phase(self) -> None:
        """恢复先完成已保存的无工具修正，并仍拥有本次修正预算。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            invalid_final = self._response(
                {
                    "narration": "不会提交的叙事。",
                    "establish": [],
                    "retire": [],
                    "session_status": [],
                }
            )
            first_harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        invalid_final,
                        ModelCallError("request_timeout", "private", retryable=True),
                    ]
                ),
                turn_id_factory=lambda: "turn_0001",
                clock=lambda: datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc),
            )
            interrupted = first_harness.start_turn(game_id, "我倾听门外。")
            saved = store.load_session(game_id).session["incomplete_turn"]
            replay_prefix = saved["deepseek_messages"]

            self.assertEqual(interrupted.error_code, "request_timeout")
            self.assertEqual(replay_prefix[-1]["role"], "user")
            self.assertIn("本地校验提示", replay_prefix[-1]["content"])

            resumed_model = ScriptedGameMasterModel(
                [
                    invalid_final,
                    self._response(
                        {
                            "narration": "门外只有潮水拍击石墙。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                ]
            )
            resumed = AgenticHarness(
                store,
                resumed_model,
                clock=lambda: datetime(2026, 7, 27, 0, 6, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_0001")

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(len(resumed_model.requests), 2)
            self.assertEqual(resumed_model.requests[0].messages, tuple(replay_prefix))
            self.assertEqual(resumed_model.requests[0].tools, ())
            self.assertEqual(resumed_model.requests[1].tools, ())

    def test_structure_repair_budget_is_hard_capped_at_one(self) -> None:
        """即使配置误设更大，单次执行也不能连续修正两次。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            invalid_final = self._response(
                {
                    "narration": "不会提交的叙事。",
                    "establish": [],
                    "retire": [],
                    "session_status": [],
                }
            )
            model = ScriptedGameMasterModel(
                [invalid_final, invalid_final, invalid_final]
            )
            harness = AgenticHarness(
                store,
                model,
                attempt_limits={
                    "max_round_trips": 8,
                    "request_timeout_seconds": 60,
                    "attempt_timeout_seconds": 180,
                    "max_structure_repairs": 2,
                },
                turn_id_factory=lambda: "turn_0001",
                clock=lambda: datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我倾听门外。")

            self.assertEqual(result.error_code, "invalid_final_response")
            self.assertEqual(len(model.requests), 2)
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(incomplete["structure_repairs_used"], 1)
            self.assertEqual(incomplete["total_structure_repairs"], 1)

    def test_invalid_recovery_requests_fail_before_model_call_or_write(self) -> None:
        """恢复 ID 或冻结 profile 不可用时，不得改变原恢复记录。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, game_id = self._create_session(root)
            AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [ModelCallError("request_timeout", "private", retryable=True)]
                ),
                turn_id_factory=lambda: "turn_0001",
                clock=lambda: datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc),
            ).start_turn(game_id, "我贴近门缝倾听。")
            session_file = store.load_session(game_id).session_directory / "session.json"
            interrupted_bytes = session_file.read_bytes()

            wrong_id_model = ScriptedGameMasterModel(
                [self._response({"unreachable": True})]
            )
            wrong_id_harness = AgenticHarness(store, wrong_id_model)
            with self.assertRaisesRegex(
                AgenticTurnBlockedError,
                "指定回合当前不可恢复",
            ):
                wrong_id_harness.resume_turn(game_id, "turn_wrong")
            self.assertEqual(wrong_id_model.requests, [])
            self.assertEqual(session_file.read_bytes(), interrupted_bytes)

            unavailable_profile_model = ScriptedGameMasterModel(
                [self._response({"unreachable": True})]
            )
            unavailable_profile_harness = AgenticHarness(
                store,
                unavailable_profile_model,
                tool_registry={"lifecycle_test": LifecycleTestTool()},
                model_profile={
                    **deepseek_model_profile(model_id="deepseek-v4-pro"),
                    "enabled_tools": ["lifecycle_test"],
                },
            )
            with self.assertRaisesRegex(
                AgenticTurnBlockedError,
                "冻结的模型运行配置当前不可用",
            ):
                unavailable_profile_harness.resume_turn(game_id, "turn_0001")
            self.assertEqual(unavailable_profile_model.requests, [])
            self.assertEqual(session_file.read_bytes(), interrupted_bytes)

            fresh_store = AgenticSessionStore(
                session_root=root / "fresh-sessions",
                game_id_factory=lambda: "game_fresh_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            fresh = fresh_store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_tracker",
                    display_name="林雁",
                )
            )
            unavailable_model = ScriptedGameMasterModel(
                [self._response({"unreachable": True})]
            )
            fresh_harness = AgenticHarness(fresh_store, unavailable_model)
            ready = fresh_harness.get_session_state(fresh.game_id)
            self.assertEqual(ready.technical_status, "ready")
            self.assertFalse(ready.has_incomplete_turn)
            with self.assertRaisesRegex(
                AgenticTurnBlockedError,
                "指定回合当前不可恢复",
            ):
                fresh_harness.resume_turn(fresh.game_id, "turn_unknown")
            self.assertEqual(unavailable_model.requests, [])

            completed_model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "你登上引潮舟，驶离沉城。",
                            "establish": [],
                            "retire": [],
                            "session_status": "complete",
                        }
                    )
                ]
            )
            completed_harness = AgenticHarness(
                fresh_store,
                completed_model,
                turn_id_factory=lambda: "turn_complete",
            )
            completed_harness.start_turn(fresh.game_id, "我驶离港口。")
            completed = completed_harness.get_session_state(fresh.game_id)
            self.assertEqual(completed.session_status, "complete")
            self.assertEqual(completed.technical_status, "complete")
            self.assertFalse(completed.has_incomplete_turn)
            model_calls_before_resume = len(completed_model.requests)
            with self.assertRaisesRegex(
                AgenticTurnBlockedError,
                "指定回合当前不可恢复",
            ):
                completed_harness.resume_turn(fresh.game_id, "turn_complete")
            self.assertEqual(len(completed_model.requests), model_calls_before_resume)

    def test_resume_rejects_unavailable_frozen_provider_before_model_call(self) -> None:
        """冻结 provider 不受当前 Harness 支持时不能开始恢复尝试。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            first = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [ModelCallError("request_timeout", "private", retryable=True)]
                ),
                turn_id_factory=lambda: "turn_0001",
            ).start_turn(game_id, "我贴近门缝倾听。")
            session_file = store.load_session(game_id).session_directory / "session.json"
            tampered = read_json(session_file)
            tampered["incomplete_turn"]["model_profile"]["provider"] = "not-deepseek"
            write_json_atomic(session_file, tampered)
            tampered_bytes = session_file.read_bytes()
            resumed_model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "不可到达。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )

            with self.assertRaisesRegex(
                AgenticTurnBlockedError,
                "冻结的模型运行配置当前不可用",
            ):
                AgenticHarness(store, resumed_model).resume_turn(game_id, first.turn_id)

            self.assertEqual(resumed_model.requests, [])
            self.assertEqual(session_file.read_bytes(), tampered_bytes)

    def test_provider_failure_code_is_allowlisted_before_persistence_or_output(self) -> None:
        """provider 失败码不是玩家输出协议，未知值必须收敛为稳定码。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    ModelCallError(
                        "provider-secret\nforged-output",
                        "provider message must remain private",
                        retryable=False,
                    )
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_0001",
                clock=lambda: datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我先观察牢门。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "provider_error")
            self.assertNotIn("provider-secret", result.error_message or "")
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(incomplete["last_failure"]["code"], "provider_error")

    def test_make_check_resolves_coc_difficulty_dice_and_success_boundaries(
        self,
    ) -> None:
        """独立骰例覆盖原生目标、奖励惩罚选择及六档 COC 成功等级。"""

        cases = (
            ("critical", "spot_hidden", "regular", "none", 0, (1, 0), 70, 1, "critical_success"),
            ("extreme boundary", "spot_hidden", "extreme", "none", 0, (4, 1), 14, 14, "extreme_success"),
            ("hard boundary", "spot_hidden", "hard", "none", 0, (5, 3), 35, 35, "hard_success"),
            ("regular boundary", "spot_hidden", "regular", "none", 0, (0, 7), 70, 70, "regular_success"),
            ("ordinary failure", "spot_hidden", "regular", "none", 0, (1, 7), 70, 71, "failure"),
            ("high ability fumble", "spot_hidden", "regular", "none", 0, (0, 0), 70, 100, "fumble"),
            ("low ability fumble", "locksmith", "regular", "none", 0, (6, 9), 1, 96, "fumble"),
            ("attribute hard target", "strength", "hard", "none", 0, (0, 2), 20, 20, "hard_success"),
            ("bonus chooses lower", "spot_hidden", "regular", "bonus", 1, (7, 8, 2), 70, 27, "hard_success"),
            ("penalty chooses higher", "spot_hidden", "regular", "penalty", 1, (7, 8, 2), 70, 87, "failure"),
            ("bonus treats zero zero as 100", "spot_hidden", "regular", "bonus", 1, (0, 0, 5), 70, 50, "regular_success"),
            ("penalty treats zero zero as 100", "spot_hidden", "regular", "penalty", 1, (0, 0, 5), 70, 100, "fumble"),
            ("two bonus dice", "spot_hidden", "regular", "bonus", 2, (9, 8, 4, 1), 70, 19, "hard_success"),
        )
        for (
            label,
            ability,
            difficulty,
            kind,
            count,
            dice,
            target,
            roll,
            success_level,
        ) in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                arguments = {
                    "actor_id": "investigator_tracker",
                    "ability": ability,
                    "difficulty": difficulty,
                    "dice_adjustment": {"kind": kind, "count": count},
                    "action": "执行边界检定",
                    "stakes": "失败会产生明确代价",
                    "visibility": "public",
                }
                model = ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments),
                        self._response(
                            {
                                "narration": "检定已经得到解释。",
                                "establish": [],
                                "retire": [],
                                "session_status": "ongoing",
                            }
                        ),
                    ]
                )
                random_source = ScriptedRandom(dice)
                harness = AgenticHarness(
                    store,
                    model,
                    turn_id_factory=lambda: "turn_0001",
                    mechanic_id_factory=lambda: "mechanic_0001",
                    random_source=random_source,
                    clock=lambda: datetime(
                        2026,
                        7,
                        28,
                        0,
                        2,
                        tzinfo=timezone.utc,
                    ),
                )

                result = harness.start_turn(game_id, "我执行这个行动。")

                public = result.public_mechanics[0]
                self.assertEqual(public.details["target"], target)
                self.assertEqual(public.details["roll"], roll)
                self.assertEqual(public.details["success_level"], success_level)
                self.assertEqual(
                    random_source.calls,
                    [(0, 9)] * (count + 2),
                )

    def test_make_check_snapshots_player_remediation_eligibility(self) -> None:
        """只有公开调查员的普通失败在提交时保留 Push/Luck 资格。"""

        cases = (
            ("public investigator failure", "investigator_tracker", "regular", "public", (1, 7), True),
            ("missed hard difficulty", "investigator_tracker", "hard", "public", (0, 7), True),
            ("investigator fumble", "investigator_tracker", "regular", "public", (0, 0), False),
            ("investigator success", "investigator_tracker", "regular", "public", (0, 7), False),
            ("public npc failure", "npc_vespera", "regular", "public", (1, 7), False),
            ("hidden investigator failure", "investigator_tracker", "regular", "hidden", (1, 7), False),
        )
        for label, actor_id, difficulty, visibility, dice, expected in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                model = ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            {
                                "actor_id": actor_id,
                                "ability": "spot_hidden",
                                "difficulty": difficulty,
                                "dice_adjustment": {"kind": "none", "count": 0},
                                "action": "尝试发现牢门上的细小痕迹",
                                "stakes": "失败会错过眼前的线索",
                                "visibility": visibility,
                            }
                        ),
                        self._response(
                            {
                                "narration": "检定已经得到解释。",
                                "establish": [],
                                "retire": [],
                                "session_status": "ongoing",
                            }
                        ),
                    ]
                )
                result = AgenticHarness(
                    store,
                    model,
                    mechanic_id_factory=lambda: "mechanic_eligibility",
                    random_source=ScriptedRandom(dice),
                ).start_turn(game_id, "我尝试寻找痕迹。")

                self.assertEqual(result.status, "committed")
                mechanic = store.load_session(game_id).session["turns"][0]["mechanics"][0]
                self.assertIs(mechanic["push_eligible"], expected)
                self.assertIs(mechanic["luck_eligible"], expected)

    def test_fixed_point_eligibility_snapshots_load_without_authorizing_push(self) -> None:
        """兼容旧运行时快照，但 Push 仍按真实角色、可见性和结果拒绝。"""

        cases = (
            ("hidden failure", "investigator_tracker", "hidden", (1, 7), True, True),
            ("npc failure", "npc_vespera", "public", (1, 7), True, True),
            ("fumble", "investigator_tracker", "public", (0, 0), True, False),
        )
        for label, actor_id, visibility, dice, old_push, old_luck in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                AgenticHarness(
                    store,
                    ScriptedGameMasterModel(
                        [
                            self._tool_response(
                                {
                                    **self._valid_check_arguments(),
                                    "actor_id": actor_id,
                                    "visibility": visibility,
                                }
                            ),
                            self._response(
                                {
                                    "narration": "旧运行时已经提交检定。",
                                    "establish": [],
                                    "retire": [],
                                    "session_status": "ongoing",
                                }
                            ),
                        ]
                    ),
                    mechanic_id_factory=lambda: "mechanic_base",
                    random_source=ScriptedRandom(dice),
                ).start_turn(game_id, "我执行一次旧运行时检定。")
                session_file = store.session_root / game_id / "session.json"
                fixed_point_session = read_json(session_file)
                mechanic = fixed_point_session["turns"][0]["mechanics"][0]
                mechanic["push_eligible"] = old_push
                mechanic["luck_eligible"] = old_luck
                write_json_atomic(session_file, fixed_point_session)

                loaded = store.load_session(game_id).session
                self.assertIs(
                    loaded["turns"][0]["mechanics"][0]["push_eligible"],
                    old_push,
                )
                random_source = ScriptedRandom(())
                result = AgenticHarness(
                    store,
                    ScriptedGameMasterModel(
                        [
                            self._tool_response(
                                {
                                    "check_id": "mechanic_base",
                                    "new_approach": "改从门轴侧面拆卸",
                                    "failure_stakes": "门轴断裂并夹伤手掌",
                                },
                                name="push_check",
                            ),
                            ModelCallError("request_timeout", "stop", retryable=True),
                        ]
                    ),
                    mechanic_id_factory=lambda: self.fail(
                        "legacy-ineligible push allocated a mechanic ID"
                    ),
                    random_source=random_source,
                ).start_turn(game_id, "我尝试孤注一掷。")

                self.assertEqual(result.error_code, "request_timeout")
                self.assertEqual(random_source.calls, [])
                incomplete = store.load_session(game_id).session["incomplete_turn"]
                self.assertEqual(
                    incomplete["tool_interactions"][0]["error"]["code"],
                    "push_not_allowed",
                )

    def test_fixed_point_eligibility_snapshots_do_not_authorize_spend_luck(self) -> None:
        """旧 Luck 快照可读取，但不会授权隐藏、NPC 或 fumble 检定。"""

        cases = (
            ("hidden failure", "investigator_tracker", "hidden", (1, 7), True, True),
            ("npc failure", "npc_vespera", "public", (1, 7), True, True),
            ("fumble", "investigator_tracker", "public", (0, 0), True, False),
        )
        for label, actor_id, visibility, dice, old_push, old_luck in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                AgenticHarness(
                    store,
                    ScriptedGameMasterModel(
                        [
                            self._tool_response(
                                {
                                    **self._valid_check_arguments(),
                                    "actor_id": actor_id,
                                    "visibility": visibility,
                                }
                            ),
                            self._response(
                                {
                                    "narration": "旧运行时已经提交检定。",
                                    "establish": [],
                                    "retire": [],
                                    "session_status": "ongoing",
                                }
                            ),
                        ]
                    ),
                    mechanic_id_factory=lambda: "mechanic_base",
                    random_source=ScriptedRandom(dice),
                ).start_turn(game_id, "我执行一次旧运行时检定。")
                session_file = store.session_root / game_id / "session.json"
                fixed_point_session = read_json(session_file)
                mechanic = fixed_point_session["turns"][0]["mechanics"][0]
                mechanic["push_eligible"] = old_push
                mechanic["luck_eligible"] = old_luck
                write_json_atomic(session_file, fixed_point_session)

                loaded = store.load_session(game_id).session
                self.assertIs(
                    loaded["turns"][0]["mechanics"][0]["luck_eligible"],
                    old_luck,
                )
                random_source = ScriptedRandom(())
                result = AgenticHarness(
                    store,
                    ScriptedGameMasterModel(
                        [
                            self._tool_response(
                                {"check_id": "mechanic_base", "points": 1},
                                name="spend_luck",
                            ),
                            ModelCallError("request_timeout", "stop", retryable=True),
                        ]
                    ),
                    mechanic_id_factory=lambda: self.fail(
                        "legacy-ineligible Luck allocated a mechanic ID"
                    ),
                    random_source=random_source,
                ).start_turn(game_id, "我尝试花费幸运。")

                self.assertEqual(result.error_code, "request_timeout")
                self.assertEqual(random_source.calls, [])
                incomplete = store.load_session(game_id).session["incomplete_turn"]
                self.assertEqual(
                    incomplete["tool_interactions"][0]["error"]["code"],
                    "luck_not_allowed",
                )

    def test_player_can_push_committed_failed_check_on_next_turn(self) -> None:
        """Push 继承可信检定参数并以新记录公开提交，不覆盖原失败。"""

        base_arguments = {
            "actor_id": "investigator_tracker",
            "ability": "spot_hidden",
            "difficulty": "hard",
            "dice_adjustment": {"kind": "bonus", "count": 1},
            "action": "用发卡拨动锈蚀锁芯",
            "stakes": "失败会制造足以引来守卫的金属声",
            "visibility": "public",
        }
        push_arguments = {
            "check_id": "mechanic_base",
            "new_approach": "拆下门轴固定钉，从铰链一侧强行卸门",
            "failure_stakes": "若仍失败，门轴会断裂并把手夹在石框中",
        }
        final = {
            "narration": "机械结果已经得到忠实解释。",
            "establish": [],
            "retire": [],
            "session_status": "ongoing",
        }
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(base_arguments),
                    self._response(final),
                    self._tool_response(push_arguments, name="push_check"),
                    self._response(final),
                ]
            )
            mechanic_ids = iter(("mechanic_base", "mechanic_pushed"))
            random_source = ScriptedRandom((0, 7, 8, 4, 1, 9))
            harness = AgenticHarness(
                store,
                model,
                mechanic_id_factory=lambda: next(mechanic_ids),
                random_source=random_source,
            )

            first = harness.start_turn(game_id, "我用发卡撬锁。")
            original = json.loads(
                json.dumps(
                    store.load_session(game_id).session["turns"][0]["mechanics"][0]
                )
            )
            second = harness.start_turn(
                game_id,
                "我不花幸运。我拆门轴并接受夹伤手掌的风险，孤注一掷。",
            )

            self.assertEqual(first.status, "committed")
            self.assertEqual(second.status, "committed")
            session = store.load_session(game_id).session
            self.assertEqual(session["turns"][0]["mechanics"][0], original)
            self.assertEqual(original["success_level"], "regular_success")
            self.assertIs(original["push_eligible"], True)
            pushed = session["turns"][1]["mechanics"][0]
            for field in (
                "actor_id",
                "ability",
                "ability_value",
                "difficulty",
                "target",
                "dice_adjustment",
                "visibility",
            ):
                self.assertEqual(pushed[field], original[field])
            self.assertEqual(pushed["mechanic_id"], "mechanic_pushed")
            self.assertEqual(pushed["action"], push_arguments["new_approach"])
            self.assertEqual(pushed["stakes"], push_arguments["failure_stakes"])
            self.assertEqual(pushed["pushed_from"], "mechanic_base")
            self.assertIs(pushed["is_pushed"], True)
            self.assertIs(pushed["push_eligible"], False)
            self.assertIs(pushed["luck_eligible"], False)
            self.assertEqual(second.public_mechanics[0].details["pushed_from"], "mechanic_base")
            self.assertIs(second.public_mechanics[0].details["is_pushed"], True)
            self.assertEqual(random_source.calls, [(0, 9)] * 6)

    def test_push_preflight_rejects_invalid_base_before_id_and_rng(self) -> None:
        """Push 的未知、非失败或非玩家来源都在可信边界前拒绝。"""

        valid = self._valid_check_arguments()
        cases = (
            ("unknown check", None, (), "missing_check", "invalid_check_id"),
            ("successful check", valid, (0, 7), "mechanic_base", "push_not_allowed"),
            ("fumble check", valid, (0, 0), "mechanic_base", "push_not_allowed"),
            (
                "npc check",
                {**valid, "actor_id": "npc_vespera"},
                (1, 7),
                "mechanic_base",
                "push_not_allowed",
            ),
            (
                "hidden check",
                {**valid, "visibility": "hidden"},
                (1, 7),
                "mechanic_base",
                "push_not_allowed",
            ),
        )
        for label, base_arguments, base_dice, check_id, expected_code in cases:
            with self.subTest(label=label):
                self._assert_push_rejected_after_base(
                    base_arguments=base_arguments,
                    base_dice=base_dice,
                    check_id=check_id,
                    expected_code=expected_code,
                )

    def test_push_requires_base_check_from_prior_player_turn(self) -> None:
        """同一未完成回合刚产生的失败不能被 GM 自动 Push。"""

        push_arguments = {
            "check_id": "mechanic_base",
            "new_approach": "改从门轴侧面拆卸",
            "failure_stakes": "门轴断裂并夹伤手掌",
        }
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            allocated: list[str] = []

            def next_mechanic_id() -> str:
                ids = ("mechanic_base", "mechanic_unexpected")
                value = ids[len(allocated)]
                allocated.append(value)
                return value

            random_source = ScriptedRandom((1, 7, 0, 7))
            result = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            self._valid_check_arguments(),
                            tool_call_id="call_base",
                        ),
                        self._tool_response(
                            push_arguments,
                            name="push_check",
                            tool_call_id="call_push",
                        ),
                        ModelCallError("request_timeout", "stop", retryable=True),
                    ]
                ),
                mechanic_id_factory=next_mechanic_id,
                random_source=random_source,
            ).start_turn(game_id, "我只声明第一次检查牢门。")

            self.assertEqual(result.error_code, "request_timeout")
            self.assertEqual(allocated, ["mechanic_base"])
            self.assertEqual(random_source.calls, [(0, 9), (0, 9)])
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(
                [mechanic["mechanic_id"] for mechanic in incomplete["mechanics"]],
                ["mechanic_base"],
            )
            self.assertEqual(
                incomplete["tool_interactions"][1]["error"]["code"],
                "push_not_allowed",
            )

    def test_push_preflight_rejects_non_check_id_before_id_and_rng(self) -> None:
        """语义化 check_id 不能引用其他 mechanic kind。"""

        profile = deepseek_model_profile(
            enabled_tools=("lifecycle_test", "push_check")
        )
        registry = {
            "lifecycle_test": LifecycleTestTool(),
            "push_check": PushCheckTool(),
        }
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        {
                            "actor_id": "investigator_tracker",
                            "amount": 1,
                            "visibility": "public",
                        },
                        name="lifecycle_test",
                    ),
                    self._response(
                        {
                            "narration": "测试机械已提交。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    ),
                    self._tool_response(
                        {
                            "check_id": "mechanic_non_check",
                            "new_approach": "换一种做法",
                            "failure_stakes": "接受更严重风险",
                        },
                        name="push_check",
                    ),
                    ModelCallError("request_timeout", "stop", retryable=True),
                ]
            )
            allocated: list[str] = []

            def next_mechanic_id() -> str:
                value = (
                    "mechanic_non_check"
                    if not allocated
                    else "mechanic_unexpected"
                )
                allocated.append(value)
                return value

            random_source = ScriptedRandom(())
            harness = AgenticHarness(
                store,
                model,
                model_profile=profile,
                tool_registry=registry,
                mechanic_id_factory=next_mechanic_id,
                random_source=random_source,
            )

            first = harness.start_turn(game_id, "我先使用测试机械。")
            second = harness.start_turn(game_id, "我尝试推动这条非检定记录。")

            self.assertEqual(first.status, "committed")
            self.assertEqual(second.error_code, "request_timeout")
            self.assertEqual(allocated, ["mechanic_non_check"])
            self.assertEqual(random_source.calls, [])
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(
                incomplete["tool_interactions"][0]["error"]["code"],
                "invalid_check_id",
            )
            self.assertEqual(incomplete["mechanics"], [])

    def test_push_chain_allows_only_one_derivation_before_id_and_rng(self) -> None:
        """基础检定不能重复 Push，pushed 派生结果也不能再次 Push。"""

        for attempted_check_id in ("mechanic_base", "mechanic_pushed"):
            with self.subTest(check_id=attempted_check_id), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                push_arguments = {
                    "check_id": "mechanic_base",
                    "new_approach": "改从门轴侧面拆卸",
                    "failure_stakes": "门轴断裂并夹伤手掌",
                }
                model = ScriptedGameMasterModel(
                    [
                        self._tool_response(self._valid_check_arguments()),
                        self._response(
                            {
                                "narration": "基础检定失败。",
                                "establish": [],
                                "retire": [],
                                "session_status": "ongoing",
                            }
                        ),
                        self._tool_response(push_arguments, name="push_check"),
                        self._response(
                            {
                                "narration": "孤注一掷已经结算。",
                                "establish": [],
                                "retire": [],
                                "session_status": "ongoing",
                            }
                        ),
                        self._tool_response(
                            {**push_arguments, "check_id": attempted_check_id},
                            name="push_check",
                        ),
                        ModelCallError("request_timeout", "stop", retryable=True),
                    ]
                )
                allocated: list[str] = []

                def next_mechanic_id() -> str:
                    ids = ("mechanic_base", "mechanic_pushed", "mechanic_unexpected")
                    value = ids[len(allocated)]
                    allocated.append(value)
                    return value

                random_source = ScriptedRandom((1, 7, 0, 7))
                harness = AgenticHarness(
                    store,
                    model,
                    mechanic_id_factory=next_mechanic_id,
                    random_source=random_source,
                )

                harness.start_turn(game_id, "我先检查牢门。")
                harness.start_turn(game_id, "我换一种做法孤注一掷。")
                before_random_calls = list(random_source.calls)
                result = harness.start_turn(game_id, "我试图再次孤注一掷。")

                self.assertEqual(result.error_code, "request_timeout")
                self.assertEqual(allocated, ["mechanic_base", "mechanic_pushed"])
                self.assertEqual(random_source.calls, before_random_calls)
                incomplete = store.load_session(game_id).session["incomplete_turn"]
                self.assertEqual(
                    incomplete["tool_interactions"][0]["error"]["code"],
                    "push_not_allowed",
                )
                self.assertEqual(incomplete["mechanics"], [])

    def test_push_rejects_check_with_prior_luck_chain_before_id_and_rng(self) -> None:
        """一旦补救链包含 Luck 记录，原失败不能再被 Push。"""

        profile = deepseek_model_profile(
            enabled_tools=("make_check", "historical_luck_marker", "push_check")
        )
        registry = {
            "make_check": MakeCheckTool(),
            "historical_luck_marker": HistoricalLuckMarkerTool(),
            "push_check": PushCheckTool(),
        }
        final = self._final_payload()
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(self._valid_check_arguments()),
                    self._response(final),
                    self._tool_response(
                        {"check_id": "mechanic_base"},
                        name="historical_luck_marker",
                    ),
                    self._response(final),
                    self._tool_response(
                        {
                            "check_id": "mechanic_base",
                            "new_approach": "改从门轴侧面拆卸",
                            "failure_stakes": "门轴断裂并夹伤手掌",
                        },
                        name="push_check",
                    ),
                    ModelCallError("request_timeout", "stop", retryable=True),
                ]
            )
            allocated: list[str] = []

            def next_mechanic_id() -> str:
                ids = ("mechanic_base", "mechanic_luck", "mechanic_unexpected")
                value = ids[len(allocated)]
                allocated.append(value)
                return value

            random_source = ScriptedRandom((1, 7))
            harness = AgenticHarness(
                store,
                model,
                model_profile=profile,
                tool_registry=registry,
                mechanic_id_factory=next_mechanic_id,
                random_source=random_source,
            )

            harness.start_turn(game_id, "我先检查牢门。")
            harness.start_turn(game_id, "我选择使用一次测试 Luck 补救。")
            before_random_calls = list(random_source.calls)
            result = harness.start_turn(game_id, "我又尝试对原失败孤注一掷。")

            self.assertEqual(result.error_code, "request_timeout")
            self.assertEqual(allocated, ["mechanic_base", "mechanic_luck"])
            self.assertEqual(random_source.calls, before_random_calls)
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(
                incomplete["tool_interactions"][0]["error"]["code"],
                "push_not_allowed",
            )
            self.assertEqual(incomplete["mechanics"], [])

    def test_pushed_check_keeps_deterministic_coc_outcome_levels(self) -> None:
        """Push 的成功、失败和 fumble 都由第二次独立骰点决定。"""

        outcomes = (
            ("success", (0, 7), "regular_success"),
            ("failure", (1, 7), "failure"),
            ("fumble", (0, 0), "fumble"),
        )
        for label, pushed_dice, expected_level in outcomes:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                model = ScriptedGameMasterModel(
                    [
                        self._tool_response(self._valid_check_arguments()),
                        self._response(
                            {
                                "narration": "第一次检定失败。",
                                "establish": [],
                                "retire": [],
                                "session_status": "ongoing",
                            }
                        ),
                        self._tool_response(
                            {
                                "check_id": "mechanic_base",
                                "new_approach": "改从门轴侧面拆卸",
                                "failure_stakes": "门轴断裂并夹伤手掌",
                            },
                            name="push_check",
                        ),
                        self._response(
                            {
                                "narration": "第二次检定结果已经解释。",
                                "establish": [],
                                "retire": [],
                                "session_status": "ongoing",
                            }
                        ),
                    ]
                )
                mechanic_ids = iter(("mechanic_base", "mechanic_pushed"))
                harness = AgenticHarness(
                    store,
                    model,
                    mechanic_id_factory=lambda: next(mechanic_ids),
                    random_source=ScriptedRandom((1, 7, *pushed_dice)),
                )

                harness.start_turn(game_id, "我尝试检查牢门。")
                result = harness.start_turn(game_id, "我换一种做法孤注一掷。")

                self.assertEqual(result.status, "committed")
                pushed = store.load_session(game_id).session["turns"][1]["mechanics"][0]
                self.assertEqual(pushed["success_level"], expected_level)
                self.assertIs(pushed["push_eligible"], False)
                self.assertIs(pushed["luck_eligible"], False)

    def test_pushed_check_replays_once_after_interruption_and_restart(self) -> None:
        """Push 提交后中断，重启恢复只重放原结果且不产生新派生记录。"""

        push_arguments = {
            "check_id": "mechanic_base",
            "new_approach": "改从门轴侧面拆卸",
            "failure_stakes": "门轴断裂并夹伤手掌",
        }
        final = {
            "narration": "孤注一掷结果已经忠实承接。",
            "establish": [],
            "retire": [],
            "session_status": "ongoing",
        }
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(self._valid_check_arguments()),
                        self._response(final),
                    ]
                ),
                turn_id_factory=lambda: "turn_base",
                mechanic_id_factory=lambda: "mechanic_base",
                random_source=ScriptedRandom((1, 7)),
            ).start_turn(game_id, "我先检查牢门。")

            pushed_random = ScriptedRandom((0, 7))
            interrupted = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            push_arguments,
                            name="push_check",
                            tool_call_id="call_push",
                        ),
                        ModelCallError("request_timeout", "stop after push", retryable=True),
                    ]
                ),
                turn_id_factory=lambda: "turn_push",
                mechanic_id_factory=lambda: "mechanic_pushed",
                random_source=pushed_random,
            ).start_turn(game_id, "我换一种做法孤注一掷。")

            self.assertEqual(interrupted.error_code, "request_timeout")
            self.assertEqual(interrupted.public_mechanics[0].mechanic_id, "mechanic_pushed")
            before_resume = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(len(before_resume["mechanics"]), 1)
            original_pushed = json.loads(json.dumps(before_resume["mechanics"][0]))
            replay_random = ScriptedRandom(())
            resumed = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            push_arguments,
                            name="push_check",
                            tool_call_id="call_push",
                        ),
                        self._response(final),
                    ]
                ),
                mechanic_id_factory=lambda: self.fail(
                    "replayed push allocated a new mechanic ID"
                ),
                random_source=replay_random,
            ).resume_turn(game_id, "turn_push")

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(replay_random.calls, [])
            session = store.load_session(game_id).session
            self.assertEqual(len(session["turns"]), 2)
            self.assertEqual(session["turns"][1]["mechanics"], [original_pushed])
            self.assertIsNone(session["incomplete_turn"])

    def test_pushed_check_write_failure_leaves_no_partial_derivation(self) -> None:
        """Push 原子写失败时不报告或保存未提交的派生检定。"""

        final = {
            "narration": "基础检定已经解释。",
            "establish": [],
            "retire": [],
            "session_status": "ongoing",
        }
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(self._valid_check_arguments()),
                        self._response(final),
                    ]
                ),
                mechanic_id_factory=lambda: "mechanic_base",
                random_source=ScriptedRandom((1, 7)),
            ).start_turn(game_id, "我先检查牢门。")
            writes = 0

            def fail_push_write(path: Path, value: object) -> None:
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("injected push write failure")
                write_json_atomic(path, value)

            pushed_random = ScriptedRandom((0, 7))
            published: list[PublicMechanic] = []
            result = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            {
                                "check_id": "mechanic_base",
                                "new_approach": "改从门轴侧面拆卸",
                                "failure_stakes": "门轴断裂并夹伤手掌",
                            },
                            name="push_check",
                        )
                    ]
                ),
                turn_id_factory=lambda: "turn_push_write_failure",
                mechanic_id_factory=lambda: "mechanic_uncommitted_push",
                random_source=pushed_random,
                session_writer=fail_push_write,
            ).start_turn(
                game_id,
                "我换一种做法孤注一掷。",
                public_mechanic_sink=published.append,
            )

            self.assertEqual(result.error_code, "tool_commit_failed")
            self.assertEqual(result.public_mechanics, ())
            self.assertEqual(published, [])
            self.assertEqual(pushed_random.calls, [(0, 9), (0, 9)])
            session = store.load_session(game_id).session
            self.assertEqual(len(session["turns"]), 1)
            self.assertEqual(
                session["turns"][0]["mechanics"][0]["mechanic_id"],
                "mechanic_base",
            )
            incomplete = session["incomplete_turn"]
            self.assertEqual(incomplete["mechanics"], [])
            self.assertEqual(incomplete["tool_interactions"], [])

    def test_loader_rejects_tampered_pushed_schema_and_inheritance(self) -> None:
        """装载时拒绝 pushed 字段伪造、断链和继承参数改写。"""

        base = {
            "actor_id": "investigator_tracker",
            "ability": "spot_hidden",
            "difficulty": "hard",
            "dice_adjustment": {"kind": "none", "count": 0},
            "action": "检查牢门",
            "stakes": "失败会错过痕迹",
            "visibility": "public",
        }
        push = {
            "check_id": "mechanic_base",
            "new_approach": "改从门轴侧面拆卸",
            "failure_stakes": "门轴断裂并夹伤手掌",
        }
        final = {
            "narration": "机械结果已经解释。",
            "establish": [],
            "retire": [],
            "session_status": "ongoing",
        }
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(base),
                        self._response(final),
                        self._tool_response(push, name="push_check"),
                        self._response(final),
                    ]
                ),
                mechanic_id_factory=iter(("mechanic_base", "mechanic_pushed")).__next__,
                random_source=ScriptedRandom((0, 7, 1, 4)),
            )
            self.assertEqual(harness.start_turn(game_id, "我检查牢门。").status, "committed")
            self.assertEqual(
                harness.start_turn(game_id, "我换一种做法孤注一掷。").status,
                "committed",
            )
            session_file = store.session_root / game_id / "session.json"
            baseline = read_json(session_file)

            for label in (
                "extra field",
                "false marker",
                "detached source",
                "changed inheritance",
                "reordered chronology",
            ):
                with self.subTest(label=label):
                    tampered = json.loads(json.dumps(baseline))
                    pushed = tampered["turns"][1]["mechanics"][0]
                    if label == "extra field":
                        pushed["unexpected"] = True
                    elif label == "false marker":
                        pushed["is_pushed"] = False
                    elif label == "detached source":
                        pushed["pushed_from"] = "mechanic_missing"
                    else:
                        if label == "changed inheritance":
                            pushed["difficulty"] = "regular"
                            pushed["target"] = 70
                        else:
                            tampered["turns"].reverse()
                    write_json_atomic(session_file, tampered)
                    with self.assertRaisesRegex(
                        AgenticSessionLoadError,
                        "CommittedTurn 格式无效|机械与冻结角色卡不一致",
                    ):
                        store.load_session(game_id)
                    write_json_atomic(session_file, baseline)

    def test_player_can_spend_exact_luck_on_committed_failed_check(self) -> None:
        """Luck 精确买到原难度并原子扣点，不覆盖原检定或重掷。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            random_source = ScriptedRandom((3, 7))
            mechanic_ids = iter(("mechanic_base", "mechanic_luck"))
            model = ScriptedGameMasterModel(
                self._base_and_luck_responses(
                    narration="机械结果已经得到忠实解释。"
                )
                + [self._response(self._final_payload("后续行动继续。"))]
            )
            harness = AgenticHarness(
                store,
                model,
                mechanic_id_factory=mechanic_ids.__next__,
                random_source=random_source,
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )

            first = harness.start_turn(game_id, "我检查牢门。")
            original = json.loads(
                json.dumps(
                    store.load_session(game_id).session["turns"][0]["mechanics"][0]
                )
            )
            second = harness.start_turn(game_id, "我花费 3 点幸运让这次检定成功。")
            third = harness.start_turn(game_id, "我依据成功结果继续行动。")

            self.assertEqual(first.status, "committed")
            self.assertEqual(second.status, "committed")
            self.assertEqual(third.status, "committed")
            self.assertEqual(original["roll"], 73)
            self.assertEqual(original["target"], 70)
            session = store.load_session(game_id).session
            self.assertEqual(session["turns"][0]["mechanics"][0], original)
            luck = session["turns"][1]["mechanics"][0]
            self.assertEqual(
                luck,
                {
                    "mechanic_id": "mechanic_luck",
                    "kind": "luck_spend",
                    "actor_id": "investigator_tracker",
                    "check_id": "mechanic_base",
                    "points_spent": 3,
                    "luck_before": 55,
                    "luck_after": 52,
                    "success_level_before": "failure",
                    "success_level_after": "regular_success",
                    "visibility": "public",
                    "committed_at": "2026-07-27T00:00:00Z",
                },
            )
            investigator = self._actor(session, "investigator_tracker")
            self.assertEqual(investigator["luck"]["current"], 52)
            self.assertEqual(
                second.public_mechanics,
                (
                    PublicMechanic(
                        mechanic_id="mechanic_luck",
                        kind="luck_spend",
                        actor_id="investigator_tracker",
                        details={
                            "check_id": "mechanic_base",
                            "points_spent": 3,
                            "luck_before": 55,
                            "luck_after": 52,
                            "success_level_before": "failure",
                            "success_level_after": "regular_success",
                        },
                    ),
                ),
            )
            package = json.loads(model.requests[-1].messages[1]["content"])
            historical_luck = package["COMMITTED_TURNS"][1]["mechanics"][0]
            self.assertEqual(historical_luck["check_id"], "mechanic_base")
            self.assertEqual(historical_luck["success_level_after"], "regular_success")
            self.assertEqual(
                package["COMMITTED_TURNS"][0]["mechanics"][0]["roll"],
                73,
            )
            self.assertEqual(random_source.calls, [(0, 9), (0, 9)])

    def test_spend_luck_reaches_declared_difficulty_without_critical(self) -> None:
        """Luck 只买到声明难度，target 为 1 时也不制造 critical。"""

        cases = (
            ("spot_hidden", "hard", (8, 3), "regular_success", "hard_success"),
            ("spot_hidden", "extreme", (7, 1), "hard_success", "extreme_success"),
            ("locksmith", "regular", (4, 0), "failure", "regular_success"),
        )
        for ability, difficulty, dice, before, after in cases:
            with self.subTest(ability=ability, difficulty=difficulty):
                with tempfile.TemporaryDirectory() as directory:
                    store, game_id = self._create_session(Path(directory))
                    base = self._valid_check_arguments()
                    base["ability"] = ability
                    base["difficulty"] = difficulty
                    random_source = ScriptedRandom(dice)
                    harness = AgenticHarness(
                        store,
                        ScriptedGameMasterModel(
                            self._base_and_luck_responses(base_arguments=base)
                        ),
                        mechanic_id_factory=iter(
                            ("mechanic_base", "mechanic_luck")
                        ).__next__,
                        random_source=random_source,
                    )

                    self.assertEqual(
                        harness.start_turn(game_id, "我先尝试检定。").status,
                        "committed",
                    )
                    result = harness.start_turn(game_id, "我花费 3 点幸运。")

                    self.assertEqual(result.status, "committed")
                    luck = store.load_session(game_id).session["turns"][1][
                        "mechanics"
                    ][0]
                    self.assertEqual(luck["success_level_before"], before)
                    self.assertEqual(luck["success_level_after"], after)
                    self.assertNotEqual(luck["success_level_after"], "critical_success")
                    self.assertEqual(random_source.calls, [(0, 9), (0, 9)])

    def test_spend_luck_preflight_rejects_invalid_base_points_and_balance(self) -> None:
        """Luck 的来源、适用性、精确支付和余额都在 ID/RNG 前拒绝。"""

        valid = self._valid_check_arguments()
        cases = (
            ("unknown", None, (), "missing", 3, "invalid_check_id"),
            ("success", valid, (0, 7), "mechanic_base", 1, "luck_not_allowed"),
            ("fumble", valid, (0, 0), "mechanic_base", 30, "luck_not_allowed"),
            (
                "npc",
                {**valid, "actor_id": "npc_vespera"},
                (1, 7),
                "mechanic_base",
                1,
                "luck_not_allowed",
            ),
            (
                "hidden",
                {**valid, "visibility": "hidden"},
                (3, 7),
                "mechanic_base",
                3,
                "luck_not_allowed",
            ),
            (
                "zero target",
                {**valid, "ability": "locksmith", "difficulty": "extreme"},
                (2, 0),
                "mechanic_base",
                2,
                "luck_not_allowed",
            ),
            ("too few", valid, (3, 7), "mechanic_base", 2, "invalid_luck_points"),
            ("too many", valid, (3, 7), "mechanic_base", 4, "invalid_luck_points"),
            (
                "insufficient balance",
                {**valid, "ability": "locksmith"},
                (3, 6),
                "mechanic_base",
                62,
                "insufficient_luck",
            ),
        )
        for label, base, dice, check_id, points, expected_code in cases:
            with self.subTest(label=label):
                self._assert_luck_rejected_after_base(
                    base_arguments=base,
                    base_dice=dice,
                    check_id=check_id,
                    points=points,
                    expected_code=expected_code,
                )

    def test_spend_luck_requires_base_check_from_prior_player_turn(self) -> None:
        """同一未完成回合刚产生的失败不能被 GM 自动花费 Luck。"""

        allocated: list[str] = []

        def next_mechanic_id() -> str:
            ids = ("mechanic_base", "mechanic_unexpected")
            value = ids[len(allocated)]
            allocated.append(value)
            return value

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            random_source = ScriptedRandom((3, 7))
            result = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            self._valid_check_arguments(),
                            tool_call_id="call_base",
                        ),
                        self._tool_response(
                            {"check_id": "mechanic_base", "points": 3},
                            name="spend_luck",
                            tool_call_id="call_luck",
                        ),
                        ModelCallError("request_timeout", "stop", retryable=True),
                    ]
                ),
                mechanic_id_factory=next_mechanic_id,
                random_source=random_source,
            ).start_turn(game_id, "我只声明第一次检查牢门。")

            self.assertEqual(result.error_code, "request_timeout")
            self.assertEqual(allocated, ["mechanic_base"])
            self.assertEqual(random_source.calls, [(0, 9), (0, 9)])
            session = store.load_session(game_id).session
            investigator = self._actor(session, "investigator_tracker")
            self.assertEqual(investigator["luck"]["current"], 55)
            incomplete = session["incomplete_turn"]
            self.assertEqual(
                [mechanic["mechanic_id"] for mechanic in incomplete["mechanics"]],
                ["mechanic_base"],
            )
            self.assertEqual(
                incomplete["tool_interactions"][1]["error"]["code"],
                "luck_not_allowed",
            )

    def test_spend_luck_rejects_non_check_and_repeated_derivation(self) -> None:
        """Luck mechanic 不是 check_id，且原失败只能花费一次 Luck。"""

        for attempted_check_id, expected_code in (
            ("mechanic_base", "luck_not_allowed"),
            ("mechanic_luck", "invalid_check_id"),
        ):
            with self.subTest(check_id=attempted_check_id), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                allocated: list[str] = []

                def next_mechanic_id() -> str:
                    ids = ("mechanic_base", "mechanic_luck", "mechanic_unexpected")
                    value = ids[len(allocated)]
                    allocated.append(value)
                    return value

                random_source = ScriptedRandom((3, 7))
                harness = AgenticHarness(
                    store,
                    ScriptedGameMasterModel(
                        self._base_and_luck_responses()
                        + [
                            self._tool_response(
                                {"check_id": attempted_check_id, "points": 3},
                                name="spend_luck",
                            ),
                            ModelCallError("request_timeout", "stop", retryable=True),
                        ]
                    ),
                    mechanic_id_factory=next_mechanic_id,
                    random_source=random_source,
                )

                harness.start_turn(game_id, "我先检查牢门。")
                harness.start_turn(game_id, "我花费 3 点幸运。")
                random_calls_before = list(random_source.calls)
                result = harness.start_turn(game_id, "我试图再次花费幸运。")

                self.assertEqual(result.error_code, "request_timeout")
                self.assertEqual(allocated, ["mechanic_base", "mechanic_luck"])
                self.assertEqual(random_source.calls, random_calls_before)
                session = store.load_session(game_id).session
                investigator = self._actor(session, "investigator_tracker")
                self.assertEqual(investigator["luck"]["current"], 52)
                incomplete = session["incomplete_turn"]
                self.assertEqual(incomplete["mechanics"], [])
                self.assertEqual(
                    incomplete["tool_interactions"][0]["error"]["code"],
                    expected_code,
                )

    def test_player_can_spend_luck_on_two_independent_failed_checks(self) -> None:
        """不同基础检定各自允许一次 Luck，并保持连续余额。"""

        mechanic_ids = iter(
            (
                "mechanic_base_a",
                "mechanic_luck_a",
                "mechanic_base_b",
                "mechanic_luck_b",
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            random_source = ScriptedRandom((3, 7, 4, 7))
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    self._base_and_luck_responses(
                        check_id="mechanic_base_a",
                    )
                    + self._base_and_luck_responses(
                        check_id="mechanic_base_b",
                        points=4,
                    )
                ),
                mechanic_id_factory=mechanic_ids.__next__,
                random_source=random_source,
            )

            for player_input in (
                "我先检查第一处痕迹。",
                "我为第一次检定花费 3 点幸运。",
                "我再检查第二处痕迹。",
                "我为第二次检定花费 4 点幸运。",
            ):
                self.assertEqual(
                    harness.start_turn(game_id, player_input).status,
                    "committed",
                )

            session = store.load_session(game_id).session
            luck_records = [
                mechanic
                for turn in session["turns"]
                for mechanic in turn["mechanics"]
                if mechanic["kind"] == "luck_spend"
            ]
            self.assertEqual(
                [
                    (record["luck_before"], record["luck_after"])
                    for record in luck_records
                ],
                [(55, 52), (52, 48)],
            )
            investigator = self._actor(session, "investigator_tracker")
            self.assertEqual(investigator["luck"]["current"], 48)
            self.assertEqual(random_source.calls, [(0, 9)] * 4)

    def test_spend_luck_rejects_check_with_prior_push_chain(self) -> None:
        """Push 后整条补救链不能再采用 Luck。"""

        final = self._final_payload()
        push = {
            "check_id": "mechanic_base",
            "new_approach": "改从门轴侧面拆卸",
            "failure_stakes": "门轴断裂并夹伤手掌",
        }
        for attempted_check_id in ("mechanic_base", "mechanic_pushed"):
            with self.subTest(check_id=attempted_check_id), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                allocated: list[str] = []

                def next_mechanic_id() -> str:
                    ids = ("mechanic_base", "mechanic_pushed", "mechanic_unexpected")
                    value = ids[len(allocated)]
                    allocated.append(value)
                    return value

                random_source = ScriptedRandom((3, 7, 4, 7))
                harness = AgenticHarness(
                    store,
                    ScriptedGameMasterModel(
                        [
                            self._tool_response(self._valid_check_arguments()),
                            self._response(final),
                            self._tool_response(push, name="push_check"),
                            self._response(final),
                            self._tool_response(
                                {"check_id": attempted_check_id, "points": 3},
                                name="spend_luck",
                            ),
                            ModelCallError("request_timeout", "stop", retryable=True),
                        ]
                    ),
                    mechanic_id_factory=next_mechanic_id,
                    random_source=random_source,
                )

                harness.start_turn(game_id, "我先检查牢门。")
                harness.start_turn(game_id, "我换一种做法孤注一掷。")
                random_calls_before = list(random_source.calls)
                result = harness.start_turn(game_id, "我又尝试花费幸运。")

                self.assertEqual(result.error_code, "request_timeout")
                self.assertEqual(allocated, ["mechanic_base", "mechanic_pushed"])
                self.assertEqual(random_source.calls, random_calls_before)
                session = store.load_session(game_id).session
                investigator = self._actor(session, "investigator_tracker")
                self.assertEqual(investigator["luck"]["current"], 55)
                self.assertEqual(
                    session["incomplete_turn"]["tool_interactions"][0]["error"]["code"],
                    "luck_not_allowed",
                )

    def test_push_rejects_check_with_real_luck_chain_before_id_and_rng(self) -> None:
        """真实 Luck 提交后，原失败不能再被 Push。"""

        allocated: list[str] = []

        def next_mechanic_id() -> str:
            ids = ("mechanic_base", "mechanic_luck", "mechanic_unexpected")
            value = ids[len(allocated)]
            allocated.append(value)
            return value

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            random_source = ScriptedRandom((3, 7))
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    self._base_and_luck_responses()
                    + [
                        self._tool_response(
                            {
                                "check_id": "mechanic_base",
                                "new_approach": "改从门轴侧面拆卸",
                                "failure_stakes": "门轴断裂并夹伤手掌",
                            },
                            name="push_check",
                        ),
                        ModelCallError("request_timeout", "stop", retryable=True),
                    ]
                ),
                mechanic_id_factory=next_mechanic_id,
                random_source=random_source,
            )

            harness.start_turn(game_id, "我先检查牢门。")
            harness.start_turn(game_id, "我花费 3 点幸运。")
            random_calls_before = list(random_source.calls)
            result = harness.start_turn(game_id, "我又尝试孤注一掷。")

            self.assertEqual(result.error_code, "request_timeout")
            self.assertEqual(allocated, ["mechanic_base", "mechanic_luck"])
            self.assertEqual(random_source.calls, random_calls_before)
            session = store.load_session(game_id).session
            investigator = self._actor(session, "investigator_tracker")
            self.assertEqual(investigator["luck"]["current"], 52)
            self.assertEqual(
                session["incomplete_turn"]["tool_interactions"][0]["error"]["code"],
                "push_not_allowed",
            )

    def test_spend_luck_rejects_non_check_mechanic_before_id_and_rng(self) -> None:
        """语义化 check_id 不能引用其他 mechanic kind。"""

        final = self._final_payload("测试机械已经解释。")
        profile = deepseek_model_profile(enabled_tools=("lifecycle_test", "spend_luck"))
        registry = {
            "lifecycle_test": LifecycleTestTool(),
            "spend_luck": SpendLuckTool(),
        }
        allocated: list[str] = []

        def next_mechanic_id() -> str:
            value = "mechanic_non_check" if not allocated else "mechanic_unexpected"
            allocated.append(value)
            return value

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            random_source = ScriptedRandom(())
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            {
                                "actor_id": "investigator_tracker",
                                "amount": 1,
                                "visibility": "public",
                            },
                            name="lifecycle_test",
                        ),
                        self._response(final),
                        self._tool_response(
                            {"check_id": "mechanic_non_check", "points": 1},
                            name="spend_luck",
                        ),
                        ModelCallError("request_timeout", "stop", retryable=True),
                    ]
                ),
                model_profile=profile,
                tool_registry=registry,
                mechanic_id_factory=next_mechanic_id,
                random_source=random_source,
            )

            self.assertEqual(
                harness.start_turn(game_id, "我先使用测试机械。").status,
                "committed",
            )
            result = harness.start_turn(game_id, "我尝试花费幸运改善它。")

            self.assertEqual(result.error_code, "request_timeout")
            self.assertEqual(allocated, ["mechanic_non_check"])
            self.assertEqual(random_source.calls, [])
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(incomplete["mechanics"], [])
            self.assertEqual(
                incomplete["tool_interactions"][0]["error"]["code"],
                "invalid_check_id",
            )

    def test_spend_luck_replays_once_after_interruption_and_restart(self) -> None:
        """Luck 提交后中断只回放同一机械，不重复扣点。"""

        final = self._final_payload("花费幸运后的结果已经解释。")
        luck_arguments = {"check_id": "mechanic_base", "points": 3}
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(self._valid_check_arguments()),
                        self._response(final),
                    ]
                ),
                mechanic_id_factory=lambda: "mechanic_base",
                random_source=ScriptedRandom((3, 7)),
            ).start_turn(game_id, "我先检查牢门。")
            luck_random = ScriptedRandom(())
            interrupted = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            luck_arguments,
                            name="spend_luck",
                            tool_call_id="call_luck",
                        ),
                        ModelCallError("request_timeout", "stop after Luck", retryable=True),
                    ]
                ),
                turn_id_factory=lambda: "turn_luck",
                mechanic_id_factory=lambda: "mechanic_luck",
                random_source=luck_random,
            ).start_turn(game_id, "我花费 3 点幸运。")

            self.assertEqual(interrupted.error_code, "request_timeout")
            self.assertEqual(luck_random.calls, [])
            before_resume = store.load_session(game_id).session
            original_luck = json.loads(
                json.dumps(before_resume["incomplete_turn"]["mechanics"][0])
            )
            investigator = self._actor(before_resume, "investigator_tracker")
            self.assertEqual(investigator["luck"]["current"], 52)
            replay_random = ScriptedRandom(())
            resumed = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            luck_arguments,
                            name="spend_luck",
                            tool_call_id="call_luck",
                        ),
                        self._response(final),
                    ]
                ),
                mechanic_id_factory=lambda: self.fail(
                    "replayed Luck allocated a new mechanic ID"
                ),
                random_source=replay_random,
            ).resume_turn(game_id, "turn_luck")

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(replay_random.calls, [])
            session = store.load_session(game_id).session
            self.assertEqual(session["turns"][1]["mechanics"], [original_luck])
            investigator = self._actor(session, "investigator_tracker")
            self.assertEqual(investigator["luck"]["current"], 52)
            self.assertIsNone(session["incomplete_turn"])

    def test_spend_luck_write_failure_leaves_no_partial_deduction(self) -> None:
        """Luck 原子写失败时不扣点、不保存机械或发布玩家事件。"""

        final = self._final_payload("基础检定已经解释。")
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(self._valid_check_arguments()),
                        self._response(final),
                    ]
                ),
                mechanic_id_factory=lambda: "mechanic_base",
                random_source=ScriptedRandom((3, 7)),
            ).start_turn(game_id, "我先检查牢门。")
            writes = 0

            def fail_luck_write(path: Path, value: object) -> None:
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("injected Luck write failure")
                write_json_atomic(path, value)

            published: list[PublicMechanic] = []
            random_source = ScriptedRandom(())
            result = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            {"check_id": "mechanic_base", "points": 3},
                            name="spend_luck",
                        )
                    ]
                ),
                turn_id_factory=lambda: "turn_luck_write_failure",
                mechanic_id_factory=lambda: "mechanic_uncommitted_luck",
                random_source=random_source,
                session_writer=fail_luck_write,
            ).start_turn(
                game_id,
                "我花费 3 点幸运。",
                public_mechanic_sink=published.append,
            )

            self.assertEqual(result.error_code, "tool_commit_failed")
            self.assertEqual(result.public_mechanics, ())
            self.assertEqual(published, [])
            self.assertEqual(random_source.calls, [])
            session = store.load_session(game_id).session
            investigator = self._actor(session, "investigator_tracker")
            self.assertEqual(investigator["luck"]["current"], 55)
            self.assertEqual(len(session["turns"]), 1)
            incomplete = session["incomplete_turn"]
            self.assertEqual(incomplete["mechanics"], [])
            self.assertEqual(incomplete["tool_interactions"], [])

    def test_loader_rejects_tampered_luck_schema_balance_and_chain(self) -> None:
        """装载时拒绝 Luck 字段、算术、余额、来源、顺序和重复派生篡改。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    self._base_and_luck_responses()
                ),
                mechanic_id_factory=iter(
                    ("mechanic_base", "mechanic_luck")
                ).__next__,
                random_source=ScriptedRandom((3, 7)),
            )
            self.assertEqual(
                harness.start_turn(game_id, "我先检查牢门。").status,
                "committed",
            )
            self.assertEqual(
                harness.start_turn(game_id, "我花费 3 点幸运。").status,
                "committed",
            )
            session_file = store.session_root / game_id / "session.json"
            baseline = read_json(session_file)

            for label in (
                "extra field",
                "broken arithmetic",
                "actor balance",
                "detached source",
                "changed source relationship",
                "reordered chronology",
                "duplicate derivation",
            ):
                with self.subTest(label=label):
                    tampered = json.loads(json.dumps(baseline))
                    luck = tampered["turns"][1]["mechanics"][0]
                    if label == "extra field":
                        luck["unexpected"] = True
                    elif label == "broken arithmetic":
                        luck["luck_after"] = 51
                    elif label == "actor balance":
                        investigator = self._actor(
                            tampered,
                            "investigator_tracker",
                        )
                        investigator["luck"]["current"] = 51
                    elif label == "detached source":
                        luck["check_id"] = "mechanic_missing"
                    elif label == "changed source relationship":
                        tampered["turns"][0]["mechanics"][0]["roll"] = 74
                    elif label == "reordered chronology":
                        tampered["turns"].reverse()
                    else:
                        duplicate = json.loads(json.dumps(luck))
                        duplicate["mechanic_id"] = "mechanic_duplicate_luck"
                        tampered["turns"][1]["mechanics"].append(duplicate)
                    write_json_atomic(session_file, tampered)
                    with self.assertRaisesRegex(
                        AgenticSessionLoadError,
                        "CommittedTurn 格式无效|机械与冻结角色卡不一致",
                    ):
                        store.load_session(game_id)
                    write_json_atomic(session_file, baseline)

    def test_public_investigator_damage_commits_hp_and_thresholds(self) -> None:
        """公开伤害按固定骰值原子更新 HP，并把可信结果续给同一 GM。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            arguments = {
                "actor_id": "investigator_tracker",
                "damage_expression": "1d6+1",
                "cause": "从湿滑石阶跌落并撞上骨钉",
                "armor_applies": True,
                "visibility": "public",
            }
            random_source = ScriptedRandom((4,))
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(arguments, name="deal_damage"),
                    self._response(self._final_payload("我仍有 10 HP。")),
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                mechanic_id_factory=lambda: "mechanic_damage",
                random_source=random_source,
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我踩滑后撞上了突出的骨钉。")

            self.assertEqual(result.status, "committed")
            self.assertEqual(result.narration, "我仍有 10 HP。")
            expected_details = {
                "cause": "从湿滑石阶跌落并撞上骨钉",
                "damage_expression": "1d6+1",
                "rolls": [4],
                "raw_damage": 5,
                "armor_applied": 0,
                "damage_taken": 5,
                "hp_before": 10,
                "hp_after": 5,
                "major_wound": True,
                "unconscious": False,
                "dead": False,
            }
            self.assertEqual(
                result.public_mechanics,
                (
                    PublicMechanic(
                        mechanic_id="mechanic_damage",
                        kind="damage",
                        actor_id="investigator_tracker",
                        details=expected_details,
                    ),
                ),
            )
            session = store.load_session(game_id).session
            self.assertEqual(
                session["turns"][0]["mechanics"][0],
                {
                    "mechanic_id": "mechanic_damage",
                    "kind": "damage",
                    "actor_id": "investigator_tracker",
                    **expected_details,
                    "visibility": "public",
                    "committed_at": "2026-07-27T00:00:00Z",
                },
            )
            investigator = self._actor(session, "investigator_tracker")
            self.assertEqual(investigator["hp"], {"current": 5, "max": 10})
            tool_result = json.loads(model.requests[1].messages[-1]["content"])
            self.assertEqual(tool_result["result"]["damage_taken"], 5)
            self.assertEqual(tool_result["result"]["hp_after"], 5)
            self.assertEqual(random_source.calls, [(1, 6)])

    def test_damage_preflight_rejects_invalid_inputs_before_id_rng_and_hp(self) -> None:
        """伤害 schema、角色、可见性与表达式边界都在执行前拒绝。"""

        valid = {
            "actor_id": "investigator_tracker",
            "damage_expression": "1d6+1",
            "cause": "从湿滑石阶跌落",
            "armor_applies": True,
            "visibility": "public",
        }
        cases = (
            ("unknown actor", {**valid, "actor_id": "actor_missing"}, "unknown_actor"),
            ("submitted hp result", {**valid, "hp_after": 0}, "invalid_arguments"),
            ("integer armor flag", {**valid, "armor_applies": 1}, "invalid_arguments"),
            ("empty cause", {**valid, "cause": ""}, "invalid_arguments"),
            ("non-string expression", {**valid, "damage_expression": 6}, "invalid_arguments"),
            ("unknown visibility", {**valid, "visibility": "secret"}, "invalid_arguments"),
            ("hidden investigator", {**valid, "visibility": "hidden"}, "invalid_visibility"),
            ("uppercase dice", {**valid, "damage_expression": "1D6"}, "invalid_dice_expression"),
            ("leading whitespace", {**valid, "damage_expression": " 1d6"}, "invalid_arguments"),
            ("leading zero", {**valid, "damage_expression": "01"}, "invalid_dice_expression"),
            ("negative fixed", {**valid, "damage_expression": "-1"}, "invalid_dice_expression"),
            ("zero dice", {**valid, "damage_expression": "0d6"}, "invalid_dice_expression"),
            ("zero sides", {**valid, "damage_expression": "1d0"}, "invalid_dice_expression"),
            ("too many dice", {**valid, "damage_expression": "21d1"}, "invalid_dice_expression"),
            ("too many sides", {**valid, "damage_expression": "1d101"}, "invalid_dice_expression"),
            ("negative theoretical minimum", {**valid, "damage_expression": "1d6-2"}, "invalid_dice_expression"),
            ("high theoretical maximum", {**valid, "damage_expression": "1d100+1"}, "invalid_dice_expression"),
            ("high fixed value", {**valid, "damage_expression": "101"}, "invalid_dice_expression"),
        )
        for label, arguments, expected_code in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    store, game_id = self._create_session(Path(directory))
                    allocated: list[str] = []
                    random_source = ScriptedRandom(())
                    harness = AgenticHarness(
                        store,
                        ScriptedGameMasterModel(
                            [
                                self._tool_response(arguments, name="deal_damage"),
                                ModelCallError(
                                    "request_timeout",
                                    "stop after damage rejection",
                                    retryable=True,
                                ),
                            ]
                        ),
                        mechanic_id_factory=lambda: allocated.append("allocated") or "unexpected",
                        random_source=random_source,
                    )

                    result = harness.start_turn(game_id, "虚构中已经发生伤害。")

                    self.assertEqual(result.error_code, "request_timeout")
                    self.assertEqual(allocated, [])
                    self.assertEqual(random_source.calls, [])
                    session = store.load_session(game_id).session
                    self.assertEqual(
                        self._actor(session, "investigator_tracker")["hp"]["current"],
                        10,
                    )
                    incomplete = session["incomplete_turn"]
                    self.assertEqual(incomplete["mechanics"], [])
                    self.assertEqual(
                        incomplete["tool_interactions"][0]["error"]["code"],
                        expected_code,
                    )

    def test_npc_damage_respects_frozen_armor_and_hidden_projection(self) -> None:
        """NPC 伤害按事前护甲参数结算，隐藏结果不产生玩家投影。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, game_id = self._create_session_with_actor_armor(
                root,
                actor_id="npc_aranis",
                armor=3,
            )
            arguments = {
                "actor_id": "npc_aranis",
                "damage_expression": "2d6+1",
                "cause": "倒塌的石梁擦中肩背",
                "armor_applies": True,
                "visibility": "hidden",
            }
            unarmored_arguments = {
                "actor_id": "npc_aranis",
                "damage_expression": "2",
                "cause": "护甲未覆盖的手掌被划伤",
                "armor_applies": False,
                "visibility": "public",
            }
            absorbed_arguments = {
                "actor_id": "npc_aranis",
                "damage_expression": "2",
                "cause": "轻微撞击被护甲完全挡住",
                "armor_applies": True,
                "visibility": "public",
            }
            random_source = ScriptedRandom((2, 4))
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(arguments, name="deal_damage"),
                    self._response(self._final_payload("GM 忠实承接了隐藏伤害。")),
                    self._tool_response(
                        unarmored_arguments,
                        name="deal_damage",
                        tool_call_id="call_public_damage",
                    ),
                    self._response(self._final_payload("公开伤害已经解释。")),
                    self._tool_response(
                        absorbed_arguments,
                        name="deal_damage",
                        tool_call_id="call_absorbed_damage",
                    ),
                    self._response(self._final_payload("护甲挡住了伤害。")),
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                mechanic_id_factory=iter(
                    (
                        "mechanic_hidden_damage",
                        "mechanic_public_damage",
                        "mechanic_absorbed_damage",
                    )
                ).__next__,
                random_source=random_source,
            )

            result = harness.start_turn(game_id, "石梁已经在她身后坍塌。")

            self.assertEqual(result.status, "committed")
            self.assertEqual(result.public_mechanics, ())
            session = store.load_session(game_id).session
            damage = session["turns"][0]["mechanics"][0]
            self.assertEqual(damage["rolls"], [2, 4])
            self.assertEqual(damage["raw_damage"], 7)
            self.assertEqual(damage["armor_applied"], 3)
            self.assertEqual(damage["damage_taken"], 4)
            self.assertEqual(damage["hp_before"], 9)
            self.assertEqual(damage["hp_after"], 5)
            self.assertFalse(damage["major_wound"])
            self.assertFalse(damage["unconscious"])
            self.assertFalse(damage["dead"])
            self.assertEqual(damage["visibility"], "hidden")
            self.assertEqual(
                self._actor(session, "npc_aranis")["hp"],
                {"current": 5, "max": 12},
            )
            tool_result = json.loads(model.requests[1].messages[-1]["content"])
            self.assertEqual(tool_result["result"], damage)

            second = harness.start_turn(
                game_id,
                "随后她未被护甲覆盖的手掌遭到划伤。",
            )

            self.assertEqual(second.status, "committed")
            self.assertEqual(len(second.public_mechanics), 1)
            session = store.load_session(game_id).session
            public_damage = session["turns"][1]["mechanics"][0]
            self.assertEqual(public_damage["raw_damage"], 2)
            self.assertEqual(public_damage["armor_applied"], 0)
            self.assertEqual(public_damage["damage_taken"], 2)
            self.assertEqual(public_damage["hp_before"], 5)
            self.assertEqual(public_damage["hp_after"], 3)
            self.assertEqual(
                self._actor(session, "npc_aranis")["hp"],
                {"current": 3, "max": 12},
            )

            third = harness.start_turn(
                game_id,
                "又一次轻微撞击被她的护甲完全挡住。",
            )

            self.assertEqual(third.status, "committed")
            self.assertEqual(len(third.public_mechanics), 1)
            session = store.load_session(game_id).session
            absorbed = session["turns"][2]["mechanics"][0]
            self.assertEqual(absorbed["raw_damage"], 2)
            self.assertEqual(absorbed["armor_applied"], 2)
            self.assertEqual(absorbed["damage_taken"], 0)
            self.assertEqual(absorbed["hp_before"], 3)
            self.assertEqual(absorbed["hp_after"], 3)
            self.assertEqual(
                self._actor(session, "npc_aranis")["hp"],
                {"current": 3, "max": 12},
            )
            self.assertEqual(random_source.calls, [(1, 6), (1, 6)])

    def test_damage_zero_and_zero_hp_threshold_boundaries(self) -> None:
        """零伤害仍提交；零 HP 时昏迷与死亡按 max HP 边界互斥。"""

        cases = (
            ("zero", "investigator_tracker", "0", 10, False, False, False),
            ("unconscious", "npc_aranis", "9", 0, True, True, False),
            ("dead", "investigator_tracker", "10", 0, True, False, True),
        )
        for label, actor_id, expression, hp_after, major, unconscious, dead in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    run = self._run_damage_case(
                        Path(directory),
                        arguments={
                            "actor_id": actor_id,
                            "damage_expression": expression,
                            "cause": "固定伤害边界",
                            "armor_applies": False,
                            "visibility": "public",
                        },
                        dice=(),
                        mechanic_id=f"mechanic_{label}",
                    )
                    damage = run.session["turns"][0]["mechanics"][0]
                    self.assertEqual(damage["rolls"], [])
                    self.assertEqual(damage["raw_damage"], int(expression))
                    self.assertEqual(damage["armor_applied"], 0)
                    self.assertEqual(damage["damage_taken"], int(expression))
                    self.assertEqual(damage["hp_after"], hp_after)
                    self.assertIs(damage["major_wound"], major)
                    self.assertIs(damage["unconscious"], unconscious)
                    self.assertIs(damage["dead"], dead)
                    self.assertEqual(
                        self._actor(run.session, actor_id)["hp"]["current"],
                        hp_after,
                    )
                    self.assertEqual(len(run.result.public_mechanics), 1)
                    self.assertEqual(run.random_source.calls, [])

    def test_damage_expression_accepts_inclusive_dice_and_range_boundaries(self) -> None:
        """共享表达式接受 N=20、M=100 与理论最小值为零的边界。"""

        cases = (
            ("20d5", (1,) * 20, 20, [(1, 5)] * 20),
            ("1d100", (100,), 100, [(1, 100)]),
            ("2d6-2", (1, 1), 0, [(1, 6), (1, 6)]),
        )
        for expression, dice, raw_damage, expected_calls in cases:
            with self.subTest(expression=expression):
                with tempfile.TemporaryDirectory() as directory:
                    run = self._run_damage_case(
                        Path(directory),
                        arguments={
                            "actor_id": "investigator_tracker",
                            "damage_expression": expression,
                            "cause": "表达式合法边界",
                            "armor_applies": False,
                            "visibility": "public",
                        },
                        dice=dice,
                        mechanic_id="mechanic_damage_boundary",
                    )
                    damage = run.session["turns"][0][
                        "mechanics"
                    ][0]
                    self.assertEqual(damage["raw_damage"], raw_damage)
                    self.assertEqual(run.random_source.calls, expected_calls)

    def test_major_wound_uses_ceiling_for_odd_max_hp(self) -> None:
        """max HP 为 11 时，5 点不重伤而 6 点达到重伤阈值。"""

        for damage_taken, expected_major in ((5, False), (6, True)):
            with self.subTest(damage_taken=damage_taken):
                with tempfile.TemporaryDirectory() as directory:
                    run = self._run_damage_case(
                        Path(directory),
                        arguments={
                            "actor_id": "investigator_mediator",
                            "damage_expression": str(damage_taken),
                            "cause": "奇数 HP 重伤边界",
                            "armor_applies": False,
                            "visibility": "public",
                        },
                        dice=(),
                        mechanic_id=f"mechanic_odd_hp_{damage_taken}",
                        investigator_id="investigator_mediator",
                    )
                    damage = run.session["turns"][0]["mechanics"][0]
                    self.assertEqual(damage["hp_before"], 11)
                    self.assertIs(damage["major_wound"], expected_major)

    def test_damage_replays_once_after_interruption_and_restart(self) -> None:
        """伤害提交后多次恢复只回放同一机械，不重复掷骰或扣 HP。"""

        arguments = {
            "actor_id": "investigator_tracker",
            "damage_expression": "1d6+1",
            "cause": "石阶跌落",
            "armor_applies": False,
            "visibility": "public",
        }
        final = self._final_payload("伤害结果已经解释。")
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            first_random = ScriptedRandom((4,))
            interrupted = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            arguments,
                            name="deal_damage",
                            tool_call_id="call_damage",
                        ),
                        ModelCallError(
                            "request_timeout",
                            "stop after damage",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_damage",
                mechanic_id_factory=lambda: "mechanic_damage",
                random_source=first_random,
            ).start_turn(game_id, "我从石阶跌落。")

            self.assertEqual(interrupted.error_code, "request_timeout")
            self.assertEqual(first_random.calls, [(1, 6)])
            before_resume = store.load_session(game_id).session
            original_damage = json.loads(
                json.dumps(before_resume["incomplete_turn"]["mechanics"][0])
            )
            self.assertEqual(
                self._actor(before_resume, "investigator_tracker")["hp"]["current"],
                5,
            )
            first_replay_random = ScriptedRandom(())
            first_resume = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            arguments,
                            name="deal_damage",
                            tool_call_id="call_damage",
                        ),
                        ModelCallError(
                            "request_timeout",
                            "stop after first resume",
                            retryable=True,
                        ),
                    ]
                ),
                mechanic_id_factory=lambda: self.fail(
                    "first replay allocated a new mechanic ID"
                ),
                random_source=first_replay_random,
            ).resume_turn(game_id, "turn_damage")

            self.assertEqual(first_resume.error_code, "request_timeout")
            self.assertEqual(first_replay_random.calls, [])
            after_first_resume = store.load_session(game_id).session
            self.assertEqual(
                after_first_resume["incomplete_turn"]["mechanics"],
                [original_damage],
            )
            self.assertEqual(
                self._actor(
                    after_first_resume,
                    "investigator_tracker",
                )["hp"]["current"],
                5,
            )
            second_replay_random = ScriptedRandom(())
            resumed = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            arguments,
                            name="deal_damage",
                            tool_call_id="call_damage",
                        ),
                        self._response(final),
                    ]
                ),
                mechanic_id_factory=lambda: self.fail(
                    "second replay allocated a new mechanic ID"
                ),
                random_source=second_replay_random,
            ).resume_turn(game_id, "turn_damage")

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(second_replay_random.calls, [])
            session = store.load_session(game_id).session
            self.assertEqual(session["turns"][0]["mechanics"], [original_damage])
            self.assertEqual(
                self._actor(session, "investigator_tracker")["hp"]["current"],
                5,
            )
            self.assertIsNone(session["incomplete_turn"])

    def test_damage_write_failure_leaves_no_partial_hp_or_mechanic(self) -> None:
        """伤害原子写失败时不扣 HP、不保存机械或发布玩家事件。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            writes = 0

            def fail_damage_write(path: Path, value: object) -> None:
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("injected damage write failure")
                write_json_atomic(path, value)

            published: list[PublicMechanic] = []
            random_source = ScriptedRandom((4,))
            result = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            {
                                "actor_id": "investigator_tracker",
                                "damage_expression": "1d6+1",
                                "cause": "石阶跌落",
                                "armor_applies": False,
                                "visibility": "public",
                            },
                            name="deal_damage",
                        )
                    ]
                ),
                turn_id_factory=lambda: "turn_damage_write_failure",
                mechanic_id_factory=lambda: "mechanic_uncommitted_damage",
                random_source=random_source,
                session_writer=fail_damage_write,
            ).start_turn(
                game_id,
                "我从石阶跌落。",
                public_mechanic_sink=published.append,
            )

            self.assertEqual(result.error_code, "tool_commit_failed")
            self.assertEqual(result.public_mechanics, ())
            self.assertEqual(published, [])
            self.assertEqual(random_source.calls, [(1, 6)])
            session = store.load_session(game_id).session
            self.assertEqual(
                self._actor(session, "investigator_tracker")["hp"]["current"],
                10,
            )
            incomplete = session["incomplete_turn"]
            self.assertEqual(incomplete["mechanics"], [])
            self.assertEqual(incomplete["tool_interactions"], [])

    def test_loader_rejects_tampered_damage_schema_math_actor_and_visibility(self) -> None:
        """装载时拒绝伤害 schema、算术、角色余额、阈值和公开性篡改。"""

        with tempfile.TemporaryDirectory() as directory:
            run = self._run_damage_case(
                Path(directory),
                arguments={
                    "actor_id": "investigator_tracker",
                    "damage_expression": "1d6+1",
                    "cause": "石阶跌落",
                    "armor_applies": False,
                    "visibility": "public",
                },
                dice=(4,),
                mechanic_id="mechanic_damage",
            )
            session_file = run.store.session_root / run.game_id / "session.json"
            baseline = read_json(session_file)

            for label in (
                "extra field",
                "invalid roll",
                "broken raw damage",
                "forged armor",
                "broken hp arithmetic",
                "actor hp balance",
                "wrong threshold",
                "hidden investigator",
                "boolean numeric",
            ):
                with self.subTest(label=label):
                    tampered = json.loads(json.dumps(baseline))
                    damage = tampered["turns"][0]["mechanics"][0]
                    if label == "extra field":
                        damage["unexpected"] = True
                    elif label == "invalid roll":
                        damage["rolls"] = [7]
                    elif label == "broken raw damage":
                        damage["raw_damage"] = 6
                    elif label == "forged armor":
                        damage["armor_applied"] = 1
                        damage["damage_taken"] = 4
                        damage["hp_after"] = 6
                        damage["major_wound"] = False
                        self._actor(
                            tampered,
                            "investigator_tracker",
                        )["hp"]["current"] = 6
                    elif label == "broken hp arithmetic":
                        damage["hp_after"] = 4
                    elif label == "actor hp balance":
                        self._actor(
                            tampered,
                            "investigator_tracker",
                        )["hp"]["current"] = 4
                    elif label == "wrong threshold":
                        damage["major_wound"] = False
                    elif label == "hidden investigator":
                        damage["visibility"] = "hidden"
                    else:
                        damage["raw_damage"] = True
                    write_json_atomic(session_file, tampered)
                    with self.assertRaisesRegex(
                        AgenticSessionLoadError,
                        "CommittedTurn 格式无效|机械与冻结角色卡不一致",
                    ):
                        run.store.load_session(run.game_id)
                    write_json_atomic(session_file, baseline)

    def test_incomplete_damage_binds_armor_applicability_to_frozen_actor(self) -> None:
        """未完成回合不能把适用护甲的调用改成未减伤结果。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, game_id = self._create_session_with_actor_armor(
                root,
                actor_id="npc_aranis",
                armor=3,
            )
            arguments = {
                "actor_id": "npc_aranis",
                "damage_expression": "2d6+1",
                "cause": "倒塌的石梁擦中肩背",
                "armor_applies": True,
                "visibility": "hidden",
            }
            result = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments, name="deal_damage"),
                        ModelCallError(
                            "request_timeout",
                            "stop after committed damage",
                            retryable=True,
                        ),
                    ]
                ),
                mechanic_id_factory=lambda: "mechanic_damage",
                random_source=ScriptedRandom((2, 4)),
            ).start_turn(game_id, "石梁已经在她身后坍塌。")
            self.assertEqual(result.error_code, "request_timeout")
            session_file = store.session_root / game_id / "session.json"
            tampered = read_json(session_file)
            incomplete = tampered["incomplete_turn"]
            damage = incomplete["mechanics"][0]
            damage["armor_applied"] = 0
            damage["damage_taken"] = 7
            damage["hp_after"] = 2
            damage["major_wound"] = True
            interaction = incomplete["tool_interactions"][0]
            interaction["result"] = json.loads(json.dumps(damage))
            actor = self._actor(tampered, "npc_aranis")
            actor["hp"]["current"] = 2
            tool_message = next(
                message
                for message in incomplete["deepseek_messages"]
                if message.get("role") == "tool"
            )
            envelope = json.loads(tool_message["content"])
            envelope["result"] = json.loads(json.dumps(damage))
            tool_message["content"] = json.dumps(
                envelope,
                ensure_ascii=False,
                sort_keys=True,
            )
            write_json_atomic(session_file, tampered)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "ToolInteraction 格式无效",
            ):
                store.load_session(game_id)

    def test_public_failed_sanity_check_commits_selected_loss_and_thresholds(self) -> None:
        """公开理智失败只抽失败损失，并原子更新 SAN 与本局累计。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            arguments = {
                "actor_id": "investigator_tracker",
                "source": "积水倒影中出现了苍白侧脸",
                "success_loss": "0",
                "failure_loss": "1d6",
                "visibility": "public",
            }
            random_source = ScriptedRandom((1, 7, 4))
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(arguments, name="make_sanity_check"),
                    self._response(self._final_payload("我的 SAN 仍是 65。")),
                ]
            )
            result = AgenticHarness(
                store,
                model,
                mechanic_id_factory=lambda: "mechanic_sanity",
                random_source=random_source,
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            ).start_turn(game_id, "倒影中的脸让我意识到它并不属于任何人。")

            self.assertEqual(result.status, "committed")
            self.assertEqual(result.narration, "我的 SAN 仍是 65。")
            expected_details = {
                "source": "积水倒影中出现了苍白侧脸",
                "roll": 71,
                "target": 65,
                "outcome": "failure",
                "loss_expression": "1d6",
                "loss_rolls": [4],
                "san_loss": 4,
                "san_before": 65,
                "san_after": 61,
                "session_san_loss": 4,
                "temporary_insanity_threshold_reached": False,
                "indefinite_insanity_threshold_crossed": False,
            }
            self.assertEqual(
                result.public_mechanics,
                (
                    PublicMechanic(
                        mechanic_id="mechanic_sanity",
                        kind="sanity_check",
                        actor_id="investigator_tracker",
                        details=expected_details,
                    ),
                ),
            )
            session = store.load_session(game_id).session
            self.assertEqual(
                session["turns"][0]["mechanics"][0],
                {
                    "mechanic_id": "mechanic_sanity",
                    "kind": "sanity_check",
                    "actor_id": "investigator_tracker",
                    **expected_details,
                    "visibility": "public",
                    "committed_at": "2026-07-27T00:00:00Z",
                },
            )
            self.assertEqual(
                self._actor(session, "investigator_tracker")["san"],
                {"current": 61, "max": 65, "session_loss": 4},
            )
            tool_result = json.loads(model.requests[1].messages[-1]["content"])
            self.assertEqual(tool_result["result"]["san_after"], 61)
            self.assertEqual(tool_result["result"]["session_san_loss"], 4)
            self.assertEqual(
                random_source.calls,
                [(0, 9), (0, 9), (1, 6)],
            )

    def test_successful_sanity_check_uses_only_fixed_success_loss(self) -> None:
        """roll 等于 SAN 时成功，固定零损失不抽取未选中的失败骰。"""

        with tempfile.TemporaryDirectory() as directory:
            run = self._run_sanity_case(
                Path(directory),
                arguments={
                    "actor_id": "investigator_tracker",
                    "source": "倒影短暂露出不自然的侧脸",
                    "success_loss": "0",
                    "failure_loss": "1d6",
                    "visibility": "public",
                },
                dice=(5, 6),
                mechanic_id="mechanic_sanity_success",
            )
            sanity = run.session["turns"][0]["mechanics"][0]
            self.assertEqual(sanity["roll"], 65)
            self.assertEqual(sanity["target"], 65)
            self.assertEqual(sanity["outcome"], "success")
            self.assertEqual(sanity["loss_expression"], "0")
            self.assertEqual(sanity["loss_rolls"], [])
            self.assertEqual(sanity["san_loss"], 0)
            self.assertEqual(sanity["san_after"], 65)
            self.assertEqual(sanity["session_san_loss"], 0)
            self.assertFalse(sanity["temporary_insanity_threshold_reached"])
            self.assertFalse(sanity["indefinite_insanity_threshold_crossed"])
            self.assertEqual(run.random_source.calls, [(0, 9), (0, 9)])

    def test_hidden_npc_sanity_updates_frozen_actor_without_player_projection(
        self,
    ) -> None:
        """NPC 可事前隐藏 SAN 机械，完整结果仅供 GM 延续虚构。"""

        with tempfile.TemporaryDirectory() as directory:
            run = self._run_sanity_case(
                Path(directory),
                arguments={
                    "actor_id": "npc_aranis",
                    "source": "她独自认出了水下倒影的真实身份",
                    "success_loss": "0",
                    "failure_loss": "2",
                    "visibility": "hidden",
                },
                dice=(6, 5),
                mechanic_id="mechanic_hidden_sanity",
                narration="她没有解释突然的沉默。",
            )
            self.assertEqual(run.result.public_mechanics, ())
            sanity = run.session["turns"][0]["mechanics"][0]
            self.assertEqual(sanity["actor_id"], "npc_aranis")
            self.assertEqual(sanity["roll"], 56)
            self.assertEqual(sanity["target"], 55)
            self.assertEqual(sanity["outcome"], "failure")
            self.assertEqual(sanity["san_loss"], 2)
            self.assertEqual(sanity["san_after"], 53)
            self.assertEqual(sanity["session_san_loss"], 2)
            self.assertEqual(sanity["visibility"], "hidden")
            self.assertEqual(
                self._actor(run.session, "npc_aranis")["san"],
                {"current": 53, "max": 55, "session_loss": 2},
            )
            tool_result = json.loads(
                run.model.requests[1].messages[-1]["content"]
            )
            self.assertEqual(tool_result["result"], sanity)

    def test_sanity_thresholds_trigger_only_on_loss_and_first_crossing(self) -> None:
        """临时阈值按单次损失，不定阈值只在本局累计首次跨越时触发。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            responses: list[ModelResponse] = []
            for index, loss in enumerate((12, 1, 1), start=1):
                responses.extend(
                    [
                        self._tool_response(
                            {
                                "actor_id": "investigator_tracker",
                                "source": f"连续恐怖来源 {index}",
                                "success_loss": "0",
                                "failure_loss": str(loss),
                                "visibility": "public",
                            },
                            name="make_sanity_check",
                            tool_call_id=f"call_sanity_{index}",
                        ),
                        self._response(self._final_payload()),
                    ]
                )
            random_source = ScriptedRandom((1, 7, 9, 9, 9, 9))
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(responses),
                mechanic_id_factory=iter(
                    ("mechanic_sanity_1", "mechanic_sanity_2", "mechanic_sanity_3")
                ).__next__,
                random_source=random_source,
            )

            for index in range(1, 4):
                self.assertEqual(
                    harness.start_turn(game_id, f"面对第 {index} 个恐怖来源。").status,
                    "committed",
                )

            session = store.load_session(game_id).session
            mechanics = [turn["mechanics"][0] for turn in session["turns"]]
            self.assertEqual(
                [mechanic["san_before"] for mechanic in mechanics],
                [65, 53, 52],
            )
            self.assertEqual(
                [mechanic["session_san_loss"] for mechanic in mechanics],
                [12, 13, 14],
            )
            self.assertEqual(
                [
                    mechanic["temporary_insanity_threshold_reached"]
                    for mechanic in mechanics
                ],
                [True, False, False],
            )
            self.assertEqual(
                [
                    mechanic["indefinite_insanity_threshold_crossed"]
                    for mechanic in mechanics
                ],
                [False, True, False],
            )
            self.assertEqual(
                self._actor(session, "investigator_tracker")["san"],
                {"current": 51, "max": 65, "session_loss": 14},
            )
            self.assertEqual(random_source.calls, [(0, 9)] * 6)

    def test_sanity_threshold_boundaries_use_exact_five_and_ceiling(self) -> None:
        """临时阈值含 5；非整除本局起始 SAN 使用向上取整。"""

        with tempfile.TemporaryDirectory() as directory:
            exact_five = self._run_sanity_case(
                Path(directory),
                arguments={
                    "actor_id": "investigator_tracker",
                    "source": "单次损失恰好达到临时阈值",
                    "success_loss": "0",
                    "failure_loss": "5",
                    "visibility": "public",
                },
                dice=(1, 7),
                mechanic_id="mechanic_sanity_exact_five",
            )
            mechanic = exact_five.session["turns"][0]["mechanics"][0]
            self.assertEqual(mechanic["san_loss"], 5)
            self.assertTrue(mechanic["temporary_insanity_threshold_reached"])

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            session_file = store.session_root / game_id / "session.json"
            seeded = read_json(session_file)
            self._actor(seeded, "investigator_tracker")["san"].update(
                {"current": 56, "session_loss": 10}
            )
            write_json_atomic(session_file, seeded)
            self.assertEqual(
                self._actor(
                    store.load_session(game_id).session,
                    "investigator_tracker",
                )["san"],
                {"current": 56, "max": 65, "session_loss": 10},
            )

            responses: list[ModelResponse] = []
            for index, loss in enumerate((3, 1), start=1):
                responses.extend(
                    [
                        self._tool_response(
                            {
                                "actor_id": "investigator_tracker",
                                "source": f"向上取整阈值来源 {index}",
                                "success_loss": "0",
                                "failure_loss": str(loss),
                                "visibility": "public",
                            },
                            name="make_sanity_check",
                            tool_call_id=f"call_sanity_ceiling_{index}",
                        ),
                        self._response(self._final_payload()),
                    ]
                )
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(responses),
                mechanic_id_factory=iter(
                    ("mechanic_sanity_ceiling_1", "mechanic_sanity_ceiling_2")
                ).__next__,
                random_source=ScriptedRandom((1, 7, 1, 7)),
            )

            self.assertEqual(
                harness.start_turn(game_id, "面对第一处恐怖。").status,
                "committed",
            )
            self.assertEqual(
                harness.start_turn(game_id, "面对第二处恐怖。").status,
                "committed",
            )
            mechanics = [
                turn["mechanics"][0]
                for turn in store.load_session(game_id).session["turns"]
            ]
            self.assertEqual(
                [item["session_san_loss"] for item in mechanics],
                [13, 14],
            )
            self.assertEqual(
                [item["indefinite_insanity_threshold_crossed"] for item in mechanics],
                [False, True],
            )

    def test_sanity_loss_is_capped_at_current_san(self) -> None:
        """原始损失超过当前 SAN 时截断到零，不让 SAN 或累计值越界。"""

        with tempfile.TemporaryDirectory() as directory:
            run = self._run_sanity_case(
                Path(directory),
                arguments={
                    "actor_id": "investigator_tracker",
                    "source": "无法承受的恐怖真相",
                    "success_loss": "0",
                    "failure_loss": "100",
                    "visibility": "public",
                },
                dice=(0, 0),
                mechanic_id="mechanic_sanity_cap",
            )
            sanity = run.session["turns"][0]["mechanics"][0]
            self.assertEqual(sanity["roll"], 100)
            self.assertEqual(sanity["san_loss"], 65)
            self.assertEqual(sanity["san_after"], 0)
            self.assertEqual(sanity["session_san_loss"], 65)
            self.assertTrue(sanity["temporary_insanity_threshold_reached"])
            self.assertTrue(sanity["indefinite_insanity_threshold_crossed"])
            self.assertEqual(
                self._actor(run.session, "investigator_tracker")["san"],
                {"current": 0, "max": 65, "session_loss": 65},
            )
            self.assertEqual(run.random_source.calls, [(0, 9), (0, 9)])

    def test_sanity_write_failure_leaves_no_partial_loss_or_mechanic(self) -> None:
        """理智原子写失败时不扣 SAN、不保存机械或发布玩家事件。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            writes = 0

            def fail_sanity_write(path: Path, value: object) -> None:
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("injected sanity write failure")
                write_json_atomic(path, value)

            published: list[PublicMechanic] = []
            random_source = ScriptedRandom((1, 7, 4))
            result = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            {
                                "actor_id": "investigator_tracker",
                                "source": "积水倒影中出现了苍白侧脸",
                                "success_loss": "0",
                                "failure_loss": "1d6",
                                "visibility": "public",
                            },
                            name="make_sanity_check",
                        )
                    ]
                ),
                turn_id_factory=lambda: "turn_sanity_write_failure",
                mechanic_id_factory=lambda: "mechanic_uncommitted_sanity",
                random_source=random_source,
                session_writer=fail_sanity_write,
            ).start_turn(
                game_id,
                "倒影中的脸让我意识到它并不属于任何人。",
                public_mechanic_sink=published.append,
            )

            self.assertEqual(result.error_code, "tool_commit_failed")
            self.assertEqual(result.public_mechanics, ())
            self.assertEqual(published, [])
            self.assertEqual(random_source.calls, [(0, 9), (0, 9), (1, 6)])
            session = store.load_session(game_id).session
            self.assertEqual(
                self._actor(session, "investigator_tracker")["san"],
                {"current": 65, "max": 65, "session_loss": 0},
            )
            incomplete = session["incomplete_turn"]
            self.assertEqual(incomplete["mechanics"], [])
            self.assertEqual(incomplete["tool_interactions"], [])

    def test_sanity_replays_once_after_interruption_and_repeated_recovery(
        self,
    ) -> None:
        """SAN 提交后反复恢复只回放同一机械，不重掷或重复扣减。"""

        arguments = {
            "actor_id": "investigator_tracker",
            "source": "积水倒影中出现了苍白侧脸",
            "success_loss": "0",
            "failure_loss": "1d6+1",
            "visibility": "public",
        }
        final = self._final_payload("理智结果已经解释。")
        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            first_random = ScriptedRandom((1, 7, 4))
            interrupted = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            arguments,
                            name="make_sanity_check",
                            tool_call_id="call_sanity",
                        ),
                        ModelCallError(
                            "request_timeout",
                            "stop after sanity",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_sanity",
                mechanic_id_factory=lambda: "mechanic_sanity",
                random_source=first_random,
            ).start_turn(game_id, "我直视积水里的苍白侧脸。")

            self.assertEqual(interrupted.error_code, "request_timeout")
            self.assertEqual(first_random.calls, [(0, 9), (0, 9), (1, 6)])
            before_resume = store.load_session(game_id).session
            original_sanity = json.loads(
                json.dumps(before_resume["incomplete_turn"]["mechanics"][0])
            )
            self.assertEqual(
                self._actor(before_resume, "investigator_tracker")["san"],
                {"current": 60, "max": 65, "session_loss": 5},
            )

            first_replay_random = ScriptedRandom(())
            first_resume = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            arguments,
                            name="make_sanity_check",
                            tool_call_id="call_sanity",
                        ),
                        ModelCallError(
                            "request_timeout",
                            "stop after first resume",
                            retryable=True,
                        ),
                    ]
                ),
                mechanic_id_factory=lambda: self.fail(
                    "first SAN replay allocated a new mechanic ID"
                ),
                random_source=first_replay_random,
            ).resume_turn(game_id, "turn_sanity")

            self.assertEqual(first_resume.error_code, "request_timeout")
            self.assertEqual(first_replay_random.calls, [])
            after_first_resume = store.load_session(game_id).session
            self.assertEqual(
                after_first_resume["incomplete_turn"]["mechanics"],
                [original_sanity],
            )
            self.assertEqual(
                self._actor(after_first_resume, "investigator_tracker")["san"],
                {"current": 60, "max": 65, "session_loss": 5},
            )

            second_replay_random = ScriptedRandom(())
            resumed = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            arguments,
                            name="make_sanity_check",
                            tool_call_id="call_sanity",
                        ),
                        self._response(final),
                    ]
                ),
                mechanic_id_factory=lambda: self.fail(
                    "second SAN replay allocated a new mechanic ID"
                ),
                random_source=second_replay_random,
            ).resume_turn(game_id, "turn_sanity")

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(second_replay_random.calls, [])
            session = store.load_session(game_id).session
            self.assertEqual(session["turns"][0]["mechanics"], [original_sanity])
            self.assertEqual(
                self._actor(session, "investigator_tracker")["san"],
                {"current": 60, "max": 65, "session_loss": 5},
            )
            self.assertIsNone(session["incomplete_turn"])

    def test_loader_rejects_tampered_sanity_schema_math_actor_and_visibility(
        self,
    ) -> None:
        """装载时拒绝 SAN schema、算术、角色余额、阈值和公开性篡改。"""

        with tempfile.TemporaryDirectory() as directory:
            run = self._run_sanity_case(
                Path(directory),
                arguments={
                    "actor_id": "investigator_tracker",
                    "source": "积水倒影中出现了苍白侧脸",
                    "success_loss": "0",
                    "failure_loss": "1d6",
                    "visibility": "public",
                },
                dice=(1, 7, 4),
                mechanic_id="mechanic_sanity",
            )
            session_file = run.store.session_root / run.game_id / "session.json"
            baseline = read_json(session_file)

            for label in (
                "extra field",
                "invalid loss roll",
                "broken loss math",
                "broken san arithmetic",
                "actor san balance",
                "wrong temporary threshold",
                "wrong indefinite threshold",
                "hidden investigator",
                "boolean numeric",
            ):
                with self.subTest(label=label):
                    tampered = json.loads(json.dumps(baseline))
                    sanity = tampered["turns"][0]["mechanics"][0]
                    if label == "extra field":
                        sanity["unexpected"] = True
                    elif label == "invalid loss roll":
                        sanity["loss_rolls"] = [7]
                    elif label == "broken loss math":
                        sanity["san_loss"] = 3
                        sanity["san_after"] = 62
                        sanity["session_san_loss"] = 3
                        self._actor(
                            tampered,
                            "investigator_tracker",
                        )["san"].update({"current": 62, "session_loss": 3})
                    elif label == "broken san arithmetic":
                        sanity["san_after"] = 60
                    elif label == "actor san balance":
                        self._actor(
                            tampered,
                            "investigator_tracker",
                        )["san"]["current"] = 60
                    elif label == "wrong temporary threshold":
                        sanity["temporary_insanity_threshold_reached"] = True
                    elif label == "wrong indefinite threshold":
                        sanity["indefinite_insanity_threshold_crossed"] = True
                    elif label == "hidden investigator":
                        sanity["visibility"] = "hidden"
                    else:
                        sanity["san_loss"] = True
                    write_json_atomic(session_file, tampered)
                    with self.assertRaisesRegex(
                        AgenticSessionLoadError,
                        "CommittedTurn 格式无效|机械与冻结角色卡不一致",
                    ):
                        run.store.load_session(run.game_id)
                    write_json_atomic(session_file, baseline)

    def test_loader_rejects_tampered_sanity_continuity_between_turns(self) -> None:
        """单条算术仍合法时，也不能断开相邻 SAN 与本局累计链。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            responses: list[ModelResponse] = []
            for index, loss in enumerate((4, 2), start=1):
                responses.extend(
                    [
                        self._tool_response(
                            {
                                "actor_id": "investigator_tracker",
                                "source": f"连续恐怖来源 {index}",
                                "success_loss": "0",
                                "failure_loss": str(loss),
                                "visibility": "public",
                            },
                            name="make_sanity_check",
                            tool_call_id=f"call_sanity_continuity_{index}",
                        ),
                        self._response(self._final_payload()),
                    ]
                )
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(responses),
                mechanic_id_factory=iter(
                    ("mechanic_sanity_continuity_1", "mechanic_sanity_continuity_2")
                ).__next__,
                random_source=ScriptedRandom((1, 7, 1, 7)),
            )
            self.assertEqual(
                harness.start_turn(game_id, "面对第一处恐怖。").status,
                "committed",
            )
            self.assertEqual(
                harness.start_turn(game_id, "面对第二处恐怖。").status,
                "committed",
            )

            session_file = store.session_root / game_id / "session.json"
            tampered = read_json(session_file)
            second = tampered["turns"][1]["mechanics"][0]
            second["target"] = 60
            second["san_before"] = 60
            second["san_after"] = 58
            self._actor(
                tampered,
                "investigator_tracker",
            )["san"]["current"] = 58
            write_json_atomic(session_file, tampered)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "机械与冻结角色卡不一致",
            ):
                store.load_session(game_id)

    def test_sanity_binds_selected_loss_and_freezes_unselected_branch(self) -> None:
        """成功/失败结果绑定所选表达式，未选分支也参与幂等比较。"""

        cases = (
            (
                "success",
                (5, 6, 3),
                "success_loss",
                "failure_loss",
                "1d4",
                [3],
                62,
            ),
            (
                "failure",
                (1, 7, 4),
                "failure_loss",
                "success_loss",
                "1d6",
                [4],
                61,
            ),
        )
        for (
            outcome,
            dice,
            selected_field,
            unselected_field,
            selected_expression,
            selected_rolls,
            san_after,
        ) in cases:
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                arguments = {
                    "actor_id": "investigator_tracker",
                    "source": f"{outcome} 分支恐怖来源",
                    "success_loss": "1d4",
                    "failure_loss": "1d6",
                    "visibility": "public",
                }
                random_source = ScriptedRandom(dice)
                first = AgenticHarness(
                    store,
                    ScriptedGameMasterModel(
                        [
                            self._tool_response(
                                arguments,
                                name="make_sanity_check",
                                tool_call_id=f"call_sanity_{outcome}",
                            ),
                            ModelCallError(
                                "request_timeout",
                                "stop after selected SAN branch",
                                retryable=True,
                            ),
                        ]
                    ),
                    turn_id_factory=lambda outcome=outcome: f"turn_sanity_{outcome}",
                    mechanic_id_factory=(
                        lambda outcome=outcome: f"mechanic_sanity_{outcome}"
                    ),
                    random_source=random_source,
                ).start_turn(game_id, "结算已经成立的恐怖来源。")

                self.assertEqual(first.error_code, "request_timeout")
                self.assertEqual(
                    random_source.calls,
                    [(0, 9), (0, 9), (1, 4 if outcome == "success" else 6)],
                )
                incomplete = store.load_session(game_id).session["incomplete_turn"]
                sanity = incomplete["mechanics"][0]
                self.assertEqual(sanity["outcome"], outcome)
                self.assertEqual(arguments[selected_field], selected_expression)
                self.assertEqual(sanity["loss_expression"], selected_expression)
                self.assertEqual(sanity["loss_rolls"], selected_rolls)
                self.assertEqual(sanity["san_after"], san_after)
                original_interaction = json.loads(
                    json.dumps(incomplete["tool_interactions"][0])
                )

                changed = {**arguments, unselected_field: "2"}
                replay_random = ScriptedRandom(())
                replayed = AgenticHarness(
                    store,
                    ScriptedGameMasterModel(
                        [
                            self._tool_response(
                                changed,
                                name="make_sanity_check",
                                tool_call_id=f"call_sanity_{outcome}",
                            )
                        ]
                    ),
                    mechanic_id_factory=lambda: self.fail(
                        "changed unselected SAN branch allocated a mechanic ID"
                    ),
                    random_source=replay_random,
                ).resume_turn(game_id, f"turn_sanity_{outcome}")

                self.assertEqual(replayed.error_code, "provider_protocol_error")
                self.assertEqual(replay_random.calls, [])
                after_replay = store.load_session(game_id).session["incomplete_turn"]
                self.assertEqual(
                    after_replay["tool_interactions"],
                    [original_interaction],
                )
                self.assertEqual(after_replay["mechanics"], [sanity])
                self.assertEqual(len(after_replay["provider_protocol_errors"]), 1)

    def test_sanity_preflight_rejects_invalid_inputs_before_id_rng_and_san(self) -> None:
        """理智 schema、角色、可见性与两条表达式都在随机前拒绝。"""

        valid = {
            "actor_id": "investigator_tracker",
            "source": "积水倒影中的苍白侧脸",
            "success_loss": "0",
            "failure_loss": "1d6",
            "visibility": "public",
        }
        cases = (
            ("unknown actor", {**valid, "actor_id": "actor_missing"}, "unknown_actor"),
            ("submitted san result", {**valid, "san_after": 0}, "invalid_arguments"),
            ("empty source", {**valid, "source": ""}, "invalid_arguments"),
            ("non-string success loss", {**valid, "success_loss": 0}, "invalid_arguments"),
            ("unknown visibility", {**valid, "visibility": "secret"}, "invalid_arguments"),
            ("hidden investigator", {**valid, "visibility": "hidden"}, "invalid_visibility"),
            ("invalid success expression", {**valid, "success_loss": "21d1"}, "invalid_dice_expression"),
            ("invalid failure expression", {**valid, "failure_loss": "1d101"}, "invalid_dice_expression"),
            ("negative theoretical loss", {**valid, "failure_loss": "1d6-2"}, "invalid_dice_expression"),
        )
        for label, arguments, expected_code in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                allocated: list[str] = []
                random_source = ScriptedRandom(())
                result = AgenticHarness(
                    store,
                    ScriptedGameMasterModel(
                        [
                            self._tool_response(
                                arguments,
                                name="make_sanity_check",
                            ),
                            ModelCallError(
                                "request_timeout",
                                "stop after sanity rejection",
                                retryable=True,
                            ),
                        ]
                    ),
                    mechanic_id_factory=lambda allocated=allocated: (
                        allocated.append("allocated") or "unexpected"
                    ),
                    random_source=random_source,
                ).start_turn(game_id, "结算已经成立的恐怖来源。")

                self.assertEqual(result.error_code, "request_timeout")
                self.assertEqual(allocated, [])
                self.assertEqual(random_source.calls, [])
                session = store.load_session(game_id).session
                self.assertEqual(
                    self._actor(session, "investigator_tracker")["san"],
                    {"current": 65, "max": 65, "session_loss": 0},
                )
                incomplete = session["incomplete_turn"]
                self.assertEqual(incomplete["mechanics"], [])
                self.assertEqual(
                    incomplete["tool_interactions"][0]["error"]["code"],
                    expected_code,
                )

    def test_invalid_make_check_is_persisted_before_same_gm_continues(self) -> None:
        """可关联的工具错误不掷骰，并以完整交互反馈同一个 GM。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            response = ModelResponse(
                assistant_message={
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "受限 provider 材料",
                    "tool_calls": [
                        {
                            "id": "call_001",
                            "type": "function",
                            "function": {
                                "name": "make_check",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                finish_reason="tool_calls",
                usage=None,
                latency_ms=11,
            )
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        response,
                        ModelCallError(
                            "request_timeout",
                            "stop after persisted tool error",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_0001",
                clock=lambda: datetime(2026, 7, 27, 0, 5, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我试着撬开牢门。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "request_timeout")
            self.assertIsNone(result.narration)
            session = store.load_session(game_id).session
            incomplete = session["incomplete_turn"]
            self.assertEqual(len(incomplete["deepseek_messages"]), 5)
            self.assertEqual(incomplete["provider_protocol_errors"], [])
            self.assertEqual(incomplete["mechanics"], [])
            interaction = incomplete["tool_interactions"][0]
            self.assertFalse(interaction["ok"])
            self.assertEqual(interaction["arguments_raw"], "{}")
            self.assertIsNone(interaction["arguments"])
            self.assertEqual(interaction["error"]["code"], "invalid_arguments")
            tool_result = json.loads(incomplete["deepseek_messages"][-1]["content"])
            self.assertFalse(tool_result["ok"])
            self.assertEqual(tool_result["tool_call_id"], "call_001")
            self.assertNotIn(
                "受限 provider 材料",
                result.error_message or "",
            )

    def test_invalid_make_check_inputs_never_draw_or_allocate_mechanic(self) -> None:
        """schema、领域与工具名反例都在 RNG 和 mechanic_id 之前被拒绝。"""

        valid = {
            "actor_id": "investigator_tracker",
            "ability": "spot_hidden",
            "difficulty": "regular",
            "dice_adjustment": {"kind": "none", "count": 0},
            "action": "检查牢门",
            "stakes": "失败会错过痕迹",
            "visibility": "public",
        }
        cases = (
            ("unknown actor", {**valid, "actor_id": "npc_missing"}, "make_check", "unknown_actor", True),
            ("unselected investigator", {**valid, "actor_id": "investigator_mediator"}, "make_check", "unknown_actor", True),
            ("unknown ability", {**valid, "ability": "locksmithing"}, "make_check", "unknown_ability", True),
            ("unowned setting skill", {**valid, "ability": "flight"}, "make_check", "unknown_ability", True),
            ("forged target", {**valid, "target": 99}, "make_check", "invalid_arguments", False),
            ("bad difficulty", {**valid, "difficulty": []}, "make_check", "invalid_difficulty", False),
            ("bad adjustment", {**valid, "dice_adjustment": {"kind": "none", "count": 1}}, "make_check", "invalid_dice_adjustment", False),
            ("missing visibility", {key: value for key, value in valid.items() if key != "visibility"}, "make_check", "invalid_arguments", False),
            ("unknown tool", valid, "apply_effect", "unknown_tool", False),
        )
        for label, arguments, tool_name, code, normalized in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                model = ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments, name=tool_name),
                        ModelCallError(
                            "request_timeout",
                            "stop after error",
                            retryable=True,
                        ),
                    ]
                )
                random_source = ScriptedRandom(())
                allocated_mechanic_ids: list[str] = []

                def forbidden_mechanic_id(
                    allocated: list[str] = allocated_mechanic_ids,
                ) -> str:
                    allocated.append("mechanic_forbidden")
                    return "mechanic_forbidden"

                harness = AgenticHarness(
                    store,
                    model,
                    turn_id_factory=lambda: "turn_0001",
                    mechanic_id_factory=forbidden_mechanic_id,
                    random_source=random_source,
                    clock=lambda: datetime(2026, 7, 28, 0, 3, tzinfo=timezone.utc),
                )

                result = harness.start_turn(game_id, "我尝试行动。")

                self.assertEqual(result.error_code, "request_timeout")
                self.assertEqual(random_source.calls, [])
                self.assertEqual(allocated_mechanic_ids, [])
                incomplete = store.load_session(game_id).session["incomplete_turn"]
                self.assertEqual(incomplete["mechanics"], [])
                interaction = incomplete["tool_interactions"][0]
                self.assertEqual(interaction["error"]["code"], code)
                self.assertEqual(interaction["arguments"] is not None, normalized)

    def test_unusable_tool_call_id_interrupts_without_synthetic_protocol_pair(self) -> None:
        """无法可靠关联的 ID 只保留受限原始 envelope 并停止。"""

        for unusable_id in (None, "", " call_001", "call_001 "):
            with self.subTest(tool_call_id=unusable_id), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                model = ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            {
                                "actor_id": "investigator_tracker",
                                "ability": "spot_hidden",
                                "difficulty": "regular",
                                "dice_adjustment": {"kind": "none", "count": 0},
                                "action": "检查牢门",
                                "stakes": "失败会错过痕迹",
                                "visibility": "public",
                            },
                            tool_call_id=unusable_id,
                            reasoning="受限协议材料",
                        )
                    ]
                )
                random_source = ScriptedRandom(())
                harness = AgenticHarness(
                    store,
                    model,
                    turn_id_factory=lambda: "turn_0001",
                    random_source=random_source,
                    clock=lambda: datetime(2026, 7, 28, 0, 4, tzinfo=timezone.utc),
                )

                result = harness.start_turn(game_id, "我检查牢门。")

                self.assertEqual(result.error_code, "provider_protocol_error")
                self.assertNotIn("受限协议材料", result.error_message or "")
                incomplete = store.load_session(game_id).session["incomplete_turn"]
                self.assertEqual(incomplete["mechanics"], [])
                self.assertEqual(incomplete["tool_interactions"], [])
                self.assertEqual(len(incomplete["deepseek_messages"]), 3)
                self.assertEqual(len(incomplete["provider_protocol_errors"]), 1)
                self.assertIn(
                    "受限协议材料",
                    incomplete["provider_protocol_errors"][0]["model_response_json"],
                )
                self.assertEqual(random_source.calls, [])
                self.assertEqual(len(model.requests), 1)

    def test_resume_omits_unpairable_provider_response_from_replay_prefix(self) -> None:
        """不可配对响应只留在受限诊断，恢复请求仅发送最后合法前缀。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            first_harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            {
                                "actor_id": "investigator_tracker",
                                "ability": "spot_hidden",
                                "difficulty": "regular",
                                "dice_adjustment": {"kind": "none", "count": 0},
                                "action": "检查牢门",
                                "stakes": "失败会错过痕迹",
                                "visibility": "public",
                            },
                            tool_call_id=None,
                            reasoning="受限协议材料",
                        )
                    ]
                ),
                turn_id_factory=lambda: "turn_0001",
                random_source=ScriptedRandom(()),
                clock=lambda: datetime(2026, 7, 28, 0, 4, tzinfo=timezone.utc),
            )
            first_harness.start_turn(game_id, "我检查牢门。")
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            replay_prefix = incomplete["deepseek_messages"]
            self.assertIn(
                "受限协议材料",
                incomplete["provider_protocol_errors"][0]["model_response_json"],
            )

            resumed_model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "牢门没有显出更多痕迹。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            resumed = AgenticHarness(
                store,
                resumed_model,
                clock=lambda: datetime(2026, 7, 28, 0, 5, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_0001")

            self.assertEqual(resumed.status, "committed")
            self.assertEqual(resumed_model.requests[0].messages, tuple(replay_prefix))
            self.assertNotIn(
                "受限协议材料",
                json.dumps(
                    resumed_model.requests[0].messages,
                    ensure_ascii=False,
                ),
            )

    def test_tool_write_failure_reports_no_mechanic_and_stops_same_gm(self) -> None:
        """工具事务写失败时不向 GM 或玩家报告未提交的随机结果。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        {
                            "actor_id": "investigator_tracker",
                            "ability": "spot_hidden",
                            "difficulty": "regular",
                            "dice_adjustment": {"kind": "none", "count": 0},
                            "action": "检查牢门",
                            "stakes": "失败会错过痕迹",
                            "visibility": "public",
                        }
                    )
                ]
            )
            writes = 0

            def fail_tool_write(path: Path, value: object) -> None:
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("injected tool write failure")
                write_json_atomic(path, value)

            random_source = ScriptedRandom((3, 4))
            published: list[PublicMechanic] = []
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_0001",
                mechanic_id_factory=lambda: "mechanic_0001",
                random_source=random_source,
                session_writer=fail_tool_write,
                clock=lambda: datetime(2026, 7, 28, 0, 5, tzinfo=timezone.utc),
            )

            result = harness.start_turn(
                game_id,
                "我检查牢门。",
                public_mechanic_sink=published.append,
            )

            self.assertEqual(result.error_code, "tool_commit_failed")
            self.assertEqual(result.public_mechanics, ())
            self.assertEqual(published, [])
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            self.assertEqual(incomplete["mechanics"], [])
            self.assertEqual(incomplete["tool_interactions"], [])
            self.assertEqual(len(incomplete["deepseek_messages"]), 3)
            self.assertEqual(len(incomplete["provider_protocol_errors"]), 1)
            self.assertEqual(random_source.calls, [(0, 9), (0, 9)])
            self.assertEqual(len(model.requests), 1)

    def test_restart_after_tool_write_failure_executes_then_replays_exactly_once(
        self,
    ) -> None:
        """写失败不留幂等映射，重启后提交一次且后续重放不再执行。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            arguments = self._valid_check_arguments()
            writes = 0

            def fail_first_tool_write(path: Path, value: object) -> None:
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("injected tool write failure")
                write_json_atomic(path, value)

            first_random = ScriptedRandom((3, 4))
            first = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [self._tool_response(arguments, tool_call_id="call_atomic")]
                ),
                turn_id_factory=lambda: "turn_atomic_replay",
                mechanic_id_factory=lambda: "mechanic_uncommitted",
                random_source=first_random,
                session_writer=fail_first_tool_write,
                clock=lambda: datetime(2026, 7, 28, 0, 32, tzinfo=timezone.utc),
            )

            failed = first.start_turn(game_id, "我检查牢门。")

            self.assertEqual(failed.error_code, "tool_commit_failed")
            after_failure = store.load_session(game_id).session["incomplete_turn"]
            assert after_failure is not None
            self.assertEqual(after_failure["mechanics"], [])
            self.assertEqual(after_failure["tool_interactions"], [])
            self.assertEqual(len(after_failure["deepseek_messages"]), 3)
            self.assertEqual(first_random.calls, [(0, 9), (0, 9)])

            committed_random = ScriptedRandom((5, 6))
            second = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments, tool_call_id="call_atomic"),
                        ModelCallError(
                            "request_timeout",
                            "stop after committed retry",
                            retryable=True,
                        ),
                    ]
                ),
                mechanic_id_factory=lambda: "mechanic_committed",
                random_source=committed_random,
                clock=lambda: datetime(2026, 7, 28, 0, 33, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_atomic_replay")

            self.assertEqual(second.error_code, "request_timeout")
            self.assertEqual(committed_random.calls, [(0, 9), (0, 9)])
            after_commit = store.load_session(game_id).session["incomplete_turn"]
            assert after_commit is not None
            self.assertEqual(len(after_commit["mechanics"]), 1)
            self.assertEqual(len(after_commit["tool_interactions"]), 1)
            self.assertEqual(
                after_commit["mechanics"][0]["mechanic_id"],
                "mechanic_committed",
            )
            self.assertEqual(after_commit["mechanics"][0]["roll"], 65)

            replay_random = ScriptedRandom(())
            third = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments, tool_call_id="call_atomic"),
                        self._response(
                            {
                                "narration": "牢门仍然紧闭。",
                                "establish": [],
                                "retire": [],
                                "session_status": "ongoing",
                            }
                        ),
                    ]
                ),
                random_source=replay_random,
                clock=lambda: datetime(2026, 7, 28, 0, 34, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_atomic_replay")

            self.assertEqual(third.status, "committed")
            self.assertEqual(replay_random.calls, [])
            session = store.load_session(game_id).session
            self.assertEqual(len(session["turns"]), 1)
            self.assertEqual(len(session["turns"][0]["mechanics"]), 1)
            self.assertEqual(
                session["turns"][0]["mechanics"][0]["mechanic_id"],
                "mechanic_committed",
            )
            self.assertEqual(session["turns"][0]["mechanics"][0]["roll"], 65)

    def test_committed_check_survives_provider_and_final_failures_exactly_once(self) -> None:
        """工具提交后的 provider/final 故障不回滚、不重掷也不重复机械。"""

        valid_arguments = {
            "actor_id": "investigator_tracker",
            "ability": "spot_hidden",
            "difficulty": "regular",
            "dice_adjustment": {"kind": "none", "count": 0},
            "action": "检查牢门",
            "stakes": "失败会错过痕迹",
            "visibility": "public",
        }
        later_failures = (
            (
                "provider",
                ModelCallError("request_timeout", "private", retryable=True),
                "request_timeout",
                None,
            ),
            (
                "invalid final",
                self._response(
                    {
                        "narration": "不能提交",
                        "establish": [],
                        "retire": [],
                        "session_status": [],
                    }
                ),
                "invalid_final_response",
                None,
            ),
            (
                "final write",
                self._response(
                    {
                        "narration": "不能写入",
                        "establish": [],
                        "retire": [],
                        "session_status": "ongoing",
                    }
                ),
                "final_commit_failed",
                3,
            ),
        )
        for label, later_step, error_code, failed_write in later_failures:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                store, game_id = self._create_session(Path(directory))
                later_steps = [later_step]
                if error_code == "invalid_final_response":
                    later_steps.append(later_step)
                model = ScriptedGameMasterModel(
                    [self._tool_response(valid_arguments), *later_steps]
                )
                writes = 0

                def controlled_writer(
                    path: Path,
                    value: object,
                    fail_at: int | None = failed_write,
                ) -> None:
                    nonlocal writes
                    writes += 1
                    if fail_at is not None and writes == fail_at:
                        raise OSError("injected final write failure")
                    write_json_atomic(path, value)

                random_source = ScriptedRandom((3, 4))
                harness = AgenticHarness(
                    store,
                    model,
                    turn_id_factory=lambda: "turn_0001",
                    mechanic_id_factory=lambda: "mechanic_0001",
                    random_source=random_source,
                    session_writer=controlled_writer,
                    clock=lambda: datetime(2026, 7, 28, 0, 6, tzinfo=timezone.utc),
                )

                result = harness.start_turn(game_id, "我检查牢门。")

                self.assertEqual(result.error_code, error_code)
                self.assertEqual(len(result.public_mechanics), 1)
                incomplete = store.load_session(game_id).session["incomplete_turn"]
                self.assertEqual(
                    [item["mechanic_id"] for item in incomplete["mechanics"]],
                    ["mechanic_0001"],
                )
                self.assertEqual(len(incomplete["tool_interactions"]), 1)
                self.assertEqual(random_source.calls, [(0, 9), (0, 9)])

    def test_load_rejects_successful_check_without_normalized_arguments(self) -> None:
        """成功交互不能丢失用于幂等与机械追溯的规范化事前参数。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            arguments = {
                "actor_id": "investigator_tracker",
                "ability": "spot_hidden",
                "difficulty": "regular",
                "dice_adjustment": {"kind": "none", "count": 0},
                "action": "检查牢门",
                "stakes": "失败会错过痕迹",
                "visibility": "public",
            }
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments),
                        ModelCallError(
                            "request_timeout",
                            "private provider detail",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_0001",
                mechanic_id_factory=lambda: "mechanic_0001",
                random_source=ScriptedRandom((3, 4)),
                clock=lambda: datetime(2026, 7, 28, 0, 6, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查牢门。")
            self.assertEqual(result.error_code, "request_timeout")
            loaded = store.load_session(game_id).session
            self.assertEqual(
                loaded["incomplete_turn"]["tool_interactions"][0]["arguments"],
                arguments,
            )

            tampered = read_json(
                store.session_root / game_id / "session.json"
            )
            tampered["incomplete_turn"]["tool_interactions"][0]["arguments"] = None
            write_json_atomic(store.session_root / game_id / "session.json", tampered)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "ToolInteraction 格式无效",
            ):
                store.load_session(game_id)

    def test_load_rejects_successful_check_with_arguments_changed_after_roll(
        self,
    ) -> None:
        """协议材料彼此自洽时也不能改写已经冻结并结算的事前参数。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            arguments = {
                "actor_id": "investigator_tracker",
                "ability": "spot_hidden",
                "difficulty": "regular",
                "dice_adjustment": {"kind": "none", "count": 0},
                "action": "检查牢门",
                "stakes": "失败会错过痕迹",
                "visibility": "public",
            }
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments),
                        ModelCallError(
                            "request_timeout",
                            "private provider detail",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_0001",
                mechanic_id_factory=lambda: "mechanic_0001",
                random_source=ScriptedRandom((3, 4)),
                clock=lambda: datetime(2026, 7, 28, 0, 6, tzinfo=timezone.utc),
            )

            result = harness.start_turn(game_id, "我检查牢门。")
            self.assertEqual(result.error_code, "request_timeout")
            tampered = read_json(store.session_root / game_id / "session.json")
            interaction = tampered["incomplete_turn"]["tool_interactions"][0]
            interaction["arguments"]["action"] = "骰后改写的行动"
            changed_raw = json.dumps(
                interaction["arguments"],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            interaction["arguments_raw"] = changed_raw
            assistant_message = next(
                message
                for message in tampered["incomplete_turn"]["deepseek_messages"]
                if message.get("role") == "assistant"
                and message.get("tool_calls")
            )
            assistant_message["tool_calls"][0]["function"]["arguments"] = (
                changed_raw
            )
            write_json_atomic(store.session_root / game_id / "session.json", tampered)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "ToolInteraction 格式无效",
            ):
                store.load_session(game_id)

    def test_load_rejects_boolean_check_target(self) -> None:
        """JSON 布尔值不能冒充能力值为 1 的可信检定目标。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(
                            {
                                "actor_id": "investigator_tracker",
                                "ability": "locksmith",
                                "difficulty": "regular",
                                "dice_adjustment": {"kind": "none", "count": 0},
                                "action": "尝试拨动锁芯",
                                "stakes": "失败会制造金属声",
                                "visibility": "public",
                            }
                        ),
                        self._response(
                            {
                                "narration": "锁芯没有转动。",
                                "establish": [],
                                "retire": [],
                                "session_status": "ongoing",
                            }
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_0001",
                mechanic_id_factory=lambda: "mechanic_0001",
                random_source=ScriptedRandom((2, 2)),
                clock=lambda: datetime(2026, 7, 28, 0, 7, tzinfo=timezone.utc),
            )
            result = harness.start_turn(game_id, "我用发卡试着撬锁。")
            self.assertEqual(result.status, "committed")

            session_file = store.session_root / game_id / "session.json"
            tampered = read_json(session_file)
            tampered["turns"][0]["mechanics"][0]["target"] = True
            write_json_atomic(session_file, tampered)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "CommittedTurn 格式无效",
            ):
                store.load_session(game_id)

    def test_load_rejects_boolean_in_normalized_failed_tool_arguments(
        self,
    ) -> None:
        """失败交互的规范参数也必须保留 JSON 整数类型。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            arguments = {
                "actor_id": "npc_missing",
                "ability": "spot_hidden",
                "difficulty": "regular",
                "dice_adjustment": {"kind": "bonus", "count": 1},
                "action": "检查牢门",
                "stakes": "失败会错过痕迹",
                "visibility": "public",
            }
            harness = AgenticHarness(
                store,
                ScriptedGameMasterModel(
                    [
                        self._tool_response(arguments),
                        ModelCallError(
                            "request_timeout",
                            "stop after persisted tool error",
                            retryable=True,
                        ),
                    ]
                ),
                turn_id_factory=lambda: "turn_0001",
                random_source=ScriptedRandom(()),
                clock=lambda: datetime(2026, 7, 28, 0, 7, tzinfo=timezone.utc),
            )
            result = harness.start_turn(game_id, "我检查牢门。")
            self.assertEqual(result.error_code, "request_timeout")

            session_file = store.session_root / game_id / "session.json"
            tampered = read_json(session_file)
            tampered["incomplete_turn"]["tool_interactions"][0]["arguments"][
                "dice_adjustment"
            ]["count"] = True
            write_json_atomic(session_file, tampered)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "ToolInteraction 格式无效",
            ):
                store.load_session(game_id)

    def test_hidden_check_is_persisted_for_gm_without_public_projection(self) -> None:
        """隐藏检定完整进入恢复材料与 GM tool result，但不产生玩家事件。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            secret_action = "守卫暗中判断是否听见排水沟的声音"
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        {
                            "actor_id": "investigator_tracker",
                            "ability": "listen",
                            "difficulty": "hard",
                            "dice_adjustment": {"kind": "penalty", "count": 1},
                            "action": secret_action,
                            "stakes": "失败意味着守卫尚未察觉",
                            "visibility": "hidden",
                        }
                    ),
                    ModelCallError(
                        "request_timeout",
                        "private provider detail",
                        retryable=True,
                    ),
                ]
            )
            published: list[PublicMechanic] = []
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_0001",
                mechanic_id_factory=lambda: "mechanic_0001",
                random_source=ScriptedRandom((2, 1, 8)),
                clock=lambda: datetime(2026, 7, 28, 0, 7, tzinfo=timezone.utc),
            )

            result = harness.start_turn(
                game_id,
                "我悄悄拨动排水沟盖。",
                public_mechanic_sink=published.append,
            )
            lifecycle = harness.get_session_state(game_id)

            self.assertEqual(result.error_code, "request_timeout")
            self.assertEqual(result.public_mechanics, ())
            self.assertEqual(published, [])
            self.assertEqual(lifecycle.public_mechanics, ())
            self.assertNotIn(secret_action, repr(lifecycle))
            incomplete = store.load_session(game_id).session["incomplete_turn"]
            mechanic = incomplete["mechanics"][0]
            self.assertEqual(mechanic["visibility"], "hidden")
            self.assertEqual(mechanic["roll"], 82)
            self.assertIn(secret_action, model.requests[1].messages[-1]["content"])
            self.assertNotIn(secret_action, result.error_message or "")

    def test_loader_rejects_mechanic_detached_from_frozen_actor_sheet(self) -> None:
        """交互内部即使自洽，也不能把冻结角色能力值改成另一目标。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._tool_response(
                        {
                            "actor_id": "investigator_tracker",
                            "ability": "spot_hidden",
                            "difficulty": "regular",
                            "dice_adjustment": {"kind": "none", "count": 0},
                            "action": "检查牢门",
                            "stakes": "失败会错过痕迹",
                            "visibility": "public",
                        }
                    ),
                    ModelCallError(
                        "request_timeout",
                        "stop with incomplete turn",
                        retryable=True,
                    ),
                ]
            )
            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_0001",
                mechanic_id_factory=lambda: "mechanic_0001",
                random_source=ScriptedRandom((3, 4)),
                clock=lambda: datetime(2026, 7, 28, 0, 8, tzinfo=timezone.utc),
            )
            harness.start_turn(game_id, "我检查牢门。")
            session_file = store.load_session(game_id).session_directory / "session.json"
            tampered = read_json(session_file)
            incomplete = tampered["incomplete_turn"]
            mechanic = incomplete["mechanics"][0]
            interaction_result = incomplete["tool_interactions"][0]["result"]
            envelope = json.loads(incomplete["deepseek_messages"][-1]["content"])
            envelope_result = envelope["result"]
            for result in (mechanic, interaction_result, envelope_result):
                result["ability_value"] = 99
                result["target"] = 99
                result["success_level"] = "hard_success"
            incomplete["deepseek_messages"][-1]["content"] = json.dumps(
                envelope,
                ensure_ascii=False,
                sort_keys=True,
            )
            write_json_atomic(session_file, tampered)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "机械与冻结角色卡不一致",
            ):
                store.load_session(game_id)

    def test_final_write_failure_recovers_to_one_committed_turn(self) -> None:
        """最终写失败不留部分事实，恢复后只提交一份回合和事实。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "这段叙事不能被提交。",
                            "establish": [
                                {
                                    "visibility": "public",
                                    "text": "这条事实不能被提交。",
                                }
                            ],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            writes = 0

            def fail_final_write(path: Path, value: object) -> None:
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("injected final write failure")
                write_json_atomic(path, value)

            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_0001",
                fact_id_factory=lambda: "fact_1001",
                clock=lambda: datetime(2026, 7, 27, 0, 6, tzinfo=timezone.utc),
                session_writer=fail_final_write,
            )

            result = harness.start_turn(game_id, "我敲击墙壁。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "final_commit_failed")
            session = store.load_session(game_id).session
            self.assertEqual(session["turns"], [])
            self.assertFalse(
                any(fact["fact_id"] == "fact_1001" for fact in session["facts"])
            )
            self.assertEqual(session["incomplete_turn"]["turn_id"], "turn_0001")
            self.assertEqual(
                session["incomplete_turn"]["last_failure"]["code"],
                "final_commit_failed",
            )

            resumed_model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "敲击声表明墙后存在空腔。",
                            "establish": [
                                {
                                    "visibility": "public",
                                    "text": "牢房墙后存在空腔。",
                                }
                            ],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            resumed = AgenticHarness(
                store,
                resumed_model,
                fact_id_factory=lambda: "fact_1001",
                clock=lambda: datetime(2026, 7, 27, 0, 7, tzinfo=timezone.utc),
            ).resume_turn(game_id, "turn_0001")

            self.assertEqual(resumed.status, "committed")
            committed = store.load_session(game_id).session
            self.assertIsNone(committed["incomplete_turn"])
            self.assertEqual(len(committed["turns"]), 1)
            self.assertEqual(committed["turns"][0]["turn_id"], "turn_0001")
            recovered_facts = [
                fact for fact in committed["facts"] if fact["fact_id"] == "fact_1001"
            ]
            self.assertEqual(len(recovered_facts), 1)
            self.assertEqual(recovered_facts[0]["text"], "牢房墙后存在空腔。")

    def test_repeated_injected_final_write_failure_still_returns_interruption(self) -> None:
        """写入器持续失败时，Harness 仍用原子恢复写回稳定中断而非抛异常。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "这段叙事不能被提交。",
                            "establish": [],
                            "retire": [],
                            "session_status": "ongoing",
                        }
                    )
                ]
            )
            writes = 0

            def fail_after_initial_write(path: Path, value: object) -> None:
                nonlocal writes
                writes += 1
                if writes > 1:
                    raise OSError("injected repeated write failure")
                write_json_atomic(path, value)

            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_0001",
                clock=lambda: datetime(2026, 7, 27, 0, 6, tzinfo=timezone.utc),
                session_writer=fail_after_initial_write,
            )

            result = harness.start_turn(game_id, "我敲击墙壁。")

            self.assertEqual(result.status, "interrupted")
            self.assertEqual(result.error_code, "final_commit_failed")
            session = store.load_session(game_id).session
            self.assertEqual(session["turns"], [])
            self.assertEqual(
                session["incomplete_turn"]["last_failure"]["code"],
                "final_commit_failed",
            )

    def test_complete_session_rejects_input_before_id_or_model_call(self) -> None:
        """本局收束后，新输入不能分配回合、改变聚合或产生模型费用。"""

        with tempfile.TemporaryDirectory() as directory:
            store, game_id = self._create_session(Path(directory))
            model = ScriptedGameMasterModel(
                [
                    self._response(
                        {
                            "narration": "你们登上引潮舟，塔纳里昂沉入雾后。",
                            "establish": [],
                            "retire": [],
                            "session_status": "complete",
                        }
                    )
                ]
            )
            allocated_turn_ids: list[str] = []

            def new_turn_id() -> str:
                value = f"turn_{len(allocated_turn_ids) + 1:04d}"
                allocated_turn_ids.append(value)
                return value

            harness = AgenticHarness(
                store,
                model,
                turn_id_factory=new_turn_id,
                clock=lambda: datetime(2026, 7, 27, 0, 7, tzinfo=timezone.utc),
            )
            committed = harness.start_turn(game_id, "我解开缆绳，驶离港口。")
            before = read_json(
                store.load_session(game_id).session_directory / "session.json"
            )

            with self.assertRaises(AgenticSessionCompleteError):
                harness.start_turn(game_id, "我还要返回城中。")

            after = read_json(
                store.load_session(game_id).session_directory / "session.json"
            )
            self.assertEqual(committed.status, "committed")
            self.assertEqual(before, after)
            self.assertEqual(allocated_turn_ids, ["turn_0001"])
            self.assertEqual(len(model.requests), 1)


if __name__ == "__main__":
    unittest.main()
