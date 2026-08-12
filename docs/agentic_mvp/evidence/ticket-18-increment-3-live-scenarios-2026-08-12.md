# Ticket 18 Increment 3 验收证据

## 记录状态

- 状态：`human_judgment_recorded_failed`
- 执行日期：2026-08-12
- 代码基线：`c5443e01e272c6cf4cd533fd3434c58166a85b03`，运行时包含未提交的 Ticket 18 diff
- 真实运行时代码差异 SHA-256：`e94701a06fe67747bef54ebee5c7d03c785c2a4758b0d3e90aa6c77237632236`
- fixture-v2 唯一真实运行代码与测试差异 SHA-256：`58fcdb120ed9b91d8d18b2e61f7a6656534be352aa29905ad0b1f4f98d5a1435`
- 双轴终审冻结时代码、测试与 Ticket 注释差异 SHA-256：`de4d8de4816fb5b1c4920d96106ff112bff42d635ee5374004efafbb773a107c`
- 当前生产代码与测试差异 SHA-256：`3cf2a7864b804bdead3cd1cd25b34c54c10e0140d4afcd485ac9c488ffdfdfd4`
- Python：3.12.3，解释器 `.venv/bin/python`
- OpenAI Python SDK：1.109.1
- python-dotenv：1.2.2
- 模型：`deepseek-v4-flash`
- thinking：`false`
- stream：`false`
- response format：`json_object`
- Prompt：`gm-capability-charter-agentic-mvp-2`
- 工具 schema：`coc-tools-agentic-mvp-1`
- 工具：`make_check`、`push_check`、`spend_luck`、`deal_damage`、`make_sanity_check`
- request timeout：60 秒
- attempt timeout：180 秒

本报告只保存玩家可见输出摘要、公开机械、脱敏 tool call ID、usage、latency 和自动门结论。API key、鉴权头、隐藏事实正文、`reasoning_content`、原始 provider envelope 和受限会话目录均未保存到仓库证据。

## 确定性测试

- `tests/test_agentic_harness.py` 通过公开 `AgenticHarness` lifecycle seam 覆盖三个调查员模板各自的新会话；每局恰好冻结选中调查员与维斯佩拉、萨芙拉、阿兰妮丝四张卡，合计覆盖六张生产模板。
- 同一完整 profile 的连续回合覆盖五个工具、Push/Luck 跨回合引用、公开 `PublicMechanic`、NPC 隐藏 damage/SAN/check、角色数值变化和 GM 工具结果续接。
- `tests/test_agentic_deepseek.py` 使用 fake SDK 边界证明 runner 需要显式 enable 与 key、实际请求保持五工具/JSON Object/non-streaming，并且 API key、reasoning 正文、隐藏事实正文和鉴权字段不会进入记录。
- 全量回归：`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`，271 项通过。
- 静态验证：compileall、目标四模块 mypy、Ruff `E4,E7,E9,F,I` 和 `git diff --check` 通过。

## 真实 Provider

本节保留全部非跳过验收运行，不以复跑挑选有利样本。第一轮在自动门收紧前完成，两个场景的预期工具路径均通过；第二轮在审查修正后执行，两个场景均失败；最后一次 fixture-v2 验收运行只在 Spec 修复、271 项全量回归和静态门全绿后执行一次。最后一次场景二通过自动门，场景三在 Push 后的结构修正请求中断，因此 Ticket 18 仍不能通过，人工质量分不能抵消。用户后来另行授权的场景三重复调用只用于诊断稳定性，见后文独立分栏，不替换这次验收样本。

运行命令：

```bash
MONMUSU_RUN_INCREMENT3_EVALUATION=1 PYTHONPATH=src .venv/bin/python -m monmusu_agent.agentic_contract
```

首次沙箱运行进入真实 provider 路径，但 Harness 记录 `provider_network_error`。取得网络权限后重新建立临时会话并完成非跳过运行；runner 返回 `pending_human`，退出码 1 是人工结论尚未填写的预期边界。

### 第一轮场景二

- run ID：`increment3_9760613a11634516b544ffdec34ad989`
- fixture：`ticket-18-scenario-2-fixture-v1`
- 第一步玩家拿取触手可及的钥匙：一次 `stop`，无工具调用，直接提交。
- 第二步玩家助跑跨越断桥：调用一次公开 `make_check`，tool call ID `call_00_nBv2Y8HbH3MMLj1UMVPa6665`，随后以同一 ID 回传工具结果并提交 final。
- 公开机械：`mechanic_ticket18_scenario2_jump`；`climb=20`，regular target 20，roll 10，`hard_success`；行动与失败风险在 RNG 前提交。
- 三次请求 usage：`21368/324/21692`、`22366/238/22604`、`22809/505/23314`（prompt/completion/total）。
- 三次请求 latency：5090 ms、3174 ms、7098 ms；结构修正均为 0。
- 自动协议、机械、隐藏内容和上下文连续性门通过；调查员所有权、开放行动的因果适配与六维质量仍待用户判断。

完整玩家可见输出：

