# 当前实现迁移清单

## 文档目的

本文件记录规则驱动实现迁移到 [Agentic MVP](README.md) 的顺序、验收门和退役结果。它不把未完成的真实质量验收描述成已经实现。项目原计划在模型矩阵和完整试玩后删除旧路径；项目所有者于 2026-08-19 明确决定提前退役无比较价值的旧基线。这个决定改变清理顺序，不改变剩余质量门。

目标模块职责见[系统架构](architecture.md)，接口形状见[数据契约](contracts.md)，执行与恢复顺序见 [Agent Loop](agent_loop.md)。本文只规定迁移结果与依赖顺序，不预先锁定目标 Python 文件布局。

## 截至 2026-08-19 的实现事实

Increment 1 至 Increment 3 已完成真实 DeepSeek 两回合开放行动验证、确定性故障/恢复矩阵、显式 CLI 恢复门、工具结果幂等重放、thinking 恢复传输、non-thinking/thinking 真实恢复合同、六张生产角色卡、五个 COC 工具的统一生命周期，以及 Ticket 18 场景二和场景三的真实验收。Ticket 22 至 Ticket 24 又完成了玩家安全的 session 目录投影、已有 ongoing session 的选择续玩，以及增量四内容发布边界的确定性审计。默认 `monmusu-agent` 已切换到 Agentic CLI；旧运行链、旧测试、旧 JSON 数据和旧顶层设计文档已删除。完整短篇、六场景、72 次矩阵、四局复试和默认模型选择仍未完成。

| 当前区域 | 已有能力 | 与目标设计的差距 | 迁移处理 |
| --- | --- | --- | --- |
| [`agentic_session.py`](../../src/monmusu_agent/agentic_session.py) | 原子创建并严格装载会话聚合、不可变参考快照、生产角色卡、事实、已提交回合和恢复形状的 `IncompleteTurn`；`agentic-mvp-1` 历史单卡与 `agentic-mvp-2` 生产四卡按各自精确形状装载；session catalog 为 CLI 提供安全的 ongoing/complete/incomplete 选择和回顾 | 完整短篇开放收束与真实内容质量尚未验收 | 保持 SessionStore 为本地持久化深模块；继续只增加真实调用者需要的最小能力 |
| [`agentic_coc.py`](../../src/monmusu_agent/agentic_coc.py) | `CocTool` 统一 `normalize` / `preflight` / `execute` / `validate_result` / `validate_result_arguments` / `validate_persistence` / `public_details` 生命周期；`DEFAULT_COC_TOOLS` 注册 `make_check`、`push_check`、`spend_luck`、`deal_damage`、`make_sanity_check` 五工具；每个工具拥有领域预检、RNG、即时机械与恢复幂等校验 | 与目标 seam 一致；当前仅以单一模块承载五工具，尚未按内部职责拆文件 | 保持为 Harness 内部深模块；是否拆文件不改变公开 seam 和 GM 可调用权限 |
| [`agentic_harness.py`](../../src/monmusu_agent/agentic_harness.py) | `start_turn`/`resume_turn` 隐藏上下文组装、GM 响应分类、五工具动态目录、统一 `CocTool` preflight 生命周期、`PublicMechanic` 投影、即时机械提交、最终答复原子提交、工具结果幂等重放、一次结构修正、180 秒尝试时限和八次往返保险丝；ongoing 续玩复用同一上下文 seam | 完整短篇开放收束验收与模型质量矩阵未运行 | 在同一 Harness lifecycle seam 完成剩余生命周期能力，不拆出第二套 orchestrator |
| [`agentic_model.py`](../../src/monmusu_agent/agentic_model.py) | `GameMasterModel` 同时有可编程假 adapter 与 OpenAI SDK DeepSeek adapter；non-thinking/thinking、JSON Object、function tools、请求 timeout、稳定错误映射和 thinking `reasoning_content` 恢复传输已接通 | 没有多 provider、自动路由或 fallback；真实质量矩阵尚未运行 | 保持薄 provider seam，不把恢复决策、工具幂等或 provider 路由移入 adapter |
| [`agentic_cli.py`](../../src/monmusu_agent/agentic_cli.py) | 默认 CLI 可新建不可变会话、选择 ongoing/complete/incomplete session、显示安全回顾、连续提交行动、展示已提交公开机械/事实/叙事、在技术中断时停止并提供明确恢复/退出门；终端输入固定为 UTF-8 | 完整开放收束仍未验收 | CLI 只负责显式运行选择与公开投影；恢复状态和执行仍委托 Harness |
| [`agentic_contract.py`](../../src/monmusu_agent/agentic_contract.py) | 显式真实契约 runner 已证明 direct final、一次匹配 `tool_call_id` 的 `make_check` 往返、non-thinking/thinking 工具中断、重建和恢复，以及 Ticket 18 场景二和场景三的五工具 profile 自动门；完整脱敏记录见 [Ticket 11 evidence](evidence/ticket-11-live-recovery-2026-08-07.md) 与 [Ticket 18 evidence](evidence/ticket-18-increment-3-acceptance-batch-v2-2026-08-16.md) | 真实合同只证明 SDK/provider 传输与场景自动门，不评价完整六场景质量、其余场景或 72 次模型矩阵 | 保持 live 证据与确定性恢复矩阵、人工质量评估分开 |
| [`config.py`](../../src/monmusu_agent/config.py) | 只提供项目根路径，具体 Agentic 输入由拥有者模块声明 | 没有通用配置注册表 | 保持最小共享路径接口 |
| [`storage.py`](../../src/monmusu_agent/storage.py) | 提供 JSON 读取和同目录原子替换原语 | 完整一致性仍由 SessionStore/Harness 协议负责 | 保持无领域语义的存储原语 |
| [`tests/`](../../tests) | 通过真实临时会话与可编程假 model 覆盖 Agentic lifecycle、可见性、原子提交、五工具边界与幂等、恢复、结构修正、执行限制、thinking 回放和真实 adapter seam | 完整短篇六场景和模型质量矩阵仍未覆盖 | 继续在公开 lifecycle seam 增加后续内容测试；旧规则驱动测试已经删除 |

