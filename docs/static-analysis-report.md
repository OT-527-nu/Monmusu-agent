# mypy 与 Ruff 静态检查报告

检查日期：2026-07-25

## 1. 结论

两种工具对当前项目都有价值，但解决的问题不同：

- **Ruff** 适合现在就引入。它运行很快，能统一导入和格式，并提前发现未使用导入、异常链丢失、闭包晚绑定等容易被忽略的问题。
- **mypy** 对模块契约尤其有价值。当前普通模式已经发现一处生产代码类型冲突；但严格模式和测试目录中的告警噪声较多，不适合直接作为全项目的强制门禁。
- 二者都不会执行代码，也不能证明游戏规则正确。它们应补充而不是替代单元测试和集成测试。

建议先让 Ruff 检查一组小而稳定的规则，并让 mypy 只检查 `src` 的普通模式。等现有告警被有意识地处理后，再把格式检查、严格类型检查或测试目录逐步纳入门禁。

本次只安装、执行并分析工具，没有自动修复源代码，也没有修改项目配置或依赖锁文件。

## 2. 安装环境

工具安装在项目现有的 `.venv` 中：

| 项目 | 实际值 |
| --- | --- |
| Python | 3.12.3 (`.venv/bin/python`) |
| mypy | 2.3.0 |
| Ruff | 0.16.0 |
| 安装方式 | `uv pip install` |

实际安装命令：

```bash
UV_CACHE_DIR=/tmp/monmusu-uv-cache uv pip install --python .venv/bin/python mypy ruff
```

`.venv` 已被 Git 忽略，因此安装不会出现在版本差异中。当前 `pyproject.toml` 也没有声明开发依赖，这意味着换一台机器或重建虚拟环境时不会自动安装这两个工具。

## 3. 两种工具分别检查什么

### Ruff

Ruff 同时提供代码检查器和格式化器。它主要从源码结构中寻找：

- 未使用或未定义的名称；
- 容易出错的 Python 写法；
- 导入顺序和现代 Python 写法；
- 可统一的代码格式。

Ruff 不理解完整的跨函数类型契约，也不验证运行时业务结果。

### mypy

mypy 根据类型标注检查数据能否沿调用链安全传递，例如：

- 一个变量是否会被赋予不兼容的类型；
- 可空值是否在使用前被排除；
- 实现是否符合 `Protocol`；
- 函数声明的返回类型是否与实际返回值一致。

mypy 的效果取决于类型标注的精度。项目大量使用从 JSON 读取的 `Any` 和普通 `dict`，严格模式因此会比普通模式报告更多“类型信息不足”，这些不一定是运行时缺陷。

## 4. Ruff 检查结果

项目目前没有 Ruff 配置。使用 Ruff 0.16.0 当前解析出的默认规则执行：

```bash
.venv/bin/ruff check src tests
```

共发现 22 项：

| 规则 | 数量 | 含义 | 判断 |
| --- | ---: | --- | --- |
| `UP035` | 7 | 建议从 `collections.abc` 导入抽象类型 | 现代化建议，风险低 |
| `SIM102` | 5 | 可合并嵌套 `if` | 可读性建议，不是缺陷 |
| `I001` | 4 | 导入块未按统一规则排序 | 风格一致性 |
| `B023` | 4 | lambda 捕获循环变量 | 当前测试中会及时调用，尚未形成运行时错误，但存在晚绑定风险 |
| `F401` | 1 | `state.py` 中 `Path` 未使用 | 明确可清理项 |
| `SIM117` | 1 | 嵌套 `with` 可合并 | 可读性建议，不是缺陷 |

其中 12 项可由 Ruff 自动修复。不过当前工作区有未提交改动，不应直接运行全局 `--fix`，否则会把机械格式变化和正在开发的逻辑混在同一份差异里。

格式检查：

```bash
.venv/bin/ruff format --check src tests
```

结果为 15 个受检文件中 7 个会被重新格式化、8 个已经符合格式。格式差异不代表行为错误。

为了模拟适合 MVP 初期的显式规则集，又执行了：

```bash
.venv/bin/ruff check --select E4,E7,E9,F,I,B src tests
```

该规则集报告 10 项：4 个 `I001`、4 个 `B023`、1 个 `F401`，以及默认结果中没有出现的 1 个 `B904`。`B904` 指出 `state.py` 在捕获 `KeyError` 后抛出领域异常时没有使用 `raise ... from ...`，因此异常究竟来自原操作还是异常处理过程不够明确。

这 10 项是一个可控的首批基线，比一次采用全部建议型规则更适合当前八周 MVP。

## 5. mypy 检查结果

### 生产代码普通模式

```bash
.venv/bin/mypy --no-incremental --show-error-codes --show-column-numbers src
```

检查 10 个源文件，发现 1 项：

```text
src/monmusu_agent/tools.py:633:18
ModuleEventSource 不能赋给已被推断为 CheckEffectSource 的变量 source
```