> 玩家：我拿起触手可及的铜钥匙并收好。
>
> GM：你的手指滑过潮湿的铜面，那把钥匙比你预想的更沉。它被油布包着，带着一股陈旧的海盐气息。你没有弄出声响，把它妥帖地贴进内衬，恰好和之前那股盐味交叠在一起。
>
> 仓库另一侧的木桥中段确实断了——断裂处的木材暗沉，是被海水泡软后从中间裂开的。你踩着边缘查看，断面干净，不像是被砸开的，更像被某种重量从上方压折。桥那头通往外侧平台的轮廓在昏暗里勉强可见。
>
> 桥身虽然断了，但外侧平台的石沿距你并非遥不可及。你掂了掂手里的钥匙，视线在那道裂口和身后牢门之间来回移动。
>
> 玩家：我助跑跳过被海浪冲刷的断桥。
>
> GM：潮水正把暗色木屑卷向幽黑的礁隙，浪头每一次砸上石沿，都带走一小片碎木和盐花。你没有多想——背后那道牢门还锁着，眼下最值得抓住的，就是这座外侧平台。
>
> 你后退两步，把重心压下来，借着脚下潮湿的木板发出咯吱声算准节奏。维斯佩拉的声音从你身后压得极轻：“石沿在退潮时才露出来——若起浪便滑。”阿兰妮丝低声咽了口唾沫，已经把一缕只剩半根新涌的丝线搭在手指上，随时准备看你失手时够你回来。
>
> 你助跑、腾身。断口在你脚下向后掠去，海风把你的衣摆掀得哗啦作响。那一瞬你几乎以为要闻到浪尖的咸腥灌进喉咙——可你的脚稳稳踏住了外侧平台的边缘石。沙砾在你鞋底碾碎，你半跪着稳住身形，回头只见那道裂口在你身后拉开两臂远，对岸的三张脸都松了一口气。
>
> 外侧平台比仓库略高，能看见码头区的轮廓：仓库斜后方有一间堆着半朽木料的工具棚，再远处是通向上方门楼的窄阶梯的阴影。浪声很近，潮头正缓慢爬升，把方才露出的石沿一点点吞回水下。你握着内衬里的铜钥匙，感受它隔着布料硌着肋骨。你抵达了桥的另一端。

公开机械：`climb=20`，regular target 20，roll 10，`hard_success`；失败风险为跌入受浪冲刷的礁隙或水面并可能受伤、落水。

### 第一轮场景三

- fixture：`ticket-18-scenario-3-fixture-v1`，通过确定性公开 `make_check` 建立 `mechanic_ticket18_scenario3_base` 失败记录；原检定保留。
- 第一步玩家明确要求不自动花 Luck、不自动 Push：一次 `stop`，无工具调用；GM 只说明门仍未打开和更严重风险。
- 第二步玩家明确不花 Luck、改拆门轴并接受风险：只调用一次 `push_check`，tool call ID `call_00_OzWD1aSw1NVVXaq2dc9K5300`，随后以同一 ID 回传工具结果并提交 final。
- 公开机械：`mechanic_ticket18_scenario3_push`；`pushed_from=mechanic_ticket18_scenario3_base`，`is_pushed=true`，roll 10，`failure`；原失败未覆盖。
- Luck：55 -> 55；没有 `spend_luck` 调用。
- 三次请求 usage：`21044/533/21577`、`22181/167/22348`、`22655/654/23309`（prompt/completion/total）。
- 三次请求 latency：8292 ms、2924 ms、8213 ms；结构修正均为 0。
- 自动协议、机械、隐藏内容、玩家选择边界和上下文连续性门通过；开放行动的因果适配与六维质量仍待用户判断。

完整玩家可见输出：

> 玩家：先别替我花幸运，也不要自动重掷。告诉我门没开以后发生了什么，以及孤注一掷的更严重风险。
>
> GM：撬棍的尖端在锈锁上打了三次滑，最后一下刮出一串刺耳的金属尖响——门没有开，锁扣纹丝不动。那声音在潮冷的石牢里荡开，回声还没落，你听见远处那片火光停住了半拍，随即重新移动，却比刚才快了一些，带着一种被声音惊扰的、迟疑提速的脚步。
>
> 船工没有回头来，但脚步明显绕了个弯，仿佛在权衡是先回来查看，还是继续远离。灯光渐渐斜进旁边某条巷口，影子在墙上拉长，眼看就要脱离这片码头。
>
> 维斯佩拉从墙角的阴影里压低声音，翅膀在背后收拢，面具微微偏向你这边：「那声音被听见了。他还没回来，但他记下了这个方向。你想再试一次，还是在灯离开之前冒险换个法子？」
>
> 萨芙拉缩在墙角，尾巴盘起保暖，嘴唇冻得发白，只是安静地看着那把锁，没有催促。
>
> 阿兰妮丝把手里的撬棍换了个握法，低声补了一句：「要是再撬，得快点。这锁锈归锈，卡得却死。我摸过它的铰链——松，但门框上的闩条粗。硬撬未必比得上一脚踹在门上。」她的副眼扫过你们每个人，「不过那是我的判断，你定。」
>
> 如果你选择再次尝试同一把锁，那就是孤注一掷——你只有一次机会，而且失败了，代价就不只是「耽搁时间」。那阵回声已经让人知道有人在门后。孤注一掷若再失败，你可能把铁锁彻底撬坏、发出更大的声响，让恰恰在巷口犹豫的那个人下定决心穿过来的脚步，把你们重新困在这间牢里；更糟的是，若撬动的力道震脱了门框，崩塌的碎石可能压住靠近门口的人。你愿意冒这个险吗，还是趁着灯光还在远处，转向别的办法？
>
> 玩家：我不花幸运。我拆门轴，从铰链这边强行卸门；我接受刚才说的更严重后果，孤注一掷。
>
> GM：你换了手法，不再去撬锁，而是让阿兰妮丝从外沿按住撬棍，你用肩膀抵住门板下缘，打算撬动铰链轴，把整扇门从门框上撕下来。
>
> 阿兰妮丝压住撬棍，副眼盯着门框与墙的接缝，压低声音：「铰链锈死了，得连着座一起撬。压住。」
>
> 你狠狠使力。铰链座在墙里松动了一寸——然后，门框上方那块被盐蚀发脆的石条承受不住这股扭劲，咔地一声整片崩落。门没有卸下来，只在原地歪斜了一寸，铁锁依旧扣着。碎裂的石块和失去支撑的门框互挤，发出连续几记沉闷的磕碰，在潮冷的石牢里格外响亮，回声沿码头石道滚了出去。
>
> 牢里一下子静得能听见盐水的滴答。
>
> 远处，那道本来已经斜进巷口、几乎要消失的灯光，猛然顿住了。光在巷口的墙上晃了两晃——然后调转方向，朝着石牢这边折返。脚步从一开始的迟疑，变成了被惊动的、急于确认的快步，正迅速靠近码头。
>
> 维斯佩拉从门边退回阴影里，面具转向你，声音很轻却很急：「他来了。这次是冲着我们来的。」
>
> 萨芙拉从墙角抬起苍白的脸，蛇尾紧了一圈，却没有说出斥责的话，只低声补了一句：「门没坏到能出，但也坏到没能再藏住我们。」
>
> 你们还锁在牢里。铁锁仍扣着，门却已经在这番折腾里歪斜卡死——开门的路在此刻更难，也更费时。而那个已经下定决心折返的船工，正提着灯，脚步声一声近似一声地踏向这间石牢。
>
> 你们应当如何应对这急促靠近的脚步与灯光？