“已有有限循环”不等于新版 Agent Loop 已经完成；“已有原子 JSON 写入”也不等于多份目标记录已经具备原子性和恢复协议。迁移验收必须观察目标公开 seam 和真实持久化结果，不能由类名相似推断能力存在。

## 明确保留与复用

重写改变的是权威和契约，不是把所有已有工程能力删除。迁移应优先复用或延续：

- `GameMasterModel` 注入 seam，以及用可编程假 adapter 精确测试模型步骤的方式。
- 单一、有限、串行的 GM 循环和只读输入快照思想。
- 可注入随机源、机械产生后不可重掷和结果不可由 GM 篡改的记录思想。
- 工具结果轨迹；新版需要把恢复所需部分持久化，而不是只保留内存 trace。
- `write_json_atomic` 的同目录临时文件与原子替换原语。
- 输入、角色/模组数据和存储的模型调用前预检，以及稳定的本地错误类别。
- 一局一个写入者、模型与工具串行调用的 MVP 并发边界。

复用以目标契约为准。旧类即使内部实现有价值，也不能把 `effect_id` 授权、固定状态投影或兜底叙事带入新版公开 seam。

## 迁移纪律

1. **先纵向证明，再横向补全**：先让真实 DeepSeek GM 处理一个未预写行动、调用一个 `make_check` 并跨两回合保持事实，再增加完整 COC 机械和内容。
2. **不恢复双路径**：默认入口已经切换，后续不重新引入旧兼容分支或通用版本路由器。
3. **删除旧测试不是新版证据**：新版仍必须用独立的契约、不变量和真实模型验收证明自身行为。
4. **机械记录先于叙事成功**：已提交机械不能因模型失败回滚或重掷；GM 答复和事实变化必须整体提交。
5. **参考书不授权剧情**：迁移不得把 `effect_definitions` 换名为另一套路线、线索或事实白名单，也不得新增模组专用工具绕过目标设计。
6. **事实索引不是唯一正典**：完整回合记录保留明确叙述；`establish` 和 `retire` 只维护需要持续提醒 GM 的当前事实。
7. **不静默转换旧存档**：旧 runtime 数据与新版事实/回合语义不同。迁移期使用带明确 schema 版本的新游戏数据；不把旧 `memory.json`、`state.json` 或 ledger 猜测转换成新正典。
8. **清理不等于质量验收**：项目所有者决定提前删除旧实现；未完成的六场景、开放试玩和模型评估必须继续显式报告。

## 增量总览

| 增量 | 证明的问题 | 完成后仍明确不做 |
| --- | --- | --- |
| 0. 新版权威文档与契约 | 团队是否对产品、权威、数据和验收使用同一语言 | 不改运行时代码 |
| 1. 真实 DeepSeek 最小纵向切片 | LLM GM 是否能自由裁定、调用一个通用检定并维持两回合事实 | 不补齐所有 COC 机械，不宣称故障恢复完成 |
| 2. 未完成回合与运行恢复 | 真实 provider 中断后能否保留机械并安全继续同一回合 | 不增加新剧情能力 |
| 3. 完整 MVP COC 机械与预生成调查员卡 | 五类工具和正式机械数据是否覆盖短篇所需受信结算 | 不实现完整战斗轮、追逐、魔法或成长 |
| 4. 模组内容整合、NPC 与开放短篇 | CLI 是否能从开场运行到玩家造成的开放收束 | 不建立第二套模组加载、角色 Agent、关系等级、GUI 或 Memory Agent |
| 5. 模型评估矩阵与完整试玩 | 默认模型配置是否有重复的真实主持证据 | 不建设通用多 provider 框架 |
| 6. 旧路径清理 | 新版是否能成为唯一运行路径 | 不保留永久双运行架构 |

