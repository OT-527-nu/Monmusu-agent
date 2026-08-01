# JSON 数据契约

## 文档状态

本文档定义 Monmusu Agent MVP 的规范性数据契约。字段名称与 [ADR-001](adr/0001-single-gm-agent-and-character-tools.md)、[ADR-002](adr/0002-lightweight-player-facing-mvp-loop.md)、[ADR-003](adr/0003-single-effect-game-state-commit.md) 和 [ADR-004](adr/0004-user-input-gameengine-turn-contract.md) 一致，调用顺序和权限规则以 [回合循环](turn_loop.md) 为准。

`TurnContext`、`GameMasterStateView`、`ModelRequest`、`GameMasterDraft`、`GameMasterTurnResult`、`GameTurnOutcome` 以及两个现行工具契约对应当前源码。明确标记为“计划中”的角色生成和 Memory 写入契约尚未接入运行时，不得被当作当前可调用工具。

## 通用规则

- JSON 字段统一使用 `snake_case`。
- 稳定 ID 使用小写英文、数字和下划线。
- 所有 Agent 工具参数、工具结果和最终结果都必须是单个 JSON 对象。
- 未在 schema 中声明的字段默认拒绝。
- LLM 生成的内容都是候选内容；只有状态提交结果表示正式写入。
- 私有记忆原文不得出现在 GameMasterAgent 工具输入、工具返回或最终结果中。
- 时间戳使用 UTC ISO 8601；回合内因果关系优先使用稳定 ID。
- 本文示例用于说明字段，不替代运行时 JSON Schema 文件。

## 标识符

| 字段 | 含义 | 示例 |
| --- | --- | --- |
| `turn_id` | 单个外层回合 | `turn_0007` |
| `tool_call_id` | 一次工具调用 | `tool_0007_03` |
| `character_id` | AI 队友角色 | `vespera` |
| `check_id` | 一次不可重掷的检定，在单局游戏内唯一 | `check_game_0001_0001` |
| `commit_id` | 一次状态提交 | `commit_0007_01` |
| `event_rule_id` | 模组中的静态事件规则 | `delay_rathi_attention` |
| `state_version` | GameState 乐观锁版本 | `12` |

## TurnContext

`TurnContext` 由 `GameEngine` 在回合开始时创建，本轮只读。它是可信代码内部的机械上下文，不直接提供给模型，也不解释用户意图。

```json
{
  "type": "object",
  "required": [
    "turn_id",
    "input_text",
    "initial_game_state",
    "max_tool_steps",
    "tool_limits"
  ],
  "properties": {
    "turn_id": {
      "type": "string",
      "pattern": "^turn_[a-z0-9_]+$"
    },
    "input_text": {
      "type": "string",
      "minLength": 1,
      "maxLength": 4000
    },
    "initial_game_state": {
      "type": "object"
    },
    "max_tool_steps": {
      "type": "integer",
      "minimum": 0,
      "maximum": 8
    },
    "tool_limits": {
      "type": "object",
      "properties": {
        "request_check": { "type": "integer", "minimum": 0, "maximum": 2 },
        "apply_effect": { "type": "integer", "minimum": 0, "maximum": 2 }
      },
      "additionalProperties": false
    }
  },
  "additionalProperties": false
}
```

语义验证：

- MVP 只有 `GameEngine.run_turn(input_text)` 这一种外层回合入口，不保存 `trigger_type`。用户表达“继续”或等待时仍是普通原文。
- `turn_id` 在本轮稳定且跨轮唯一，但不要求递增，也不写入 GameState。
- `tool_limits` 是可信代码的预算输入，不是 GM 工具目录。GM 此刻可用的工具只能来自 `ToolSession.available_tool_definitions()`。
- 当前实现只支持 `request_check` 和 `apply_effect`；总步骤或某工具配额耗尽时，动态目录移除相应工具。
- 模型只读取下面定义的安全投影和 `ModelRequest`，看不到完整 `TurnContext`、`turn_id` 或原始预算。

## GameMasterStateView

`GameMasterStateView` 是 GameEngine 从回合开始时的 GameState 确定性生成的固定公开投影。它不持久化，也不是第二状态权威。

```json
{
  "type": "object",
  "required": [
    "state_version",
    "current_scene",
    "user_public_state",
    "character_public_states",
    "clues_found",
    "accessible_locations",
    "threat_clock",
    "gm_visible_flags"
  ],
  "properties": {
    "state_version": { "type": "integer", "minimum": 0 },
    "current_scene": { "type": "string", "minLength": 1 },
    "user_public_state": { "type": "object" },
    "character_public_states": { "type": "object" },
    "clues_found": { "type": "array", "items": { "type": "string" } },
    "accessible_locations": { "type": "array", "items": { "type": "string" } },
    "threat_clock": { "type": "object" },
    "gm_visible_flags": { "type": "object" }
  },
  "additionalProperties": false
}
```