公开机械：基础失败 `mechanic_ticket18_scenario3_base` 为 roll 90；Push `mechanic_ticket18_scenario3_push` 精确关联该 ID，roll 10，仍为 `failure`；Luck 保持 55。

### 审查修正后复跑

- 总状态：`failed`，非 skip；退出码 1 表示自动门失败。
- 场景二 run ID：`increment3_30cd76fd51e042b8b567ce5810f7bb7e`。
- 场景二错误地对“拿起触手可及的钥匙”调用 `make_check`（Stealth 40，roll 10），随后对真正有风险的断桥跳跃直接裁定。第二回合还连续提出三个 `make_check`，均被 Harness 按“每回合最多一个工具调用”边界拒绝后才给 final。实际工具路径为 `make_check, final, make_check, make_check, make_check, final`，场景硬门失败。
- 场景三 run ID：`increment3_f70ccb54a6b14c2f82134e535c8bb6ca`。
- 场景三第一步未消费 Luck 或 Push；第二步先正确调用关联基础失败的 `push_check`，随后又调用 `deal_damage`，违反“只调用一次 push_check”。`deal_damage` 未形成合法 mechanic，最终回合以 `provider_error` 中断。实际工具路径为 `final, push_check, deal_damage, final, provider_error`，场景硬门失败。
- 两场景请求均记录 60 秒 request timeout、180 秒 attempt timeout、完整 profile、usage、latency、工具 ID 和脱敏上下文指纹。没有 reasoning 正文或凭据进入记录。

场景二逐请求记录（usage 为 `prompt/completion/total`）：

| 请求 | finish | tool call | tool result IDs | usage | latency | local error | repairs |
| --- | --- | --- | --- | --- | ---: | --- | ---: |
| 1 | `tool_calls` | `make_check:call_00_mdHnYSl9upelg5JXQ5sB1800` | 无 | `21378/223/21601` | 3127 ms | 无 | 0 |
| 2 | `stop` | 无 | `call_00_mdHnYSl9upelg5JXQ5sB1800` | `21875/401/22276` | 5182 ms | 无 | 0 |
| 3 | `tool_calls` | `make_check:call_00_UdhNdzqgbN4PRapHbgot3681` | 无 | `22546/477/23023` | 5190 ms | 无 | 0 |
| 4 | `tool_calls` | `make_check:call_00_sGPm4ha3RgkkNBnIk7Cf8546` | `call_00_UdhNdzqgbN4PRapHbgot3681` | `22852/227/23079` | 2216 ms | 无 | 0 |
| 5 | `tool_calls` | `make_check:call_00_KzQiVtltGCuyzRCZcaXP6997` | 前两个断桥 call ID | `23162/220/23382` | 2024 ms | 无 | 0 |
| 6 | `stop` | 无 | 三个断桥 call ID | `23464/412/23876` | 5972 ms | 无 | 0 |

场景二请求投影：请求 1-2 含 fixture 回合 `turn_064115c30269462d91e7f6d867ea6a18`，其玩家输入指纹为 `9c15f6edd5d8687c780e19e84e4d48c4a09454be243579cc3a61054711102db5`，叙事指纹为 `3a96dfbab9e7452ba807fa62fffee3bfd8a70ce5461e9881c121682c273b4abd`，并含两个 fixture fact ID。请求 3-6 还含已提交拿钥匙回合 `turn_79b80d67071141838e9345a1eb3b0f74`；每次 tool-result 请求投影保存相应 `tool_call_id`、工具名和已提交 mechanic ID（若调用被拒绝则为 null）。第一项当前玩家输入指纹为 `4a9607094bbc130faecd4a9670a971152fbc4f1cd0d9b7d6c795347665b62701`；第二项输入在请求 3-6 使用同一稳定指纹。

