# modules

## 1. 作用

九个维度模块的容器包，外加一个提示词组装件（它不是维度）。每个子包只依赖 `contracts`，彼此不 import。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，说明本包放的是九个维度模块。 |

## 3. 与其它模块的交互

- 本模块自身没有代码，交互都发生在子包里（见各子包的 intro.md）。
- **被调用**：`composition` 逐个子包调用它们的装配入口（`build_handlers`、`build_catalog`、`Checkpoint`、`Session`、`bootstrap_runtime_state`）。
- **例外是 `prompts`**：它只在位点 3 把系统提示词填进本次请求的 payload，不写日志、不改执行态。
- 子包之间不直接 import；它们的协作全部发生在 `modules/agent_loop` 的位点上，靠 `SiteContext` 里的 `state` / `session` / `ports` 与 payload 交换数据。
