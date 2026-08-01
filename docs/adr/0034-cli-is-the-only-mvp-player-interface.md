# ADR-034：CLI 是 MVP 唯一正式玩家界面

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-016](0016-one-continuous-gm-tool-loop.md)、[ADR-024](0024-mechanical-results-are-public-by-default.md) 与 [ADR-025](0025-mechanics-commit-before-atomic-gm-response.md)；在 MVP 界面范围上取代 [ADR-002](0002-lightweight-player-facing-mvp-loop.md) 中将 Web UI 视为可选加分项的表述

MVP 只提供 CLI 玩家界面，不实现 Web UI 或 TUI。这个选择用于集中验证 GM 主持自由度、COC 工具循环、跨回合正典连续性与完整短篇体验，而不是把 CLI 当作临时调试入口；增加另一种界面并不能降低这些核心不确定性。

CLI 必须支持一局游戏的完整交互，并清楚区分 GM 叙事、公开机械结果、玩家自由文本输入和技术中断或恢复提示。普通玩家界面不展示隐藏机械结果、模型隐藏推理或内部调试轨迹。CLI 只负责输入输出与运行组装，不取得虚构裁定、COC 机械或正典写入权。本决策不预先决定输出是否采用 streaming。
