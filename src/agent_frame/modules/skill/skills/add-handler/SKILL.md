---
name: add-handler
description: 给这个项目加一个位点 handler：选位点与优先级、写实现、装配、补文档与测试
when_to_use: 要往某个位点插逻辑，或者要给已有模块加一个执行点时
model_invocable: true
---

# 加一个 handler

顺序别反：**先定位点、再定优先级、再写实现，最后补文档与测试。**

## 1. 定位点

十个位点见 `docs/ARCHITECTURE.md` §2。问两个问题：

- 这件事发生在什么时候？用户消息刚进来是位点 1，发请求前是位点 3，工具执行前是位点 6。
- 它读谁的产出、谁要读它的产出？—— 这决定优先级。

## 2. 定优先级

段位是硬约束（装配期校验，越界直接抛 `ValueError`）：

    50        恢复判据（只有 checkpoint 用）
    100–499   维度逻辑
    500–899   用户 hook
    900       落盘屏障，永远最后

维度段里怎么排，只看"你读谁、谁读你"。现成的坐标：

    100       往 payload 里填内容（提示词、技能目录）
    105       紧随其后填注入消息（技能目录）
    110       算 token 占用、读上面填好的东西
    200       压缩
    300       依赖投影，或依赖前面几位的产出
    400–499   等别人写完再动（判定、统计）
    900       屏障

**先看 `docs/HANDLERS.md` 里那个位点现在长什么样**，别去动别人已有的数字 —— 中间空着很多，取中间值。

## 3. 写实现

- 一个模块 × 一个位点 = 一个 handler 实例，`id` 写成 `模块名.位点名`。
- 只改 `ctx.payload` 与 `ctx.state`；返回值只表达控制：`None`/`True` 继续，`False` 否决，而**否决只在位点 1 与 6 有意义**。
- 要中止整次 trace 抛 `SiteAbort`，落盘失败抛 `FlushError` —— 别自己吞。
- 不 import 别的维度模块。要别人的数据从 `ctx` 拿，要别人的能力走 `contracts` 的端口。

## 4. 装配

`composition/container.py` 的 `SitePipeline` 列表里加一行 `*your_module.build_handlers()`。

## 5. 补文档与测试

三样缺一不可：

1. `docs/HANDLERS.md` §2 那张位点表加一行、§3 明细加一行、**合计 +1**；
2. `tests/test_composition/test_container.py` 里 `test_container_wires_every_handler` 的总数 +1；
3. `tests/test_<模块>/` 里加测试。

`tests/test_composition/test_handler_doc.py` 会检查文档漏没漏、priority 抄没抄错 —— 忘了改它就红。

## 6. 收尾

    .venv\Scripts\python.exe -m ruff check src tests
    .venv\Scripts\python.exe -m pytest -q

**不跑 `ruff format`**：它改文件，会把不相干的行也重排（见 `run-tests` 技能）。