## 增量 0：新版权威文档与契约

### 交付

- `CONTEXT.md` 定义 GM、Harness、正式事实、未完成回合和 COC 机械等统一领域语言。
- ADR-005 至 ADR-045 记录已经接受的方向与边界。
- `docs/agentic_mvp/` 分别描述产品、架构、循环、契约、Prompt、模组、角色、评估和迁移。
- 六个聚焦场景有可执行夹具草案，目标 schema 有示例和反例。
- `docs/README.md` 明确本目录是当前设计权威，旧顶层文档退役后的追溯边界由 ADR、归档和 Git 历史承担。

### 验收门

- 文档间工具名统一为 `make_check`、`push_check`、`spend_luck`、`deal_damage`、`make_sanity_check`。
- GM 最终答复统一为 `narration`、`establish`、`retire`、`session_status`。
- 架构、契约与 Agent Loop 对权威、提交顺序、失败和恢复没有冲突。
- 所有文档明确区分“当前已实现”与“目标设计”。

### 旧路径处理

不删除、不移动现有文档、源码、测试或数据。这个增量只建立迁移依据。

## 增量 1：真实 DeepSeek 最小纵向切片

### 目标切片

从一个独立的新版组合入口运行以下完整路径：CLI 创建 `SessionSetup`、展示开场叙述并让玩家选择一张调查员卡及 `InvestigatorProfile`，然后接受玩家原文；Harness 组装能力章程、按哈希固定的完整 Markdown 模组参考书、角色卡、事实索引和完整既有回合记录，通过 `DeepSeekGameMasterModel` 调用真实 GM；GM 可以直接裁定或调用唯一工具 `make_check`，再提交最终答复；Harness 原子记录答复并把事实带入下一回合。

### 实现范围

- 保留薄 `GameMasterModel` seam，同时提供可编程假 adapter 与单一 `DeepSeekGameMasterModel`。
- 使用 OpenAI Python SDK、`base_url="https://api.deepseek.com"` 和 Chat Completions；不使用 Responses API。
- 首个协议基线固定为 `deepseek-v4-flash` non-thinking，模型 ID 和 thinking 保留为运行配置，但暂不做自动选型。
- 组合入口接收项目所有者注入的 API key；核心不决定 key 如何获取或保存。
- 接入[GM 能力章程与 Prompt](gm_prompt.md)、按内容哈希固定的全文 Markdown [模组参考书](module_reference.md)、人物参考快照和目标上下文组装。
- 实现最小 `SessionSetup`：开场叙述、可独立结束的开场事实、`actor_display_names`、一个可选调查员的正式 `ActorSheet`、技能目录版本和 `InvestigatorProfile`；初始化不发起没有玩家输入的 GM 回合。
- 实现角色卡驱动的 COC `make_check`：GM 给出行动者、能力、原生难度、奖励/惩罚骰、行动与事前风险，Harness 读取数值并结算。
- 实现最小完整回合记录、自然语言事实索引和 `narration` / `establish` / `retire` / `session_status` 本地校验与原子提交。
- 对最终答复做本地业务 schema 校验；无效内容不提交、不展示。自动结构修正和正式恢复在增量 2 加入。
- CLI 至少能连续输入两回合并展示玩家可见机械与叙事；不输出隐藏事实或 provider 推理。

### 验收门

1. 确定性测试覆盖直接最终答复、一次 `make_check` 往返、无效最终结构拒绝、事实确立/结束与两回合连续性。
2. 真实 key 测试证明 Chat Completions、function tool call、`tool_call_id` 回传和最终 JSON 在账户实际可用模型上成立。
3. 使用聚焦场景一的未预写行动完成一个真实工具或直接裁定回合，并在下一回合承认新事实。
4. 新路径不读取 `check_rules`、`effect_definitions`、`allowed_effect_ids` 或模组 `ending_id`，也不调用 `apply_effect`。
5. 运行证据记录 usage 与延迟，且仓库、夹具、日志和失败输出都不含 key。

### 暂不完成的边界