用户和角色公开状态只包含当前叙事与规则需要的白名单字段。关系阶段、私有记忆、`commit_metadata` 和未列入模组 `gm_visible_flag_ids` 的 flags 不得进入投影。

## ModelRequest

`GameMasterAgent` 每一步都把固定投影和 ToolSession 当前动态信息组装成新的 `ModelRequest`：

```json
{
  "type": "object",
  "required": [
    "input_text",
    "state_view",
    "scene_context",
    "public_memory",
    "available_tools",
    "tool_interactions"
  ],
  "properties": {
    "input_text": { "type": "string", "minLength": 1, "maxLength": 4000 },
    "state_view": { "type": "object" },
    "scene_context": { "type": "object" },
    "public_memory": { "type": "array" },
    "available_tools": { "type": "array" },
    "tool_interactions": { "type": "array" }
  },
  "additionalProperties": false
}
```

- `scene_context` 包含当前场景的公开事实、交互、边界、发现机会和已经发现的线索公开文本；MVP 不另设 `query_scene` 或 `query_clue`。
- `public_memory` 是本轮开始时从 Memory 读取的只读快照；当前 GameEngine 不写回 Memory。
- `available_tools` 每一步刷新；`tool_interactions` 只含本轮此前调用和给模型裁剪后的可信结果。
- 模型看不到 `turn_id`、完整 GameState、原始预算、角色私有记忆或隐藏 flags。

## GameMasterDraft

模型结束工具循环时只能提供以下候选内容：

```json
{
  "type": "object",
  "required": ["strategy", "narration", "suggested_actions"],
  "properties": {
    "strategy": {
      "type": "string",
      "enum": ["fast", "dramatic", "urgent"]
    },
    "narration": { "type": "string", "minLength": 1 },
    "suggested_actions": {
      "type": "array",
      "items": { "type": "string", "minLength": 1 }
    }
  },
  "additionalProperties": false
}
```

Draft 不包含 `turn_id`、角色工具结果、检定、提交、结局或工具轨迹。上述字段由 GameEngine 从可信来源组装。

## CharacterProfile

`CharacterProfile` 是静态角色卡配置。它不是 Agent 配置，也不包含运行时记忆。

```json
{
  "type": "object",
  "required": [
    "character_id",
    "character_name",
    "species",
    "identity",
    "personality",
    "speech_style",
    "skills",
    "weaknesses",
    "fears",
    "hidden_motive",
    "relationship_stages",
    "boundaries"
  ],
  "properties": {
    "character_id": {
      "type": "string",
      "pattern": "^[a-z0-9_]+$"
    },
    "character_name": {
      "type": "string",
      "minLength": 1
    },
    "species": {
      "type": "string"
    },
    "identity": {
      "type": "string"
    },
    "personality": {
      "type": "array",
      "items": { "type": "string" },
      "minItems": 1
    },
    "speech_style": {
      "type": "array",
      "items": { "type": "string" },
      "minItems": 1
    },
    "skills": {
      "type": "object",
      "additionalProperties": {
        "type": "integer",
        "minimum": 1,
        "maximum": 99
      }
    },
    "weaknesses": {
      "type": "array",
      "items": { "type": "string" }
    },
    "fears": {
      "type": "array",
      "items": { "type": "string" }
    },
    "hidden_motive": {
      "type": "string"
    },
    "relationship_stages": {
      "type": "array",
      "items": { "type": "string" },
      "minItems": 1
    },
    "boundaries": {
      "type": "array",
      "items": { "type": "string" },
      "minItems": 1
    }
  },
  "additionalProperties": false
}
```

角色卡正文以 [AI 队友角色卡](character_cards.md) 为准，运行配置可以把正文转换为该结构或等价结构。

## RuleEngine 静态规则数据

角色配置和用户角色配置中的 `skills` 保存技能基础值，键为稳定技能标识，值为 1～99 的整数。基础值属于可信静态数据，不能由 `request_check` 提供或覆盖。

模组配置中的 `check_rules` 保存与场景对象绑定的静态检定规则：

```json
{
  "rule_id": "pry_stone_cell_lock",
  "scene_id": "stone_cell",
  "target_id": "stone_cell_lock",
  "allowed_skills": ["improvisation", "mechanics"],
  "difficulty_modifier": -10,
  "effects_by_outcome": {
    "critical_success": ["unlock_stone_cell_lock"],
    "success": ["unlock_stone_cell_lock"],
    "failure": [],
    "fumble": ["raise_threat_from_lock_noise"]
  }
}
```

