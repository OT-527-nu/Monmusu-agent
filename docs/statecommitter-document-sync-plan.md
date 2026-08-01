# StateCommitter 文档同步计划

> 状态：已完成
> 记录日期：2026-07-23
> 完成日期：2026-07-23
> 范围：把现行文档从旧的批量补丁模型迁移到已实现的 StateCommitter MVP 契约。本文是同步计划，不替代 `schemas.md` 或已接受 ADR 的正式契约。

## 执行结果

- ToolExecutor 外部入口已冻结为 `ToolExecutor.start_turn(context)`、`ToolSession.available_tool_definitions()` 和 `ToolSession.execute(tool_name, arguments)`。
- 当前动态工具目录只包含 `request_check` 和 `apply_effect`。
- `apply_effect` 接收 `expected_state_version`、`source_type`、`source_id`、`effect_id` 和 `reason`；`reason` 进入 ToolSession 规范化轨迹，但不承诺写入 commit metadata 或 EventLog。
- `schemas.md`、`architecture.md`、`turn_loop.md`、`prompts.md` 和 `mvp_design.md` 已同步；查询、角色生成、Memory 和 EventLog 明确标记为后续 MVP 切片。
- [ADR-003](adr/0003-single-effect-game-state-commit.md) 已局部取代 ADR-001 的旧批量工具名称和首版跨文件写入范围。

以下保留原同步目标和顺序，作为本次文档迁移的审计记录。

## 已冻结的 MVP 目标

以下内容来自当前可执行 seam，后续同步不得退回旧模型：

- 一次状态申请只选择一个固定 `effect_id`；不提供外部批量效果提交。
- GM 的申请语义是 `expected_state_version + source + effect_id + reason`，而不是 `path + operation + value` 补丁。
- `source` 只允许两类：`check_id`，或模组静态 `event_rule_id`。不再允许 `tool_call` 作为状态效果来源。
- `RuleEngine` 根据 `CheckRule.effects_by_outcome` 在检定时冻结 `CheckResult.allowed_effect_ids`；StateCommitter 只验证该授权快照，不重新解释检定规则。
- 模组的 `effect_definitions` 保存具体 operations。MVP 只支持受白名单约束的 `set`、`increment`、`add_unique`、`remove`、`ensure_at_least`，不是通用 JSON Patch。
- 单个 effect 内的多条 operation 必须全部预演成功才写入；对外一次只返回该 effect 的 `CommitResult`。
- 首版原子范围仅为一个 `GameState` 文件及其 `state_version`、`commit_metadata`。Memory、EventLog、关系阶段和跨文件事务不属于这次提交。
- 结果状态为 `applied`、`already_applied`、`no_state_change` 或 `rejected`。`no_state_change` 不生成 `commit_id`、不递增版本、也不消费来源。

`tool_call_id` 仍保留为 ToolExecutor 的轨迹、错误和角色生成结果标识；计划删除的是它作为**状态效果授权来源**的含义，而不是删除该通用追踪字段。

## 同步顺序

### 1. ToolExecutor 接口冻结后先定外部名称（已完成）

前置条件：下一个 ToolExecutor 模块明确其公开工具名、参数转换和每回合预算。

- 以现有内部 seam `StateCommitter.apply_effect` 为基线，决定对 GM 使用 `apply_effect`，还是保留一个明确的单数适配名称。
- 将 ToolExecutor 的输入转换为 `ApplyEffectArgs`；GM 不能构造 `StateChange` 或模组 operation。
- 决定 `reason` 是否进入工具轨迹。当前它是申请字段，但尚未写入 `commit_metadata` 或 EventLog，因此文档不能提前承诺审计语义。
- 明确当 `allowed_effect_ids` 非空时，Agent Loop 如何决定是否申请其中一个效果；StateCommitter 只保证“至多选择一个”，不强制每个检定都被消费。

### 2. 以 `schemas.md` 为中心替换旧批量契约（已完成）

