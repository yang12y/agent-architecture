# 加一个工具

工具层（`src/agent_frame/tools/`）只依赖 `contracts`，不认识任何维度模块、不实现位点。
加一个工具的本质是两件事：**写一份声明**，**在装配处注册**。

---

## 1. 两条路，先选一条

| | 声明式（`HttpTool`） | 函数式 |
|---|---|---|
| 适合 | 一次 HTTP 请求就能取到数据、要求写死的接口 | 一次调用要打好几个接口、要做本地算术 |
| 写法 | 填一份 `HttpTool` | 自己写 `ToolDeclaration` + `ToolRunner` |
| 分页 / 截断 / 超时 / 错误文案 | 通用层全包 | 自己处理（可复用 `paging.fetch`） |
| 注册 | `catalog.build([...], transport, prepare)` | `catalog.build` 的同名入口都能用（见 §5） |

一般的业务查询用声明式；跨接口的聚合（一次问三个接口、自己算比率）用函数式。

---

## 2. 声明式：`HttpTool` 的六个必填项

```python
from agent_frame.tools.params import obj, text, choice
from agent_frame.tools.spec import FetchMode, HttpTool

ORDERS = HttpTool(
    name="order_list",
    description=(
        "【能力】查工单清单：总数 + 前 20 条明细。"
        "【适用场景】用户点名某个设备或某个机构的工单时调用。"
        "【禁止场景】不做数量分布、占比、趋势等统计。"
    ),
    parameters=obj({
        "deviceGuid": text("设备 guid（选填）"),
        "status": choice(["0", "1", "2"], "工单状态：0 处理中 / 1 已完成 / 2 作废"),
    }),
    path="/api/orders/list",
    body=lambda args: {k: v for k, v in args.items() if v not in (None, "")},
    fetch=FetchMode.FIRST_PAGE,
    fields={"工单号": "code", "状态": "statusName", "开始时间": "startTime"},
    output="JSON：count 与 items（工单号 / 状态 / 开始时间）。",
)
```

**`description` 写三段**（能力 / 适用场景 / 禁止场景）：这段文字会进上下文，模型选不选它几乎全看它。
三句话说不清的工具，通常应该拆成两个。

### 参数 DSL（`tools/params.py`）

| 函数 | 生成什么 | 例子 |
|---|---|---|
| `text(说明, required=)` | `type: string` | `text("设备名称，模糊匹配")` |
| `integer(说明, minimum=)` | `type: integer` | `integer("条数上限", minimum=1)` |
| `flag(说明)` | `type: boolean`（挡冒牌布尔） | `flag("true=只查本级")` |
| `choice([值…], 说明)` | `enum`（检查链真的拦非法值） | `choice(["0","1"], "0 停 / 1 开")` |
| `list_of(元素, 说明)` | `type: array` | `list_of({"type": "string"}, "guid 列表")` |
| `obj({字段…})` | 对象，`required` 自动汇总 | `obj({"guid": text("…", required=True)})` |

只生成 `required / type / enum / description` —— 不产生 `anyOf`、`$ref`、`default: null`。

### 取数策略（`FetchMode`）

| 值 | 发什么 | 用在哪 |
|---|---|---|
| `FIRST_PAGE` | `page=1&limit=20`，`count` 取后端总数 | 清单类默认 |
| `ALL_PAGES` | `limit=1000`，按 `count` 算页数**并发**取，最多 15 页 | 统计、指标 |
| `SINGLE` | 不带分页字段 | 接口本身没有分页 |
| `COUNT_ONLY` | 不带分页字段，只留总数 | 只要一个数字 |
| `UNTIL_SHORT` | 逐页取到不满一页为止 | 下游不回 `count` |

上限常量（`MAX_ITEMS=50`、`MAX_FETCH_PAGES=15`、`FETCH_PAGE_SIZE=1000`）只写在 `paging.py` 里，别在业务里再定一套。

### 白名单投影（`fields`）

模型看到的行**只有这里列出的键**：漏列等于对模型隐身。下游一行一百多个字段是常态，裁到十来个才对得起上下文。

值可以是下游键名，也可以是一个函数（拼字段、取第一个非空、算派生值）：

```python
fields={
    "开始时间": lambda row: first_present(row, "startTime", "createTime"),
    "状态": "statusName",
}
```

自定义输出（`shape`）拿到的是整页结果与原始参数，返回任意 JSON —— 比率、分布、分组计数都在这儿算，**别让模型自己算**。

---

## 3. `prepare`：声明不认识的几件事

`HttpTool` 只描述接口形状；地址、请求头、超时、并发、缺省参数由 `prepare` 补：

```python
def prepare(tool, args, ctx):
    return Call(
        method=tool.method,
        url=settings.base_url + tool.path,
        headers={"Accept": "application/json", "Authorization": ctx.variables["authorization"]},
        timeout=settings.timeout_seconds,
        concurrency=settings.fetch_concurrency,
        payload=tool.body(args) if tool.body else args,
    )
```

凭证怎么来：前端每次请求带上来，存在 trace 变量里（`ctx.variables`），后端不存、不落盘 ——
所以 `prepare` 里取不到就直接报一句人话，别让下游回 401 让人猜。

---

## 4. 输出与错误

- **成功与没取全走同一个形状**：`{count, items, truncated}`，`truncated: true` 表示翻页到了上限。
- 失败一律回**给人听的一句话**（`ToolError`），别把下游的 JSON 或堆栈丢给模型。
- 单条结果的字符上限由位点 8 统一截断（`AGENT_TOOL_RESULT_MAX_CHARS`），工具里不要自己截。

---

## 5. 注册

只有一处：`src/agent_frame/composition/container.py` 的 `build_container`。

```python
http = build_http_transport()
for declaration, runner in catalog.build(TOOLS, http, prepare):
    tools.register(declaration, runner)
```

函数式工具注册同一张表：

```python
tools.register(MY_TOOL, my_runner)
```

注册期会校验**工具名全局唯一** —— 重名会让模型二选一，也会让排错无从下手。

---

## 6. 权限与副作用

`ToolDeclaration` 上有两个字段决定沙箱怎么判、失败怎么重试：

| 字段 | 取值 | 效果 |
|---|---|---|
| `required_permission` | `READ_ONLY` / `EDITABLE` / `FULL_ACCESS` | 低于当前档位就进审批或直接被拒 |
| `side_effect` | `NONE` / `IDEMPOTENT` / `NON_IDEMPOTENT` | 只有 `NONE` 与 `IDEMPOTENT` 能自动重试 |

纯查询两个都便宜：`READ_ONLY` + `NONE`。写操作至少 `EDITABLE` + `IDEMPOTENT`；转账、下单这种不可重放的动作必须是 `NON_IDEMPOTENT`。
要等人工（提问、二次确认）的工具把 `timeout` 声明长一点，默认 60 秒人来不及答。

---

## 7. 检查清单

1. `description` 三段齐全，且**不提别的工具名**；
2. 参数能枚举就别用自由文本；
3. `fields` 把下游字段裁到模型真用得上的那些；
4. 比率、分布、分组计数在 `shape` 里算完，不要交给模型；
5. 权限与副作用按最坏情况声明；
6. 加完跑 `uv run pytest -q tests/test_tools tests/test_composition`。
