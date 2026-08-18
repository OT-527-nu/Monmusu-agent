# Agentic MVP 数据契约

## 文档状态

本文定义新版 MVP 的规范性目标契约。截至 2026-08-17，opt-in Agentic 路径已实现 `session.json` 聚合、不可变会话开场、角色卡与资料、事实账本、`GameMasterResponse`、`CommittedTurn`、`IncompleteTurn`、五工具 COC 机械、六张生产角色卡、non-thinking/thinking `GameMasterModel` seam、显式恢复、完整运行保险丝、单次结构修正、工具结果幂等重放、真实恢复合同证据和 provider 有界重试。统一 `CocTool` 生命周期、各工具生成的统一 `PublicMechanic` 投影、按未完成回合冻结 profile 恢复，以及 `model_profile.retry_policy` 与 `IncompleteTurn.provider_retry` 均已交付。已有完整会话的继续入口、完整短篇、模型评估矩阵和默认入口切换仍是目标子集。旧 `docs/schemas.md` 仅作为迁移对照。执行顺序见 [Agent Loop](agent_loop.md)，职责与 seam 见[系统架构](architecture.md)。

## 契约原则

- GM 只提供虚构判断和调用 COC 机械所需的事前语义参数。
- Harness 生成标识符、读取角色卡数值、掷骰、计算结果并持久化。
- 模组参考书不提供工具权限、检定授权、效果 ID 或状态字段。
- COC 机械结果即时提交且不可重掷；GM 最终答复中的叙事和事实变化原子提交。
- 世界事实是自然语言记录，不包含 JSON 路径、任意补丁或模组专用枚举。
- 所有对象拒绝未知字段；所有字符串去除首尾空白后必须非空，除非字段明确允许 `null`。

## 标识符

下列游戏标识符均由 Harness 生成或从受信静态数据读取，模型不能创建或覆盖。`tool_call_id` 是例外：它由 DeepSeek provider 返回，adapter 只负责校验、保存和原样关联，不把它当作游戏权威：

| 标识符 | 含义 |
| --- | --- |
| `game_id` | 一局短篇的稳定标识 |
| `turn_id` | 一次玩家输入及其所有恢复尝试的稳定标识 |
| `actor_id` | 调查员或具有机械角色卡的 NPC 标识 |
| `fact_id` | 一条已确立事实的稳定标识 |
| `mechanic_id` | 一条不可变机械记录的稳定标识 |
| `tool_call_id` | DeepSeek assistant tool call 与本地结果的协议关联标识；不具有游戏权威 |

`tool_call_id` 可用的确定性条件是：JSON 类型为字符串、值非空、且值与去除首尾空白后的自身完全相同；Harness 不做其他 ID 规范化。一个 assistant 响应内的 ID 必须互不重复；跨响应重用同一 ID 时仍按 `(turn_id, tool_call_id)` 的参数幂等规则处理。

## 运行聚合 `session.json`

MVP 使用一个本地会话聚合保存需要原子协调的短篇状态。Markdown 参考书全文不内嵌于该 JSON；创建游戏时，Harness 把模组和人物参考保存为按内容哈希寻址的只读会话快照，并把哈希写入 `SessionSetup`。生产数据提供三张调查员和三名固定同行者共六张模板；单局只把玩家选中的一张调查员卡、三张固定同行者 NPC 卡和该调查员的叙事资料复制进聚合，使本局不受工作树模板或参考书后续修改影响。未选的两张调查员模板不进入本局存档、上下文或可控角色集合。

```json
{
  "schema_version": "agentic-mvp-2",
  "game_id": "game_01JXYZ",
  "module_id": "escape_thalarion",
  "skill_catalog_version": "coc7e-agentic-mvp-1",
  "setup": {
    "setup_id": "setup_0001",
    "module_reference_revision": "escape_thalarion-agentic-mvp-1",
    "module_reference_sha256": "16119ec99d6536f8033130fd13b6e5d276a04ba1eff6739160eccd67e75cf2c9",
    "character_reference_revision": "characters-agentic-mvp-1",
    "character_reference_sha256": "c951a6a8ea66ca8f5da1af3b9e54ac4bb2fe5256298d1a93227afb61d7688a5f",
    "opening_narration": "海难后的盐水仍从石缝间渗出。牢外忽然传来商人凄厉而短促的惨叫，唯一的灯光随船工的脚步远去。铁锁还扣在门上，但现在没有人守着你们。你打算怎么做？",
    "opening_fact_ids": [],
    "created_at": "2026-07-26T10:00:00Z"
  },
  "session_status": "ongoing",
  "selected_investigator_id": "investigator_tracker",
  "actor_display_names": {
    "investigator_tracker": "林雁",
    "npc_vespera": "维斯佩拉",
    "npc_saphra": "萨芙拉·伊斯卡兰",
    "npc_aranis": "阿兰妮丝"
  },
  "investigator_profile": {
    "actor_id": "investigator_tracker",
    "display_name": "林雁",
    "honorific": null,
    "pronouns": "她",
    "occupation": null,
    "appearance": null,
    "background_hook": null,
    "keepsake": null
  },
  "actors": [],
  "facts": [],
  "turns": [],
  "incomplete_turn": null,
  "created_at": "2026-07-26T10:00:00Z",
  "updated_at": "2026-07-26T10:00:00Z"
}
```

上例只展示 `session.json` 的顶层关系，`actors: []` 和 `opening_fact_ids: []` 是为了避免重复粘贴初始化阶段示意，不是可以开始游戏的存档。Harness 必须在接受第一条玩家输入前完成 `SessionSetup`、拆分并写入开场事实，并把选中的调查员以及维斯佩拉、萨芙拉、阿兰妮丝共四张 `ActorSheet` 写入本局。CLI 展示 `setup.opening_narration` 后才接受第一条输入；这一步不发起没有玩家输入的 GM 回合。

约束：