- `build_check_context` 只把当前 `scene_id` 的规则加入本轮可信上下文。
- `target_id` 是 GM 在 `RequestCheckArgs.target` 中可以引用的稳定标识；非空目标没有对应规则时，RuleEngine 必须拒绝。
- `target=null` 表示没有静态交互对象的开放行动，使用行动者已有技能和难度修正 0。
- `allowed_skills` 限制该目标允许采用的技能；最终基础值仍从行动者静态 `skills` 读取。
- `effects_by_outcome` 可以省略，表示该规则只产生叙事结果；一旦提供，就必须覆盖 `critical_success`、`success`、`failure` 和 `fumble` 四种结果。实际 outcome 的数组在检定时冻结为 `CheckResult.allowed_effect_ids`，空数组表示该结果没有可提交的机械效果。

模组配置中的 `effect_definitions` 保存固定效果的受限操作 DSL。GM 只提交 `effect_id`，不能提交路径、操作或值。当前允许的操作是 `set`、`increment`、`add_unique`、`remove` 和 `ensure_at_least`，具体路径仍由 `StateCommitter` 的白名单校验。

模组配置中的 `event_rules`（若有）把 `event_rule_id`、场景、重复策略、条件和唯一 `effect_id` 绑定在一起。它是第二类状态效果来源，不是一个由 Agent 自由创建的事件账本。

模组配置中的 `modifier_sources` 定义可作为语境修正理由的可信来源：

```json
{
  "source_id": "scene_stone_cell_lock",
  "allowed_reason_tags": ["poor_position", "time_pressure"]
}
```

已经进入 `GameState.clues_found` 的线索可以作为 `relevant_clue` 来源。非零语境修正没有理由、来源不存在或理由标签不受该来源支持时，RuleEngine 必须在创建 `check_id` 和掷骰前拒绝请求。

## generate_character_turn

> 计划中：该工具尚未加入当前 `ToolExecutor` 目录；本节保留后续 MVP 切片的候选契约。

### GenerateCharacterTurnArgs

GameMasterAgent 只能提供角色标识、参与角色、生成模式和本轮目的。角色卡、私有记忆、关系状态和可用的公开前序台词由 ToolExecutor 注入，不能作为自由参数传入。

```json
{
  "type": "object",
  "required": [
    "character_id",
    "participation_role",
    "generation_mode",
    "purpose"
  ],
  "properties": {
    "character_id": {
      "type": "string",
      "pattern": "^[a-z0-9_]+$"
    },
    "participation_role": {
      "type": "string",
      "enum": [
        "primary_responder",
        "supporter",
        "dissenter",
        "reactor"
      ]
    },
    "generation_mode": {
      "type": "string",
      "enum": ["independent_proposal", "public_reaction"]
    },
    "purpose": {
      "type": "string",
      "minLength": 1,
      "maxLength": 240
    }
  },
  "additionalProperties": false
}
```

禁止增加 `private_memory`、`hidden_motive`、`full_prompt` 或其他可绕过上下文隔离的参数。

### CharacterTurnProposal

`CharacterTurnProposal` 描述角色说什么、想做什么以及其公开可用的意图。它不是正式行动。

```json
{
  "type": "object",
  "required": [
    "character_id",
    "speech",
    "proposed_action",
    "intent",
    "target",
    "requires_check",
    "suggested_skill",
    "relationship_signal",
    "action_authority",
    "nonverbal_cue",
    "used_pending_echo"
  ],
  "properties": {
    "character_id": {
      "type": "string",
      "pattern": "^[a-z0-9_]+$"
    },
    "speech": {
      "type": "string",
      "minLength": 1,
      "maxLength": 800
    },
    "proposed_action": {
      "type": ["string", "null"],
      "maxLength": 300
    },
    "intent": {
      "type": "string",
      "minLength": 1,
      "maxLength": 240
    },
    "target": {
      "type": ["string", "null"],
      "maxLength": 120
    },
    "requires_check": {
      "type": "boolean"
    },
    "suggested_skill": {
      "type": ["string", "null"],
      "maxLength": 80
    },
    "relationship_signal": {
      "type": "string",
      "maxLength": 240
    },
    "action_authority": {
      "type": "string",
      "enum": [
        "natural_reaction",
        "authorized_support",
        "consent_required",
        "user_only"
      ]
    },
    "nonverbal_cue": {
      "type": ["string", "null"],
      "maxLength": 240
    },
    "used_pending_echo": {
      "type": "boolean"
    }
  },
  "additionalProperties": false
}
```

