# GameEngine 设计同步说明

> 状态：已冻结、完成首轮 TDD 实现，并于 2026-07-26 同步至正式文档
> 记录日期：2026-07-25
> 整理者：Codex；本文记录的决定均已与用户逐项确认
> 范围：保留 GameEngine 契约形成过程和同步范围，正式规范以 ADR-004 与现行架构、回合、Prompt、schema 文档为准

## 文档定位

本文防止实现继续沿用旧 `RoundTrigger`、旧 `TurnContext` 和“模型直接生成完整 `GameMasterTurnResult`”设计。正式取代关系已经由 [ADR-004](adr/0004-user-input-gameengine-turn-contract.md) 记录；本文现在只作为实现与文档同步记录，不再优先于正式契约。

## 已冻结契约

### 外层回合

```python
GameEngine.run_turn(input_text: str) -> GameTurnOutcome
```

- 只允许一次用户原文同步启动一轮，不接收触发类型、按钮模式或预分类意图。
- 空白或超过 4000 字符的输入在创建回合前抛出 `GameInputError`。
- GameState、Memory 和所需静态数据在分配 `turn_id` 前读取并校验；已有 `ending_id` 时抛出 `GameEndedError`。
- `turn_id` 在本轮稳定、跨轮唯一，但不要求递增；删除 `GameState.turn_number`。
- 同一 `game_id` 只允许一个活动回合，由调用层串行化。MVP 不实现排队、锁或自动重试。

采用有限依赖注入：

```python
GameEngine(
    paths,
    agent=game_master_agent,
    tool_executor=tool_executor,
    turn_id_factory=turn_id_factory,
)
```

`initialize()` 不需要运行依赖；未配置依赖时调用 `run_turn()`，抛出 `GameEngineConfigurationError`。不引入依赖注入框架。

### TurnContext 与授权

`TurnContext` 是可信代码内部的只读机械上下文，不直接交给模型：

```text
TurnContext
├─ turn_id
├─ input_text
├─ initial_game_state
├─ max_tool_steps
└─ tool_limits
```

删除 `trigger_type`、`allow_state_changes`、`standing_assignment_ids` 和 `module_reaction_ids`。MVP 只有用户输入回合；工具权限仍由动态目录、RuleEngine 与 StateCommitter 控制。

角色机械行动授权只保留：

- `user_declared`：用户声明自己的行动。
- `user_delegated`：用户在本轮明确授权某名角色行动。

无状态角色反应可以叙事；需要检定或正式状态效果的角色行动必须有本轮用户授权。

### 模型输入

GameEngine 在回合开始时确定性生成临时只读投影：

```text
GameMasterStateView
├─ state_version
├─ current_scene
├─ user_public_state
├─ character_public_states
├─ clues_found
├─ accessible_locations
├─ threat_clock
└─ gm_visible_flags
```

投影排除内部 metadata、私有记忆、关系阶段和隐藏 flags，不持久化，也不是第二状态权威。场景上下文来自模组的 `SceneDefinition(public_facts, interactions, boundaries, discovery_opportunities)`；已发现线索可提供 `ClueDefinition(title, public_text)`，未发现线索只提供发现机会。

GameMasterAgent 每一步组装：

```python
ModelRequest(
    input_text,
    state_view,
    scene_context,
    public_memory,
    available_tools,
    tool_interactions,
)
```

- GameEngine 拥有固定输入、状态/场景投影与公开记忆。
- ToolSession 提供每一步动态工具目录和可信结果。
- Adapter 只负责把 ModelRequest 序列化为 LLM 请求，并把响应解析为 ModelStep。
- 模型看不到 `turn_id`、完整 TurnContext、完整 GameState、原始预算或私有记忆。
- 初始投影在本轮不变；已提交变化通过 ToolInteraction 的可信 `context_delta` 反馈。

MVP 不实现 `query_scene` 或 `query_clue`。`apply_effect.context_delta` 只表达 `revealed_clues` 和 `entered_scene`；它由 ToolExecutor 根据可信 changes 与模组数据派生，不获得状态权威。`rejected` 与 `no_state_change` 不产生新上下文。

### 模型输出与最终结果

模型只返回候选内容：

```text
GameMasterDraft(strategy, narration, suggested_actions)
```

模型策略只允许 `fast`、`dramatic`、`urgent`。GameEngine 只验证结构，不增加语义审核 LLM；非法 Draft 使用 `invalid_draft` 降级，本切片不执行格式修复调用。

GameEngine 从可信来源组装：

```text
GameMasterTurnResult
├─ turn_id
├─ strategy
├─ narration
├─ character_turns
├─ checks
├─ committed_effects
├─ suggested_actions
└─ ending_id

GameTurnOutcome
├─ result: GameMasterTurnResult
├─ tool_trace
├─ degraded
└─ failure_code
```