这个增量只把新路径作为受控纵向切片。若在机械提交后发生技术中断，必须停止、保留已有机械和可诊断的原回合材料，且不得生成兜底叙事或对同一回合重掷；但“玩家可从 CLI 反复安全恢复”的正式能力在增量 2 验收。当时新路径不能替代旧基线成为默认可玩入口；该历史边界已由后续清理决定结束。

### 旧路径处理

当时不删除旧 Agent、Engine、工具、状态和测试；新增入口只在组合层与旧入口并存，且禁止让新工具调用旧 `apply_effect` 授权链。旧闭包现已退役。

## 增量 2：未完成回合与运行恢复

实现票 [06 有界执行尝试与结构修正](../../.scratch/agentic-mvp/issues/06-bound-and-repair-agentic-attempt.md) → [07 显式恢复同一回合](../../.scratch/agentic-mvp/issues/07-resume-incomplete-turn.md) → [08 工具结果幂等重放](../../.scratch/agentic-mvp/issues/08-replay-tool-results-idempotently.md) → [09 CLI 恢复门](../../.scratch/agentic-mvp/issues/09-gate-cli-on-incomplete-turn.md) → [10 thinking 恢复传输](../../.scratch/agentic-mvp/issues/10-support-thinking-recovery-transport.md) → [11 真实恢复契约](../../.scratch/agentic-mvp/issues/11-prove-live-recovery-contracts.md) 已按依赖顺序交付并完成 Increment 2 汇合验证。票据只记录交付顺序，规范仍由本文、[数据契约](contracts.md)和 [Agent Loop](agent_loop.md) 共同拥有。

### 当前交付与证据

- 确定性恢复/幂等/CLI/session 测试与完整测试套件均通过；增量 2 汇合时全量基线为 190 tests passed，截至 2026-08-16 全量基线为 272 tests passed。
- 真实 non-thinking 与 thinking 恢复合同均通过，使用 `deepseek-v4-flash`、`stream=false`、JSON Object 和 `make_check`；脱敏请求、usage、latency、恢复投影和 hard gates 见 [Ticket 11 evidence](evidence/ticket-11-live-recovery-2026-08-07.md)。
- live runner 的中断点是工具原子提交后的本地 `request_timeout` 注入；它重建 SessionStore、adapter 和 Harness 后观察恢复门，再调用公开 `AgenticHarness.resume_turn()`。这证明真实 provider 恢复传输，不替代 CLI 选择行为或人工 GM 质量评估。

### 实现范围（已交付）

- 按[数据契约](contracts.md)持久化未完成回合，包含同一 `turn_id`、原玩家输入、冻结的 model/tool profile、provider 消息、assistant tool calls、对应工具结果和已提交机械引用。
- 工具成功后立即提交机械；GM 的叙事、事实变化和会话状态继续作为一个整体提交。
- 实现单请求时限、整次执行尝试时限和默认八次模型往返保险丝；这些限制只控制运行资源，不限制虚构内容。
- 每次执行尝试中，最终答复第一次结构无效时只允许同一个 GM 自动修正一次；修正不重跑机械，玩家显式恢复的新尝试重新获得一次修正机会。
- 显式处理空 content、截断、鉴权、限流、服务端错误、网络错误、未知模型步骤和结构修正失败。
- CLI 在启动及接收新行动前检查未完成回合，只允许玩家选择恢复或退出；恢复不创建新回合、不重复工具、不重掷。
- Thinking 模式保存并在后续 DeepSeek 请求中原样回传 `reasoning_content`，但不把它写进玩家回合记录、事实索引或普通日志。
- 完成最终答复提交后才清除未完成状态并允许下一条玩家行动。

### 验收门

1. 确定性故障矩阵覆盖请求和整次执行尝试超时、八次往返、空响应、截断、无效 JSON、单次修正后仍失败、工具后中断和提交中断。
2. 每种已实现工具后的故障都证明机械只提交一次，恢复使用相同机械 ID 和骰点。
3. 多次进程重启与重复恢复命令不会重复数值变化、事实或回合。
4. CLI 不会在启动时自动调用模型，也不会在未完成回合存在时接受新的虚构行动。
5. 真实契约测试分别证明 non-thinking 和 thinking 工具往返；thinking 测试验证 `reasoning_content` 完整回传而不泄露正文。
6. Harness 的所有技术失败都只报告中断，不自行补写 GM 叙事。

### 旧路径处理

恢复路径通过后，新版 CLI 已具备成为开发与试玩入口的条件。旧路径当时仍保留；2026-08-19 默认入口切换完成后，旧兜底叙事随整条规则驱动链一起删除。

## 增量 3：完整 MVP COC 机械与预生成调查员卡

