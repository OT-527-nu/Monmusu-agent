# Ticket 20：OpenCode Go 真实契约证据（2026-08-17）

## 结论

在包含 Ticket 20 工作树改动的代码上，OpenCode Go 的 direct-final 与 tool-then-final 两条真实契约均为 `passed`。direct-final 证明 `https://opencode.ai/zen/go/v1` 可以完成一次非流式 Chat Completions 最终答复；tool-then-final 证明网关返回原生 `tool_calls`，Harness 执行 `make_check` 后把匹配 `tool_call_id` 的工具结果回传同一 GM，并提交合法最终答复。

runner 记录的 `git_revision` 为运行时的 `HEAD` `a993479d5604d1b44ed84407e70af423d63c4dbd`；本报告同时注明运行时工作树包含尚未提交的 Ticket 20 实现，因此正式合并后应把该字段视为“运行前 HEAD”，而不是冻结的发布基线。

## 运行边界

- runner：`MONMUSU_RUN_OPENCODE_GO_CONTRACT=1 PYTHONPATH=src .venv/bin/python -m monmusu_agent.agentic_contract`
- 解释器与依赖：Python 3.12.3、OpenAI SDK 1.109.1、python-dotenv 1.2.2。
- provider：`opencode-go`；`base_url=https://opencode.ai/zen/go/v1`。
- 共同配置：`model_id=deepseek-v4-flash`、`thinking=false`、`stream=false`、`temperature=null`、`top_p=null`、`max_tokens=4096`。
- 版本：`prompt_revision=gm-capability-charter-agentic-mvp-2`、`tool_schema_version=coc-tools-agentic-mvp-1`、`module_revision=escape_thalarion-agentic-mvp-1`、`character_revision=characters-agentic-mvp-1`。
- 限制：`max_round_trips=8`、`request_timeout_seconds=60`、`attempt_timeout_seconds=180`、`max_structure_repairs=1`。
- 凭据：runner 组合入口调用 `load_dotenv(PROJECT_ROOT / ".env", override=False)`；外部环境变量优先。key、鉴权头、完整客户端对象和凭据片段未进入存档、证据或标准输出。

## 协议发现与实现边界

- opencode-go 的 `deepseek-v4-flash` 在 `response_format={"type":"json_object"}` 下不会返回原生 `tool_calls`，而是把工具调用写进 JSON content。因此 adapter 对 opencode-go 省略 SDK `response_format`，依赖本地 Harness 校验最终 JSON；`model_profile.response_format` 仍保持 `json_object` 作为本地契约。
- opencode-go 的 assistant 消息使用 `reasoning` 字段而不是 `reasoning_content`。adapter 会把 opencode-go 的 `reasoning` 归一为 `reasoning_content`；缺失或非字符串时补空字符串，再进入回放消息。DeepSeek 官方路径行为不变。
- 请求仍发送 `extra_body={"thinking":{"type":"disabled"}}`。

## Direct-final 记录

- `run_id=contract_41254f943e674f088cb2b8d746738c77`
- `scenario_version=ticket-20-direct-final-v1`
- `executed_at=2026-08-17T02:41:59.450633+00:00`
- `turn_id=turn_77e3d2b3cc5a4c6ea2ebad8c816ba6a7`
- 状态：`committed`，无公开错误，无工具调用。
- 单次请求：`finish_reason=stop`；usage `prompt_tokens=19779`、`completion_tokens=2563`、`total_tokens=22342`、`prompt_tokens_details.cached_tokens=19712`；延迟 `107326 ms`。
- 有效参数投影：`model=deepseek-v4-flash`、`tools=[make_check]`、SDK `response_format` 省略、`stream=false`、`thinking=disabled`、`max_tokens=4096`、`timeout=60.0`。

玩家输入：

> 直接裁定下面的行动，不要调用任何工具，也不要先写检定说明：我拿起眼前无人看守、伸手就能够到的铜钥匙。请直接提交最终答复。

最终叙事摘要：GM 裁定石牢内没有铜钥匙，并让三名 NPC 各自回应；同时确立砖缝松动、远处骨链拖行声等新事实。完整公开叙事与事实变化保留在 runner 脱敏 JSON 中。

## Tool-then-final 记录

- `run_id=contract_4806c8a846124d93a1de32d3e77592b0`
- `scenario_version=ticket-20-tool-then-final-v1`
- `executed_at=2026-08-17T02:42:44.495612+00:00`
- `turn_id=turn_5bb803f48ba34d978491dcab757007db`
- `tool_call_id=call_50094f14004847c8b8d6c59e`
- 状态：`committed`，无公开错误。

玩家输入：

> 我必须用一次 make_check 工具结算“用肩膀猛撞锈蚀牢门，试图撞断锁扣”；在工具结果返回前不要提交最终答复。失败风险是巨响可能引来正在远去的船工。

公开机械：

| 字段 | 值 |
| --- | --- |
| `mechanic_id` | `mechanic_8785c4a3615e498eb34ef6a05cd864d8` |
| actor / ability | `investigator_tracker` / `strength=40` |
| difficulty / target | `regular` / `40` |
| dice adjustment | `none`, `count=0` |
| roll / result | `39` / `regular_success` |
| action | 用肩膀猛撞锈蚀牢门，试图撞断锁扣 |
| stakes | 成功未必能把门撞开；失败时撞门的巨响可能把正在远去的船工引回牢房 |

请求轨迹：

1. 初始请求 `finish_reason=tool_calls`，返回 `make_check` tool call；usage `prompt_tokens=19791`、`completion_tokens=487`、`total_tokens=20278`、`cached_tokens=19456`；延迟 `25220 ms`。
2. 工具结果请求 `tool_result_ids=[call_50094f14004847c8b8d6c59e]`，`finish_reason=stop`；usage `prompt_tokens=20529`、`completion_tokens=922`、`total_tokens=21451`、`cached_tokens=20224`；延迟 `19750 ms`。

最终叙事确认锁扣被撞断、门已打开、船工未回头；公开事实变化包含门锁状态、船工远去和调查员肩伤。完整公开叙事与事实变化保留在 runner 脱敏 JSON 中。

## 安全边界

本报告及 runner JSON 不包含 API key、鉴权头、隐藏事实正文、`reasoning` / `reasoning_content` 正文、原始 provider envelope 或私有诊断。公开记录只包含玩家可见叙事、公开机械、公开事实变化、usage、latency、脱敏 tool call ID 和本地错误分类。
