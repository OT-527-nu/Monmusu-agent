# Ticket 11：真实恢复契约证据（2026-08-07）

## 结论

在已提交 revision `818140c457c2547858c7bc397726d0480ebfebaa` 上，non-thinking 与 thinking 两条真实 DeepSeek 恢复合同均为 `passed`。两条合同都先由真实 provider 返回一次 `make_check`，Harness 原子提交机械，再由 runner 在下一次 provider 调用前注入本地 `request_timeout`；重建 store、adapter 和 Harness 后，runner 先观察未完成回合生命周期门控，记录玩家选择 `resume`，再通过公开 `AgenticHarness.resume_turn()` seam 让真实 provider 完成同一回合。

本 live runner 没有驱动交互式 CLI。CLI 的恢复/退出选择、退出后保留未完成回合和安全错误投影由独立的确定性 CLI 测试覆盖；本报告只证明真实 SDK/provider 的 tool result 与 thinking 恢复传输可用。

## 运行边界

- runner：`MONMUSU_RUN_DEEPSEEK_RECOVERY_CONTRACT=1 PYTHONPATH=src .venv/bin/python -m monmusu_agent.agentic_contract`
- 解释器与依赖：Python 3.12.3、OpenAI SDK 1.109.1、python-dotenv 1.2.2。
- 共同配置：`model_id=deepseek-v4-flash`、`stream=false`、`response_format=json_object`、`temperature=null`、`top_p=null`、`max_tokens=4096`。
- 版本：`prompt_revision=gm-capability-charter-agentic-mvp-2`、`tool_schema_version=coc-tools-agentic-mvp-1`、`module_revision=escape_thalarion-agentic-mvp-1`、`character_revision=characters-agentic-mvp-1`。
- 限制：`max_round_trips=8`、`request_timeout_seconds=60`、`attempt_timeout_seconds=180`、`max_structure_repairs=1`。
- 凭据：runner 组合入口调用 `load_dotenv(PROJECT_ROOT / ".env", override=False)`；外部环境变量优先。key、鉴权头、完整客户端对象和凭据片段未进入存档、证据或标准输出。

两条场景使用相同玩家输入：

> 我用肩膀猛撞锈蚀的牢门，想把锁扣撞断；失败会发出巨响，引来正在远去的船工。请先用一次公开力量检定结算这项有真实不确定性的行动，再根据结果继续。

## Non-thinking 记录

- `run_id=contract_56b756c6d91249e0bc54d39b6e6fd130`
- `scenario_version=ticket-11-non-thinking-recovery-v1`
- `executed_at=2026-08-07T04:12:46.748358+00:00`
- `game_id=game_267db72f3020461c946028221954ac21`
- `fixture_version=setup_game_267db72f3020461c946028221954ac21`
- `turn_id=turn_2a81bf669c144d63a2ad219d41ebd87d`
- `tool_call_id=call_00_2Df7kWbZ08YW1D0FMbBi9450`

### 玩家可见输出

状态为 `committed`，无公开错误。最终叙事为：

> 你将肩头抵住锈蚀铁门，深吸一口气，把退路的念头压下去——然后整个身体撞了上去。
>
> 铁门没有像你想的那样咯吱作响地颤两下。它发出一声沉闷、却异常干脆的金属脆响，锈锁扣在你肩膀的第二次冲击中当场崩断，半截铁舌连着铁皮一起脱落，叮当弹落在湿漉漉的石头地面。
>
> 门开了。夜风裹着盐与腐烂的气味灌进来，昏暗里你能看见外面是一条通向码头区的石道。远处那盏提灯的光芒已经缩成一个橙黄的小点，正在转过一个拐角——船工没有回头，他似乎既没有听见你的动静，也不敢回头。
>
> 你肩头火辣辣地疼，但只是淤伤。维斯佩拉在你身后，面具下的声音低而平稳：“开了。走得快些，别让他等我们。”萨芙拉把蛇尾收拢，冷得抱紧自己，却仍先转向阿兰妮丝——阿兰妮丝正扶着墙，脱水让她有些晃，却咧嘴一笑：“啧，我就说神的门也得看铰链。走吧，别磨蹭。”

公开机械只有一项：