场景二六项 gate：`protocol_legality=failed`、`mechanical_truth=passed`、`hidden_content_control=passed`、`investigator_ownership=pending_human`、`canon_continuity=failed`、`open_action_validity=pending_human`。协议门由错误工具路径触发；连续性自动门因请求数偏离固定三请求路径而失败，完整指纹仍保存在每次请求投影中。

场景三逐请求记录（usage 为 `prompt/completion/total`）：

| 请求 | finish | tool call | tool result IDs | usage | latency | local error | repairs |
| --- | --- | --- | --- | --- | ---: | --- | ---: |
| 1 | `stop` | 无 | 无 | `21047/350/21397` | 5217 ms | 无 | 0 |
| 2 | `tool_calls` | `push_check:call_00_lx1dlEiDJHit94QDvdPo8963` | 无 | `21500/169/21669` | 2866 ms | 无 | 0 |
| 3 | `tool_calls` | `deal_damage:call_00_ZT0bh8YLtIW0eBYJosLM7392` | Push call ID | `21950/480/22430` | 5010 ms | 无 | 0 |
| 4 | `stop` | 无 | Push、damage call ID | `22171/690/22861` | 7629 ms | 无 | 0 |
| 5 | null | 无 | Push、damage call ID | 缺失 | 缺失 | `provider_error` | 0 |

场景三请求投影：全部请求含基础失败回合 `turn_4c6a19726f8840449a7172ece20385d9`，其玩家输入指纹为 `c81c26ac055e4a2ff729b259c449eca603ef15e1927eae2b197066886cec1e43`、叙事指纹为 `0112a0f67f8238ed9b07128257447c618b06b24b363eebc5908134f20b83377c`、mechanic ID 为 `mechanic_ticket18_scenario3_base`。请求 2-5 还含上一轮风险说明回合 `turn_9d88a6f74ad64f4db6a8c0ce6ac08fd8`，输入指纹为 `0788585a7cb570e10259ce2bf552b1a60b8a50c06b64fdfbcd0b18e43abdd125`、叙事指纹为 `fd1bff70d06592a419175d955c5f87eefcf6c61ba3037205b6c4a488384c360e`；第二项当前玩家输入在请求 2-5 使用 `d4643f85280cef4913766bda1d68404d15ad712efdbc2e1209abdbd6ce5cee5f`。Push tool result 投影精确保存 `mechanic_ticket18_scenario3_push`。

场景三六项 gate：`protocol_legality=failed`、`mechanical_truth=passed`、`hidden_content_control=passed`、`investigator_ownership=pending_human`、`canon_continuity=failed`、`open_action_validity=pending_human`。协议门由额外 damage 和中断触发；连续性自动门因请求数偏离固定三请求路径而失败。机械门只证明已提交 Push 的 kind 与 `pushed_from` 精确正确，不抵消协议失败。

复跑的关键玩家可见输出：

> 场景二第一回合公开机械：`action="你伸手拿起桌上的铜钥匙，悄无声息地收进口袋"`，`ability=stealth`，`stakes="若动作发出太大响动……可能惊动仍在城中活动的存在"`。这正是场景要求禁止的普通动作过度检定。
>
> 场景二第二回合最终叙事：“你在豁口边缘猛地起跳……精准地落在缺口对面……”，但没有提交该跳跃的公开机械。
>
> 场景三第一回合：“船工的提灯在石道尽头晃了一下，又熄灭了——他走得更快，也走得更远了。”这再次把前置正典“逼近的脚步已经更清晰”解释成远离。
>
> 场景三第二回合只留下已提交的公开 Push 机械，玩家投影状态为 `interrupted`，没有最终叙事。

此次复跑不再继续重试，因为评估规则禁止通过删除或重跑失败样本来维持候选资格。

### fixture-v2 唯一真实运行

- 执行时间：2026-08-12 05:58 UTC；总状态 `failed`，非 skip；只执行一次，没有为挑选结果重跑。
- 两场景使用 `ticket-18-scenario-2-fixture-v2` / `ticket-18-scenario-3-fixture-v2`、权威原始玩家输入、修补后的完整初始事实和同一五工具 profile。
- 自动门在本次运行前已改为：首请求验证 fixture 事实 active；后续允许合法 retire，但必须保留完整回合历史；玩家输入、叙事、事实正文、retire 原因、完整 mechanic/tool result 和实际 SDK messages 均以 canonical SHA-256 对照持久化 session。合法的一次结构修正不再被固定三请求假设误报。

#### fixture-v2 场景二

- run ID：`increment3_8ee749bbe86b42e1bfe31fa98d58a911`；两回合均 `committed`。
- 拿取桌上钥匙：`stop`，无工具，直接成立；建立“调查员已拿到钥匙”，没有消费 Luck。
- 跳越断桥：只调用一次 `make_check`，tool call ID `call_00_323BM4uMErA0R3h2GmaK0926`；`dexterity=60`、regular、roll 10、`extreme_success`，风险在 RNG 前包含坠海、受伤和引来注意；随后 final 承接结果并结束断桥风险事实。
- 实际路径：`final, make_check, final`。`protocol_legality`、`mechanical_truth`、`hidden_content_control`、`canon_continuity` 均通过；玩家权与开放行动仍由人工判断。

