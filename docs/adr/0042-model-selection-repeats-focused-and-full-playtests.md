# ADR-042：模型选择重复聚焦场景并复试前两名

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-033](0033-evaluate-deepseek-model-profile.md)、[ADR-040](0040-live-gm-evaluation-uses-gates-and-human-rubric.md) 与 [ADR-041](0041-live-gm-evaluation-uses-six-focused-scenarios.md)

模型选择首先比较 `deepseek-v4-flash`、`deepseek-v4-pro` 各自的 thinking 与 non-thinking 配置。每种配置对六个聚焦场景分别运行三次，共形成 72 次短场景运行；任一次违反硬门槛或质量资格线，该配置就暂时失去默认资格。Prompt、采样设置或模型配置发生调整后，应把它视为新的候选并重新运行完整六场景矩阵，不能只保留成功样本或局部补测来掩盖失败率。

全部通过硬门槛与质量资格线的候选按人工质量评分、延迟和用量成本综合比较。排名前两位各完成两次开放式《逃离塔纳里昂》人工试玩，再依据完整体验选择最终默认 GM 配置。三次短场景重复不是统计学充分证明，而是 MVP 用于发现偶发越权和不稳定主持表现的最低实证门槛。
