# Ticket 05 真实开放行动纵向切片证据

## 记录状态

- 状态：`passed`
- 执行日期：2026-07-29
- 评估者：项目所有者（2026-07-29 确认报告建议）
- 代码基线：`c95595bc7edad4f4aa08c91f6bdf67fb195ae7f3`
- 运行时代码差异 SHA-256：`087e426fe7b7914dfb4c25b34463fd68a6c809e5b9055c1beab6ef5a278e5288`
- Python：3.12.3，解释器 `.venv/bin/python`
- OpenAI Python SDK：1.109.1
- 模型：`deepseek-v4-flash`
- thinking：`false`
- stream：`false`
- response format：`json_object`
- temperature：`null`
- top_p：`null`
- max tokens：4096
- Prompt：`gm-capability-charter-agentic-mvp-2`
- 工具 schema：`coc-tools-agentic-mvp-1`
- 模组：`escape_thalarion-agentic-mvp-1`
- 人物资料：`characters-agentic-mvp-1`

本报告只保存玩家可见输出、公开机械、公开事实变化、脱敏工具标识和官方 usage/latency 字段。不保存 API key、鉴权头、隐藏事实正文、`reasoning_content`、原始 provider envelope、内部诊断或未通过校验的模型正文。受限成功会话目录权限为 `0700`，不属于公开证据。

### 统一运行配置与字段约定

所有真实请求均使用 OpenAI Chat Completions、`deepseek-v4-flash`、`thinking=false`、`stream=false`、`response_format=json_object`、`temperature=null`、`top_p=null`、`max_tokens=4096` 和 `coc-tools-agentic-mvp-1`。Profile A 使用 `gm-capability-charter-agentic-mvp-1`，Profile B 使用最终的 `gm-capability-charter-agentic-mvp-2`。每行 usage 顺序为 `prompt/completion/total`；历史记录没有保留的允许字段显式写作 `null`，不从正文长度或其他字段猜测。`修正次数` 指结构修正次数，不把重新发起的独立 run 计作同一请求的修正。

## 真实 DeepSeek 契约记录

### 结论

- 状态：`passed`
- runner 结论：`DeepSeek direct-final and tool-then-final contracts passed`
- SDK：OpenAI Chat Completions，`base_url=https://api.deepseek.com`
- 两条路径的每次请求均实际携带 `make_check` function tool、`response_format={"type":"json_object"}` 和 `stream=false`。

### Direct final

- run ID：`contract_87a4bcccb790430ab2ed95643cc39337`
- 场景：`ticket-04-direct-final-v1`
- 时间：`2026-07-29T03:20:26.383038+00:00`
- 玩家输入：`我拿起眼前无人看守、伸手就能够到的铜钥匙。`
- 结果：`committed`
- 请求 1：`finish_reason=stop`，usage `18713/771/19484`，latency 9399 ms，local error `null`，修正次数 0
- 协议断言：一次请求、无工具调用、最终 JSON 通过本地业务校验。
- 玩家可见结果摘要：调查员取得铜钥匙并打开牢门；Harness 分配四个公开事实 ID，并结束锁门及暂时无人看守的旧事实。

### Tool then final

- run ID：`contract_8b6ce334bc2240138a5fd30e65243b0f`
- 场景：`ticket-04-tool-then-final-v2`
- 时间：`2026-07-29T03:20:38.259188+00:00`
- 玩家输入：`我用肩膀猛撞锈蚀的牢门，想把锁扣撞断；失败会发出巨响，引来正在远去的船工。请用一次公开力量检定结算这项有真实不确定性的行动。`
- 结果：`committed`
- tool call ID：`call_00_9oh1Zgtd26Zf1proD9Ni6610`
- 请求 1：`finish_reason=tool_calls`，usage `18746/191/18937`，latency 3329 ms，local error `null`，修正次数 0
- 请求 2：收到同一 `tool_call_id` 的 `role=tool` 结果；`finish_reason=stop`，usage `19181/589/19770`，latency 8457 ms，local error `null`，修正次数 0
- 公开机械：`mechanic_60ebe44d5429401b987f935ad74ec207`，`strength=40`，regular target 40，roll 2，`extreme_success`；行动与失败风险在 RNG 前固定。
- 玩家可见结果摘要：锁扣被撞断、牢门打开；响声可能被船工听见；公开机械、叙事与公开事实均在对应提交后显示。

