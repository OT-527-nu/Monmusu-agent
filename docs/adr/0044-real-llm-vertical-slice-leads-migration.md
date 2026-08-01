# ADR-044：真实 LLM 纵向切片优先于完整机械扩展

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-011](0011-first-slice-proves-open-action-continuity.md)、[ADR-023](0023-mvp-coc-mechanics-scope.md)、[ADR-031](0031-deepseek-through-openai-sdk.md)、[ADR-039](0039-validation-has-deterministic-and-live-deepseek-lanes.md) 与 [ADR-043](0043-agentic-mvp-docs-become-the-new-authority.md)

迁移首先完成新版权威文档、契约与评估夹具；随后立即接通真实 DeepSeek GM、主持能力章程、Markdown 模组参考书、一个 `make_check`、自然语言事实账本和连续 CLI，证明一次模组未预写的行动能够产生并跨两个回合维持正式世界事实。项目不先重建全部 COC 机械、通用状态框架或内容系统，再到末期验证真实 GM 是否可用。

纵向切片成立后，依次补齐未完成回合与运行恢复，增加孤注一掷、幸运、伤害/HP、理智/SAN 和全部预生成角色卡，再沿用首切片的全文加载机制补齐模组与 NPC 内容并完成开放短篇。最后执行真实模型评估矩阵和完整试玩；只有新版路径通过相应门槛后，才删除旧 `request_check`、`apply_effect`、模组效果白名单、关系阶段与六格威胁时钟实现。旧路径在迁移期间是可比较基线，不是新版设计约束。