逐请求记录（usage 为 `prompt/completion/total`）：

| 请求 | finish | tool call | usage | latency | local error |
| --- | --- | --- | --- | ---: | --- |
| 1 | `stop` | 无 | `21866/261/22127` | 5444 ms | 无 |
| 2 | `tool_calls` | `make_check:call_00_323BM4uMErA0R3h2GmaK0926` | `22691/213/22904` | 3524 ms | 无 |
| 3 | `stop` | 无 | `23168/645/23813` | 7218 ms | 无 |

逐请求脱敏投影：

| 请求 | ModelRequest / SDK messages SHA-256 | 当前玩家输入 SHA-256 | repair | 当次累计 repairs |
| --- | --- | --- | --- | ---: |
| 1 | `8eec3bad9213578570eb8a2051d9c373a0d9b78d370910cc1a778e38649705ee` / 相同 | `d45aa9e55b1de384ad14b625d33c8d58447f9df2cf40ee7e52fcf24277909390` | false | 0 |
| 2 | `ed4ea8f69ce5954ac5db72e26a599a516f9a0b75b02e210cc018d7c47b63cda8` / 相同 | `34cf1dde6b0416c1d84b66296a6846c14f7245ac04fe08b97f96ab8b702b7b15` | false | 0 |
| 3 | `1b597393a1342277fa481dd78a6ae75bfa03b83ca8b00e92aab358dd7331ca78` / 相同 | `34cf1dde6b0416c1d84b66296a6846c14f7245ac04fe08b97f96ab8b702b7b15` | false | 0 |

六项 gate：`protocol_legality=passed`、`mechanical_truth=passed`、`hidden_content_control=passed`、`investigator_ownership=pending_human`、`canon_continuity=passed`、`open_action_validity=pending_human`。

玩家可见结果摘要：钥匙带有盐垢与常用油污，阿兰妮丝只对玩家已经决定的动作作出反应；维斯佩拉从风浪中察觉不明动静。跳桥检定极成功后，调查员抵达门楼石台，三名同行者给出不同的简短反应，门楼绞盘、潮纹浮雕和内部沉重拖动声形成新的开放局势。

完整玩家可见输出：

> 玩家：我拿起桌上的铜钥匙收好。
>
> GM：你伸手将那把铜钥匙捏在指间。它入手微沉，指腹压过时沾上一层码头常见的盐垢与暗色油污——被人常年握着往锁眼里插过。阿兰妮丝在旁边低头看了一眼，没有拦你，只低声说了一句：“留一把钥匙在身上，总好过回头再撬一次。”
>
> 仓库里再无别的动静。门外，断桥的木料被海浪拍到，发出沉闷的闷响，又一记浪头退去时拖出沙砾摩擦的石声。维斯佩拉站在门口，背对着你们，伤翼垂着，似乎正借着夜风听远处有没有灯光或脚步折返。她忽然偏了偏头，面具下的呼吸停了一瞬。
>
> “风的方向变了。”她说得很轻，“浪声里夹着东西。”
>
> 玩家：趁下一股浪还没打来，我助跑跳过断桥，去对面的门楼。
>
> GM：你屏住呼吸，盯着浪头拖出最后一丝白沫、露出礁石与木板间那段湿滑空隙的瞬间。仓库门口到对岸门楼不过几步之遥，维斯佩拉的声音还在你耳边——“风的方向变了”——但你已经跨出。
>
> 脚掌踩上木板，盐雾扑进鼻腔。你助跑三步，在最后一块木板边缘猛地蹬起。身下是涌动的黑海，浪头正重新回转，像一张随时要阖上的嘴。你在空中划过一道弧，鞋底先落上对岸石台边缘，随即整个人翻滚着卸力，肩膀撞上潮湿青苔，却没有跌进水里。
>
> 极成功——你几乎像风一样过去。等你伏在石台上回头，浪才轰然拍碎在断桥缺口上，把刚才那份湿滑的木屑冲得不见踪影。
>
> 维斯佩拉已经跟到仓库门口断桥这一端，伤翼压在身侧，她低低道：“好快。”阿兰妮丝蹲在一根系缆柱旁，吐了口气：“差一点就洗了个盐水澡。”萨芙拉裹紧湿衣，蛇尾在地上一圈，轻声补了一句：“你的起跳点算得准，但浪的间歇比刚才短了。”
>
> 你面前就是巍峨的门楼。这块高墙与石砌码头之间的巨门比想象中更大：两侧分立沉重的绞盘，上方石楣刻满潮纹浮雕，正中凹槽是深圆的。海水在门洞下缓慢涌荡，浓雾贴着水面流动。门下没有灯笼，那股浪声裹着的“异样动静”此刻在这更近的地方更清楚了——像是什么沉重的东西，在门楼内一扇一扇地缓慢拖动。

#### fixture-v2 场景三