- `session_status` 只能是 `ongoing` 或 `complete`。
- `setup` 在创建游戏时一次写入，之后不可改写；`opening_fact_ids` 必须引用 `facts` 中由该 setup 建立的事实。
- `skill_catalog_version` 在创建游戏时冻结；每个 `ActorSheet.skill_catalog_version` 必须相同，恢复和机械结算不得读取其他版本。
- 新建 Increment 3 会话使用 `schema_version: "agentic-mvp-2"`；`actors` 恰好包含 `selected_investigator_id` 引用的一张 `role: "investigator"` 角色卡，以及 `npc_vespera`、`npc_saphra`、`npc_aranis` 三张 `role: "npc"` 角色卡，另两张未选调查员只留在生产模板目录。
- 历史 `schema_version: "agentic-mvp-1"` 只兼容 Increment 1 的精确单卡形状：选中的 `investigator_tracker` 及其当时已展开的完整技能集合。装载器不把 `v1` 补成四卡，也不把缺卡的 `v2` 猜成历史存档；其他版本与角色集合明确拒绝。
- `actor_display_names` 在创建游戏时冻结；每个已装载 `ActorSheet` 必须有一个条目，调查员条目必须与 `investigator_profile.display_name` 一致。它只用于稳定显示，不承载人格或机械规则。
- `investigator_profile.actor_id` 必须等于 `selected_investigator_id`；调查员自定义资料在创建游戏时冻结，不是机械数值，也不自动变成 GM 事实。
- 同一 `game_id` 同时只有一个写入者；MVP CLI 串行执行回合。
- 每次机械提交和最终答复提交都通过临时文件与原子替换写入完整聚合。
- `incomplete_turn != null` 时不得接受新的游戏行动，只能恢复该回合或退出 CLI。
- 玩家投影过滤隐藏事实、隐藏机械、DeepSeek 消息、模型隐藏推理和运行诊断。

## 会话开场 `SessionSetup`

`SessionSetup` 是开局前唯一的受信初态载入，不是第二本规则书。它只保存模组参考书中“开场最小正典”所需的开场叙述、参考内容的修订与哈希，以及一组可独立结束的初始事实。实现期可以用很小的结构化 setup fixture 维护这些事实；不得把整本 Markdown 参考书拆成路线、权限或效果表。

```json
{
  "setup_id": "setup_0001",
  "module_reference_revision": "escape_thalarion-agentic-mvp-1",
  "module_reference_sha256": "16119ec99d6536f8033130fd13b6e5d276a04ba1eff6739160eccd67e75cf2c9",
  "character_reference_revision": "characters-agentic-mvp-1",
  "character_reference_sha256": "c951a6a8ea66ca8f5da1af3b9e54ac4bb2fe5256298d1a93227afb61d7688a5f",
  "opening_narration": "海难后的盐水仍从石缝间渗出。牢外忽然传来商人凄厉而短促的惨叫，唯一的灯光随船工的脚步远去。铁锁还扣在门上，但现在没有人守着你们。你打算怎么做？",
  "opening_fact_ids": ["fact_0001", "fact_0002", "fact_0003"],
  "created_at": "2026-07-26T10:00:00Z"
}
```

初始化契约如下：

- Harness 在首条玩家输入前创建唯一 `setup_id`，根据 `module_reference.md` 的开场最小正典 fixture 写入可独立变化的 `FactRecord`；不是机械地把每个 Markdown 段落合成一条事实。
- Harness 同时保存模组与人物 Markdown 的只读内容快照，并校验各自 SHA-256。后续所有 GM 请求只读取这些快照；快照缺失或哈希不符时稳定中止，不能退回读取已经变化的工作树文件。
- 每条 setup 事实的 `origin.kind` 为 `opening_canon`，`origin.source_ref` 指向同一 `module_reference_revision#opening_minimum_canon`，`established_turn_id` 为 `null`。
- `opening_fact_ids` 与这些事实在同一次初始化原子写入中保存；开场叙述由 CLI 直接展示，不产生没有玩家输入的 `CommittedTurn`。
- setup 事实之后和 GM 确立的事实使用同一事实账本，可以被 `retire` 独立结束；setup 本身不授予工具权限，也不限制 GM 的后续即兴裁定。

## 调查员叙事资料 `InvestigatorProfile`

`InvestigatorProfile` 与机械 `ActorSheet` 分离，保存玩家在选卡时自定义的身份表达。它进入 GM 上下文和本局存档，但不是 COC 数值或 GM 事实。

```json
{
  "actor_id": "investigator_tracker",
  "display_name": "林雁",
  "honorific": null,
  "pronouns": "她",
  "occupation": null,
  "appearance": "短发，穿旧防水外套",
  "background_hook": "来梦中寻找失踪的弟弟",
  "keepsake": "一枚裂了边的铜怀表"
}
```

`display_name` 必填；`honorific`、`pronouns`、`occupation`、`appearance`、`background_hook` 和 `keepsake` 可以为 `null`，非空时必须去除首尾空白。MVP 只在创建游戏时接受这些字段，运行中没有改写调查员资料的工具；GM 不得把玩家未写出的重大选择从这些字段中推导出来。

## 机械角色卡 `ActorSheet`

只有参与可信 COC 计算的数据进入角色卡。人格、关系、欲望、秘密与说话方式属于[人物参考](characters.md)和本局事实。

角色 ID 在[角色参考](characters.md)中稳定定义；玩家的显示姓名等资料保存在 `InvestigatorProfile`，不能改存档中的 `actor_id`。实现期静态模板使用 `data/characters/agentic_mvp_actor_templates.json`，技能目录使用 `data/characters/agentic_mvp_skill_catalog.json`；两者都带冻结版本。启动器必须在任何模型调用和 session 写入前共同验证三张调查员模板与三张固定同行者模板，再按目录把中文显示名映射为规范化技能键。只有选中的调查员和三名同行者的最终解析值复制进本局角色卡；修改或删除工作树模板不能改变既有会话。GM 只能引用本局四张角色卡实际提供的属性或技能名称。

