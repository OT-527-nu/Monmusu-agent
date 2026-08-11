# 13 — 冻结六张生产 ActorSheet

**What to build:** 玩家开局仍从三张预生成调查员卡中选择一张，但新会话会从版本化模板与技能目录解析并冻结三张调查员和维斯佩拉、萨芙拉、阿兰妮丝共六张生产 `ActorSheet`。本局的可信检定、HP、SAN、Luck 和护甲始终读取冻结副本，不受后续工作树内容变化或 GM 输出影响。

**Blocked by:** 11 — 证明真实恢复契约

**Status:** ready-for-human

- [x] 六个稳定 `actor_id` 都有符合目标契约的生产模板，覆盖五类 MVP 工具会读取的八项属性、规范化技能、HP、SAN、Luck 与 armor；三张调查员卡各自保持调查、交涉或行动侧重，三名固定同行者具备可信机械所需数据。
- [x] 会话初始化验证六张生产模板，但单局只原子冻结选中的一张调查员和维斯佩拉、萨芙拉、阿兰妮丝三张 NPC `ActorSheet`；未选调查员模板不进入本局存档、GM 上下文或玩家可控角色集合。
- [x] 新会话依据冻结版本的技能目录解析固定基础值、派生值和角色覆盖值，把最终数值写入本局 `ActorSheet`；专长键和 setting skill 不被折叠、猜测或动态创建。
- [x] 玩家选择只决定 `selected_investigator_id` 和对应 `InvestigatorProfile`；身份自定义不改变机械值或稳定 `actor_id`，未选调查员不能被当作本局玩家角色控制。
- [x] 会话初始化在任何模型调用前校验模板、目录、角色边界和交叉引用并原子冻结；缺失、重复、越界、未知技能、版本不匹配或损坏输入产生稳定启动错误，不创建部分 session。
- [x] 任何模板或目录 preflight 失败都发生在 session 写入和模型调用之前；初始化不会先创建部分四卡 session 再补错。
- [x] 已有会话只读取自己的冻结 ActorSheet、目录版本和内容快照；修改或移除工作树模板不会静默改变既有角色，也不会在恢复时回退到当前文件。
- [x] GM 只能引用冻结卡中实际存在的角色、属性或技能；未知角色与能力产生结构化错误，不使用默认数值，也不提供运行时建卡或改卡工具。
- [x] schema、边界和公开生命周期测试独立核对六张卡的代表性数值、派生技能、选择/资料分离、快照不变性与原子初始化；现有最小卡 session fixture 仍按其 schema/version 兼容策略得到明确处理。

## Comments

- 2026-08-11：补齐三张调查员和维斯佩拉、萨芙拉、阿兰妮丝六张生产模板。初始化先严格验证模板 schema、六个稳定 ID/role、技能目录版本与封闭键集合、资源边界和显示名交叉引用，再只冻结选中的调查员与三名固定 NPC；未选调查员不进入存档、GM 请求包或可用角色引用。
- 2026-08-11：普通技能按 `coc7e-agentic-mvp-1` 的固定基础值或 `dodge` 派生公式展开，模板覆盖优先；专长键保持完整规范键。`flight` 作为 setting skill 只由维斯佩拉模板显式拥有，其他角色调用得到 `unknown_ability`。冻结卡若注入目录外技能，装载在进入 Harness 前拒绝。
- 2026-08-11：经用户确认升级会话 schema。新会话写 `agentic-mvp-2` 并严格要求四卡；历史 `agentic-mvp-1` 只接受 Increment 1 的寻迹者单卡及旧完整技能集合，不补卡、不改写。缺卡的 `v2` 与四卡的 `v1` 均拒绝；仓库现有 5 个历史 `v1` 会话已实际装载验证。
- 2026-08-11：以 `99507e2` 为固定点完成 Standards/Spec 双轴审查。修复了无 provenance 的单卡兼容、setting skill 被全员补齐、冻结卡可注入目录外技能，以及普通技能/setting skill 文档总则冲突；最终两轴复审均无剩余实现阻塞或明确代码异味。
- 验证：`.venv/bin/python`（Python 3.12.3）；`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`（221 passed）、`PYTHONPATH=src .venv/bin/python -m compileall -q src tests`、目标源码 mypy、Ruff `E4,E7,E9,F,I`、角色模板 JSON 解析与 `git diff --check 99507e2` 均通过。Ticket 13 未运行真实 provider 场景；确定性证据不替代 Ticket 18 的真实场景与人工判断。

**Not in this ticket:** 改写角色叙事、增加更多调查员或临时 NPC 卡、角色成长、装备系统、运行时 GM 改卡，以及实现其余四个 COC 工具。
