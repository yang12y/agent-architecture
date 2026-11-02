# infra/http

## 1. 作用

`HttpTransport` 端口的实现：一条进程级连接池，负责连接复用、超时、状态码与 JSON 解析。业务报文的容器形状与翻页不在这一层。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，对外转出 `HttpxTransport`、`build_http_transport`、`DEFAULT_TIMEOUT_SECONDS`。 |
| `client.py` | `HttpxTransport`：发一次请求、解析 JSON，把连不上 / 超时 / HTTP ≥ 400 / 非 JSON 收成 `DownstreamError`。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：实现 `HttpTransport` 端口，失败抛 `DownstreamError`。
- **被调用**：
  - `composition` 在装配期建一个实例，交给 `tools/` 的业务工具。
  - `tools/` 的取数层按 `HttpTransport` 的类型发请求，不认识 `httpx`。