示例：

```json
{
  "character_id": "vespera",
  "speech": "门外有东西拖过石面。先别靠门，我来听清它离我们多远。",
  "proposed_action": "贴近门缝辨认走廊里的移动方向",
  "intent": "判断开门是否会立刻暴露队伍",
  "target": "stone_cell_door",
  "requires_check": true,
  "suggested_skill": "listen",
  "relationship_signal": "把人类队友安排在更安全的位置，但仍用职责解释这种保护",
  "action_authority": "consent_required",
  "nonverbal_cue": "伤翼先于话语向玩家一侧展开",
  "used_pending_echo": false
}
```

角色工具返回值不得包含：

- 私有记忆原文。
- 隐藏思维过程或逐步推理。
- 其他角色的动机、感受或行动。
- 检定结果、线索发现或状态更新。

语义验证：

- `generation_mode=independent_proposal` 时，ToolExecutor 不得注入其他角色尚未公开的判断。
- `generation_mode=public_reaction` 时，只能注入本轮已经公开的台词。
- `action_authority=natural_reaction` 时，`proposed_action` 必须为 `null` 或不改变正式状态。
- `action_authority=user_only` 时，该提议只能作为台词或选项展示，不能直接进入检定。
- `used_pending_echo=true` 时，ToolExecutor 必须确认该角色当前确有 `pending_echo`，且本轮允许记忆状态变化。

## request_check

### RequestCheckArgs

```json
{
  "type": "object",
  "required": [
    "actor_id",
    "actor_type",
    "action",
    "target",
    "suggested_skill",
    "suggested_context_modifier",
    "modifier_reasons",
    "authorization",
    "authorization_evidence"
  ],
  "properties": {
    "actor_id": {
      "type": "string",
      "minLength": 1
    },
    "actor_type": {
      "type": "string",
      "enum": ["user", "character"]
    },
    "action": {
      "type": "string",
      "minLength": 1,
      "maxLength": 500
    },
    "target": {
      "type": ["string", "null"],
      "maxLength": 120
    },
    "suggested_skill": {
      "type": "string",
      "minLength": 1,
      "maxLength": 80
    },
    "suggested_context_modifier": {
      "type": "integer",
      "minimum": -10,
      "maximum": 10
    },
    "modifier_reasons": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["reason_tag", "source_id"],
        "properties": {
          "reason_tag": {
            "type": "string",
            "enum": [
              "relevant_clue",
              "useful_equipment",
              "good_position",
              "poor_position",
              "active_condition",
              "time_pressure",
              "unsupported_approach"
            ]
          },
          "source_id": {
            "type": "string",
            "minLength": 1
          }
        },
        "additionalProperties": false
      }
    },
    "authorization": {
      "type": "string",
      "enum": [
        "user_declared",
        "user_delegated"
      ]
    },
    "authorization_evidence": {
      "type": "string",
      "minLength": 1,
      "maxLength": 500
    }
  },
  "additionalProperties": false
}
```

语义验证：

- `actor_type=user` 时，`authorization` 必须为 `user_declared`。
- `actor_type=character` 时，`authorization` 必须为 `user_delegated`。
- 两种授权都必须引用当前 `TurnContext.input_text` 中实际出现的非空原文。MVP 不根据跨回合分工或模型自称的紧急反应授予机械行动权限。
- 角色行动的实际语境修正会再次 clamp 到 -5 到 +5。
- `modifier_reasons.source_id` 必须指向已发现线索、合法装备、当前条件或场景规则。
- `RequestCheckArgs` 不得包含 `check_id`、`game_id`、`turn_id`、`base_skill`、`difficulty_modifier`、最终 `target`、`roll` 或 `outcome`；这些字段只能由可信代码产生。
- `target` 是用户或 GM 请求的目标标识；如果它对应静态交互，RuleEngine 必须把它解析为 `target_id` 和 `rule_id`，不能把自由文本当作模组规则。
- 参数合法只表示可以掷骰，不表示行动必定成功或产生状态效果。

### CheckResult

