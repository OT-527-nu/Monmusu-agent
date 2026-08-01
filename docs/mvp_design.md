# Monmusu Agent MVP 设计

## 文档状态

本文档定义 Monmusu Agent MVP 的产品目标、范围、成功标准和交付顺序，并落实 [ADR-001](adr/0001-single-gm-agent-and-character-tools.md)、[ADR-002](adr/0002-lightweight-player-facing-mvp-loop.md)、[ADR-003](adr/0003-single-effect-game-state-commit.md) 与 [ADR-004](adr/0004-user-input-gameengine-turn-contract.md)。系统架构以 [系统架构](architecture.md) 为准，单轮行为以 [回合循环](turn_loop.md) 为准，模组内容以 [《逃离塔纳里昂》模组设计](game_design.md) 为准。

开放行动、即兴检定和沉浸式信息隔离的产品边界保持不变。当前已经实现 `request_check`、`apply_effect`、动态工具目录、GameState-only StateCommitter、有限 GameMasterAgent 和单用户输入 GameEngine 外层回合。角色生成、Memory 写入、正式 EventLog、真实 LLM adapter 和连续 CLI 仍是后续 MVP 实现项。

## 项目一句话定位

一个由单一 `GameMasterAgent` 主持、由三名 AI 队友角色陪伴的回合制克系调查文字游戏，让单人用户在约 30 分钟内体验一场包含角色互动、线索推进、规则检定和可控结局的短篇跑团。

## 用户需求

### 目标用户

- 喜欢 TRPG、克系故事、互动小说和 AI 角色扮演的玩家。
- 没有固定跑团时间，但希望快速完成一局短篇冒险的单人用户。
- 希望观察 Agent 工具调用、角色上下文隔离和确定性规则协作方式的开发者或演示观众。

### 核心体验

- 用户是故事中的主角和最终决策者。
- AI 队友拥有不同人格、能力、私有记忆和关系变化，但不会轮流抢占叙事。
- 主持人能理解开放式输入，并在模组边界内动态选择必要工具。
- 用户拥有自己的擅长领域和受限梦兆，不只是审批队友方案。
- 骰点、目标值和成功等级对玩家可见，正式状态可信、可复现、可检查；世界内角色不会感知 GM、工具或检定系统的存在。
- 角色能够通过称呼、身体语言和一次事件回声表现“记得玩家”。
- 一次完整游戏在 25-35 分钟内形成清晰开场、压力升级、关键选择和结局。

## MVP 范围

### 游戏内容

- 只支持一个正式短篇模组：《逃离塔纳里昂》。
- 用户扮演 1 名人类入梦者，并选择一句背景钩子、一个擅长领域和每场景一次的受限梦兆。
- 系统提供 3 名固定 AI 队友角色：维斯佩拉、萨芙拉·伊斯卡兰和阿兰妮丝。
- 普通回合由 `GameMasterAgent` 按需调用 0-1 名角色，任一回合最多调用 2 名角色。
- 使用石牢与码头区、白骨街与千奇集市、阿卡利尔门楼三个主要场景。
- 使用“拉提的注视”六格危机时钟推动压力。
- 使用阿卡利尔门印和引潮舟构成 MVP 唯一稳定、可预测的正式逃离路线。
- 支持多种过程代价和结果层级，但不在 MVP 内展开第二条完整逃生链。
- 场景二不实现角色专属诱惑支线；场景三不让幸存船工突然加入队伍。
- 高时钟或既有失败需要代价时，可以使用四人内部的“最后承重”选择。

### 技术范围

- 系统中只有一个拥有有限 agent loop 的 `GameMasterAgent`。
- `GameEngine` 创建内部最小 `TurnContext`、模型安全投影和公开 Memory 快照，控制每轮固定工具预算、循环终止、可信结果组装和降级。
- `GameMasterAgent` 直接理解用户原文并选择快速、戏剧或紧迫策略；运行时不实现 ADR-002 已取代的外围路由层。
- 计划中的角色台词由 `generate_character_turn` 工具生成，每次调用只读取一个角色的上下文。
- 规则检定、状态验证和正式写入由确定性代码完成。
- CLI 完整试玩是 MVP 交付门槛；Web UI 只作为有余量时的加分项。
- 状态和记忆使用本地 JSON 文件。
- 模组、角色卡、Prompt 和 schema 使用版本控制中的文件维护。
- LLM 接入通过统一 `LLMClient` 封装，具体模型与提供方由配置决定。

