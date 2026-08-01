# 05 — 验证真实两回合开放行动纵向切片

**What to build:** 使用真实 key 先完成非跳过的 DeepSeek 契约验证，再以聚焦场景一从新版 CLI 完成两回合受控运行并形成可审查证据。真实 DeepSeek 必须接住参考书未预写的玩家行动，直接裁定或调用 `make_check`，建立由该行动造成的正式事实，并在第二回合明确承接这项世界变化。

Blocked by: 04 — 通过显式运行配置接入 DeepSeek Chat Completions

Status: done

## References

- [Parent spec](../spec.md): User Story 18 and 50-52; the vertical-slice, player-authority/visibility, and MVP-completion acceptance gates; deterministic versus real-GM testing decisions.
- [Migration](../../../docs/agentic_mvp/migration.md): “增量 1：真实 DeepSeek 最小纵向切片”的验收门和旧路径处理。
- [Evaluation](../../../docs/agentic_mvp/evaluation.md): “真实 DeepSeek 契约测试”, “场景一：接住模组未预写的合理破局”, “硬门槛”和唯一的“评估记录格式”。
- [ADR-011](../../../docs/adr/0011-first-slice-proves-open-action-continuity.md), [ADR-039](../../../docs/adr/0039-validation-has-deterministic-and-live-deepseek-lanes.md), [ADR-040](../../../docs/adr/0040-live-gm-evaluation-uses-gates-and-human-rubric.md), and [ADR-044](../../../docs/adr/0044-real-llm-vertical-slice-leads-migration.md).

- [x] 使用项目所有者提供给组合入口的真实 key 非跳过地运行 Ticket 04 的契约套件，证明账户实际可用模型完成直接 final 和一次带匹配 `tool_call_id` 的 `make_check` 往返；skip、mocked SDK 或 fake adapter 结果不能替代这项门槛。
- [x] 从独立新会话运行已批准的聚焦场景一及其两条玩家输入，使用同一版本的主持能力章程、工具 schema、模组/人物快照、角色数据和合法 `SessionSetup`；运行不读取旧检查规则、效果白名单、固定场景权限或预定义结局。
- [x] 第一回合对参考书未预写但当前虚构中合理的行动作出具体裁定，可以直接解决或调用一次 `make_check`，不能因为参考书没有预写路线而拒绝行动或强行导回钥匙路线；若调用调查员的玩家主动检定，GM 必须在 RNG 前将其标记为 public。
- [x] 第一回合至少 `establish` 一项由该未预写行动造成的事实，Harness 为其分配稳定 `fact_id` 并持久化；无关、预制或纯装饰事实不满足此门槛。
- [x] 第二回合的实际 GM 请求包含第一回合仍有效的事实，真实 GM 输出明确承认并依据该事实继续裁定玩家行动；人工评估者分别记录“行动与事实存在因果关系”和“第二回合承接了该后果”的判断及理由。
- [x] 两回合都只展示已经提交的公开机械、已提交的公开事实变化和通过验证、原子提交后的 narration；隐藏事实、隐藏机械、provider envelope、诊断或未验证内容均不出现在玩家侧或公开评估证据中。
- [x] 真实契约运行和两回合场景统一按 [Evaluation 的“评估记录格式”](../../../docs/agentic_mvp/evaluation.md)保存脱敏证据，不另定义平行字段集；记录必须完整包含该格式要求的版本/配置/输入输出/事实变化，并逐请求记录 `finish_reason`、usage、latency、有效 provider 参数、本地错误类别和修正次数。
- [x] 证据明确记录协议合法性、机械真实性、隐藏内容控制、调查员所有权、承认正典和开放行动有效性六项硬门槛，并为每项结论引用具体消息、工具记录或 `fact_id`，另附人工因果判断；任一硬门槛失败即如实记录为失败，不以流畅文案抵消，也不只保留精选成功对话。
- [x] 完成该证据只证明 Increment 1 的真实纵向切片，不把新 CLI 切成默认入口，不宣称正式恢复、完整 COC 工具、完整六场景矩阵或默认模型选择已经完成。

**Not in this ticket:** Increment 2-6 的恢复、其余工具、内容整合、72 次候选矩阵、完整试玩、默认配置选择或旧路径退役。

## Comments

- 2026-07-29：真实 `deepseek-v4-flash` non-thinking 契约运行通过直接 final 与一次 `make_check` 往返；聚焦场景一在独立新会话提交两个回合，第一回合建立的两个公开事实实际进入第二回合请求并影响后续裁定。完整成功、失败与修正记录见 `docs/agentic_mvp/evidence/ticket-05-live-open-action-2026-07-29.md`。
- 项目所有者确认报告建议：六项硬门槛全部通过；行动与事实存在因果关系，第二回合承接该后果；六维评分为虚构因果 5、即兴能力 5、跨回合连续性 5、NPC 表现 4、节奏 3、氛围 4，均分 4.33。
- 双轴复核（固定点 `c95595b`，包含当前工作树）：Standards 无剩余规范问题或代码异味；Spec 确认真实运行记录已逐请求包含 profile、`finish_reason`、usage、latency、本地错误、修正次数（历史缺失值显式为 `null`），且六项硬门槛均有具体 run/tool/mechanic/fact/final message 引用。
- 最终验证：`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_agentic_*.py'`（64 passed）；`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`（149 passed）；`PYTHONPATH=src .venv/bin/python -m compileall -q src tests`、目标源码 mypy、变更文件 Ruff 和 `git diff --check` 均通过。解释器为 `.venv/bin/python` 3.12.3，Python 3.12.3，OpenAI SDK 1.109.1。