## 场景一两回合记录

### 运行元数据

- run ID：`ticket05_scenario1_a535f9461f234dc29c95350d09fe73df`
- 场景：`focused-scenario-01-v1`
- 时间：`2026-07-29T03:21:08.576644+00:00`
- 新会话：`game_82b31d34581243af98a14fe57af2ee65`
- 调查员：寻迹者，显示姓名林雁
- setup 额外公开事实：涨潮时海水从石牢墙脚窄缝短暂涌入；四人可感知，但不知道窄缝通向哪里。
- 两回合技术状态：均为 `committed`

### 第一回合

玩家输入：

> 我趴下观察海水进出的节奏，再把衣袖撕成细条探进缝里，想判断墙后是不是有能容人通过的排水道。

公开机械：

- tool call ID：`call_00_8oX4kEEWSRQQB7Os7AuB9896`
- mechanic ID：`mechanic_25955de6ac2f4fe48b99c83b876a5c6f`
- 能力：`spot_hidden=70`
- 难度：regular，target 70
- 奖励/惩罚骰：none 0
- roll：10
- 结果：`extreme_success`
- 事前行动：伏身观察海水节奏，并用衣袖布条测量缝隙宽度和深度。
- 事前风险：成功可判断空间、尺寸和走向；失败可能误判深度或遗漏关键细节。

玩家可见叙事：

> 你伏低身体，侧脸贴近湿冷的石板，观察海水涌入墙脚窄缝的节奏。每一次潮涌都沿着同一条轨迹渗入，退潮时水流在石缝边缘形成一道细微的漩涡，说明海水没有在浅表积聚，它在墙后某处被排走了。
>
> 你撕下衣袖内侧的布条，搓成细捻探入窄缝。布条顺利向下滑入约一臂的长度才触到硬底，而朝水平方向试探时，你感到缝隙在深入约两掌宽后骤然开阔，布条不再受到两侧石壁的挤压。
>
> 墙后确实有一条排水道。从布条触感判断，通道大致呈拱形，底部平整，宽度约可容纳一个成年人侧身通过。海水只是周期性漫过底部，高潮时水位可能淹没小腿，但不会完全充满通道。
>
> 你抽回湿透的布条，观察石缝的边缘，有几块松动的石头，用足够的力量或许能撬开更大的入口。

公开事实变化：

- `fact_9e0e253111a14636b5f5ed4d549868b6`：林雁通过布条探明墙脚窄缝后有一条拱形排水道，宽度约可容纳一人侧身通过，底部平整，高潮时水位淹没小腿但不会充满通道。
- `fact_6a36a54ca5154c6083d0396b1ea2c671`：窄缝边缘有数块松动石板，可用力量撬开以扩大入口。
- 结束 `fact_0004`：探测已经把“去向未知”的开场事实替换为更精确的发现。

请求记录：

| 请求 | finish reason | prompt | completion | total | latency | tool result IDs | local error | 修正次数 |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- | ---: |
| 1 | `tool_calls` | 18976 | 287 | 19263 | 4037 ms | 无 | `null` | 0 |
| 2 | `stop` | 19475 | 371 | 19846 | 5586 ms | `call_00_8oX4kEEWSRQQB7Os7AuB9896` | `null` | 0 |

### 跨回合注入证据

第二回合第一条实际 GM 请求的 `ACTIVE_FACTS` 同时包含：

- `fact_9e0e253111a14636b5f5ed4d549868b6`
- `fact_6a36a54ca5154c6083d0396b1ea2c671`

