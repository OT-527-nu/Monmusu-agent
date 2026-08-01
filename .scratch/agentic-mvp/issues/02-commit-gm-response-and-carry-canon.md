# 02 — 提交 GM 最终答复并将正典带入下一回合

**What to build:** 玩家在新版 CLI 输入自由文本后，同一个 GM Loop 可以通过可编程假 `GameMasterModel` 返回直接最终答复。Harness 从冻结的本局资料组装请求，验证并原子提交叙事与公开/隐藏正式世界事实（正典），只在提交成功后向玩家展示公开结果，并在下一回合把仍然有效的完整正典交还同一个 GM。

Blocked by: 01 — 从新 CLI 初始化不可变的 Agentic Session

Status: done

## References

- [Parent spec](../spec.md): User Stories 18-24, 28, 32, 35, 39 and 47; Implementation Decisions “Runtime authority identifiers”, “Context assembly”, “Final response contract”, “Canon and facts”, “Final validation and atomicity”, and the canonical-submission acceptance gate.
- [Migration](../../../docs/agentic_mvp/migration.md): “增量 1：真实 DeepSeek 最小纵向切片”的目标切片、实现范围、验收门和暂不完成边界。
- [Data contracts](../../../docs/agentic_mvp/contracts.md): “事实账本 `FactRecord`”, “GM 最终答复 `GameMasterResponse`”, “已提交回合 `CommittedTurn`”, “未完成回合 `IncompleteTurn`”, “GM 上下文”和“`GameMasterModel` seam”。
- [Agent Loop](../../../docs/agentic_mvp/agent_loop.md): “新回合预检与上下文”, “最终答复校验与提交”和“故障处理”。

- [x] 新路径定义一个薄的 `GameMasterModel` provider adapter seam，并提供可编程假 adapter；假 adapter 能脚本化响应并捕获请求，但 final/tool-call 分类、协议 ID 判断、业务 schema 校验、正典与存储提交全部由 Harness 负责。
- [x] 首次和后续请求从冻结会话组装当前玩家原文、主持能力章程与最终答复格式、不可变 `SessionSetup` 开场记录、按哈希验证的完整模组与人物快照、`InvestigatorProfile`、显示名与角色卡、所有当前公开和隐藏事实、按顺序排列的完整已提交游戏记录，以及当前启用的工具定义；不引入 RAG、摘要、场景权限投影或第二个 Agent。
- [x] Harness 接受玩家原文后、第一次模型请求前生成稳定 `turn_id`，并把该 ID、原玩家输入和最小未完成回合外壳原子持久化；后续 provider 材料、工具交互、失败状态和最终提交都复用该 ID，final 阶段不得重新分配 `turn_id`。
- [x] Harness 只接受顶层恰好包含 `narration`、`establish`、`retire` 和 `session_status` 的合法最终答复；校验顶层及嵌套字段类型、未知字段、枚举、去除首尾空白后非空的字符串、事实可见性、有效且不重复的 retire 引用，以及非空 `retire.reason`。确定性测试覆盖合法 establish/retire、未知/已结束/重复 retire 和代表性的类型、未知字段及空白字符串错误；任一无效 final 都不提交或展示叙事与事实。
- [x] 合法最终答复复用回合开始时持久化的 `turn_id`，并由 Harness 按 `establish` 出现顺序分配稳定 `fact_id`；公开和隐藏 `establish`、事实 retire 历史、`CommittedTurn`、`session_status` 与 `incomplete_turn = null` 在同一次 `session.json` 原子替换中一起成功或一起失败。
- [x] `session_status=complete` 成功提交后，任何后续玩家输入都在分配新 `turn_id` 和调用模型前被稳定拒绝；确定性测试证明不会创建未完成回合、改变会话聚合或调用 `GameMasterModel`。
- [x] 玩家投影只在提交成功后展示 `narration` 和公开事实变化；隐藏事实仍作为可信正典保存在同一聚合的事实账本与 GM 游戏记录中，并进入后续 GM 请求，但其正文不进入 CLI 普通输出、技术错误或公开测试产物。
- [x] 确定性两回合测试让第一回合脚本化 `establish` 获得稳定并持久化的 `fact_id`，再证明第二回合捕获的 GM 请求包含该有效事实；该测试不以字符串断言判断事实是否由开放行动造成，也不评价第二回合文案的连续性。
- [x] provider 调用失败、响应 envelope 无法形成合法 final，或最终结构/原子写入失败时，Harness 保留原玩家输入和已经收到的原始回合材料及稳定失败状态，向 CLI 返回明确的技术中断，且不展示未提交模型文本、不生成兜底叙事、不另起回合或重复提交。
- [x] 中断后的会话不会接受新的虚构行动覆盖原回合；本票只要求原材料可诊断、已提交状态不丢失和安全停止，不提供正式 resume UX、自动结构 repair、请求/尝试超时或完整幂等恢复矩阵。

**Not in this ticket:** COC 工具执行、真实 provider adapter、人工主持质量判断或 Increment 2 的正式恢复能力。

## Comments

- 2026-07-27：实现 `GameMasterModel`/`ScriptedGameMasterModel` 与 `AgenticHarness.start_turn`。Harness 在调用模型前保存稳定 `turn_id` 的未完成外壳，从冻结快照、完整历史和当前公开/隐藏事实组装请求；合法 direct final 只经一次原子替换提交回合、事实变化和会话状态。
- 确定性 fake-adapter 覆盖两回合正典连续性、公开/隐藏投影、合法 establish/retire、未知/重复/已结束引用和代表性 schema 错误、provider/协议/原子写入失败、完成态阻塞及事实与回合双向完整性。真实 DeepSeek、COC 工具和正式 resume 仍未验证，按后续 ticket 处理。
- 验证：`PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_agentic_*.py'`（31 passed，`/usr/bin/python3` 3.12.3）；`PYTHONPATH=src python3 -m unittest discover -s tests`（116 passed）；`PYTHONPATH=src python3 -m compileall -q src tests`、data JSON 解析、变更文件的 mypy/Ruff 及 `git diff --check` 均通过。全量 mypy 仍有旧 `src/monmusu_agent/tools.py:633` 告警；全量 Ruff 仍有未触及旧文件/测试的 10 项告警。
- 2026-07-28：提交 `13681c7` 修复复审阻塞项：`model_profile` 在分配回合前按精确非秘密字段重建；不可哈希 final 枚举稳定转为 `invalid_final_response`；运行时 System 正文直接读取权威 `gm_prompt.md` 章程代码块；已结束的开场事实仍以原文和来源进入 `OPENING_FACT_HISTORY`。
- 修复后复审：以 `a2de19b...13681c7` 为固定范围分别完成 Standards 与 Spec 审查。Standards 无硬性违规，记录 assistant 消息和 model profile 验证的重复所有权异味供 Ticket 03 收敛；Spec 无阻塞项或额外范围，`finish_reason=length` 与完整恢复仍按 Increment 2 延后。
- 修复后验证：`PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_agentic_*.py'`（34 passed）；`PYTHONPATH=src python3 -m unittest discover -s tests`（119 passed）；目标文件 mypy/Ruff 与 `git diff --check` 通过。解释器仍为 `/usr/bin/python3` 3.12.3。
