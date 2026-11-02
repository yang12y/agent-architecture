# tools

## 1. 作用

业务工具层：把"打哪个接口、怎么取数、怎么出结果"写成数据，装成 `ToolCatalog` 里的工具。它不进任何位点，只依赖 `contracts`。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记。 |
| `spec.py` | `HttpTool` / `Call` / `Page` / `FetchMode`：一条工具声明就是一份数据。 |
| `params.py` | 参数 schema 小 DSL（`text` / `integer` / `choice` / `list_of` / `obj`）。 |
| `paging.py` | 四种取数（首屏 / 翻页取全 / 单次 / 只取总数）与五个上限常量。 |
| `envelope.py` | 业务报文容器 → (明细行, 总数)；认不出的形状一律报错。 |
| `records.py` | 字段白名单投影：模型看到的键 → 下游键或取值函数。 |
| `errors.py` | `RequestContextError`：缺请求头这类前置数据。 |
| `catalog.py` | 声明 → `(ToolDeclaration, ToolRunner)`；工具名唯一性在装配期校验。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`ToolContext`、`ToolDeclaration`、`JsonValue`、`DownstreamError`、`HttpTransport`。
- **被调用**：`composition` 装配期调业务域的 `build()`，把工具注册进 `ToolCatalog`；`tool_calling` 在位点 7 执行它们。
- 下游 HTTP 走 `infra/http` 的 `HttpTransport` 实现 —— 本层不认识 `httpx`。
