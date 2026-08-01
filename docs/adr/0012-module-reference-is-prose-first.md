# ADR-012：模组参考书采用叙事优先的文档

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-007](0007-reference-becomes-canon-when-established.md) 与 [ADR-010](0010-tools-expose-coc-semantics.md)

模组参考书使用 GM 可以直接阅读的自然语言 Markdown，内容可以包括游戏前提、基调、人物、地点、秘密、威胁、可能发展与主持建议。示例检定和后果只能作为建议，不要求场景 ID、固定路线、检定授权、效果表或结局枚举；少量标题、版本等文档元数据可以独立存在，但不得重新形成剧情规则 schema。

只有 COC 数据、会话初始化和运行事实等 Harness 必须可信处理的数据使用最小的结构化 schema，例如调查员技能、HP、SAN 和开场 setup；模组主体仍保持自然语言 Markdown。现行把叙事内容、`check_rules` 和 `effect_definitions` 混合在一起的模组 JSON 不再是新版 MVP 的目标格式；具体迁移与参考书章节结构留待完整 MVP 文档重写时处理。
