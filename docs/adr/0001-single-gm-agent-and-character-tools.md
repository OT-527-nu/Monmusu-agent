# ADR-001：采用单 GameMasterAgent + Character Tools

- 状态：Accepted
- 日期：2026-07-16
- 决策范围：Monmusu Agent MVP
- 后续局部取代：[ADR-002](0002-lightweight-player-facing-mvp-loop.md)、[ADR-003](0003-single-effect-game-state-commit.md)、[ADR-004](0004-user-input-gameengine-turn-contract.md)
- 相关文档：`../README.md`、`../architecture.md`、`../mvp_design.md`、`../turn_loop.md`

## 背景

项目最初被设计为一个 multi-agent 文字跑团游戏：

- 1 个 Host Agent 负责主持、叙事和裁定。
- 3 个 Player Agent 分别扮演 AI 队友。
- GameEngine 负责调用和协调这些 Agent。
- 每个 Player Agent 根据角色卡、公开记忆和私有记忆生成台词与行动。

随着回合协议继续细化，系统引入了 `TurnRouter`、
`ParticipationPlanner`、`AuthorityValidator`、`ResolutionPlanner`
等组件，用于解决多名 Player Agent 的发言调度、行动冲突、
权限验证和延迟问题。

进一步分析后发现，Player Agent 在 MVP 中实际上只执行一次受约束的
LLM 生成：

```text
角色卡
+ 私有记忆
+ 公开场景
+ 关系状态
→ 角色台词和行动提议
```

Player Agent 不会独立选择工具、循环观察结果、规划长期任务或主动修改状态。
因此，它们更接近具有独立上下文的角色生成器，而不是完整 Agent。

继续维持 multi-agent 抽象会引入以下成本：

- 需要额外设计 Agent 调度和通信协议。
- 每轮产生更多模型调用、延迟和 token 消耗。
- 需要处理多个 Agent 的行动冲突、抢话和内容复述。
- “Player Agent”命名与其实际能力不一致。
- 工程复杂度增加，但没有带来等量的用户价值。

## 决策

MVP 改为：

> 一个受 GameEngine 约束的 GameMasterAgent，通过工具调用完成角色扮演、规则检定、状态查询和游戏推进。

只有 `GameMasterAgent` 拥有有限的 agent loop。

原 Player Agent 改为由 LLM-backed Character Tool 生成的 AI 队友角色，
不再拥有独立的 agent loop，也不再称为 Agent。

简化后的架构：

```text
User
  ↓
GameEngine
  ↓
GameMasterAgent
  ├─ generate_character_turn
  ├─ request_check
  ├─ query_scene
  ├─ query_clue
  ├─ apply_validated_effects
  └─ update_memory
```

## 规范性术语

本 ADR 接受后，现行文档统一使用以下名词：

- **Monmusu Agent**：项目名称。名称中的 Agent 不表示系统采用 multi-agent 架构。
- **GameMasterAgent**：系统中唯一拥有有限工具调用循环的 Agent；中文称“主持人”。
- **Character**：AI 队友这一领域对象；面向玩家的中文统一称“AI 队友角色”或“队友角色”。
- **CharacterProfile**：角色卡及其静态配置，不包含运行时状态或私有记忆。
- **generate_character_turn**：内部调用 LLM 的角色生成工具；它不是 Agent，也不能继续调用其他工具。
- **CharacterTurnProposal**：角色生成工具返回的台词与行动提议，不是正式行动或状态事实。
- **GameMasterTurnResult**：GameMasterAgent 完成工具循环后的最终叙事结果，不直接携带可绕过验证的状态写入。
- **GameEngine**：外层回合控制器，负责预算、工具权限、验证、终止和持久化编排。
- **StateCommitter**：正式 GameState、Memory 与 EventLog 的唯一写入边界；它可以是 GameEngine 内部职责，不要求实现为独立类。
- **character_id / character_name**：角色标识字段，替代 `agent_id` / `agent_name`。
- **private_memory_by_character**：按角色隔离的私有记忆，替代 `private_memory_by_agent`。

`CharacterPolicy` 不作为 MVP 的通用替代名。只有未来确实出现可互换的角色生成策略对象时，才使用该名称。

### GameMasterAgent

负责：

- 理解用户输入。
- 决定本轮需要调用哪些工具。
- 决定是否需要某个角色发言或提出行动。
- 根据工具返回的检定结果和角色回应组织最终叙事。
- 在模组允许范围内推进剧情。

GameMasterAgent 不能直接写入正式状态，也不能修改代码已经确定的检定结果。

### generate_character_turn

这是一个内部调用 LLM 的工具。

输入示例：

```json
{
  "character_id": "vespera",
  "participation_role": "primary_responder",
  "purpose": "回应用户关于门外危险的询问"
}
```

工具内部自行读取：

- 对应角色卡。
- 角色私有记忆。
- 与用户及队友的关系状态。
- 当前公开场景与线索。
- 本轮允许的参与目的。

工具返回：

```json
{
  "character_id": "vespera",
  "speech": "……",
  "proposed_action": "……",
  "intent": "……",
  "relationship_signal": "……"
}
```

角色私有记忆不会直接暴露给 GameMasterAgent 或其他角色。上下文隔离由工具执行器负责，
不依赖独立 Agent 实现。

