from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from smalllog import FileLock

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_lock_is_reusable_and_validates_arguments(tmp_path: Path) -> None:
    path = tmp_path / "a.lock"
    lock = FileLock(path)
    with lock:
        assert path.exists()
    with FileLock(path, timeout=0):
        pass

    with pytest.raises(ValueError):
        FileLock(path, timeout=-1)
    with pytest.raises(ValueError):
        FileLock(path, poll_interval=0)
    with FileLock(path) as held:
        with pytest.raises(RuntimeError):
            held.acquire()


def test_another_process_times_out_while_lock_is_held(tmp_path: Path) -> None:
    path = tmp_path / "contended.lock"
    script = """
import sys
from smalllog import FileLock
try:
    with FileLock(sys.argv[1], timeout=0.2):
        print("acquired")
except TimeoutError:
    print("timeout")
"""
    with FileLock(path):
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(path)],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate(timeout=20)

    assert process.returncode == 0, stderr
    assert stdout.strip() == "timeout"
