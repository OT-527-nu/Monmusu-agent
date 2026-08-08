# 15 — 支持玩家选择的 spend_luck

**What to build:** 对一条规则允许的失败检定，玩家可以在当前自由文本中明确花费指定幸运点数或授权花费达到目标所需的点数；同一个 GM 调用 `spend_luck` 后，Harness 校验检定补救链、所需点数和余额，原子扣减角色 Luck 并追加有效成功等级记录，而不覆盖原骰点。

**Blocked by:** 12 — 扩展 Agentic COC 工具生命周期; 13 — 冻结六张生产 ActorSheet; 14 — 支持玩家选择的 push_check

**Status:** ready-for-agent

- [ ] `spend_luck` 参数只接受一条 `kind: "check"` 机械记录的 `mechanic_id` 作为语义化 `check_id` 和合法正整数点数；actor、原骰点、目标值与当前 Luck 均从可信会话读取。
- [ ] Harness 独立计算把原结果提高到下一项规则允许成功等级所需的点数，并拒绝零/负数、过量或不足点数、余额不足、成功、fumble、未知或非 check ID，以及规则禁止花 Luck 的结果。
- [ ] `push_check` 与 `spend_luck` 在同一原始检定及其所有派生 `kind: "check"` 记录组成的补救链上互斥：任何节点已经推动或花费 Luck 后，整条链不能再采用另一种补救，也不能重复花费。
- [ ] 成功调用追加新的 `kind: "luck_spend"` 机械，记录原 check 引用、扣减前后 Luck、花费点数及前后有效成功等级；原 check 的骰点、目标和原始成功等级保持不变，后续上下文按调整后的有效等级解释。
- [ ] 调查员 Luck 变化恒为公开且不接受 visibility 参数；机械、角色扣减、交互及协议消息原子提交后才发布，任何写入失败都不扣点或留下部分记录。
- [ ] 工具后 provider 中断、进程重建和重复恢复只回放同一扣减与 `luck_spend` 机械，不重复消耗 Luck；GM 最终叙事不能覆盖可信数值或有效成功等级。
- [ ] 确定性规则例、余额/等级边界、push 后花 Luck 与花 Luck 后 push 的双向反例、原子故障和公开 Harness seam 测试通过；玩家是否明确选择由后续真实场景验证，不伪装成 Harness 语义断言。

**Not in this ticket:** Luck 恢复或成长、对 damage/SAN/fumble 使用 Luck、自然语言意图分类器、自动最优消费、真实 DeepSeek 场景验收或旧路径迁移。
