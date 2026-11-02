# modules/skill

## 1. 作用

可复用的任务指令包：扫描 `SKILL.md`、合并多个来源、把可用技能渲染成目录注入本次请求，正文由 `skill` 工具按需取。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，并对外转出注册表、渲染、工具与装配入口。 |
| `manifest.py` | `SKILL.md` 的 front-matter 解析：名称（kebab-case）、描述、何时使用、能否被模型调用；正文解析单独一个函数。 |
| `scanner.py` | 扫一个根目录：每个含 `SKILL.md` 的子目录是一个技能，按目录名排序。 |
| `registry.py` | `SkillRegistry`：多来源合并去重（后面的覆盖前面的），提供全量 / 模型可见 / 按名字取。 |
| `catalog.py` | 目录渲染：一行一个技能；一个可调用的都没有时返回空串。 |
| `loader.py` | `skill` 工具的声明与实现：按名字读正文；不认识的名字回一份可用清单。 |
| `handler.py` | 位点 3 的 SiteHandler：把目录追加进 `payload.injected_messages`。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`ToolDeclaration`、`LlmBeforePayload`、`Message`、`Site`、`AgentError`。
- **与 `modules/prompts` 同一条注入链**：都往 `payload.injected_messages` 追加消息，**都不写 Session**。本模块排在它后面（105 > 100），所以请求里系统提示词在前、技能目录紧随其后。
- **被 `composition` 调用两次**：`SkillRegistry.load(default_roots(workspace))` 建表，`build_handlers(registry)` 注册位点 3；同一张表交给 `make_runner(registry)` 当 `skill` 工具的实现，工具登记在 `tool_calling` 的工具目录上。
- **被 `tool_calling` 执行**（经工具表，不是 import）：模型调 `skill` 时走位点 6/7/8 的普通工具调用。
- **不执行技能里的脚本**：要跑脚本就是一次普通的 `run_command`。
