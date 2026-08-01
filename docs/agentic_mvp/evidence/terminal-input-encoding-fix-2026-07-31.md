# Agentic CLI 终端输入编码问题复现与修复报告

## 记录信息

- 日期：2026-07-31
- 影响入口：`monmusu-agent-agentic` / `python -m monmusu_agent.agentic_cli`
- 影响范围：新会话资料和玩家行动的真实终端输入
- 修复文件：`src/monmusu_agent/agentic_cli.py`
- 回归测试：`tests/test_agentic_cli.py`

## 用户现象

真实终端输入中文后，程序在写入会话时失败。用户报告的两条路径分别是：

1. 自定义调查员资料写入 `session.json` 时失败：

   ```text
   UnicodeEncodeError: 'utf-8' codec can't encode character '\udce7'
   ...
   AgenticSessionPublishError: 会话无法原子发布
   ```

2. 建局成功后，第一条中文行动写入未完成回合时失败：

   ```text
   UnicodeEncodeError: 'utf-8' codec can't encode characters in position 4-5:
   surrogates not allowed
   ...
   AgenticTurnPersistenceError: 未完成回合无法持久化，模型未被调用
   ```

第二条路径中的“模型未被调用”是正确的安全结果：错误发生在 Harness 保存未完成回合的边界，DeepSeek 没有收到这条行动。

## 最小复现

### 写盘层复现

下面的命令把原始字节 `CE D2` 按带 `surrogateescape` 的 UTF-8 读取。它形成的字符串含有 `\udcce\udcd2`，随后进入同一个 JSON 原子写入函数：

```bash
.venv/bin/python -c "from pathlib import Path; from tempfile import TemporaryDirectory; from monmusu_agent.storage import write_json_atomic; raw=bytes.fromhex('ced2'); value=raw.decode('utf-8','surrogateescape'); print(repr(value)); d=TemporaryDirectory(); write_json_atomic(Path(d.name)/'session.json', {'player_input':value})"
```

结果为 `UnicodeEncodeError: ... surrogates not allowed`。这证明失败原因是字符串包含代理码位，而不是 JSON 或普通中文不支持 UTF-8。

对比：合法的 Python Unicode 字符串 `我观察石牢里其他人的情况` 可以正常写入同一个函数。

### locale 触发条件

在关闭 Python 的 UTF-8 locale 强制、并使用 C locale 时，真实 stdin 会是
`ascii + surrogateescape`：

```bash
printf '我\\n' | PYTHONCOERCECLOCALE=0 LC_ALL=C PYTHONUTF8=0 \
  .venv/bin/python -c 'import sys; print(sys.stdin.encoding, sys.stdin.errors); print(ascii(input()))'
```

修复前的输入结果是 `ascii surrogateescape` 和 `\udce6\udc88\udc91`。修复后的
`_configure_terminal_input()` 将同一 stdin 重配置为 `utf-8 surrogateescape`，输入结果为
合法的 `\u6211`。

### 真实 CLI 边界复现

以下命令向真实 CLI 的 stdin 注入一段故意损坏的字节；它不调用 DeepSeek：

```bash
printf '1\n\303(\n' | DEEPSEEK_API_KEY=test-key PYTHONPATH=src .venv/bin/python -m monmusu_agent.agentic_cli
```

修复前，这类输入最终会在会话 JSON 写入处产生 traceback。修复后的结果为退出码 `2`，并显示：

```text
输入错误：终端输入不是有效的 UTF-8；请确认终端和输入法使用 UTF-8 后重试
```

## 根因

Python 文本 stdin 使用了 `surrogateescape`。当终端发送的原始字节不能按当前 stdin 编码解码时，Python 不一定立即抛错，而是把这些字节保留成 `U+DC80` 至 `U+DCFF` 范围内的代理码位，例如 `\udce7`。

随后 `storage.write_json_atomic()` 以 UTF-8 打开文件并使用 `ensure_ascii=False` 写入。代理码位不是可编码的 Unicode 标量值，因此在 `file.write()` 阶段触发 `surrogates not allowed`。

测试此前通过是因为测试把已经正确解码的 Python 字符串注入 `read_line`，没有经过真实终端的“原始字节 -> stdin 文本”边界。这个测试路径验证了 CLI、Harness 和存储行为，但没有验证终端 locale 或输入法编码。

## 修复内容

### 1. CLI 入口显式使用 UTF-8 标准 I/O

`main()` 在读取真实终端前把 stdin 配置为
`encoding="utf-8", errors="surrogateescape"`，并把 stdout/stderr 配置为 UTF-8 严格输出，
不再依赖启动 shell 的默认 locale。

### 2. 在输入边界恢复可识别的字节

所有 CLI 读行路径都经过统一包装：

- 没有代理码位的正常字符串原样保留。
- 对 `surrogateescape` 产生的低代理码位，先恢复原始字节，再严格按 UTF-8 解码。
- 如果恢复后的字节仍不是合法 UTF-8，抛出稳定的 `CliInputEncodingError`。
- 不使用 `errors="replace"`，避免静默丢失或改变玩家输入。

这覆盖了调查员资料、首条行动、后续行动以及直接调用 CLI seam 时传入的文本。

### 3. CLI 输出稳定错误

`main()` 捕获 `CliInputEncodingError`，只输出输入错误和退出码 `2`，不暴露 Python traceback，也不会创建部分会话或调用模型。

## 验证结果

解释器：`.venv/bin/python`，Python 3.12.3。

- 最小代理码位写盘复现：修复前稳定失败，确认根因。
- `tests.test_agentic_cli.AgenticCliTest.test_cli_repairs_utf8_bytes_preserved_by_surrogateescape`：通过。
- `tests.test_agentic_cli.AgenticCliTest.test_cli_rejects_bytes_that_cannot_be_recovered_as_utf8`：通过。
- `PYTHONPATH=src .venv/bin/python -m unittest tests.test_agentic_cli`：10 passed。
- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_agentic_*.py'`：67 passed。
- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`：152 passed。
- `PYTHONPATH=src .venv/bin/python -m compileall -q src tests`：通过。
- 变更源码 Ruff、目标源码 mypy、选定安全 Ruff 规则和 `git diff --check`：通过。
- `printf '我\\n' | PYTHONCOERCECLOCALE=0 LC_ALL=C PYTHONUTF8=0 PYTHONPATH=src .venv/bin/python -c 'import monmusu_agent.agentic_cli as c; c._configure_terminal_input(); print(ascii(input()))'`：读取为合法的 `\u6211`。
- 真实 CLI 损坏 stdin 复现：退出码 2，无 traceback，输出稳定输入错误。

## 边界与残余风险

此修复可以处理“原始输入本来是 UTF-8，但被错误 stdin locale 解码成 `surrogateescape`”的情况，也可以把问题从深层 JSON 写盘提前到 CLI 输入边界。

如果终端实际发送的是 GBK 或其他非 UTF-8 字节，程序不会猜测编码并默默转换，而是拒绝输入并提示使用 UTF-8。玩家仍需确保终端、输入法和 shell locale 使用 UTF-8；可用 `locale` 检查 `LANG` / `LC_ALL`。

修复没有改变 Agent Harness、DeepSeek 协议、会话 schema、原子提交或未完成回合语义。
