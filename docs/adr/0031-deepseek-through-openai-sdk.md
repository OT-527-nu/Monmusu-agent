# ADR-031：MVP 通过 OpenAI SDK 接入 DeepSeek

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-016](0016-one-continuous-gm-tool-loop.md)
- 协议依据：[DeepSeek API 官方文档](https://api-docs.deepseek.com/)

核心保留薄 `GameMasterModel` 接口，MVP 只实现一个 DeepSeek adapter，不建设通用多提供商路由或能力框架。Adapter 使用 OpenAI Python SDK，将 `base_url` 指向 DeepSeek 官方 API，并通过官方文档化的 Chat Completions 接口运行同一个 GM 的多轮 function tool calls；不使用 OpenAI Responses API。

模型 ID 由运行配置提供，API key 由组合入口或运行环境显式注入 adapter；核心不规定 key 的获取、保存或轮换方式。调研日官方当前候选是 `deepseek-v4-flash` 与 `deepseek-v4-pro`，具体默认模型由真实 GM 质量、延迟和成本测试决定。COC 工具执行、机械记录、事实账本、未完成回合和恢复逻辑继续属于本地 Harness；provider adapter 只负责消息与 DeepSeek 协议之间的转换。
