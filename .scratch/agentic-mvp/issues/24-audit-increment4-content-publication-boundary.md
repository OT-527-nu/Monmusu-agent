# 24 — 审计并冻结增量四内容发布与验收边界

**What to build:** 在 session 浏览和普通续玩完成后，审查 Agentic MVP 的参考资料 provenance、快照、上下文组装和 Prompt revision 边界，并把项目文档同步到已经确认的增量四范围。若审计发现真实缺口，只增加最小确定性回归测试或必要修复；不重复建设已有快照能力。

**Blocked by:** 23 — 实现 Agentic CLI 的 session 选择与普通续玩

**Status:** ready-for-agent

- [ ] 审计并用确定性测试证明：新 session 保存模组/角色 revision 与实际 SHA-256，session-local 快照保持不变，工作树后续修改不会改变已有 session 的 GM 上下文。
- [ ] 审计并用确定性测试证明：完整已提交回合、当前事实索引、模组参考书和角色资料进入 ongoing 新回合上下文；Prompt 使用运行级 `PROMPT_REVISION`，不新增 per-session Prompt 快照或内容 manifest。
- [ ] 记录内容发布纪律：模组、角色或 GM Prompt 发生实质修改时，维护者主动递增相应 revision；hash 负责精确内容识别，revision 负责人工可读的发布标识。
- [ ] 更新权威迁移说明，使六个聚焦场景和真实场景 runner 统一留给增量五；增量四的工程完成条件与项目所有者后续人工试玩的项目验收条件明确分开，同时不改写 ADR-041/ADR-042 对增量五评估矩阵的要求。
- [ ] 文档和测试明确：Ticket 22–24 通过不等于真实 GM 质量通过；本票不运行聚焦场景、开放试玩、真实 provider、72 次矩阵或模型选择。
- [ ] Agent 不修改模组参考书、角色资料或 GM Prompt 的语义内容；内容打磨、revision 发布和最终人工试玩由项目所有者完成。
- [ ] 运行全量确定性测试、编译检查、目标静态检查和差异检查，并在结果中列出任何未验证的真实试玩风险。

**Not in this ticket:** 新建模组加载器、Prompt per-session 持久化、内容 manifest、真实场景证据 runner、模型评估、默认入口切换或旧路径清理。