```json
{
  "type": "object",
  "required": [
    "kind",
    "check_id",
    "game_id",
    "turn_id",
    "module_id",
    "scene_id",
    "rule_id",
    "target_id",
    "actor_id",
    "actor_type",
    "skill",
    "base_skill",
    "difficulty_modifier",
    "context_modifier",
    "target",
    "roll",
    "outcome",
    "allowed_effect_ids",
    "reason_tags"
  ],
  "properties": {
    "kind": {
      "const": "check_result"
    },
    "check_id": {
      "type": "string",
      "pattern": "^check_[a-z0-9_]+$"
    },
    "game_id": {
      "type": "string",
      "minLength": 1
    },
    "turn_id": {
      "type": "string",
      "pattern": "^turn_[a-z0-9_]+$"
    },
    "module_id": {
      "type": "string",
      "minLength": 1
    },
    "scene_id": {
      "type": "string",
      "minLength": 1
    },
    "rule_id": {
      "type": ["string", "null"]
    },
    "target_id": {
      "type": ["string", "null"]
    },
    "actor_id": {
      "type": "string"
    },
    "actor_type": {
      "type": "string",
      "enum": ["user", "character"]
    },
    "skill": {
      "type": "string"
    },
    "base_skill": {
      "type": "integer",
      "minimum": 1,
      "maximum": 99
    },
    "difficulty_modifier": {
      "type": "integer"
    },
    "context_modifier": {
      "type": "integer",
      "minimum": -10,
      "maximum": 10
    },
    "target": {
      "type": "integer",
      "minimum": 5,
      "maximum": 95
    },
    "roll": {
      "type": "integer",
      "minimum": 1,
      "maximum": 100
    },
    "outcome": {
      "type": "string",
      "enum": [
        "critical_success",
        "success",
        "failure",
        "fumble"
      ]
    },
    "allowed_effect_ids": {
      "type": "array",
      "items": { "type": "string", "minLength": 1 },
      "uniqueItems": true
    },
    "reason_tags": {
      "type": "array",
      "items": { "type": "string" }
    }
  },
  "additionalProperties": false
}
```

`CheckResult` 创建后不可修改、覆盖或使用同一 `check_id` 重掷。

`CheckResult` 同时是 `CheckLedger` 中的完整检定记录：`game_id`、`turn_id`、`module_id`、`scene_id`、`rule_id` 和 `target_id` 记录它与当前回合及静态模组规则的关联。MVP 将 `CheckLedger` 保存为独立的 `var/check_records.json`（或等价的独立持久化集合），它不是 `GameState`、Memory 或 EventLog 的可变字段。`check_id` 由 RuleEngine 在所有请求验证通过后创建，在单局 `game_id` 范围内单调递增；请求参数、最终叙事和 `GameMasterTurnResult.checks` 都不能提供或覆盖它。`rule_id` 或 `target_id` 可以为 `null`，表示该检定没有对应的静态交互，但仍必须绑定当前模块和场景。`allowed_effect_ids` 是检定完成时从静态规则按 outcome 冻结的授权快照；开放检定（`rule_id=null`）和未定义 `effects_by_outcome` 的静态检定都必须返回空数组。

schema、授权、目标、规则或预算验证失败的 `request_check` 只返回 `ToolError`，不得创建 `CheckResult` 或 `check_id`。已创建记录在本局结束前保留，StateCommitter 通过 `check_id` 从 `CheckLedger` 查询它，而不是从 Agent 的效果请求中复制检定字段。

## apply_effect

### ApplyEffectArgs

一次调用只申请一个由模组定义的固定效果。GM 不提供路径、操作或值，也不能把 `tool_call_id` 当作状态授权来源。

```json
{
  "type": "object",
  "required": [
    "expected_state_version",
    "source_type",
    "source_id",
    "effect_id",
    "reason"
  ],
  "properties": {
    "expected_state_version": {
      "type": "integer",
      "minimum": 0
    },
    "source_type": {
      "type": "string",
      "enum": ["check", "module_event"]
    },
    "source_id": {
      "type": "string",
      "minLength": 1
    },
    "effect_id": {
      "type": "string",
      "minLength": 1
    },
    "reason": {
      "type": "string",
      "minLength": 1
    }
  },
  "additionalProperties": false
}
```

`source_type=check` 时，`source_id` 是 `check_id`；`source_type=module_event` 时，`source_id` 是静态 `event_rule_id`。StateCommitter 从 `CheckLedger` 或模组事件规则读取权威来源，并验证该来源是否允许 `effect_id`。

新申请的 `expected_state_version` 使用 ToolSession 当前快照版本。精确重试同一来源和效果时允许保留原版本，让 StateCommitter 在版本检查前返回 `already_applied`；其他旧版本请求返回 `rejected`。

### CommitResult

```json
{
  "type": "object",
  "required": [
    "kind",
    "status",
    "effect_id",
    "commit_id",
    "state_version",
    "changes",
    "error_code",
    "message"
  ],
  "properties": {
    "kind": { "const": "commit_result" },
    "status": {
      "type": "string",
      "enum": ["applied", "already_applied", "no_state_change", "rejected"]
    },
    "effect_id": { "type": "string" },
    "commit_id": { "type": ["string", "null"] },
    "state_version": { "type": "integer", "minimum": 0 },
    "changes": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["path", "before", "after"],
        "properties": {
          "path": { "type": "string" },
          "before": {},
          "after": {}
        },
        "additionalProperties": false
      }
    },
    "error_code": { "type": ["string", "null"] },
    "message": { "type": "string" }
  },
  "additionalProperties": false
}
```

