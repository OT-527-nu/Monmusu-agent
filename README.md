# Monmusu Agent

Monmusu Agent 是一个由单一 AI 游戏主持人驱动、NPC 同行者陪伴的克系跑团文字游戏项目。

默认入口是 Agentic CLI。MVP 以真实 DeepSeek GM 为核心，让模组成为参考书，并把 COC 规则映射为 Harness 中的可信工具；CLI 是唯一正式玩家界面。完整短篇、模型矩阵和开放试玩仍是未完成的质量验收工作。

当前设计权威与迁移入口见 [Monmusu Agent 文档索引](docs/README.md)。Agentic MVP 已完成 Increment 1 至 Increment 4 的工程切片（五工具 COC 机械、生产角色卡、会话续玩和内容发布边界），真实六场景、完整短篇、模型矩阵和默认模型选择仍未完成；运行行为以当前源码和测试为准。

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
uv pip install -e .
python3 -m monmusu_agent
```

运行后会在 `var/agentic_sessions/` 下创建新版会话目录；旧 `game_state.json`、`memory.json` 和 `check_records.json` 不会被读取或转换。

## Agentic MVP 初始化切片

入口可以创建带不可变参考快照的新版会话：

```bash
PYTHONPATH=src python3 -m monmusu_agent.agentic_cli
```

安装项目后也可运行 `monmusu-agent` 或兼容名称 `monmusu-agent-agentic`。入口从外部环境读取 `DEEPSEEK_API_KEY`，可用 `MONMUSU_DEEPSEEK_MODEL_ID` 和 `MONMUSU_DEEPSEEK_THINKING` 显式覆盖默认的 `deepseek-v4-flash`、non-thinking 配置；它把会话写入 `var/agentic_sessions/`，展示开场并在同一 GM 会话中持续接收玩家行动，直到本局收束或发生技术中断。显式 thinking 模式仍使用非流式 Chat Completions；工具后的 `reasoning_content` 只作为受限未完成回合恢复材料保存和回传，不进入玩家记录。旧存档与新版 schema 不兼容，不会被静默迁移。

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
