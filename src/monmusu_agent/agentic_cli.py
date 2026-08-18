"""Agentic MVP 会话初始化的独立命令行组合入口。"""

from __future__ import annotations

import getpass
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv, set_key

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

_DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
_OPENCODE_GO_DEFAULT_BASE_URL = "https://opencode.ai/zen/go/v1"
_SUPPORTED_PROVIDERS = ("deepseek", "opencode-go", "custom")
_PROVIDER_DISPLAY_NAMES = {
    "deepseek": "DeepSeek 官方",
    "opencode-go": "OpenCode Go",
    "custom": "其它 DeepSeek 模型提供商",
}
_PROVIDER_KEY_ENVS = {
    "deepseek": "DEEPSEEK_API_KEY",
    "opencode-go": "OPENCODE_GO_API_KEY",
    "custom": "MONMUSU_CUSTOM_API_KEY",
}
_PROVIDER_BASE_URL_ENVS = {
    "deepseek": "DEEPSEEK_BASE_URL",
    "opencode-go": "OPENCODE_GO_BASE_URL",
    "custom": "MONMUSU_CUSTOM_BASE_URL",
}
_PROVIDER_DEFAULT_BASE_URLS = {
    "deepseek": _DEEPSEEK_DEFAULT_BASE_URL,
    "opencode-go": _OPENCODE_GO_DEFAULT_BASE_URL,
}

_INVALID_UTF8_INPUT_MESSAGE = (
    "终端输入不是有效的 UTF-8；请确认终端和输入法使用 UTF-8 后重试"
)
_LINUX_IUTF8 = 0x4000


class CliInputEncodingError(ValueError):
    """表示终端输入无法作为可靠的 UTF-8 文本使用。"""


class ProviderConfigError(ValueError):
    """表示用户提供的 provider 运行配置无效。"""


class CliPlayerInterrupt(Exception):
    """表示玩家在会话流程中以 Ctrl+C 请求的优雅退出。

    捕获 seam 已按存档真实状态打印提示；main 将其收敛为退出码 130。
    """


_EXIT_BEFORE_GAME_MESSAGE = "已退出。"
_EXIT_SAVED_SESSION_MESSAGE = "已退出；本局已提交回合均已保存。"
_EXIT_INTERRUPTED_TURN_MESSAGE = (
    "已退出；未完成回合已保留，下次启动选择恢复即可继续。"
)


@dataclass(frozen=True)
class ProviderConfig:
    """保存一次 CLI 启动所需的非秘密 provider 运行配置。"""

    provider: str
    api_key: str
    base_url: str

    @property
    def display_name(self) -> str:
        return _PROVIDER_DISPLAY_NAMES[self.provider]


@dataclass(frozen=True)
class NewSessionCliResult:
    """把已发布会话和首条玩家输入交给后续组合层。"""

    created: CreatedSession
    first_action: str


def _configure_terminal_input() -> Callable[[], None]:
    """让真实 CLI 的标准 I/O 不受启动 shell 默认 locale 的影响。"""

    restore_utf8_backspace = _enable_utf8_backspace()
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
    return restore_utf8_backspace


def _enable_utf8_backspace() -> Callable[[], None]:
    """让 Linux 终端在 Python 读取前按 UTF-8 字符处理退格。"""

    if not sys.platform.startswith("linux"):
        return lambda: None
    try:
        import termios

        file_descriptor = sys.stdin.fileno()
        if not os.isatty(file_descriptor):
            return lambda: None
        original_attributes = termios.tcgetattr(file_descriptor)
        if original_attributes[0] & _LINUX_IUTF8:
            return lambda: None
        configured_attributes = original_attributes[:]
        configured_attributes[0] |= _LINUX_IUTF8
        termios.tcsetattr(file_descriptor, termios.TCSANOW, configured_attributes)
    except (AttributeError, OSError, ValueError):
        return lambda: None

    # stdin.reconfigure 不能影响内核的 canonical 行编辑；退出后必须还原调用者终端。
    def restore() -> None:
        try:
            termios.tcsetattr(
                file_descriptor,
                termios.TCSANOW,
                original_attributes,
            )
        except OSError:
            pass

    return restore


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