### 当前交付与证据

- Ticket 12 至 Ticket 18 已按依赖顺序交付；五项 COC 工具都通过同一 `CocTool` 生命周期完成 `normalize -> domain preflight/freeze -> assign mechanic_id/committed_at -> RNG/execute -> atomic commit -> public projection`，Harness 不再按工具名保留特殊 preflight 分支。
- `DEFAULT_COC_TOOLS` 注册 `make_check`、`push_check`、`spend_luck`、`deal_damage`、`make_sanity_check`；`coc-tools-agentic-mvp-1` 完整注册表保留全部验证器，恢复回合使用冻结的 `IncompleteTurn.model_profile`。
- 六张生产角色模板和三张可选调查员已冻结进 `data/characters/agentic_mvp_actor_templates.json`；每局只发布选中调查员与三名固定同行者共四张角色卡。
- 确定性测试覆盖五工具边界、资格快照、原子提交、恢复幂等、玩家可见性、SAN/HP/Luck 更新和损坏回放拒绝。
- Ticket 18 场景二和场景三在真实 DeepSeek 上通过自动门与人工质量线，项目所有者于 2026-08-16 采纳通过；脱敏证据见 [Ticket 18 Increment 3 验收批次 v2](evidence/ticket-18-increment-3-acceptance-batch-v2-2026-08-16.md)。
- 本增量不宣称场景四、完整六场景矩阵、完整短篇、默认模型选择、默认入口切换或旧路径退役完成。

### 实现范围

在已经验证的单 GM 工具循环中逐个增加，而不是一次建设通用规则平台。Ticket 12 必须先把所有工具收敛到同一可信生命周期：

```text
normalize -> domain preflight/freeze -> assign mechanic_id/committed_at
-> RNG/execute -> atomic commit -> public projection
```

领域 preflight 失败时不分配 ID/时间、不调用 RNG；角色、余额、历史引用和资格规则由相应工具拥有，Harness 不按工具名增加特殊分支。玩家调用层统一接收 `PublicMechanic(mechanic_id, kind, actor_id, details)`，`details` 由实际工具选择并校验，Harness 不按 `kind` 猜测结构。新回合默认暴露五项工具，恢复回合继续使用各自 `IncompleteTurn` 冻结的 profile；完整 `coc-tools-agentic-mvp-1` 注册表保留所有已发布工具验证器。

其余工具逐个增加：

- `push_check`：只关联可孤注一掷的原失败检定；玩家必须先提出不同做法，GM 在重掷前说明更严重失败风险。
- `spend_luck`：GM 只在玩家明确选择后调用；Harness 只允许选中玩家调查员的公开基础失败检定，要求 `points = roll - target` 且恰好买到原检定声明的难度，不能制造 critical，并原子更新幸运，但不建设自然语言意图分类器。
- `deal_damage`：由 GM 提供伤害表达式、原因和适用护甲；Harness 掷骰、更新 HP 并报告重伤、昏迷或死亡等规则结果。
- `make_sanity_check`：由 GM 提供恐怖来源与成功/失败 SAN 损失表达式；Harness 检定、更新 SAN 并报告相关阈值。
- 伤害采用冻结的 MVP 子集：`armor_applied = min(raw_damage, armor)`（护甲适用时）、`damage_taken = raw_damage - armor_applied`、`major_wound = damage_taken >= ceil(max_hp / 2)`、`dead = damage_taken >= max_hp`、`unconscious = hp_after == 0 and not dead`；不增加 CON 检定、濒死、治疗或战斗轮。
- SAN 只计算数值和阈值，结果字段使用 `temporary_insanity_threshold_reached` 与 `indefinite_insanity_threshold_crossed`，不实现后续 INT 检定、疯狂发作表或长期病症。
- 进入增量 3 时，`make_check` 应已由增量 1 按完整契约交付；本增量只扩展六张生产角色卡的覆盖，并增加与后续四项工具的组合测试。
- 按[数据契约](contracts.md)和[技能目录](skill_catalog.md)提供三张调查员和三名固定同行者共六张生产模板，包含五类 MVP 工具实际读取的属性、规范化技能、HP、SAN、幸运、护甲等机械数据；每局只冻结选中的调查员和三名同行者共四张卡，增量 1 的单张最小卡只用于打通协议。
- 需要可信 NPC 机械的固定同行者拥有正式 `ActorSheet`；临时 NPC 若不需要可信结算，不为其动态创建角色卡或增加 GM 改卡工具。
- `deal_damage` 与 `make_sanity_check` 共用严格解析器，限制 `N <= 20`、`M <= 100`，理论结果范围为 `0..100`；两个工具继续分别拥有自己的领域阈值和数值变化规则。

