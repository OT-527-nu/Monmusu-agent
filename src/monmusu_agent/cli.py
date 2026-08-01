"""Monmusu Agent 的命令行入口。"""

from __future__ import annotations

from monmusu_agent.config import AppPaths
from monmusu_agent.engine import GameEngine


def main() -> int:
    """初始化一局游戏，并向终端输出开场状态。"""

    engine = GameEngine(paths=AppPaths())
    state = engine.initialize()

    print(engine.opening_text())
    print(f"当前场景: {state['current_scene']}")
    print(
        "威胁时钟: "
        f"{state['threat_clock']['value']}/{state['threat_clock']['maximum']}"
    )
    print("状态文件已创建: var/game_state.json, var/memory.json")
    return 0
