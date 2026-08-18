# 22 — 提供经过校验且玩家安全的 session 目录投影

**What to build:** Agentic CLI 在进入“从已存在的 session 继续”选择时，能够发现本地 session 目录、逐个校验并提供玩家可安全阅读的摘要。有效 session 可以被选择；损坏 session 不会阻止其他游戏，也不会把内部路径、隐藏事实或 provider 诊断暴露给玩家。

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [ ] 对所有候选 session 逐个执行完整装载校验，只把通过校验的 session 纳入可选择列表；一个损坏或不可读的 session 不会使其他有效 session 消失。
- [ ] 每个可选择摘要显示稳定的 `game_id`、调查员显示名、ongoing/complete 状态、已提交回合数和 `updated_at`；摘要不包含隐藏事实、隐藏机械、provider 消息、API key、内部文件路径或 reasoning content。
- [ ] 未完成回合可以在摘要中被识别，并按 `updated_at` 提供稳定排序；目录扫描不创建 session、不写入存档、不调用模型。
- [ ] 对损坏条目提供稳定、玩家可理解的不可选择提示；错误投影不泄露异常 repr、堆栈或敏感诊断。
- [ ] 公开生命周期测试使用临时真实 session 目录，覆盖空目录、多个有效 session、ongoing/complete/incomplete 状态、一个损坏条目和排序边界，并断言 model request 数为零、session 字节不变。
- [ ] 现有未完成回合恢复、session 装载、provider 配置和全量确定性测试保持通过；本票不实现启动菜单分支或真实 provider 运行。

**Not in this ticket:** 普通续玩菜单、complete session 的返回行为、模组/角色/Prompt 内容修改、模型评估、旧路径切换或旧代码删除。
