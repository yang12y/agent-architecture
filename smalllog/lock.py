"""Advisory inter-process file locks used by every writer and reader."""

from __future__ import annotations

import errno
import os
import time
from pathlib import Path

if os.name == "nt":  # pragma: no cover - exercised on Windows only
    import msvcrt
else:  # pragma: no cover - exercised on POSIX only
    import fcntl


_PATH_TYPES = (str, os.PathLike)


def lock_path_for(path: str | os.PathLike[str]) -> Path:
    """Return the sidecar lock path for a log file."""

    log_path = Path(path)
    return log_path.with_name(log_path.name + ".lock")


class FileLock:
    """A small advisory lock backed by ``flock`` (POSIX) or ``msvcrt`` (Windows).

    The lock is associated with a sidecar file, not with the JSONL file itself.
    That matters because archiving replaces the JSONL file with ``os.replace``;
    an advisory lock on the replaced inode would no longer protect the new file.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.01,
    ) -> None:
        if not isinstance(path, _PATH_TYPES):
            raise TypeError("lock path must be a path-like object")
        if timeout < 0:
            raise ValueError("timeout must be >= 0")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be > 0")
        self.path = Path(path)
        self.timeout = float(timeout)
        self.poll_interval = float(poll_interval)
        self._handle = None

    def acquire(self) -> None:
        if self._handle is not None:
            raise RuntimeError("FileLock is not re-entrant")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            # msvcrt.locking() cannot lock a zero-length file.
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()

            deadline = time.monotonic() + self.timeout
            while True:
                handle.seek(0)
                try:
                    self._acquire_handle(handle)
                    self._handle = handle
                    return
                except OSError as exc:
                    if not self._is_contention(exc):
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            f"timed out after {self.timeout:g}s waiting for {self.path}"
                        ) from exc
                    time.sleep(min(self.poll_interval, remaining))
        except BaseException:
            handle.close()
            raise

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            handle.seek(0)
            self._release_handle(handle)
        finally:
            handle.close()

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    @staticmethod
    def _acquire_handle(handle) -> None:
        if os.name == "nt":  # pragma: no cover - Windows only
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _release_handle(handle) -> None:
        if os.name == "nt":  # pragma: no cover - Windows only
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _is_contention(exc: OSError) -> bool:
        # EDEADLK is returned by some msvcrt versions when the byte range is busy.
        return exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
