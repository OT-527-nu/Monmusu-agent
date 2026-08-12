# 18 — 验收 Increment 3 工具与真实场景

**What to build:** 把 `make_check`、`push_check`、`spend_luck`、`deal_damage` 和 `make_sanity_check` 作为同一 GM Loop 的完整工具面进行集成验收，并使用真实 DeepSeek 完成聚焦场景二和三。确定性证据证明机械、原子性、恢复和可见性，真实场景证据证明 GM 尊重检定时机与玩家拥有的 push/Luck 选择；两条证据分开记录、共同构成 Increment 3 交付门。

**Blocked by:** 14 — 支持玩家选择的 push_check; 15 — 支持玩家选择的 spend_luck; 16 — 支持公开与隐藏 deal_damage; 17 — 支持公开与隐藏 make_sanity_check

**Status:** ready-for-agent

- [ ] 新回合完整 profile 在同一请求配置中暴露五个规范工具并保持 `tool_schema_version: "coc-tools-agentic-mvp-1"`、JSON Object、non-streaming、单 GM 与单次响应至多一个工具调用的既有协议；完整注册表保留所有已发布验证器，旧未完成回合仍使用自己的冻结工具子集。原玩家输入和全部已提交结果始终回到同一 GM 上下文。
- [ ] 确定性组合矩阵跨独立会话覆盖六张生产模板，每局只冻结选中调查员与三名同行者共四张 ActorSheet；矩阵覆盖五工具的成功路径、边界、非法前置、未知角色/能力、公开/隐藏投影、GM 结果续接及跨工具引用，不以真实模型结果替代规则断言。
- [ ] 组合矩阵区分六张生产模板与每局四张冻结卡，并覆盖统一 preflight、统一 `PublicMechanic`、Push/Luck 资格快照与精确 Luck 点数、严格骰子限制、伤害阈值和 SAN threshold 字段。
- [ ] 每种新增工具都制造工具成功后 provider 中断、进程重建和显式恢复，证明相同 `turn_id`、`tool_call_id`、mechanic ID、骰点及 HP/SAN/Luck 变化只出现一次；原子写入故障不留下部分机械或数值变化。
- [ ] 跨工具测试证明 `check_id` 实际引用 `kind: "check"` 的 `mechanic_id`，push/Luck 在整条派生检定链上双向互斥，GM 最终叙事和事实变化不能覆盖任何已提交机械权威。
- [ ] 聚焦场景二使用真实 DeepSeek 证明明显成立的行动被直接裁定，有真实不确定性与失败代价的行动才调用事前公开的 `make_check`，不增加检定前暂停，也不先叙述结果再补骰。
- [ ] 聚焦场景三使用真实 DeepSeek 证明 GM 可以说明 push/Luck 机会但不会自动调用；玩家未选择时不消费资源或重掷，玩家明确提出不同做法并接受风险后只调用关联原失败检定的 `push_check`，不偷偷同时花 Luck。
- [ ] live 记录固定模型、thinking、stream、tool schema、Prompt/profile、fixture、timeout 与依赖版本，按既有格式脱敏保存请求投影、usage、latency、结果和 hard gates；没有 key 或 skip 不能算通过，凭据与 reasoning 正文不得进入证据。
- [ ] 确定性测试、真实 provider 合同和用户人工 GM 行为判断明确分栏报告；场景二、三都必须非跳过真实运行并由用户给出结论。缺少 key、任一真实场景或人工结论时本票保持未完成；通过本票只宣称 Increment 3 完成，不宣称场景四、完整六场景矩阵、默认模型选择、完整短篇、默认入口切换或旧路径退役完成。

**Not in this ticket:** 修改单项工具规则来迁就模型输出、降低 hard gate、场景四 NPC 表现、72 次配置矩阵、完整试玩、默认配置选择、默认入口切换或旧代码删除。

## Comments

