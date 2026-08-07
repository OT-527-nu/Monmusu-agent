# Monmusu Agent

Monmusu Agent 是一个由单一 AI 游戏主持人驱动、NPC 同行者陪伴的克系跑团文字游戏项目。

默认入口仍是规则驱动的 CLI 基线。下一版 MVP 以真实 DeepSeek GM 为核心，让模组成为参考书，并把 COC 规则映射为 Harness 中的可信工具；CLI 是该 MVP 唯一正式玩家界面。

当前设计权威与迁移入口见 [Monmusu Agent 文档索引](docs/README.md)。Agentic MVP 仍在迁移中，运行行为以当前源码和测试为准。

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
uv pip install -e .
python3 -m monmusu_agent
```

运行后会在 `var/` 下创建最小 `game_state.json`、`memory.json` 和 `check_records.json`。

## Agentic MVP 初始化切片

独立的 opt-in 入口可以创建带不可变参考快照的新版会话：

```bash
PYTHONPATH=src python3 -m monmusu_agent.agentic_cli
```

安装项目后也可运行 `monmusu-agent-agentic`。该入口从外部环境读取 `DEEPSEEK_API_KEY`，可用 `MONMUSU_DEEPSEEK_MODEL_ID` 和 `MONMUSU_DEEPSEEK_THINKING` 显式覆盖默认的 `deepseek-v4-flash`、non-thinking 配置；它把会话写入 `var/agentic_sessions/`，展示开场并在同一 GM 会话中持续接收玩家行动，直到本局收束或发生技术中断。显式 thinking 模式仍使用非流式 Chat Completions；工具后的 `reasoning_content` 只作为受限未完成回合恢复材料保存和回传，不进入玩家记录。默认 `monmusu-agent` 入口和旧存档不受影响。

## 测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

## 目录

```text
src/monmusu_agent/   应用代码
data/                MVP 模组与角色数据
tests/               最小测试
docs/                设计文档
var/                 本地运行状态，不提交
```
