# 回合循环

## 文档状态

本文档是 Monmusu Agent MVP 的现行回合协议。它实现 [ADR-001](adr/0001-single-gm-agent-and-character-tools.md) 的单 Agent 决策、[ADR-002](adr/0002-lightweight-player-facing-mvp-loop.md) 的 GM 语义路由、[ADR-003](adr/0003-single-effect-game-state-commit.md) 的单效果 GameState 提交边界和 [ADR-004](adr/0004-user-input-gameengine-turn-contract.md) 的外层回合与结果所有权，并替代已归档的 [回合协议设计 v2](archive/turn_protocol_v2.md)。

字段以 [JSON 数据契约](schemas.md) 为准，组件职责以 [系统架构](architecture.md) 为准。

## 回合不变量

每个回合都必须满足：

1. 只有 `GameMasterAgent` 拥有工具循环。
2. `GameEngine` 在循环开始前创建只读 `TurnContext`，确定总步骤和每工具配额；动态工具目录由 ToolSession 产生。
3. `generate_character_turn` 实现后，普通回合调用 0-1 次，任一回合最多调用 2 次。
4. 计划中的角色工具不能读取其他角色私有记忆或调用其他工具。
5. 角色输出、Agent 参数和叙事建议都不是正式状态。
6. d100 骰点、目标值和成功等级只由 `RuleEngine` 产生。
7. 当前正式 `GameState` 效果只由 `StateCommitter` 写入；Memory、EventLog 和关系阶段使用后续独立契约。
8. 用户的行动和关键选择不能由角色或主持人代替提交。
9. 达到预算、模型调用失败或输出非法时，本轮必须降级终止，不能递归扩张。
10. 运行时不存在 ADR-002 已取代的外围路由层。
11. 模型只返回 `GameMasterDraft`；最终检定、提交、结局和轨迹只能由 GameEngine 从可信来源组装。

## 回合输入

MVP 只有一个外层入口：

```python
GameEngine.run_turn(input_text: str) -> GameTurnOutcome
```

`input_text` 必须是 1 至 4000 字符的非空用户原文。程序不在调用前推断触发类型；用户输入“继续”“等一下”或“让她先说”时，仍由 GameMasterAgent 在世界内语境中理解。空输入不构成回合。

确定性系统事件和 UI 闲置提醒当前都没有入口。未来出现真实需求时，应另行定义权限和调用契约，不能重新给本接口添加含义混杂的 `trigger_type`。

### TurnContext

GameEngine 为一次合法用户输入创建最小、只读的 `TurnContext`：

- `turn_id` 和未经改写的 `input_text`。
- `initial_game_state` 只读快照。
- `max_tool_steps` 和 `tool_limits`。

`TurnContext` 只供可信代码和 ToolSession 使用，不直接交给模型。GameEngine 另外创建固定的 `GameMasterStateView`、场景投影和公开记忆快照；GameMasterAgent 每一步把它们与动态工具目录、此前工具交互组装为 `ModelRequest`。模型看不到 `turn_id`、完整 GameState、原始预算、私有记忆或隐藏 flags。

### GM 回合策略

`GameMasterAgent` 读取用户原文和只读状态后，自行选择执行策略：

| 策略 | 用途 | 角色调用期望 |
| --- | --- | --- |
| `fast` | 普通提问、调查、闲聊和单一行动 | 0-1 |
| `dramatic` | 真实分歧、显著代价和多人协作 | 1-2 |
| `urgent` | 危机时钟达到 5/6 或 6/6 | 0-2 |

策略只影响 Agent 的表达与工具选择，不授予权限，也不改变 GameEngine 设定的最高预算。输入缺少必要信息时，GM 可以直接请求澄清，不必创建独立模式。

## 默认回合预算

MVP 默认上限：

| 预算 | 上限 |
| --- | --- |
| GameMasterAgent 工具循环步骤 | 8 |
| `request_check` | 2 |
| `apply_effect` | 2 |

`GameMasterAgent.max_iterations` 由构造时注入并保持有限；它与 ToolSession 的工具步骤预算是两个边界。`generate_character_turn` 和 `update_memory` 尚未加入当前 `ToolExecutor`；各自实现时再增加独立配额。场景与已发现线索已经随 `ModelRequest` 提供，MVP 不另设查询工具。

## 总流程