- `applied`：效果首次提交，生成 `commit_id`，并递增 `state_version`。
- `already_applied`：同一来源和效果已经提交，返回原提交收据，不重复写入。
- `no_state_change`：来源合法，但效果在当前状态没有变化；不生成 `commit_id`，不递增版本，也不消费来源。
- `rejected`：来源、效果、版本或状态条件不合法；保留原状态并返回 `error_code`。

一个效果内部的多条模组 operation 必须全部预演成功才写入；因此不存在批量请求中的部分接受或部分拒绝。

## update_memory

> 计划中：当前 `StateCommitter` 只原子写入 `GameState`，尚未实现该工具、关系阶段写入或 Memory 跨文件事务。本节不构成已交付接口。

### UpdateMemoryArgs

```json
{
  "type": "object",
  "required": [
    "expected_state_version",
    "public_events",
    "character_turn_call_ids",
    "relationship_events",
    "unresolved_questions"
  ],
  "properties": {
    "expected_state_version": {
      "type": "integer",
      "minimum": 0
    },
    "public_events": {
      "type": "array",
      "items": {
        "type": "string",
        "maxLength": 500
      }
    },
    "character_turn_call_ids": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "uniqueItems": true
    },
    "relationship_events": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "character_id",
          "event_tag",
          "summary",
          "visibility",
          "source_id",
          "set_pending_echo"
        ],
        "properties": {
          "character_id": {
            "type": "string"
          },
          "event_tag": {
            "type": "string",
            "enum": ["respect", "trust", "disclosure", "conflict", "repair"]
          },
          "summary": {
            "type": "string",
            "maxLength": 300
          },
          "visibility": {
            "type": "string",
            "enum": ["public", "character_private"]
          },
          "source_id": {
            "type": "string"
          },
          "set_pending_echo": {
            "type": "boolean"
          }
        },
        "additionalProperties": false
      }
    },
    "unresolved_questions": {
      "type": "array",
      "items": {
        "type": "string",
        "maxLength": 300
      }
    }
  },
  "additionalProperties": false
}
```

`character_private` 更新必须能通过 `source_id` 指向该角色自己的工具调用或关系事件。每名角色最多保留一个 `pending_echo`；新事件可以替换旧候选。角色工具返回 `used_pending_echo=true` 且对应台词进入最终结果时，GameEngine 请求计划中的记忆写入边界清空该角色的待回声事件；未采用的候选台词不消耗回声。当前 StateCommitter 不执行该操作，GameMasterAgent 也不能把自由文本写入任意角色私有记忆。

### MemoryUpdateResult

```json
{
  "type": "object",
  "required": [
    "state_version",
    "public_entries_added",
    "private_entries_added_by_character",
    "relationship_entries_added",
    "rejected_entries"
  ],
  "properties": {
    "state_version": {
      "type": "integer",
      "minimum": 0
    },
    "public_entries_added": {
      "type": "integer",
      "minimum": 0
    },
    "private_entries_added_by_character": {
      "type": "object",
      "additionalProperties": {
        "type": "integer",
        "minimum": 0
      }
    },
    "relationship_entries_added": {
      "type": "integer",
      "minimum": 0
    },
    "rejected_entries": {
      "type": "array",
      "items": {
        "type": "object"
      }
    }
  },
  "additionalProperties": false
}
```

返回值只提供计数和拒绝信息，不把私有记忆内容返回给 GameMasterAgent。

## GameMasterTurnResult

`GameMasterTurnResult` 由 GameEngine 从合法 `GameMasterDraft`、可信工具轨迹和 ToolSession 最终状态快照组装。它不是模型输出 schema，也不包含待应用状态更新。

```json
{
  "type": "object",
  "required": [
    "turn_id",
    "strategy",
    "narration",
    "character_turns",
    "checks",
    "committed_effects",
    "suggested_actions",
    "ending_id"
  ],
  "properties": {
    "turn_id": {
      "type": "string"
    },
    "strategy": {
      "type": "string",
      "enum": ["fast", "dramatic", "urgent", "degraded"]
    },
    "narration": {
      "type": "string",
      "minLength": 1
    },
    "character_turns": {
      "type": "array",
      "items": { "type": "object" },
      "maxItems": 2
    },
    "checks": {
      "type": "array",
      "items": { "type": "object" }
    },
    "committed_effects": {
      "type": "array",
      "items": { "type": "object" }
    },
    "suggested_actions": {
      "type": "array",
      "items": { "type": "string", "minLength": 1 }
    },
    "ending_id": {
      "type": ["string", "null"]
    }
  },
  "additionalProperties": false
}
```

