# 18 — 验收 Increment 3 工具与真实场景

**What to build:** 把 `make_check`、`push_check`、`spend_luck`、`deal_damage` 和 `make_sanity_check` 作为同一 GM Loop 的完整工具面进行集成验收，并使用真实 DeepSeek 完成聚焦场景二和三。确定性证据证明机械、原子性、恢复和可见性，真实场景证据证明 GM 尊重检定时机与玩家拥有的 push/Luck 选择；两条证据分开记录、共同构成 Increment 3 交付门。

**Blocked by:** 14 — 支持玩家选择的 push_check; 15 — 支持玩家选择的 spend_luck; 16 — 支持公开与隐藏 deal_damage; 17 — 支持公开与隐藏 make_sanity_check

**Status:** ready-for-agent

- [ ] 完整 profile 在同一请求配置中暴露五个规范工具并保持 JSON Object、non-streaming、单 GM 与单次响应至多一个工具调用的既有协议；原玩家输入和全部已提交结果始终回到同一 GM 上下文。
- [ ] 确定性组合矩阵使用六张冻结 ActorSheet，覆盖五工具的成功路径、边界、非法前置、未知角色/能力、公开/隐藏投影、GM 结果续接及跨工具引用，不以真实模型结果替代规则断言。
- [ ] 每种新增工具都制造工具成功后 provider 中断、进程重建和显式恢复，证明相同 `turn_id`、`tool_call_id`、mechanic ID、骰点及 HP/SAN/Luck 变化只出现一次；原子写入故障不留下部分机械或数值变化。
- [ ] 跨工具测试证明 `check_id` 实际引用 `kind: "check"` 的 `mechanic_id`，push/Luck 在整条派生检定链上双向互斥，GM 最终叙事和事实变化不能覆盖任何已提交机械权威。
- [ ] 聚焦场景二使用真实 DeepSeek 证明明显成立的行动被直接裁定，有真实不确定性与失败代价的行动才调用事前公开的 `make_check`，不增加检定前暂停，也不先叙述结果再补骰。
- [ ] 聚焦场景三使用真实 DeepSeek 证明 GM 可以说明 push/Luck 机会但不会自动调用；玩家未选择时不消费资源或重掷，玩家明确提出不同做法并接受风险后只调用关联原失败检定的 `push_check`，不偷偷同时花 Luck。
- [ ] live 记录固定模型、thinking、stream、tool schema、Prompt/profile、fixture、timeout 与依赖版本，按既有格式脱敏保存请求投影、usage、latency、结果和 hard gates；没有 key 或 skip 不能算通过，凭据与 reasoning 正文不得进入证据。
- [ ] 确定性测试、真实 provider 合同和人工 GM 行为判断明确分栏报告；通过本票只宣称 Increment 3 完成，不宣称场景四、完整六场景矩阵、默认模型选择、完整短篇、默认入口切换或旧路径退役完成。

**Not in this ticket:** 修改单项工具规则来迁就模型输出、降低 hard gate、场景四 NPC 表现、72 次配置矩阵、完整试玩、默认配置选择、默认入口切换或旧代码删除。