```text
user input
  -> GameEngine preflight
  -> TurnContext
  -> GameMasterAgent limited tool loop
       -> understand input / choose strategy
       -> request_check
       -> apply_effect
       -> GameMasterDraft
  -> GameEngine trusted aggregation
  -> GameTurnOutcome(result + tool_trace + degradation)
```

以上是当前已实现工具切片。角色生成、Memory 写入和正式 EventLog 会在后续 MVP 切片接入，但不能在进入动态目录前被 GM 调用。工具可以按需要交错调用，但必须满足因果顺序，例如不能在获得检定结果前提交依赖该结果的效果。

## 阶段 1：创建回合快照

`GameEngine`：

1. 校验用户原文和运行依赖。
2. 读取并校验 GameState、Memory 和本轮需要的模组/角色静态数据。
3. 已有 `ending_id` 时拒绝创建新回合。
4. 分配本轮稳定且跨轮唯一的 `turn_id`；它不要求递增，也不写入 GameState。
5. 创建只读 `TurnContext`、`GameMasterStateView`、场景投影和公开记忆快照。
6. 用固定上限组装 `max_tool_steps` 和各工具 `tool_limits`。

预检失败发生在 `turn_id` 分配、模型调用和工具调用之前。`ToolExecutor.start_turn(context)` 随后创建本回合唯一的 `ToolSession`。如果输入需要澄清，GameMasterAgent 可以直接返回 Draft，不启动检定或状态写入。

## 阶段 2：有限工具循环

`GameMasterAgent` 每一步只能：

- 调用一个当前允许的工具；或
- 返回候选 `GameMasterDraft`。

循环开始后，GameMasterAgent 先理解用户目的并选择 `fast`、`dramatic` 或 `urgent` 策略。该策略只进入 Draft，不能改变工具目录或预算。

每一步前，GameMasterAgent 重新组装 `ModelRequest`，并只能从其中的 `available_tools` 获得此刻可用的名称和参数 schema。该字段直接来自 `ToolSession.available_tool_definitions()`；模型不能从 Prompt、先前回合或隐藏预算推导第二套工具清单。工具配额耗尽后，下一次请求会移除该工具。

`ToolSession.execute(tool_name, arguments)` 在每次调用前检查：

- 工具是否属于当前实现且在 `tool_limits` 中。
- 参数是否符合 schema。
- 本轮剩余预算。
- RuleEngine 或 StateCommitter 是否接受对象、授权、来源和效果。

拒绝调用时，工具返回统一 `ToolResult(ok=false, data=null, error=ToolError)`，并消耗一次总循环步骤，但参数预检失败不消耗该工具的分发配额。所有调用都会获得只用于轨迹的 `tool_call_id`。

### 场景与线索上下文

GameEngine 在循环开始前确定性组装 `scene_context`：当前场景的 `public_facts`、`interactions`、`boundaries`、`discovery_opportunities`，以及 `clues_found` 中线索的标题和公开文本。该投影本轮固定且只读。

MVP 不实现 `query_scene` 或 `query_clue`。未发现线索的正文不会进入 `ModelRequest`；发现机会只允许 GM 安排观察和行动，不能直接叙述为用户已经掌握的事实。`apply_effect` 若正式揭示线索或进入场景，会通过可信 `context_delta` 告知本轮模型，但不改写初始投影。

### 生成角色回合

> 后续 MVP 切片：当前 `ToolExecutor` 尚未暴露本节工具。

调用 `generate_character_turn` 时，GameMasterAgent 只提供：

- `character_id`。
- `participation_role`。
- `generation_mode`。
- `purpose`。

`ToolExecutor` 再补充该角色允许读取的上下文。GameMasterAgent 不能传入、查看或拼接角色私有记忆。

合法的 `participation_role`：

- `primary_responder`：主要回应用户或场景。
- `supporter`：补充不同能力角度。
- `dissenter`：提出有依据的不同意见或风险。
- `reactor`：对刚发生的结果作简短角色反应。

合法的 `generation_mode`：

- `independent_proposal`：形成独立方案、风险判断或分歧，不读取其他角色尚未公开的判断。
- `public_reaction`：回应已经公开的台词或结果，可以读取本轮明确提供的公开发言。

工具返回 `CharacterTurnProposal`。其中：