```json
{
  "actor_id": "investigator_tracker",
  "role": "investigator",
  "skill_catalog_version": "coc7e-agentic-mvp-1",
  "attributes": {
    "strength": 40,
    "constitution": 55,
    "size": 50,
    "dexterity": 60,
    "appearance": 55,
    "intelligence": 75,
    "power": 65,
    "education": 70
  },
  "skills": {
    "listen": 55,
    "locksmith": 1,
    "navigate": 40,
    "spot_hidden": 70
  },
  "hp": {"current": 10, "max": 10},
  "san": {"current": 65, "max": 65, "session_loss": 0},
  "luck": {"current": 55},
  "armor": 0
}
```

约束：

- `role` 只能是 `investigator` 或 `npc`。
- `attributes` 必须包含 COC 的八项键 `strength`、`constitution`、`size`、`dexterity`、`appearance`、`intelligence`、`power`、`education`，数值为 0 到 100 的整数。
- `skills` 使用本局冻结目录中的规范化技能键和值；每个值必须是 0 到 100 的整数。角色模板未列但目录允许的普通技能由装载器从冻结的 COC 基础值或派生公式补齐；setting skill 只有被该角色模板显式覆盖时才进入角色卡。进入存档后不再依赖模板文件。GM 只能按名称引用，不能提交基础值。中文显示名不是运行时键。
- 规范化键及专长编码以[技能目录](skill_catalog.md)为准，例如 `spot_hidden`、`fighting__brawl`、`language_other__ancient_serpent` 和 `art_craft__rigging`；同一含义不得同时存在多个键。
- `hp.max` 是 1 到 100 的整数，`hp.current` 是 0 到 `hp.max` 的整数。
- `san.max`、`san.current` 和 `san.session_loss` 是 0 到 99 的整数，且 `san.current <= san.max`；`luck.current` 是 0 到 99 的整数。
- `armor` 是 0 到 100 的整数。`move`、`build` 和 `damage_bonus` 不被五项 MVP 工具读取，因此不进入首版 `ActorSheet`；未来机械确实需要时再以证据扩展。
- HP、SAN、幸运和护甲只能由对应 COC 机械更新。
- 新创作的 NPC 不需要角色卡；GM 可以直接裁定其虚构行动。只有确实需要可信 COC 计算的 NPC 才必须在开局数据中拥有 `ActorSheet`。
- MVP 不提供运行时创建或修改角色卡的 GM 工具。

## 事实账本 `FactRecord`

```json
{
  "fact_id": "fact_0007",
  "text": "通往码头的石阶已被海水淹没，只能涉水通过。",
  "visibility": "public",
  "status": "active",
  "established_turn_id": "turn_0003",
  "origin": {
    "kind": "gm_turn",
    "source_ref": null
  },
  "retired_turn_id": null,
  "retire_reason": null
}
```

约束：

- `visibility` 只能是 `public` 或 `hidden`；GM 能看到两者，玩家只能看到公开事实。
- `status` 只能是 `active` 或 `retired`。
- `origin.kind` 只能是 `opening_canon` 或 `gm_turn`。`opening_canon` 必须有 `source_ref`、`established_turn_id: null`，并引用本局 `setup.module_reference_revision#opening_minimum_canon`；`gm_turn` 必须有现存的已提交 `turn_id`、`source_ref: null`。
- Harness 按最终答复中 `establish` 的顺序分配 `fact_id`。
- 结束事实不会删除历史。改变含义使用“结束旧事实，再确立新事实”。
- 揭示隐藏事实通常使用明确叙事，并结束隐藏事实、确立对应公开事实。
- 事实索引是回忆辅助，不是正典许可层。GM 在 `narration` 中作出的明确事实叙述即使漏记于 `establish`，仍因完整回合记录而有效。
- 传闻、比喻、角色观点和刻意保持的不确定内容不应写成客观事实。

## 工具调用统一外壳

DeepSeek 每次 assistant 响应只能选择一个工具调用或一个最终答复。多个 `tool_calls` 被视为协议错误。若所有 `tool_call_id` 都可用且互不重复，Harness 保留原 assistant 消息，为每个 ID 追加一个 `role: "tool"` 消息，其 `content` 是序列化后的 `ok: false` 统一结果（`code: "multiple_tool_calls_not_allowed"`），但不执行其中任何一个；这些协议消息和交互记录先原子写入未完成回合，再交回同一个 GM。若 ID 缺失、不可用或重复，Harness 只把序列化的 `ModelResponse` envelope 写入受限 `IncompleteTurn.provider_protocol_errors`，设置稳定的 `last_failure` code/message，保持 `deepseek_messages` 的最后一个可回放前缀，不把它作为可回放的 assistant/tool 对写入，也不再用“逐 ID tool result”路径继续请求。显式恢复时只发送该前缀，不发送原始 envelope；该响应仍消耗一次模型往返。

成功结果：

```json
{
  "tool_call_id": "call_abc123",
  "tool_name": "make_check",
  "ok": true,
  "result": {},
  "error": null
}
```

失败结果：

```json
{
  "tool_call_id": "call_abc123",
  "tool_name": "make_check",
  "ok": false,
  "result": null,
  "error": {
    "code": "unknown_ability",
    "message": "actor investigator_tracker 没有能力 locksmithing"
  }
}
```

工具参数错误不会产生 `mechanic_id` 或任何状态变化。GM 可以在同一次执行尝试的剩余往返中修正参数；反复失败最终由步骤或时限保险丝中止。

### `ToolInteraction`

每个 `(turn_id, tool_call_id)` 最多对应一条 `ToolInteraction`。它既是幂等索引，也是恢复时重建 assistant/tool 协议消息的最小本地记录：

