# 17 — 支持公开与隐藏 make_sanity_check

**What to build:** 当角色遭遇已经成立的恐怖来源时，同一个 GM 可以事前提交成功与失败 SAN 损失表达式及可见性；Harness 使用冻结 ActorSheet 完成可信理智检定、结算 SAN 和本局累计损失，并即时报告目标契约规定的理智阈值。GM 负责表现感官与行为，不得直接修改 SAN 或替玩家决定调查员行动。

**Blocked by:** 12 — 扩展 Agentic COC 工具生命周期; 13 — 冻结六张生产 ActorSheet

**Status:** ready-for-human

- [x] `make_sanity_check` 只接受冻结 actor、非空恐怖来源、成功/失败两项受限 SAN 损失表达式和事前 visibility；不接受 SAN 目标、检定骰、损失骰、最终损失、角色行为或状态补丁。
- [x] `deal_damage` 与 `make_sanity_check` 共用严格表达式解析器；`N <= 20`、`M <= 100`，理论最小值和最大值都必须在 `0..100`。Harness 在任何随机抽取前拒绝未知 actor、非法/过大/理论负损失表达式和不合法 visibility，再依据冻结 SAN 完成 d100 检定并只抽取所选分支的损失表达式。
- [x] 成功调用追加 sanity 机械，保存恐怖来源、检定骰、成功与否、选用表达式及骰值、SAN 前后值、本局累计损失和 `temporary_insanity_threshold_reached` / `indefinite_insanity_threshold_crossed` 阈值；SAN 与累计值保持 schema 边界。
- [x] SAN 只计算数值和阈值：`target=san_before`，`outcome=success if d100<=target else failure`，只抽取所选分支损失，`san_loss=min(raw_loss, san_before)`；`session_start_san=san_before+previous_session_loss`、`indefinite_threshold=ceil(session_start_san/5)`，临时阈值为 `san_loss>=5`，不定性字段只在 `previous_session_loss < indefinite_threshold <= session_san_loss` 时为 true。固定值 `0` 不调用 RNG。
- [x] 调查员 SAN 变化必须公开并结构化拒绝 hidden；契约允许的秘密 NPC 理智机械可以事前 hidden，完整结果供 GM 使用但从玩家记录和普通输出过滤，结果产生后不能改可见性。
- [x] sanity 机械、SAN/累计损失变化、交互和协议消息一次原子提交后才返回 GM；写入失败不改变角色，工具后中断与重复恢复不重新检定、重掷损失或重复扣减。
- [x] GM 只能忠实表现可信结果，不能用最终叙事覆盖骰点、SAN 数值或规则阈值，也不能借理智结果接管玩家调查员未声明的长期意图、台词或关系承诺。
- [x] 确定性测试覆盖成功/失败两分支、表达式和 SAN 边界、阈值反例、公开/隐藏投影、独立 RNG 期望、原子故障与恢复幂等，并保持完整疯狂规则不在本票范围内。

## Comments

- 2026-08-12：实现 `MakeSanityCheckTool`，从冻结 ActorSheet 读取当前 SAN 与本局累计损失，先完成 d100 成败判断，再只解析并抽取事前提交的对应损失分支。固定零损失不调用损失 RNG；SAN 截断、累计损失、临时阈值和不定阈值均由同一纯结果函数计算并在装载时复算。
- 2026-08-12：调查员 SAN 变化结构化强制公开；NPC hidden 理智机械完整写盘并返回同一 GM，但不产生玩家 `PublicMechanic`。机械、角色 SAN、交互和协议消息原子提交；注入写失败不留部分状态，provider 中断及连续两次恢复不重新分配 ID、掷骰或扣减。
- 2026-08-12：loader 严格复核 schema、所选表达式与骰值、d100 成败、SAN 算术、累计连续性、两个阈值、角色余额和调查员可见性。同 ID 恢复若只更换未选损失分支也视为协议冲突，证明两条表达式均在 RNG 前冻结。
- 2026-08-12：以 `f1aa619` 为固定点完成 Standards/Spec 双轴审查。Standards 的持久化骰值验证与普通测试装配重复已收敛；Spec 发现的未授权 `san.current + session_loss <= san.max/99` 约束已移除，并增加 `san_loss == 5` 与 `ceil(66/5)` 的判别边界。复核未发现剩余规范缺失、范围扩张或实现错误。
- 验证：`.venv/bin/python`（Python 3.12.3）；`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`（267 passed）、`PYTHONPATH=src .venv/bin/python -m compileall -q src tests`、`agentic_coc.py`/`agentic_harness.py`/`agentic_session.py` mypy、Ruff `E4,E7,E9,F,I`、5 个本地历史 v1 session 装载与 `git diff --check f1aa619` 均通过。Ticket 17 未运行真实 provider 场景；该票只提供确定性 Harness/持久化证据。

**Not in this ticket:** 完整疯狂发作表、恐惧症/躁狂症内容生成、治疗与恢复、魔法、由 Harness 判断恐怖来源是否成立，或替玩家控制调查员。