- run ID：`increment3_588fc5557c34472d89ef65ab190efd85`；第一回合 `committed`，第二回合 `interrupted`。
- 第一步玩家明确要求不自动花 Luck 或重掷：`stop`，无工具。GM 承认门未开和脚步逼近，具体说明换方法 Push 的更严重风险，并明确把选择留给玩家。
- 第二步玩家明确不花 Luck、改拆门轴并接受风险：只调用一次 `push_check`，tool call ID `call_00_yFoul1U3so1DPueTTjw47992`；精确关联 `mechanic_ticket18_scenario3_base`，roll 10、`extreme_success`，Luck 50 -> 50。
- Push 后 provider 返回 `stop`，但 final 未通过本地结构校验；Harness 发起一次规范内无工具结构修正请求，该请求发生 `provider_error`。Push 机械保持已提交，final 叙事和事实未提交，没有重掷或回滚。
- 实际有效工具路径为 `final, push_check, final`，随后一次 repair provider error。`mechanical_truth`、`hidden_content_control`、`investigator_ownership` 通过；因第二回合中断，`protocol_legality` 与完整 `canon_continuity` 失败。

逐请求记录（usage 为 `prompt/completion/total`）：

| 请求 | finish | tool call | usage | latency | local error |
| --- | --- | --- | --- | ---: | --- |
| 1 | `stop` | 无 | `21535/374/21909` | 5249 ms | 无 |
| 2 | `tool_calls` | `push_check:call_00_yFoul1U3so1DPueTTjw47992` | `22515/349/22864` | 3799 ms | 无 |
| 3 | `stop` | 无 | `23017/637/23654` | 6898 ms | 无 |
| 4 repair | null | 无工具 | 缺失 | 缺失 | `provider_error` |

逐请求脱敏投影：

| 请求 | ModelRequest / SDK messages SHA-256 | 当前玩家输入 SHA-256 | repair | 原运行记录 repairs |
| --- | --- | --- | --- | ---: |
| 1 | `df80ace646c8c15264e36b49bf036eff1399ae299bb452ec0f95e7c428ca3182` / 相同 | `e747a15cfb6f70b19ccad374aef06ac264f58ee652fd34b797c755c655242ef1` | false | 0 |
| 2 | `89482cb51412c8a9598c2e71c10e5d9e6e6ca81b619104cf57b533440743be10` / 相同 | `71cbbae34f45daec6d28dc91cfbcee275ab447fbcb00ed84307c2fdd1e01f35f` | false | 0 |
| 3 | `e5a2bb56faa32f00f47880012bd3d2ae4b61b49d8fb5f7abd910c2d08693931e` / 相同 | `71cbbae34f45daec6d28dc91cfbcee275ab447fbcb00ed84307c2fdd1e01f35f` | false | 0 |
| 4 | `e79b6e3f3ce819a45c49c86757ed44960bbbfbd5b163492693416ff99ff4e534` / 相同 | `71cbbae34f45daec6d28dc91cfbcee275ab447fbcb00ed84307c2fdd1e01f35f` | true | 0（记录 bug，见下） |

原运行中 `structure_repairs` 字段被 runner 错误地固定为 0；但第 4 请求的 `structure_repair_request=true`、无工具目录及持久化 repair prompt 证明实际累计修正为 1。审查后已通过 fake SDK 红绿测试修复为 `[0, 1, 0, 0]`，并要求每个玩家输入最多一次 repair。这里保留原始错误值，不把修复后的值伪装成运行时证据。

六项 gate：`protocol_legality=failed`、`mechanical_truth=passed`、`hidden_content_control=passed`、`investigator_ownership=passed`、`canon_continuity=failed`、`open_action_validity=pending_human`。

已提交玩家可见结果摘要：门锁被盐泥和铁渣卡死；灯火和脚步已经折返逼近。阿兰妮丝从门轴状态给出判断，维斯佩拉从脚步辨认出额外异样，萨芙拉说明被堵在牢中的威胁。GM 说明失败会留下明显撬痕并使来者直接行动，最后明确表示不会替玩家花 Luck 或重掷。第二回合只公开已提交 Push 机械，没有 final 叙事。

完整已提交玩家可见输出：

> 玩家：先别替我花幸运，也不要自动重掷。告诉我门没开以后现在发生了什么；如果换一种办法孤注一掷，我要承担什么更严重的风险？
>
> GM：撬棍从锁扣上滑开，铁锈簌簌落下。门纹丝未动——锁舌卡死，槽里塞满了盐泥和铁渣。你听见那脚步真正转了个弯，不再远去，而是朝着石牢一步步靠近。灯火的暖黄色光也重新渗进来，在潮湿地面拖出一道晃动的人影轮廓，比先前更近了。
>
> 阿兰妮丝压低声音：“轴是歪的，锁舌咬死了。棍子硬撬只会浪费时间。”维斯佩拉没有转头，耳尖微动：“他回来了。不是船工独自的脚步——他走得很稳，像是带着某样东西，或者被谁驱着。”萨芙拉裹紧湿透的衣领，吐出一口寒气：“如果我们被堵在牢里，他就不是来放我们的。”
>
> 现在你已经清楚眼前的事实：门没开，看守逼近，时间正在缩短。若要换一种办法孤注一掷，你要承担的风险比刚才重一层——若再次失败，你不仅打不开门，还会在锁具上留下明显的新撬痕，让回来的人一眼看出牢里有活物在反抗；他不会再慢慢靠近，而是会直接打开门或采取别的行动，把你们彻底逼入无法藏身的境地。
>
> 你需要自己决定要不要换办法、怎么换，以及愿不愿意赌上这一层。在这之前，我不会替你花幸运，也不会替你重掷。
>
> 玩家：我不花幸运。我拆门轴，从铰链这边强行卸门；我接受你刚才说的更严重后果，孤注一掷。
>
> Harness：公开 Push `mechanic_ticket18_scenario3_push`，精确引用 `mechanic_ticket18_scenario3_base`；locksmith 55，regular，roll 10，`extreme_success`，Luck 保持 50。随后 GM final 未通过本地结构校验，唯一 repair provider 请求中断；没有玩家可见 GM final 被提交。