```json
{
  "tool_call_id": "call_abc123",
  "tool_name": "make_check",
  "arguments_raw": "{\"actor_id\":\"investigator_tracker\",\"ability\":\"locksmith\",\"difficulty\":\"regular\",\"dice_adjustment\":{\"kind\":\"none\",\"count\":0},\"action\":\"用发卡拨动锈蚀锁芯\",\"stakes\":\"失败会制造足以引来走廊守卫的金属声\",\"visibility\":\"public\"}",
  "arguments": {
    "actor_id": "investigator_tracker",
    "ability": "locksmith",
    "difficulty": "regular",
    "dice_adjustment": {"kind": "none", "count": 0},
    "action": "用发卡拨动锈蚀锁芯",
    "stakes": "失败会制造足以引来走廊守卫的金属声",
    "visibility": "public"
  },
  "ok": true,
  "result": {
    "mechanic_id": "mechanic_0012",
    "kind": "check",
    "actor_id": "investigator_tracker",
    "ability": "locksmith",
    "ability_value": 1,
    "difficulty": "regular",
    "target": 1,
    "dice_adjustment": {"kind": "none", "count": 0},
    "roll": 63,
    "success_level": "failure",
    "action": "用发卡拨动锈蚀锁芯",
    "stakes": "失败会制造足以引来走廊守卫的金属声",
    "visibility": "public",
    "push_eligible": true,
    "luck_eligible": true,
    "committed_at": "2026-07-26T10:04:12Z"
  },
  "error": null
}
```

`arguments_raw` 保存 provider 原始 function arguments 字符串；`arguments` 在 JSON 可解析且通过 schema 时保存规范对象，否则为 `null`。`result` 和 `error` 与上面的统一工具外壳相同，成功时 `error: null`，失败时 `result: null`。存档按 `(turn_id, tool_call_id)` 索引交互；幂等比较优先使用规范化参数，无法规范化时使用原始字符串。成功工具调用的机械记录、角色数值变化、该 `ToolInteraction` 以及对应 assistant/tool 消息必须在同一次 `session.json` 原子替换中写入，写入成功后才能向 GM 或 CLI 报告成功。相同 ID 搭配相同参数只返回已保存结果；相同 ID 搭配不同参数属于协议错误，不得重新掷骰。具有可用 `tool_call_id` 的错误工具调用也保存 `ok: false` 的交互和协议消息，但不产生机械记录。单工具调用若缺失或含不可用 ID，则无法形成这一索引或匹配的 tool 消息；Harness 只把序列化原始 `ModelResponse` envelope 写入受限 `provider_protocol_errors`，不创建合成 `ToolInteraction`，并立即中断。

## 共同机械字段

所有成功机械结果都包含：

| 字段 | 含义 |
| --- | --- |
| `mechanic_id` | Harness 生成的不可变记录 ID |
| `kind` | 机械种类 |
| `actor_id` | 被结算的角色 |
| `visibility` | `public` 或 `hidden`，在随机结果产生前确定 |
| `committed_at` | 机械写入会话聚合的时间 |

玩家主动检定以及调查员自己的 HP、SAN、幸运变化必须公开。Harness 可以根据角色与数值类型结构化地强制调查员 HP、SAN、幸运变化公开，但不通过自然语言分类判断某次检定是否来自玩家声明；该语义责任由 GM 指令和真实场景硬门槛验证。秘密 NPC 行动或真正会泄露秘密的机械判断可以事前标记为隐藏；模型不能看到结果后再更改可见性。

## 统一玩家投影 `PublicMechanic`

玩家调用层只接收一种不可变公开投影：

```text
PublicMechanic(
    mechanic_id: str,
    kind: str,
    actor_id: str,
    details: Mapping[str, JSONValue],
)
```

`PublicMechanic` 不是持久化机械 schema，也不把不同工具的完整结果抹平成同一组字段。Harness 只负责包装已提交、事前确定为 `public` 的机械 ID、种类和角色引用；实际执行该调用的 `CocTool` 负责从自己的完整结果中选择并校验 `details`。`make_check` 和 `push_check` 即使都使用 `kind: "check"`，也必须分别保留各自公开契约规定的字段；Harness 不通过 `kind` 猜测投影类型。隐藏机械完整保存并返回 GM，但不构造玩家投影。

`push_check` 与 `spend_luck` 参数中的 `check_id` 不是第二套标识符；它必须等于一条 `kind: "check"` 机械记录的 `mechanic_id`。这个语义化字段名让 Harness 在 schema 层就能拒绝伤害、理智或幸运记录 ID。

`push_eligible` 与 `luck_eligible` 是基础检定提交时的不可变初始资格快照，不是会随补救链变化的当前状态。普通失败只有在它是选中玩家调查员的公开基础检定且规则允许补救时才可保存为 `true`；成功、`fumble`、NPC 或隐藏检定保存为 `false`。一旦使用 Push 或 Luck，原检定和所有派生检定都不回写、不覆盖；当前资格必须由工具 preflight 沿完整不可变补救链推导。`pushed` 派生检定的两个快照字段均为 `false`。

## `make_check`

### 参数

```json
{
  "actor_id": "investigator_tracker",
  "ability": "locksmith",
  "difficulty": "regular",
  "dice_adjustment": {"kind": "none", "count": 0},
  "action": "用发卡拨动锈蚀锁芯",
  "stakes": "失败会制造足以引来走廊守卫的金属声",
  "visibility": "public"
}
```

- `difficulty` 只能是 `regular`、`hard` 或 `extreme`。
- `dice_adjustment.kind` 只能是 `none`、`bonus` 或 `penalty`。
- `none` 的 `count` 必须为 0；奖励或惩罚骰的 `count` 为 1 或 2。
- GM 提供能力名、难度、行动、事前风险和奖励/惩罚骰，不提供目标值、骰点或结果。
- 没有真实不确定性或有意义失败后果时，GM 应直接裁定，不调用检定。

### 结果

```json
{
  "mechanic_id": "mechanic_0012",
  "kind": "check",
  "actor_id": "investigator_tracker",
  "ability": "locksmith",
  "ability_value": 1,
  "difficulty": "regular",
  "target": 1,
  "dice_adjustment": {"kind": "none", "count": 0},
  "roll": 63,
  "success_level": "failure",
  "action": "用发卡拨动锈蚀锁芯",
  "stakes": "失败会制造足以引来走廊守卫的金属声",
  "visibility": "public",
  "push_eligible": true,
  "luck_eligible": true,
  "committed_at": "2026-07-26T10:04:12Z"
}
```

