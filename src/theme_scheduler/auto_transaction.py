"""Durable stage-4 automatic-run transaction contract."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .accent_theme import ThemeVisualState, theme_visual_state_from_dict
from .errors import DataError
from .persistence import atomic_write_json, load_json_object
from .state import AppState

AUTO_TRANSACTION_KIND = "themescheduler.auto-transaction"
AUTO_TRANSACTION_SCHEMA_VERSION = 1
AUTO_TRANSACTION_FILE_NAME = "auto.json"
AUTO_TRANSACTION_STATUSES = frozenset(
    {
        "planned",
        "windows-verified",
        "state-committed",
        "completed",
        "failed",
        "partial",
    }
)
TERMINAL_AUTO_STATUSES = frozenset({"completed", "failed", "partial"})
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_TRANSACTION_PATTERN = re.compile(r"accent-\d{8}T\d{6}-[0-9a-f]{8}")
_ERROR_PATTERN = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")
_ALLOWED_TRANSITIONS = {
    "planned": frozenset({"windows-verified", "failed", "partial"}),
    "windows-verified": frozenset({"state-committed", "failed", "partial"}),
    "state-committed": frozenset({"completed", "partial"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "partial": frozenset({"state-committed", "completed"}),
}


class AutoTransactionError(DataError):
    """Raised when an automatic transaction is malformed or unsafe."""


def _timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise AutoTransactionError(f"{field} must be ISO 8601.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AutoTransactionError(f"{field} must be ISO 8601.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AutoTransactionError(f"{field} must include a UTC offset.")
    return value


def json_document_sha256(payload: Mapping[str, Any]) -> str:
    content = (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class AutoTransaction:
    transaction_id: str
    status: str
    started_at: str
    updated_at: str
    target_profile: str
    target_apps_theme: str
    learn_profile: str | None
    state_before_sha256: str
    state_after: AppState
    state_after_sha256: str
    windows_target: ThemeVisualState | None = None
    error_code: str | None = None
    message: str | None = None
    rollback_succeeded: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(
            self.transaction_id, str
        ) or not _TRANSACTION_PATTERN.fullmatch(self.transaction_id):
            raise AutoTransactionError("transactionId is invalid.")
        if self.status not in AUTO_TRANSACTION_STATUSES:
            raise AutoTransactionError("Automatic transaction status is invalid.")
        _timestamp(self.started_at, "startedAt")
        _timestamp(self.updated_at, "updatedAt")
        if self.target_profile not in {"day", "night"}:
            raise AutoTransactionError("targetProfile is invalid.")
        if self.target_apps_theme not in {"light", "dark"}:
            raise AutoTransactionError("targetAppsTheme is invalid.")
        if self.learn_profile not in {None, "day", "night"}:
            raise AutoTransactionError("learnProfile is invalid.")
        if self.learn_profile == self.target_profile:
            raise AutoTransactionError("A transaction cannot learn its target.")
        if not _SHA256_PATTERN.fullmatch(self.state_before_sha256):
            raise AutoTransactionError("stateBeforeSha256 is invalid.")
        if not _SHA256_PATTERN.fullmatch(self.state_after_sha256):
            raise AutoTransactionError("stateAfterSha256 is invalid.")
        expected_after_hash = json_document_sha256(self.state_after.as_dict())
        if self.state_after_sha256 != expected_after_hash:
            raise AutoTransactionError("stateAfterSha256 does not bind stateAfter.")
        if (
            self.state_after.active_profile != self.target_profile
            or self.state_after.last_applied_profile != self.target_profile
            or self.state_after.last_result != "success"
        ):
            raise AutoTransactionError("stateAfter does not commit the target.")
        if (
            self.status in {"windows-verified", "state-committed", "completed"}
            and self.windows_target is None
        ):
            raise AutoTransactionError(f"{self.status} requires windowsTarget.")
        if self.status in {
            "planned",
            "windows-verified",
            "state-committed",
            "completed",
        } and any(
            value is not None
            for value in (
                self.error_code,
                self.message,
                self.rollback_succeeded,
            )
        ):
            raise AutoTransactionError(f"{self.status} cannot contain failure fields.")
        if self.status in {"failed", "partial"}:
            if not isinstance(self.error_code, str) or not _ERROR_PATTERN.fullmatch(
                self.error_code
            ):
                raise AutoTransactionError(
                    f"{self.status} requires a stable errorCode."
                )
            if (
                not isinstance(self.message, str)
                or not self.message
                or len(self.message) > 500
            ):
                raise AutoTransactionError(f"{self.status} requires a bounded message.")
        elif self.error_code is not None or self.message is not None:
            raise AutoTransactionError("Success states cannot contain an error.")
        if self.rollback_succeeded not in {None, True, False}:
            raise AutoTransactionError("rollbackSucceeded must be boolean or null.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": AUTO_TRANSACTION_KIND,
            "schemaVersion": AUTO_TRANSACTION_SCHEMA_VERSION,
            "transactionId": self.transaction_id,
            "status": self.status,
            "startedAt": self.started_at,
            "updatedAt": self.updated_at,
            "targetProfile": self.target_profile,
            "targetAppsTheme": self.target_apps_theme,
            "learnProfile": self.learn_profile,
            "stateBeforeSha256": self.state_before_sha256,
            "stateAfter": self.state_after.as_dict(),
            "stateAfterSha256": self.state_after_sha256,
            "windowsTarget": (
                self.windows_target.as_dict()
                if self.windows_target is not None
                else None
            ),
            "errorCode": self.error_code,
            "message": self.message,
            "rollbackSucceeded": self.rollback_succeeded,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AutoTransaction:
        expected = {
            "kind",
            "schemaVersion",
            "transactionId",
            "status",
            "startedAt",
            "updatedAt",
            "targetProfile",
            "targetAppsTheme",
            "learnProfile",
            "stateBeforeSha256",
            "stateAfter",
            "stateAfterSha256",
            "windowsTarget",
            "errorCode",
            "message",
            "rollbackSucceeded",
        }
        if set(payload) != expected:
            raise AutoTransactionError(
                "Automatic transaction fields do not match schema."
            )
        if payload.get("kind") != AUTO_TRANSACTION_KIND:
            raise AutoTransactionError(
                "JSON is not a ThemeScheduler automatic transaction."
            )
        if payload.get("schemaVersion") != AUTO_TRANSACTION_SCHEMA_VERSION:
            raise AutoTransactionError(
                "Unsupported automatic transaction schemaVersion."
            )
        state_after = payload.get("stateAfter")
        windows_target = payload.get("windowsTarget")
        if not isinstance(state_after, Mapping):
            raise AutoTransactionError("stateAfter must be an object.")
        if windows_target is not None and not isinstance(windows_target, Mapping):
            raise AutoTransactionError("windowsTarget must be null or an object.")
        return cls(
            transaction_id=payload.get("transactionId"),  # type: ignore[arg-type]
            status=payload.get("status"),  # type: ignore[arg-type]
            started_at=payload.get("startedAt"),  # type: ignore[arg-type]
            updated_at=payload.get("updatedAt"),  # type: ignore[arg-type]
            target_profile=payload.get("targetProfile"),  # type: ignore[arg-type]
            target_apps_theme=payload.get("targetAppsTheme"),  # type: ignore[arg-type]
            learn_profile=payload.get("learnProfile"),
            state_before_sha256=payload.get("stateBeforeSha256"),  # type: ignore[arg-type]
            state_after=AppState.from_dict(state_after),
            state_after_sha256=payload.get("stateAfterSha256"),  # type: ignore[arg-type]
            windows_target=(
                theme_visual_state_from_dict(windows_target)
                if windows_target is not None
                else None
            ),
            error_code=payload.get("errorCode"),
            message=payload.get("message"),
            rollback_succeeded=payload.get("rollbackSucceeded"),
        )


class AutoTransactionStore:
    """Create once, then allow only explicit adjacent status transitions."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def create(self, transaction: AutoTransaction) -> AutoTransaction:
        if transaction.status != "planned":
            raise AutoTransactionError("A new transaction must be planned.")
        atomic_write_json(self.path, transaction.as_dict())
        return self.load()

    def load(self) -> AutoTransaction:
        return AutoTransaction.from_dict(load_json_object(self.path))

    def save(self, transaction: AutoTransaction) -> AutoTransaction:
        current = self.load()
        allowed = _ALLOWED_TRANSITIONS[current.status]
        if transaction.status not in allowed:
            raise AutoTransactionError(
                f"Illegal automatic transaction transition: "
                f"{current.status} -> {transaction.status}."
            )
        immutable_current = current.as_dict()
        immutable_next = transaction.as_dict()
        for field in (
            "transactionId",
            "startedAt",
            "targetProfile",
            "targetAppsTheme",
            "learnProfile",
            "stateBeforeSha256",
            "stateAfter",
            "stateAfterSha256",
        ):
            if immutable_current[field] != immutable_next[field]:
                raise AutoTransactionError(
                    f"Automatic transaction field {field} is immutable."
                )
        atomic_write_json(self.path, transaction.as_dict(), force=True)
        written = self.load()
        if written != transaction:
            raise OSError(f"Automatic transaction readback mismatch: {self.path}")
        return written