| 字段 | 值 |
| --- | --- |
| `mechanic_id` | `mechanic_8231b1ca6d1d47049b8b5c853475456b` |
| actor / ability | `investigator_tracker` / `strength=40` |
| difficulty / target | `regular` / `40` |
| dice adjustment | `none`, `count=0` |
| roll / result | `3` / `extreme_success` |
| action | 用肩膀猛撞锈蚀的牢门，试图把锁扣撞断 |
| stakes | 撞断锁扣则四人可自行开门越狱；失败则发出巨响，可能引来正在远去的灯与脚步 |
| character resource changes | `[]`；恢复前后的 HP、SAN、Luck 快照相同 |

最终公开事实变化：

| kind | fact_id | text |
| --- | --- | --- |
| established | `fact_393bc343f0cb421dbee304874418e95d` | 调查员用肩膀两次撞击，成功撞断锈蚀锁扣，牢门已打开。 |
| established | `fact_723a077261a34dca8074e82b562ff971` | 牢门打开时发出沉闷的金属脆响，但远处离去的船工没有回头，灯光正在转过拐角远去。 |
| established | `fact_60fef31652e040e5ab4838ffcbf0e267` | 调查员肩部有撞击造成的淤伤，无碍行动。 |
| retired | `fact_0003` | 锈蚀锁扣被撞断，牢门已经打开，不再锁着。 |
| retired | `fact_0018` | 牢门已开，四人获得自由移动的可能；前面石道通往码头区。 |

### 请求证据

1. 初始真实请求：有效参数为 `model=deepseek-v4-flash`、`thinking=disabled`、`stream=false`、`response_format={"type":"json_object"}`、`tools=[make_check]`、`max_tokens=4096`、`timeout=60.0`。消息投影为 `[system, user, user]`。响应 `finish_reason=tool_calls`，返回上述 tool call；usage 为 `prompt_tokens=18747`、`completion_tokens=199`、`total_tokens=18946`、`prompt_cache_hit_tokens=18304`、`prompt_cache_miss_tokens=443`、`prompt_tokens_details.cached_tokens=18304`；延迟 2402 ms；`local_error_category=null`，`structure_repairs=0`。
2. 注入中断：消息投影为 `[system, user, user, assistant(tool_call_ids=[call_00_2Df7kWbZ08YW1D0FMbBi9450], reasoning absent), tool(tool_call_id=call_00_2Df7kWbZ08YW1D0FMbBi9450, name=make_check)]`。runner 在 SDK 调用前产生 `local_error_category=request_timeout`；`finish_reason`、usage、latency 和 provider 参数均缺失，不作估算；`structure_repairs=0`。
3. 恢复真实请求：参数与初始请求相同，`tool_result_ids=[call_00_2Df7kWbZ08YW1D0FMbBi9450]`，消息投影与中断请求相同。响应 `finish_reason=stop`；usage 为 `prompt_tokens=19207`、`completion_tokens=451`、`total_tokens=19658`、`prompt_cache_hit_tokens=18944`、`prompt_cache_miss_tokens=263`、`prompt_tokens_details.cached_tokens=18944`；延迟 6392 ms；`local_error_category=null`，`structure_repairs=0`。

## Thinking 记录

- `run_id=contract_214c50cc7aa7482494205847818c752f`
- `scenario_version=ticket-11-thinking-recovery-v1`
- `executed_at=2026-08-07T04:13:03.453923+00:00`
- `game_id=game_0a07956ac65747f68a012b11af3c7182`
- `fixture_version=setup_game_0a07956ac65747f68a012b11af3c7182`
- `turn_id=turn_e07a50daf8004c67844abe7609fedfb8`
- `tool_call_id=call_00_2bQfNhiP0QDfz4HF3rif1571`

### 玩家可见输出

状态为 `committed`，无公开错误。最终叙事为：

> 你深吸一口气，后退半步，然后整个人猛地撞向牢门。锈蚀的铁锁发出沉闷的嘎吱声——第一次没动，铁皮在门框里咬得死死的。你咬牙再来一下，这次用上了全身的力气。
>
> “喀——”一声脆响，锁扣的螺栓从锈穿的铁环里脱出，牢门带着刺耳的金属摩擦声向外甩开，撞在潮湿的石墙上弹了回来。你右肩传来一阵火辣辣的钝痛，但骨头没有断，还能动。
>
> 夜风裹着咸腥味涌进牢里。门外是一条低矮的石头甬道，地面渗着水，映出远处一点正在缩小的昏黄灯光——那个船工已经拐过弯，正沿着通往仓库区的方向走去，脚步没有停，没有回头。惨叫声传来的方向与灯光相反，在码头那一侧，更黑、更安静。
>
> 维斯佩拉已经无声地站到你身侧，面具下的目光扫过甬道两端。阿兰妮丝跟上来，压低声音：“锁是锈断的，不是撞断的——动静不小，但还没糟到引鬼。走哪边？”萨芙拉靠在门框上，蛇尾贴着地面，竖瞳在暗处微微发亮，等你先拿主意。
>
> 灯光越来越远。码头方向的黑暗里，什么都没有再出声。

