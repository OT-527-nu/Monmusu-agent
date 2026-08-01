# 11 — 证明真实恢复契约

**What to build:** 使用项目所有者从外部提供的真实 key，分别运行 non-thinking 与 thinking 的 DeepSeek 工具中断/恢复契约，形成统一格式的脱敏证据。证明同一 `turn_id`、匹配 `tool_call_id`、相同已提交机械和合法 provider 续接在真实服务上成立，且无重掷、重复资源变化或 reasoning 泄露。

Blocked by: 09 — 用未完成回合门控 CLI; 10 — 支持 thinking 恢复传输

Status: ready-for-human

## References

- [Parent spec](../spec.md): User Stories 25-27, 39, 49-50 and 52; recovery, player-visibility and MVP evidence acceptance gates; deterministic/live evidence separation.
- [Migration](../../../docs/agentic_mvp/migration.md): “增量 2：未完成回合与运行恢复”的 non-thinking/thinking 真实契约验收门。
- [Data contracts](../../../docs/agentic_mvp/contracts.md): `ToolInteraction`, `IncompleteTurn`, `GameMasterModel` seam、运行配置和玩家安全投影。
- [Agent Loop](../../../docs/agentic_mvp/agent_loop.md): “工具分支”, “故障处理”, “显式恢复时序”和恢复验收场景。
- [Evaluation](../../../docs/agentic_mvp/evaluation.md): “真实 DeepSeek 契约测试”和唯一的“评估记录格式”。
- [ADR-025](../../../docs/adr/0025-mechanics-commit-before-atomic-gm-response.md), [ADR-038](../../../docs/adr/0038-player-explicitly-resumes-incomplete-turn.md), and [ADR-039](../../../docs/adr/0039-validation-has-deterministic-and-live-deepseek-lanes.md).

- [ ] 只有在项目所有者明确提供外部 key 并启用 live runner 时运行；无 key 时明确 skip 且不视为通过。key、鉴权头、完整客户端对象和凭据片段不持久化、不记录、不打印，也不进入证据。
- [ ] 固定并记录 model ID、thinking 开关、stream=false、JSON Object、工具 schema、Prompt/profile 修订、timeout/attempt limits、fixture 版本与依赖版本；行为相关配置或 fixture 变化后必须重跑全部本票证据。
- [ ] non-thinking 合同制造一次 `make_check` 已原子提交而后续 provider 步骤中断的未完成回合；退出并重建进程后由玩家显式恢复，真实 GM 收到匹配 `tool_call_id` 的已保存 tool result 并提交合法 final。
- [ ] thinking 合同覆盖同样的工具后中断/进程重建/显式恢复，并验证 assistant `reasoning_content` 按 provider 协议逐字回传；公开证据只能记录存在性、完整性判断或哈希等脱敏结论，不能保存正文。
- [ ] 两个合同都引用具体 `game_id`/`turn_id`、tool call、`mechanic_id`、骰点、角色变化和最终 turn，证明恢复前后机械相同且只出现一次，没有新回合、隐藏重掷、重复扣减、重复事实或重复 final。
- [ ] 每个请求按 [Evaluation](../../../docs/agentic_mvp/evaluation.md) 既有格式记录有效 provider 参数、消息角色/协议 ID 的脱敏投影、`finish_reason`、usage、latency、本地错误类别和修正次数；缺失数据明确为缺失，不估算 token、成本或 hidden reasoning。
- [ ] 玩家输出、`CommittedTurn`、事实账本、mechanic、普通日志和公开证据均以 canary/扫描证明不含 reasoning 正文、隐藏事实、隐藏机械、provider envelope 或私有诊断；失败运行和修正过程与成功运行一起保留。
- [ ] 将 live 契约结果与确定性 Harness 恢复矩阵分开报告：前者只证明真实 SDK/provider 协议可用，不能替代幂等、超时、原子故障测试，也不评分 GM 虚构因果、节奏、氛围或模型质量。
- [ ] 本票通过后只宣称 Increment 2 的正式恢复和真实传输契约成立；不宣称其余 COC 工具、完整短篇、六场景模型矩阵、默认配置选择或旧路径退役完成。

**Not in this ticket:** 人工 GM 质量评分、开放行动因果复评、其余四个 COC 工具、72 次模型矩阵、完整试玩、默认入口切换或旧代码删除。
