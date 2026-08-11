# 15 — 支持玩家选择的 spend_luck

**What to build:** 对一条规则允许的失败检定，玩家可以在当前自由文本中明确花费指定幸运点数或授权花费达到目标所需的点数；同一个 GM 调用 `spend_luck` 后，Harness 校验检定补救链、所需点数和余额，原子扣减角色 Luck 并追加有效成功等级记录，而不覆盖原骰点。

**Blocked by:** 12 — 扩展 Agentic COC 工具生命周期; 13 — 冻结六张生产 ActorSheet; 14 — 支持玩家选择的 push_check

**Status:** ready-for-human

- [x] `spend_luck` 参数只接受一条 `kind: "check"` 机械记录的 `mechanic_id` 作为语义化 `check_id` 和合法正整数点数；actor、原骰点、声明难度的 `target` 与当前 Luck 均从可信会话读取。
- [x] Harness 独立计算 `points = original_roll - original_target`，要求玩家恰好支付该点数并使有效骰值达到原检定声明的难度；拒绝零/负数、过量或不足点数、余额不足、`target == 0`、成功、critical、fumble、NPC/隐藏检定、未知或非 check ID，以及规则禁止花 Luck 的结果。Luck 不能制造 `critical_success`。
- [x] `push_check` 与 `spend_luck` 在同一原始检定及其所有派生 `kind: "check"` 记录组成的补救链上互斥：任何节点已经推动或花费 Luck 后，整条链不能再采用另一种补救，也不能重复花费。
- [x] 成功调用追加新的 `kind: "luck_spend"` 机械，记录原 check 引用、扣减前后 Luck、花费点数及前后有效成功等级；原 check 的骰点、目标和原始成功等级保持不变，后续上下文按调整后的有效等级解释。
- [x] 只有选中玩家调查员的公开检定可以进入 `spend_luck`；Luck 变化恒为公开且不接受 visibility 参数。机械、角色扣减、交互及协议消息原子提交后才发布，任何写入失败都不扣点或留下部分记录。
- [x] 工具后 provider 中断、进程重建和重复恢复只回放同一扣减与 `luck_spend` 机械，不重复消耗 Luck；GM 最终叙事不能覆盖可信数值或有效成功等级。
- [x] 确定性规则例、余额/等级边界、push 后花 Luck 与花 Luck 后 push 的双向反例、原子故障和公开 Harness seam 测试通过；玩家是否明确选择由后续真实场景验证，不伪装成 Harness 语义断言。

## Comments

- 2026-08-11：实现 `SpendLuckTool`。严格参数只接受语义化 `check_id` 与正整数 `points`；preflight 从可信机械和冻结角色卡读取 actor、原骰点、声明难度目标与当前 Luck，要求 `points = roll - target`，并在任何 ID 分配、数值变化或随机源访问前拒绝非法点数、余额和来源。
- 2026-08-11：Luck 只接受先前玩家回合提交的公开调查员基础失败；成功、fumble、`target == 0`、NPC/隐藏、同回合自动使用、未知/非 check、pushed 来源、既有 Push/Luck 链及重复消费均稳定拒绝。Push/Luck 共用可信基础检定授权 helper，工具仍保留各自资格字段、错误契约与执行规则。
- 2026-08-11：成功调用不使用 RNG，原子扣减调查员 Luck 并追加公开 `luck_spend`，保留原 check 的骰点、目标和原成功等级；有效等级按原声明难度精确映射为 regular/hard/extreme success，不能制造 critical。loader 验证来源先于派生、链唯一性、精确算术、角色余额连续性和公开投影。
- 2026-08-11：以 `ddce080` 为固定点完成 Standards/Spec 双轴审查。Spec 未发现漏项、错误实现或范围蔓延；Standards 发现生产授权逻辑与拒绝测试搭建重复，收敛后又发现 Ticket 14 的两条持久化错误消息发生漂移及一个未使用测试参数，均已修复并由全量回归验证。
- 验证：`.venv/bin/python`（Python 3.12.3）；`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`（246 passed）、`PYTHONPATH=src .venv/bin/python -m compileall -q src tests`、`agentic_coc.py`/`agentic_harness.py` mypy、Ruff `E4,E7,E9,F,I`、5 个本地历史 v1 session 装载与 `git diff --check ddce080` 均通过。Ticket 15 未运行真实 provider 场景；玩家是否明确选择花费 Luck 仍由 Ticket 18 的真实场景与人工判断独立证明。

**Not in this ticket:** Luck 恢复或成长、对 damage/SAN/fumble 使用 Luck、自然语言意图分类器、自动最优消费、真实 DeepSeek 场景验收或旧路径迁移。