`success_level` 使用 COC 7e 的 `critical_success`、`extreme_success`、`hard_success`、`regular_success`、`failure` 或 `fumble`。完整 d100、奖励/惩罚骰和阈值规则由 Harness 实现并通过独立示例测试。

`push_eligible` 与 `luck_eligible` 只记录这条基础检定在提交时是否具备对应资格。`fumble` 两者均为 `false`；它们不会因为后续 Push 或 Luck 而被修改。Push/Luck 的当前可用性由工具沿原始检定及其派生记录组成的补救链重新计算，并且只对选中玩家调查员的公开检定开放。

## `push_check`

孤注一掷是玩家在一次失败检定后作出的选择。GM 必须先从玩家输入确认新做法，并在重掷前说明更严重的失败风险。

### 参数

```json
{
  "check_id": "mechanic_0012",
  "new_approach": "拆下门轴固定钉，从铰链一侧强行卸门",
  "failure_stakes": "若仍失败，门轴会断裂并把调查员的手夹在石框中"
}
```

Harness 从原检定读取行动者、能力、难度、奖励/惩罚骰和可见性。原检定必须是选中玩家调查员的公开、非 `fumble`、失败基础检定，且其补救链尚未采用 Push 或 Luck；已经孤注一掷、规则不允许推动、NPC/隐藏检定或已经花费幸运解决的检定会被拒绝。成功结果仍使用 `kind: "check"`，拥有新的 `mechanic_id`，并增加：

```json
{
  "pushed_from": "mechanic_0012",
  "is_pushed": true
}
```

原机械记录保持不变；新结果即时提交。GM 必须忠实承接孤注一掷的结果和事前风险。

## `spend_luck`

### 参数

```json
{
  "check_id": "mechanic_0012",
  "points": 23
}
```

玩家必须在当前输入中明确选择花费幸运或明确授权花费足够点数。GM 负责忠实理解这项选择；Harness 只验证检定类型、规则适用性、尚未推动、余额和精确点数，不用自然语言分类器审批玩家意图。GM 不能自动替玩家花费。

### 结果

```json
{
  "mechanic_id": "mechanic_0013",
  "kind": "luck_spend",
  "actor_id": "investigator_tracker",
  "check_id": "mechanic_0012",
  "points_spent": 23,
  "luck_before": 55,
  "luck_after": 32,
  "success_level_before": "failure",
  "success_level_after": "regular_success",
  "visibility": "public",
  "committed_at": "2026-07-26T10:05:08Z"
}
```

花费幸运追加新的机械记录，不覆盖原骰点。对原检定记录的 `roll` 与 `target`，合法点数必须严格为：

```text
points = roll - target
effective_roll_after = target
```

`points` 必须为正整数且恰好等于该差值；不足、超量、余额不足或 `target == 0` 都拒绝。成功后有效结果必须达到原检定声明的 `difficulty`，不能仅改善到较低成功等级，也不能制造 `critical_success`。后续上下文以调整后的有效成功等级解释该检定。

## 骰子表达式

`deal_damage` 与 `make_sanity_check` 共用一个严格骰子表达式解析器，而不是任意代码。MVP 支持固定非负整数，或 `NdM` 后接可选整数修正，例如 `1d6`、`1d10+2`、`2d6-2`。共同限制为 `N <= 20`、`M <= 100`，且表达式的理论最小值与理论最大值都必须落在 `0..100`；固定值也必须落在该范围。Harness 在掷骰前完成语法、数量、骰面和理论范围校验；负损失、未知语法与过大表达式在任何 RNG 前被拒绝。两个工具共用语法和默认限制，但仍分别拥有自己的损失、护甲、HP、SAN 和阈值规则。

## `deal_damage`

### 参数

```json
{
  "actor_id": "investigator_tracker",
  "damage_expression": "1d6+1",
  "cause": "从湿滑石阶跌落并撞上突出的骨钉",
  "armor_applies": true,
  "visibility": "public"
}
```

GM 提供伤害表达式、虚构原因以及现有护甲是否适用于这次伤害。Harness 从角色卡读取护甲与 HP，掷伤害并计算结果；GM 不能直接提交 HP 数值。

### 结果

```json
{
  "mechanic_id": "mechanic_0021",
  "kind": "damage",
  "actor_id": "investigator_tracker",
  "cause": "从湿滑石阶跌落并撞上突出的骨钉",
  "damage_expression": "1d6+1",
  "rolls": [4],
  "raw_damage": 5,
  "armor_applied": 0,
  "damage_taken": 5,
  "hp_before": 10,
  "hp_after": 5,
  "major_wound": true,
  "unconscious": false,
  "dead": false,
  "visibility": "public",
  "committed_at": "2026-07-26T10:11:00Z"
}
```

Harness 使用以下确定性 MVP 子集报告 COC 规则阈值；GM 裁定这些数值在当前虚构中如何表现：

```text
armor_applied = armor_applies ? min(raw_damage, armor) : 0
damage_taken  = raw_damage - armor_applied
hp_after      = max(0, hp_before - damage_taken)
major_wound   = damage_taken >= ceil(hp.max / 2)
dead          = damage_taken >= hp.max
unconscious   = hp_after == 0 and not dead
```

护甲不会造成负伤害；最终伤害为 0 也保存完整可审计机械结果。该子集不增加 CON 检定、濒死计时、治疗、急救或战斗轮；完整战斗状态后置。

## `make_sanity_check`

### 参数

```json
{
  "actor_id": "investigator_tracker",
  "source": "积水倒影中出现了不属于任何人的苍白侧脸",
  "success_loss": "0",
  "failure_loss": "1d6",
  "visibility": "public"
}
```

