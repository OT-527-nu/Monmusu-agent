# ADR-039：验证分为确定性测试与真实 DeepSeek 测试

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-031](0031-deepseek-through-openai-sdk.md)、[ADR-033](0033-evaluate-deepseek-model-profile.md)、[ADR-036](0036-agent-loop-has-eight-round-trip-safety-fuse.md) 与 [ADR-038](0038-player-explicitly-resumes-incomplete-turn.md)

MVP 保留两条互补的验证路径。确定性单元与 seam 测试使用可编程的假 adapter，稳定覆盖工具调用、步骤超限、超时、无效结构、机械不回滚和未完成回合恢复等 Harness 不变量；真实契约与 GM 评估测试则使用 OpenAI Python SDK、真实 DeepSeek 服务和真实 API key，验证实际模型、消息协议、工具往返、结构化答复、主持质量与连续性。真实测试是 MVP 验收必跑项，但不替代每次本地运行都应可靠执行的确定性测试。

核心 `DeepSeekGameMasterModel` 只接收由组合入口传入的 API key，不负责决定凭据如何获取或保存；具体注入方式由运行环境与项目所有者管理。真实测试不得把 key 写入仓库、测试夹具、快照、日志或失败输出。对真实模型不断言固定措辞，而依据独立的协议条件和 GM 评估标准判断结果。
