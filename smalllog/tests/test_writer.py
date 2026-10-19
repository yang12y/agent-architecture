from __future__ import annotations

import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest
from smalllog import LogWriter
from smalllog.reader import read_from_offset

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_append_writes_jsonl_and_returns_final_offset(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    writer = LogWriter(path)
    source = {"n": 1}

    first = writer.append(source, timestamp="2024-01-01T00:00:00Z")
    second = writer.append({"n": 2}, timestamp="2024-01-01T00:00:01Z")

    assert source == {"n": 1}
    lines = path.read_bytes().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"n": 1, "timestamp": "2024-01-01T00:00:00Z"}
    assert first.offset == 0
    assert second.offset == len(lines[0]) + 1
    entries = read_from_offset(path, second.offset)
    assert [entry.value["n"] for entry in entries] == [2]


def test_max_entries_archives_oldest_and_returns_usable_offset(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    writer = LogWriter(path, max_entries=3)

    results = [
        writer.append({"n": number}, timestamp=f"2024-01-01T00:00:0{number}Z")
        for number in range(1, 6)
    ]

    active = read_from_offset(path)
    assert [entry.value["n"] for entry in active] == [3, 4, 5]
    assert [result.archived_records for result in results] == [0, 0, 0, 1, 1]
    archived = gzip.decompress((tmp_path / "app.log.gz").read_bytes()).splitlines()
    assert [json.loads(line)["n"] for line in archived] == [1, 2]
    assert read_from_offset(path, results[-1].offset)[0].value["n"] == 5


def test_archive_can_be_triggered_without_append(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    writer = LogWriter(path, max_entries=5)
    for number in range(5):
        writer.append({"n": number}, timestamp="2024-01-01T00:00:00Z")

    # Lowering max_entries after construction needs a new writer; archive()
    # still exercises the public on-demand path.
    smaller = LogWriter(path, max_entries=2)
    result = smaller.archive()

    assert result.archived_records == 3
    assert result.active_records == 2
    assert [entry.value["n"] for entry in read_from_offset(path)] == [3, 4]


def test_max_entries_rejects_values_below_one(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        LogWriter(tmp_path / "a.log", max_entries=0)
    with pytest.raises(TypeError):
        LogWriter(tmp_path / "a.log", max_entries=True)


def test_cross_process_appends_do_not_lose_or_duplicate_records(tmp_path: Path) -> None:
    path = tmp_path / "concurrent.log"
    script = """
import sys
from smalllog import LogWriter

path, worker, count = sys.argv[1], sys.argv[2], int(sys.argv[3])
writer = LogWriter(path, lock_timeout=20)
for index in range(count):
    writer.append(
        {"worker": worker, "index": index},
        timestamp="2024-01-01T00:00:00Z",
    )
"""
    workers = 4
    per_worker = 25
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(path),
                str(worker),
                str(per_worker),
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for worker in range(workers)
    ]

    failures = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=60)
        if process.returncode != 0:
            failures.append((process.returncode, stdout, stderr))
    assert failures == []

    entries = read_from_offset(path)
    pairs = {(entry.value["worker"], entry.value["index"]) for entry in entries}
    assert len(entries) == workers * per_worker
    assert len(pairs) == workers * per_worker
