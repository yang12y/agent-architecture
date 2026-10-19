from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from smalllog import ArchiveError, LogWriter, archive_old_entries
from smalllog.reader import read_from_offset


def _values(payload: bytes) -> list[int]:
    return [json.loads(line)["n"] for line in payload.splitlines()]


def test_archive_keeps_newest_valid_records_and_preserves_corrupt_lines(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mixed.log"
    path.write_bytes(
        b'{"n":1}\n'
        b"garbage\n"
        b'{"n":2}\n'
        b"\n"
        b'{"n":3}\n'
        b'{"n":4}\n'
    )
    archive_path = tmp_path / "old.jsonl.gz"

    result = archive_old_entries(path, keep=2, archive_path=archive_path)

    assert result.archived_records == 2
    assert result.active_records == 2
    assert _values(gzip.decompress(archive_path.read_bytes())) == [1, 2]
    assert [entry.value["n"] for entry in read_from_offset(path)] == [3, 4]
    active = path.read_bytes()
    assert b"garbage\n" in active
    assert active.startswith(b"garbage\n")


def test_repeated_archives_accumulate_in_one_valid_gzip(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    archive_path = tmp_path / "app.log.gz"
    path.write_bytes(b'{"n":1}\n{"n":2}\n{"n":3}\n{"n":4}\n')

    archive_old_entries(path, keep=2, archive_path=archive_path)
    with path.open("ab") as handle:
        handle.write(b'{"n":5}\n{"n":6}\n')
    archive_old_entries(path, keep=2, archive_path=archive_path)

    assert _values(gzip.decompress(archive_path.read_bytes())) == [1, 2, 3, 4]
    assert _values(path.read_bytes()) == [5, 6]


def test_keep_zero_archives_all_valid_records_but_keeps_corrupt_data(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_bytes(b'{"n":1}\nnot-json\n{"n":2}\n')

    result = archive_old_entries(path, keep=0)

    assert result.archived_records == 2
    assert result.active_records == 0
    assert _values(gzip.decompress((tmp_path / "app.log.gz").read_bytes())) == [1, 2]
    assert path.read_bytes() == b"not-json\n"


def test_archive_is_a_noop_when_within_limit_or_file_missing(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_bytes(b'{"n":1}\n{"n":2}\n')

    result = archive_old_entries(path, keep=2)
    assert (result.archived_records, result.active_records) == (0, 2)
    assert not (tmp_path / "app.log.gz").exists()

    missing = archive_old_entries(tmp_path / "missing.log", keep=1)
    assert (missing.archived_records, missing.active_records) == (0, 0)


def test_bad_existing_archive_fails_without_touching_active_log(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    archive_path = tmp_path / "app.log.gz"
    original = b'{"n":1}\n{"n":2}\n{"n":3}\n'
    path.write_bytes(original)
    archive_path.write_bytes(b"this is not gzip")

    with pytest.raises(ArchiveError):
        archive_old_entries(path, keep=1, archive_path=archive_path)

    assert path.read_bytes() == original


def test_writer_archive_path_must_differ_from_log_path(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    with pytest.raises(ValueError):
        LogWriter(path, max_entries=1, archive_path=path)


def test_archive_rejects_invalid_keep(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        archive_old_entries(tmp_path / "a.log", keep=-1)
    with pytest.raises(TypeError):
        archive_old_entries(tmp_path / "a.log", keep=True)