- `speech` 可以直接进入候选叙事。
- `proposed_action` 只是角色自己的行动提议。
- `intent`、`suggested_skill` 和 `relationship_signal` 用于主持人理解和后续工具调用。
- 工具不得返回角色私有记忆原文或其他角色信息。

### 角色行动授权

角色行为分为自然反应、已授权协作、显著代价行动和用户专属决定。`request_check.authorization` 只接受以下来源：

- `user_declared`：用户在本轮声明自己的行动，只适用于用户角色。
- `user_delegated`：用户在本轮明确授权该角色行动。

两种授权的证据都必须是当前 `input_text` 中实际出现的原文。只是说话、观察性动作或不改变正式状态的表现不需要伪造授权，也不调用当前两个机械工具。普通建议、争论或未经授权的完整行动只能作为台词和选项展示。跨回合分工和模组紧急反应若未来需要机械效果，应另行冻结契约，不能由 GM 自行声明。

### 请求检定

`request_check` 至少包含：

- 行动者和行动者类型。
- 行动描述与目标。
- 建议技能。
- `suggested_context_modifier` 及其公开理由。
- 角色行动的授权来源。
- 可追溯的本轮用户原文证据。
- 不包含 `check_id`、基础技能、场景难度、最终目标值或骰点；这些字段由可信代码产生。

GameMasterAgent 只判断当前行动是否需要检定，并提交上述候选信息。ToolExecutor 先验证动态目录、参数 schema 和剩余预算；预检失败直接返回 `ToolError`，不调用 RuleEngine，也不创建检定记录。行动者、授权、目标和规则语义由 RuleEngine 验证。

`RuleEngine`：

1. 验证行动者、目标、授权和场景合法性。
2. 用 `CharacterProfile`、当前状态和 `ModuleStore` 的静态交互规则解析技能、基础值和场景难度。
3. 验证语境理由只使用已知线索、装备、当前条件和合法模组规则。
4. 对建议修正进行 actor-specific clamp：用户为 -10 到 +10，角色为 -5 到 +5。
5. 所有验证通过后，由 RuleEngine 在当前 `game_id` 范围内生成唯一、单调递增的 `check_id`。
6. 掷 d100 并计算唯一结果，从 `effects_by_outcome` 冻结 `allowed_effect_ids`，创建并保存不可变 `CheckResult`；创建、掷骰和保存必须作为一次可信代码操作完成。

`CheckLedger` 是 GameEngine 内部的逻辑记录边界，MVP 物理上保存为独立的 `var/check_records.json`（或等价的独立持久化集合）。它保存本局全部已创建的 `CheckResult`，并在游戏生命周期内保留。StateCommitter 通过 `check_id` 从该权威记录查询效果来源，不从 Agent 请求或最终叙事中重建检定。schema 非法、授权失败、目标或静态规则不匹配、预算耗尽时，只保留工具拒绝记录，不生成 `check_id` 或 `CheckResult`。

同一 `game_id` 内的 `check_id` 不得重用；同一 `CheckResult` 不得重掷或被第二个结果覆盖。`target=null` 的开放检定会得到空的 `allowed_effect_ids`，只能产生当前回合叙事。GameMasterAgent 可以解释结果，但不能改变 `roll`、`target`、`outcome` 或授权效果列表。

### 提交状态效果

`apply_effect` 一次只申请一个固定效果，参数为：

- 新申请使用当前会话确认的 `expected_state_version`；响应丢失后的精确幂等重试可以重复原参数。
- `source_type=check` 和对应 `check_id`，或 `source_type=module_event` 和对应静态 `event_rule_id`。
- 一个 `effect_id`。
- 供轨迹阅读的 `reason`。

GM 不能提交 `path`、`operation`、`value` 或批量 `effects[]`。`tool_call_id` 只能关联工具轨迹，不能作为效果来源。

`StateCommitter` 检查来源所属游戏、回合和场景；检定来源必须由 `CheckResult.allowed_effect_ids` 授权，事件来源必须由静态 `event_rule` 绑定。随后它从 `effect_definitions` 读取 operations，在 GameState 副本中完整预演路径、操作、数值、场景约束和版本。全部成功后才原子写入一个 GameState 文件。

`CommitResult.status` 的处理规则：