GM 提供恐怖来源以及成功和失败的 SAN 损失表达式。Harness 从角色卡读取 SAN、完成 d100 检定、选择并掷损失、更新会话累计损失。

### 结果

```json
{
  "mechanic_id": "mechanic_0024",
  "kind": "sanity_check",
  "actor_id": "investigator_tracker",
  "source": "积水倒影中出现了不属于任何人的苍白侧脸",
  "roll": 71,
  "target": 65,
  "outcome": "failure",
  "loss_expression": "1d6",
  "loss_rolls": [4],
  "san_loss": 4,
  "san_before": 65,
  "san_after": 61,
  "session_san_loss": 4,
  "temporary_insanity_threshold_reached": false,
  "indefinite_insanity_threshold_crossed": false,
  "visibility": "public",
  "committed_at": "2026-07-26T10:13:40Z"
}
```

SAN 只计算数值与阈值，不宣称完成完整疯狂判定。计算顺序为：

```text
target = san_before
outcome = success if d100 <= target else failure
raw_loss = 只抽取 outcome 对应的损失表达式
san_loss = min(raw_loss, san_before)
san_after = san_before - san_loss
session_san_loss = previous_session_loss + san_loss
session_start_san = san_before + previous_session_loss
indefinite_threshold = ceil(session_start_san / 5)
temporary_insanity_threshold_reached = san_loss >= 5
indefinite_insanity_threshold_crossed = (
    previous_session_loss < indefinite_threshold
    and session_san_loss >= indefinite_threshold
)
```

固定值 `0` 不调用 RNG；只抽取并记录成功或失败分支实际使用的一个损失表达式。具体惊恐、失控、后续 INT 检定、精神症状或长期病症由 GM 根据结果和虚构裁定，均不在本票实现。

## GM 最终答复 `GameMasterResponse`

模型结束工具循环时只输出以下 JSON 对象：

```json
{
  "narration": "锁芯发出刺耳的刮擦声。门没有打开，走廊尽头却亮起了正在返回的灯火。你仍有时间换一种更冒险的办法。",
  "establish": [
    {
      "visibility": "public",
      "text": "走廊守卫已听见牢门方向的金属声，正在返回。"
    }
  ],
  "retire": [],
  "session_status": "ongoing"
}
```

`retire` 的元素结构为：

```json
{
  "fact_id": "fact_0007",
  "reason": "潮水退去后，通往码头的石阶重新露出。"
}
```

约束：

- 顶层字段只能是 `narration`、`establish`、`retire` 和 `session_status`。
- `narration` 是玩家可见的本轮完整叙事，不包含隐藏事实、内部诊断或模型推理。
- `establish` 可以为空；每项只包含 `visibility` 与自然语言 `text`。
- `retire` 可以为空；每个 `fact_id` 必须引用当前有效事实，且同一答复不得重复结束。
- `session_status` 只能是 `ongoing` 或 `complete`，不包含 `ending_id`。
- GM 不重复输出机械记录、事实 ID、回合 ID、工具轨迹、建议行动菜单或故障字段。
- Harness 在本地解析并校验。首次失败时把具体错误交给同一个 GM，每次执行尝试最多自动结构修正一次。
- 修正响应不能调用工具，也不能改变已提交机械；再次失败则保留未完成回合。

## 已提交回合 `CommittedTurn`

```json
{
  "turn_id": "turn_0003",
  "player_input": "我用发卡试着撬锁。",
  "mechanics": [],
  "narration": "锁舌轻响一声，牢门向外松开。远处的灯仍在移动。",
  "established_fact_ids": ["fact_0007"],
  "retirements": [],
  "session_status": "ongoing",
  "committed_at": "2026-07-26T10:04:30Z"
}
```

- `mechanics` 按工具提交顺序保存本轮完整机械结果，包括隐藏结果。
- 玩家可见投影只显示公开机械、`narration` 和公开事实变化。
- 完整 GM 游戏记录按回合顺序重建玩家输入、精简机械结果、叙事及公开/隐藏事实变化。
- 模型隐藏推理、provider 原始响应和运行错误不进入已提交回合。

## 未完成回合 `IncompleteTurn`

```json
{
  "turn_id": "turn_0004",
  "player_input": "我沿着排水沟爬到牢房后墙看看。",
  "started_at": "2026-07-26T10:06:00Z",
  "attempt_number": 1,
  "attempt_started_at": "2026-07-26T10:06:00Z",
  "round_trips_used": 0,
  "total_round_trips": 0,
  "structure_repairs_used": 0,
  "total_structure_repairs": 0,
  "model_profile": {
    "provider": "deepseek",
    "model_id": "deepseek-v4-flash",
    "thinking": false,
    "stream": false,
    "response_format": "json_object",
    "temperature": null,
    "top_p": null,
    "max_tokens": 4096,
    "prompt_revision": "gm-capability-charter-agentic-mvp-2",
    "tool_schema_version": "coc-tools-agentic-mvp-1",
    "enabled_tools": [
      "make_check",
      "push_check",
      "spend_luck",
      "deal_damage",
      "make_sanity_check"
    ],
    "retry_policy": {
      "mode": "normal",
      "max_retries": 2,
      "retryable_codes": [
        "provider_empty_response",
        "provider_network_error",
        "provider_rate_limited",
        "provider_server_error",
        "request_timeout"
      ],
      "backoff": {
        "initial_delay_ms": 500,
        "max_delay_ms": 10000,
        "jitter_ratio": 0.1
      }
    },
    "base_url": "https://api.deepseek.com"
  },
  "attempt_limits": {
    "max_round_trips": 8,
    "request_timeout_seconds": 60,
    "attempt_timeout_seconds": 180,
    "max_structure_repairs": 1
  },
  "mechanics": [],
  "tool_interactions": [],
  "deepseek_messages": [],
  "provider_protocol_errors": [],
  "provider_retry": {
    "retries_used": 0,
    "total_retries": 0,
    "last_retry": null
  },
  "last_failure": null
}
```