- 2026-08-12：新增公开 Harness 组合测试，跨三个调查员会话覆盖六张生产模板、每局四张冻结卡、五工具完整 profile、Push/Luck、公开与隐藏机械及跨工具上下文；全量 271 项测试通过。
- 2026-08-12：新增显式 `MONMUSU_RUN_INCREMENT3_EVALUATION=1` runner。fake SDK 证明 enable/key 门和脱敏边界；真实 `deepseek-v4-flash` non-thinking 场景二、三均非跳过完成，自动协议/机械门通过。证据见 `docs/agentic_mvp/evidence/ticket-18-increment-3-live-scenarios-2026-08-12.md`。
- 2026-08-12：用户已说明睡觉期间无法提供人工 GM 判断；人工硬门与六维量表保持 `pending_user`，因此本票不标记完成，等待用户审阅真实输出。
- 2026-08-12：最终 Spec 复核后收紧 Push 来源 ID、脱敏上下文连续性和 timeout 证据，并让三个独立调查员会话各自实际运行完整五工具 profile；全量仍为 271 项通过。修正后真实复跑未 skip，但场景二错误检定拿钥匙且未检定跳桥，场景三在 Push 后擅自调用 damage 并中断，自动硬门失败。Codex 对第一轮完整叙事的候选人工评价已记录，用户结论仍为 `pending_user`；本票不能通过。
- 2026-08-12：Spec 再复核发现 fixture active 误判、正文/结果只按 ID 取证和固定三请求假设；已通过公开 runner seam 修复并增加 canonical SHA-256 对照，允许合法 retire 与一次结构修正，不放宽工具路径。fixture-v2 使用权威原输入、完整仓库/断桥风险、可同时 Luck/Push 的基础失败，并结束与“脚步逼近”冲突的开场事实；全量 271 项、compileall、四模块 mypy、目标 Ruff 和差异检查通过。
- 2026-08-12：仅执行一次 fixture-v2 真实运行。场景二 `increment3_8ee749bbe86b42e1bfe31fa98d58a911` 自动门通过：拿钥匙直接成立，跳桥只调用一次公开 `make_check` 并提交 final。场景三 `increment3_588fc5557c34472d89ef65ab190efd85` 正确保留 Luck/Push 选择并提交关联基础失败的唯一 Push，但 Push 后 final 结构无效，唯一修正请求发生 `provider_error`，回合中断；协议和完整连续性门失败。未重跑挑样本，本票仍为 `ready-for-agent` 且不能完成。
- 2026-08-12：用户明确回复“采用”，采纳 Codex 对 fixture-v2 的候选人工判断。场景二六项硬门全通过，六维为 `4/4/5/4/3/5`、平均 `4.17`；场景三因协议中断与完整连续性失败不进入正式六维评分。人工判断条件现已满足，但不能补偿真实场景三失败；按禁止挑样复跑和不得修改工具规则迁就输出的边界，本票没有可辩护的本票内修复路径，保持 `ready-for-agent`、未完成且不提交。
- 2026-08-12：用户随后明确授权多跑场景三，以诊断原失败是否来自 provider 输出不稳定。相同 fixture-v2、profile、输入和骰序列下新增五个独立诊断样本，全部两回合 committed、路径 `final, push_check, final`、零 repair、零 provider/local error，五项自动门通过；这反驳确定性的 Push 后本地缺陷，并支持原无效 final 具有非确定性。原 repair 的 generic `provider_error` 仍无法由这些未触发 repair 的样本精确归因。五份样本的 run ID、usage、latency、脱敏记录哈希、自动结果与语义异常均已单独分栏记录；完整脱敏 JSON 暂存 `/tmp`，不替换冻结验收样本或已采纳的失败结论。
- 2026-08-12：用户再次明确回复“采用”，确认五次成功运行仅作为稳定性诊断，不重新定义为 Ticket 18 的新验收批次。原场景三验收失败与已采纳人工结论保持有效，本票继续未完成。
