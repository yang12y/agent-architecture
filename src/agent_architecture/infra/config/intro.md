# infra/config

## 1. 作用

配置的加载与冻结：从环境变量与 `.env` 读出全局配置，校验后压成每个 trace 一份的只读快照。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，并对外转出 `load_app_config`。 |
| `defaults.py` | 所有配置项的默认值，loader 与其它模块引用同一份。 |
| `dotenv.py` | 极简 `.env` 解析：定位文件、读文本、解析键值。 |
| `freeze.py` | `freeze_config`：把 `AppConfig` 算成 `FrozenConfig`，含由窗口大小与压缩比例派生的阈值。 |
| `loader.py` | 从 `YZY_*` 环境变量加载 `AppConfig`，做取值范围与互斥关系的校验。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`AppConfig`、`FrozenConfig`、`ConfigError`、`Permission`、`SessionMeta`。
- **被调用**：
  - `composition` 调 `load_app_config()` 建全局配置。
  - `modules/agent_loop` 在 trace 开始时调 `freeze_config(...)` 拿本次 trace 的快照。