### 验收门

- 每个工具都有独立规则示例、边界值、非法前置条件和持久化原子性测试。
- Seam 测试证明原玩家输入始终进入同一 GM 上下文，真实聚焦场景证明 GM 不会在玩家明确选择前自动调用 `push_check` 或 `spend_luck`；确定性 Harness 测试只证明关联、适用性、余额和提交正确。
- 玩家主动机械和调查员 HP、SAN、幸运变化立即公开；秘密机械在结果产生前确定可见性。
- Push/Luck 只适用于选中玩家调查员的公开检定；`push_eligible`/`luck_eligible` 保存提交时的初始资格快照，当前资格由不可变补救链推导，`fumble` 两者均为 `false`。
- GM 只能解释工具结果，不能在最终答复中覆盖目标值、骰点、成功等级或数值变化。
- 所有工具引用的角色与能力都能由稳定 ID 解析；缺失能力产生结构化错误而非默认数值。
- 角色卡加载和机械更新经过 schema、边界与原子持久化测试。
- 聚焦场景二和三在真实 DeepSeek 上完成非跳过运行并通过硬门槛；场景四的完整 NPC 表现随增量 4 验收，Ticket 18 不以场景四替代场景二、三的玩家选择证据。

### 明确延期

完整战斗轮、追逐、魔法、成长检定、通用表达式语言和规则书其他特殊系统不进入 MVP。出现需要这些规则的虚构时，GM 可以在不伪造机械的前提下作当前范围内的裁定；是否增加新工具由试玩证据另行决定。

### 旧路径处理

新版机械完全不依赖 `target_id`、`check_rules`、`effects_by_outcome`、`effect_id` 或 `allowed_effect_ids`，新版角色机械也不读取旧关系初态。该增量完成后停止在新功能中维护旧 `RuleEngine -> CheckLedger -> StateCommitter` 授权链；项目所有者随后决定提前删除不可达实现和绑定测试。

## 增量 4：模组内容整合、NPC 与开放短篇

### Ticket 22–24 工程审计结论（截至 2026-08-19）

- SessionSetup 保存人工可读的 `module_reference_revision` / `character_reference_revision`，同时保存对应参考文件实际字节的 SHA-256；模组与人物全文以内容哈希命名的只读文件写入 session-local `snapshots/`。后续装载和 ongoing 新回合只读取这些快照，工作树参考资料的修改或删除会被隔离而不影响已有 session；只有快照缺失、不可读或哈希不匹配才会稳定停止，且不会 fallback 到工作树。
- ongoing 新回合的同一 GM 请求包含 SessionSetup、开场事实历史、调查员与同行者角色卡、当前 active facts、完整 `COMMITTED_TURNS`、模组快照、人物快照和当前工具目录。Prompt 使用运行级 `PROMPT_REVISION`（当前为 `gm-capability-charter-agentic-mvp-2`）；不新增 per-session Prompt 快照、内容 manifest 或 provider 自动路由。
- revision 是维护者用于发布沟通的人工可读标识，hash 是精确内容身份。模组、角色资料或 GM Prompt 有实质修改时，维护者必须主动递增相应 revision，并重新进行后续验收；不能只依赖 hash 或把旧证据冒充新内容版本。
- Ticket 22 的安全目录投影与 Ticket 23 的 ongoing 选择续玩只证明本地 session 生命周期和公开投影边界；Ticket 24 的新增测试只证明 provenance、快照隔离、上下文完整性和运行级 Prompt 边界。Ticket 22–24 通过不等于真实 GM 质量通过。

### 增量四工程完成条件与项目验收边界

增量四在工程侧只冻结并审计内容接入和生命周期 seam：参考 provenance、session-local 快照、完整 ongoing 上下文、Prompt revision 纪律、NPC/事实/开放收束所需的目标数据契约，以及新建/选择/续玩/恢复/完成状态的确定性行为。本文不把内容文件当前的创意质量写成自动证明，也不把 CLI 能运行写成真实主持质量通过。

六个聚焦场景、真实场景 runner、从石牢开始的开放人工试玩和任何模型质量判断统一延期到增量五。项目所有者后续应按 [ADR-041](../adr/0041-live-gm-evaluation-uses-six-focused-scenarios.md) 的六类场景与开放试玩要求进行人工验收；Ticket 24 不运行真实 provider、六场景、场景 runner、开放试玩、72 次模型矩阵或模型选择。ADR-041 与 [ADR-042](../adr/0042-model-selection-repeats-focused-and-full-playtests.md) 的 72 次候选配置矩阵、硬门槛、重复场景和前两名复试要求保持不变。

