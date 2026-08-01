# ADR-033：先建立 DeepSeek 协议基线，再评估默认模型配置

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-031](0031-deepseek-through-openai-sdk.md) 与 [ADR-032](0032-one-local-schema-repair-attempt.md)

DeepSeek adapter 保留模型 ID 与 thinking 模式的运行配置，但不据此建设通用多提供商框架、自动路由或逐回合模型切换。首个真实 LLM 协议切片使用 `deepseek-v4-flash` 的 non-thinking 模式，先验证消息转换、工具循环与最终答复校验的协议基线；工具提交后的中断与正式恢复按后续增量实现。这只是实现基线，不是对最终模型质量的预判。

协议切片成立后，使用同一组 GM 评估案例比较 `deepseek-v4-flash`、`deepseek-v4-pro` 及其 thinking/non-thinking 配置。最终默认值依据主持质量、COC 工具调用正确率、跨回合正典连续性、延迟与成本的实测结果选择，同时允许运行配置显式覆盖。
