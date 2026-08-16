"""集中定义静态数据和运行时文件的路径。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# 从包目录向上两级定位仓库根目录，避免依赖启动命令所在的位置。
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class AppPaths:
    """保存应用使用的目录，并提供具体文件的统一入口。"""

    data_dir: Path = PROJECT_ROOT / "data"
    runtime_dir: Path = PROJECT_ROOT / "var"

    @property
    def module_file(self) -> Path:
        """返回当前 MVP 剧本模块的定义文件。"""

        return self.data_dir / "modules" / "escape_thalarion.json"

    @property
    def characters_file(self) -> Path:
        """返回 AI 队友角色的配置文件。"""

        return self.data_dir / "characters" / "characters.json"

    @property
    def game_state_file(self) -> Path:
        """返回可变游戏状态的持久化文件。"""

        return self.runtime_dir / "game_state.json"

    @property
    def memory_file(self) -> Path:
        """返回代理共享及私有记忆的持久化文件。"""

        return self.runtime_dir / "memory.json"

    @property
    def check_records_file(self) -> Path:
        """返回独立保存正式检定记录的账本文件。"""

        return self.runtime_dir / "check_records.json"