组装规则：

- `strategy`、`narration` 和 `suggested_actions` 来自已验证 Draft；降级时由 GameEngine 使用 `strategy=degraded` 和固定叙事。
- `checks` 按执行顺序收集本轮 `ToolSession.trace` 中成功的完整 `CheckResult`。
- `committed_effects` 只收集状态为 `applied` 或 `already_applied` 的完整 `CommitResult`，并按 `commit_id` 去重；`no_state_change` 和 `rejected` 只保留在轨迹。
- 当前角色生成工具未实现，`character_turns` 必须为空；以后只能收集可信角色工具结果。
- `ending_id` 始终读取 ToolSession 最终 GameState 快照；非空即表示本局已进入该结局，不再需要 `is_ending`。
- `turn_id`、检定、提交、角色结果和结局都不能由模型提供或覆盖。

## GameTurnOutcome

`GameTurnOutcome` 是 `GameEngine.run_turn` 返回给调用层的完整结果。用户可见结果与可信诊断数据在这里分层，而不是要求模型提交轨迹 ID。

```json
{
  "type": "object",
  "required": ["result", "tool_trace", "degraded", "failure_code"],
  "properties": {
    "result": { "type": "object" },
    "tool_trace": {
      "type": "array",
      "items": { "type": "object" }
    },
    "degraded": { "type": "boolean" },
    "failure_code": {
      "type": ["string", "null"],
      "enum": [
        null,
        "model_failure",
        "invalid_model_step",
        "iteration_limit_exceeded",
        "invalid_draft"
      ]
    }
  },
  "additionalProperties": false
}
```

`tool_trace` 使用 ToolSession 持有的完整 `ToolTraceEntry`，包括调用顺序、内部 `tool_call_id`、规范化参数、是否已分发和 `ToolResult`。它是可信运行记录，不属于 `GameMasterDraft` 或玩家世界内记忆。

## ToolDefinition

`ToolSession.available_tool_definitions()` 返回当前回合动态可用的工具目录。GM 不应从 `TurnContext.tool_limits` 猜测工具，也不能调用目录之外的名称。

```json
{
  "type": "object",
  "required": ["name", "description", "input_schema"],
  "properties": {
    "name": { "type": "string" },
    "description": { "type": "string" },
    "input_schema": { "type": "object" }
  },
  "additionalProperties": false
}
```

当前目录只可能包含 `request_check` 和 `apply_effect`。总工具步骤用尽或某工具配额为零时，目录会动态移除工具。角色生成和记忆工具只有在实现并冻结权限契约后才可加入目录；当前场景与已发现线索已经由 GameEngine 投影到 `ModelRequest`，MVP 不另设查询工具。

## ToolResult

每次 `ToolSession.execute` 都返回统一外壳；`tool_call_id` 只用于本轮轨迹、错误关联和调试，不是状态效果授权来源。

```json
{
  "type": "object",
  "required": ["tool_call_id", "tool_name", "ok", "data", "error"],
  "properties": {
    "tool_call_id": { "type": "string" },
    "tool_name": { "type": "string" },
    "ok": { "type": "boolean" },
    "data": { "type": ["object", "null"] },
    "error": { "type": ["object", "null"] }
  },
  "additionalProperties": false
}
```

`ok=false` 表示工具在名称、参数、预算或 RuleEngine 验证阶段被拒绝；此时 `data=null`。`apply_effect` 对已分发的提交始终返回 `ok=true`，具体裁定在 `data.status` 中，包括 `rejected` 和 `no_state_change`。

## ToolError

错误消息只描述可修复问题，不能附带角色私有上下文或未公开模组事实。

```json
{
  "type": "object",
  "required": ["code", "message", "retryable"],
  "properties": {
    "code": {
      "type": "string",
      "enum": [
        "tool_not_allowed",
        "budget_exceeded",
        "invalid_arguments",
        "rule_rejected"
      ]
    },
    "message": { "type": "string" },
    "retryable": { "type": "boolean" }
  },
  "additionalProperties": false
}
```

`request_check` 失败时的 `ToolError` 不包含 `check_id`；只有验证通过并成功创建 `CheckResult` 后，工具结果的 `data` 才可以返回该标识。

## game_state.json

建议的正式状态结构：

