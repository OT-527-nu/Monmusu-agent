# 03 — 在同一 GM Loop 中执行并持久化 `make_check`

**What to build:** 同一个 GM 可以在处理玩家自由文本时调用唯一的 `make_check` 语义工具。Harness 从冻结角色卡执行可信 COC 7e 检定，先原子保存机械和协议交互，再把结果返回同一个 GM 完成本轮；玩家立即看到已提交的公开机械，而隐藏机械只供 GM 使用。

Blocked by: 02 — 提交 GM 最终答复并将正典带入下一回合

Status: done

## References

- [Parent spec](../spec.md): User Stories 7-10, 14, 29-30, 33-34, 36 and 48; Implementation Decisions “COC tool surface and contract-specific parameters”, “Tool protocol and idempotency”, “Mechanical commit ordering”, plus the mechanical and protocol acceptance gates.
- [Migration](../../../docs/agentic_mvp/migration.md): “增量 1：真实 DeepSeek 最小纵向切片”的 `make_check` 范围、验收门和暂不完成边界。
- [Data contracts](../../../docs/agentic_mvp/contracts.md): “工具调用统一外壳”, “`ToolInteraction`”, “共同机械字段”, “`make_check`”, “原子性与不变量”和“纵向交付子集”。
- [Agent Loop](../../../docs/agentic_mvp/agent_loop.md): “工具分支”, “机械提交与玩家输出”和“故障处理”。

- [x] 当前动态工具目录只暴露数据契约定义的 `make_check`；调用在 RNG 前固定并验证 `actor_id`、`ability`、原生 `difficulty`、奖励/惩罚骰、`action`、事前 `stakes` 和 `visibility`，GM 不能提供目标值、骰点、成功等级或结果。
- [x] Harness 只从本局冻结的 `ActorSheet` 和技能目录读取能力值，并正确结算 regular、hard、extreme、奖励/惩罚骰、critical、fumble 及所有成功等级；确定性 RNG 测试使用独立预期值覆盖代表性边界和无效能力/参数反例。
- [x] `visibility` 是同一工具契约中的必填参数并在 RNG 前冻结。公开结果在提交后立即向 CLI 展示行动、能力、难度、奖励/惩罚骰 `dice_adjustment`、事前风险、骰点与结果；隐藏结果完整持久化并返回 GM，但不出现在 CLI 普通输出、技术错误或公开证据中，且结果产生后不能更改可见性。
- [x] Harness 而非 adapter 识别单工具分支并校验可关联的 `tool_call_id`；成功调用将机械记录、`ToolInteraction`、原始与规范化参数及对应 assistant/tool 协议消息在同一次原子替换中保存，只有写入成功后才向 GM 或 CLI 报告成功。
- [x] 可编程假 adapter 的端到端测试覆盖一次 `make_check`、匹配 `tool_call_id` 的 tool result 回传以及同一个 GM 随后的合法 final；最终 `CommittedTurn` 按提交顺序引用该机械，并且公开叙事仍须在最终原子提交后才展示。
- [x] 参数或领域校验失败且 `tool_call_id` 可可靠关联时，不调用 RNG、不生成机械或改变角色状态；Harness 在把结构化 tool error 返回同一个 GM 前，原子保存 assistant tool-call、失败 `ToolInteraction`、`arguments_raw`、可规范化时的 `arguments` 和对应 tool-error 消息。若协议 ID 无法可靠关联或该写入失败，则只保留可诊断的原始协议材料并技术中断，不用叙事掩盖失败；全面的 replay、multiple-tool、ID 异常与恢复矩阵留给 Increment 2。
- [x] 工具已经成功提交后，任何后续 provider、final 校验或 final 写入失败都保留该机械、工具交互和原始回合材料，向 CLI 返回技术中断；测试证明没有回滚、兜底叙事、隐藏重掷、重复机械或重复提交。
- [x] 新路径不读取旧 `check_rules`、`effect_definitions`、`allowed_effect_ids` 或 `ending_id`，也不调用 `request_check`、`apply_effect` 或旧效果授权链；旧实现继续作为独立基线存在。

**Not in this ticket:** `push_check`、`spend_luck`、`deal_damage`、`make_sanity_check`、正式恢复 UX、超时、结构 repair、八响应保险丝或完整异常协议矩阵。

## Comments

- 2026-07-28：实现 `make_check` 的唯一动态工具目录、冻结角色卡读取、COC 7e d100/奖励惩罚骰/成功等级结算、单工具协议回传、公开/隐藏机械投影及原子工具提交。确定性 fake-adapter 覆盖单工具后同一 GM final、无效参数和不可关联 ID、写入失败、工具后 provider/final 故障、隐藏可见性和 CLI 发布顺序；旧效果授权路径未被新路径调用。
- 2026-07-28：提交 `fc6ef70` 补强已保存检定与冻结 ActorSheet 的来源一致性。提交 `20054db` 补强 JSON 类型边界：`true` 不能冒充能力值为 1 的 `target`，也不能冒充失败 `ToolInteraction` 中奖励骰的整数 `count`。
- 验证：`PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_agentic_*.py'`（48 passed，`/usr/bin/python3` 3.12.3）；`PYTHONPATH=src python3 -m unittest discover -s tests`（133 passed）；`PYTHONPATH=src python3 -m compileall -q src tests`、目标源码 mypy、目标文件 Ruff 和 `git diff --check` 均通过。确定性 fake-adapter 是本票 Harness 证据；真实 DeepSeek adapter 与人工因果评估分别仍属于 Ticket 04 和 Ticket 05。