def validated_base_url(value: str) -> str:
    """只检查 base URL 的非空和协议前缀，不改写路径。"""

    stripped = value.strip()
    if not stripped or not stripped.startswith(("http://", "https://")):
        raise ProviderConfigError("Base URL 必须以 http:// 或 https:// 开头。")
    return stripped


def provider_config_from_env(
    env: Mapping[str, str],
) -> ProviderConfig | None:
    """从合并后的环境中解析当前 provider；缺失标记或 key 为空表示未配置。"""

    raw_provider = env.get("MONMUSU_PROVIDER")
    if raw_provider is None or not raw_provider.strip():
        return None
    provider = raw_provider.strip()
    if provider not in _SUPPORTED_PROVIDERS:
        raise ProviderConfigError(
            "MONMUSU_PROVIDER 无效，可选 deepseek、opencode-go 或 custom。"
        )
    api_key = env.get(_PROVIDER_KEY_ENVS[provider], "").strip()
    if not api_key:
        return None
    raw_base_url = env.get(_PROVIDER_BASE_URL_ENVS[provider], "").strip()
    if not raw_base_url and provider in _PROVIDER_DEFAULT_BASE_URLS:
        raw_base_url = _PROVIDER_DEFAULT_BASE_URLS[provider]
    if not raw_base_url:
        raise ProviderConfigError(
            f"缺少 {_PROVIDER_BASE_URL_ENVS[provider]}。"
        )
    return ProviderConfig(
        provider=provider,
        api_key=api_key,
        base_url=validated_base_url(raw_base_url),
    )


def _read_secret(
    read_line: Callable[[str], str],
    *,
    prompt: str,
    existing_value: str | None = None,
    allow_blank_existing: bool = True,
) -> str:
    """读取 API key；可保留已有值时绝不回显明文。"""

    if allow_blank_existing and existing_value:
        value = read_line(f"{prompt}（已保存 ****，直接回车保留）：").strip()
        return value or existing_value
    while True:
        value = read_line(f"{prompt}：").strip()
        if value:
            return value


def _interactive_read_line(read_line: Callable[[str], str]) -> Callable[[str], str]:
    """真实终端优先使用 getpass 隐藏 key；注入 seam 不经过 getpass。"""

    if read_line is input and sys.stdin.isatty():
        return lambda prompt: getpass.getpass(prompt)
    return read_line


def _read_provider_choice(
    read_line: Callable[[str], str],
    write_line: Callable[[str], None],
    *,
    current_provider: str | None,
) -> str:
    write_line("请选择模型提供商：")
    for index, provider in enumerate(_SUPPORTED_PROVIDERS, start=1):
        write_line(f"{index}. {_PROVIDER_DISPLAY_NAMES[provider]}")
    while True:
        raw = read_line("请选择模型提供商编号：").strip()
        if not raw and current_provider is not None:
            return current_provider
        try:
            selected_index = int(raw)
        except ValueError:
            write_line("请输入有效的模型提供商编号。")
            continue
        if 1 <= selected_index <= len(_SUPPORTED_PROVIDERS):
            return _SUPPORTED_PROVIDERS[selected_index - 1]
        write_line("请输入有效的模型提供商编号。")


def _read_base_url(
    read_line: Callable[[str], str],
    write_line: Callable[[str], None],
    *,
    existing_value: str | None = None,
    allow_blank_existing: bool = True,
) -> str:
    while True:
        if allow_blank_existing and existing_value:
            raw = read_line("请输入 Base URL（已保存，直接回车保留）：").strip()
            if not raw:
                return validated_base_url(existing_value)
        else:
            raw = read_line(
                "请输入 Base URL（例如 https://your-gateway.example.com/v1）："
            ).strip()
        try:
            return validated_base_url(raw)
        except ProviderConfigError as error:
            write_line(str(error))


