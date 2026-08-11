# 16 — 支持公开与隐藏 deal_damage

**What to build:** 当虚构中已经成立伤害来源时，同一个 GM 可以提交受限伤害表达式、原因、护甲适用性和事前可见性；Harness 为冻结 ActorSheet 掷伤害、应用护甲、更新 HP，并即时报告可信的重伤、昏迷或死亡等规则结果。GM 负责叙述具体后果，不能直接提交 HP 补丁。

**Blocked by:** 12 — 扩展 Agentic COC 工具生命周期; 13 — 冻结六张生产 ActorSheet

**Status:** ready-for-human

- [x] `deal_damage` 只接受冻结 actor、目标契约规定的受限骰子表达式、非空原因、护甲适用性和事前 visibility；不接受最终伤害、HP、阈值结果、状态路径或任意代码表达式。
- [x] `deal_damage` 与 `make_sanity_check` 共用严格表达式解析器；`N <= 20`、`M <= 100`，理论最小值和最大值都必须在 `0..100`。Harness 在第一次随机抽取前拒绝未知 actor、非法或过大表达式、理论负伤害及不合法护甲参数；合法表达式使用可注入 RNG，并以冻结 armor 与 HP 计算独立可核对的最终伤害。
- [x] 成功调用追加 damage 机械，保存表达式、每次骰值、原始伤害、护甲减免、最终伤害、HP 前后值和重伤/昏迷/死亡等契约阈值；HP 不低于规则下界，零最终伤害也形成可审计结果。
- [x] 伤害阈值固定为 `armor_applied=min(raw_damage, armor)`（护甲适用时，否则为 0）、`damage_taken=raw_damage-armor_applied`、`major_wound=damage_taken>=ceil(max_hp/2)`、`dead=damage_taken>=max_hp`、`unconscious=hp_after==0 and not dead`；不增加 CON 检定、濒死、治疗或战斗轮。
- [x] 调查员 HP 变化必须公开并结构化拒绝 hidden；只有契约允许的 NPC 机械可以事前标记 hidden，随机结果产生后不能更改 visibility，隐藏记录完整保存但不进入玩家投影。
- [x] damage 机械、HP/状态变化、交互与协议消息一次原子提交后才返回 GM；写入失败不改变角色，工具后中断和重复恢复不重新掷伤害或重复扣减。
- [x] GM 上下文收到完整可信结果并只能解释它；最终答复若声称不同骰点、伤害、HP 或规则阈值，不会改变机械权威。
- [x] 确定性规则例、表达式与护甲边界、0 HP 和阈值反例、公开/隐藏投影、原子故障及恢复幂等测试通过，并保持既有五工具之外的规则范围不变。

## Comments

- 2026-08-11：实现共享严格骰子表达式与 `DealDamageTool`。固定非负整数和 `NdM` 可选修正经过完整语法、`N <= 20`、`M <= 100` 及理论 `0..100` 边界校验；非法字段、未知 actor、错误可见性和非法表达式均在 ID/RNG/HP 变化前稳定拒绝。
- 2026-08-11：伤害执行从冻结 actor 读取 HP/armor，以单一纯规则函数计算护甲、最终伤害、HP、重伤、昏迷和死亡。成功结果严格保存契约字段；固定零伤害、护甲完全吸收、奇数 max HP 的 ceil 边界、0 HP 昏迷/死亡互斥及 `N=20`/`M=100` 等号边界均有独立字面期望。
- 2026-08-11：调查员 HP 变化结构化强制公开；NPC 隐藏伤害完整写盘并回传 GM，但无玩家投影。`validate_result_arguments` 同时读取冻结 actors，把 `armor_applies` 与精确减伤结果绑定；未完成回合即使同时篡改 mechanic、interaction、tool 消息和当前 HP 也不能伪造护甲适用性。
- 2026-08-11：工具提交、角色 HP、交互和协议消息保持一次原子替换；写失败不留下部分状态。工具后 provider 中断及连续两次恢复只回放同一 damage，不重新分配 ID、抽骰或扣 HP；loader 复算 schema、骰值、算术、角色余额、阈值、可见性和伤害连续性。
- 2026-08-11：以 `40fddeb` 为固定点完成 Standards/Spec 双轴初审。Standards 的生产规则/actor 读取重复与普通测试装配重复已收敛；Spec 的重复恢复证据、奇数 max HP 反例和护甲适用性可信绑定三项发现均已修复，并通过新增回归及全量测试。
- 验证：`.venv/bin/python`（Python 3.12.3）；`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`（256 passed）、`PYTHONPATH=src .venv/bin/python -m compileall -q src tests`、`agentic_coc.py`/`agentic_harness.py`/`agentic_session.py` mypy、Ruff `E4,E7,E9,F,I`、5 个本地历史 v1 session 装载与 `git diff --check 40fddeb` 均通过。Ticket 16 未运行真实 provider 场景；该票只提供确定性 Harness/持久化证据。

**Not in this ticket:** 攻击命中检定、战斗轮、先攻、武器/装备系统、治疗、追逐、任意表达式语言或由 Harness 决定虚构伤害是否成立。
