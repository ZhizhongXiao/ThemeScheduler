"""Strict six-hour deduplication state for error notifications."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .persistence import atomic_write_json, load_json_object

DEDUP_KIND = "themescheduler.notification-dedup"
DEDUP_SCHEMA_VERSION = 1
DEDUP_WINDOW = timedelta(hours=6)
MAX_ENTRIES = 64


def _whole_second(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("Notification dedup time must be a datetime.")
    _timestamp(value)
    return value.replace(microsecond=0)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Notification dedup time must include a UTC offset.")
    return value.isoformat(timespec="seconds")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("sentAt must be ISO 8601 text.")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("sentAt must include a UTC offset.")
    return parsed


def _key(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 129
        or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789.-|"
            for character in value
        )
    ):
        raise ValueError("Notification dedup key is invalid.")
    return value


@dataclass(frozen=True)
class NotificationDedupState:
    entries: tuple[tuple[str, datetime], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.entries, tuple):
            raise ValueError("Notification dedup entries must be a tuple.")
        normalized = tuple(
            (_key(key), _whole_second(instant)) for key, instant in self.entries
        )
        object.__setattr__(self, "entries", normalized)
        keys = []
        for key, _instant in normalized:
            keys.append(key)
        if len(keys) != len(set(keys)):
            raise ValueError("Notification dedup keys must be unique.")
        if tuple(sorted(normalized)) != normalized:
            raise ValueError("Notification dedup entries must be key-sorted.")
        if len(normalized) > MAX_ENTRIES:
            raise ValueError("Notification dedup state has too many entries.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": DEDUP_KIND,
            "schemaVersion": DEDUP_SCHEMA_VERSION,
            "entries": [
                {"key": key, "sentAt": _timestamp(instant)}
                for key, instant in self.entries
            ],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> NotificationDedupState:
        if set(payload) != {"kind", "schemaVersion", "entries"}:
            raise ValueError("Notification dedup fields do not match schema.")
        if payload.get("kind") != DEDUP_KIND:
            raise ValueError("JSON is not ThemeScheduler notification dedup state.")
        if payload.get("schemaVersion") != DEDUP_SCHEMA_VERSION:
            raise ValueError("Unsupported notification dedup schemaVersion.")
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list):
            raise ValueError("Notification dedup entries must be an array.")
        entries: list[tuple[str, datetime]] = []
        for raw in raw_entries:
            if not isinstance(raw, Mapping) or set(raw) != {
                "key",
                "sentAt",
            }:
                raise ValueError("Notification dedup entry is malformed.")
            entries.append(
                (
                    _key(raw.get("key")),
                    _parse_timestamp(raw.get("sentAt")),
                )
            )
        return cls(tuple(entries))


class NotificationDedupStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> NotificationDedupState:
        if not self.path.exists():
            return NotificationDedupState(())
        return NotificationDedupState.from_dict(load_json_object(self.path))

    def should_send(self, key: str, *, now: datetime) -> bool:
        validated = _key(key)
        current = self.load()
        sent = dict(current.entries).get(validated)
        return sent is None or now - sent >= DEDUP_WINDOW

    def record(self, key: str, *, now: datetime) -> NotificationDedupState:
        validated = _key(key)
        _timestamp(now)
        current = self.load()
        cutoff = now - DEDUP_WINDOW
        values = {
            item_key: instant
            for item_key, instant in current.entries
            if instant > cutoff
        }
        values[validated] = now
        newest = sorted(
            values.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:MAX_ENTRIES]
        target = NotificationDedupState(tuple(sorted(newest, key=lambda item: item[0])))
        atomic_write_json(
            self.path,
            target.as_dict(),
            force=self.path.exists(),
        )
        written = self.load()
        if written != target:
            raise OSError("Notification dedup readback mismatch.")
        return written