- `applied`：首次提交成功；使用新的 `commit_id` 和 `state_version`。
- `already_applied`：幂等重试；沿用原 `commit_id`，不得重复叙述第二次变化。
- `no_state_change`：申请合法但当前没有可写变化；不生成提交、不递增版本、不消费来源，可以叙述行动延缓了威胁等可感知反馈，但不能声称数值改变。
- `rejected`：不产生正式变化；GM 可以根据 `error_code` 修正参数或保守结束，不能把被拒绝效果写进叙事。

同一来源至多选择一个效果。一次效果可以包含多条内部 operation，但对外不是批量提交，也不会出现部分写入。ToolSession 不在分发前拒绝旧版本，以便相同请求抵达 StateCommitter 的幂等检查；每次 `apply_effect` 返回后都会重新读取 GameState，因此新的后续申请必须使用刷新后的 `current_state_version`。

### 更新记忆

> 后续 MVP 切片：当前 `StateCommitter` 只写 GameState，本节接口尚未实现。

`update_memory` 可以请求：

- 追加公开事件摘要。
- 记录已公开的承诺、关系事件和未解决问题。
- 根据本轮角色工具调用，把角色私有变化路由到对应角色。

GameMasterAgent 不能提供“写入另一角色私有记忆”的自由文本。私有更新由 ToolExecutor 根据角色工具调用和可见性规则生成或验证。

## 阶段 3：最终结果

模型结束循环时只返回 `GameMasterDraft(strategy, narration, suggested_actions)`。GameEngine 验证三项结构；模型不能提交 `turn_id`、角色结果、检定、状态提交、结局或轨迹引用。

GameEngine 随后从可信来源组装 `GameMasterTurnResult`：

- Draft 提供策略、叙事和建议行动。
- `character_turns` 当前为空；角色工具实现后只能收集可信工具结果。
- `checks` 按执行顺序收集成功的 `CheckResult`。
- `committed_effects` 只收集 `applied`、`already_applied`，并按 `commit_id` 去重。
- `ending_id` 来自 ToolSession 最终 GameState 快照；不再维护 `is_ending`。

调用层收到 `GameTurnOutcome(result, tool_trace, degraded, failure_code)`。完整工具轨迹直接来自 ToolSession，不要求模型回传 `tool_trace_ids`。GameEngine 不在回合末再次读取 GameState，也不写 Memory 或 EventLog。

后续角色/Memory 切片中，某条已采用台词对应 `used_pending_echo=true` 时，才由专门的记忆写入边界清空该角色的 `pending_echo`；当前 StateCommitter 不承担此职责。

## 用户主导权

- 用户角色的行动只能来自用户输入。
- 角色可以给建议、表达反对、提出自己的行动或请求分工。
- 涉及路线、重大代价、关键资源和结局的选择必须留给用户。
- 用户说“继续”“你们先说”时，只让出有限的发言或已说明的行动机会，不等于把用户角色交给系统控制。
- GameMasterAgent 可以制造模组允许的压力和后果，不能替用户决定其态度、台词或内心感受。

## 信息边界

- 公开场景、公开线索和已披露事实可以进入所有角色工具上下文。
- 某角色的隐藏动机、未披露恐惧和私有关系变化只进入该角色工具上下文。
- GameMasterAgent 只知道“角色工具已根据私有上下文生成结果”，不读取私有上下文本身。
- 角色台词实际披露某项秘密后，`update_memory` 才能把该事实转入公开记忆。
- 调试日志必须按角色过滤私有输入，不能用一个聚合视图泄露全部角色秘密。

## 失败降级

### 角色工具失败

- 不重试超过一次。
- 丢弃非法结果，不生成空白角色占位台词。
- GameMasterAgent 使用其他已有信息继续；没有必要时不补调另一角色。

### 检定工具失败

- 参数非法时不掷骰，返回可修正错误。
- 已经成功生成 `CheckResult` 后发生下游错误，不得重掷。
- 无法修复时，GameMasterAgent 说明当前无法完成该行动裁定，并保留状态。

### 状态提交失败

- 非法效果返回单个 `CommitResult(status="rejected")`。
- 状态版本冲突时不覆盖新状态；ToolSession 刷新快照后，GM 可以在剩余预算内用新版本修正一次，或保守终止本轮写入。
- 最终叙事不得描述被拒绝的变化。

### GameMasterAgent 失败