`provider_protocol_errors` 是仅用于未完成回合恢复与诊断的受限数组；每项结构固定为：

```json
{
  "code": "provider_protocol_error",
  "message": "tool_call_id missing or unusable",
  "model_response_json": "{\"assistant_message\":{...},\"finish_reason\":\"tool_calls\"}",
  "recorded_at": "2026-07-26T10:06:02Z"
}
```

`model_response_json` 是 adapter 收到的 `ModelResponse` envelope 的原始 JSON 字符串；它可以包含 provider hidden reasoning，只能留在受限未完成回合中，不能进入 `deepseek_messages`、已提交回合、事实、玩家输出或普通日志。`deepseek_messages` 只包含可直接发送给 DeepSeek 的合法消息前缀。

`last_failure` 使用稳定错误码和不含秘密的短消息，例如：

```json
{
  "code": "request_timeout",
  "message": "DeepSeek 请求超过 60 秒"
}
```

`provider_retry` 记录当前请求链的重试状态。`retries_used` 在每次成功收到 `ModelResponse` 后归零，`total_retries` 只增不减；`last_retry` 只在 sleep 前写入一次已调度重试：

```json
{
  "code": "request_timeout",
  "message": "DeepSeek request timed out",
  "delay_ms": 500,
  "scheduled_at": "2026-08-17T10:06:02Z",
  "status": null,
  "request_id": null
}
```

该对象仅供恢复与诊断，不进入玩家输出。

约束：

- Harness 接受玩家输入并分配 `turn_id` 后立即保存未完成回合。
- 上例展示第一次 provider 请求前的构造状态，因此往返、结构修正和交互数组均为零或空；初始 system/user 消息在发起请求前写入 `deepseek_messages`。
- `model_profile` 保存恢复该 provider 对话所需的全部非秘密、行为相关配置。新回合默认冻结五个 COC 工具；恢复必须使用该未完成回合自己的模型、thinking 模式、Prompt 修订、工具 schema 版本和 `enabled_tools`，不要求等于当前新回合默认 profile。Harness 以完整注册表逐项验证冻结工具子集仍受 `coc-tools-agentic-mvp-1` 支持；若版本、工具实现不可用或 profile 损坏，明确拒绝恢复，不能静默换配置。API key 永不保存。
- `attempt_limits` 描述当前执行尝试。玩家恢复会增加 `attempt_number` 并重置计数与 deadline，但默认继续使用相同的冻结限额；显式开发配置变更必须记录为新的尝试配置。
- `round_trips_used` 与 `structure_repairs_used` 在新执行尝试中重置；`total_round_trips` 与 `total_structure_repairs` 只增不减，用于恢复与费用诊断。
- 失败的 provider 请求不增加 `round_trips_used`；重试次数由冻结的 `model_profile.retry_policy.max_retries` 单独限制，且每次重试 sleep 与下一次请求都必须落在 `attempt_timeout_seconds` 剩余预算内。
- 重试耗尽后保留最后一次 provider 错误码；只有 deadline 耗尽时转 `attempt_timeout`。`provider_retry` 落盘失败时以 `retry_state_persistence_failed` 中断。
- 每次成功机械调用都会把机械记录、相关角色数值、`ToolInteraction`、assistant tool-call 消息和对应 tool result 消息在同一次完整聚合原子替换中写盘，再把结果交给 GM 和 CLI。
- `deepseek_messages` 是恢复 DeepSeek Chat Completions 协议所需的可回放消息前缀；无法配对的 assistant 响应不加入该前缀，而是以 `provider_protocol_errors[].model_response_json` 的可序列化原始 provider envelope 保存在受限恢复状态中，并在 `last_failure` 记录 `provider_protocol_error`。显式恢复只把 `deepseek_messages` 前缀发送给同一冻结 provider 配置，不回放该原始 envelope，也不伪造 `ToolInteraction` 或 tool 消息。thinking 模式的 `reasoning_content` 必须原样保存和回传，但永不进入玩家记录或事实。
- 成功提交最终答复时，Harness 将该对象转换为 `CommittedTurn`、更新事实账本与会话状态，并在同一次原子写入中把 `incomplete_turn` 设为 `null`。
- 玩家明确恢复沿用同一 `turn_id`、输入、机械和工具交互，增加 `attempt_number`，并获得新的八次往返与 180 秒尝试预算。
- 已提交机械绝不重新执行。累计尝试、往返与结构修正次数仅用于诊断，不改变正典。

## GM 上下文

每次执行尝试向同一个 GM 提供：

1. 主持能力章程和最终 JSON 格式。
2. `SessionSetup` 的开场叙述、开场事实来源、模组/人物参考修订与哈希固定的完整 Markdown 参考书。
3. `InvestigatorProfile`、`actor_display_names`、选中调查员资料与所有参与机械的角色卡。
4. 当前公开和隐藏有效事实。
5. 本局完整已提交游戏记录（包含 setup 记录和后续回合）。
6. 当前未完成回合的原玩家输入、既有工具交互和机械结果。
7. 当前五类 COC 语义工具中的已实现子集。

MVP 不提供摘要、RAG、Memory Agent、场景投影或模组权限目录。API key、内部文件路径、玩家不可见诊断和已完成回合的模型隐藏推理不进入 GM 上下文。

## `GameMasterModel` seam

核心保留一个薄模型接口：接收已经组装的消息、一个动态工具目录、单次请求超时和冻结的模型运行配置，返回一个保留完整 assistant 协议消息的 `ModelResponse` envelope。adapter 不在 seam 上替 Harness 执行“只能一个工具”的剧情策略；Harness 根据 envelope 把响应分类为单工具、最终答复、多个工具或 provider 协议错误。

