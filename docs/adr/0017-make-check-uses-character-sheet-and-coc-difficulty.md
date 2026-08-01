# ADR-017：检定读取角色卡并使用原生 COC 难度

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-006](0006-gm-chooses-mechanics-harness-resolves.md) 与 [ADR-010](0010-tools-expose-coc-semantics.md)

首个 `make_check` 工具由 GM 提供行动者、技能或属性名称、常规/困难/极难难度、奖励或惩罚骰，以及行动和事前风险说明。Harness 从正式角色卡读取真实数值，验证能力存在，掷 d100，计算成功等级并保存不可重掷的机械记录；GM 不提交目标值、骰点或结果。

`make_check` 不接收模组 `target_id`、`rule_id`、`effect_id` 或任意数字修正，也不预先携带剧情效果。检定对世界造成的具体影响由 GM 根据事前风险、虚构情境与机械结果，在最终回合答复中裁定并记录为世界事实变化。
