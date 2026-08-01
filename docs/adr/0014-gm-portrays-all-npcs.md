# ADR-014：GM 直接扮演所有 NPC 与队友

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-005](0005-gm-authority-over-fictional-causality.md)，取代 [ADR-001](0001-single-gm-agent-and-character-tools.md) 中通过角色生成工具扮演队友的决定

MVP 由唯一的 GM Agent 直接扮演所有 NPC 和 AI 队友。GM 读取角色资料、秘密与本局正式事实，可以在一次回合答复中自然组织多人对话、沉默、冲突和行动；角色资料提供人格、知识、欲望和关系参考，但角色不是独立 Agent，也不通过 `generate_character_turn` 一类工具生成。

因此 MVP 不设置角色调用配额、参与角色模式或独立与接话生成流程。只有后续试玩证明某个角色确实需要独立观察、工具使用或跨回合自主规划时，才评估可选角色子 Agent；这一未来能力不得反向限制 GM 当前扮演角色的自由。