这里不是运行时分支错误。`source` 在第一个分支中被 mypy 推断成了单一类型，而代码实际允许两种合法来源。显式声明下面的联合类型即可表达真实契约：

```python
source: CheckEffectSource | ModuleEventSource
```

这是本次检查中最直接、最值得优先修复的生产代码告警。

### 生产代码严格模式

```bash
.venv/bin/mypy --no-incremental --strict src
```

共发现 8 项，分布在 3 个文件：

- 上述联合类型推断问题 1 项；
- 从动态 JSON 或字典返回 `Any` 4 项；
- 裸 `dict` 缺少键和值类型参数 3 项。

后 7 项主要说明 JSON 进入领域代码后仍携带较多 `Any`，而不是已经证实的运行时 bug。它们有长期价值，但若现在为消除全部严格告警而重写状态 schema 或引入大量类型模型，投入与 MVP 收益不匹配。

### 测试代码

```bash
.venv/bin/mypy --no-incremental tests
```

共发现 58 项，分布如下：

| 文件 | 数量 | 主要原因 |
| --- | ---: | --- |
| `tests/test_tools.py` | 51 | 直接下标访问可空的 `ToolResult.data`，或直接访问可空的 `error` |
| `tests/test_engine.py` | 6 | 循环变量 `outcome` 后来被赋成结果对象；4 个 lambda 使用 `list.append(...) or value` |
| `tests/test_agent.py` | 1 | 测试故意传入不符合 `GameMasterModel` 协议的假模型 |

`test_tools.py` 的测试能够通过，是因为运行时数据确实符合预期；但 `ToolResult` 当前被定义成：

```python
ok: bool
data: Mapping[str, Any] | None
error: ToolError | None
```

mypy 不知道 `ok=True` 必然意味着 `data` 非空，也不会从 `unittest` 的 `assertIsNone`、`assertTrue` 自动推导这组字段之间的关系。因此这些告警主要暴露了“测试断言与类型收窄之间的缺口”，不是 51 个独立业务错误。

MVP 阶段可以在测试中使用普通 `assert result.data is not None`、`assert result.error is not None` 或少量测试辅助函数完成收窄。暂时没有必要只为 mypy 把 `ToolResult` 重构成多个结果类。

`test_agent.py` 的非法模型是负路径测试的必要输入。这里应把精确的 `type: ignore[arg-type]` 放在 mypy 实际报告参数错误的那一行，而不是改变生产协议来迁就测试。

第一次增量检查曾触发 mypy 自身的内部错误；使用 `--no-incremental` 后正常完成，随后普通增量命令也能稳定给出上述 1 项告警。当前证据更像一次缓存或工具瞬态问题，不能算作项目缺陷；若再次出现，应保留 traceback 并清理或绕过 `.mypy_cache` 后复查。

## 6. 对当前项目的实际价值

### Ruff 的近期价值较高

- 在每次提交前用很低成本发现明显的 Python 结构问题；
- 统一多人或 AI 生成代码的导入和格式，减少评审噪声；
- `B` 类规则能发现单元测试通过但写法仍有潜在风险的代码。

### mypy 的价值集中在模块边界

这个项目正在冻结 `GameEngine`、`GameMasterAgent`、`ToolSession`、`RuleEngine` 和 `StateCommitter` 之间的契约。mypy 能在不运行游戏的情况下发现返回值、可空状态、协议实现和来源联合类型是否对得上，尤其适合这些边界。

不过，LLM 输出是否合法、状态效果是否符合模组、玩家体验是否有趣，仍需运行时校验、集成测试和实际游玩验证。mypy 无法回答这些问题。

## 7. 推荐的渐进采用方式

1. **先固定版本和规则，不立即全量格式化。** 后续单独修改 `pyproject.toml`，记录开发依赖，并显式配置 Ruff 的 `E4,E7,E9,F,I,B`，避免升级工具后默认规则变化。
2. **先清理 10 项 Ruff 基线。** 将机械导入/格式改动与逻辑改动分开提交；修复 `B023`、`B904` 时人工复核，不盲用 `--unsafe-fixes`。
3. **让 mypy 普通模式先只检查 `src`。** 修复 `tools.py` 的联合来源标注后，可把这一命令作为日常检查或 CI 门禁。
4. **暂不启用全局 `--strict`。** 在模块接口稳定后，逐步减少 JSON 边界的 `Any`，再按文件提高严格度。
5. **测试类型检查分阶段处理。** 先增加明确的非空断言和测试辅助函数，消除高频可空告警；保留负路径测试，并对故意违反协议的位置使用精确忽略。

建议的近期日常命令是：

```bash
.venv/bin/ruff check --select E4,E7,E9,F,I,B src tests
.venv/bin/mypy src
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```

这套组合分别覆盖 Python 结构问题、静态类型契约和真实运行行为，成本适合当前 MVP 阶段。