- Draft 只提供 `strategy`、`narration` 和 `suggested_actions`。
- 当前角色工具未实现，`character_turns` 为空；以后只能收集可信角色工具结果。
- `checks` 收集本轮成功的 CheckResult，按执行顺序排列。
- `committed_effects` 只收集 `applied`、`already_applied`，并按 `commit_id` 去重；`no_state_change`、`rejected` 只保留在轨迹。
- `ending_id` 来自 ToolSession 的最终 GameState 快照。
- 删除旧 `is_ending`、`tool_trace_ids`；`visible_state_changes` 改为 `committed_effects`，`available_next_actions` 改为 `suggested_actions`。

## 执行顺序

```text
validate input
  -> load and validate GameState / Memory / required static data
  -> reject an already-ended game
  -> allocate turn_id
  -> build fixed projections and TurnContext
  -> create the turn's only ToolSession
  -> run the bounded GameMasterAgent loop
  -> validate GameMasterDraft or create a degraded draft
  -> derive trusted results from ToolSession.trace
  -> read ending_id from ToolSession's final state snapshot
  -> return GameTurnOutcome
```

GameEngine 不在回合末再次读取 GameState。回合开始时的校验、StateCommitter 写入校验和 ToolSession 在每次 `apply_effect` 后的刷新已经覆盖单写者流程。ToolSession 需向 GameEngine 暴露最终只读状态快照，而不只提供版本号。

## Memory 边界

- GameEngine 在分配 `turn_id` 前读取 Memory，但本切片不写 Memory。
- 文件缺失、损坏、`game_id` 不匹配或 `public_memory` 格式非法时抛出 `GameMemoryError`；合法的空列表不是错误。
- 用户元语言和 GM 自由文本不会自动进入公开记忆。
- 删除 `memory.json.state_version`。未来如需乐观锁，Memory 使用独立 `memory_version`，不与 GameState 版本比较。

## 失败边界

已知 Agent/Draft 失败可以返回降级结果，例如 `model_failure`、`invalid_model_step`、`iteration_limit_exceeded` 和 `invalid_draft`：

- 不回滚已有 CheckResult 或已提交效果，也不额外调用 LLM。
- 使用 `strategy="degraded"`、空 `suggested_actions` 和固定系统提示。
- 保留可信角色结果、检定、提交、结局与完整工具轨迹。
- 系统提示不写入公开记忆，也不提供给角色。

GameState/Memory 损坏、持久化结果无法确认、静态数据非法、缺少运行依赖及意外程序异常继续抛出。正常 `ToolError`、`CommitResult(status="rejected")` 和版本冲突属于工具协议结果。GameEngine 不用宽泛的 `except Exception` 包装整个回合。

## 本轮实现落点

| 位置 | 已完成迁移 |
| --- | --- |
| `engine.py` | 已增加 `run_turn()`、只读投影、可信结果组装、输入/数据预检和受控降级；初始化已移除两个旧版本字段 |
| `tools.py` | 已缩小 TurnContext/授权类型，暴露最终只读状态快照，并从可信提交变化派生 `context_delta` |
| `agent.py` | ModelRequest 已改用安全投影，模型只返回 GameMasterDraft；模型可见工具结果不会泄漏 `turn_id` |
| `escape_thalarion.json` | 已增加石牢的最小场景、线索和 GM 可见 flag 定义 |
| `test_engine.py` | 已覆盖无工具回合、真实检定/提交、幂等重试、隐私边界、预检失败和 Agent 降级 |

## 正式文档同步结果

2026-07-26 已完成：

1. `schemas.md`：同步新 TurnContext、模型投影、Draft、最终结果与 Outcome，并删除两个旧版本字段。
2. `turn_loop.md`、`architecture.md`：同步单用户输入流程、可信结果所有权和失败边界。
3. `prompts.md`：同步模型只读 ModelRequest、只输出 Draft，以及非法 Draft 直接降级。
4. `mvp_design.md`：同步 Memory 只读切片、角色授权收缩和后续交付计划。
5. `ADR-004`：在不改写 ADR-001、ADR-002 历史正文的前提下，记录 `RoundTrigger`、旧 TurnContext 和结果所有权的局部取代关系。

## TDD 验收重点

- 输入、已结束游戏、GameState/Memory 非法都在模型和工具调用前失败。
- 正常无工具回合能从 Draft 组装可信结果。
- 检定、提交、拒绝、`no_state_change` 与幂等重试按轨迹正确归类。
- Agent 在产生机械结果后失败时不回滚，并返回确定性降级结果。
- 存储和意外异常继续抛出；回合末不写 Memory、不额外重读 GameState。
- 模型看不到私有/隐藏状态、完整 GameState、turn ID 或原始预算。

完成以上切片后，再进入真实 LLM Adapter、CharacterTurnGenerator、Memory 写入和 UI 流式输出。