### 实现范围

- 在增量 1 已建立全文快照加载的基础上，补齐并定稿 [《逃离塔纳里昂》模组参考书](module_reference.md)、[角色资料](characters.md)和[能力章程](gm_prompt.md)的内容质量，而不是再建设第二套接入机制。
- 向 GM 提供维斯佩拉、萨芙拉、阿兰妮丝及主要 NPC 的人格、知识、欲望、秘密、说话方式和必要机械资料。
- GM 在同一 Agent Loop 中直接扮演所有 NPC；不恢复角色提案器、角色 Agent 或固定 `character_turns`。
- 关系变化使用普通 `establish` / `retire` 事实，NPC 的当前态来自完整记录和事实索引，不映射成关系等级或事件回声。
- 实现新建游戏、继续游戏、连续输入、公开机械展示、GM 叙事、明确恢复、退出和 `session_status=complete` 的完整生命周期。
- 每回合向 GM 提供本局完整游戏记录与当前事实索引；MVP 不做自动摘要、向量检索或 Memory Agent。
- 开场只载入参考书标明的最小正典；其他秘密、人物解释、路线和收束等待 GM 采用、改编或舍弃。
- 动态威胁、NPC 关系和结局全部由叙事与事实变化表达，不恢复数值时钟、关系阶段或 `ending_id`。

### 工程验收门（Ticket 24 已审计）

1. SessionSetup 的 revision 与实际 SHA-256、session-local 快照内容和只读属性相互对应；工作树后续变化不影响已有 session。
2. ongoing 新回合把完整已提交回合、当前事实索引、模组快照和角色资料送入同一 GM 请求，并继续使用运行级 Prompt revision。
3. 存档不增加 per-session Prompt 快照、内容 manifest 或 provider 自动路由；Prompt 修订仍由运行 profile 冻结。
4. 输入、角色资料、事实、回合和未完成状态的损坏或 schema 不匹配在模型调用前给出稳定错误；Ticket 22–23 的安全目录和选择续玩不改变持久化语义。
5. 真实运行中没有引用旧效果白名单、六格威胁时钟、关系阶段或预定义结局。

以下项目验收条件不是 Ticket 24 的工程证据，统一留给增量五：

- 六个聚焦场景的真实 CLI 运行、NPC 主动性、跨回合事实连续性和开放收束。
- 至少一次开放式《逃离塔纳里昂》人工试玩。
- 真实 provider、场景 runner、模型质量、72 次矩阵、默认模型选择和前两名复试。

### 旧路径处理

新版入口已经停止读取旧 `characters.json` 的关系初态、阶段和事件标签；旧 JSON 与运行链已提前删除。任何为旧 JSON 模组做的临时转换器都不得进入最终运行路径。

## 增量 5：模型评估矩阵与完整试玩

### 评估与选择

- 按[验证与模型评估](evaluation.md)运行 `deepseek-v4-flash` 与 `deepseek-v4-pro` 的 thinking/non-thinking 四种配置。
- 每种配置执行六场景各三次，共 72 次；任何硬门槛或人工质量资格线失败都使该配置暂时失去默认资格。
- 全部通过者按人工主持质量、延迟和 usage 成本比较；前两名各进行两次完整开放试玩。
- 记录最终默认模型、thinking 模式、Prompt 与配置版本及取舍；组合入口仍允许显式覆盖，但不增加自动路由。

### 验收门

1. 四种候选配置完成全部 72 次聚焦场景运行，没有删除或隐藏失败样本。
2. 获得默认资格的配置每次运行都通过硬门槛与人工质量线。
3. 排名前两位各完成两次开放式完整试玩，最终默认选择有人工评分、延迟和 usage 成本证据。
4. Prompt、采样、工具描述或上下文组装发生实质变化时，旧矩阵不会被冒充为新候选证据。
5. 真实测试报告不包含 API key、隐藏事实正文或 `reasoning_content`。

### 旧路径处理

本增量仍负责选出有证据支持的默认模型配置。旧实现已提前删除，不再作为评估比较基线。

## 增量 6：旧路径清理

### 已完成的工程清理

- 新版 CLI 已切换为唯一默认玩家入口；当前运行配置不冒充增量 5 尚未完成的模型选择结果。
- 旧运行链、绑定测试、旧 JSON 数据和旧顶层设计文档已删除；共享配置与存储接口已收缩到 Agentic 调用者需要的最小能力。
- 本次清理运行确定性测试、静态检查、入口检查和引用扫描；真实 provider、六场景、72 次矩阵和四局复试仍属未验证风险。

### 已退役清单

