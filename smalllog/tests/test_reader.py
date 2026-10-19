from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from smalllog import LogWriter
from smalllog.reader import LogReader, read_from_offset, read_time_range


def _write_corrupt_log(path: Path) -> list[int]:
    chunks = [
        b'{"n":1,"timestamp":"2024-01-01T00:00:00Z"}\n',
        b"broken-json\n",
        b"\n",
        b'{"n":2,"timestamp":"2024-01-01T01:00:00Z"}\n',
        b"\xff\xfe\n",
        b'{"n":3}\n',
    ]
    path.write_bytes(b"".join(chunks))
    offsets = []
    position = 0
    for chunk in chunks:
        offsets.append(position)
        position += len(chunk)
    return offsets


def test_iter_lines_keeps_corrupt_lines_but_iter_entries_skips_them(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.log"
    _write_corrupt_log(path)
    reader = LogReader(path)

    lines = list(reader.iter_lines())
    assert len(lines) == 6
    assert [line.ok for line in lines] == [True, False, False, True, False, True]

    entries = list(reader.iter_entries())
    assert [entry.value["n"] for entry in entries] == [1, 2, 3]


def test_read_from_offset_starts_on_a_record_boundary(tmp_path: Path) -> None:
    path = tmp_path / "offset.log"
    offsets = _write_corrupt_log(path)

    entries = read_from_offset(path, offsets[3])
    assert [entry.value["n"] for entry in entries] == [2, 3]

    # Any byte inside a line, or the newline that ends it, drops that partial
    # line and resumes at the next complete one.
    assert [entry.value["n"] for entry in read_from_offset(path, 5)] == [2, 3]
    assert [entry.value["n"] for entry in read_from_offset(path, offsets[3] - 1)] == [2, 3]
    assert read_from_offset(path, path.stat().st_size) == []
    assert read_from_offset(path, 10_000) == []


def test_read_from_offset_limit_and_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "offset.log"
    _write_corrupt_log(path)

    assert [entry.value["n"] for entry in read_from_offset(path, limit=1)] == [1]
    assert read_from_offset(path, limit=0) == []
    assert read_from_offset(tmp_path / "missing.log") == []


def test_time_range_is_half_open_and_skips_bad_timestamps(tmp_path: Path) -> None:
    path = tmp_path / "time.log"
    writer = LogWriter(path)
    writer.append({"n": 1}, timestamp=datetime(2024, 1, 1, 0, tzinfo=UTC))
    writer.append({"n": 2}, timestamp=datetime(2024, 1, 1, 1, tzinfo=UTC))
    writer.append({"n": 3}, timestamp=datetime(2024, 1, 1, 2, tzinfo=UTC))
    writer.append({"n": 4}, timestamp=None)
    writer.append({"n": 5}, timestamp="not-a-date")
    reader = LogReader(path)

    selected = reader.read_time_range(
        "2024-01-01T00:30:00",
        "2024-01-01T02:00:00Z",
    )
    assert [entry.value["n"] for entry in selected] == [2]

    # Naive bounds are UTC; start is inclusive.
    selected = reader.read_time_range(datetime(2024, 1, 1, 2), None)
    assert [entry.value["n"] for entry in selected] == [3]

    assert reader.read_time_range(limit=0) == []
    assert [entry.value["n"] for entry in reader.read_time_range(limit=1)] == [1]
    assert [entry.value["n"] for entry in reader.read_time_range()] == [1, 2, 3, 4, 5]


def test_time_range_validation(tmp_path: Path) -> None:
    path = tmp_path / "time.log"
    writer = LogWriter(path)
    writer.append({"n": 1}, timestamp="2024-01-01T00:00:00Z")
    reader = LogReader(path)

    with pytest.raises(ValueError, match="start"):
        reader.read_time_range("2024-01-02T00:00:00Z", "2024-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="valid"):
        reader.read_time_range("not-a-date")
    assert reader.read_time_range(
        "2024-01-01T00:00:00Z",
        "2024-01-01T00:00:00Z",
    ) == []
    with pytest.raises(ValueError):
        reader.read_from_offset(-1)
    with pytest.raises(ValueError):
        reader.read_from_offset(limit=-1)


def test_module_level_time_range_wrapper(tmp_path: Path) -> None:
    path = tmp_path / "time.log"
    LogWriter(path).append({"n": 1}, timestamp="2024-01-01T00:00:00Z")

    entries = read_time_range(
        path,
        "2024-01-01T00:00:00Z",
        "2024-01-02T00:00:00Z",
    )
    assert [entry.value["n"] for entry in entries] == [1]
