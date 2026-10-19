"""Compress the oldest valid records into a gzip archive."""

from __future__ import annotations

import gzip
import os
import stat
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from .codec import decode_line
from .lock import FileLock, lock_path_for


class ArchiveError(RuntimeError):
    """Raised when an existing archive cannot be read or a rewrite fails."""


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    archived_records: int
    active_records: int
    archive_path: Path


def archive_old_entries(
    path: str | os.PathLike[str],
    *,
    keep: int,
    archive_path: str | os.PathLike[str] | None = None,
    lock_path: str | os.PathLike[str] | None = None,
    lock_timeout: float = 30.0,
) -> ArchiveResult:
    """Archive old records while holding the log's inter-process lock."""

    log_path = Path(path)
    lock = Path(lock_path) if lock_path is not None else lock_path_for(log_path)
    if lock.resolve() == log_path.resolve():
        raise ValueError("lock_path must differ from the JSONL path")
    target = (
        Path(archive_path)
        if archive_path is not None
        else log_path.with_name(log_path.name + ".gz")
    )
    if lock.resolve() == target.resolve():
        raise ValueError("lock_path must differ from the archive path")
    with FileLock(lock, timeout=lock_timeout):
        return archive_excess_locked(
            log_path,
            keep=keep,
            archive_path=archive_path,
        )


def archive_excess_locked(
    path: str | os.PathLike[str],
    *,
    keep: int,
    archive_path: str | os.PathLike[str] | None = None,
) -> ArchiveResult:
    """Archive all but the newest ``keep`` valid records.

    The caller must already hold the log's lock.  Corrupt lines are never
    deleted: they stay in the active JSONL file, while only valid JSON records
    count toward ``keep``.

    Archive is written before the active file is replaced.  A crash between the
    two steps can duplicate records (the archive has them and the active file
    still has them), but cannot lose them.
    """

    if isinstance(keep, bool) or not isinstance(keep, int):
        raise TypeError("keep must be an integer")
    if keep < 0:
        raise ValueError("keep must be >= 0")

    log_path = Path(path)
    target = (
        Path(archive_path)
        if archive_path is not None
        else log_path.with_name(log_path.name + ".gz")
    )
    if target.resolve() == log_path.resolve():
        raise ValueError("archive_path must differ from the JSONL path")
    if not log_path.exists():
        return ArchiveResult(0, 0, target)

    data = log_path.read_bytes()
    lines = _split_lf_lines(data)
    decoded = [decode_line(raw, offset=0) for raw in lines]
    valid_indexes = [index for index, line in enumerate(decoded) if line.ok]
    active_valid = len(valid_indexes)
    archive_count = max(0, active_valid - keep)
    if archive_count == 0:
        return ArchiveResult(0, active_valid, target)

    selected = set(valid_indexes[:archive_count])
    archive_payload = bytearray()
    retained = bytearray()
    for index, line in enumerate(decoded):
        if index in selected:
            archive_payload.extend(line.raw)
            if not line.raw.endswith(b"\n"):
                archive_payload.extend(b"\n")
        else:
            retained.extend(line.raw)

    old_archive = b""
    if target.exists():
        try:
            old_archive = gzip.decompress(target.read_bytes())
        except (OSError, EOFError, zlib.error) as exc:
            raise ArchiveError(f"cannot read existing archive {target}: {exc}") from exc

    try:
        # Rewriting one gzip member (instead of appending a gzip member) keeps
        # the archive valid even if an earlier process died mid-write.
        new_archive = gzip.compress(
            old_archive + bytes(archive_payload),
            compresslevel=9,
            mtime=0,
        )
    except (OSError, EOFError, zlib.error) as exc:
        raise ArchiveError(f"cannot compress archive {target}: {exc}") from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    archive_mode = _mode_or_none(target)
    if archive_mode is None:
        archive_mode = 0o644
    log_mode = _mode_or_none(log_path)

    # Ordering is deliberate: archive first, active file second.
    _atomic_write(target, new_archive, mode=archive_mode)
    _atomic_write(log_path, bytes(retained), mode=log_mode)
    return ArchiveResult(archive_count, active_valid - archive_count, target)


def _split_lf_lines(data: bytes) -> list[bytes]:
    """Split on LF only, preserving line endings and a final unterminated line."""

    if not data:
        return []
    parts = data.split(b"\n")
    lines = [part + b"\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _mode_or_none(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return None


def _atomic_write(path: Path, data: bytes, *, mode: int | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temp_path, mode)
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _fsync_directory(path: Path) -> None:
    """Persist a rename on POSIX; Windows has no equivalent and skips this."""

    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        # Some filesystems do not support fsync on directory handles.
        pass
    finally:
        os.close(fd)
