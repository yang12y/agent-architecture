from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from smalllog.codec import (
    decode_line,
    encode_line,
    parse_timestamp,
    prepare_record,
    timestamp_of,
)


def test_prepare_record_adds_utc_timestamp_without_mutating_input() -> None:
    source = {"message": "你好"}

    item = prepare_record(source)

    assert source == {"message": "你好"}
    assert item["message"] == "你好"
    parsed = parse_timestamp(item["timestamp"])
    assert parsed is not None
    assert parsed.tzinfo is UTC
    assert abs(datetime.now(UTC) - parsed) < timedelta(minutes=1)


def test_prepare_record_preserves_existing_or_explicit_timestamp() -> None:
    assert prepare_record({"timestamp": "keep-me"})["timestamp"] == "keep-me"

    explicit = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=8)))
    item = prepare_record({"timestamp": "old"}, timestamp=explicit)
    assert item["timestamp"] == "2024-01-01T19:04:05.000000Z"


def test_prepare_record_can_write_json_null_timestamp() -> None:
    assert prepare_record({}, timestamp=None)["timestamp"] is None


def test_encode_line_is_compact_utf8_jsonl() -> None:
    line = encode_line({"message": "你好", "n": 1})

    assert line.endswith(b"\n")
    assert line.count(b"\n") == 1
    assert json.loads(line) == {"message": "你好", "n": 1}
    assert "你好".encode() in line


def test_encode_line_rejects_non_strict_json() -> None:
    with pytest.raises(ValueError, match="strict JSON"):
        encode_line({"bad": float("nan")})
    with pytest.raises(ValueError, match="strict JSON"):
        encode_line({"bad": object()})


def test_decode_line_distinguishes_valid_null_from_error() -> None:
    valid_null = decode_line(b"null\n", offset=10)
    assert valid_null.ok
    assert valid_null.value is None
    assert valid_null.offset == 10
    assert valid_null.end_offset == 15

    broken = decode_line(b'{"n":\n')
    assert not broken.ok
    assert "JSONDecodeError" in broken.error

    nonstandard = decode_line(b'{"n":NaN}\n')
    assert not nonstandard.ok
    assert "non-standard" in nonstandard.error


def test_decode_line_tolerates_blank_non_utf8_and_deep_json() -> None:
    blank = decode_line(b"\r\n")
    assert not blank.ok
    assert blank.error == "blank line"

    binary = decode_line(b"\xff\xfe\n")
    assert not binary.ok
    assert "UnicodeDecodeError" in binary.error

    deeply_nested = b"[" * 3000 + b"]" * 3000 + b"\n"
    assert not decode_line(deeply_nested).ok


def test_parse_timestamp_accepts_iso_naive_epoch_and_rejects_garbage() -> None:
    assert parse_timestamp("2024-01-02T03:04:05Z") == datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert parse_timestamp("2024-01-02T03:04:05") == datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert parse_timestamp(0) == datetime(1970, 1, 1, tzinfo=UTC)
    assert parse_timestamp(True) is None
    assert parse_timestamp(float("nan")) is None
    assert parse_timestamp("not-a-date") is None
    assert parse_timestamp(object()) is None


def test_timestamp_of_requires_mapping_and_key() -> None:
    stamp = datetime(2024, 1, 1, tzinfo=UTC)
    assert timestamp_of({"ts": "2024-01-01T00:00:00Z"}, "ts") == stamp
    assert timestamp_of(["2024-01-01T00:00:00Z"], "ts") is None
    assert timestamp_of({"ts": "bad"}, "ts") is None