```json
{
  "assistant_message": {
    "role": "assistant",
    "content": null,
    "reasoning_content": null,
    "tool_calls": [
      {
        "id": "call_abc123",
        "type": "function",
        "function": {
          "name": "make_check",
          "arguments": "{\"actor_id\":\"investigator_tracker\",\"ability\":\"locksmith\",\"difficulty\":\"regular\",\"dice_adjustment\":{\"kind\":\"none\",\"count\":0},\"action\":\"用发卡拨动锈蚀锁芯\",\"stakes\":\"失败会制造足以引来走廊守卫的金属声\",\"visibility\":\"public\"}"
        }
      }
    ]
  },
  "finish_reason": "tool_calls",
  "usage": null,
  "latency_ms": 842
}
```

`tool_calls` 长度为 1 且 ID 可用于协议关联时进入单工具分支；单个调用缺失或不可用 ID 时只把序列化原始 envelope 写入受限 `provider_protocol_errors`，不创建 `ToolInteraction` 或 tool 消息。长度大于 1 时保留消息并按[工具调用统一外壳](#工具调用统一外壳)为每个可用且唯一 ID 形成协议错误；任一 ID 缺失、不可用或重复时只保存原始协议事件并保持最后一个可回放消息前缀。长度为 0 且 `content` 非空时进入最终答复分支；其他形状是稳定 provider 协议错误。正常 DeepSeek 请求同时提供当前 function tools 与 JSON Object response format。

鉴权、限流、网络、请求超时和无法形成 assistant 消息的 SDK 响应不伪装成 `ModelResponse`。adapter 通过稳定 `ModelCallError(code, message, retryable, status, provider_retry_after_ms, request_id)` 错误模式返回；Harness 按 `model_profile.retry_policy` 做有界重试，重试耗尽后把最后一次错误映射到 `IncompleteTurn.last_failure` 并中断本次尝试。正常结束但没有可回放内容的响应归类为可重试的 `provider_empty_response`。已经收到且可以序列化的异常 assistant 消息则仍作为 `ModelResponse` 交给 Harness 做协议分类。

- 生产 adapter：`DeepSeekGameMasterModel`，使用 OpenAI Python SDK Chat Completions 与 `base_url="https://api.deepseek.com"`。
- 测试 adapter：可编程假模型，用于稳定制造工具调用、超时、非法结构和恢复路径。
- Adapter 只转换 DeepSeek 消息协议并保留 `ModelResponse`，不拥有 COC 规则、事实账本、游戏存储或恢复决策。
- MVP 不建设通用 provider 注册、路由、能力发现或自动切换框架。

## 运行配置

首个真实协议切片的默认非秘密配置为：

```json
{
  "model_id": "deepseek-v4-flash",
  "thinking": false,
  "stream": false,
  "max_round_trips": 8,
  "request_timeout_seconds": 60,
  "attempt_timeout_seconds": 180,
  "retry_policy": {
    "mode": "normal",
    "max_retries": 2,
    "retryable_codes": [
      "provider_empty_response",
      "provider_network_error",
      "provider_rate_limited",
      "provider_server_error",
      "request_timeout"
    ],
    "backoff": {
      "initial_delay_ms": 500,
      "max_delay_ms": 10000,
      "jitter_ratio": 0.1
    }
  }
}
```

- `model_id` 与 `thinking` 可在运行和评估入口覆盖。
- `stream` 在首版固定为 `false`。
- 新回合的 `enabled_tools` 默认是五项 `make_check`、`push_check`、`spend_luck`、`deal_damage`、`make_sanity_check`；旧未完成回合可以继续使用自己冻结的较小子集，只要完整注册表仍支持它。
- `tool_schema_version` 保持 `coc-tools-agentic-mvp-1`；版本对应的完整工具注册表不得因 profile 只暴露子集而删除已发布验证器。
- 八次往返包含初始响应、工具结果后的继续响应、协议修正与最终结构修正；本地工具执行不计入。
- 每次执行尝试拥有独立的 `request_timeout_seconds=60` 请求上限和 `attempt_timeout_seconds=180` 总时限。
- `retry_policy` 由 `deepseek_model_profile` 解析后冻结；`mode` 当前只支持 `normal`，默认 `max_retries=2`。可重试 code 只接受 `provider_rate_limited`、`provider_server_error`、`provider_network_error`、`request_timeout`、`provider_empty_response`。
- API key 由项目所有者选择的外部机制注入 adapter，不属于该配置 schema，也不得持久化或输出。

## CLI 回合结果

CLI 只需要区分：

- `committed`：显示按顺序产生的公开机械和最终 `narration`，然后接受下一条自由文本输入或结束会话。
- `interrupted`：显示已经提交的公开机械和技术中断提示；未完成回合阻塞新行动，只允许明确恢复或退出。

隐藏机械、隐藏事实、工具参数错误、provider 消息和调试轨迹不作为普通玩家输出。Harness 可以把不含凭据与隐藏内容的诊断写入独立开发日志。

## 原子性与不变量

1. 工具参数验证失败不掷骰、不分配机械 ID、不写状态。
2. 成功机械结果在返回模型前写盘，之后不可修改、删除或重掷。
3. 最终答复结构或事实引用无效时，叙事和全部事实变化都不提交。
4. 最终提交失败不回滚本轮机械；回合保持未完成。
5. `retire` 只能结束有效事实，历史记录永远保留。
6. 玩家主动机械与自身数值变化始终公开；隐藏性必须在随机结果前决定。
7. GM 的明确叙述是正典来源；事实索引遗漏不能撤销已经提交的叙述。
8. `session_status=complete` 只表示本局核心困境已收束，不映射到预定义结局。

## 纵向交付子集

首个真实 LLM 切片只实现完整契约中的：

- DeepSeek non-thinking、非流式 Chat Completions adapter。
- `make_check`。
- GM 最终答复与自然语言事实账本。
- 完整回合记录和连续两个回合的 CLI。
- 一条模组未预写行动的真实契约测试。

`push_check`、`spend_luck`、`deal_damage`、`make_sanity_check`、完整恢复体验和最终模型评估矩阵按[迁移清单](migration.md)逐步加入；它们是最终 MVP 范围，不是首切片前置条件。
