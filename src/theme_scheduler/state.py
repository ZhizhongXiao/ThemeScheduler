"""Strict version-1 runtime state contract and conservative trust boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import DataError
from .persistence import (
    JsonDocumentError,
    Migrator,
    atomic_write_json,
    load_json_object,
    migrate_json_file,
)

STATE_KIND = "themescheduler.state"
STATE_SCHEMA_VERSION = 1
PROFILE_NAMES = frozenset({"day", "night"})
RESULT_NAMES = frozenset({"never", "success", "partial", "failed"})


class StateValidationError(DataError):
    """Raised when a state document violates the frozen schema."""


class UntrustedStateError(DataError, RuntimeError):
    """Raised when automatic changes must stop because state is not trustworthy."""


def _timestamp_or_none(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise StateValidationError(f"{field} must be null or an ISO 8601 string.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise StateValidationError(f"{field} is not valid ISO 8601.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StateValidationError(f"{field} must include a UTC offset.")
    return value


def _profile_or_none(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in PROFILE_NAMES:
        raise StateValidationError(f"{field} must be day, night, or null.")
    return value


@dataclass(frozen=True)
class AppState:
    paused: bool
    active_profile: str | None
    last_run_at: str | None
    last_applied_profile: str | None
    last_result: str

    def __post_init__(self) -> None:
        if not isinstance(self.paused, bool):
            raise StateValidationError("paused must be boolean.")
        _profile_or_none(self.active_profile, "activeProfile")
        _profile_or_none(self.last_applied_profile, "lastAppliedProfile")
        _timestamp_or_none(self.last_run_at, "lastRunAt")
        if (
            not isinstance(self.last_result, str)
            or self.last_result not in RESULT_NAMES
        ):
            raise StateValidationError("lastResult is unsupported.")
        if self.last_result == "never" and (
            self.last_run_at is not None or self.last_applied_profile is not None
        ):
            raise StateValidationError(
                "lastResult=never requires null lastRunAt and lastAppliedProfile."
            )
        if self.last_result != "never" and self.last_run_at is None:
            raise StateValidationError("A completed run result requires lastRunAt.")
        if self.last_result == "success" and self.last_applied_profile is None:
            raise StateValidationError("A successful run requires lastAppliedProfile.")

    @classmethod
    def initial(cls) -> AppState:
        """Explicit installation-time state; never an implicit recovery default."""

        return cls(False, None, None, None, "never")

    @classmethod
    def pending_initial_setup(cls) -> AppState:
        """Safe first-install state until the GUI commits user choices."""

        return cls(True, None, None, None, "never")

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": STATE_KIND,
            "schemaVersion": STATE_SCHEMA_VERSION,
            "paused": self.paused,
            "activeProfile": self.active_profile,
            "lastRunAt": self.last_run_at,
            "lastAppliedProfile": self.last_applied_profile,
            "lastResult": self.last_result,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AppState:
        expected = {
            "kind",
            "schemaVersion",
            "paused",
            "activeProfile",
            "lastRunAt",
            "lastAppliedProfile",
            "lastResult",
        }
        if set(payload) != expected:
            raise StateValidationError(
                "State fields do not match schema; "
                f"missing={sorted(expected - set(payload))}, "
                f"unknown={sorted(set(payload) - expected)}."
            )
        if payload.get("kind") != STATE_KIND:
            raise StateValidationError("JSON is not ThemeScheduler state.")
        if payload.get("schemaVersion") != STATE_SCHEMA_VERSION:
            raise StateValidationError("Unsupported state schemaVersion.")
        return cls(
            paused=payload.get("paused"),  # type: ignore[arg-type]
            active_profile=_profile_or_none(
                payload.get("activeProfile"), "activeProfile"
            ),
            last_run_at=_timestamp_or_none(payload.get("lastRunAt"), "lastRunAt"),
            last_applied_profile=_profile_or_none(
                payload.get("lastAppliedProfile"), "lastAppliedProfile"
            ),
            last_result=payload.get("lastResult"),  # type: ignore[arg-type]
        )


def load_trusted_state(path: Path) -> AppState:
    """Load state or block automatic changes; never synthesize recovery state."""

    try:
        return AppState.from_dict(load_json_object(path))
    except (OSError, JsonDocumentError, StateValidationError) as exc:
        raise UntrustedStateError(
            "State is missing or invalid; automatic system changes are blocked."
        ) from exc


class StateStore:
    """State writes are allowed only after explicit initialization or trusted load."""

    def __init__(
        self, path: Path, *, migrations: Mapping[int, Migrator] | None = None
    ) -> None:
        self.path = Path(path)
        self.migrations = dict(migrations or {})

    def initialize(self, state: AppState | None = None) -> AppState:
        value = state or AppState.initial()
        atomic_write_json(self.path, value.as_dict())
        return self.load()

    def load(self) -> AppState:
        return load_trusted_state(self.path)

    def save(self, state: AppState) -> AppState:
        self.load()
        atomic_write_json(self.path, state.as_dict(), force=True)
        written = self.load()
        if written != state:
            raise OSError(f"State readback mismatch: {self.path}")
        return written

    def migrate(self) -> AppState:
        try:
            payload = migrate_json_file(
                self.path,
                kind=STATE_KIND,
                current_version=STATE_SCHEMA_VERSION,
                migrations=self.migrations,
                validator=AppState.from_dict,
            )
            return AppState.from_dict(payload)
        except Exception as exc:
            raise UntrustedStateError(
                "State migration failed; automatic system changes are blocked."
            ) from exc
