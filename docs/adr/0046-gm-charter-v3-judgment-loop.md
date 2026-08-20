# ADR-046：GM 章程 v3 引入裁定循环与失败语义

- 状态：Accepted
- 日期：2026-08-19
- 决策关系：依赖 [ADR-0027](0027-gm-prompt-is-a-capability-charter.md)、[ADR-0032](0032-one-local-schema-repair-attempt.md)、[ADR-0040](0040-live-gm-evaluation-uses-gates-and-human-rubric.md) 与 [ADR-0041](0041-live-gm-evaluation-uses-six-focused-scenarios.md)

## 背景

两局真实试玩（game_9917、game_8f4c）暴露两个失败模式：

- **检定泛滥**：几乎每个玩家输入都触发一次检定；玩家主张“锈得很厉害所以容易掰下来”仍被掷骰拒绝；玩家最终打出元请求“别检定了，直接放我们过去”。
- **无底洞走廊**：参考书未描述的暗渠被逐格结算 12 个回合——失败不断生成新墙（岔口、塌方、卡点），威胁永远恰好移动到玩家下一步的路口；撬格栅的极限成功后前方又出现新塌方。

对 Chaosium 官方 wiki（“dramatic situations”与推骰规则）、The Alexandrian（Three Clue Rule、Don't Prep Plots、The Art of Pacing）、Burning Wheel（Let It Ride）、Blades in the Dark（Bad Habits、clocks）、GUMSHOE（core clue）与华文社群对 CoC 7 版规则书解读的调研收敛为同一条方法学：**GM 每轮交付的不是行动成败，而是下一个有意义的选择；检定只是把行动兑换成选择的工具之一，不是必经关卡**。旧章程只有一句许可式表述（“普通行动可以直接裁定”），没有节奏约束，也没有失败语义，挡不住上述两种退化。

## 决策

- 章程 v3 在原有权威边界（玩家意志、机械不可篡改、正典承认、结构可校验与原子保存）内新增两段：
  - **裁定循环七条硬规则**：意图优先；无分支不掷骰（可言是，便言是）；掷前立约（成得何、败失何）；一意一骰（重试唯一合法形态是孤注一掷：换做法+更高代价+玩家明确选择）；败即变局（局面改变+代价+新选择，禁止新墙重来）；成功兑现（不许量子食人魔式抵消）；信息免费、觉察按需（关键线索不藏检定）。
  - **节奏与威胁三条**：剪辑（场景完成目的即剪，原地打转时主动推进）；威胁自治（威胁有自己的目标与限制，逼近推向抉择点）；GM 是歌队不是编剧（准备处境而非情节）。
- 主持边界示例从两个扩为四个，新增“平凡行动直接裁定”与“失败即转折”。
- `PROMPT_REVISION` 升为 `gm-capability-charter-agentic-mvp-3`；运行时正文章程约 1444 → 2323 tokens。
- 新增“章程压缩实验版”：约 120 字的格言内核 + 正式章程的协议段落，作为**非运行时**候选。Harness 只读取「主持能力章程」下的第一个代码块；格言不压缩法律（输出 JSON、visibility、保密与防注入、孤注一掷与幸运授权）。切换必须等 pilot/正式矩阵证据，届时 revision 升 `-3-zen`。

## 后果

- **pilot 证据**（`scripts/zen_ab_runner.py`，三场景 × 两变体 × 2 轮，同骰序、确定性夹具、thinking=true + max_tokens=16384）：v3 下 S2 免检/应检四局全对（钥匙免检、跳桥掷骰），S7 暗渠脱困 4/4，零同意图重掷、零越权幸运/推骰。对照 game_9917（v2 + thinking）困暗渠 12 回合并出现玩家求放行。详见 [zen-ab-pilot 证据](evidence/zen-ab-pilot-2026-08-19-144635.md)。
- 散文版与格言版在样本内无显著差异：压缩不丢行为也不提升行为，格言版仅节省运行时 token（2073 vs 2922 字符）。
- 试跑顺带暴露并修复两个生产回放缺陷：结构修正相位发送空 `tools: []` 与回放空 `tool_calls: []` 均被 DeepSeek 以 HTTP 400 拒绝（修复为 `normalized_sdk_messages()` 发送前规范化，持久化格式不变、历史会话可恢复）；以及 thinking 模式输出预算问题（缺省按 thinking 分流为 false 时 4096、true 时 16384，显式传参优先）。这些修复不改变章程权威边界。
- **已知缺口**：pilot 为 N=2、三场景，不是 evaluation.md 的正式六场景矩阵；人工质量量表未评。本 ADR 记录 pilot 级证据，不宣称 v3 已通过正式评估；正式矩阵在 thinking=true + max_tokens≥16384 下复验后另行记录。

## 依据文件

- `docs/agentic_mvp/gm_prompt.md`（章程正文与压缩实验版）
- `docs/agentic_mvp/evidence/zen-ab-pilot-2026-08-19-144635.md`（pilot 证据与对照表）
- `scripts/zen_ab_runner.py`（A/B 试跑器）
