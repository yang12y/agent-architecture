"""smalllog: a tiny, dependency-free JSONL log library."""

from __future__ import annotations

from .archive import ArchiveError, ArchiveResult, archive_old_entries
from .codec import (
    DEFAULT_TIMESTAMP_KEY,
    DecodedLine,
    decode_line,
    encode_line,
    format_timestamp,
    parse_timestamp,
    prepare_record,
    timestamp_of,
)
from .lock import FileLock, lock_path_for
from .reader import LogEntry, LogReader, read_from_offset, read_time_range
from .writer import AppendResult, LogWriter, append_record

__all__ = [
    "DEFAULT_TIMESTAMP_KEY",
    "AppendResult",
    "ArchiveError",
    "ArchiveResult",
    "DecodedLine",
    "FileLock",
    "LogEntry",
    "LogReader",
    "LogWriter",
    "append_record",
    "archive_old_entries",
    "decode_line",
    "encode_line",
    "format_timestamp",
    "lock_path_for",
    "parse_timestamp",
    "prepare_record",
    "read_from_offset",
    "read_time_range",
    "timestamp_of",
]
