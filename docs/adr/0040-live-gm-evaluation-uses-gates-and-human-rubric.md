# ADR-040：真实 GM 评估使用硬门槛与人工质量量表

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-005](0005-gm-authority-over-fictional-causality.md)、[ADR-015](0015-player-owns-investigator-intent.md)、[ADR-024](0024-mechanical-results-are-public-by-default.md) 与 [ADR-039](0039-validation-has-deterministic-and-live-deepseek-lanes.md)

真实 DeepSeek GM 评估先检查不可折中的硬门槛：工具调用与最终答复结构合法；不篡改 Harness 机械结果；不泄露隐藏内容；不替玩家作出调查员的主动选择；承认已经成立的正典；能够处理模组未预写但在虚构中合理的行动。任一硬门槛失败，该次运行直接判定失败，流畅文笔不能抵消权威或协议错误。

通过硬门槛后，由人类评估者分别按虚构因果、即兴能力、跨回合连续性、NPC 表现、节奏与氛围给出 1 至 5 分。一次运行的质量资格线是平均分至少 4，且没有单项低于 3；它与协议硬门槛一样是默认 GM 的必要条件。评估不要求固定措辞，也不使用第二个 LLM judge。模型配置是否可成为默认 GM，必须结合多次相同案例运行，而不能由单次高分决定。