def configure_provider(
    env: Mapping[str, str],
    *,
    read_line: Callable[[str], str],
    write_line: Callable[[str], None],
    current_provider: str | None = None,
) -> ProviderConfig:
    """交互收集 provider、key 与 base_url，不写盘、不构造 Harness。"""

    secret_read_line = _interactive_read_line(read_line)
    current = current_provider or env.get("MONMUSU_PROVIDER", "").strip() or None
    if current not in _SUPPORTED_PROVIDERS:
        current = None
    if current is not None:
        write_line(f"当前模型提供商：{_PROVIDER_DISPLAY_NAMES[current]}")
        if env.get(_PROVIDER_KEY_ENVS[current], "").strip():
            write_line("API Key：已保存（****）")
    selected = _read_provider_choice(
        read_line,
        write_line,
        current_provider=current,
    )

    if selected == "custom":
        write_line("当前版本仍按 DeepSeek 系列模型调用。")
        existing_base_url = env.get(_PROVIDER_BASE_URL_ENVS[selected], "").strip()
        keep_existing = current is None or selected == current
        base_url = _read_base_url(
            read_line,
            write_line,
            existing_value=existing_base_url or None,
            allow_blank_existing=keep_existing,
        )
    else:
        existing_base_url = env.get(_PROVIDER_BASE_URL_ENVS[selected], "").strip()
        if existing_base_url:
            base_url = validated_base_url(existing_base_url)
        else:
            base_url = _PROVIDER_DEFAULT_BASE_URLS[selected]

    existing_key = env.get(_PROVIDER_KEY_ENVS[selected], "").strip()
    keep_existing = current is None or selected == current
    api_key = _read_secret(
        secret_read_line,
        prompt="请输入 API Key",
        existing_value=existing_key or None,
        allow_blank_existing=keep_existing,
    )
    return ProviderConfig(
        provider=selected,
        api_key=api_key,
        base_url=base_url,
    )


def save_provider_config(env_path: Path, config: ProviderConfig) -> None:
    """只更新 CLI 拥有的 provider 键，保留 .env 中其他行。"""

    set_key(str(env_path), "MONMUSU_PROVIDER", config.provider)
    set_key(
        str(env_path),
        _PROVIDER_KEY_ENVS[config.provider],
        config.api_key,
    )
    set_key(
        str(env_path),
        _PROVIDER_BASE_URL_ENVS[config.provider],
        config.base_url,
    )


def _stdin_is_interactive() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def compose_deepseek_harness(
    store: AgenticSessionStore,
    *,
    api_key: str,
    model_id: str,
    thinking: bool,
    provider: str = "deepseek",
    base_url: str | None = None,
    retry_policy: Mapping[str, Any] | None = None,
    client: Any | None = None,
) -> AgenticHarness:
    """在组合边界注入 provider 配置，并构造同一 Agentic Harness seam。"""

    try:
        profile = deepseek_model_profile(
            model_id=model_id,
            thinking=thinking,
            provider=provider,
            base_url=base_url,
            retry_policy=retry_policy,
        )
    except ModelProfileValidationError as error:
        raise ModelCallError(
            "unsupported_model_profile",
            "DeepSeek model profile is unsupported",
            retryable=False,
        ) from error
    model = DeepSeekGameMasterModel(
        api_key,
        base_url=profile["base_url"],
        client=client,
    )
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
    try:
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
    except KeyboardInterrupt:
        write_line(_EXIT_BEFORE_GAME_MESSAGE)
        raise CliPlayerInterrupt from None
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
            except KeyboardInterrupt:
                write_line(_interrupt_exit_message(harness, game_id))
                raise CliPlayerInterrupt from None
            _write_turn_result(result, write_line)
            last_result = result
            show_recovery_state = False
            continue

        if lifecycle.technical_status == "complete":
            return last_result
        if player_input is None:
            try:
                player_input = _read_first_action(read_line, write_line)
            except KeyboardInterrupt:
                write_line(_EXIT_SAVED_SESSION_MESSAGE)
                raise CliPlayerInterrupt from None
        try:
            result = run_turn_cli(
                harness,
                game_id,
                player_input,
                write_line=write_line,
            )
        except KeyboardInterrupt:
            write_line(_interrupt_exit_message(harness, game_id))
            raise CliPlayerInterrupt from None
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