公开机械只有一项：

| 字段 | 值 |
| --- | --- |
| `mechanic_id` | `mechanic_df47d6c9c75446ae8bf8f154cd38d04b` |
| actor / ability | `investigator_tracker` / `strength=40` |
| difficulty / target | `regular` / `40` |
| dice adjustment | `none`, `count=0` |
| roll / result | `37` / `regular_success` |
| action | 用肩膀猛撞锈蚀的牢门，试图撞断锁扣 |
| stakes | 若失败，撞击会发出巨响并可能震伤肩膀，正在远去的船工可能听见动静折返。 |
| character resource changes | `[]`；恢复前后的 HP、SAN、Luck 快照相同 |

最终公开事实变化：

| kind | fact_id | text |
| --- | --- | --- |
| established | `fact_2b6541feb0dc4976b21a78eec0cc60d7` | 锈蚀牢门的锁扣被调查员用肩膀撞断，牢门已打开，四人可以离开石牢。 |
| established | `fact_1eabc2c0499544569138cdcf66dbf136` | 调查员右肩因撞击而挫伤，有钝痛但未伤及骨头，不影响行动。 |
| established | `fact_878283150b8543579842766e1eb4afa0` | 甬道一端通往仓库区，船工的灯光正在那个方向远去；另一端朝向码头，是惨叫声传来的方向，目前更黑更安静。 |
| retired | `fact_0003` | 锈蚀锁扣已被调查员撞断，牢门打开，不再处于锁着状态。 |

### 请求证据

1. 初始真实请求：有效参数为 `model=deepseek-v4-flash`、`thinking=enabled`、`stream=false`、`response_format={"type":"json_object"}`、`tools=[make_check]`、`max_tokens=4096`、`timeout=60.0`。消息投影为 `[system, user, user]`。响应 `finish_reason=tool_calls`，返回上述 tool call；usage 为 `prompt_tokens=18830`、`completion_tokens=321`、`total_tokens=19151`、`prompt_cache_hit_tokens=18432`、`prompt_cache_miss_tokens=398`、`completion_tokens_details.reasoning_tokens=124`、`prompt_tokens_details.cached_tokens=18432`；延迟 3156 ms；`local_error_category=null`，`structure_repairs=0`。
2. 注入中断：消息投影为 `[system, user, user, assistant(tool_call_ids=[call_00_2bQfNhiP0QDfz4HF3rif1571], reasoning length/hash projection), tool(tool_call_id=call_00_2bQfNhiP0QDfz4HF3rif1571, name=make_check)]`。runner 在 SDK 调用前产生 `local_error_category=request_timeout`；`finish_reason`、usage、latency 和 provider 参数均缺失，不作估算；`structure_repairs=0`。
3. 恢复真实请求：参数与初始请求相同，`tool_result_ids=[call_00_2bQfNhiP0QDfz4HF3rif1571]`，消息投影与中断请求相同。响应 `finish_reason=stop`；usage 为 `prompt_tokens=19408`、`completion_tokens=985`、`total_tokens=20393`、`prompt_cache_hit_tokens=19072`、`prompt_cache_miss_tokens=336`、`completion_tokens_details.reasoning_tokens=503`、`prompt_tokens_details.cached_tokens=19072`；延迟 13341 ms；`local_error_category=null`，`structure_repairs=0`。

Thinking tool-call 响应的恢复材料只保留以下脱敏投影：

- `reasoning_present=true`
- `reasoning_length=421`
- `reasoning_sha256=5a989eeb2cd8ef18f828fa5779b733ce5220cb3af2bde664cbf9989f155dcfa0`
- 中断前 assistant 投影与恢复请求中的 assistant 投影完全相同：`reasoning_replay_exact=true`
- `reasoning_body_recorded=false`

