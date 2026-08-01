# ADR-037：GM Agent Loop 使用请求与执行尝试双层时限

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-025](0025-mechanics-commit-before-atomic-gm-response.md)、[ADR-031](0031-deepseek-through-openai-sdk.md) 与 [ADR-036](0036-agent-loop-has-eight-round-trip-safety-fuse.md)

MVP 为每次 GM 执行尝试设置两个可配置的技术时限：单次 DeepSeek 请求初始默认最多等待 60 秒（`request_timeout_seconds`）；从 Harness 接受新玩家输入或玩家明确恢复到成功提交 GM 最终答复，执行尝试初始默认最多持续 180 秒（`attempt_timeout_seconds`）。前者终止单个挂起请求，后者防止多个缓慢但成功的模型往返令一次尝试无界延长。模型评测可以显式覆盖这些配置，默认值也应根据真实延迟轨迹调整。

这些时限只约束运行等待，不代表虚构世界经过时间，也不限制 GM 的裁定内容。任一时限触发时，Harness 停止本次执行，不回滚已提交机械，不提交部分叙事或事实变化，并将本轮保留为可恢复的未完成回合；不得用超时作为要求 GM 仓促收束剧情的 Prompt 指令。
