"""Strict daily and one-time trigger contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ._validation import _boolean, _exact_keys, _text
from .constants import _TIME_PATTERN
from .errors import SchedulerContractError


@dataclass(frozen=True)
class DailyTrigger:
    trigger_id: str
    local_time: str
    days_interval: int = 1
    enabled: bool = True
    trigger_type: str = "Daily"

    def __post_init__(self) -> None:
        _text(self.trigger_id, "task.trigger.id")
        if not isinstance(self.local_time, str) or not _TIME_PATTERN.fullmatch(
            self.local_time
        ):
            raise SchedulerContractError("Trigger localTime must use HH:mm.")
        if (
            not isinstance(self.days_interval, int)
            or isinstance(self.days_interval, bool)
            or self.days_interval < 1
        ):
            raise SchedulerContractError("Trigger daysInterval must be positive.")
        if not isinstance(self.enabled, bool):
            raise SchedulerContractError("Trigger enabled must be boolean.")
        _text(self.trigger_type, "task.trigger.type")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.trigger_id,
            "localTime": self.local_time,
            "daysInterval": self.days_interval,
            "enabled": self.enabled,
            "type": self.trigger_type,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DailyTrigger:
        _exact_keys(
            payload,
            {"id", "localTime", "daysInterval", "enabled", "type"},
            "task.trigger",
        )
        return cls(
            _text(payload["id"], "task.trigger.id"),
            _text(payload["localTime"], "task.trigger.localTime"),
            payload["daysInterval"],
            _boolean(payload["enabled"], "task.trigger.enabled"),
            _text(payload["type"], "task.trigger.type"),
        )


def _aware_timestamp(value: Any, location: str) -> str:
    text = _text(value, location)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SchedulerContractError(f"{location} must be valid ISO 8601.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SchedulerContractError(f"{location} must include a UTC offset.")
    if parsed.second != 0 or parsed.microsecond != 0:
        raise SchedulerContractError(f"{location} must be aligned to a whole minute.")
    return parsed.isoformat(timespec="seconds")


@dataclass(frozen=True)
class TimeTrigger:
    trigger_id: str
    start_at: str
    enabled: bool = True
    trigger_type: str = "Time"

    def __post_init__(self) -> None:
        _text(self.trigger_id, "task.trigger.id")
        object.__setattr__(
            self,
            "start_at",
            _aware_timestamp(self.start_at, "task.trigger.startAt"),
        )
        if not isinstance(self.enabled, bool):
            raise SchedulerContractError("Trigger enabled must be boolean.")
        _text(self.trigger_type, "task.trigger.type")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.trigger_id,
            "startAt": self.start_at,
            "enabled": self.enabled,
            "type": self.trigger_type,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TimeTrigger:
        _exact_keys(
            payload,
            {"id", "startAt", "enabled", "type"},
            "task.trigger",
        )
        return cls(
            _text(payload["id"], "task.trigger.id"),
            _text(payload["startAt"], "task.trigger.startAt"),
            _boolean(payload["enabled"], "task.trigger.enabled"),
            _text(payload["type"], "task.trigger.type"),
        )


type TaskTrigger = DailyTrigger | TimeTrigger


def _trigger_from_dict(payload: Mapping[str, Any]) -> TaskTrigger:
    trigger_type = payload.get("type")
    if trigger_type == "Daily":
        return DailyTrigger.from_dict(payload)
    if trigger_type == "Time":
        return TimeTrigger.from_dict(payload)
    raise SchedulerContractError(f"Unsupported task trigger type: {trigger_type!r}.")