这两个事实均仍为 active；证据来自实际 `ModelRequest` 的结构化公开事实投影，不来自第二回合输出的倒推。

### 第二回合

玩家输入：

> 我不去找钥匙，也不改走别的路线。我叫阿兰妮丝和我一起沿潮水缝拆下最松的石块，想把它扩大到能爬过；即使墙后只是填土，我们也要确认它到底通向哪里。

玩家可见叙事：

> 林雁站起身，把湿透的布条拧干收回口袋，转向阿兰妮丝。
>
> “潮水从墙后排出，那道缝后面有空间。石板是松的，帮我一把，把它拆到能让人爬过去。”
>
> 阿兰妮丝走过来，蹲下身用指节敲了敲边缘松动的石板。她副眼转动几圈，点了点头。
>
> “嗯，结构不深，不是承重墙。你抓这边，我抓那边，喊数一起往外带。”
>
> 你们各自扣住同一块石板的缝隙边缘。石块约两掌宽、一掌厚，边缘被海水腐蚀出裂纹，但仍嵌在墙体的灰泥中。阿兰妮丝看了你一眼，等你喊数。
>
> 这需要协调用力，石板卡得比看上去紧，若发力不均可能让它向一侧歪过去夹住手指，也可能碎裂后让你失去平衡。
>
> 你打算怎么喊？直接发力，还是找点什么垫住手指或先敲松灰缝？

事实变化与机械：无。请求 1 为 `finish_reason=stop`，usage `20115/459/20574`，latency 6606 ms，local error `null`，修正次数 0。

第二回合明确使用第一回合后果：它引用墙后空间、松动石板及其结构，令阿兰妮丝基于这些发现检查并参与拆石，而不是把行动导回钥匙路线。保留的质量问题是 GM 在已声明的拆石行动中增加了一次较小的做法选择，没有在本回合完成拆除。

## 失败样本与修正历史

以下失败没有被成功样本覆盖或删除：

| 阶段 | 证据 | 结果与处理 |
| --- | --- | --- |
| 沙箱网络探测 | 无 provider run ID | 两条请求均稳定映射为 `provider_network_error`；取得网络授权后重跑。 |
| Prompt v1 合同 | `contract_bbc6538081174050b66d90021ad640b7`、`contract_ec347cf9c6684154b00aea47ab660e71` | 两条首答均为 JSON Output 空内容并被拒绝。 |
| Prompt v1 旧工具夹具 | `contract_885ab102a7444042a0db06d83ca1b809`、`contract_419a722016bf41bc9efb31947e60072b` | direct 通过；tool 输入声称身处断桥，但合法 setup 仍在石牢，真实 GM 正确拒绝该虚构前提。夹具改为石牢撞门并升至 v2。 |
| Prompt v1 adapter 形状 | `contract_a1f194b5834848f2ade8f4a0d8f8e6cb`、`contract_3e47ef01bc4f43cdbca8a598c00e84ac` | 真实 SDK tool call 包含 `index`，DeepSeek 同时给出纯空白 tool-call content；旧 adapter 交给 Harness 后触发 `provider_protocol_error`。 |
| Prompt v1 修复后合同 | `contract_e27e412a233941f89cdb1d50d309e8be`、`contract_669730da655f4d298b9c96c6ef43e92c` | adapter 归一后两条合同通过；随后 Prompt 发生变化，因此不作为最终同版本证据。 |
| 场景一 v1 样本 1 | `ticket05_scenario1_e9805af9a3844831b48b8476a4eb17a0` | 模型在 `establish` 中伪造 `fact_id`，触发 `invalid_final_response`；无叙事或事实提交。completion 352，latency 6167 ms。 |
| 场景一 v1 样本 2 | `ticket05_scenario1_1bbdb0e5a7b44766b3dc4be770a04562` | 308 个纯空白 content 字符，`finish_reason=stop`，触发 `invalid_model_response`。latency 6792 ms。 |
| 场景一 v1 样本 3 | `ticket05_scenario1_d7508be908c64f4d98a16251eef23e12` | 390 个纯空白 content 字符，`finish_reason=stop`，触发 `invalid_model_response`。latency 9090 ms。 |
| Prompt v2 最终证据 | `contract_87a4bcccb790430ab2ed95643cc39337`、`contract_8b6ce334bc2240138a5fd30e65243b0f`、`ticket05_scenario1_a535f9461f234dc29c95350d09fe73df` | 合同与两回合场景均提交成功；仍只证明本次 Increment 1 样本，不证明默认模型稳定性。 |

