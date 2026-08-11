# 14 — 支持玩家选择的 push_check

**What to build:** 一次允许孤注一掷的失败检定提交后，玩家可以在后续自由文本中提出不同做法并接受更严重失败风险；同一个 GM 随后调用 `push_check`，Harness 引用原检定的可信机械记录并完成一次新的不可覆盖检定。原骰点和结果永久保留，新结果即时提交并由 GM 忠实承接。

**Blocked by:** 12 — 扩展 Agentic COC 工具生命周期; 13 — 冻结六张生产 ActorSheet

**Status:** ready-for-human

- [x] `push_check` 参数只接受一条 `kind: "check"` 机械记录的 `mechanic_id` 作为语义化 `check_id`，以及非空的新做法和事前更严重失败风险；它不引入旧 MVP 的第二套检定 ID。
- [x] Harness 从原检定读取 actor、能力、难度、奖励/惩罚骰和可见性，不接受 GM 重交目标值、骰点、成功等级或替换角色；所有参数和前置条件在随机数产生前固定并校验。
- [x] 只有选中玩家调查员的公开、失败、非 fumble、规则允许且尚未进入 Push/Luck 补救链的基础检定可以推动；成功、fumble、NPC/隐藏检定、未知或非 check ID、已经推动、已经花费 Luck，以及对 pushed 派生结果再次推动都在抽取随机数前返回稳定错误。
- [x] 成功调用创建新的 `kind: "check"` 机械及新 `mechanic_id`，记录 `is_pushed=true` 与指向原失败记录的 `pushed_from`；原记录不覆盖、不删除、不重掷。
- [x] 原始检定的 `push_eligible`/`luck_eligible` 只作为提交时资格快照保留；Push 成功后不回写原记录，当前链级资格由 preflight 从不可变历史推导，pushed 结果的两个快照字段为 `false`。
- [x] pushed 机械沿用原检定可见性并按共同生命周期即时原子提交；工具后中断与多次恢复只重放同一 pushed 结果，不产生第二次骰点或派生记录。
- [x] GM 可以说明 push 机会，但真实场景中只有玩家当前输入明确提出新做法并接受风险后才调用；Harness 不增加自然语言意图分类器或额外确认事件。
- [x] 确定性规则例、边界反例、原子写入故障和公开 Harness seam 测试覆盖成功/失败/fumble、非法前置、链级一次性、可见性、同 GM 续接与恢复幂等。

## Comments

- 2026-08-11：实现 `PushCheckTool`。preflight 只接受先前玩家回合已提交的公开调查员基础失败，冻结 actor、能力、难度、奖励/惩罚骰和可见性；成功执行复用共同 d100 规则，以新 ID 保存 `pushed_from`/`is_pushed`，替换新做法与更严重风险，并把两个补救资格快照固定为 `false`。基础记录保持不变，公开投影保留 Push 来源字段。
- 2026-08-11：补救链按不可变历史推导并强制因果顺序。未知/非 check、成功、fumble、NPC/隐藏、同回合自动 Push、重复 Push、推动 pushed 结果及既有 Luck 链均在第二次 ID/RNG 前稳定拒绝；loader 拒绝 source 晚于派生记录、断链、继承参数改写和额外字段。
- 2026-08-11：基础检定资格快照改为仅公开调查员的非 fumble 失败；失败按声明难度判断，因此未达到 hard/extreme 的较低成功等级仍可补救。为保持恢复兼容，loader 额外接受 `2c8bf10` 曾生成的精确旧资格公式，但不改写旧记录；Push preflight 仍按真实角色、可见性、结果和链重新授权。
- 2026-08-11：以 `2c8bf10` 为固定点完成 Standards/Spec 双轴审查。首轮发现旧快照无法恢复、同回合可自动 Push、loader 未验证因果顺序；修复并补齐回归测试后，最终两轴复审均无剩余实现阻塞、范围蔓延或明确代码异味。
- 验证：`.venv/bin/python`（Python 3.12.3）；`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`（233 passed）、`PYTHONPATH=src .venv/bin/python -m compileall -q src tests`、目标源码 mypy、Ruff `E4,E7,E9,F,I`、5 个本地历史 v1 session 装载与 `git diff --check 2c8bf10` 均通过。Ticket 14 未运行真实 provider 场景；玩家自然语言选择仍由 Ticket 18 的真实场景与人工判断独立证明。

**Not in this ticket:** 花费 Luck、伤害、理智、自然语言意图审批、自动替玩家 push、完整战斗或真实 DeepSeek 场景验收。
