# modules/tool_calling/builtin

## 1. 作用

内置工具集：文件读写、搜索、列目录、执行命令，装成一份工具目录交给上层。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | `build_catalog`：把下面这些工具装成一份 `ToolCatalog`。 |
| `common.py` | 各工具共用的小件：必填 / 可选字符串取值、正整数取值、路径显示。 |
| `edit.py` | `edit`：把文件里唯一的一段文本替换掉。 |
| `glob.py` | `glob`：按文件名模式递归找文件。 |
| `grep.py` | `grep`：按正则表达式搜文件内容。 |
| `list_dir.py` | `list_dir`：列出一个目录里的条目。 |
| `read.py` | `read`：读工作目录里的一个文本文件。 |
| `run_command.py` | `run_command`：执行一条命令并取回输出。 |
| `write.py` | `write`：把一个文本文件整份写成给定内容。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`ToolDeclaration`、`SideEffect`、`Permission`、`ToolArgumentError`、`resolve_in_workspace`。
- **本模块调用父包** `modules/tool_calling`：用它的 `ToolCatalog`、`ToolContext`、`ToolFn` 定义工具。
- **本模块在声明里给出权限与副作用**：判定由 `modules/sandbox` 在位点 6 做，执行由父包的 `executor` 做。
- **被调用**：父包的 `build_handlers` / 装配流程调 `build_catalog` 取工具集。