### MVP 成功标准

- 可以从新游戏开始完整跑通《逃离塔纳里昂》并到达一个结局。
- 至少两名队友角色能在同一场景表现出可辨识的能力、语气和立场差异。
- 角色生成工具不会泄露其他角色的私有记忆。
- 用户未点名角色且场景不需要角色参与时，可以零角色调用完成回合。
- 普通回合通常不调用超过一名角色，任一回合不会调用超过两名角色。
- 用户至少能通过擅长领域或梦兆直接影响一次局势。
- d100 骰点、目标值和成功等级直接向玩家显示，并可从工具轨迹追溯。
- GM 能接受低影响且有趣的开放行动，只在成功与失败都会产生有意义差异时请求检定。
- 不在静态模组规则中的检定不能改变关键线索、主要路线、场景、危机时钟或其他持久状态。
- 用户使用“GM”“掷骰”或“检定”等元语言时，角色生成上下文和公开记忆中不会泄露这些系统概念。
- 至少一次选择会持续影响下一场景或结局，至少一名角色会回调此前共同经历。
- 运行时不存在被 ADR-002 取代的输入路由组件；模型看不到完整 `TurnContext`，也不能修改工具预算。
- 模型只返回 `GameMasterDraft`，不能提供最终检定、状态提交、结局或工具轨迹。
- GameMasterAgent 无法直接修改正式状态或改写检定结果。
- 任一角色生成调用失败时，当前回合仍能结束并继续游戏。
- Demo 可以展示“用户输入 → 工具调用 → 角色回应 → 检定 → 状态提交 → 主持叙事”的完整轨迹。

## 非目标

- 不实现多个独立 Agent 之间的通信、handoff 或协商协议。
- 不让 AI 队友拥有工具循环、长期自主任务或递归发起回合的能力。
- 不实现开放世界探索或自动生成无限模组。
- 不复刻完整 COC 7 版规则。
- 不实现复杂战斗系统、联网多人或商业级账号系统。
- 不在 MVP 阶段实现跨模组长期成长和大型向量数据库记忆。
- 不实现 ADR-002 已取代的外围路由层。
- 不实现角色专属诱惑支线、船工终局抉择、实时倒计时或通用多人挑战框架。
- 不把完整 Web UI 作为 MVP 完成条件。
- 不保证每次游戏产生完全不同的剧情；MVP 优先保证边界清楚和流程稳定。

## 核心游戏循环

### 1. 创建回合

- `GameEngine.run_turn(input_text)` 只接收一次 1 至 4000 字符的用户原文；MVP 不预分类触发类型。
- 在分配 `turn_id` 前读取并校验 GameState、Memory 和所需静态数据；已经结束或数据非法时不调用模型和工具。
- `GameEngine` 创建只读 `TurnContext`，只含 `turn_id`、用户原文、初始 GameState、总工具步骤和各工具配额。
- GameEngine 另行创建固定 `GameMasterStateView`、当前场景上下文和公开记忆快照；模型只通过 `ModelRequest` 读取这些安全投影。
- GameEngine 在 Agent 启动前确定最多 2 次检定、最多 2 次效果申请和 Agent 循环上限；角色工具接入后再加入最多 2 次角色调用配额。

### 2. GameMasterAgent 选择工具

- 只从 `ToolSession.available_tool_definitions()` 获取此刻可用工具；当前目录只有 `request_check` 和 `apply_effect`。
- 先理解用户意图，必要时请求澄清，并选择快速、戏剧或紧迫策略。
- 可按需调用一个或两个角色，指定其参与角色和目的。
- 可跳过角色调用，直接回应纯规则、纯观察或无需队友参与的输入。