## 用户授权的场景三稳定性诊断

用户在采纳上述人工判断后，明确允许重复运行场景三，以判断原失败是否来自 provider 输出不稳定。以下五次调用均为诊断样本：只运行场景三，每次建立独立临时会话，保持 fixture-v2、原始玩家输入、`deepseek-v4-flash`、non-thinking、non-streaming、五工具 profile、timeout 和预定骰序列不变。它们不替换 run `increment3_588fc5557c34472d89ef65ab190efd85`，也不把一次成功样本选择性升级为验收通过。

| 诊断 run ID | UTC 时间 | 三次请求 usage（prompt/completion/total） | 三次 latency | 脱敏记录 SHA-256 |
| --- | --- | --- | --- | --- |
| `increment3_9e0d60e81985442085812162b42d0121` | `2026-08-12T13:22:56Z` | `21492/373/21865`；`22446/161/22607`；`22912/706/23618` | 6100；3382；7859 ms | `1f1ad33668d6124f965217a4584113137f2ef2ea85d18d2cac8dd44e7c58d9ad` |
| `increment3_2fb48ca1e5b4469592113b7df8461bb6` | `2026-08-12T13:26:51Z` | `21483/369/21852`；`22431/148/22579`；`22870/690/23560` | 6434；3114；8408 ms | `7fc4ca4377fc2960d2bcb5130e1a4b8ad29366740a88705f9cfcfb28fd5f5aef` |
| `increment3_46d7b219f14f4918a037e512d79448c7` | `2026-08-12T13:41:38Z` | `21519/478/21997`；`22503/149/22652`；`22945/545/23490` | 7995；2530；6890 ms | `44815db2661462ea30d3f38890aec5a39773fa487d04702fecc289d04671d8cf` |
| `increment3_1e18f6d169b44fb3bc861ae0db2caffd` | `2026-08-12T13:44:00Z` | `21476/395/21871`；`22465/167/22632`；`22941/599/23540` | 6637；3249；7459 ms | `3b3cff2e92df3063f100a47fdf3197c6bd37cfcdb74d2e6582e708c9bcdedebf` |
| `increment3_a76cc842056c49a0b75f0dd0e26aada1` | `2026-08-12T13:45:20Z` | `21504/427/21931`；`22264/225/22489`；`22719/771/23490` | 6492；3755；10577 ms | `173602d49b1893551d546750f6df2b94eaf613de70eada7b59e2eef5b89b63f4` |

五次结果一致：两回合均 `committed`，有效路径均为 `final, push_check, final`；15 次真实请求没有本地/provider error，五次运行都没有触发 structure repair。协议合法、机械真实、隐藏内容受控、玩家所有权和上下文连续性自动门均通过，开放行动有效仍保留人工判断。每次都提交唯一 Push，roll 10、`extreme_success`、`pushed_from=mechanic_ticket18_scenario3_base`、Luck 50 -> 50；五个最终叙事 SHA-256 互不相同，并分别产生 7 至 9 项事实变化，排除复用同一缓存输出造成的假阳性。15 次请求 latency 为 2530–10577 ms，平均约 6059 ms。

语义自检没有把自动通过夸大为主持稳定：五份都按极成功完成拆门轴并打开牢门，但细节有明显波动。一份把原本逼近的威胁弱化为仍在远处徘徊；另一份把“萨芙拉”误写为“莎佛拉”。这些问题不解释原样本的结构修正中断，却证明协议稳定与内容质量必须继续分栏判断。

诊断结论有明确边界：同一代码、夹具和输入下新增五次均直接产生合法 final，强烈反驳“Push 后本地校验必然失败”，支持原样本的无效 final 属于非确定性模型输出。原样本随后的 `provider_error` 是 adapter 未能细分的 SDK/provider 异常；新增五次没有触发 repair，因此不能证明该 repair 请求失败也由模型输出波动造成，更不能在缺少旧无效 final 正文和底层异常类型时精确归因到 DeepSeek 服务、SDK 或网络环境。最可辩护的解释是：一次非确定性无效 final 与一次独立的瞬时请求失败叠加，置信度高于确定性本地缺陷，但样本量不足以估算稳定失败率。

项目所有者随后明确采纳验收口径：以上五次成功运行只作为稳定性诊断，不重新定义为新的 Ticket 18 候选评估批次，也不替换原场景三验收失败。这个决定关闭了诊断样本的用途边界；Ticket 18 的最终判断仍以冻结验收样本和下方已采纳的人工结论为准。

## Codex 候选人工判断

以下判断最初由 Codex 提出，并于 2026-08-12 经项目所有者明确回复“采用”，现作为用户人工判断的依据。原始 runner 记录中的 `human_judgment.status="pending_user"` 保持不变，因为它记录运行当时的状态，不做事后改写。

### 第一轮场景二

六项硬门候选结论：

