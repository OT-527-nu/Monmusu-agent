# 23 — 实现 Agentic CLI 的 session 选择与普通续玩

**What to build:** Agentic CLI 在启动时根据存档状态提供恢复、普通 session 续玩和新建游戏的明确入口。玩家可以继续一局已有的 ongoing session，看到足够的公开回顾后提交下一条行动；complete session 只能被识别为已完成并返回菜单，不能追加新的虚构回合。

**Blocked by:** 22 — 提供经过校验且玩家安全的 session 目录投影

**Status:** ready-for-agent

- [ ] 存在未完成回合时显示三项入口：“从上一次未完成的回合继续”“从已存在的 session 继续”“创建新游戏”；不存在未完成回合时显示“从已存在的 session 继续”和“创建新游戏”。
- [ ] 第一项列出所有未完成 session，按 `updated_at` 排序并进入既有显式恢复门；恢复使用原 `turn_id`、原玩家输入、冻结 profile 和已提交机械，不建立新回合。
- [ ] 第二项使用 Ticket 22 的安全目录投影；选择 incomplete session 时仍转入恢复门，选择 ongoing session 时不创建新 session，选择 complete session 时只显示已完成状态并返回主菜单。
- [ ] 选择 ongoing session 后，CLI 先显示调查员、回合数、更新时间、最近一次已提交 GM 叙事和当前有效公开事实；零回合 session 显示冻结的开场叙述。回顾不展示隐藏内容、内部机械、provider 轨迹或 reasoning content。
- [ ] 普通 ongoing session 的下一回合使用当前 CLI provider 配置和当前运行级 Prompt revision；未完成回合继续使用其冻结的 provider、model、Prompt revision、工具 profile 和消息前缀。
- [ ] 菜单、目录、公开回顾、无效选择和 complete 返回路径都不调用模型、不改变存档；Ctrl+C、EOF 和既有退出码/恢复语义保持不变。
- [ ] 公开 CLI seam 测试覆盖有无未完成回合的两种菜单、多个 session 选择、损坏条目、ongoing 续玩、complete 返回、incomplete 转恢复、公开回顾脱敏和零模型调用。
- [ ] 项目全量确定性测试、编译检查、目标静态检查和差异检查保持通过；本票不运行真实场景或模型评估。

**Not in this ticket:** 修改模组、角色或 GM Prompt 的创意内容，增加 session replay/export/delete，provider 自动路由，默认入口切换或旧路径清理。
