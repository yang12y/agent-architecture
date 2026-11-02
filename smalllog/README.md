# smalllog

`smalllog` 是一个只用 Python 标准库实现的小型 JSONL 日志库。它不依赖第三方
运行时包，适合放进单机服务里做追加日志、按字节游标读取、按时间过滤和简单归档。

要求 Python 3.11+（使用了 `datetime.UTC`）。

## 文件结构

```text
smalllog/
├── __init__.py      对外 API
├── codec.py         JSON 编解码、时间戳解析
├── lock.py          跨进程文件锁
├── writer.py        追加写与自动归档触发
├── reader.py        offset / 时间范围读取
├── archive.py       压缩归档与原子替换
└── tests/           pytest 用例
```

所有写入、读取和归档都使用同一个 sidecar 锁文件，默认是
`<日志文件>.lock`。锁文件故意独立于日志文件：归档会通过 `os.replace()`
替换日志 inode，如果锁在旧 inode 上，替换后就保护不到新文件了。

## 快速开始

```python
from datetime import UTC, datetime

from smalllog import LogReader, LogWriter

path = "logs/app.jsonl"

writer = LogWriter(
    path,
    timestamp_key="ts",   # 默认就是 "timestamp"
    max_entries=10_000,   # 超过一万条有效记录时归档最老的
)

result = writer.append(
    {"level": "INFO", "message": "服务启动"},
    timestamp=datetime(2024, 1, 1, 8, 0, tzinfo=UTC),
)
print(result.offset)  # 这条记录在（归档后的）文件中的字节偏移

reader = LogReader(path, timestamp_key="ts")

# 从任意字节位置读；如果落在某行中间，会丢掉那半行，从下一整行继续。
entries = reader.read_from_offset(result.offset, limit=100)
for entry in entries:
    print(entry.offset, entry.value)

# 左闭右开：[start, end)
recent = reader.read_time_range(
    "2024-01-01T08:00:00Z",
    "2024-01-02T00:00:00Z",
    limit=100,
)
```

也可以用模块级快捷函数：

```python
from smalllog import append_record, read_from_offset, read_time_range

append_record("logs/app.jsonl", {"message": "hello"})
read_from_offset("logs/app.jsonl", 0, limit=10)
read_time_range("logs/app.jsonl", "2024-01-01T00:00:00Z", None)
```

## 写入语义

- 每条记录必须是可 JSON 序列化的 mapping。
- 未显式传 `timestamp` 时，自动补当前 UTC 时间；
  记录里已有 timestamp 时保留原值。
- 时间戳写成 `2024-01-01T00:00:00.000000Z` 形式。
- `encode_line()` 使用紧凑 JSON、UTF-8、`allow_nan=False`，拒绝 NaN/Infinity 等非标准 JSON。
- `AppendResult.offset` 是记录在**归档后**活动文件中的最终字节偏移，
  可直接回传给 `read_from_offset()`。
- `fsync=False` 是默认值，优先吞吐；需要掉电后尽量不丢时传 `fsync=True`。

## 读取语义

- `read_from_offset(offset)` / `iter_entries(start_offset)` 使用字节偏移。
- 如果 offset 指向某行中间，会丢弃该行剩余部分，从下一行开始读。
- `iter_lines()` 返回每一行及其解码错误，适合诊断；`iter_entries()` 只产出
  合法 JSON 行，损坏行和空行自动跳过。
- 时间范围是 `[start, end)`；start/end 可以是带时区 datetime、naive datetime
  （按 UTC 处理）或 ISO-8601 字符串。
- 带时间条件读取时，没有合法时间戳的记录会被跳过。
- 不假设文件内时间有序，因此时间范围读取会扫描整个文件，而不是遇到
  大于 end 的记录就提前停止。

## 归档语义

```python
from smalllog import LogWriter, archive_old_entries

writer = LogWriter("logs/app.jsonl", max_entries=3)

# 构造时就指定归档文件
writer = LogWriter(
    "logs/app.jsonl",
    max_entries=3,
    archive_path="logs/app-old.jsonl.gz",
)

# 手动触发
writer.archive()

# 或者不使用 writer
archive_old_entries("logs/app.jsonl", keep=3)
```

- 默认归档到 `<日志文件>.gz`，例如 `app.jsonl` -> `app.jsonl.gz`。
- `max_entries=N` 表示活动文件最多保留最新的 N 条**合法 JSON 记录**。
- 每次 append 后检查；超过 N 时把最老的记录移入 gzip，活动文件保留最新 N 条。
- 多次归档会合并进同一个 `.gz`，每次重新生成一个完整 gzip 成员，避免在崩溃时
  留下截断的 gzip 尾部。
- 归档内容仍是 JSONL 文本，可直接用 `gzip.open(..., "rt")` 读取。
- 损坏行不删除、不计数：它们会留在活动文件里。因此活动文件物理行数可能
  大于 N，但合法记录数保持不超过 N。
- 归档先写 `.gz`，再用 `os.replace()` 原子替换活动文件。两步之间崩溃可能
  产生重复记录（归档和活动文件各有一份），但不会静默丢记录。

## 并发与崩溃

- append 使用 `O_APPEND`，并在 sidecar 锁内完成写入。
- POSIX 使用 `fcntl.flock()`；Windows 使用 `msvcrt.locking()`。
- 读取和归档也拿同一把排他锁，因此不会读到归档替换中的半个文件。
- 文件锁是 advisory lock：同一台机器、遵守同一套锁协议的进程之间有效。
  不保证网络文件系统、NFS 或跨主机语义。
- 不要手动删除正在使用的 `.lock` 文件；删除会让不同进程锁到不同 inode。
- `iter_lines()` / `iter_entries()` 是生成器，锁会一直持有到迭代结束。
  如果只想读少量记录，用 `limit`，或尽早关闭/耗尽生成器。

## 设计取舍

- **JSONL 而不是二进制格式**：方便 `tail`、`grep`、事故现场直接看；
  代价是解析和空间效率不如二进制。
- **排他锁而不是读写锁**：实现小，归档和读不会互相撞；代价是并发读也只能串行。
- **时间范围全量扫描而不是建索引**：库保持无状态，不需要维护索引文件；
  代价是超大文件的时间查询是 O(file size)。
- **归档整文件重写**：逻辑简单、原子，`.gz` 始终是单个完整成员；
  代价是归档操作的时间和内存都是 O(活动文件 + 旧归档)。
- **损坏行保留在活动文件**：避免“容错”变成“悄悄删数据”；
  代价是极端损坏文件可能长期占用活动文件空间。
- **严格 JSON**：与写入端的 `allow_nan=False` 一致，避免 Python 扩展 JSON
  污染日志格式。

## 自查后修正

实现完成后重新读了一遍代码，做了这些修正：

1. 读取不存在的日志文件现在返回空结果，而不是抛 `FileNotFoundError`；
   同时补上 `limit=0` 的短路，避免时间范围分支先追加一条再判断。
2. 归档读取损坏 gzip 时捕获 `zlib.error`；新建归档默认权限设为 `0644`；
   目录 `fsync` 在不支持的文件系统上不再把一次已成功的替换报成失败。
3. 删除 archive 接口里没有实际作用的 `timestamp_key` 参数；
   解码端拒绝 `NaN`/`Infinity`，与写入端的严格 JSON 保持一致。

## 测试

```bash
python -m pytest smalllog/tests -q
```

测试覆盖写入、跨进程并发追加、offset 边界、损坏行、时间范围、gzip 归档、
归档失败保护，以及锁的跨进程超时。
