# ADR-011：首个切片证明开放行动可以持续改变世界

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-005](0005-gm-authority-over-fictional-causality.md)、[ADR-009](0009-mechanics-in-tools-facts-in-final-response.md) 与 [ADR-010](0010-tools-expose-coc-semantics.md)

新方向的首个可运行切片使用真实 LLM 驱动一个 GM Agent。GM 接收玩家原文、当前事实索引、本局完整回合记录和全文短篇模组参考书；面对参考书没有预写的行动时，它可以直接裁定或调用一个通用 `make_check` 工具，然后输出玩家可见叙事以及公开和隐藏世界事实变化。Harness 保存完整回合，下一回合必须让 GM 继续承认这些新事实。

切片验收不依赖 `check_rules`、`effect_definitions` 或模组效果白名单。队友生成、关系、理智、伤害、战斗、结局与复杂检索不进入这个最先实现的证明性切片，但可以作为后续 MVP 增量；这一范围不是对最终 MVP 能力的上限。