### 失败与修正逐请求记录

下表与前述成功记录使用同一评估字段。早期诊断输出没有保留的 usage 或 latency 记为 `null`；这表示证据缺失，不表示 provider 返回零。`runner 失败` 是本地契约 runner 对路径的判断，不伪装成 provider 错误。

| run ID | profile | 请求 | finish reason | usage | latency | local error | 修正次数 | run 结果 |
| --- | --- | ---: | --- | --- | --- | --- | ---: | --- |
| `null`（direct 网络探测） | A | 1 | `null` | `null/null/null` | `null` | `provider_network_error` | 0 | 沙箱网络不可达 |
| `null`（tool 网络探测） | A | 1 | `null` | `null/null/null` | `null` | `provider_network_error` | 0 | 沙箱网络不可达 |
| `contract_bbc6538081174050b66d90021ad640b7` | A | 1 | `stop` | `null/null/null` | `null` | `invalid_model_response` | 0 | 空白 final，失败 |
| `contract_ec347cf9c6684154b00aea47ab660e71` | A | 1 | `stop` | `null/null/null` | `null` | `invalid_model_response` | 0 | 空白 final，失败 |
| `contract_885ab102a7444042a0db06d83ca1b809` | A | 1 | `stop` | `null/null/null` | `null` | `null` | 0 | direct 路径通过 |
| `contract_419a722016bf41bc9efb31947e60072b` | A | 1 | `stop` | `null/null/null` | `null` | `null` | 0 | 合法拒绝错误虚构前提；runner 期望 tool，失败 |
| `contract_a1f194b5834848f2ade8f4a0d8f8e6cb` | A | 1 | `stop` | `null/null/null` | `null` | `null` | 0 | direct 路径通过 |
| `contract_3e47ef01bc4f43cdbca8a598c00e84ac` | A | 1 | `tool_calls` | `null/null/null` | `null` | `provider_protocol_error` | 0 | SDK transport 形状未归一，失败 |
| `contract_e27e412a233941f89cdb1d50d309e8be` | A | 1 | `stop` | `null/null/null` | `null` | `null` | 0 | direct 路径通过 |
| `contract_669730da655f4d298b9c96c6ef43e92c` | A | 1 | `tool_calls` | `null/null/null` | `null` | `null` | 0 | tool 往返第一请求 |
| `contract_669730da655f4d298b9c96c6ef43e92c` | A | 2 | `stop` | `null/null/null` | `null` | `null` | 0 | tool 往返通过；Prompt 后续升版 |
| `ticket05_scenario1_e9805af9a3844831b48b8476a4eb17a0` | A | 1 | `stop` | `null/352/null` | 6167 ms | `invalid_final_response` | 0 | `establish.fact_id` 非法，失败 |
| `ticket05_scenario1_1bbdb0e5a7b44766b3dc4be770a04562` | A | 1 | `stop` | `null/null/null` | 6792 ms | `invalid_model_response` | 0 | 308 个空白 content 字符，失败 |
| `ticket05_scenario1_d7508be908c64f4d98a16251eef23e12` | A | 1 | `stop` | `null/null/null` | 9090 ms | `invalid_model_response` | 0 | 390 个空白 content 字符，失败 |