前置条件：步骤 1 的工具名和外部参数已确定。

- 给 `CheckResult` 增加必填的 `allowed_effect_ids`，并说明 `rule_id=null` 时它必须为空。
- 删除 `apply_validated_effects` 的 `effects[]`、`path`、`operation`、`value`、`accepted_effects` 和 `rejected_effects` 批量 schema。
- 新增单效果请求与 `CommitResult` schema：版本、两类 source、`effect_id`、`reason`、状态、可选 `commit_id`、`changes` 与可选 `error_code`。
- 将旧 `module_event_id` 改为当前实现使用的静态 `event_rule_id`；不要暗示存在独立的模组事件 Ledger。
- 为模组数据补充最小契约说明：`effect_definitions`、`check_rules[].effects_by_outcome`、`event_rules` 和受限 operation DSL。不要把 operation 字段暴露给 GM 工具请求。

### 3. 同步叙事与执行文档（已完成）

前置条件：步骤 2 的 schema 已落定，避免文本与 JSON 契约分别命名同一个工具。

| 文档 | 当前需要替换的内容 | 同步后的重点 |
| --- | --- | --- |
| `architecture.md` | 架构图中的 `apply_validated_effects`；StateCommitter 的工具结果来源、任意路径校验与跨文件原子提交描述 | 单效果数据流；`allowed_effect_ids` 授权快照；GameState-only 原子边界；Memory/EventLog 另列为未实现职责 |
| `turn_loop.md` | 批量工具预算、三类来源、路径/数值补丁校验和批量接受/拒绝结果 | 每次提交一个 effect；`check_id` / `event_rule_id`；幂等、来源消费、版本冲突与 `no_state_change` 的回合处理 |
| `prompts.md` | GM 可以引用 `tool_call_id` 申请效果的规则 | GM 只提交 `expected_state_version`、来源、`effect_id` 与 `reason`，只能叙述 `CommitResult` 已确认的状态变化 |
| `mvp_design.md` | StateCommitter 同时写 GameState、Memory、EventLog 的范围与旧接口名称 | 当前已交付的 GameState-only 首版；Memory/EventLog/关系阶段列入后续工作 |

`docs/adr/0002-lightweight-player-facing-mvp-loop.md` 不包含旧提交细节，预期不需要因本次同步而修改。

### 4. 处理 ADR-001 的窄冲突（已完成）

`ADR-001` 将 StateCommitter 写成同时写入 GameState、Memory 和 EventLog。已接受的 ADR 不应直接改写历史理由。

[ADR-003](adr/0003-single-effect-game-state-commit.md) 已按该范围建立：它只取代 ADR-001 中 StateCommitter 的 MVP 写入范围和旧批量工具名称，不改变单 GM Agent 等其余决定。Memory、EventLog、关系阶段、跨文件事务和更丰富的持久后果仍列为后续计划。

## 不在这次同步中处理的内容

- 实现 Memory、EventLog 或关系阶段写入。
- 实现跨文件事务、崩溃恢复或多进程并发控制。
- 让开放检定或即兴行动获得新的持久状态写入能力。
- 增加 `persistent_consequence`、硬回声或通用事件系统。
- 把受限 operation DSL 扩展为脚本语言或任意 JSON Patch。

## 完成判据

同步完成后已满足：

1. [x] 现行文档中不存在 `tool_call` 可作为状态效果来源的表述。
2. [x] 现行状态提交工具没有批量 `effects[]` 或 GM 提供 `path / operation / value` 的 schema。
3. [x] `schemas.md`、`architecture.md`、`turn_loop.md`、`prompts.md` 和 `mvp_design.md` 对工具名称、两类来源、单效果语义和写入范围使用同一术语。
4. [x] 文档声称的 `CheckResult`、`ApplyEffectArgs` 和 `CommitResult` 字段均能在代码与 seam 测试中找到对应行为。
5. [x] Memory、EventLog 和关系变化仍被明确标记为未进入首版 StateCommitter 原子范围。
