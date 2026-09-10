"""Trusted one-time decision state for interactive scheduled switches."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from .errors import DataError
from .notification_protocol import NotificationAction
from .persistence import JsonDocumentError, atomic_write_json, load_json_object

SWITCH_OVERRIDE_KIND = "themescheduler.pending-switch"
SWITCH_OVERRIDE_SCHEMA_VERSION = 1
PREPARE_LEAD = timedelta(minutes=5)
DELAY_INTERVAL = timedelta(minutes=30)
PROFILE_NAMES = frozenset({"day", "night"})


class SwitchOverrideError(DataError):
    """Raised when pending-switch state or a requested transition is invalid."""


class SwitchDecision(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SKIPPED = "skipped"


class ExecutionDecision(str, Enum):
    WAIT = "wait"
    APPLY = "apply"
    SKIP = "skip"
    SUPERSEDED = "superseded"


def _aware_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise SwitchOverrideError(f"{field} must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise SwitchOverrideError(f"{field} must include a UTC offset.")
    return value


def _parse_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise SwitchOverrideError(f"{field} must be an ISO 8601 string.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SwitchOverrideError(f"{field} is not valid ISO 8601.") from exc
    return _aware_datetime(parsed, field)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _whole_second(value: datetime, field: str) -> datetime:
    """Canonicalize persisted decision timestamps to schema precision."""

    return _aware_datetime(value, field).replace(microsecond=0)


def _token_or_none(value: object) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) != 32
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SwitchOverrideError(
            "actionToken must be null or 32 lowercase hexadecimal characters."
        )
    return value


@dataclass(frozen=True)
class PendingSwitch:
    """One fixed switch plus any user-requested 30-minute deferrals."""

    target_profile: str
    original_scheduled_at: datetime
    scheduled_at: datetime
    next_fixed_at: datetime
    decision: SwitchDecision
    defer_count: int
    action_token: str | None
    prepared_at: datetime | None
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.target_profile not in PROFILE_NAMES:
            raise SwitchOverrideError("targetProfile must be day or night.")
        original = _whole_second(self.original_scheduled_at, "originalScheduledAt")
        scheduled = _whole_second(self.scheduled_at, "scheduledAt")
        next_fixed = _whole_second(self.next_fixed_at, "nextFixedAt")
        updated = _whole_second(self.updated_at, "updatedAt")
        object.__setattr__(self, "original_scheduled_at", original)
        object.__setattr__(self, "scheduled_at", scheduled)
        object.__setattr__(self, "next_fixed_at", next_fixed)
        object.__setattr__(self, "updated_at", updated)
        if not isinstance(self.decision, SwitchDecision):
            raise SwitchOverrideError("decision is unsupported.")
        if (
            isinstance(self.defer_count, bool)
            or not isinstance(self.defer_count, int)
            or self.defer_count < 0
        ):
            raise SwitchOverrideError("deferCount must be a non-negative integer.")
        if scheduled != original + DELAY_INTERVAL * self.defer_count:
            raise SwitchOverrideError(
                "scheduledAt must match originalScheduledAt plus all deferrals."
            )
        if scheduled >= next_fixed:
            raise SwitchOverrideError("scheduledAt must be before nextFixedAt.")
        if updated >= scheduled:
            raise SwitchOverrideError("updatedAt must precede scheduledAt.")
        token = _token_or_none(self.action_token)
        prepared = self.prepared_at
        if prepared is not None:
            prepared = _whole_second(prepared, "preparedAt")
            object.__setattr__(self, "prepared_at", prepared)
        if self.decision is SwitchDecision.PENDING:
            if (token is None) != (prepared is None):
                raise SwitchOverrideError(
                    "A prepared pending switch requires both token and preparedAt."
                )
            if prepared is not None and not (self.prepare_at <= prepared < scheduled):
                raise SwitchOverrideError(
                    "preparedAt must be within the five-minute decision window."
                )
        elif token is not None or prepared is not None:
            raise SwitchOverrideError(
                "A decided switch cannot retain an action token or preparedAt."
            )

    @property
    def prepare_at(self) -> datetime:
        return self.scheduled_at - PREPARE_LEAD

    @property
    def can_delay(self) -> bool:
        return self.scheduled_at + DELAY_INTERVAL < self.next_fixed_at

    @classmethod
    def create(
        cls,
        *,
        target_profile: str,
        scheduled_at: datetime,
        next_fixed_at: datetime,
        now: datetime,
    ) -> PendingSwitch:
        return cls(
            target_profile=target_profile,
            original_scheduled_at=scheduled_at,
            scheduled_at=scheduled_at,
            next_fixed_at=next_fixed_at,
            decision=SwitchDecision.PENDING,
            defer_count=0,
            action_token=None,
            prepared_at=None,
            updated_at=now,
        )

    def arm(self, *, now: datetime, token: str) -> PendingSwitch:
        """Open the five-minute action window for this execution point."""

        current = _aware_datetime(now, "now")
        validated_token = _token_or_none(token)
        if validated_token is None:
            raise SwitchOverrideError("A non-null action token is required.")
        if self.decision is not SwitchDecision.PENDING:
            raise SwitchOverrideError("The switch already has a decision.")
        if self.action_token is not None:
            raise SwitchOverrideError("The switch is already prepared.")
        if not self.prepare_at <= current < self.scheduled_at:
            raise SwitchOverrideError(
                "The switch can only be prepared during its five-minute window."
            )
        return replace(
            self,
            action_token=validated_token,
            prepared_at=current,
            updated_at=current,
        )

    def apply_action(
        self,
        action: NotificationAction,
        *,
        token: str,
        now: datetime,
    ) -> PendingSwitch:
        """Consume one notification action and invalidate its token."""

        current = _aware_datetime(now, "now")
        if not isinstance(action, NotificationAction):
            raise SwitchOverrideError("Notification action is unsupported.")
        if self.decision is not SwitchDecision.PENDING:
            raise SwitchOverrideError("The switch already has a decision.")
        if self.action_token is None or token != self.action_token:
            raise SwitchOverrideError(
                "Notification action token is absent, stale, or invalid."
            )
        if current >= self.scheduled_at:
            raise SwitchOverrideError("Notification action has expired.")
        if action is NotificationAction.CONFIRM:
            return replace(
                self,
                decision=SwitchDecision.CONFIRMED,
                action_token=None,
                prepared_at=None,
                updated_at=current,
            )
        if action is NotificationAction.SKIP:
            return replace(
                self,
                decision=SwitchDecision.SKIPPED,
                action_token=None,
                prepared_at=None,
                updated_at=current,
            )

        delayed_at = self.scheduled_at + DELAY_INTERVAL
        if not self.can_delay:
            raise SwitchOverrideError(
                "Delay would reach or cross the next fixed boundary."
            )
        return replace(
            self,
            scheduled_at=delayed_at,
            defer_count=self.defer_count + 1,
            action_token=None,
            prepared_at=None,
            updated_at=current,
        )

    def execution_decision(self, *, now: datetime) -> ExecutionDecision:
        current = _aware_datetime(now, "now")
        if current >= self.next_fixed_at:
            return ExecutionDecision.SUPERSEDED
        if current < self.scheduled_at:
            return ExecutionDecision.WAIT
        if self.decision is SwitchDecision.SKIPPED:
            return ExecutionDecision.SKIP
        return ExecutionDecision.APPLY

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": SWITCH_OVERRIDE_KIND,
            "schemaVersion": SWITCH_OVERRIDE_SCHEMA_VERSION,
            "targetProfile": self.target_profile,
            "originalScheduledAt": _timestamp(self.original_scheduled_at),
            "scheduledAt": _timestamp(self.scheduled_at),
            "nextFixedAt": _timestamp(self.next_fixed_at),
            "decision": self.decision.value,
            "deferCount": self.defer_count,
            "actionToken": self.action_token,
            "preparedAt": (
                _timestamp(self.prepared_at) if self.prepared_at is not None else None
            ),
            "updatedAt": _timestamp(self.updated_at),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PendingSwitch:
        expected = {
            "kind",
            "schemaVersion",
            "targetProfile",
            "originalScheduledAt",
            "scheduledAt",
            "nextFixedAt",
            "decision",
            "deferCount",
            "actionToken",
            "preparedAt",
            "updatedAt",
        }
        if set(payload) != expected:
            raise SwitchOverrideError(
                "Pending-switch fields do not match schema; "
                f"missing={sorted(expected - set(payload))}, "
                f"unknown={sorted(set(payload) - expected)}."
            )
        if payload.get("kind") != SWITCH_OVERRIDE_KIND:
            raise SwitchOverrideError(
                "JSON is not ThemeScheduler pending-switch state."
            )
        if payload.get("schemaVersion") != SWITCH_OVERRIDE_SCHEMA_VERSION:
            raise SwitchOverrideError("Unsupported pending-switch schemaVersion.")
        try:
            decision = SwitchDecision(payload.get("decision"))
        except (TypeError, ValueError) as exc:
            raise SwitchOverrideError("decision is unsupported.") from exc
        prepared_value = payload.get("preparedAt")
        return cls(
            target_profile=payload.get("targetProfile"),  # type: ignore[arg-type]
            original_scheduled_at=_parse_timestamp(
                payload.get("originalScheduledAt"), "originalScheduledAt"
            ),
            scheduled_at=_parse_timestamp(payload.get("scheduledAt"), "scheduledAt"),
            next_fixed_at=_parse_timestamp(payload.get("nextFixedAt"), "nextFixedAt"),
            decision=decision,
            defer_count=payload.get("deferCount"),  # type: ignore[arg-type]
            action_token=_token_or_none(payload.get("actionToken")),
            prepared_at=(
                None
                if prepared_value is None
                else _parse_timestamp(prepared_value, "preparedAt")
            ),
            updated_at=_parse_timestamp(payload.get("updatedAt"), "updatedAt"),
        )


class PendingSwitchStore:
    """Persist pending-switch state with strict readback and no defaults."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> PendingSwitch:
        try:
            return PendingSwitch.from_dict(load_json_object(self.path))
        except (OSError, JsonDocumentError, SwitchOverrideError) as exc:
            raise SwitchOverrideError(
                "Pending-switch state is missing or invalid."
            ) from exc

    def create(self, value: PendingSwitch) -> PendingSwitch:
        atomic_write_json(self.path, value.as_dict())
        return self._verify(value)

    def save(self, value: PendingSwitch) -> PendingSwitch:
        self.load()
        atomic_write_json(self.path, value.as_dict(), force=True)
        return self._verify(value)

    def clear(self, expected: PendingSwitch | None = None) -> None:
        if expected is not None and self.load() != expected:
            raise SwitchOverrideError(
                "Pending-switch state changed before it could be cleared."
            )
        self.path.unlink(missing_ok=True)
        if self.path.exists():
            raise OSError(
                f"Pending-switch state still exists after deletion: {self.path}"
            )

    def _verify(self, expected: PendingSwitch) -> PendingSwitch:
        written = self.load()
        if written != expected:
            raise OSError(f"Pending-switch readback mismatch: {self.path}")
        return written