def _interrupt_exit_message(
    harness: AgenticHarness,
    game_id: str,
) -> str:
    """按存档真实状态给出中断退出提示，不承诺任何未保存的进度。"""

    try:
        lifecycle = harness.get_session_state(game_id)
    except Exception:
        return _EXIT_BEFORE_GAME_MESSAGE
    if lifecycle.has_incomplete_turn:
        return _EXIT_INTERRUPTED_TURN_MESSAGE
    return _EXIT_SAVED_SESSION_MESSAGE


def main(argv: Sequence[str] | None = None) -> int:
    """配置终端后，从外部运行配置组合 DeepSeek 并运行连续 GM 回合。"""

    restore_terminal = _configure_terminal_input()
    try:
        return _run_main(argv)
    except KeyboardInterrupt:
        # 兜底：_run_main 未覆盖的启动窗口（终端配置、env 装载等）。
        print(_EXIT_BEFORE_GAME_MESSAGE)
        return 130
    finally:
        restore_terminal()


def _run_main(argv: Sequence[str] | None = None) -> int:
    """在终端已配置的前提下组合并运行 Agentic CLI。"""

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    configure_requested = arguments == ("--configure",)
    if arguments and not configure_requested:
        print("未知参数。仅支持 monmusu-agent-agentic --configure。")
        return 2

    env_path = PROJECT_ROOT / ".env"
    config: ProviderConfig | None
    if configure_requested:
        try:
            config = configure_provider(
                os.environ,
                read_line=input,
                write_line=print,
            )
        except ProviderConfigError as error:
            print(f"配置错误：{error}")
            return 2
        except (EOFError, KeyboardInterrupt):
            print("配置未完成，未写入任何配置。")
            return 2
        try:
            save_provider_config(env_path, config)
        except OSError as error:
            print(f"配置保存失败：{error}；未写入任何模型调用配置。")
            return 2
        print(f"模型提供商：{config.display_name}")
        return 0

    try:
        config = provider_config_from_env(os.environ)
    except ProviderConfigError as error:
        print(f"配置错误：{error}")
        return 2
    if config is None:
        if not _stdin_is_interactive():
            print(
                "未检测到模型提供商配置；请运行 "
                "monmusu-agent-agentic --configure，或导出 "
                "MONMUSU_PROVIDER 与对应 API Key。",
            )
            return 2
        try:
            config = configure_provider(
                os.environ,
                read_line=input,
                write_line=print,
            )
        except ProviderConfigError as error:
            print(f"配置错误：{error}")
            return 2
        except (EOFError, KeyboardInterrupt):
            print("配置未完成，未写入任何配置。")
            return 2
        try:
            save_provider_config(env_path, config)
        except OSError as error:
            print(f"配置保存失败：{error}；未写入任何模型调用配置。")
            return 2

    print(f"模型提供商：{config.display_name}")
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
            api_key=config.api_key,
            model_id=model_id,
            thinking=thinking_text == "true",
            provider=config.provider,
            base_url=config.base_url,
        )
    except ModelCallError as error:
        print(f"运行配置错误（{error.code}）：{error.message}")
        return 2
    try:
        run_agentic_cli(harness, store)
    except CliInputEncodingError as error:
        print(f"输入错误：{error}")
        return 2
    except CliPlayerInterrupt:
        return 130
    except KeyboardInterrupt:
        # 兜底：落在未被 seam 捕获的窗口（会话读取、目录发布等）。
        print(_EXIT_BEFORE_GAME_MESSAGE)
        return 130
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
    raise SystemExit(main(sys.argv[1:]))