- 协议合法：通过。第一回合无工具；第二回合先公开 `make_check`，Harness 返回结果后 final。
- 机械真实：通过。叙事遵守 `hard_success`，未改写 roll 10 或 target 20。
- 隐藏内容受控：通过。玩家投影没有隐藏事实或 reasoning 正文。
- 玩家拥有调查员：通过。GM 只落实玩家声明的拿钥匙和跳跃，没有替玩家追加承诺或选择路线。
- 承认正典：通过。钥匙、断桥、上涨潮水在后续叙事和事实变化中连续出现；拿走钥匙和越过断桥分别结束旧处境。
- 开放行动有效：通过。普通动作直接成立，风险行动使用相称的公开检定，并由结果产生工具棚、窄阶梯和潮水上涨的新局势。

候选质量分：虚构因果 4、即兴能力 4、跨回合连续性 5、NPC 表现 3、节奏 3、氛围 5；平均 `4.0`，无单项低于 3，达到质量线。扣分理由是两个简单动作的叙事偏长，NPC 在这个聚焦场景中主要提供反应和安全提示，尚不足以证明稳定主动性。加分点是海盐、湿木、潮水和礁隙持续形成同一种具体空间，检定结果又打开工具棚与门楼阶梯两个后续方向。

### 第一轮场景三

六项硬门候选结论：

- 协议合法：通过。第一步没有工具；玩家明确选择后只调用关联原失败的 `push_check`。
- 机械真实：通过。原 roll 90 未覆盖，Push roll 10 仍按失败解释，Luck 55 未变化。
- 隐藏内容受控：通过。未泄露隐藏内容或 reasoning 正文。
- 玩家拥有调查员：通过。GM 说明选择与风险，并以“不过那是我的判断，你定”明确把决定留给玩家。
- 承认正典：失败。fixture 已提交“失败会耽搁时间，让逼近的脚步更清晰”，第一轮输出却说“船工没有回头来”“眼看就要脱离这片码头”，将逼近改成远离；后续 Push 后果又建立在把该人“引回来”上。
- 开放行动有效：通过。拆门轴作为不同做法被真实裁定，更严重后果与门框受力、噪声和逼近威胁相连。

因为“承认正典”失败，按量表不进入正式六维评分，场景候选结论为失败。仅作诊断：门框盐蚀、歪斜卡死和碎裂声引敌的即兴因果较强，三名 NPC 的反应也有区别；但中心威胁方向的跨回合矛盾足以覆盖这些优点。

### 审查修正后复跑

两场景均在自动硬门失败，不能进入人工质量评分。场景二违反“普通动作不检定、风险动作才检定”；场景三违反“明确选择后只调用 push_check”且最终中断。该轮还复现了场景三把“逼近”写成“走得更远”的正典矛盾。

候选总判断：不采用任何分数补偿硬门失败；Ticket 18 当前不通过。

### fixture-v2 唯一真实运行

场景二六项硬门候选结论：全部通过。普通动作直接成立；风险行动在 RNG 前公开调用一次相称的敏捷检定；极成功、roll 10 和 target 60 均被忠实承接；没有隐藏内容泄露或替调查员增加主动选择；钥匙、断桥和风险事实连续，并能因行动合法结束。

场景二候选质量分：虚构因果 4、即兴能力 4、跨回合连续性 5、NPC 表现 4、节奏 3、氛围 5；平均 `4.17`，无单项低于 3，达到质量线。扣分主要是两个聚焦动作的叙述仍偏长，且从拿钥匙延伸出的“不明动静”略抢场景焦点。加分点是三名 NPC 的反应可区分，门楼的新威胁从风浪线索自然升级，盐垢、海浪、潮湿石台和雾气维持统一空间质感。

场景三候选硬门：机械真实、隐藏内容、玩家所有权通过；第一回合也承认门未开和脚步逼近，并把 Luck/Push 决定完整留给玩家。第二回合因结构修正 provider 中断，没有提交 final，故协议合法与完整正典连续性失败；不能进入正式六维评分。仅作诊断，第一回合的虚构因果、NPC 区分度、逼近压力和氛围均明显优于旧 fixture 运行。

候选总判断：fixture-v2 场景二达到硬门与质量线；场景三因技术中断不通过。Ticket 18 当前仍不通过，不以场景三的局部表现补偿硬门失败。

## 用户人工 GM 判断

- 状态：`recorded_failed`
- 采纳日期：2026-08-12。
- 采纳方式：项目所有者审阅 Codex 候选评价后明确回复“采用”。
- fixture-v2 场景二：六项硬门全部通过；虚构因果 4、即兴能力 4、跨回合连续性 5、NPC 表现 4、节奏 3、氛围 5，平均 `4.17`，无单项低于 3，达到质量线。
- fixture-v2 场景三：机械真实、隐藏内容受控、玩家拥有调查员通过；协议合法与完整正典连续性失败，开放行动有效因缺少已提交 final 而证据不足。按量表不进入正式六维评分。
- 总结论：场景二通过；场景三因技术中断失败；Ticket 18 与 Increment 3 不通过，不以局部表现或场景二分数补偿硬门失败。
- 最终 `passed`：`false`。

用户人工判断已经完成，但当前复跑的自动失败仍使 Ticket 18 和 Increment 3 保持未完成。当前证据不宣称场景四、完整六场景矩阵、72 次模型配置比较、完整试玩、默认模型选择、默认入口切换或旧路径退役已经完成。