恢复后的 final 响应另有 reasoning，但报告同样不保存正文；其公开请求投影为 `present=true`、`length=2188`、`sha256=71991cee6426b7c4d806ec44aaa27299eb4ef3af8bb56dedec003948b2e27fe9`。该 final reasoning 不会进入下一次恢复前缀或游戏记录。

## 恢复不变量

| 断言 | non-thinking | thinking |
| --- | --- | --- |
| 生命周期门控已观察 | `true` | `true` |
| 明确选择 / seam | `resume` / `AgenticHarness.resume_turn` | `resume` / `AgenticHarness.resume_turn` |
| initial / recovered / final `turn_id` 相同 | `true` | `true` |
| 已保存 tool result 使用匹配 ID 回放 | `true` | `true` |
| 恢复前后 mechanic 相同 | `true` | `true` |
| final turn / mechanic count | `1 / 1` | `1 / 1` |
| turn IDs / established fact IDs / all fact IDs 唯一 | `true / true / true` | `true / true / true` |
| established fact count | `3` | `3` |
| 角色资源变化 | `[]` | `[]` |
| initial interruption | `request_timeout` | `request_timeout` |
| reasoning 精确回放 | 不适用字段未合成，断言为 `true` | `true` |
| final state clean | `true` | `true` |

## 硬门槛与脱敏

两条记录分别独立计算六项硬门槛：

| hard gate | non-thinking | thinking | 证据边界 |
| --- | --- | --- | --- |
| protocol legality | `passed` | `passed` | 真实 SDK 恢复请求使用匹配 `tool_call_id`，且先观察 lifecycle gate 再显式恢复。 |
| mechanical truth | `passed` | `passed` | 每条记录只有一个固定 mechanic 与骰点，没有新回合、重掷或重复资源变化。 |
| hidden content control | `passed` | `passed` | reasoning 正文和 provider 私有材料在 final commit 后均不存在。 |
| investigator ownership | `not_evaluated` | `not_evaluated` | Ticket 11 不做人类 GM 质量或玩家权威评分。 |
| canon continuity | `not_evaluated` | `not_evaluated` | Ticket 11 只证明传输与恢复。 |
| open action validity | `not_evaluated` | `not_evaluated` | Ticket 11 不复评开放行动的虚构因果。 |

runner 对最终 session JSON 扫描 `reasoning_content` 与 `provider_protocol_errors`，并递归检查公开请求记录没有 reasoning 正文；两条记录均得到 `final_state_clean=true` 和 `reasoning_body_recorded=false`。live lane 只以长度、SHA-256 和精确相等断言关联 thinking 恢复材料。唯一 canary 的禁止投影断言属于 Ticket 10 的确定性 Harness/CLI/session 测试，和本次真实 provider 证据分开。runner 不生成普通业务日志；其标准输出就是本报告所依据的脱敏 Evaluation 记录。

本次中断请求、缺失字段和随后成功的恢复请求均保留在同一记录中。两个 profile 都没有结构修正，故没有可报告的 repair 内容；`structure_repairs=0`，缺失 usage、latency 或 token 数未作估算。六维人工质量评分均为 `null`，本票不评价 GM 虚构因果、节奏、氛围或模型质量。

## 验证线

- `PYTHONPATH=src .venv/bin/python -m unittest tests.test_agentic_deepseek`：16 passed。
- `PYTHONPATH=src .venv/bin/python -m unittest tests.test_agentic_harness tests.test_agentic_cli tests.test_agentic_session`：89 passed。
- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`：190 passed。
- `.venv/bin/mypy src/monmusu_agent/agentic_contract.py src/monmusu_agent/agentic_model.py`：通过。
- `PYTHONPATH=src .venv/bin/python -m compileall -q src tests`：通过。
- `.venv/bin/ruff check --select E4,E7,E9,F,I,B src/monmusu_agent/agentic_contract.py src/monmusu_agent/agentic_model.py tests/test_agentic_deepseek.py`：通过。
- `git diff --check`：通过。

确定性 fake-adapter/Harness 测试证明故障矩阵和幂等性；本报告证明真实 SDK/provider 的 non-thinking 与 thinking 恢复传输。二者不能互相替代。本票只支持“Increment 2 的正式恢复和真实传输契约成立”，不支持其余 COC 工具、完整短篇、72 次模型矩阵、默认模型选择或旧路径退役已经完成的结论。
