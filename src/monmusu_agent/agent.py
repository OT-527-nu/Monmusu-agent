"""实现 GameMasterAgent 的有限模型循环。"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from monmusu_agent.tools import ToolDefinition, ToolResult, ToolSession, TurnContext


def _freeze_projection(value: Any) -> Any:
    """递归复制并冻结提供给模型的状态投影。"""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_projection(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_projection(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_projection(item) for item in value)
    return value


@dataclass(frozen=True)
class GameMasterStateView:
    """保存可以安全提供给 GM 模型的公开状态投影。"""

    state_version: int
    current_scene: str
    user_public_state: Mapping[str, Any]
    character_public_states: Mapping[str, Mapping[str, Any]]
    clues_found: tuple[str, ...]
    accessible_locations: tuple[str, ...]
    threat_clock: Mapping[str, Any]
    gm_visible_flags: Mapping[str, Any]

    def __post_init__(self) -> None:
        """阻止模型通过嵌套容器改写本轮固定投影。"""

        for field_name in (
            "user_public_state",
            "character_public_states",
            "clues_found",
            "accessible_locations",
            "threat_clock",
            "gm_visible_flags",
        ):
            object.__setattr__(
                self,
                field_name,
                _freeze_projection(getattr(self, field_name)),
            )


@dataclass(frozen=True)
class GameMasterDraft:
    """保存模型完成工具循环后给出的候选叙事内容。"""

    strategy: str
    narration: str
    suggested_actions: tuple[str, ...]


@dataclass(frozen=True)
class FinalModelStep:
    """表示模型选择结束当前工具循环。"""

    draft: GameMasterDraft


@dataclass(frozen=True)
class ToolCallModelStep:
    """表示模型请求执行当前动态目录中的一个工具。"""

    tool_name: str
    arguments: Mapping[str, Any]


ModelStep = FinalModelStep | ToolCallModelStep


@dataclass(frozen=True)
class ToolInteraction:
    """关联一次模型工具请求与可信模块返回的结果。"""

    call: ToolCallModelStep
    result: ToolResult


@dataclass(frozen=True)
class ModelRequest:
    """提供一次模型决策所需的可信回合信息与动态工具目录。"""

    input_text: str
    state_view: GameMasterStateView
    scene_context: Mapping[str, Any]
    public_memory: tuple[Any, ...]
    available_tools: tuple[ToolDefinition, ...]
    tool_interactions: tuple[ToolInteraction, ...]

    def __post_init__(self) -> None:
        """冻结由引擎提供的场景与公开记忆快照。"""

        object.__setattr__(
            self,
            "scene_context",
            _freeze_projection(self.scene_context),
        )
        object.__setattr__(
            self,
            "public_memory",
            _freeze_projection(self.public_memory),
        )


class GameMasterModel(Protocol):
    """隔离未来真实 LLM 与当前可编程测试替身的 seam。"""

    def next_step(self, request: ModelRequest) -> ModelStep:
        """返回本次模型决策。"""


class GameMasterAgentError(RuntimeError):
    """表示应由外层 GameEngine 处理的受控 Agent 失败。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _readonly_tool_result(result: ToolResult) -> ToolResult:
    """复制工具结果，避免模型改写 ToolSession 持有的可信轨迹。"""

    data = None if result.data is None else dict(result.data)
    if data is not None and data.get("kind") == "check_result":
        # check_id 仍需用于效果申请，回合及存储关联字段只留在可信轨迹。
        for field_name in ("game_id", "turn_id", "module_id"):
            data.pop(field_name, None)
    sequence = result.tool_call_id.rsplit("_", 1)[-1]
    return ToolResult(
        tool_call_id=f"tool_{sequence}",
        tool_name=result.tool_name,
        ok=result.ok,
        data=None if data is None else _freeze_projection(data),
        error=result.error,
    )


class GameMasterAgent:
    """驱动唯一的 GM 模型循环。"""

    def __init__(
        self,
        *,
        model: GameMasterModel,
        max_iterations: int,
    ) -> None:
        self.model = model
        self.max_iterations = max_iterations

    def run(
        self,
        context: TurnContext,
        tool_session: ToolSession,
        *,
        state_view: GameMasterStateView,
        scene_context: Mapping[str, Any],
        public_memory: tuple[Any, ...],
    ) -> GameMasterDraft:
        """运行有限模型循环并返回最终结构化结果。"""

        interactions: list[ToolInteraction] = []
        for _ in range(self.max_iterations):
            request = ModelRequest(
                input_text=context.input_text,
                state_view=state_view,
                scene_context=scene_context,
                public_memory=public_memory,
                available_tools=tool_session.available_tool_definitions(),
                tool_interactions=tuple(interactions),
            )
            try:
                step = self.model.next_step(request)
            except Exception as error:
                raise GameMasterAgentError(
                    "model_failure",
                    "GameMasterAgent 的模型调用失败",
                ) from error
            if isinstance(step, FinalModelStep):
                return step.draft
            if isinstance(step, ToolCallModelStep):
                result = tool_session.execute(step.tool_name, step.arguments)
                interactions.append(
                    ToolInteraction(
                        call=step,
                        result=_readonly_tool_result(result),
                    )
                )
                continue
            raise GameMasterAgentError(
                "invalid_model_step",
                "GameMasterAgent 收到未知的模型步骤",
            )
        raise GameMasterAgentError(
            "iteration_limit_exceeded",
            "GameMasterAgent 达到循环上限",
        )
