# ADR-035：首版 CLI 不流式展示模型输出

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-025](0025-mechanics-commit-before-atomic-gm-response.md)、[ADR-026](0026-minimal-gm-final-response.md)、[ADR-032](0032-one-local-schema-repair-attempt.md) 与 [ADR-034](0034-cli-is-the-only-mvp-player-interface.md)

首版 DeepSeek adapter 使用非流式 Chat Completion，CLI 不逐 token 展示模型原始输出。公开机械结果仍在 Harness 结算并提交后立即显示；GM 最终答复则必须先完整接收、通过本地 schema 校验，并与本轮事实变化一起成功提交，随后才整段显示 `narration`。

这一边界避免把隐藏事实、模型隐藏推理、残缺 JSON 或最终被拒绝的叙事提前暴露给玩家。以后可以增加 streaming，但必须通过单独设计的安全输出协议，明确区分已提交事件与尚未验证的模型文本，不能让 provider 数据流直接绕过 Harness 连接 CLI。