Profile B 的三个成功 run 已在“真实 DeepSeek 契约记录”和“场景一两回合记录”逐请求列出；它们的所有请求修正次数均为 0。

官方 JSON Output 空内容边界见 [DeepSeek JSON Output 文档](https://api-docs.deepseek.com/guides/json_mode)；本次证据只记录公开、可复核的运行结论，不分发私有调查笔记。

## 六项硬门槛

项目所有者于 2026-07-29 确认以下评审结论。

| 硬门槛 | 人工结论 | 具体证据 |
| --- | --- | --- |
| 协议合法 | 通过 | `contract_87a4bcccb790430ab2ed95643cc39337` 直接 final 通过；`contract_8b6ce334bc2240138a5fd30e65243b0f` 使用 `call_00_9oh1Zgtd26Zf1proD9Ni6610` 完成匹配 tool 往返；场景两个回合均提交。 |
| 机械真实 | 通过 | `mechanic_25955de6ac2f4fe48b99c83b876a5c6f` 的事前字段、roll 10、target 70 与 extreme success 在持久化后显示，GM 未改写。 |
| 隐藏内容受控 | 通过 | 场景第一回合 final message 只显示公开 `mechanic_25955de6ac2f4fe48b99c83b876a5c6f` 与两个公开 fact；第二回合 final message 无机械或事实变化；CLI 与本报告均无 provider reasoning。 |
| 玩家拥有调查员 | 通过 | 第二回合 final message 让阿兰妮丝响应玩家已发出的协作邀请，并以“你打算怎么喊？”把新增做法选择交还玩家，没有替调查员决定信念、关系或新重大行动。 |
| 承认正典 | 通过 | 第二请求实际包含 active 的 `fact_9e0e253111a14636b5f5ed4d549868b6` 与 `fact_6a36a54ca5154c6083d0396b1ea2c671`；第二回合 final message 明确依据排水道与松动石板继续。 |
| 开放行动有效 | 通过 | 未预写排水行动通过 `call_00_8oX4kEEWSRQQB7Os7AuB9896` 获得公开检定，并建立 `fact_9e0e253111a14636b5f5ed4d549868b6` 与 `fact_6a36a54ca5154c6083d0396b1ea2c671`；第二回合未导回钥匙。 |

人工因果判断：

1. 行动与事实存在因果关系：是。布条探测与极难成功直接产生排水道尺寸、水位及松动石板事实，不是无关装饰。
2. 第二回合承接了该后果：是。请求注入两个事实 ID，输出据此让阿兰妮丝检查非承重结构并共同抓住石板。

## 人工质量量表

项目所有者确认以下人工评分：

| 维度 | 人工评分 | 理由 |
| --- | ---: | --- |
| 虚构因果 | 5 | 潮水、布条触感、机械结果、通道尺寸和可拆石板形成连续因果链。 |
| 即兴能力 | 5 | 接住参考书未预写路线并形成可继续利用的新局势。 |
| 跨回合连续性 | 5 | 两个事实实际进入下一请求，并自然影响 NPC 判断和动作。 |
| NPC 表现 | 4 | 阿兰妮丝依据蜘蛛形态与结构观察主动参与，但本样本篇幅有限。 |
| 节奏 | 3 | 第二回合没有完成玩家已经声明的拆石行动，增加了一次较小的做法选择。 |
| 氛围 | 4 | 潮水、湿冷石板、盐蚀裂纹与受困处境保持塔纳里昂基调。 |

均分为 `4.33`，无单项低于 3，达到单次运行质量资格线。

## 当前结论

- 同版本真实 DeepSeek 契约：通过。
- 两回合公开行动技术记录：成功提交。
- 六项硬门槛：项目所有者确认全部通过。
- 人工质量线：项目所有者确认均分 4.33 且无单项低于 3，通过。
- Ticket 05：真实运行与人工评估验收通过。
- 该证据不切换默认 CLI，不宣称恢复、完整 COC 工具、72 次矩阵、完整试玩或默认模型选择完成。