### 3. 角色生成

本节是后续 MVP 切片，`generate_character_turn` 尚未加入当前动态工具目录。

- `generate_character_turn` 读取目标角色卡、该角色私有记忆、公开场景和关系状态。
- 独立提议不读取另一角色尚未公开的判断；公开接话可以读取本轮已经公开的台词。
- 工具返回 `CharacterTurnProposal`，包含台词、意图和可选行动提议。
- 提议不会自动进入正式状态，也不能替用户作出决定。

### 4. 规则检定

- GameMasterAgent 通过 `request_check` 判断需要检定，并描述行动、目标、建议技能、语境修正和授权证据；它不创建检定实例或 `check_id`。
- 用户可以用“我要进行检定”等元语言表达尝试，但不能强制触发骰点。GameMasterAgent 仍根据风险、不确定性和结果差异决定是否需要检定；结果明显或失败没有趣味时直接叙述。
- `RequestCheckArgs.target` 只表示当前场景中由模组 `check_rules` 定义的权威目标 ID。行动在叙事中可以有具体对象，但没有对应权威目标时使用 `target=null`，不新增含义重叠的 `ad_hoc` 类型。
- `target=null` 表示低影响开放行动：使用行动者已有技能，静态难度修正为 0。GM 不能为它临时编造模组难度。
- 如果行动可能增减关键线索、显著偏离模组方向或改变其他重要持久状态，却没有对应的 `check_rule`，GM 不直接判定该重大结果，而是把尝试转化为观察、试探、寻找条件等安全的准备行动。
- ToolExecutor 先验证动态目录、schema 和工具预算；RuleEngine 再验证行动者、授权、目标与规则语义。失败时只记录 `ToolError`，不创建检定记录。
- `RuleEngine` 从角色配置、当前状态和静态模组交互规则解析技能与难度，验证授权和修正理由。
- 验证通过后，RuleEngine 创建本局唯一 `check_id`，掷 d100，从静态规则冻结 `allowed_effect_ids`，并把完整 `CheckResult` 保存到 `CheckLedger`（MVP 物理落点为独立的 `var/check_records.json`）。
- 不存在绕过 RuleEngine 和 CheckLedger 的“叙事骰”。`rule_id=null` 的检定结果同样不可重掷或改写，但只影响当前回合的描写，不授予正式状态或记忆的写入权限。
- `StateCommitter` 通过 `check_id` 查询 `CheckLedger`，验证状态效果来源；它不信任 Agent 复制的骰点字段。
- 界面直接展示技能、目标值、骰点和成功等级。

### 5. 状态与记忆

- 当前 `apply_effect` 一次只申请一个静态 `effect_id`，来源只能是合法 `check_id` 或模组静态 `event_rule_id`。
- GM 只提交版本、来源、`effect_id` 和理由；StateCommitter 从模组读取路径、操作和值，并原子写入一个 GameState 文件。
- `CommitResult` 区分 `applied`、`already_applied`、`no_state_change` 和 `rejected`；只有前两者能够支持正式状态变化叙事。
- 当前 `tool_call_id` 只用于轨迹，不是效果授权来源。
- `update_memory`、关系阶段写入、正式 EventLog 和跨文件事务仍是后续 MVP 切片，不属于当前 StateCommitter 原子范围。
- `rule_id=null` 的开放检定不会写入线索、公开记忆、角色私有记忆或正式世界事件；其机械记录只保留在 CheckLedger 和调试轨迹中。
- 公开记忆只保存世界内事实。用户原文中的 GM、工具、Prompt、掷骰或检定等元语言，必须先转换为世界内行动与可感知结果，不能原样进入角色上下文。
- 每名角色最多维护一个待回声事件 `pending_echo`，在后续合适场景使用一次。

### 6. 最终叙事

