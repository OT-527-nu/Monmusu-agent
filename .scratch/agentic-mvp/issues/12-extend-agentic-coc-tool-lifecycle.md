# 12 — 扩展 Agentic COC 工具生命周期

**What to build:** 在现有单 GM Loop 中建立可逐项接入其余四个 COC 工具的共同执行生命周期，使每个工具都能沿用统一的参数校验、即时机械提交、协议回传、玩家投影和未完成回合恢复，同时保持已经交付的 `make_check` 行为兼容。这个扩展只保护共享的可信边界，不建设通用规则平台或第二套 orchestrator。

**Blocked by:** 11 — 证明真实恢复契约

**Status:** ready-for-human

- [x] 同一动态工具目录和 Harness 生命周期能够按显式运行 profile 暴露多个已注册 COC 工具；未知工具仍产生稳定错误，现有只启用 `make_check` 的 profile、请求和恢复记录保持可读可运行。
- [x] 新工具调用统一经过 `normalize → 领域 preflight/冻结输入 → Harness 分配 mechanic_id/committed_at → RNG/execute → 原子提交 → PublicMechanic 投影`；preflight 失败不分配 ID/时间、不取 RNG、不改变角色数值，且任何工具名都不在 Harness 中拥有专用分支。
- [x] 所有工具统一保存 provider 原始参数、通过 schema 后的规范参数、成功结果或结构化错误；带可用 `tool_call_id` 的失败调用不产生机械或数值变化，但其交互与 assistant/tool 协议消息在反馈 GM 前原子提交。
- [x] 成功工具调用把机械记录、角色数值变化、`ToolInteraction` 与对应协议消息作为一次原子替换持久化，写入成功后才返回 GM 或发布公开机械；提交失败不留下部分结果。
- [x] 恢复按 `(turn_id, tool_call_id)` 对每一种已启用工具执行同参数幂等重放；已经提交的骰点、机械 ID 和角色变化不重复生成，相同 ID 搭配不同参数仍被拒绝。
- [x] 共同机械校验接受各工具规范定义的 `kind` 和字段，同时强制 Harness 生成的 `mechanic_id`、可信角色引用、提交时间和事前可见性；不能继续把所有机械或投影写死为 `make_check`。
- [x] 玩家投影只发布 `public` 机械并保留规定的事前参数与结果；隐藏机械、隐藏事实、provider envelope、私有诊断和 reasoning content 不进入普通输出。
- [x] 玩家调用层只有一种 `PublicMechanic(mechanic_id, kind, actor_id, details)`；`details` 由实际 `CocTool` 选择和校验，Harness 不按 `kind` 猜测结构，`push_check` 的 `pushed_from`/`is_pushed` 等公开字段不能丢失。
- [x] 新回合默认 profile 暴露五个规范 COC 工具；恢复回合使用各自 `IncompleteTurn` 冻结的 `model_profile` 与工具子集，只需验证它仍受 `coc-tools-agentic-mvp-1` 完整注册表支持，不要求等于新回合默认 profile。
- [x] 公开 Harness 生命周期测试使用临时真实 session 和可编程假 model，证明至少一个非 `make_check` 测试工具能走通成功、参数错误、工具后中断、进程重建与同回合恢复；既有 Increment 1/2 全量基线保持通过。

## Comments

- 2026-08-08：`CocTool` 注册器接入统一 Harness 生命周期；`make_check` 通过适配器保持兼容，并由测试专用 `lifecycle_test` 证明规范化、原子机械/幸运变化提交、公开投影、工具后中断、进程重建和 `(turn_id, tool_call_id)` 幂等恢复。
- 2026-08-08：修复审查发现的可信边界：profile 可选择注册目录子集；工具只接收 actor 快照；异常、坏 mechanic、伪造 actor/ID/时间和隐藏调查员资源变化均在写盘前拒绝；恢复校验使用同一注册工具的 normalizer/result validator/public projection。
- 2026-08-08：结构校验、规范参数与结果字段映射、冻结角色引用和跨机械持久化一致性均由注册工具契约拥有；Session 不再按具体 mechanic kind 或 `make_check` 分支，并允许不同工具校验同一种 mechanic kind。验证器只接收快照，不能修改待提交或已装载状态。
- 2026-08-11：默认 `coc-tools-agentic-mvp-1` 注册表发布五个规范目录项；后四项只实现严格 schema normalizer 与 `tool_not_implemented` preflight，占位调用仍保存 raw/规范参数并支持语义同参重放，不提前实现 Ticket 14-17 的领域规则。多工具拒绝路径也使用同一 normalizer 保存和重放失败交互。
- 2026-08-11：`PublicMechanic` 在公开类型自身递归冻结 `details`，并提供独立 JSON 副本供 CLI 与脱敏 contract 记录序列化；CLI 统一输出完整 tool-owned details，不再按 `kind` 选择字段。冻结 provider 必须与当前 Harness 声明的 provider 能力一致，但 model ID、thinking 与工具子集继续按未完成回合 profile 恢复。
- 2026-08-11：以 `af440c6` 为固定点完成 Standards/Spec 双轴审查。修复了嵌套和直接构造投影可变、CLI 丢失 Push 来源字段、占位工具未规范化、冻结 provider 未预检以及多工具失败路径未保存规范参数等发现；最终复审无实现阻塞。保留的非阻塞判断是构造期 profile 校验与恢复 profile 校验存在少量重复，本票不为此扩展抽象。
- 验证：`.venv/bin/python`（Python 3.12.3）；`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`（210 passed）、`PYTHONPATH=src .venv/bin/python -m compileall -q src tests`、目标五个源码文件 mypy、Ruff `E4,E7,E9,F,I` 与 `git diff --check af440c6` 均通过。Ticket 12 未运行真实 provider 场景；真实场景二、三仍由 Ticket 18 独立证明。

**Not in this ticket:** 实现 `push_check`、`spend_luck`、`deal_damage` 或 `make_sanity_check` 的具体 COC 规则，增加任意状态补丁工具、通用表达式语言、provider registry、自动模型路由或旧路径删除。