### 确定性组件

以下职责继续由普通代码完成：

- 回合和模型调用预算。
- d100 掷骰及成功等级计算。
- 工具参数和权限验证。
- GameState 正式写入。
- HP、压力、危机时钟等数值变化。
- 线索和地点的合法性验证。
- Agent loop 最大迭代次数和终止条件。

## 保留的设计

本次决策不改变以下内容：

- 《逃离塔纳里昂》的模组设计。
- 塔纳里昂城市设定。
- 三名 AI 队友角色的角色卡、性格和关系发展。
- 公开记忆与角色私有记忆的区分。
- d100 检定规则。
- 线索、场景、危机时钟和结局设计。
- LLM 输出需要经过 schema 和代码验证的原则。
- 保留 FactSet + TurnMode 作为 GameMasterAgent 外围的轻量路由与工具权限机制，但不保留面向多个独立 Agent 的复杂调度协议。

## 被替代的设计

以下设计不再作为 MVP 实现基础：

- 每个角色拥有独立 agent loop。
- 每轮调用全部 Player Agent。
- Player Agent 之间直接通信。
- 完整的 multi-agent handoff。
- 为多个 Agent 设计的复杂 ParticipationPlanner。
- Player Agent 主动递归开启新回合。
- 把角色生成结果直接视为正式行动。

`archive/turn_protocol_v2.md` 将作为设计探索记录归档，其中关于用户主导权、
信息边界、状态权限和失败降级的原则仍然保留。

## 考虑过的替代方案

### 方案 A：保留完整 multi-agent 架构

优点：

- 概念上接近真人多人跑团。
- 每个角色可以拥有更强的自治能力。
- 便于展示 Agent 之间的协作。

未采用原因：

- MVP 中的角色没有真正需要独立 agent loop 的任务。
- 调度、冲突和延迟成本过高。
- 容易形成“为了 multi-agent 而 multi-agent”的架构。

该方案可以在未来需要角色长期自主规划时重新评估。

### 方案 B：由一次 LLM 调用同时扮演所有角色

优点：

- 调用次数少。
- 实现最简单。

未采用原因：

- 角色私有信息容易泄露。
- 人格和说话风格更容易互相污染。
- 难以为每个角色维护独立记忆和关系状态。

因此仍然为不同角色执行独立的生成调用，但这些调用被建模为工具，
而不是 Agent。

### 方案 C：完全确定性的工作流

优点：

- 容易测试和复现。
- 成本和延迟低。

未采用原因：

- 难以处理用户开放式自然语言输入。
- 无法充分表现动态角色扮演和即兴叙事。
- 无法根据当前情况动态选择角色和能力。

因此采用“确定性 GameEngine + 有限 GameMaster agent loop”的混合架构。

## 后果

### 正面后果

- 减少模型调用和响应延迟。
- 删除复杂的多 Agent 调度协议。
- 保留角色上下文和私有记忆隔离。
- 架构名称与实际能力一致。
- 更容易在 8 周内完成可运行 MVP。
- 更容易解释为什么某些部分使用 Agent、某些部分使用普通代码。

### 负面后果

- AI 队友不具备真正的长期自主规划能力。
- GameMasterAgent 成为主要协调点，需要限制其工具权限和循环预算。
- LLM-backed tool 内部再次调用 LLM，会增加调用链追踪难度。
- 未来如果角色需要独立使用工具或跨回合自主执行任务，可能需要重新引入 Agent。

## 实施计划

1. 将面向运行时的 `PlayerAgent` 统一改为 `Character`，角色卡数据使用 `CharacterProfile`。
2. 将 `PlayerAgentAction` 改为 `CharacterTurnProposal`，将 `HostResolution` 改为 `GameMasterTurnResult`。
3. 将 `data/agents/player_agents.json` 迁移为 `data/characters/characters.json`。
4. 将 `agent_id`、`agent_name` 和 `private_memory_by_agent` 分别改为 `character_id`、`character_name` 和 `private_memory_by_character`。
5. 重写 `architecture.md` 和 `mvp_design.md`，建立现行文档入口和权威顺序。
6. 用简化的 `turn_loop.md` 替代并归档 `turn_protocol_v2.md`。
7. 使 schema、prompt、模组文档和角色卡采用同一套术语。
8. 实现 `GameMasterAgent` 的有限工具调用循环。
9. 实现 `generate_character_turn`，验证角色上下文隔离。
10. 保留并扩展现有 RuleEngine、存储层和测试。

## 验收标准

该决策实施完成后，应满足：

- 除 ADR 的历史说明和归档文档外，现行文档不再把 AI 队友角色称为 Agent。
- 系统中只有一个具备工具循环的 Agent，即 `GameMasterAgent`。
- 角色生成调用不能直接修改 GameState。
- 每个角色只能读取自己的私有记忆。
- GameMasterAgent 可以按需调用 0-2 个角色，而非每轮调用全部角色。
- d100 结果和正式状态仍由确定性代码控制。
- 能跑通“用户输入 → 角色回应 → 检定 → 主持人叙事 → 状态提交”的完整回合。
- Demo 中可以展示 GameMasterAgent 的工具调用轨迹。
