# ADR-032：最终答复只允许一次同 GM 结构修正

- 状态：Accepted
- 日期：2026-07-26
- 决策关系：依赖 [ADR-025](0025-mechanics-commit-before-atomic-gm-response.md)、[ADR-026](0026-minimal-gm-final-response.md) 与 [ADR-031](0031-deepseek-through-openai-sdk.md)

DeepSeek JSON Output 只保证合法 JSON，不保证符合 GM 最终答复的业务 schema。Harness 在本地解析并校验最终答复；若结构无效，就把简短、具体的校验错误返回同一个 GM，并允许它在当前执行尝试的原未完成回合中重新提交一次。结构修正请求不提供 function tools（或强制 `tool_choice: none`），只接受最终 JSON；它不调用第二个模型或审核 Agent，不重新运行 COC 工具，也不允许改变已经提交的机械结果。

若该执行尝试的单次结构修正仍然失败，Harness 保留未完成回合并向玩家报告技术中断，不提交部分叙事或事实变化。玩家明确恢复会开启新的执行尝试，并重新获得一次结构修正机会。语义质量、正典一致性与主持表现通过 Prompt、评估和试玩改进，不由这一结构校验步骤审批。
