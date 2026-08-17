# 19 — 配置 Agentic CLI 的模型提供商

**What to build:** 让 Agentic CLI 在首次运行时交互选择模型提供商，输入 API key，并把 provider、key 与生效 base_url 自动写入项目根目录 `.env`；之后启动检测到完整配置时直接加载并回显 provider，不再询问。增加 `--configure` 显式重新配置入口。所有配置读写只发生在 CLI 组合边界，核心库和 Harness 不读取 `.env`、不提示输入、不保存秘密。

**Blocked by:** 无（可在 Increment 4 之前独立开始）

**Status:** ready-for-human

## References

- [Parent spec](../spec.md): User Stories 1, 42 and 50; Implementation Decisions “Provider adapter”, “CLI boundary” and “Security and privacy”; deterministic/live evidence separation.
- [Migration](../../../docs/agentic_mvp/migration.md): 当前实现迁移清单的 provider seam 与旧路径边界。
- [Data contracts](../../../docs/agentic_mvp/contracts.md): `model_profile`、未完成回合冻结配置与恢复验证。
- [Agent Loop](../../../docs/agentic_mvp/agent_loop.md): 恢复沿用冻结 profile，不要求等于当前新回合默认 profile。
- [ADR-031](../../../docs/adr/0031-deepseek-through-openai-sdk.md), [ADR-034](../../../docs/adr/0034-cli-is-the-only-mvp-player-interface.md), and [ADR-038](../../../docs/adr/0038-player-explicitly-resumes-incomplete-turn.md).

- [x] `agentic_cli.main()` 在读取运行配置前调用 `load_dotenv(PROJECT_ROOT / ".env", override=False)`；外部环境变量优先，`.env` 只补缺。核心库、Harness 和 adapter 不加载 `.env`，仍通过构造参数接收 key 和 base_url。
- [x] 首次判定以 `MONMUSU_PROVIDER` 为配置完成标记：缺失时进入 provider 向导，即使 `.env` 已有 `DEEPSEEK_API_KEY`，也只将其作为可保留项。已有 `MONMUSU_PROVIDER` 且当前 provider 的 key 非空时直接加载，不再显示菜单。
- [x] 向导菜单固定为 `1. DeepSeek 官方`、`2. OpenCode Go`、`3. 其它 DeepSeek 模型提供商`。选 1 或 2 时只询问 key，base_url 使用已知默认并显式写入 `.env`；选 3 时先提示“当前版本仍按 DeepSeek 系列模型调用”，再要求输入 Base URL（示例 `https://your-gateway.example.com/v1`）和 key。
- [x] 已知 provider 默认 base_url 固定为 `DEEPSEEK_BASE_URL=https://api.deepseek.com`、`OPENCODE_GO_BASE_URL=https://opencode.ai/zen/go/v1`；自定义 provider 使用 `MONMUSU_PROVIDER=custom`、`MONMUSU_CUSTOM_API_KEY` 和 `MONMUSU_CUSTOM_BASE_URL`。base_url 只做最小校验：去首尾空白后非空，且以 `http://` 或 `https://` 开头；不自动补路径或改写。
- [x] 写入 `.env` 使用 `python-dotenv` 的 `set_key`，只更新本票拥有的键并保留其他行。key 输入优先使用隐藏输入；如果测试 seam 或平台限制无法隐藏，也绝不回显。不设置平台特定文件权限；错误消息、日志、`repr` 和诊断永不打印 key 或 base_url 的敏感部分。
- [x] 增加 `--configure` 参数：显示当前 provider 和 `API Key：已保存（****）`，同 provider 回车保留旧 key，切换 provider 才要求新 key 和必要 base_url。`--configure` 不创建游戏会话、不调用模型。
- [x] 正常启动已有配置时打印一行 `模型提供商：<显示名>`，不打印 key，不打印完整 base_url。无配置且 stdin 非交互时打印明确错误并返回 `2`，提示运行 `--configure` 或导出环境变量；不进入向导、不卡住管道。
- [x] `compose_deepseek_harness` 根据选中的 provider、key 和生效 base_url 构造同一个 `DeepSeekGameMasterModel` 与 `deepseek_model_profile`；类名保持 `DeepSeekGameMasterModel`，构造函数接受 base_url，对外仍只暴露一个薄 adapter seam。
- [x] 新 `model_profile` 增加 `base_url` 字段并随未完成回合冻结；provider 限定为 `deepseek | opencode-go | custom`。加载器对历史 profile 缺 `base_url` 时按 provider 补默认值：`deepseek -> https://api.deepseek.com`、`opencode-go -> https://opencode.ai/zen/go/v1`；`custom` 无历史档案，不接受缺失。
- [x] CLI 确定性测试使用注入的 read/write seam 和假 adapter，覆盖首次向导、`.env` 合并写入、已有配置直接加载、外部环境变量优先、`--configure` 保留/切换 provider、非交互无 key、base_url 最小校验、key/错误脱敏，以及配置过程 model request 数为零。
- [x] 现有全量确定性测试和静态门禁保持通过；本票不运行真实 DeepSeek 或 opencode-go 请求，也不改变默认入口。

**Not in this ticket:** opencode-go 真实协议证据、thinking=true、模型能力表、自动 provider 路由或 fallback、一般 session browser、默认入口切换、Increment 4 内容整合、旧路径清理。

## Comments

- 2026-08-17：由项目所有者与 agent 的需求拷问形成共识后创建。provider 菜单、`.env` 键名、`--configure`、外部 env 优先、非交互行为和 base_url 冻结规则均已在 ticket 文本中固定。
- 2026-08-17：按 TDD 实现。先新增 `tests/test_agentic_provider_config.py` 并在实现前确认失败；随后接入 `load_dotenv`、ProviderConfig 解析/向导/保存 seam、`DeepSeekGameMasterModel(base_url=...)` 与 `model_profile.base_url` 冻结及历史 profile 补齐。新增 25 项 provider 配置测试，更新 3 项旧 main 流程测试以覆盖 provider 显式配置。
- 2026-08-17：验证通过：`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`（297 tests OK）；`uv run ruff check` 通过；`uv run mypy src` 通过；`git diff --check` 通过。本票未运行真实 DeepSeek 或 opencode-go 请求。
