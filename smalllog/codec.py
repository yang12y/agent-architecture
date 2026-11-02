"""JSON encoding, tolerant decoding, and timestamp helpers."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

DEFAULT_TIMESTAMP_KEY = "timestamp"
_MISSING = object()


@dataclass(frozen=True, slots=True)
class DecodedLine:
    """One physical JSONL line and its decode result.

    ``error is None`` means the line is valid UTF-8 JSON.  A valid JSON ``null``
    has ``value is None`` but still has ``error is None``.
    """

    offset: int
    end_offset: int
    raw: bytes
    value: Any
    error: str | None

    @property
    def ok(self) -> bool:
        return self.error is None


def format_timestamp(value: datetime) -> str:
    """Format a datetime as an ISO-8601 UTC string with a trailing ``Z``."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def utc_now_iso() -> str:
    return format_timestamp(datetime.now(UTC))


def parse_timestamp(value: Any) -> datetime | None:
    """Best-effort conversion of a log timestamp to an aware datetime.

    ISO-8601 strings and Unix timestamps are accepted.  Naive datetimes and
    strings are interpreted as UTC.  Invalid values return ``None`` so callers
    can skip that record without treating the whole file as broken.
    """

    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, bool):
            return None
        elif isinstance(value, (int, float)):
            if not math.isfinite(value):
                return None
            parsed = datetime.fromtimestamp(value, tz=UTC)
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.endswith(("Z", "z")):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
        else:
            return None
    except (OSError, OverflowError, TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def timestamp_of(value: Any, key: str = DEFAULT_TIMESTAMP_KEY) -> datetime | None:
    if not isinstance(value, Mapping):
        return None
    return parse_timestamp(value.get(key))


def prepare_record(
    record: Mapping[str, Any],
    *,
    timestamp_key: str = DEFAULT_TIMESTAMP_KEY,
    timestamp: datetime | str | None | object = _MISSING,
) -> dict[str, Any]:
    """Copy a record and add/normalize its timestamp.

    If ``timestamp`` is omitted, an existing timestamp is preserved and a
    missing one is filled with the current UTC time.  Passing ``None``
    explicitly writes JSON ``null``; that is useful for tests and for callers
    that deliberately want a record without a usable time.
    """

    if not isinstance(record, Mapping):
        raise TypeError("record must be a mapping")
    item = dict(record)
    if timestamp is _MISSING:
        item.setdefault(timestamp_key, utc_now_iso())
    elif timestamp is None:
        item[timestamp_key] = None
    elif isinstance(timestamp, datetime):
        item[timestamp_key] = format_timestamp(timestamp)
    elif isinstance(timestamp, str):
        item[timestamp_key] = timestamp
    else:
        raise TypeError("timestamp must be a datetime, string, or None")
    return item


def encode_line(record: Mapping[str, Any]) -> bytes:
    """Serialize one record as compact UTF-8 JSON plus a newline."""

    try:
        text = json.dumps(
            record,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"record is not strict JSON: {exc}") from exc
    return text.encode("utf-8") + b"\n"


def _reject_nonstandard_json_constant(value: str) -> None:
    # Python's JSON decoder accepts NaN/Infinity by default; JSONL should not.
    raise ValueError(f"non-standard JSON constant: {value}")


def decode_line(raw: bytes, *, offset: int = 0) -> DecodedLine:
    """Decode one physical line without raising for malformed input."""

    error: str | None = None
    value: Any = None
    try:
        text = raw.decode("utf-8")
        if not text.strip():
            error = "blank line"
        else:
            value = json.loads(text, parse_constant=_reject_nonstandard_json_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    return DecodedLine(
        offset=offset,
        end_offset=offset + len(raw),
        raw=raw,
        value=value,
        error=error,
    )