- GameMasterAgent 根据工具返回的事实、角色台词和检定结果只生成 `GameMasterDraft(strategy, narration, suggested_actions)`。
- GameEngine 校验 Draft，并从 ToolSession 可信轨迹和最终状态快照组装 `GameMasterTurnResult`：真实检定、已提交效果、角色结果、`turn_id` 和 `ending_id` 都不由模型提供。
- 调用层收到 `GameTurnOutcome`，其中把用户可见结果与完整工具轨迹、降级标记和失败码分开保存。
- 用户界面把世界内叙事与玩家专属机械信息分开呈现：角色只接触前者，玩家可以额外查看技能、目标值、骰点和成功等级。

完整时序、权限和失败降级见 [回合循环](turn_loop.md)。

## 职责分工

### GameMasterAgent

- 理解用户输入并决定本轮需要哪些工具。
- 选择快速、戏剧或紧迫策略；策略不能扩大动态工具目录、预算或确定性模块权限。
- 组织场景、NPC、角色回应和规则结果。
- 判断开放行动是否值得检定，以及其结果是否会越过低影响边界；重大但无静态规则的尝试应转为安全的准备行动。
- 保持克制、压迫的模组氛围。
- 在模组允许范围内推进节奏，但不越过用户关键选择。
- 不直接读取角色私有记忆、写入状态或修改检定结果。

### AI 队友角色

- 根据角色卡和自身上下文表达台词、判断、情绪和行动提议。
- 可以误判、害怕、隐瞒动机或与其他角色产生分歧。
- 不能裁定结果、创造关键线索、控制用户或读取其他角色私有信息。
- 不拥有工具调用能力；运行时只是 `CharacterTurnProposal` 的来源。

### GameEngine

- 管理单用户输入外层回合、数据预检、工具预算、循环终止和失败降级。
- 创建内部 `TurnContext`，并为模型生成公开状态、场景和 Memory 的只读投影。
- 校验 `GameMasterDraft`，从可信轨迹和最终快照组装 `GameMasterTurnResult` / `GameTurnOutcome`。
- 当前回合只读取 Memory，不在回合末重读 GameState，也不写 Memory 或 EventLog。

### ToolExecutor / ToolSession

- `ToolExecutor.start_turn` 为每回合创建一个隔离的 `ToolSession`。
- `available_tool_definitions` 是 GM 的唯一工具目录，会随总步骤和单工具配额动态缩小。
- `execute` 统一完成参数预检、预算扣减、可信模块分发和 `ToolResult` 轨迹。
- `apply_effect` 返回后刷新会话 GameState 快照；`tool_call_id` 不承担状态授权。

### RuleEngine

- 验证行动、授权、技能来源和静态模组规则。
- 在生成 `check_id` 后计算目标值并掷 d100，将不可变结果写入 `CheckLedger`。
- 限制 LLM 建议的语境修正。
- 返回成功等级，并把当前 outcome 对应的 `allowed_effect_ids` 冻结到 CheckResult。

### StateCommitter

- 接收一次一个 `ApplyEffectArgs`，校验 `check_id` / `event_rule_id` 来源、固定效果、状态版本和模组条件。
- 从模组 `effect_definitions` 读取受限 operations，在完整预演后原子写入 GameState。
- 通过 `commit_metadata` 保证幂等和同一来源至多选择一个效果。
- 当前不写 Memory、EventLog 或关系阶段。

### CharacterTurnGenerator

后续 MVP 切片：

- 为指定角色组装隔离上下文。
- 把本轮参与目的表达为世界内摘要，不传入用户原文、GM 元语言、工具轨迹或检定机械信息。
- 执行一次受 schema 约束的 LLM 生成。
- 返回候选台词和行动，不执行规则或状态写入。

## d100 检定

MVP 保留轻量的低点成功规则：

```text
target = clamp(base_skill + difficulty_modifier + context_modifier, 5, 95)
success = roll <= target
```