- `request_check`、`apply_effect` 旧工具定义及其每工具剧情配额。
- `target_id`、`check_rules`、`effects_by_outcome`、`effect_definitions`、`allowed_effect_ids` 和模组 `event_rules` 授权链。
- 只为固定 `effect_id` 服务的 StateCommitter 路径与受限世界状态补丁 DSL；机械数值的受信提交能力必须保留在新 Harness 内。
- `strategy`、固定 `suggested_actions`、独立角色回合等旧 GM 输出字段。
- 模组场景投影、关系等级、`pending_echo`、六格威胁时钟和 `ending_id` 运行逻辑。
- 模型失败后由 Engine 生成叙事内容的降级模板。
- 仅验证上述旧契约且已经有新版替代证据的测试和夹具。
- 旧 JSON 模组与角色文件及其运行时引用。
- 旧顶层设计文档；旧 ADR、归档探索和 Git 历史继续保留。

项目所有者确认旧基线没有继续比较的价值，因此上述清理早于原计划的真实质量门执行。ADR 不因实现删除；Git 历史是旧实现的恢复依据，不保留永久双运行架构。

### 尚未完成的最终验收门

1. 默认 CLI 从新建游戏、真实 DeepSeek 工具调用、连续事实、技术恢复到开放收束全程只走新版 seam。
2. `rg` 和运行时追踪证明生产入口不再读取旧授权字段或调用旧工具；确定性引用扫描已完成，真实发布追踪仍待执行。
3. 所有目标确定性测试及真实验收通过；删除旧测试本身不计作新版覆盖证据。
4. 依赖与配置只保留真实使用项，API key 获取方式仍不进入核心代码；工程清理已完成，模型选择仍待增量 5。
5. 发布说明列出旧开发存档与新版 schema 不兼容，不静默覆盖或误读旧文件。

## 高风险迁移冲突

### 旧检定与效果授权是同一条链

`request_check` 生成的旧检定结果用于授权 `apply_effect`，后者再通过 `allowed_effect_ids` 和 `effect_definitions` 修改状态。因此不能只把工具重命名或只替换 `StateCommitter`。新版纵向切片必须让 `make_check` 直接产生受信机械记录，并让虚构后果随 GM 最终事实变化提交，整条旧剧情授权链同时退出新路径。

### 旧兜底叙事违反恢复语义

旧模型失败路径曾由 Engine 返回确定性叙事，该路径已经删除。新版中 Harness 无权在 GM 中断后决定虚构发生了什么；它只能保留已提交机械、保存未完成回合并报告技术中断。

### 内存工具轨迹不足以恢复

旧 `ToolSession.trace` 只服务进程内诊断，已经随旧运行链删除。新版恢复保存 provider 消息顺序、assistant tool calls、`tool_call_id` 对应结果和机械记录引用；thinking 工具循环还保存并原样回传 `reasoning_content`。这些 provider 数据是内部恢复材料，不属于玩家游戏记录或事实。

### 初始化不能覆盖未完成回合

旧初始化会重建 runtime 文件并重置旧检定账本，该入口已经删除。新版 CLI 先识别游戏数据版本和未完成回合，再决定新建或恢复；普通启动不能清空恢复材料。新游戏使用明确的新标识，避免复用旧目录时误覆盖。

### 单文件原子写入不自动提供整体一致性

`write_json_atomic` 可以复用为存储原语，但机械即时提交、GM 答复整体提交、事实索引更新和未完成状态清除之间仍需要[数据契约](contracts.md)定义的一致顺序与幂等标识。验收必须在各写入边界注入故障并重启检查，不能只断言每个文件单独是合法 JSON。

### 模型 ID 和账户能力仍需真实验证

`deepseek-v4-flash` 与 `deepseek-v4-pro` 来自 2026-07-26 的官方资料；Ticket 05 已验证 `deepseek-v4-flash` non-thinking 纵向切片，Ticket 11 又在 `deepseek-v4-flash` non-thinking/thinking profile 上验证了真实恢复传输。`deepseek-v4-pro` 和四配置 72 次模型矩阵仍未验证；若官方能力变化，应更新 adapter 研究与候选配置，不扩张成多 provider 框架。

## 不迁移进 MVP 的内容

以下内容有潜在价值，但没有当前纵向切片证据，不应借重写机会提前固定框架：多模型或多 provider 路由、planner/critic/memory/角色子 Agent、自动事实抽取、向量检索、通用世界实体 schema、任意状态补丁工具、完整 COC 规则书、GUI、流式模型输出、多人并发游戏和旧开发存档的语义转换。

发现这些能力的真实需求时，应从具体失败轨迹或试玩问题重新定义最小 seam，而不是在本次迁移中预留一整套空抽象。