```json
{
  "schema_version": "1.0",
  "game_id": "game_0001",
  "module_id": "escape_thalarion",
  "state_version": 12,
  "current_scene": "stone_cell",
  "user_character": {
    "character_id": "user",
    "background_hook": "一直被远海的浪声召回梦中",
    "specialty": "willpower",
    "skills": {
      "willpower": 60,
      "improvisation": 45
    },
    "dream_omen_used_in_scenes": ["stone_cell"],
    "hp": 10,
    "sanity": 50,
    "pressure": 1,
    "conditions": []
  },
  "characters": {
    "vespera": {
      "hp": 11,
      "sanity": 62,
      "pressure": 1,
      "conditions": [],
      "speech_register": "duty_bound"
    },
    "saphra_iskaran": {
      "hp": 12,
      "sanity": 58,
      "pressure": 1,
      "conditions": ["tail_abraded"],
      "speech_register": "formal_confident"
    },
    "aranis": {
      "hp": 9,
      "sanity": 55,
      "pressure": 2,
      "conditions": ["dehydrated"],
      "speech_register": "practical_coworker"
    }
  },
  "clues_found": [],
  "accessible_locations": ["stone_cell"],
  "flags": {},
  "threat_clock": {
    "clock_id": "rathi_gaze",
    "value": 0,
    "maximum": 6
  },
  "ending_id": null
}
```

允许写入的具体路径和范围由 StateCommitter 白名单与模组规则共同决定，不能仅凭示例推断所有字段都可任意修改。

`turn_id` 不写入 GameState，`turn_number` 也不再存在。关系阶段只由下面的 `memory.json.relationship_state` 持有，避免两份权威状态分叉。

## memory.json

```json
{
  "schema_version": "1.0",
  "game_id": "game_0001",
  "public_memory": [],
  "private_memory_by_character": {
    "vespera": [],
    "saphra_iskaran": [],
    "aranis": []
  },
  "relationship_state": {
    "vespera": {
      "stage": "unnamed_choice",
      "events": [],
      "pending_echo": null
    },
    "saphra_iskaran": {
      "stage": "family_name_is_certainty",
      "events": [],
      "pending_echo": null
    },
    "aranis": {
      "stage": "temporary_same_rope",
      "events": [],
      "pending_echo": null
    }
  },
  "unresolved_questions": [],
  "turn_log": []
}
```

角色私有记忆的读取接口必须要求 `character_id`，不能提供“一次读取全部角色私有记忆”的 GameMasterAgent 工具。

当前 GameEngine 只校验 `schema_version`、`game_id` 和 `public_memory`，并把公开记忆作为本轮固定只读快照提供给模型；它不在回合末写 Memory。Memory 不复制 GameState 的 `state_version`。未来若需要并发写入，应定义独立的 `memory_version`。

## 旧字段迁移

| 旧名称 | 现行名称 |
| --- | --- |
| `HostAgent` | `GameMasterAgent` |
| `HostResolution` | `GameMasterTurnResult` |
| 模型生成完整 `GameMasterTurnResult` | 模型只返回 `GameMasterDraft`，GameEngine 组装结果 |
| `PlayerAgent` | `Character` |
| `PlayerAgentAction` | `CharacterTurnProposal` |
| `agent_id` | `character_id` |
| `agent_name` | `character_name` |
| `player_agents.json` | `characters.json` |
| `private_memory_by_agent` | `private_memory_by_character` |

以下运行时概念由 ADR-002 取代，不应出现在新实现中：

| 已取代概念 | 现行设计 |
| --- | --- |
| `InputNormalizer` | `GameMasterAgent` 直接理解 `TurnContext.input_text` |
| `FactSet` | 最小只读 `TurnContext` |
| `TurnRouter` | `GameMasterAgent` 选择回合策略 |
| 权限型 `TurnMode` | GameEngine 固定预算与 ToolExecutor 权限验证 |
| `RoundTrigger` / `trigger_type` | `GameEngine.run_turn(input_text)` 的单用户输入入口 |
| `allow_state_changes` | 动态工具目录与确定性工具验证 |
| `standing_assignment_ids` / `module_reaction_ids` | MVP 不提供跨回合或模组反应的角色机械授权 |
| `is_ending` | `ending_id is not null` |
| `visible_state_changes` | `committed_effects` |
| `available_next_actions` | `suggested_actions` |
| `tool_trace_ids` | `GameTurnOutcome.tool_trace` |
| `GameState.turn_number` | 无；`turn_id` 由 GameEngine 独立分配 |
| `memory.json.state_version` | 无；未来写入边界使用独立 `memory_version` |

旧字段只允许出现在 ADR 的历史说明、归档文档和迁移代码中，不应继续出现在新配置、Prompt 或现行设计文档中。