- `base_skill`：来自角色配置。
- `difficulty_modifier`：非空权威目标从当前场景的 `check_rules` 读取；`target=null` 时固定为 0，不能由 GM 临时编造。
- `suggested_context_modifier`：GameMasterAgent 根据已发现线索、装备、处境和行动表述提出的建议。
- `context_modifier`：RuleEngine 对建议值和理由验证、限制后实际采用的修正。
- `check_id`：RuleEngine 在请求验证通过后生成的单局唯一检定标识；非法请求和预算耗尽不会生成它。
- 用户行动的建议范围为 -10 到 +10；角色行动为 -5 到 +5。
- `roll`、`target` 和 `outcome` 只能由 RuleEngine 产生。
- 每次检定必须向玩家展示技能、`target`、`roll` 和 `outcome`。
- 每次实际掷骰都必须生成并保存 `CheckResult`；不存在不留痕的旁白骰。

成功等级：

- `critical_success`：`roll <= 5`。
- `success`：`roll <= target`。
- `failure`：`roll > target`。
- `fumble`：`roll >= 96`。

特殊等级优先于普通成功或失败。具体字段以 [JSON 数据契约](schemas.md) 为准。

## LLM 与确定性代码边界

### 使用 LLM

- GameMasterAgent 对开放式输入的理解和工具选择。
- GameMasterAgent 对快速、戏剧和紧迫策略的选择。
- 场景、NPC 和检定结果的叙事表达。
- 角色工具中的人物台词、建议和情绪反应。
- 受限的检定语境修正建议。
- 必要的公开记忆摘要文本。

### 使用确定性代码

- 用户输入预检、`TurnContext`、`GameMasterStateView`、场景/公开记忆投影和固定调用预算。
- 角色私有上下文选择和隔离。
- 用户原文、玩家专属机械信息与角色生成上下文之间的结构隔离。
- schema、工具参数和行动授权验证。
- 基础技能、难度、修正 clamp、掷骰和成功等级。
- 场景、线索、危机时钟、HP、压力和条件状态。
- 状态写入、版本冲突处理和结局条件。
- 最终结果聚合、工具轨迹和失败降级；正式 EventLog 仍待实现。

## 主要技术栈

- Python 3.11+。
- OpenAI SDK 或兼容接口，由 `LLMClient` 隔离具体提供方。
- 本地 JSON 状态与记忆。
- Markdown 设计文档与 JSON/YAML 运行配置。
- CLI 作为第一运行入口，Web UI 复用 GameEngine。
- Pydantic 或 `jsonschema` 进行结构校验。
- Python 标准库 `unittest` 作为当前最小测试入口。

## 8 周交付计划

### 第 1 周：冻结体验与数据基线

- 同步 ADR-001 至 ADR-004，建立唯一文档入口和术语表。
- 定稿内部 `TurnContext`、模型安全 `ModelRequest`、三种回合策略、角色权限、入梦者最小角色卡和模组轻量改动。
- 定义工具、状态、关系事件和记忆 schema。
- 验收：现行文档只把单用户输入、内部最小 `TurnContext` 和 GM 语义路由作为运行时方案。

### 第 2 周：确定性规则与状态

- 完成 RuleEngine 的 d100、修正限制和成功等级。
- 完成公开骰点格式、行动授权证据和入梦者梦兆次数。
- 完成 GameState 存储和 CheckLedger 边界。
- 完成 StateCommitter 的单效果白名单、版本、幂等和来源检查。
- 验收：不调用 LLM 也能执行检定和受验证状态提交。

### 第 3 周：GameMasterAgent、GameEngine 与工具执行器

- 实现有限 agent loop、内部 `TurnContext`、模型安全投影、固定工具预算和终止条件。
- 实现 GameEngine 的输入/数据预检、Draft 验证、可信结果组装和保守降级。
- 接入真实 LLM adapter；场景和已发现线索由 GameEngine 投影，不增加查询工具。
- 实现快速、戏剧和紧迫策略。
- 接入已实现的检定和状态工具。
- 验收：固定输入能够产生合法 `GameMasterDraft`、可信工具轨迹和 `GameTurnOutcome`。

### 第 4 周：角色生成工具

