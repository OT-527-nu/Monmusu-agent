# ADR-003：单效果 GameState 提交边界

- 状态：Accepted
- 日期：2026-07-23
- 决策范围：Monmusu Agent 8 周 MVP 的状态效果申请、授权来源和首版原子写入范围
- 决策关系：局部取代 [ADR-001](0001-single-gm-agent-and-character-tools.md)，依赖 [ADR-002](0002-lightweight-player-facing-mvp-loop.md)
- 局部取代范围：ADR-001 中 `apply_validated_effects` 批量工具名称，以及 StateCommitter 在首版同时原子写入 GameState、Memory 和 EventLog 的描述
- 相关文档：`../schemas.md`、`../architecture.md`、`../turn_loop.md`、`../prompts.md`、`../mvp_design.md`

## 背景

ADR-001 确立了单一 GameMasterAgent 和确定性状态权威，但当时仍把状态提交描述为批量效果工具，并让一个逻辑 StateCommitter 同时负责 GameState、Memory 和 EventLog。

RuleEngine、CheckLedger 和 StateCommitter 的 MVP seam 实现后，批量任意补丁会让 GM 同时承担选择机械效果、构造路径和值以及组织跨文件事务。这个边界既扩大模型权限，也增加幂等、部分失败、版本冲突和测试组合。Memory、关系阶段与 EventLog 还具有不同的可见性和生命周期，不应为了接口表面统一而塞进首版 GameState 事务。

## 决策

### 单效果外部接口

GameMasterAgent 只通过 `ToolSession.execute("apply_effect", arguments)` 申请状态效果。一次调用只包含：

- `expected_state_version`
- `source_type`
- `source_id`
- `effect_id`
- `reason`

GM 不得提交 `path`、`operation`、`value` 或批量 `effects[]`。具体变化只来自版本化模组数据中的 `effect_definitions`。

### 两类授权来源

状态效果来源只允许：

- `source_type=check`：`source_id` 引用当前游戏和回合的 `check_id`。RuleEngine 已按实际 outcome 把授权冻结在 `CheckResult.allowed_effect_ids`。
- `source_type=module_event`：`source_id` 引用模组静态 `event_rule_id`。事件规则绑定场景、条件、重复策略和唯一效果。

`tool_call_id` 只用于 ToolSession 轨迹和错误关联，不能授权状态写入。

### StateCommitter 首版范围

StateCommitter 当前是 GameState 的唯一效果写入边界：

- 验证来源、效果授权、当前场景、模组条件和 `expected_state_version`。
- 从 `effect_definitions` 读取受限 operations，并对一个 effect 的全部操作进行完整预演。
- 只有全部操作成功才原子替换一个 GameState 文件，同时更新 `state_version` 和 `commit_metadata`。
- 通过来源和效果键保证幂等，并限制同一来源至多选择一个效果。

首版原子范围不包含 Memory、EventLog、关系阶段或跨文件事务。它们仍属于 MVP 目标，但必须使用后续明确契约，不能借用 `apply_effect.reason`、`tool_call_id` 或任意 GameState 路径实现。

### 稳定结果

`CommitResult.status` 只有四种：

- `applied`
- `already_applied`
- `no_state_change`
- `rejected`

`no_state_change` 不生成 `commit_id`、不递增版本、也不消费来源。`rejected` 不写入状态。一个调用只有一个效果，因此没有批量部分接受语义。

## 后果

### 正面

- GM 只选择已经由规则或事件授权的效果，无法构造任意状态补丁。
- StateCommitter 的职责集中在一个 GameState 文件，幂等和版本冲突更容易测试。
- RuleEngine 负责在检定发生时冻结授权，StateCommitter 不重复解释检定规则。
- Memory、关系和日志可以按各自的隐私、回放和持久化需求单独设计。

### 代价

- 一个检定即使允许多个候选效果，也需要 GM 至多选择一个并单独调用。
- 当前无法把即兴开放检定直接转换成持久状态；`allowed_effect_ids` 为空时只能产生当回合叙事。
- 完整回合仍需要后续补齐 Memory 和 EventLog 写入边界。

## 不变的既有决定

本 ADR 不改变 ADR-001 的单 GameMasterAgent、角色上下文隔离、确定性检定和用户主导权，也不改变 ADR-002 的 GM 语义路由与最小 TurnContext 决定。

## 后续计划

- 根据真实回合数据决定是否需要单独的 Memory/关系写入接口。
- 为正式 EventLog 定义不与 GameState 强绑的追加与回放契约。
- 只有实际需求证明有价值时，再评估跨文件事务、硬回声或更丰富的持久后果系统。
