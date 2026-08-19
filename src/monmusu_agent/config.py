"""集中定义项目根路径。"""

from __future__ import annotations

from pathlib import Path

# 从包目录向上两级定位仓库根目录，避免依赖启动命令所在的位置。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
