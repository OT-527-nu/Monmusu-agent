"""负责初始化游戏并编排一次完整的外层回合。"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from monmusu_agent.agent import (
    GameMasterAgent,
    GameMasterAgentError,
    GameMasterDraft,
    GameMasterStateView,
)
from monmusu_agent.config import AppPaths
from monmusu_agent.rules import CheckLedger
from monmusu_agent.storage import read_json, write_json
from monmusu_agent.tools import ToolExecutor, ToolTraceEntry, TurnContext


_MODEL_STRATEGIES = frozenset({"fast", "dramatic", "urgent"})
_USER_PUBLIC_STATE_FIELDS = (
    "character_id",
    "background_hook",
    "specialty",
    "skills",
    "hp",
    "sanity",
    "pressure",
    "conditions",
)
_CHARACTER_PUBLIC_STATE_FIELDS = (
    "hp",
    "sanity",
    "pressure",
    "conditions",
    "speech_register",
)
_DEGRADED_NARRATION = (
    "系统暂时无法完成本轮叙事；已经发生的检定与状态变化仍然有效。"
)
_DEGRADED_DRAFT = GameMasterDraft(
    strategy="degraded",
    narration=_DEGRADED_NARRATION,
    suggested_actions=(),
)


@dataclass(frozen=True)
class GameMasterTurnResult:
    """保存由引擎从候选叙事与可信工具结果组装出的回合结果。"""

    turn_id: str
    strategy: str
    narration: str
    character_turns: tuple[Mapping[str, Any], ...]
    checks: tuple[Mapping[str, Any], ...]
    committed_effects: tuple[Mapping[str, Any], ...]
    suggested_actions: tuple[str, ...]
    ending_id: str | None


@dataclass(frozen=True)
class GameTurnOutcome:
    """向调用层返回用户可见结果、可信轨迹与降级状态。"""

    result: GameMasterTurnResult
    tool_trace: tuple[ToolTraceEntry, ...]
    degraded: bool
    failure_code: str | None


class GameEngineConfigurationError(RuntimeError):
    """表示外层回合缺少运行所需的注入依赖。"""


class GameInputError(ValueError):
    """表示用户原文不满足外层回合的输入边界。"""


class GameEndedError(RuntimeError):
    """表示已经写入结局的游戏不能继续启动新回合。"""


class GameStateError(RuntimeError):
    """表示正式 GameState 无法形成可信回合快照。"""


class GameMemoryError(RuntimeError):
    """表示本局 Memory 缺失、损坏或不符合读取契约。"""


class GameStaticDataError(RuntimeError):
    """表示模组或角色静态配置无法支持本轮运行。"""


@dataclass
class GameEngine:
    """根据剧本初始化游戏，并协调 GM 与可信工具模块。"""

    paths: AppPaths
    agent: GameMasterAgent | None = None
    tool_executor: ToolExecutor | None = None
    turn_id_factory: Callable[[], str] | None = None

    def initialize(self) -> dict:
        """生成初始游戏状态与记忆，并将它们写入运行时目录。"""

        module = read_json(self.paths.module_file)
        characters = read_json(self.paths.characters_file)

        # 游戏状态只保存推进规则所需的事实，叙事记忆则单独持久化。
        state = {
            "schema_version": "1.0",
            "game_id": "game_0001",
            "module_id": module["module_id"],
            "state_version": 0,
            "current_scene": module["starting_scene"],
            "user_character": module["user_character"],
            "characters": self._initial_characters(characters),
            "clues_found": [],
            "accessible_locations": module["accessible_locations"],
            "flags": {},
            "threat_clock": module["threat_clock"],
            "ending_id": None
        }
        memory = {
            "schema_version": "1.0",
            "game_id": state["game_id"],
            "public_memory": [],
            "private_memory_by_character": {
                character["character_id"]: [] for character in characters
            },
            "relationship_state": {
                character["character_id"]: {
                    "stage": character["initial_state"]["relationship_stage"],
                    "events": [],
                    "pending_echo": None
                }
                for character in characters
            },
            "unresolved_questions": [],
            "turn_log": []
        }

        write_json(self.paths.game_state_file, state)
        write_json(self.paths.memory_file, memory)
        CheckLedger(self.paths.check_records_file).reset()
        return state

    def opening_text(self) -> str:
        """返回当前 MVP 剧本的固定开场旁白。"""

        return read_json(self.paths.module_file)["opening_text"]

    def run_turn(self, input_text: str) -> GameTurnOutcome:
        """同步运行一次用户输入驱动的完整 GM 回合。"""

        if (
            not isinstance(input_text, str)
            or not input_text.strip()
            or len(input_text) > 4000
        ):
            raise GameInputError("用户输入必须是 1 至 4000 字符的非空文本")
        if (
            self.agent is None
            or self.tool_executor is None
            or self.turn_id_factory is None
        ):
            raise GameEngineConfigurationError("GameEngine 尚未配置运行依赖")

        state = self._load_game_state()
        module = self._load_static_json(self.paths.module_file, "模组")
        characters = self._load_static_json(self.paths.characters_file, "角色配置")
        self._validate_module(module)
        character_ids = self._validate_characters(characters)
        self._validate_game_state(state, module)
        if frozenset(state["characters"]) != character_ids:
            raise GameStaticDataError("角色配置与 GameState 队友不匹配")
        memory = self._load_memory(state["game_id"])
        if state.get("ending_id") is not None:
            raise GameEndedError("游戏已经结束，不能再启动新回合")

        turn_id = self.turn_id_factory()
        context = TurnContext(
            turn_id=turn_id,
            input_text=input_text,
            initial_game_state=state,
            max_tool_steps=8,
            tool_limits={"request_check": 2, "apply_effect": 2},
        )
        session = self.tool_executor.start_turn(context)
        degraded = False
        failure_code: str | None = None
        try:
            draft = self.agent.run(
                context,
                session,
                state_view=self._build_state_view(state, module),
                scene_context=self._build_scene_context(state, module),
                public_memory=tuple(copy.deepcopy(memory["public_memory"])),
            )
        except GameMasterAgentError as error:
            draft = _DEGRADED_DRAFT
            degraded = True
            failure_code = error.code
        else:
            if not self._is_valid_draft(draft):
                draft = _DEGRADED_DRAFT
                degraded = True
                failure_code = "invalid_draft"
        final_state = session.final_state_snapshot
        trace = session.trace
        checks, committed_effects = self._aggregate_trusted_results(trace)
        return GameTurnOutcome(
            result=GameMasterTurnResult(
                turn_id=turn_id,
                strategy=draft.strategy,
                narration=draft.narration,
                character_turns=(),
                checks=checks,
                committed_effects=committed_effects,
                suggested_actions=draft.suggested_actions,
                ending_id=final_state["ending_id"],
            ),
            tool_trace=trace,
            degraded=degraded,
            failure_code=failure_code,
        )

    @staticmethod
    def _is_valid_draft(draft: object) -> bool:
        """验证模型只能提供三项候选叙事字段。"""

        return (
            isinstance(draft, GameMasterDraft)
            and draft.strategy in _MODEL_STRATEGIES
            and isinstance(draft.narration, str)
            and bool(draft.narration.strip())
            and isinstance(draft.suggested_actions, tuple)
            and all(
                isinstance(action, str) and bool(action.strip())
                for action in draft.suggested_actions
            )
        )

    @staticmethod
    def _aggregate_trusted_results(
        trace: tuple[ToolTraceEntry, ...],
    ) -> tuple[
        tuple[Mapping[str, Any], ...],
        tuple[Mapping[str, Any], ...],
    ]:
        """按执行顺序提取正式检定，并按 commit_id 去重已提交效果。"""

        checks: list[Mapping[str, Any]] = []
        committed_effects: list[Mapping[str, Any]] = []
        seen_commit_ids: set[str] = set()
        for entry in trace:
            result = entry.tool_result
            if not result.ok or result.data is None:
                continue
            if result.data.get("kind") == "check_result":
                checks.append(result.data)
                continue
            if (
                result.data.get("kind") != "commit_result"
                or result.data.get("status")
                not in {"applied", "already_applied"}
            ):
                continue
            commit_id = result.data.get("commit_id")
            if not isinstance(commit_id, str) or commit_id in seen_commit_ids:
                continue
            seen_commit_ids.add(commit_id)
            committed_effects.append(result.data)
        return tuple(checks), tuple(committed_effects)

    @staticmethod
    def _validate_memory(memory: object, game_id: str) -> dict[str, Any]:
        """校验当前只读 Memory 切片依赖的字段。"""

        if not isinstance(memory, dict):
            raise GameMemoryError("Memory 必须是对象")
        if memory.get("schema_version") != "1.0":
            raise GameMemoryError("Memory schema_version 无效")
        if memory.get("game_id") != game_id:
            raise GameMemoryError("Memory game_id 与 GameState 不匹配")
        if not isinstance(memory.get("public_memory"), list):
            raise GameMemoryError("Memory public_memory 必须是数组")
        return memory

    def _load_memory(self, game_id: str) -> dict[str, Any]:
        """读取本局 Memory，并把可预期的存储损坏归一为领域错误。"""

        try:
            memory = read_json(self.paths.memory_file)
        except (OSError, json.JSONDecodeError) as error:
            raise GameMemoryError("Memory 文件缺失或损坏") from error
        return self._validate_memory(memory, game_id)

    def _load_game_state(self) -> Any:
        """读取 GameState，并把可预期的存储损坏归一为领域错误。"""

        try:
            return read_json(self.paths.game_state_file)
        except (OSError, json.JSONDecodeError) as error:
            raise GameStateError("GameState 文件缺失或损坏") from error

    @staticmethod
    def _load_static_json(path: Path, label: str) -> Any:
        """读取静态 JSON，并为缺失与损坏提供稳定错误类型。"""

        try:
            return read_json(path)
        except (OSError, json.JSONDecodeError) as error:
            raise GameStaticDataError(f"{label}文件缺失或损坏") from error

    @staticmethod
    def _validate_module(module: object) -> None:
        """校验模型场景投影依赖的最小模组定义。"""

        if not isinstance(module, dict):
            raise GameStaticDataError("模组必须是对象")
        module_id = module.get("module_id")
        if not isinstance(module_id, str) or not module_id:
            raise GameStaticDataError("模组 module_id 无效")
        raw_scenes = module.get("scenes")
        if not isinstance(raw_scenes, list) or not raw_scenes:
            raise GameStaticDataError("模组 scenes 必须是非空数组")
        scene_ids: set[str] = set()
        for scene in raw_scenes:
            if not isinstance(scene, dict):
                raise GameStaticDataError("模组场景必须是对象")
            scene_id = scene.get("scene_id")
            if (
                not isinstance(scene_id, str)
                or not scene_id
                or scene_id in scene_ids
            ):
                raise GameStaticDataError("模组场景标识无效或重复")
            scene_ids.add(scene_id)
            for field_name in (
                "public_facts",
                "interactions",
                "boundaries",
                "discovery_opportunities",
            ):
                if not isinstance(scene.get(field_name), list):
                    raise GameStaticDataError(
                        f"模组场景 {scene_id}.{field_name} 必须是数组"
                    )
        if module.get("starting_scene") not in scene_ids:
            raise GameStaticDataError("模组 starting_scene 没有场景定义")

        clue_definitions = module.get("clue_definitions")
        if not isinstance(clue_definitions, dict):
            raise GameStaticDataError("模组 clue_definitions 必须是对象")
        for clue_id, definition in clue_definitions.items():
            if (
                not isinstance(clue_id, str)
                or not clue_id
                or not isinstance(definition, dict)
                or not isinstance(definition.get("title"), str)
                or not isinstance(definition.get("public_text"), str)
            ):
                raise GameStaticDataError("模组包含无效线索定义")
        visible_flag_ids = module.get("gm_visible_flag_ids")
        if not isinstance(visible_flag_ids, list) or not all(
            isinstance(flag_id, str) and flag_id for flag_id in visible_flag_ids
        ):
            raise GameStaticDataError("模组 gm_visible_flag_ids 必须是字符串数组")

    @staticmethod
    def _validate_characters(characters: object) -> frozenset[str]:
        """校验角色配置可提供唯一角色与开场状态。"""

        if not isinstance(characters, list) or not characters:
            raise GameStaticDataError("角色配置必须是非空数组")
        character_ids: set[str] = set()
        for character in characters:
            if not isinstance(character, dict):
                raise GameStaticDataError("角色配置项必须是对象")
            character_id = character.get("character_id")
            if (
                not isinstance(character_id, str)
                or not character_id
                or character_id in character_ids
                or not isinstance(character.get("skills"), dict)
                or not isinstance(character.get("initial_state"), dict)
            ):
                raise GameStaticDataError("角色配置标识、技能或初始状态无效")
            character_ids.add(character_id)
        return frozenset(character_ids)

    @staticmethod
    def _validate_game_state(
        state: object,
        module: Mapping[str, Any],
    ) -> None:
        """校验本轮投影和工具上下文依赖的最小正式状态契约。"""

        if not isinstance(state, dict):
            raise GameStateError("GameState 必须是对象")
        if state.get("schema_version") != "1.0":
            raise GameStateError("GameState schema_version 无效")
        game_id = state.get("game_id")
        if not isinstance(game_id, str) or not game_id:
            raise GameStateError("GameState game_id 无效")
        if state.get("module_id") != module.get("module_id"):
            raise GameStateError("GameState module_id 与当前模组不匹配")
        state_version = state.get("state_version")
        if (
            not isinstance(state_version, int)
            or isinstance(state_version, bool)
            or state_version < 0
        ):
            raise GameStateError("GameState state_version 无效")

        raw_scenes = module.get("scenes")
        if not isinstance(raw_scenes, list):
            raise GameStateError("当前模组缺少场景定义")
        scene_ids = {
            scene.get("scene_id")
            for scene in raw_scenes
            if isinstance(scene, dict)
        }
        if state.get("current_scene") not in scene_ids:
            raise GameStateError("GameState current_scene 无效")
        if not isinstance(state.get("user_character"), dict):
            raise GameStateError("GameState user_character 无效")
        characters = state.get("characters")
        if not isinstance(characters, dict) or not all(
            isinstance(character_id, str)
            and character_id
            and isinstance(character_state, dict)
            for character_id, character_state in characters.items()
        ):
            raise GameStateError("GameState characters 无效")
        clues_found = state.get("clues_found")
        clue_definitions = module.get("clue_definitions", {})
        if not isinstance(clues_found, list) or not all(
            isinstance(clue_id, str)
            and clue_id
            and clue_id in clue_definitions
            for clue_id in clues_found
        ):
            raise GameStateError("GameState clues_found 无效")
        accessible_locations = state.get("accessible_locations")
        if not isinstance(accessible_locations, list) or not all(
            isinstance(scene_id, str) and scene_id in scene_ids
            for scene_id in accessible_locations
        ):
            raise GameStateError("GameState accessible_locations 无效")
        if not isinstance(state.get("flags"), dict):
            raise GameStateError("GameState flags 无效")

        threat_clock = state.get("threat_clock")
        if not isinstance(threat_clock, dict):
            raise GameStateError("GameState threat_clock 无效")
        clock_id = threat_clock.get("clock_id")
        value = threat_clock.get("value")
        maximum = threat_clock.get("maximum")
        if (
            not isinstance(clock_id, str)
            or not clock_id
            or not isinstance(value, int)
            or isinstance(value, bool)
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or maximum < 1
            or not 0 <= value <= maximum
        ):
            raise GameStateError("GameState threat_clock 无效")
        ending_id = state.get("ending_id")
        if ending_id is not None and (
            not isinstance(ending_id, str) or not ending_id
        ):
            raise GameStateError("GameState ending_id 无效")

    @staticmethod
    def _build_state_view(
        state: Mapping[str, Any],
        module: Mapping[str, Any],
    ) -> GameMasterStateView:
        """从正式状态创建不含内部字段和隐藏 flag 的模型投影。"""

        user_character = state["user_character"]
        user_public_state = {
            field_name: copy.deepcopy(user_character[field_name])
            for field_name in _USER_PUBLIC_STATE_FIELDS
            if field_name in user_character
        }
        flags = state["flags"]
        visible_flag_ids = module.get("gm_visible_flag_ids", [])
        return GameMasterStateView(
            state_version=state["state_version"],
            current_scene=state["current_scene"],
            user_public_state=user_public_state,
            character_public_states={
                character_id: {
                    field_name: copy.deepcopy(character_state[field_name])
                    for field_name in _CHARACTER_PUBLIC_STATE_FIELDS
                    if field_name in character_state
                }
                for character_id, character_state in state["characters"].items()
            },
            clues_found=tuple(state["clues_found"]),
            accessible_locations=tuple(state["accessible_locations"]),
            threat_clock=copy.deepcopy(state["threat_clock"]),
            gm_visible_flags={
                flag_id: copy.deepcopy(flags[flag_id])
                for flag_id in visible_flag_ids
                if flag_id in flags
            },
        )

    @staticmethod
    def _build_scene_context(
        state: Mapping[str, Any],
        module: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """只公开当前场景及已经发现的线索定义。"""

        current_scene = state["current_scene"]
        scene = next(
            item for item in module["scenes"] if item["scene_id"] == current_scene
        )
        clue_definitions = module.get("clue_definitions", {})
        discovered_clues = [
            {
                "clue_id": clue_id,
                **copy.deepcopy(clue_definitions[clue_id]),
            }
            for clue_id in state["clues_found"]
            if clue_id in clue_definitions
        ]
        return {
            "scene_id": current_scene,
            "public_facts": copy.deepcopy(scene["public_facts"]),
            "interactions": copy.deepcopy(scene["interactions"]),
            "boundaries": copy.deepcopy(scene["boundaries"]),
            "discovery_opportunities": copy.deepcopy(
                scene["discovery_opportunities"]
            ),
            "discovered_clues": discovered_clues,
        }

    @staticmethod
    def _initial_characters(character_configs: list[dict]) -> dict:
        """把角色配置中的开场状态转换为角色状态映射。"""

        characters: dict[str, dict] = {}
        for character in character_configs:
            initial_state = dict(character["initial_state"])
            # 关系阶段只由 relationship_state 持有，避免双份权威状态分叉。
            initial_state.pop("relationship_stage", None)
            characters[character["character_id"]] = initial_state
        return characters
