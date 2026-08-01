# 01 — 从新 CLI 初始化不可变的 Agentic Session

**What to build:** 玩家可以从一个独立的新版 CLI 组合入口选择预生成调查员、填写本局叙事资料并创建目标 Agentic MVP 会话。CLI 只在完整且不可变的开场材料已经原子落盘后展示开场叙述并接受第一条自由文本输入，初始化本身不调用 GM 模型，也不改变旧基线入口。

Blocked by: None — can start immediately

Status: done

## References

- [Parent spec](../spec.md): User Stories 1-4 and 45-47; Implementation Decisions “Session setup and snapshots”, “Investigator and actor data”, “One aggregate and bounded projections”, and “Compatibility boundary”.
- [Migration](../../../docs/agentic_mvp/migration.md): “增量 1：真实 DeepSeek 最小纵向切片”的目标切片、实现范围和旧路径处理。
- [Data contracts](../../../docs/agentic_mvp/contracts.md): “运行聚合 `session.json`”, “会话开场 `SessionSetup`”, “调查员叙事资料 `InvestigatorProfile`”和“机械角色卡 `ActorSheet`”。
- [Agent Loop](../../../docs/agentic_mvp/agent_loop.md): “新回合预检与上下文”。

- [x] 新组合入口创建数据契约定义的最小合法 `session.json` 聚合，而不是临时或过渡 schema；聚合具有明确 schema 版本，并包含 `SessionSetup`、选中的正式 `ActorSheet`、`selected_investigator_id`、冻结的 `actor_display_names`、冻结的 `skill_catalog_version`、事实账本、空的回合集合及合法的初始会话状态。
- [x] 玩家可以选择至少一张预生成调查员卡，并在创建会话时填写允许的身份表达字段；生成的 `InvestigatorProfile` 与机械 `ActorSheet` 分离、引用同一调查员，并在本局中冻结且进入后续 GM 上下文。
- [x] 每个开场事实作为独立的 `FactRecord` 获得稳定 ID、正确的 `opening_canon` 来源和 `opening_fact_ids` 引用，因此后续可以分别 `retire`；初始化不会把模组段落转换成路线、权限、检定或效果表。
- [x] 模组参考书和人物参考在创建会话时保存为按内容哈希寻址的只读 Markdown 快照，`SessionSetup` 固定各自修订与 SHA-256；后续装载只接受这些快照，快照缺失、不可读或哈希不符时在模型调用前稳定停止，绝不回退读取工作树版本。
- [x] 初始化通过原子替换发布完整会话；故障测试证明不会留下可被当作有效游戏继续使用的部分聚合，也不会让 `opening_fact_ids`、角色数据或快照引用处于不一致状态。
- [x] CLI 在接受首条玩家输入前先展示 `opening_narration`；可编程模型替身证明选择调查员、冻结资料和展示开场期间没有模型调用，也没有生成无玩家输入的 `CommittedTurn`。
- [x] 新路径只在组合边界与旧路径并存；旧入口、旧存档和现有测试保持可访问且不被静默转换，新入口尚不成为默认生产路径。

**Not in this ticket:** GM 回合执行、`make_check`、DeepSeek 接入、恢复命令或旧路径删除。

## Comments

- 2026-07-27：已完成新版 CLI 的不可变会话初始化。`SessionSetup` 由 Harness 为每局生成唯一 ID，开场 facts、角色资料和 Markdown 快照在发布前完整校验；bootstrap 装载只接受空回合、无未完成回合的开场聚合。
- CLI 集成测试以会计数且失败的替身替换旧 `GameMasterAgent.run`，完成选卡、冻结资料、展示开场和收集首条输入后仍断言零模型调用与零 `CommittedTurn`。
- 验证：`PYTHONPATH=src python3 -m unittest discover -s tests`（104 passed，`/usr/bin/python3` 3.12.3）、`PYTHONPATH=src python3 -m compileall -q src tests`、JSON fixture 解析和 `git diff --check` 均通过。
