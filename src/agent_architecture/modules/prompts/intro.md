# modules/prompts

## 1. 作用

系统提示词的组装：每次模型调用前拼一份，插到这次请求的最前面 —— **它是请求的一部分，不是会话的一部分。**

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，并对外转出 `build_handlers`、`render_system_prompt`、`section_names`。 |
| `prompt.py` | 提示词本身：五段文案与各自的 `order`，加上组装规则（排序、填变量、丢空段、空行连接）。 |
| `handler.py` | 位点 3 的 SiteHandler：把拼好的 system 消息插到 `payload.messages` 最前面。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`Message`、`MessageRole.SYSTEM`、`LlmBeforePayload`、`Site`、`AgentError`。
- **本模块只往 payload 里写两个字段**：`system_prompt` 与 `system_tokens`。**不动 messages、不动 Session**：不 append 条目、不改执行态。
- **它不在 messages 里**：内核发请求时才把它放到最前面（`turn_runner._request_messages`）。压缩改的就是 `payload.messages`，所以二级压缩从来碰不到它。
- **本模块排在 100**：早于 `runtime_state`（110）算占用 —— 这份提示词的 token 因此进预算；也早于 `compression`（200）。
- **本模块读 `ctx.session.meta` 与 `ctx.config`**：工作目录、模型、权限档位这三个事实在会话期内不变，拼出来的文本才逐字稳定（前缀缓存与 token 锚点都靠这一点）。
- **被调用**：`composition` 把 `handler.build_handlers()` 的结果注册到位点 3。
