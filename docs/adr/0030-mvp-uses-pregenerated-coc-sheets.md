# ADR-030：MVP 使用预生成 COC 角色卡

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-017](0017-make-check-uses-character-sheet-and-coc-difficulty.md) 与 [ADR-023](0023-mvp-coc-mechanics-scope.md)

MVP 提供少量规则合法的预生成调查员卡，玩家选择后可以自定义姓名、称谓、代词、职业表述、外观、简短背景钩子和一件随身小物，并作为独立 `InvestigatorProfile` 冻结进本局。属性、技能、HP、SAN、幸运及其他被 COC 工具读取的机械数据保持在 `ActorSheet`；叙事资料与机械卡分离，两者都不规定调查员的人格或行动选择。

三名队友也拥有机械上需要的结构化角色卡，其人格、秘密和关系继续由叙事参考资料与本局事实表达。MVP 不实现属性生成、职业选择、点数分配等完整角色创建流程；未来角色创建可以作为独立入口增加，不改变 GM Agent Loop。
