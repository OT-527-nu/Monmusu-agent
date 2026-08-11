"""Agentic MVP 会话初始化的独立命令行组合入口。"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from monmusu_agent.agentic_harness import (
    AgenticHarness,
    AgenticTurnBlockedError,
    PublicMechanic,
    SessionLifecycleView,
    TurnResult,
)
from monmusu_agent.agentic_model import (
    DeepSeekGameMasterModel,
    ModelCallError,
    ModelProfileValidationError,
    deepseek_model_profile,
)
from monmusu_agent.agentic_session import (
    AgenticSessionStore,
    CreatedSession,
    InvestigatorChoice,
    NewSessionRequest,
)
from monmusu_agent.config import PROJECT_ROOT

_INVALID_UTF8_INPUT_MESSAGE = (
    "终端输入不是有效的 UTF-8；请确认终端和输入法使用 UTF-8 后重试"
)


class CliInputEncodingError(ValueError):
    """表示终端输入无法作为可靠的 UTF-8 文本使用。"""


@dataclass(frozen=True)
class NewSessionCliResult:
    """把已发布会话和首条玩家输入交给后续组合层。"""

    created: CreatedSession
    first_action: str


def _configure_terminal_input() -> None:
    """让真实 CLI 的标准 I/O 不受启动 shell 默认 locale 的影响。"""

    for stream, errors in (
        (sys.stdin, "surrogateescape"),
        (sys.stdout, "strict"),
        (sys.stderr, "strict"),
    ):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors=errors)
        except (OSError, ValueError):
            # 某些测试或嵌入式调用者提供不可重配置的标准流，仍使用注入的读取 seam。
            continue


def _normalise_cli_input(value: str) -> str:
    """恢复可识别的 surrogateescape 字节，拒绝不可逆的输入损坏。"""

    if not isinstance(value, str):
        raise CliInputEncodingError("终端输入必须是文本")
    if not any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        return value
    try:
        raw = value.encode("utf-8", errors="surrogateescape")
        return raw.decode("utf-8")
    except UnicodeError as error:
        raise CliInputEncodingError(_INVALID_UTF8_INPUT_MESSAGE) from error


def _wrap_read_line(read_line: Callable[[str], str]) -> Callable[[str], str]:
    """在 CLI 输入 seam 统一处理 stdin 解码和 surrogateescape。"""

    def wrapped(prompt: str) -> str:
        try:
            value = read_line(prompt)
        except UnicodeDecodeError as error:
            raise CliInputEncodingError(_INVALID_UTF8_INPUT_MESSAGE) from error
        return _normalise_cli_input(value)

    return wrapped


def compose_deepseek_harness(
    store: AgenticSessionStore,
    *,
    api_key: str,
    model_id: str,
    thinking: bool,
    client: Any | None = None,
) -> AgenticHarness:
    """在组合边界注入 key，并构造同一 Agentic Harness seam。"""

    try:
        profile = deepseek_model_profile(model_id=model_id, thinking=thinking)
    except ModelProfileValidationError as error:
        raise ModelCallError(
            "unsupported_model_profile",
            "DeepSeek model profile is unsupported",
            retryable=False,
        ) from error
    model = DeepSeekGameMasterModel(api_key, client=client)
    return AgenticHarness(store, model, model_profile=profile)


def run_agentic_cli(
    harness: AgenticHarness,
    store: AgenticSessionStore,
    *,
    read_line: Callable[[str], str] = input,
    write_line: Callable[[str], None] = print,
) -> TurnResult | None:
    """优先门控未完成回合，否则进入现有新游戏流程。"""

    incomplete_game_ids = store.find_incomplete_session_ids()
    if not incomplete_game_ids:
        new_session = run_new_session_cli(
            store,
            read_line=read_line,
            write_line=write_line,
        )
        return run_game_cli(
            harness,
            new_session.created.game_id,
            new_session.first_action,
            read_line=read_line,
            write_line=write_line,
        )

    wrapped_read_line = _wrap_read_line(read_line)
    game_id = _select_incomplete_session(
        incomplete_game_ids,
        wrapped_read_line,
        write_line,
    )
    if game_id is None:
        return None
    return _run_session_cli(
        harness,
        game_id,
        first_action=None,
        show_initial_recovery=True,
        read_line=read_line,
        write_line=write_line,
    )


def run_new_session_cli(
    store: AgenticSessionStore,
    *,
    read_line: Callable[[str], str] = input,
    write_line: Callable[[str], None] = print,
) -> NewSessionCliResult:
    """收集建局资料，展示已冻结开场，并返回首条自由文本。"""

    read_line = _wrap_read_line(read_line)
    choices = store.available_investigators()
    write_line("选择调查员：")
    for index, choice in enumerate(choices, start=1):
        write_line(
            f"{index}. {choice.label}（建议姓名：{choice.suggested_display_name}）"
        )
    selected = _read_choice(choices, read_line, write_line)
    display_name = read_line(
        f"显示姓名（默认 {selected.suggested_display_name}）："
    ).strip()
    request = NewSessionRequest(
        investigator_id=selected.actor_id,
        display_name=display_name or selected.suggested_display_name,
        honorific=_optional_answer(read_line("称谓（可留空）：")),
        pronouns=_optional_answer(read_line("代词（可留空）：")),
        occupation=_optional_answer(read_line("职业表述（可留空）：")),
        appearance=_optional_answer(read_line("外观（可留空）：")),
        background_hook=_optional_answer(read_line("背景钩子（可留空）：")),
        keepsake=_optional_answer(read_line("随身小物（可留空）：")),
    )
    created = store.create_session(request)

    # 只有目录事务完成并返回后，玩家才会看到可成为正典的开场文本。
    write_line(created.opening_narration)
    first_action = _read_first_action(read_line, write_line)
    return NewSessionCliResult(created=created, first_action=first_action)


def run_turn_cli(
    harness: AgenticHarness,
    game_id: str,
    player_input: str,
    *,
    write_line: Callable[[str], None] = print,
) -> TurnResult:
    """执行一次回合，并只显示 Harness 返回的可信玩家投影。"""

    player_input = _normalise_cli_input(player_input)
    result = harness.start_turn(
        game_id,
        player_input,
        public_mechanic_sink=lambda mechanic: write_line(
            _format_public_mechanic(mechanic)
        ),
    )
    _write_turn_result(result, write_line)
    return result


def _write_turn_result(
    result: TurnResult,
    write_line: Callable[[str], None],
) -> None:
    """展示一次生命周期调用新提交的公开投影。"""

    if result.status == "interrupted":
        write_line(f"未完成回合：{result.turn_id}")
        write_line(
            f"技术中断（{result.error_code}）：{result.error_message}"
        )
        return

    assert result.narration is not None
    write_line(result.narration)
    for change in result.public_fact_changes:
        if change.kind == "established":
            write_line(f"公开事实已确立：{change.text}")
        else:
            write_line(f"公开事实已结束：{change.text}")


def run_game_cli(
    harness: AgenticHarness,
    game_id: str,
    first_action: str,
    *,
    read_line: Callable[[str], str] = input,
    write_line: Callable[[str], None] = print,
) -> TurnResult:
    """在同一会话中持续接收行动，直到收束或技术中断。"""

    result = _run_session_cli(
        harness,
        game_id,
        first_action=first_action,
        show_initial_recovery=False,
        read_line=read_line,
        write_line=write_line,
    )
    assert result is not None
    return result


def _run_session_cli(
    harness: AgenticHarness,
    game_id: str,
    *,
    first_action: str | None,
    show_initial_recovery: bool,
    read_line: Callable[[str], str],
    write_line: Callable[[str], None],
) -> TurnResult | None:
    """只依据 Harness 投影在新行动与显式恢复之间切换。"""

    read_line = _wrap_read_line(read_line)
    player_input = (
        None if first_action is None else _normalise_cli_input(first_action)
    )
    last_result: TurnResult | None = None
    show_recovery_state = show_initial_recovery
    while True:
        lifecycle = harness.get_session_state(game_id)
        if lifecycle.has_incomplete_turn:
            if show_recovery_state:
                _write_recovery_state(lifecycle, write_line)
            choice = _read_recovery_choice(read_line, write_line)
            if choice == "exit":
                return last_result
            assert lifecycle.turn_id is not None
            try:
                result = harness.resume_turn(
                    game_id,
                    lifecycle.turn_id,
                    public_mechanic_sink=lambda mechanic: write_line(
                        _format_public_mechanic(mechanic)
                    ),
                )
            except AgenticTurnBlockedError:
                # 冻结运行配置不可用时，保持 blocker 并回到同一显式门。
                write_line(
                    "技术中断（recovery_unavailable）："
                    "当前运行配置无法恢复该回合；未完成回合已保留"
                )
                show_recovery_state = False
                continue
            _write_turn_result(result, write_line)
            last_result = result
            show_recovery_state = False
            continue

        if lifecycle.technical_status == "complete":
            return last_result
        if player_input is None:
            player_input = _read_first_action(read_line, write_line)
        result = run_turn_cli(
            harness,
            game_id,
            player_input,
            write_line=write_line,
        )
        last_result = result
        player_input = None
        show_recovery_state = False


def _format_public_mechanic(mechanic: PublicMechanic) -> str:
    details = json.dumps(
        mechanic.details_as_json(),
        ensure_ascii=False,
        sort_keys=True,
    )
    return (
        f"公开机械 | 类型：{mechanic.kind} | 角色：{mechanic.actor_id} | "
        f"详情：{details}"
    )


def _select_incomplete_session(
    game_ids: tuple[str, ...],
    read_line: Callable[[str], str],
    write_line: Callable[[str], None],
) -> str | None:
    write_line("检测到未完成回合：")
    for index, game_id in enumerate(game_ids, start=1):
        write_line(f"{index}. 会话 {game_id}")
    while True:
        try:
            raw = read_line("请选择要处理的未完成会话编号：").strip()
        except (EOFError, KeyboardInterrupt):
            write_line("已退出；未完成回合已保留。")
            return None
        try:
            index = int(raw)
        except ValueError:
            write_line("请输入有效的未完成会话编号。")
            continue
        if 1 <= index <= len(game_ids):
            return game_ids[index - 1]
        write_line("请输入有效的未完成会话编号。")


def _write_recovery_state(
    lifecycle: SessionLifecycleView,
    write_line: Callable[[str], None],
) -> None:
    if not lifecycle.has_incomplete_turn or lifecycle.turn_id is None:
        raise RuntimeError("发现的会话当前没有未完成回合")
    write_line(f"未完成回合：{lifecycle.turn_id}")
    for mechanic in lifecycle.public_mechanics:
        write_line(_format_public_mechanic(mechanic))
    write_line(
        f"技术中断（{lifecycle.error_code}）：{lifecycle.error_message}"
    )


def _read_recovery_choice(
    read_line: Callable[[str], str],
    write_line: Callable[[str], None],
) -> str:
    while True:
        try:
            choice = read_line(
                "输入“恢复”继续原回合，或输入“退出”结束："
            ).strip()
        except (EOFError, KeyboardInterrupt):
            write_line("已退出；未完成回合已保留。")
            return "exit"
        if choice == "恢复":
            return "resume"
        if choice == "退出":
            write_line("已退出；未完成回合已保留。")
            return "exit"
        write_line("只能输入“恢复”或“退出”。")


def main() -> int:
    """从外部运行配置组合 DeepSeek，并运行连续 Agentic GM 回合。"""

    _configure_terminal_input()
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if api_key is None or not api_key.strip():
        print("DEEPSEEK_API_KEY 未设置，无法启动 Agentic GM。")
        return 2
    model_id = os.environ.get(
        "MONMUSU_DEEPSEEK_MODEL_ID",
        "deepseek-v4-flash",
    ).strip()
    thinking_text = os.environ.get(
        "MONMUSU_DEEPSEEK_THINKING",
        "false",
    ).strip().lower()
    if thinking_text not in {"false", "true"}:
        print("MONMUSU_DEEPSEEK_THINKING 必须为 false 或 true。")
        return 2
    store = AgenticSessionStore(
        session_root=PROJECT_ROOT / "var" / "agentic_sessions"
    )
    try:
        harness = compose_deepseek_harness(
            store,
            api_key=api_key,
            model_id=model_id,
            thinking=thinking_text == "true",
        )
    except ModelCallError as error:
        print(f"运行配置错误（{error.code}）：{error.message}")
        return 2
    try:
        run_agentic_cli(harness, store)
    except CliInputEncodingError as error:
        print(f"输入错误：{error}")
        return 2
    return 0


def _read_choice(
    choices: tuple[InvestigatorChoice, ...],
    read_line: Callable[[str], str],
    write_line: Callable[[str], None],
) -> InvestigatorChoice:
    while True:
        raw = read_line("请选择调查员编号：").strip()
        try:
            index = int(raw)
        except ValueError:
            write_line("请输入有效的调查员编号。")
            continue
        if 1 <= index <= len(choices):
            return choices[index - 1]
        write_line("请输入有效的调查员编号。")


def _read_first_action(
    read_line: Callable[[str], str],
    write_line: Callable[[str], None],
) -> str:
    while True:
        value = read_line("你的行动：").strip()
        if value:
            return value
        write_line("行动不能为空。")


def _optional_answer(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None


if __name__ == "__main__":
    raise SystemExit(main())