- `model_failure`、`invalid_model_step`、`iteration_limit_exceeded` 或 `invalid_draft` 使 GameEngine 返回确定性降级 Draft。
- 降级结果使用 `strategy=degraded`、固定叙事和空建议行动，并保留此前已经产生的检定、提交、角色结果、结局和完整轨迹。
- 不执行额外修复模型调用，也不回滚已经确认的机械结果。
- GameState、Memory、静态数据或运行依赖非法时直接抛出对应领域错误，不伪装成一次成功的降级回合。

## 可观察性

Demo 和调试日志至少展示：

- `turn_id`、用户输入摘要、GM 选择的回合策略和固定预算。
- 工具名、调用顺序、规范化参数、是否分发和成功或拒绝原因。
- 角色工具仅显示公开输入摘要，不显示私有记忆原文。
- `CheckLedger` 中 `CheckResult` 的 `check_id`、技能、修正、目标值、骰点和成功等级。
- 每次状态申请的 `effect_id`、来源类型、`CommitResult.status`、`commit_id` 和版本变化。
- 最终结果是否由正常流程或降级模板产生。

## 验收场景

### 直接询问角色

用户点名维斯佩拉询问门外危险：

- GameMasterAgent 选择 `fast` 策略。
- GameMasterAgent 直接读取 GameEngine 提供的场景投影；角色工具接入后可调用一次维斯佩拉。
- 不需要检定或状态写入。
- 当前切片的 `character_turns` 为空；角色工具接入后，最终结果才可包含可信的维斯佩拉台词。

### 用户执行行动

用户尝试撬开石牢门锁：

- GameMasterAgent 选择 `fast` 策略并识别用户已经声明行动。
- GameMasterAgent 从场景投影读取门锁事实，角色工具接入后可调用一名相关角色提出建议。
- `request_check` 返回唯一 d100 结果。
- `check_id` 由可信代码创建，且可从 `CheckLedger` 查询；Agent 不能提供或修改它。
- 只有与结果匹配的门锁、噪声或压力效果可以提交。
- 最终叙事必须忠实描述已接受结果。

### 未授权的角色提议

萨芙拉建议独自搜索危险区域：

- 提议可以作为台词展示。
- 没有本轮用户明确授权时，不执行检定和状态提交。
- 最终结果把选择交还给用户。

### 紧迫逃亡

“拉提的注视”达到 5/6 后，用户准备启动门楼机关：

- GameMasterAgent 选择 `urgent` 策略，只描述迫近危险并询问立即行动。
- 不默认整理完整菜单；用户自由声明行动。
- 不可逆代价仍需明确确认，普通检定和工具调用仍受固定预算约束。
- 现实等待时间不会自动推进状态。

### 角色生成失败

阿兰妮丝的工具调用超时：

- 记录失败但不阻塞回合。
- 不生成“阿兰妮丝保持沉默”之类的伪输出。
- GameMasterAgent 使用已有场景和其他合法结果完成回应。

## 回合验收标准

- 运行时不存在 ADR-002 已取代的外围路由层。
- 每轮只创建一个只读 `TurnContext`；GameMasterModel 只读取 `ModelRequest`，看不到或修改内部预算。
- 外层回合只由合法用户原文启动，不保存 `RoundTrigger` 或 `trigger_type`。
- 普通回合角色调用数通常为 0-1，任一回合始终不超过 2。
- 角色私有上下文不会进入 GameMasterAgent 输入或其他角色调用。
- 每个正式效果都能追溯到合法来源。
- 每次 `apply_effect` 只有一个 `effect_id`，来源只能是 `check_id` 或静态 `event_rule_id`。
- `tool_call_id` 只用于工具轨迹，不能授权状态效果。
- 每个检定结果只生成一次且不可被 Agent 改写。
- 非法、未授权或预算耗尽的检定请求不会创建 `check_id` 或 `CheckResult`。
- 每个已接受 `check_id` 都能从 `CheckLedger` 查询，并可供 StateCommitter 验证效果来源。
- 当前 StateCommitter 只原子写入 GameState；Memory、EventLog 和关系阶段不会被隐式一起修改。
- 模型只返回 `GameMasterDraft`，GameEngine 从可信轨迹和最终快照组装 `GameMasterTurnResult` 与 `GameTurnOutcome`。
- 用户未授权的角色行动不会进入正式状态。
- 任一工具失败或循环超限时，本轮都能以保守方式终止。
