# infra/storage

## 1. 作用

存储实现：会话常驻内存、日志落到磁盘，并提供把条目还原回来的编解码。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，并对外转出 `FileSessionStore`。 |
| `codec.py` | 编解码：`SessionEntry` / `SessionMeta` 与 JSON 兼容值互转，按 `type` 分派各 payload，出问题抛 `CodecError`。 |
| `file_store.py` | `FileSessionStore`：进程内缓存会话，`meta.json` + `entries.jsonl` 落盘，提供建 / 读 / 追加 / 列 / 删。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`SessionEntry`、`SessionMeta`、各 payload 类型、`FlushError`，并实现 `SessionStore` 协议。
- **被调用**：
  - `composition` 建唯一实例，放进 `Container` 并注入给 checkpoint。
  - `modules/checkpoint` 的落盘屏障经 `SessionStore` 把日志推到盘上。
  - `api` 的会话列表与详情经 `composition` 的会话注册表读到它加载的会话。
