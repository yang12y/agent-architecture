"""Offset and time-range reads with corruption-tolerant iteration."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from itertools import islice
from pathlib import Path
from typing import Any

from .codec import (
    DEFAULT_TIMESTAMP_KEY,
    DecodedLine,
    decode_line,
    parse_timestamp,
    timestamp_of,
)
from .lock import FileLock, lock_path_for


@dataclass(frozen=True, slots=True)
class LogEntry:
    value: Any
    offset: int
    end_offset: int
    raw: bytes


class LogReader:
    """Read JSONL records while sharing the writer's sidecar lock."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        timestamp_key: str = DEFAULT_TIMESTAMP_KEY,
        lock_path: str | os.PathLike[str] | None = None,
        lock_timeout: float = 30.0,
    ) -> None:
        self.path = Path(path)
        self.timestamp_key = timestamp_key
        self.lock_path = (
            Path(lock_path) if lock_path is not None else lock_path_for(self.path)
        )
        if self.lock_path.resolve() == self.path.resolve():
            raise ValueError("lock_path must differ from the log path")
        self.lock_timeout = lock_timeout

    def iter_lines(self, start_offset: int = 0) -> Iterator[DecodedLine]:
        """Yield every physical line, including malformed ones.

        The lock is held for the lifetime of the iterator.  Starting in the
        middle of a line discards that partial line, which makes a saved
        ``end_offset`` or any byte position safe to pass back in.
        """

        if isinstance(start_offset, bool) or not isinstance(start_offset, int):
            raise TypeError("start_offset must be an integer")
        if start_offset < 0:
            raise ValueError("start_offset must be >= 0")

        with FileLock(self.lock_path, timeout=self.lock_timeout):
            try:
                handle = self.path.open("rb")
            except FileNotFoundError:
                return
            with handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                if start_offset >= size:
                    return

                handle.seek(start_offset)
                if start_offset > 0:
                    handle.seek(start_offset - 1)
                    if handle.read(1) != b"\n":
                        handle.readline()

                while True:
                    position = handle.tell()
                    raw = handle.readline()
                    if not raw:
                        break
                    yield decode_line(raw, offset=position)

    def iter_entries(self, start_offset: int = 0) -> Iterator[LogEntry]:
        """Yield valid JSON lines and silently skip corrupt/blank lines."""

        for line in self.iter_lines(start_offset):
            if line.ok:
                yield LogEntry(
                    value=line.value,
                    offset=line.offset,
                    end_offset=line.end_offset,
                    raw=line.raw,
                )

    def read_from_offset(self, offset: int = 0, *, limit: int | None = None) -> list[LogEntry]:
        """Read valid records beginning at a byte offset."""

        self._validate_limit(limit)
        entries = self.iter_entries(offset)
        return list(entries if limit is None else islice(entries, limit))

    def read_time_range(
        self,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        *,
        start_offset: int = 0,
        limit: int | None = None,
    ) -> list[LogEntry]:
        """Read records whose timestamp is in ``[start, end)``.

        Bounds may be aware datetimes, naive datetimes (interpreted as UTC), or
        ISO-8601 strings.  If both bounds are ``None``, this behaves like an
        unfiltered read.  Records without a parseable timestamp are skipped
        when a bound is supplied.
        """

        self._validate_limit(limit)
        start_dt = self._bound(start, "start")
        end_dt = self._bound(end, "end")
        if start_dt is not None and end_dt is not None and start_dt > end_dt:
            raise ValueError("start must be <= end")
        if limit == 0:
            return []
        if start_dt is not None and end_dt is not None and start_dt == end_dt:
            return []
        if start_dt is None and end_dt is None:
            return self.read_from_offset(start_offset, limit=limit)

        result: list[LogEntry] = []
        for entry in self.iter_entries(start_offset):
            stamp = timestamp_of(entry.value, self.timestamp_key)
            if stamp is None:
                continue
            if start_dt is not None and stamp < start_dt:
                continue
            if end_dt is not None and stamp >= end_dt:
                continue
            result.append(entry)
            if limit is not None and len(result) >= limit:
                break
        return result

    @staticmethod
    def _bound(value: datetime | str | None, name: str) -> datetime | None:
        if value is None:
            return None
        parsed = parse_timestamp(value)
        if parsed is None:
            raise ValueError(f"{name} is not a valid datetime")
        return parsed

    @staticmethod
    def _validate_limit(limit: int | None) -> None:
        if limit is None:
            return
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an integer or None")
        if limit < 0:
            raise ValueError("limit must be >= 0")


def read_from_offset(
    path: str | os.PathLike[str],
    offset: int = 0,
    *,
    limit: int | None = None,
    timestamp_key: str = DEFAULT_TIMESTAMP_KEY,
    lock_path: str | os.PathLike[str] | None = None,
    lock_timeout: float = 30.0,
) -> list[LogEntry]:
    """Convenience wrapper for :meth:`LogReader.read_from_offset`."""

    return LogReader(
        path,
        timestamp_key=timestamp_key,
        lock_path=lock_path,
        lock_timeout=lock_timeout,
    ).read_from_offset(offset, limit=limit)


def read_time_range(
    path: str | os.PathLike[str],
    start: datetime | str | None = None,
    end: datetime | str | None = None,
    *,
    start_offset: int = 0,
    limit: int | None = None,
    timestamp_key: str = DEFAULT_TIMESTAMP_KEY,
    lock_path: str | os.PathLike[str] | None = None,
    lock_timeout: float = 30.0,
) -> list[LogEntry]:
    """Convenience wrapper for :meth:`LogReader.read_time_range`."""

    return LogReader(
        path,
        timestamp_key=timestamp_key,
        lock_path=lock_path,
        lock_timeout=lock_timeout,
    ).read_time_range(
        start,
        end,
        start_offset=start_offset,
        limit=limit,
    )
