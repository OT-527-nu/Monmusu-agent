"""创建并装载 Agentic MVP 的不可变会话开场。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from monmusu_agent.agentic_coc import (
    MakeCheckError,
    normalize_make_check_arguments,
    validate_check_result,
)
from monmusu_agent.agentic_model import (
    ModelProfileValidationError,
    validated_model_profile,
)
from monmusu_agent.config import PROJECT_ROOT
from monmusu_agent.storage import read_json, write_json_atomic

SCHEMA_VERSION = "agentic-mvp-1"
MODULE_ID = "escape_thalarion"
SKILL_CATALOG_VERSION = "coc7e-agentic-mvp-1"

_SESSION_FIELDS = frozenset(
    {
        "schema_version",
        "game_id",
        "module_id",
        "skill_catalog_version",
        "setup",
        "session_status",
        "selected_investigator_id",
        "actor_display_names",
        "investigator_profile",
        "actors",
        "facts",
        "turns",
        "incomplete_turn",
        "created_at",
        "updated_at",
    }
)
_SETUP_FIELDS = frozenset(
    {
        "setup_id",
        "module_reference_revision",
        "module_reference_sha256",
        "character_reference_revision",
        "character_reference_sha256",
        "opening_narration",
        "opening_fact_ids",
        "created_at",
    }
)
_PROFILE_FIELDS = frozenset(
    {
        "actor_id",
        "display_name",
        "honorific",
        "pronouns",
        "occupation",
        "appearance",
        "background_hook",
        "keepsake",
    }
)
_ACTOR_FIELDS = frozenset(
    {
        "actor_id",
        "role",
        "skill_catalog_version",
        "attributes",
        "skills",
        "hp",
        "san",
        "luck",
        "armor",
    }
)
_ATTRIBUTE_FIELDS = frozenset(
    {
        "strength",
        "constitution",
        "size",
        "dexterity",
        "appearance",
        "intelligence",
        "power",
        "education",
    }
)
_FACT_FIELDS = frozenset(
    {
        "fact_id",
        "text",
        "visibility",
        "status",
        "established_turn_id",
        "origin",
        "retired_turn_id",
        "retire_reason",
    }
)
_FACT_ORIGIN_FIELDS = frozenset({"kind", "source_ref"})
_COMMITTED_TURN_FIELDS = frozenset(
    {
        "turn_id",
        "player_input",
        "mechanics",
        "narration",
        "established_fact_ids",
        "retirements",
        "session_status",
        "committed_at",
    }
)
_RETIREMENT_FIELDS = frozenset({"fact_id", "reason"})
_INCOMPLETE_TURN_FIELDS = frozenset(
    {
        "turn_id",
        "player_input",
        "started_at",
        "attempt_number",
        "attempt_started_at",
        "round_trips_used",
        "total_round_trips",
        "structure_repairs_used",
        "total_structure_repairs",
        "model_profile",
        "attempt_limits",
        "mechanics",
        "tool_interactions",
        "deepseek_messages",
        "provider_protocol_errors",
        "last_failure",
    }
)
_ATTEMPT_LIMIT_FIELDS = frozenset(
    {
        "max_round_trips",
        "request_timeout_seconds",
        "attempt_timeout_seconds",
        "max_structure_repairs",
    }
)
_PROVIDER_PROTOCOL_ERROR_FIELDS = frozenset(
    {"code", "message", "model_response_json", "recorded_at"}
)
_LAST_FAILURE_FIELDS = frozenset({"code", "message"})
_TOOL_INTERACTION_FIELDS = frozenset(
    {
        "tool_call_id",
        "tool_name",
        "arguments_raw",
        "arguments",
        "ok",
        "result",
        "error",
    }
)
_TOOL_ERROR_FIELDS = frozenset({"code", "message"})
_ASSISTANT_MESSAGE_FIELDS = frozenset(
    {"role", "content", "reasoning_content", "tool_calls"}
)
_TOOL_CALL_FIELDS = frozenset({"id", "type", "function"})
_FUNCTION_CALL_FIELDS = frozenset({"name", "arguments"})
_TOOL_MESSAGE_FIELDS = frozenset(
    {"role", "tool_call_id", "name", "content"}
)
_TOOL_ENVELOPE_FIELDS = frozenset(
    {"tool_call_id", "tool_name", "ok", "result", "error"}
)


def tool_call_matches_interaction(
    interaction: Mapping[str, Any],
    tool_name: str,
    arguments_raw: str,
) -> bool:
    """按持久化幂等规则比较一次 provider 工具重发。"""

    if interaction.get("tool_name") != tool_name:
        return False
    persisted_arguments = interaction.get("arguments")
    if isinstance(persisted_arguments, dict):
        try:
            incoming_arguments = normalize_make_check_arguments(arguments_raw)
        except MakeCheckError:
            return arguments_raw == interaction.get("arguments_raw")
        return incoming_arguments == persisted_arguments
    return arguments_raw == interaction.get("arguments_raw")


class AgenticSessionError(RuntimeError):
    """表示 Agentic 会话无法被可靠创建或装载。"""


class AgenticSessionSourceError(AgenticSessionError):
    """表示冻结前的目标静态数据不符合契约。"""


class AgenticSessionConflictError(AgenticSessionError):
    """表示目标游戏标识已经存在或不安全。"""


class AgenticSessionLoadError(AgenticSessionError):
    """表示已发布会话或其冻结材料无法形成可信输入。"""


class AgenticSessionPublishError(AgenticSessionError):
    """表示完整会话目录无法通过一次原子替换发布。"""


@dataclass(frozen=True)
class AgenticSessionSources:
    """集中声明新版会话初始化读取的权威静态材料。"""

    setup_fixture: Path = (
        PROJECT_ROOT / "data" / "modules" / "agentic_mvp_session_setup.json"
    )
    actor_templates: Path = (
        PROJECT_ROOT / "data" / "characters" / "agentic_mvp_actor_templates.json"
    )
    skill_catalog: Path = (
        PROJECT_ROOT / "data" / "characters" / "agentic_mvp_skill_catalog.json"
    )
    module_reference: Path = (
        PROJECT_ROOT / "docs" / "agentic_mvp" / "module_reference.md"
    )
    character_reference: Path = (
        PROJECT_ROOT / "docs" / "agentic_mvp" / "characters.md"
    )


@dataclass(frozen=True)
class NewSessionRequest:
    """保存玩家选卡时允许冻结的叙事资料。"""

    investigator_id: str
    display_name: str
    honorific: str | None = None
    pronouns: str | None = None
    occupation: str | None = None
    appearance: str | None = None
    background_hook: str | None = None
    keepsake: str | None = None


@dataclass(frozen=True)
class InvestigatorChoice:
    """向 CLI 暴露一张可选预生成调查员卡。"""

    actor_id: str
    label: str
    suggested_display_name: str


@dataclass(frozen=True)
class CreatedSession:
    """返回已经完整发布的新会话及开场文本。"""

    game_id: str
    session_directory: Path
    session_file: Path
    opening_narration: str
    session: Mapping[str, Any]


@dataclass(frozen=True)
class LoadedSession:
    """保存经过会话目录装载的聚合与两份全文快照。"""

    session_directory: Path
    session: Mapping[str, Any]
    module_reference: str
    character_reference: str


class AgenticSessionStore:
    """隐藏 Agentic 会话的静态装载、聚合构造与本地发布。"""

    def __init__(
        self,
        session_root: Path,
        *,
        sources: AgenticSessionSources | None = None,
        game_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        directory_publisher: Callable[[Path, Path], None] | None = None,
    ) -> None:
        self.session_root = session_root
        self.sources = sources or AgenticSessionSources()
        self.game_id_factory = game_id_factory or (
            lambda: f"game_{uuid4().hex}"
        )
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.directory_publisher = directory_publisher or os.replace

    def available_investigators(self) -> tuple[InvestigatorChoice, ...]:
        """返回当前目标数据中可由玩家选择的调查员。"""

        templates = self._load_object(self.sources.actor_templates, "角色模板")
        actors = templates.get("actors")
        if not isinstance(actors, list):
            raise AgenticSessionSourceError("角色模板 actors 必须是数组")

        choices: list[InvestigatorChoice] = []
        for actor in actors:
            if not isinstance(actor, dict) or actor.get("role") != "investigator":
                continue
            actor_id = self._required_string(actor.get("actor_id"), "actor_id")
            label = self._required_string(actor.get("choice_label"), "choice_label")
            suggested_name = self._required_string(
                actor.get("suggested_display_name"),
                "suggested_display_name",
            )
            choices.append(
                InvestigatorChoice(
                    actor_id=actor_id,
                    label=label,
                    suggested_display_name=suggested_name,
                )
            )
        if not choices:
            raise AgenticSessionSourceError("至少需要一张预生成调查员卡")
        return tuple(choices)

    def create_session(self, request: NewSessionRequest) -> CreatedSession:
        """构造完整开场聚合，并发布到一个新的游戏目录。"""

        fixture = self._load_object(self.sources.setup_fixture, "会话开场")
        catalog = self._load_object(self.sources.skill_catalog, "技能目录")
        templates = self._load_object(self.sources.actor_templates, "角色模板")
        module_bytes = self._read_bytes(self.sources.module_reference, "模组参考书")
        character_bytes = self._read_bytes(
            self.sources.character_reference,
            "人物参考",
        )

        game_id = self._new_game_id()
        session_directory = self.session_root / game_id
        if session_directory.exists():
            raise AgenticSessionConflictError(f"游戏目录已经存在: {game_id}")

        created_at = self._timestamp(self.clock())
        catalog_version = self._required_string(
            catalog.get("catalog_version"),
            "catalog_version",
        )
        actor = self._build_actor(
            request.investigator_id,
            catalog,
            templates,
            catalog_version,
        )
        profile = self._build_profile(request)
        module_hash = hashlib.sha256(module_bytes).hexdigest()
        character_hash = hashlib.sha256(character_bytes).hexdigest()
        module_revision = self._required_string(
            fixture.get("module_reference_revision"),
            "module_reference_revision",
        )
        character_revision = self._required_string(
            fixture.get("character_reference_revision"),
            "character_reference_revision",
        )
        facts = self._build_opening_facts(
            fixture,
            source_ref=f"{module_revision}#opening_minimum_canon",
        )
        opening_fact_ids = [fact["fact_id"] for fact in facts]
        actor_display_names = self._actor_display_names(fixture)
        actor_display_names[request.investigator_id] = profile["display_name"]
        opening_narration = self._required_string(
            fixture.get("opening_narration"),
            "opening_narration",
        )

        setup = {
            "setup_id": f"setup_{game_id}",
            "module_reference_revision": module_revision,
            "module_reference_sha256": module_hash,
            "character_reference_revision": character_revision,
            "character_reference_sha256": character_hash,
            "opening_narration": opening_narration,
            "opening_fact_ids": opening_fact_ids,
            "created_at": created_at,
        }
        session: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "game_id": game_id,
            "module_id": self._required_string(fixture.get("module_id"), "module_id"),
            "skill_catalog_version": catalog_version,
            "setup": setup,
            "session_status": "ongoing",
            "selected_investigator_id": request.investigator_id,
            "actor_display_names": actor_display_names,
            "investigator_profile": profile,
            "actors": [actor],
            "facts": facts,
            "turns": [],
            "incomplete_turn": None,
            "created_at": created_at,
            "updated_at": created_at,
        }

        self.session_root.mkdir(parents=True, exist_ok=True)
        staging_directory = Path(
            tempfile.mkdtemp(
                dir=self.session_root,
                prefix=f".{game_id}.",
                suffix=".tmp",
            )
        )
        try:
            self._write_snapshot(
                staging_directory,
                "module_reference",
                module_hash,
                module_bytes,
            )
            self._write_snapshot(
                staging_directory,
                "character_reference",
                character_hash,
                character_bytes,
            )
            write_json_atomic(staging_directory / "session.json", session)
            self._load_session_directory(staging_directory, game_id)
            self.directory_publisher(staging_directory, session_directory)
        except Exception as error:
            shutil.rmtree(staging_directory, ignore_errors=True)
            if isinstance(error, AgenticSessionPublishError):
                raise
            raise AgenticSessionPublishError("会话无法原子发布") from error

        session_file = session_directory / "session.json"

        return CreatedSession(
            game_id=game_id,
            session_directory=session_directory,
            session_file=session_file,
            opening_narration=opening_narration,
            session=session,
        )

    def load_session(self, game_id: str) -> LoadedSession:
        """只从已发布目录装载聚合及其内容寻址快照。"""

        safe_game_id = self._validated_game_id(game_id)
        session_directory = self.session_root / safe_game_id
        return self._load_session_directory(session_directory, safe_game_id)

    def _load_session_directory(
        self,
        session_directory: Path,
        expected_game_id: str,
    ) -> LoadedSession:
        """校验一个明确目录，不访问任何工作树参考文件。"""

        try:
            session = read_json(session_directory / "session.json")
        except (OSError, ValueError) as error:
            raise AgenticSessionLoadError("session.json 无法读取") from error
        if not isinstance(session, dict):
            raise AgenticSessionLoadError("session.json 必须是 JSON 对象")
        if session.get("schema_version") != SCHEMA_VERSION:
            raise AgenticSessionLoadError("session.json schema_version 不受支持")
        if session.get("game_id") != expected_game_id:
            raise AgenticSessionLoadError("session.json game_id 与目录不匹配")
        self._validate_session_basics(session)
        setup = session.get("setup")
        assert isinstance(setup, dict)
        self._validate_fact_and_turn_references(session, setup)
        self._validate_investigator_references(session)
        self._validate_mechanic_actor_references(session)
        module_hash = self._snapshot_hash(
            setup.get("module_reference_sha256"),
            "module_reference_sha256",
        )
        character_hash = self._snapshot_hash(
            setup.get("character_reference_sha256"),
            "character_reference_sha256",
        )
        module_reference = self._read_snapshot(
            session_directory,
            "module_reference",
            module_hash,
            "模组参考书",
        )
        character_reference = self._read_snapshot(
            session_directory,
            "character_reference",
            character_hash,
            "人物参考",
        )
        return LoadedSession(
            session_directory=session_directory,
            session=session,
            module_reference=module_reference,
            character_reference=character_reference,
        )

    @classmethod
    def _validate_session_basics(cls, session: Mapping[str, Any]) -> None:
        setup = session.get("setup")
        turns = session.get("turns")
        incomplete_turn = session.get("incomplete_turn")
        if (
            set(session) != _SESSION_FIELDS
            or session.get("module_id") != MODULE_ID
            or session.get("skill_catalog_version") != SKILL_CATALOG_VERSION
            or session.get("session_status") not in {"ongoing", "complete"}
            or not isinstance(setup, dict)
            or set(setup) != _SETUP_FIELDS
            or not isinstance(turns, list)
            or (
                incomplete_turn is not None
                and not isinstance(incomplete_turn, dict)
            )
            or (
                session.get("session_status") == "complete"
                and incomplete_turn is not None
            )
        ):
            raise AgenticSessionLoadError("session.json 基本结构无效")
        cls._load_required_string(setup.get("setup_id"), "setup_id")
        cls._load_required_string(
            setup.get("character_reference_revision"),
            "character_reference_revision",
        )
        cls._load_required_string(
            setup.get("opening_narration"),
            "opening_narration",
        )
        cls._validate_timestamp(setup.get("created_at"), "setup.created_at")
        cls._validate_timestamp(session.get("created_at"), "created_at")
        cls._validate_timestamp(session.get("updated_at"), "updated_at")

    @staticmethod
    def _validate_timestamp(value: object, label: str) -> None:
        if not isinstance(value, str) or not value.endswith("Z"):
            raise AgenticSessionLoadError(f"{label} 格式无效")
        try:
            parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
        except ValueError as error:
            raise AgenticSessionLoadError(f"{label} 格式无效") from error
        if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise AgenticSessionLoadError(f"{label} 格式无效")

    @classmethod
    def _validate_fact_and_turn_references(
        cls,
        session: Mapping[str, Any],
        setup: Mapping[str, Any],
    ) -> None:
        opening_fact_ids = setup.get("opening_fact_ids")
        facts = session.get("facts")
        if (
            not isinstance(opening_fact_ids, list)
            or not opening_fact_ids
            or not isinstance(facts, list)
        ):
            raise AgenticSessionLoadError(
                "opening_fact_ids 与开场事实不一致"
            )
        module_revision = cls._load_required_string(
            setup.get("module_reference_revision"),
            "module_reference_revision",
        )
        expected_source = f"{module_revision}#opening_minimum_canon"
        actual_opening_ids: list[str] = []
        all_fact_ids: set[str] = set()
        facts_by_id: dict[str, Mapping[str, Any]] = {}
        for fact in facts:
            fact_id, origin_kind = cls._validate_fact_record(
                fact,
                opening_source=expected_source,
            )
            if fact_id in all_fact_ids:
                raise AgenticSessionLoadError(
                    "FactRecord 格式无效"
                )
            all_fact_ids.add(fact_id)
            assert isinstance(fact, dict)
            facts_by_id[fact_id] = fact
            if origin_kind != "opening_canon":
                continue
            actual_opening_ids.append(fact_id)
        if (
            opening_fact_ids != actual_opening_ids
            or len(set(opening_fact_ids)) != len(opening_fact_ids)
        ):
            raise AgenticSessionLoadError(
                "opening_fact_ids 与开场事实不一致"
            )

        turns = session.get("turns")
        assert isinstance(turns, list)
        turns_by_id: dict[str, Mapping[str, Any]] = {}
        active_fact_ids = set(actual_opening_ids)
        declared_retirements: dict[str, tuple[str, str]] = {}
        for index, turn in enumerate(turns):
            turn_id, established_ids, retirements, turn_status = (
                cls._validate_committed_turn(turn)
            )
            if turn_id in turns_by_id:
                raise AgenticSessionLoadError("CommittedTurn 格式无效")
            turns_by_id[turn_id] = turn
            if turn_status == "complete" and index != len(turns) - 1:
                raise AgenticSessionLoadError("CommittedTurn 状态顺序无效")

            expected_established_ids = [
                fact_id
                for fact_id, fact in facts_by_id.items()
                if fact.get("origin", {}).get("kind") == "gm_turn"
                and fact.get("established_turn_id") == turn_id
            ]
            if established_ids != expected_established_ids:
                raise AgenticSessionLoadError("回合与事实引用不一致")

            for retirement in retirements:
                fact_id = retirement["fact_id"]
                reason = retirement["reason"]
                if fact_id not in active_fact_ids or fact_id in declared_retirements:
                    raise AgenticSessionLoadError("回合结束事实引用无效")
                declared_retirements[fact_id] = (turn_id, reason)
                active_fact_ids.remove(fact_id)
            for fact_id in established_ids:
                if fact_id not in facts_by_id or fact_id in active_fact_ids:
                    raise AgenticSessionLoadError("回合与事实引用不一致")
                active_fact_ids.add(fact_id)

        for fact_id, fact in facts_by_id.items():
            origin = fact["origin"]
            if origin["kind"] == "gm_turn":
                turn_id = fact["established_turn_id"]
                if turn_id not in turns_by_id:
                    raise AgenticSessionLoadError("GM 事实引用未知回合")
            recorded_retirement = declared_retirements.get(fact_id)
            if recorded_retirement is None:
                if (
                    fact["status"] != "active"
                    or fact["retired_turn_id"] is not None
                    or fact["retire_reason"] is not None
                    or fact_id not in active_fact_ids
                ):
                    raise AgenticSessionLoadError("FactRecord 结束历史不一致")
            elif (
                fact["status"] != "retired"
                or fact["retired_turn_id"] != recorded_retirement[0]
                or fact["retire_reason"] != recorded_retirement[1]
                or fact_id in active_fact_ids
            ):
                raise AgenticSessionLoadError("FactRecord 结束历史不一致")

        expected_status = turns[-1]["session_status"] if turns else "ongoing"
        if session.get("session_status") != expected_status:
            raise AgenticSessionLoadError("会话状态与回合历史不一致")
        incomplete_turn = session.get("incomplete_turn")
        if incomplete_turn is not None:
            cls._validate_incomplete_turn(incomplete_turn, set(turns_by_id))

    @classmethod
    def _validate_fact_record(
        cls,
        fact: object,
        *,
        opening_source: str,
    ) -> tuple[str, str]:
        if not isinstance(fact, dict) or set(fact) != _FACT_FIELDS:
            raise AgenticSessionLoadError("FactRecord 格式无效")
        fact_id = cls._load_required_string(fact.get("fact_id"), "fact_id")
        cls._load_required_string(fact.get("text"), "fact.text")
        if fact.get("visibility") not in {"public", "hidden"}:
            raise AgenticSessionLoadError("FactRecord 格式无效")
        if fact.get("status") not in {"active", "retired"}:
            raise AgenticSessionLoadError("FactRecord 格式无效")

        origin = fact.get("origin")
        if not isinstance(origin, dict) or set(origin) != _FACT_ORIGIN_FIELDS:
            raise AgenticSessionLoadError("FactRecord 格式无效")
        origin_kind = origin.get("kind")
        established_turn_id = fact.get("established_turn_id")
        if origin_kind == "opening_canon":
            if (
                origin.get("source_ref") != opening_source
                or established_turn_id is not None
            ):
                raise AgenticSessionLoadError(
                    "opening_fact_ids 与开场事实不一致"
                )
        elif origin_kind == "gm_turn":
            if (
                origin.get("source_ref") is not None
                or not isinstance(established_turn_id, str)
                or not established_turn_id.strip()
                or established_turn_id != established_turn_id.strip()
            ):
                raise AgenticSessionLoadError("FactRecord 格式无效")
        else:
            raise AgenticSessionLoadError("FactRecord 格式无效")

        status = fact.get("status")
        retired_turn_id = fact.get("retired_turn_id")
        retire_reason = fact.get("retire_reason")
        if status == "active" and (
            retired_turn_id is not None or retire_reason is not None
        ):
            raise AgenticSessionLoadError("FactRecord 格式无效")
        if status == "retired":
            cls._load_required_string(retired_turn_id, "retired_turn_id")
            cls._load_required_string(retire_reason, "retire_reason")
        return fact_id, origin_kind

    @classmethod
    def _validate_committed_turn(
        cls,
        turn: object,
    ) -> tuple[str, list[str], list[dict[str, str]], str]:
        if not isinstance(turn, dict) or set(turn) != _COMMITTED_TURN_FIELDS:
            raise AgenticSessionLoadError("CommittedTurn 格式无效")
        turn_id = cls._load_required_string(turn.get("turn_id"), "turn_id")
        cls._load_required_string(turn.get("player_input"), "turn.player_input")
        cls._load_required_string(turn.get("narration"), "turn.narration")
        cls._validate_timestamp(turn.get("committed_at"), "turn.committed_at")
        mechanics = turn.get("mechanics")
        if not isinstance(mechanics, list):
            raise AgenticSessionLoadError("CommittedTurn 格式无效")
        mechanic_ids: set[str] = set()
        for mechanic in mechanics:
            try:
                validate_check_result(mechanic)
            except (ValueError, TypeError, KeyError) as error:
                raise AgenticSessionLoadError("CommittedTurn 格式无效") from error
            assert isinstance(mechanic, dict)
            mechanic_id = mechanic["mechanic_id"]
            if mechanic_id in mechanic_ids:
                raise AgenticSessionLoadError("CommittedTurn 格式无效")
            mechanic_ids.add(mechanic_id)
            cls._validate_timestamp(
                mechanic.get("committed_at"),
                "mechanic.committed_at",
            )
        status = turn.get("session_status")
        if status not in {"ongoing", "complete"}:
            raise AgenticSessionLoadError("CommittedTurn 格式无效")

        established = turn.get("established_fact_ids")
        if (
            not isinstance(established, list)
            or any(
                not isinstance(fact_id, str)
                or not fact_id.strip()
                or fact_id != fact_id.strip()
                for fact_id in established
            )
            or len(set(established)) != len(established)
        ):
            raise AgenticSessionLoadError("CommittedTurn 格式无效")

        retirements_raw = turn.get("retirements")
        if not isinstance(retirements_raw, list):
            raise AgenticSessionLoadError("CommittedTurn 格式无效")
        retirements: list[dict[str, str]] = []
        for retirement in retirements_raw:
            if (
                not isinstance(retirement, dict)
                or set(retirement) != _RETIREMENT_FIELDS
            ):
                raise AgenticSessionLoadError("CommittedTurn 格式无效")
            fact_id = cls._load_required_string(
                retirement.get("fact_id"),
                "retirement.fact_id",
            )
            reason = cls._load_required_string(
                retirement.get("reason"),
                "retirement.reason",
            )
            retirements.append({"fact_id": fact_id, "reason": reason})
        if len({item["fact_id"] for item in retirements}) != len(retirements):
            raise AgenticSessionLoadError("CommittedTurn 格式无效")
        return turn_id, established, retirements, status

    @classmethod
    def _validate_incomplete_turn(
        cls,
        incomplete: Mapping[str, Any],
        committed_turn_ids: set[str],
    ) -> None:
        if set(incomplete) != _INCOMPLETE_TURN_FIELDS:
            raise AgenticSessionLoadError("IncompleteTurn 格式无效")
        turn_id = cls._load_required_string(
            incomplete.get("turn_id"),
            "incomplete_turn.turn_id",
        )
        cls._load_required_string(
            incomplete.get("player_input"),
            "incomplete_turn.player_input",
        )
        cls._validate_timestamp(
            incomplete.get("started_at"),
            "incomplete_turn.started_at",
        )
        cls._validate_timestamp(
            incomplete.get("attempt_started_at"),
            "incomplete_turn.attempt_started_at",
        )
        if turn_id in committed_turn_ids:
            raise AgenticSessionLoadError("未完成回合与已提交回合冲突")
        attempt_number = incomplete.get("attempt_number")
        round_trips_used = incomplete.get("round_trips_used")
        total_round_trips = incomplete.get("total_round_trips")
        structure_repairs_used = incomplete.get("structure_repairs_used")
        total_structure_repairs = incomplete.get("total_structure_repairs")
        counters = (
            attempt_number,
            round_trips_used,
            total_round_trips,
            structure_repairs_used,
            total_structure_repairs,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in counters
        ):
            raise AgenticSessionLoadError("IncompleteTurn 格式无效")
        assert isinstance(attempt_number, int)
        assert isinstance(round_trips_used, int)
        assert isinstance(total_round_trips, int)
        assert isinstance(structure_repairs_used, int)
        assert isinstance(total_structure_repairs, int)
        if (
            attempt_number < 1
            or round_trips_used > total_round_trips
            or structure_repairs_used > total_structure_repairs
        ):
            raise AgenticSessionLoadError("IncompleteTurn 格式无效")
        mechanics = incomplete.get("mechanics")
        interactions = incomplete.get("tool_interactions")
        if not isinstance(mechanics, list) or not isinstance(interactions, list):
            raise AgenticSessionLoadError("IncompleteTurn 格式无效")
        mechanics_by_id: dict[str, Mapping[str, Any]] = {}
        for mechanic in mechanics:
            try:
                validate_check_result(mechanic)
            except (ValueError, TypeError, KeyError) as error:
                raise AgenticSessionLoadError("IncompleteTurn 格式无效") from error
            assert isinstance(mechanic, dict)
            mechanic_id = mechanic["mechanic_id"]
            if mechanic_id in mechanics_by_id:
                raise AgenticSessionLoadError("IncompleteTurn 格式无效")
            mechanics_by_id[mechanic_id] = mechanic
            cls._validate_timestamp(
                mechanic.get("committed_at"),
                "mechanic.committed_at",
            )

        interactions_by_id: dict[str, Mapping[str, Any]] = {}
        successful_mechanic_ids: list[str] = []
        for interaction in interactions:
            tool_call_id, result = cls._validate_tool_interaction(interaction)
            if tool_call_id in interactions_by_id:
                raise AgenticSessionLoadError("IncompleteTurn 格式无效")
            assert isinstance(interaction, dict)
            interactions_by_id[tool_call_id] = interaction
            if result is not None:
                mechanic_id = result["mechanic_id"]
                if mechanics_by_id.get(mechanic_id) != result:
                    raise AgenticSessionLoadError("IncompleteTurn 格式无效")
                successful_mechanic_ids.append(mechanic_id)
        if successful_mechanic_ids != list(mechanics_by_id):
            raise AgenticSessionLoadError("IncompleteTurn 格式无效")
        cls._validate_model_profile(incomplete.get("model_profile"))
        cls._validate_attempt_limits(incomplete.get("attempt_limits"))
        cls._validate_incomplete_messages(
            incomplete.get("deepseek_messages"),
            interactions,
        )
        cls._validate_protocol_errors(
            incomplete.get("provider_protocol_errors")
        )
        last_failure = incomplete.get("last_failure")
        if last_failure is not None:
            if (
                not isinstance(last_failure, dict)
                or set(last_failure) != _LAST_FAILURE_FIELDS
            ):
                raise AgenticSessionLoadError("IncompleteTurn 格式无效")
            cls._load_required_string(last_failure.get("code"), "failure.code")
            cls._load_required_string(
                last_failure.get("message"),
                "failure.message",
            )

    @classmethod
    def _validate_model_profile(cls, profile: object) -> None:
        if not isinstance(profile, Mapping):
            raise AgenticSessionLoadError("model_profile 格式无效")
        try:
            validated_model_profile(profile, enabled_tools=("make_check",))
        except ModelProfileValidationError as error:
            raise AgenticSessionLoadError("model_profile 格式无效") from error

    @classmethod
    def _validate_attempt_limits(cls, limits: object) -> None:
        if not isinstance(limits, dict) or set(limits) != _ATTEMPT_LIMIT_FIELDS:
            raise AgenticSessionLoadError("attempt_limits 格式无效")
        if any(
            not cls._is_bounded_integer(
                limits.get(field),
                0 if field == "max_structure_repairs" else 1,
                1_000_000,
            )
            for field in _ATTEMPT_LIMIT_FIELDS
        ):
            raise AgenticSessionLoadError("attempt_limits 格式无效")

    @classmethod
    def _validate_tool_interaction(
        cls,
        interaction: object,
    ) -> tuple[str, Mapping[str, Any] | None]:
        if (
            not isinstance(interaction, dict)
            or set(interaction) != _TOOL_INTERACTION_FIELDS
        ):
            raise AgenticSessionLoadError("ToolInteraction 格式无效")
        tool_call_id = cls._load_required_string(
            interaction.get("tool_call_id"),
            "tool_call_id",
        )
        tool_name = cls._load_required_string(
            interaction.get("tool_name"),
            "tool_name",
        )
        arguments_raw = interaction.get("arguments_raw")
        arguments = interaction.get("arguments")
        if not isinstance(arguments_raw, str) or (
            arguments is not None and not isinstance(arguments, dict)
        ):
            raise AgenticSessionLoadError("ToolInteraction 格式无效")
        if arguments is not None:
            if tool_name != "make_check":
                raise AgenticSessionLoadError("ToolInteraction 格式无效")
            try:
                normalized = normalize_make_check_arguments(arguments_raw)
                persisted_normalized = normalize_make_check_arguments(
                    json.dumps(arguments, ensure_ascii=False, sort_keys=True)
                )
            except MakeCheckError as normalization_error:
                raise AgenticSessionLoadError(
                    "ToolInteraction 格式无效"
                ) from normalization_error
            except (TypeError, ValueError) as serialization_error:
                raise AgenticSessionLoadError(
                    "ToolInteraction 格式无效"
                ) from serialization_error
            if persisted_normalized != normalized:
                raise AgenticSessionLoadError("ToolInteraction 格式无效")

        ok = interaction.get("ok")
        result = interaction.get("result")
        tool_error_value = interaction.get("error")
        if not isinstance(ok, bool):
            raise AgenticSessionLoadError("ToolInteraction 格式无效")
        if ok:
            if (
                tool_name != "make_check"
                or not isinstance(arguments, dict)
                or tool_error_value is not None
            ):
                raise AgenticSessionLoadError("ToolInteraction 格式无效")
            try:
                validate_check_result(result)
            except (ValueError, TypeError, KeyError) as validation_error:
                raise AgenticSessionLoadError(
                    "ToolInteraction 格式无效"
                ) from validation_error
            assert isinstance(result, dict)
            if any(
                arguments.get(field) != result.get(field)
                for field in (
                    "actor_id",
                    "ability",
                    "difficulty",
                    "dice_adjustment",
                    "action",
                    "stakes",
                    "visibility",
                )
            ):
                raise AgenticSessionLoadError("ToolInteraction 格式无效")
            return tool_call_id, result
        if (
            result is not None
            or not isinstance(tool_error_value, dict)
            or set(tool_error_value) != _TOOL_ERROR_FIELDS
        ):
            raise AgenticSessionLoadError("ToolInteraction 格式无效")
        cls._load_required_string(
            tool_error_value.get("code"),
            "tool_error.code",
        )
        cls._load_required_string(
            tool_error_value.get("message"),
            "tool_error.message",
        )
        return tool_call_id, None

    @classmethod
    def _validate_incomplete_messages(
        cls,
        messages: object,
        interactions: list[Any],
    ) -> None:
        if not isinstance(messages, list) or len(messages) < 3:
            raise AgenticSessionLoadError("deepseek_messages 格式无效")
        interactions_by_id = {
            interaction["tool_call_id"]: interaction
            for interaction in interactions
            if isinstance(interaction, dict)
            and isinstance(interaction.get("tool_call_id"), str)
        }
        if len(interactions_by_id) != len(interactions):
            raise AgenticSessionLoadError("deepseek_messages 格式无效")
        expected_interaction_order = list(interactions_by_id)
        first_seen_interaction_ids: list[str] = []
        seen_interaction_ids: set[str] = set()
        message_index = 0
        while message_index < len(messages):
            message = messages[message_index]
            if not isinstance(message, dict):
                raise AgenticSessionLoadError("deepseek_messages 格式无效")
            role = message.get("role")
            if role in {"system", "user"}:
                if set(message) != {"role", "content"}:
                    raise AgenticSessionLoadError("deepseek_messages 格式无效")
                cls._load_required_string(message.get("content"), "message.content")
                message_index += 1
            elif role == "assistant":
                content = message.get("content")
                reasoning_content = message.get("reasoning_content")
                if (
                    set(message) != _ASSISTANT_MESSAGE_FIELDS
                    or reasoning_content is not None
                    and not isinstance(reasoning_content, str)
                ):
                    raise AgenticSessionLoadError("deepseek_messages 格式无效")
                tool_calls = message.get("tool_calls")
                if tool_calls == []:
                    if not isinstance(content, str) or not content.strip():
                        raise AgenticSessionLoadError(
                            "deepseek_messages 格式无效"
                        )
                    message_index += 1
                    continue
                if (
                    content is not None
                    or not isinstance(tool_calls, list)
                    or not tool_calls
                    or message_index + len(tool_calls) >= len(messages)
                ):
                    raise AgenticSessionLoadError("deepseek_messages 格式无效")
                response_ids: set[str] = set()
                for offset, call in enumerate(tool_calls):
                    if (
                        not isinstance(call, dict)
                        or set(call) != _TOOL_CALL_FIELDS
                        or call.get("type") != "function"
                        or not isinstance(call.get("id"), str)
                        or not call["id"]
                        or call["id"] != call["id"].strip()
                        or call["id"] in response_ids
                        or not isinstance(call.get("function"), dict)
                        or set(call["function"]) != _FUNCTION_CALL_FIELDS
                        or not isinstance(call["function"].get("name"), str)
                        or not call["function"]["name"]
                        or call["function"]["name"]
                        != call["function"]["name"].strip()
                        or not isinstance(call["function"].get("arguments"), str)
                    ):
                        raise AgenticSessionLoadError("deepseek_messages 格式无效")
                    response_ids.add(call["id"])
                    tool_message = messages[message_index + 1 + offset]
                    interaction = interactions_by_id.get(call["id"])
                    first_occurrence = call["id"] not in seen_interaction_ids
                    arguments_raw = call["function"]["arguments"]
                    arguments_match = isinstance(interaction, dict) and (
                        arguments_raw == interaction.get("arguments_raw")
                        if first_occurrence
                        else tool_call_matches_interaction(
                            interaction,
                            call["function"]["name"],
                            arguments_raw,
                        )
                    )
                    if (
                        not isinstance(tool_message, dict)
                        or set(tool_message) != _TOOL_MESSAGE_FIELDS
                        or tool_message.get("role") != "tool"
                        or not isinstance(interaction, dict)
                        or call.get("id") != interaction.get("tool_call_id")
                        or call["function"].get("name")
                        != interaction.get("tool_name")
                        or not arguments_match
                        or tool_message.get("tool_call_id")
                        != interaction.get("tool_call_id")
                        or tool_message.get("name") != interaction.get("tool_name")
                    ):
                        raise AgenticSessionLoadError("deepseek_messages 格式无效")
                    content_raw = tool_message.get("content")
                    if not isinstance(content_raw, str):
                        raise AgenticSessionLoadError("deepseek_messages 格式无效")
                    try:
                        envelope = json.loads(content_raw)
                    except json.JSONDecodeError as error:
                        raise AgenticSessionLoadError(
                            "deepseek_messages 格式无效"
                        ) from error
                    if (
                        not isinstance(envelope, dict)
                        or set(envelope) != _TOOL_ENVELOPE_FIELDS
                        or envelope.get("tool_call_id")
                        != interaction.get("tool_call_id")
                        or envelope.get("tool_name")
                        != interaction.get("tool_name")
                        or envelope.get("ok") is not interaction.get("ok")
                        or envelope.get("result") != interaction.get("result")
                        or envelope.get("error") != interaction.get("error")
                    ):
                        raise AgenticSessionLoadError("deepseek_messages 格式无效")
                    if first_occurrence:
                        seen_interaction_ids.add(call["id"])
                        first_seen_interaction_ids.append(call["id"])
                message_index += 1 + len(tool_calls)
            else:
                raise AgenticSessionLoadError("deepseek_messages 格式无效")
        if first_seen_interaction_ids != expected_interaction_order:
            raise AgenticSessionLoadError("deepseek_messages 格式无效")

    @classmethod
    def _validate_protocol_errors(cls, errors: object) -> None:
        if not isinstance(errors, list):
            raise AgenticSessionLoadError("provider_protocol_errors 格式无效")
        for error in errors:
            if (
                not isinstance(error, dict)
                or set(error) != _PROVIDER_PROTOCOL_ERROR_FIELDS
            ):
                raise AgenticSessionLoadError("provider_protocol_errors 格式无效")
            cls._load_required_string(error.get("code"), "protocol_error.code")
            cls._load_required_string(
                error.get("message"),
                "protocol_error.message",
            )
            serialized = cls._load_required_string(
                error.get("model_response_json"),
                "protocol_error.model_response_json",
            )
            try:
                json.loads(serialized)
            except json.JSONDecodeError as parse_error:
                raise AgenticSessionLoadError(
                    "provider_protocol_errors 格式无效"
                ) from parse_error
            cls._validate_timestamp(
                error.get("recorded_at"),
                "protocol_error.recorded_at",
            )

    @classmethod
    def _validate_investigator_references(
        cls,
        session: Mapping[str, Any],
    ) -> None:
        selected_id = session.get("selected_investigator_id")
        profile = session.get("investigator_profile")
        actors = session.get("actors")
        display_names = session.get("actor_display_names")
        catalog_version = session.get("skill_catalog_version")
        if (
            not isinstance(selected_id, str)
            or not selected_id
            or not isinstance(profile, dict)
            or not isinstance(actors, list)
            or not isinstance(display_names, dict)
            or not isinstance(catalog_version, str)
            or not catalog_version
        ):
            raise AgenticSessionLoadError("调查员引用不一致")
        cls._validate_profile(profile)
        cls._validate_display_names(display_names)
        seen_actor_ids: set[str] = set()
        for actor in actors:
            actor_id = cls._validate_actor_sheet(actor, catalog_version)
            if actor_id in seen_actor_ids:
                raise AgenticSessionLoadError("ActorSheet 格式无效")
            seen_actor_ids.add(actor_id)
        matching_actors = [
            actor
            for actor in actors
            if isinstance(actor, dict) and actor.get("actor_id") == selected_id
        ]
        if (
            len(matching_actors) != 1
            or matching_actors[0].get("role") != "investigator"
            or matching_actors[0].get("skill_catalog_version") != catalog_version
            or profile.get("actor_id") != selected_id
            or display_names.get(selected_id) != profile.get("display_name")
        ):
            raise AgenticSessionLoadError("调查员引用不一致")
        for actor in actors:
            if display_names.get(actor.get("actor_id")) is None:
                raise AgenticSessionLoadError("调查员引用不一致")

    @classmethod
    def _validate_mechanic_actor_references(
        cls,
        session: Mapping[str, Any],
    ) -> None:
        actors = session.get("actors")
        turns = session.get("turns")
        if not isinstance(actors, list) or not isinstance(turns, list):
            raise AgenticSessionLoadError("机械与冻结角色卡不一致")
        actors_by_id = {
            actor["actor_id"]: actor
            for actor in actors
            if isinstance(actor, dict)
        }
        records: list[object] = [
            mechanic
            for turn in turns
            if isinstance(turn, dict)
            for mechanic in turn.get("mechanics", [])
        ]
        incomplete = session.get("incomplete_turn")
        if isinstance(incomplete, dict):
            records.extend(incomplete.get("mechanics", []))

        seen_mechanic_ids: set[str] = set()
        for mechanic in records:
            if not isinstance(mechanic, dict):
                raise AgenticSessionLoadError("机械与冻结角色卡不一致")
            mechanic_id = mechanic.get("mechanic_id")
            actor = actors_by_id.get(mechanic.get("actor_id"))
            ability = mechanic.get("ability")
            if (
                not isinstance(mechanic_id, str)
                or mechanic_id in seen_mechanic_ids
                or not isinstance(actor, dict)
                or not isinstance(ability, str)
            ):
                raise AgenticSessionLoadError("机械与冻结角色卡不一致")
            seen_mechanic_ids.add(mechanic_id)
            attributes = actor.get("attributes")
            skills = actor.get("skills")
            frozen_value: object = None
            if isinstance(attributes, dict) and ability in attributes:
                frozen_value = attributes[ability]
            elif isinstance(skills, dict) and ability in skills:
                frozen_value = skills[ability]
            if mechanic.get("ability_value") != frozen_value:
                raise AgenticSessionLoadError("机械与冻结角色卡不一致")

    @classmethod
    def _validate_profile(cls, profile: Mapping[str, Any]) -> None:
        if set(profile) != _PROFILE_FIELDS:
            raise AgenticSessionLoadError("InvestigatorProfile 格式无效")
        cls._load_required_string(profile.get("actor_id"), "profile.actor_id")
        cls._load_required_string(
            profile.get("display_name"),
            "profile.display_name",
        )
        for field in _PROFILE_FIELDS - {"actor_id", "display_name"}:
            value = profile.get(field)
            if value is not None:
                cls._load_required_string(value, f"profile.{field}")

    @classmethod
    def _validate_display_names(cls, display_names: Mapping[str, Any]) -> None:
        if not display_names:
            raise AgenticSessionLoadError("actor_display_names 格式无效")
        for actor_id, display_name in display_names.items():
            cls._load_required_string(actor_id, "actor_display_names actor_id")
            cls._load_required_string(
                display_name,
                "actor_display_names display_name",
            )

    @classmethod
    def _validate_actor_sheet(
        cls,
        actor: object,
        catalog_version: str,
    ) -> str:
        if not isinstance(actor, dict) or set(actor) != _ACTOR_FIELDS:
            raise AgenticSessionLoadError("ActorSheet 格式无效")
        actor_id = cls._load_required_string(actor.get("actor_id"), "actor_id")
        attributes = actor.get("attributes")
        skills = actor.get("skills")
        hp = actor.get("hp")
        san = actor.get("san")
        luck = actor.get("luck")
        if (
            actor.get("role") not in {"investigator", "npc"}
            or actor.get("skill_catalog_version") != catalog_version
            or not isinstance(attributes, dict)
            or set(attributes) != _ATTRIBUTE_FIELDS
            or not all(
                cls._is_bounded_integer(value, 0, 100)
                for value in attributes.values()
            )
            or not isinstance(skills, dict)
            or not skills
            or not all(
                isinstance(key, str)
                and bool(key)
                and key == key.strip()
                and cls._is_bounded_integer(value, 0, 100)
                for key, value in skills.items()
            )
            or not isinstance(hp, dict)
            or set(hp) != {"current", "max"}
            or not cls._is_bounded_integer(hp.get("max"), 1, 100)
            or not cls._is_bounded_integer(hp.get("current"), 0, hp.get("max"))
            or not isinstance(san, dict)
            or set(san) != {"current", "max", "session_loss"}
            or not cls._is_bounded_integer(san.get("max"), 0, 99)
            or not cls._is_bounded_integer(san.get("current"), 0, san.get("max"))
            or not cls._is_bounded_integer(san.get("session_loss"), 0, 99)
            or not isinstance(luck, dict)
            or set(luck) != {"current"}
            or not cls._is_bounded_integer(luck.get("current"), 0, 99)
            or not cls._is_bounded_integer(actor.get("armor"), 0, 100)
        ):
            raise AgenticSessionLoadError("ActorSheet 格式无效")
        return actor_id

    @staticmethod
    def _is_bounded_integer(value: object, minimum: int, maximum: object) -> bool:
        return (
            isinstance(maximum, int)
            and not isinstance(maximum, bool)
            and isinstance(value, int)
            and not isinstance(value, bool)
            and minimum <= value <= maximum
        )

    def _new_game_id(self) -> str:
        return self._validated_game_id(self.game_id_factory())

    @staticmethod
    def _validated_game_id(game_id: object) -> str:
        if (
            not isinstance(game_id, str)
            or not game_id
            or game_id != game_id.strip()
            or re.fullmatch(r"[A-Za-z0-9_-]+", game_id) is None
        ):
            raise AgenticSessionConflictError("game_id 必须是安全的非空标识符")
        return game_id

    @staticmethod
    def _snapshot_hash(value: object, label: str) -> str:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[0-9a-f]{64}", value) is None
        ):
            raise AgenticSessionLoadError(f"{label} 格式无效")
        return value

    @staticmethod
    def _load_required_string(value: object, label: str) -> str:
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise AgenticSessionLoadError(f"{label} 格式无效")
        return value

    @staticmethod
    def _read_snapshot(
        session_directory: Path,
        kind: str,
        sha256: str,
        label: str,
    ) -> str:
        snapshot = session_directory / "snapshots" / kind / f"{sha256}.md"
        try:
            content = snapshot.read_bytes()
        except OSError as error:
            raise AgenticSessionLoadError(f"{label}快照无法读取") from error
        if hashlib.sha256(content).hexdigest() != sha256:
            raise AgenticSessionLoadError(f"{label}快照哈希不匹配")
        try:
            return content.decode("utf-8")
        except UnicodeError as error:
            raise AgenticSessionLoadError(f"{label}快照无法读取") from error

    @classmethod
    def _build_profile(cls, request: NewSessionRequest) -> dict[str, Any]:
        return {
            "actor_id": cls._required_string(
                request.investigator_id,
                "investigator_id",
            ),
            "display_name": cls._required_string(
                request.display_name,
                "display_name",
            ),
            "honorific": cls._optional_string(request.honorific, "honorific"),
            "pronouns": cls._optional_string(request.pronouns, "pronouns"),
            "occupation": cls._optional_string(request.occupation, "occupation"),
            "appearance": cls._optional_string(request.appearance, "appearance"),
            "background_hook": cls._optional_string(
                request.background_hook,
                "background_hook",
            ),
            "keepsake": cls._optional_string(request.keepsake, "keepsake"),
        }

    def _build_actor(
        self,
        actor_id: str,
        catalog: Mapping[str, Any],
        templates: Mapping[str, Any],
        catalog_version: str,
    ) -> dict[str, Any]:
        template_version = self._required_string(
            templates.get("skill_catalog_version"),
            "角色模板 skill_catalog_version",
        )
        if template_version != catalog_version:
            raise AgenticSessionSourceError("角色模板与技能目录版本不一致")
        actor_templates = templates.get("actors")
        if not isinstance(actor_templates, list):
            raise AgenticSessionSourceError("角色模板 actors 必须是数组")
        template = next(
            (
                item
                for item in actor_templates
                if isinstance(item, dict) and item.get("actor_id") == actor_id
            ),
            None,
        )
        if template is None or template.get("role") != "investigator":
            raise AgenticSessionSourceError("未找到可选的预生成调查员卡")

        attributes = self._integer_map(template.get("attributes"), "attributes")
        expected_attributes = {
            "strength",
            "constitution",
            "size",
            "dexterity",
            "appearance",
            "intelligence",
            "power",
            "education",
        }
        if set(attributes) != expected_attributes:
            raise AgenticSessionSourceError("调查员卡必须包含八项 COC 属性")
        skill_definitions = catalog.get("skills")
        if not isinstance(skill_definitions, dict) or not skill_definitions:
            raise AgenticSessionSourceError("技能目录 skills 必须是非空对象")
        skills = {
            key: self._base_skill_value(key, value, attributes)
            for key, value in skill_definitions.items()
        }
        overrides = self._integer_map(
            template.get("skill_overrides"),
            "skill_overrides",
        )
        unknown_overrides = set(overrides).difference(skills)
        if unknown_overrides:
            raise AgenticSessionSourceError("角色模板包含未知技能键")
        skills.update(overrides)

        return {
            "actor_id": actor_id,
            "role": "investigator",
            "skill_catalog_version": catalog_version,
            "attributes": attributes,
            "skills": skills,
            "hp": self._resource_pair(template.get("hp"), "hp", maximum=100),
            "san": self._san_resource(template.get("san")),
            "luck": self._luck_resource(template.get("luck")),
            "armor": self._bounded_integer(template.get("armor"), "armor", 0, 100),
        }

    @classmethod
    def _base_skill_value(
        cls,
        key: object,
        definition: object,
        attributes: Mapping[str, int],
    ) -> int:
        cls._required_string(key, "技能键")
        if not isinstance(definition, dict):
            raise AgenticSessionSourceError("技能定义必须是对象")
        base = definition.get("base")
        if not isinstance(base, dict):
            raise AgenticSessionSourceError("技能基础值定义必须是对象")
        if base.get("kind") == "fixed":
            return cls._bounded_integer(base.get("value"), "技能基础值", 0, 100)
        if (
            base.get("kind") == "derived"
            and base.get("formula") == "floor(dexterity / 2)"
        ):
            return attributes["dexterity"] // 2
        raise AgenticSessionSourceError("技能目录包含不支持的基础值公式")

    @classmethod
    def _build_opening_facts(
        cls,
        fixture: Mapping[str, Any],
        *,
        source_ref: str,
    ) -> list[dict[str, Any]]:
        fact_texts = fixture.get("opening_facts")
        if not isinstance(fact_texts, list) or not fact_texts:
            raise AgenticSessionSourceError("opening_facts 必须是非空数组")
        facts: list[dict[str, Any]] = []
        for index, value in enumerate(fact_texts, start=1):
            facts.append(
                {
                    "fact_id": f"fact_{index:04d}",
                    "text": cls._required_string(value, "opening_fact"),
                    "visibility": "public",
                    "status": "active",
                    "established_turn_id": None,
                    "origin": {
                        "kind": "opening_canon",
                        "source_ref": source_ref,
                    },
                    "retired_turn_id": None,
                    "retire_reason": None,
                }
            )
        return facts

    @classmethod
    def _actor_display_names(cls, fixture: Mapping[str, Any]) -> dict[str, str]:
        value = fixture.get("actor_display_names")
        if not isinstance(value, dict):
            raise AgenticSessionSourceError("actor_display_names 必须是对象")
        return {
            cls._required_string(
                actor_id,
                "actor_display_names 键",
            ): cls._required_string(display_name, "actor_display_names 值")
            for actor_id, display_name in value.items()
        }

    @staticmethod
    def _write_snapshot(
        session_directory: Path,
        kind: str,
        sha256: str,
        content: bytes,
    ) -> None:
        snapshot = session_directory / "snapshots" / kind / f"{sha256}.md"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(content)
        snapshot.chmod(0o444)

    @staticmethod
    def _timestamp(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            raise AgenticSessionSourceError("clock 必须返回带时区的时间")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _load_object(path: Path, label: str) -> dict[str, Any]:
        try:
            value = read_json(path)
        except (OSError, ValueError) as error:
            raise AgenticSessionSourceError(f"{label}无法读取") from error
        if not isinstance(value, dict):
            raise AgenticSessionSourceError(f"{label}必须是 JSON 对象")
        return value

    @staticmethod
    def _read_bytes(path: Path, label: str) -> bytes:
        try:
            return path.read_bytes()
        except OSError as error:
            raise AgenticSessionSourceError(f"{label}无法读取") from error

    @classmethod
    def _integer_map(cls, value: object, label: str) -> dict[str, int]:
        if not isinstance(value, dict):
            raise AgenticSessionSourceError(f"{label} 必须是对象")
        return {
            cls._required_string(key, f"{label} 键"): cls._bounded_integer(
                item,
                f"{label} 值",
                0,
                100,
            )
            for key, item in value.items()
        }

    @classmethod
    def _resource_pair(
        cls,
        value: object,
        label: str,
        *,
        maximum: int,
    ) -> dict[str, int]:
        if not isinstance(value, dict) or set(value) != {"current", "max"}:
            raise AgenticSessionSourceError(f"{label} 必须包含 current 和 max")
        max_value = cls._bounded_integer(value["max"], f"{label}.max", 1, maximum)
        current = cls._bounded_integer(
            value["current"],
            f"{label}.current",
            0,
            max_value,
        )
        return {"current": current, "max": max_value}

    @classmethod
    def _san_resource(cls, value: object) -> dict[str, int]:
        resource = cls._resource_pair(value, "san", maximum=99)
        return {**resource, "session_loss": 0}

    @classmethod
    def _luck_resource(cls, value: object) -> dict[str, int]:
        if not isinstance(value, dict) or set(value) != {"current"}:
            raise AgenticSessionSourceError("luck 必须只包含 current")
        return {
            "current": cls._bounded_integer(value["current"], "luck.current", 0, 99)
        }

    @staticmethod
    def _bounded_integer(value: object, label: str, minimum: int, maximum: int) -> int:
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < minimum
            or value > maximum
        ):
            raise AgenticSessionSourceError(
                f"{label} 必须是 {minimum} 到 {maximum} 的整数"
            )
        return value

    @staticmethod
    def _required_string(value: object, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise AgenticSessionSourceError(f"{label} 必须是非空字符串")
        return value.strip()

    @staticmethod
    def _optional_string(value: object, label: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise AgenticSessionSourceError(f"{label} 必须是非空字符串或 null")
        return value.strip()
