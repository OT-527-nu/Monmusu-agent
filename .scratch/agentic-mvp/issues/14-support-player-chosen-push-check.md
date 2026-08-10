# 14 — 支持玩家选择的 push_check

**What to build:** 一次允许孤注一掷的失败检定提交后，玩家可以在后续自由文本中提出不同做法并接受更严重失败风险；同一个 GM 随后调用 `push_check`，Harness 引用原检定的可信机械记录并完成一次新的不可覆盖检定。原骰点和结果永久保留，新结果即时提交并由 GM 忠实承接。

**Blocked by:** 12 — 扩展 Agentic COC 工具生命周期; 13 — 冻结六张生产 ActorSheet

**Status:** ready-for-agent

- [ ] `push_check` 参数只接受一条 `kind: "check"` 机械记录的 `mechanic_id` 作为语义化 `check_id`，以及非空的新做法和事前更严重失败风险；它不引入旧 MVP 的第二套检定 ID。
- [ ] Harness 从原检定读取 actor、能力、难度、奖励/惩罚骰和可见性，不接受 GM 重交目标值、骰点、成功等级或替换角色；所有参数和前置条件在随机数产生前固定并校验。
- [ ] 只有选中玩家调查员的公开、失败、非 fumble、规则允许且尚未进入 Push/Luck 补救链的基础检定可以推动；成功、fumble、NPC/隐藏检定、未知或非 check ID、已经推动、已经花费 Luck，以及对 pushed 派生结果再次推动都在抽取随机数前返回稳定错误。
- [ ] 成功调用创建新的 `kind: "check"` 机械及新 `mechanic_id`，记录 `is_pushed=true` 与指向原失败记录的 `pushed_from`；原记录不覆盖、不删除、不重掷。
- [ ] 原始检定的 `push_eligible`/`luck_eligible` 只作为提交时资格快照保留；Push 成功后不回写原记录，当前链级资格由 preflight 从不可变历史推导，pushed 结果的两个快照字段为 `false`。
- [ ] pushed 机械沿用原检定可见性并按共同生命周期即时原子提交；工具后中断与多次恢复只重放同一 pushed 结果，不产生第二次骰点或派生记录。
- [ ] GM 可以说明 push 机会，但真实场景中只有玩家当前输入明确提出新做法并接受风险后才调用；Harness 不增加自然语言意图分类器或额外确认事件。
- [ ] 确定性规则例、边界反例、原子写入故障和公开 Harness seam 测试覆盖成功/失败/fumble、非法前置、链级一次性、可见性、同 GM 续接与恢复幂等。

**Not in this ticket:** 花费 Luck、伤害、理智、自然语言意图审批、自动替玩家 push、完整战斗或真实 DeepSeek 场景验收。
