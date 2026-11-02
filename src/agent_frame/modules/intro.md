# modules

## 1. 作用

九个维度模块的容器包，外加三个非维度组装件（`prompts`、`todo`、`charts`、`ask_user`）。每个子包只依赖 `contracts`，彼此不 import。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，说明本包放的是九个维度模块。 |

## 3. 与其它模块的交互

- 本模块自身没有代码，交互都发生在子包里（见各子包的 intro.md）。
- **被调用**：`composition` 逐个子包调用它们的装配入口（`build_handlers`、`build_catalog`、`Checkpoint`、`Session`、`bootstrap_runtime_state`）。
- **`prompts` 与 `skill` 只往本次请求里注入**：前者添系统提示词、后者添技能目录，都不写日志、不改执行态（`prompts` 不是维度，`skill` 是 D3）。
- **`todo` 与 `charts` 相反，它们只写日志**：模型调 `todo_write` / `chart_*` 之后，位点 8 落一条 `todo` / `chart` 条目并广播 —— 这两类条目**都不进上下文**，也不参与投影与压缩。待办另有一位点 1 的 handler：新一轮开始把上一轮的清单清掉。
- **`device_tree` 完全不碰位点**：它只被 `GET /tree` 调用，回答"这个机构下面有什么"。不写日志、不进上下文、不打 trace —— 它是页面的目录，不是智能体的能力。
- **`ask_user` 只挂起**：工具把问题交给提问端口（`infra/questions`）后停在 `await` 上，回答作为普通的工具结果进日志与上下文；问题本身不落盘。
- 子包之间不直接 import；它们的协作全部发生在 `modules/agent_loop` 的位点上，靠 `SiteContext` 里的 `state` / `session` / `ports` 与 payload 交换数据。
