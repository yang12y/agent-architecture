"""Append records under an inter-process lock and trigger archival."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .archive import ArchiveResult, archive_excess_locked
from .codec import DEFAULT_TIMESTAMP_KEY, encode_line, prepare_record
from .lock import FileLock, lock_path_for

_UNSET = object()


@dataclass(frozen=True, slots=True)
class AppendResult:
    """Metadata about the record that was appended.

    ``offset`` is the record's byte offset *after* any archive rewrite, so it
    remains useful as the input to :meth:`LogReader.read_from_offset`.
    """

    offset: int
    record: dict[str, Any]
    archived_records: int = 0


class LogWriter:
    """A process-safe JSONL writer.

    A sidecar ``<name>.lock`` file serializes all mutations.  When
    ``max_entries`` is set, every append checks whether the active file has more
    than that many valid records and archives the oldest ones.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        timestamp_key: str = DEFAULT_TIMESTAMP_KEY,
        max_entries: int | None = None,
        archive_path: str | os.PathLike[str] | None = None,
        lock_path: str | os.PathLike[str] | None = None,
        lock_timeout: float = 30.0,
        fsync: bool = False,
    ) -> None:
        self.path = Path(path)
        self.timestamp_key = timestamp_key
        if max_entries is not None:
            if isinstance(max_entries, bool) or not isinstance(max_entries, int):
                raise TypeError("max_entries must be an integer or None")
            if max_entries < 1:
                raise ValueError("max_entries must be >= 1")
        self.max_entries = max_entries
        self.archive_path = (
            Path(archive_path)
            if archive_path is not None
            else self.path.with_name(self.path.name + ".gz")
        )
        if self.archive_path.resolve() == self.path.resolve():
            raise ValueError("archive_path must differ from the JSONL path")
        self.lock_path = (
            Path(lock_path) if lock_path is not None else lock_path_for(self.path)
        )
        if self.lock_path.resolve() in {
            self.path.resolve(),
            self.archive_path.resolve(),
        }:
            raise ValueError("lock_path must differ from the log and archive paths")
        self.lock_timeout = lock_timeout
        self.fsync = fsync

    def append(
        self,
        record: Mapping[str, Any],
        *,
        timestamp: datetime | str | None | object = _UNSET,
    ) -> AppendResult:
        item = prepare_record(
            record,
            timestamp_key=self.timestamp_key,
            timestamp=timestamp,
        )
        line = encode_line(item)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        with FileLock(self.lock_path, timeout=self.lock_timeout):
            offset = self._append_bytes(line)
            archived = 0
            if self.max_entries is not None:
                result = archive_excess_locked(
                    self.path,
                    keep=self.max_entries,
                    archive_path=self.archive_path,
                )
                archived = result.archived_records
                if archived:
                    # Archiving rewrites the file, so the pre-append offset is
                    # no longer valid.  The new record is always one of the
                    # retained ("newest keep") records.
                    offset = self.path.stat().st_size - len(line)

        return AppendResult(offset=offset, record=item, archived_records=archived)

    def archive(self) -> ArchiveResult:
        """Archive excess records now, even if no append triggered it."""

        if self.max_entries is None:
            raise ValueError("max_entries is not configured")
        with FileLock(self.lock_path, timeout=self.lock_timeout):
            return archive_excess_locked(
                self.path,
                keep=self.max_entries,
                archive_path=self.archive_path,
            )

    def _append_bytes(self, data: bytes) -> int:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
        fd = os.open(self.path, flags, 0o644)
        try:
            offset = os.fstat(fd).st_size
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("os.write returned no progress")
                view = view[written:]
            if self.fsync:
                os.fsync(fd)
            return offset
        finally:
            os.close(fd)


def append_record(
    path: str | os.PathLike[str],
    record: Mapping[str, Any],
    *,
    timestamp: datetime | str | None | object = _UNSET,
    timestamp_key: str = DEFAULT_TIMESTAMP_KEY,
    max_entries: int | None = None,
    archive_path: str | os.PathLike[str] | None = None,
    lock_path: str | os.PathLike[str] | None = None,
    lock_timeout: float = 30.0,
    fsync: bool = False,
) -> AppendResult:
    """Convenience wrapper around :class:`LogWriter` for one append."""

    writer = LogWriter(
        path,
        timestamp_key=timestamp_key,
        max_entries=max_entries,
        archive_path=archive_path,
        lock_path=lock_path,
        lock_timeout=lock_timeout,
        fsync=fsync,
    )
    return writer.append(record, timestamp=timestamp)