- 迁移三张 CharacterProfile。
- 实现 `generate_character_turn` 和 `CharacterTurnProposal`。
- 实现独立提议、公开接话、关系语体、非语言提示和 `pending_echo`。
- 验证角色私有记忆隔离和最多 2 次调用上限。
- 验收：同一公开场景中角色风格不同，且不存在跨角色私密信息泄露。

### 第 5 周：完整回合

- 接通用户输入、角色回应、检定、状态提交、记忆更新和最终叙事。
- 为 Memory、关系阶段和 EventLog 定义并实现独立于 GameState effect 的写入边界。
- 实现角色行动权限、紧急保护和用户主导权规则。
- 验证拒绝菜单、直接点名角色和合理怪招。
- 验收：CLI 连续运行多个回合，工具失败不会阻塞游戏。

### 第 6 周：模组联调与降级

- 完成模型超时边界、受控错误和保守模板；非法 Draft 当前直接降级，不增加修复调用。
- 接入札记标价、门楼最后承重、协作蒙太奇和三个结局。
- 加入模组边界、线索公开和状态效果验证。
- 验收：完整流程控制在 25-35 分钟，常见越权和 LLM 失败均可降级。

### 第 7 周：桌面模拟与修正

- 至少完成三次 CLI 全流程试玩。
- 专门测试拒绝菜单、单角色互动、合理怪招、拒绝亲密接触、紧急保护和角色工具失败。
- 根据实际记录减少复述、无意义检定和过长角色台词。
- 验收：三次完整试玩均能到达结局，且玩家主导权不依赖固定菜单。

### 第 8 周：Demo 打磨

- 准备固定 seed 或可复现脚本。
- 展示工具调用、角色隔离、检定和状态提交轨迹。
- 完成 README、演示讲稿和失败兜底。
- Web UI 仅在有余量时实现最小壳层。
- 验收：10 分钟讲清架构，25-35 分钟从 CLI 完成一次试玩。

## Demo 展示

### 10 分钟架构演示

1. 从石牢场景开始。
2. 用户询问门外威胁。
3. 展示内部 `TurnContext` 与模型实际收到的 `ModelRequest` 边界、固定预算和 GM 选择的快速策略。
4. 展示 GameEngine 的场景投影，并在角色工具接入后调用维斯佩拉作为主要回应者。
5. 展示 `generate_character_turn` 的隔离输入摘要和结构化输出。
6. 用户尝试行动并触发 `request_check`。
7. 直接展示不可修改的 d100 结果和受验证状态提交。
8. 展示 `GameMasterDraft` 如何被组装为 `GameTurnOutcome`，以及工具轨迹和公开/私有记忆边界。

### 30 分钟完整试玩

1. 石牢脱困并确定队伍关系。
2. 在白骨街与千奇集市理解门印、路线和拉提警告。
3. 前往阿卡利尔门楼，完成门印方向和双绞盘操作。
4. 必要时处理最后承重，并根据线索、状态和时钟进入不同结果层级。

## 后续扩展条件

只有当角色确实需要独立选择工具、跨回合维护任务和自主观察执行结果时，才重新评估角色 Agent。届时应新增 ADR，不得仅通过改名把 `CharacterTurnGenerator` 悄然扩展为独立 Agent。角色专属诱惑、船工终局事件、实时倒计时、通用关系图、通用多人挑战和完整 Web UI 同样属于 MVP 后扩展。

试玩后可以根据趣味性、主线偏移风险和工程复杂度重新评估开放检定的效果边界，但不得在缺少实际回合记录时预先引入第二套骰点或自由状态写入机制。评估时应重点观察 GM 是否过度拒绝怪招、是否产生过多无意义检定，以及低影响结果是否值得跨回合回收。

若跨回合回收确实能显著增加趣味性，可在 MVP 后设计受限的 `npc_flavor_echo`：只绑定已有的具名 NPC，只影响一次后续台词、戒备动作或氛围表现，并在使用一次或短期过期后清除。它不能提供检定修正、增减线索、改变路线或成为通用 NPC 记忆系统。
