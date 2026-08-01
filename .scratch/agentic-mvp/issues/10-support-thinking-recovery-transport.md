# 10 — 支持 thinking 恢复传输

**What to build:** 允许组合入口显式选择 DeepSeek thinking profile，并把 provider 恢复所需的 `reasoning_content` 作为受限 `IncompleteTurn` 协议材料原样保存和回传。Harness、持久化投影和 adapter 必须共同证明这些内容只用于未完成对话恢复，不进入玩家记录、正典、公开证据或普通日志。

Blocked by: 08 — 幂等重放已提交工具结果

Status: ready-for-agent

## References

- [Parent spec](../spec.md): User Stories 39, 42 and 50; Implementation Decisions “Provider adapter”, “Provider protocol preservation and explicit recovery” and “Security and privacy”; recovery and player-visibility acceptance gates.
- [Migration](../../../docs/agentic_mvp/migration.md): “增量 2：未完成回合与运行恢复”的 thinking 回放及真实 non-thinking/thinking 验收门。
- [Data contracts](../../../docs/agentic_mvp/contracts.md): `IncompleteTurn`, GM 上下文, `GameMasterModel` seam, 运行配置以及原子性与不变量。
- [Agent Loop](../../../docs/agentic_mvp/agent_loop.md): “模型响应协议”, “非流式输出边界”和“显式恢复时序”。
- [ADR-031](../../../docs/adr/0031-deepseek-through-openai-sdk.md), [ADR-035](../../../docs/adr/0035-first-cli-does-not-stream-model-output.md), and [ADR-038](../../../docs/adr/0038-player-explicitly-resumes-incomplete-turn.md).

- [ ] 组合入口允许显式 `thinking=true` profile，仍固定 non-streaming、单一 DeepSeek/OpenAI SDK adapter 和外部 key 注入；不增加自动模型选择、fallback、provider registry 或运行时路由。
- [ ] `GameMasterModel` envelope 保留 DeepSeek assistant 消息中恢复协议要求的 `reasoning_content`；adapter 不解释、总结、校验或记录其语义，Harness 仍负责响应分类、工具和持久化。
- [ ] thinking tool-call 响应的 `reasoning_content` 与其 assistant/tool 配对在同一次原子写入中保存为受限 `IncompleteTurn` 恢复材料；恢复时按 provider 要求原样、原顺序回传，不重写、截断、合并或注入 committed game context。
- [ ] non-thinking 消息不要求或合成 reasoning 字段；thinking profile 缺少 provider 必需恢复字段时产生稳定技术中断并保留最后合法前缀，不静默降级为 non-thinking 或重新开始对话。
- [ ] 最终成功提交后，`CommittedTurn`、事实账本、mechanic、玩家投影和后续普通 GM 正典上下文都不包含 reasoning content；未完成状态之外不复制该字段，清除恢复阻塞时也清除其受限副本。
- [ ] CLI、技术错误、普通日志、公共评估记录、序列化异常和 adapter/Harness `repr` 不泄露 reasoning 正文、隐藏事实或 provider 私有诊断；测试使用唯一 canary 文本在所有禁止投影中作否定断言。
- [ ] mocked SDK 测试验证 thinking 请求参数以及带 tool call 的 assistant `reasoning_content` 在下一请求中精确回传；公开 Harness 生命周期测试用真实临时 session 和可编程假 model 覆盖工具后中断、进程重建、恢复 final、恢复再次中断与最终清理。
- [ ] non-thinking 现有 contract 与恢复测试保持通过，证明 thinking 支持没有改变 Increment 1 默认显式配置或玩家可见协议。

**Not in this ticket:** 真实 key 调用、模型质量比较、thinking 内容审查或持久化为游戏记录、streaming、自动配置选择及通用 provider 支持。
