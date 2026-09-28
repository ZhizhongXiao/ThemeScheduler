"""Strict contracts for the independent Stage 8.5 uninstaller."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .errors import ContractError
from .lifecycle import InstallContractError, InstallLayout
from .persistence import atomic_write_json, load_json_object

UNINSTALL_REQUEST_KIND = "themescheduler.uninstall-request"
UNINSTALL_JOURNAL_KIND = "themescheduler.uninstall-journal"
UNINSTALL_SCHEMA_VERSION = 1
UNINSTALL_STEPS = (
    "task-removed",
    "appearance-handled",
    "shortcuts-removed",
    "registration-removed",
    "program-root-removed",
    "user-data-cleaned",
    "self-cleanup-scheduled",
)
UNINSTALL_STATUSES = frozenset(
    {"planned", "prepared", "mutating", "partial", "completed"}
)
_TRANSACTION_PATTERN = re.compile(r"^uninstall-[0-9a-f]{32}$")
_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class UninstallContractError(ContractError):
    """Raised when an uninstall request or journal is untrusted."""


class AppearanceChoice(str, Enum):  # noqa: UP042 - Preserve str(Enum) output pending a dedicated migration.
    KEEP = "keep-current-appearance"
    RESTORE = "restore-pre-install-app-mode-and-accent"


def _exact(
    payload: Mapping[str, Any],
    fields: set[str],
    label: str,
) -> None:
    if set(payload) != fields:
        raise UninstallContractError(
            f"{label} fields do not match schema; "
            f"missing={sorted(fields - set(payload))}, "
            f"unknown={sorted(set(payload) - fields)}."
        )


def _timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise UninstallContractError(f"{label} is invalid.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise UninstallContractError(f"{label} must be ISO 8601.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise UninstallContractError(f"{label} must include a UTC offset.")
    return value


def _transaction_id(value: Any) -> str:
    if not isinstance(value, str) or not _TRANSACTION_PATTERN.fullmatch(value):
        raise UninstallContractError("transactionId is invalid.")
    return value


def _token(value: Any) -> str:
    if not isinstance(value, str) or not _TOKEN_PATTERN.fullmatch(value):
        raise UninstallContractError("authorizationToken is invalid.")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise UninstallContractError(f"{label} is invalid.")
    return value


def _message(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 500:
        raise UninstallContractError(
            "Journal errors must be non-empty and at most 500 characters."
        )
    return value


@dataclass(frozen=True)
class UninstallOptions:
    appearance: AppearanceChoice
    keep_config_and_profiles: bool
    keep_logs: bool

    def __post_init__(self) -> None:
        if not isinstance(self.appearance, AppearanceChoice):
            raise UninstallContractError("appearance choice is invalid.")
        if not isinstance(self.keep_config_and_profiles, bool):
            raise UninstallContractError("keepConfigAndProfiles must be boolean.")
        if not isinstance(self.keep_logs, bool):
            raise UninstallContractError("keepLogs must be boolean.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "appearance": self.appearance.value,
            "keepConfigAndProfiles": self.keep_config_and_profiles,
            "keepLogs": self.keep_logs,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> UninstallOptions:
        _exact(
            payload,
            {"appearance", "keepConfigAndProfiles", "keepLogs"},
            "uninstall options",
        )
        try:
            appearance = AppearanceChoice(payload.get("appearance"))
        except (TypeError, ValueError) as exc:
            raise UninstallContractError("appearance choice is unsupported.") from exc
        return cls(
            appearance,
            payload.get("keepConfigAndProfiles"),  # type: ignore[arg-type]
            payload.get("keepLogs"),  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class UninstallRequest:
    transaction_id: str
    created_at: str
    program_root: str
    data_root: str
    launcher_process_id: int
    source_uninstaller_sha256: str
    cleanup_script_sha256: str
    authorization_token: str
    options: UninstallOptions

    def __post_init__(self) -> None:
        _transaction_id(self.transaction_id)
        _timestamp(self.created_at, "createdAt")
        if (
            isinstance(self.launcher_process_id, bool)
            or not isinstance(self.launcher_process_id, int)
            or self.launcher_process_id <= 0
        ):
            raise UninstallContractError(
                "launcherProcessId must be a positive integer."
            )
        _sha256(
            self.source_uninstaller_sha256,
            "sourceUninstallerSha256",
        )
        _sha256(
            self.cleanup_script_sha256,
            "cleanupScriptSha256",
        )
        _token(self.authorization_token)
        if not isinstance(self.options, UninstallOptions):
            raise UninstallContractError("options are invalid.")
        try:
            layout = InstallLayout(
                Path(self.program_root),
                Path(self.data_root),
            )
        except (InstallContractError, OSError, ValueError) as exc:
            raise UninstallContractError(f"Uninstall roots are invalid: {exc}") from exc
        object.__setattr__(self, "program_root", str(layout.program_root))
        object.__setattr__(self, "data_root", str(layout.data_root))

    @property
    def layout(self) -> InstallLayout:
        return InstallLayout(
            Path(self.program_root),
            Path(self.data_root),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": UNINSTALL_REQUEST_KIND,
            "schemaVersion": UNINSTALL_SCHEMA_VERSION,
            "transactionId": self.transaction_id,
            "createdAt": self.created_at,
            "programRoot": self.program_root,
            "dataRoot": self.data_root,
            "launcherProcessId": self.launcher_process_id,
            "sourceUninstallerSha256": self.source_uninstaller_sha256,
            "cleanupScriptSha256": self.cleanup_script_sha256,
            "authorizationToken": self.authorization_token,
            "options": self.options.as_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> UninstallRequest:
        _exact(
            payload,
            {
                "kind",
                "schemaVersion",
                "transactionId",
                "createdAt",
                "programRoot",
                "dataRoot",
                "launcherProcessId",
                "sourceUninstallerSha256",
                "cleanupScriptSha256",
                "authorizationToken",
                "options",
            },
            "uninstall request",
        )
        if payload.get("kind") != UNINSTALL_REQUEST_KIND:
            raise UninstallContractError(
                "JSON is not a ThemeScheduler uninstall request."
            )
        if payload.get("schemaVersion") != UNINSTALL_SCHEMA_VERSION:
            raise UninstallContractError("Uninstall request schema is unsupported.")
        options = payload.get("options")
        if not isinstance(options, Mapping):
            raise UninstallContractError("options must be an object.")
        return cls(
            transaction_id=payload.get("transactionId"),  # type: ignore[arg-type]
            created_at=payload.get("createdAt"),  # type: ignore[arg-type]
            program_root=payload.get("programRoot"),  # type: ignore[arg-type]
            data_root=payload.get("dataRoot"),  # type: ignore[arg-type]
            launcher_process_id=payload.get(  # type: ignore[arg-type]
                "launcherProcessId"
            ),
            source_uninstaller_sha256=payload.get(  # type: ignore[arg-type]
                "sourceUninstallerSha256"
            ),
            cleanup_script_sha256=payload.get(  # type: ignore[arg-type]
                "cleanupScriptSha256"
            ),
            authorization_token=payload.get(  # type: ignore[arg-type]
                "authorizationToken"
            ),
            options=UninstallOptions.from_dict(options),
        )


@dataclass(frozen=True)
class UninstallPlan:
    layout: InstallLayout
    options: UninstallOptions
    program_targets: tuple[Path, ...]
    data_delete_targets: tuple[Path, ...]
    data_preserve_targets: tuple[Path, ...]
    remove_entire_data_root: bool
    unknown_data_preserved: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "themescheduler.uninstall-plan",
            "schemaVersion": UNINSTALL_SCHEMA_VERSION,
            "programRoot": str(self.layout.program_root),
            "dataRoot": str(self.layout.data_root),
            "options": self.options.as_dict(),
            "programTargets": [str(path) for path in self.program_targets],
            "dataDeleteTargets": [str(path) for path in self.data_delete_targets],
            "dataPreserveTargets": [str(path) for path in self.data_preserve_targets],
            "removeEntireDataRoot": self.remove_entire_data_root,
            "unknownDataPreserved": self.unknown_data_preserved,
            "windowsChanged": False,
            "filesChanged": False,
        }


def build_uninstall_plan(
    layout: InstallLayout,
    options: UninstallOptions,
) -> UninstallPlan:
    """Create the fixed cleanup matrix without touching the filesystem."""

    if not isinstance(layout, InstallLayout):
        raise UninstallContractError("layout is invalid.")
    if not isinstance(options, UninstallOptions):
        raise UninstallContractError("options are invalid.")
    remove_root = not options.keep_config_and_profiles and not options.keep_logs
    if remove_root:
        delete_targets = (layout.data_root,)
        preserve_targets: tuple[Path, ...] = ()
    else:
        delete: list[Path] = [
            layout.data_root / "runtime",
            layout.data_root / "WebView2",
        ]
        preserve: list[Path] = []
        config_targets = (
            layout.data_root / "config.json",
            layout.data_root / "state.json",
            layout.data_root / "profiles",
            layout.data_root / "initial-setup.json",
        )
        if options.keep_config_and_profiles:
            preserve.extend(config_targets)
        else:
            delete.extend(config_targets)
        if options.keep_logs:
            preserve.append(layout.data_root / "logs")
        else:
            delete.append(layout.data_root / "logs")
        backup = layout.data_root / "backup"
        if (
            options.appearance is AppearanceChoice.RESTORE
            or not options.keep_config_and_profiles
        ):
            delete.append(backup)
        else:
            preserve.append(backup)
        delete_targets = tuple(delete)
        preserve_targets = tuple(preserve)
    return UninstallPlan(
        layout=layout,
        options=options,
        program_targets=(
            layout.app,
            layout.maintenance,
            layout.metadata,
            layout.program_root,
        ),
        data_delete_targets=delete_targets,
        data_preserve_targets=preserve_targets,
        remove_entire_data_root=remove_root,
        unknown_data_preserved=not remove_root,
    )


@dataclass(frozen=True)
class UninstallJournal:
    transaction_id: str
    status: str
    started_at: str
    updated_at: str
    request: UninstallRequest
    completed_steps: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _transaction_id(self.transaction_id)
        if self.status not in UNINSTALL_STATUSES:
            raise UninstallContractError("journal status is invalid.")
        started = datetime.fromisoformat(_timestamp(self.started_at, "startedAt"))
        updated = datetime.fromisoformat(_timestamp(self.updated_at, "updatedAt"))
        if updated < started:
            raise UninstallContractError("updatedAt cannot precede startedAt.")
        if (
            not isinstance(self.request, UninstallRequest)
            or self.request.transaction_id != self.transaction_id
        ):
            raise UninstallContractError("journal request transaction does not match.")
        if not isinstance(self.completed_steps, tuple):
            raise UninstallContractError("completedSteps must be a tuple.")
        expected_prefix = UNINSTALL_STEPS[: len(self.completed_steps)]
        if self.completed_steps != expected_prefix:
            raise UninstallContractError("completedSteps must be an ordered prefix.")
        if not isinstance(self.errors, tuple):
            raise UninstallContractError("errors must be a tuple.")
        for error in self.errors:
            _message(error)
        if self.status == "partial" and not self.errors:
            raise UninstallContractError("partial journal requires an error.")
        if self.status != "partial" and self.errors:
            raise UninstallContractError("Only a partial journal may contain errors.")
        if self.status == "completed" and (self.completed_steps != UNINSTALL_STEPS):
            raise UninstallContractError("completed journal requires every step.")

    @classmethod
    def create(
        cls,
        request: UninstallRequest,
    ) -> UninstallJournal:
        return cls(
            transaction_id=request.transaction_id,
            status="planned",
            started_at=request.created_at,
            updated_at=request.created_at,
            request=request,
        )

    def advance(
        self,
        step: str,
        *,
        updated_at: str,
        status: str = "mutating",
    ) -> UninstallJournal:
        if self.status in {"partial", "completed"}:
            raise UninstallContractError("Terminal journal cannot advance.")
        index = len(self.completed_steps)
        if index >= len(UNINSTALL_STEPS) or UNINSTALL_STEPS[index] != step:
            raise UninstallContractError("Uninstall step is out of order.")
        completed = (*self.completed_steps, step)
        target_status = "completed" if completed == UNINSTALL_STEPS else status
        return replace(
            self,
            status=target_status,
            updated_at=updated_at,
            completed_steps=completed,
        )

    def with_status(
        self,
        status: str,
        *,
        updated_at: str,
    ) -> UninstallJournal:
        if status not in {"prepared", "mutating"}:
            raise UninstallContractError(
                "Only prepared or mutating status can be set directly."
            )
        if self.status in {"partial", "completed"}:
            raise UninstallContractError("Terminal journal cannot change status.")
        return replace(self, status=status, updated_at=updated_at)

    def fail(
        self,
        message: str,
        *,
        updated_at: str,
    ) -> UninstallJournal:
        return replace(
            self,
            status="partial",
            updated_at=updated_at,
            errors=(_message(message),),
        )

    def resume(self, *, updated_at: str) -> UninstallJournal:
        if self.status != "partial":
            raise UninstallContractError("Only a partial journal can resume.")
        return replace(
            self,
            status="mutating",
            updated_at=updated_at,
            errors=(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": UNINSTALL_JOURNAL_KIND,
            "schemaVersion": UNINSTALL_SCHEMA_VERSION,
            "transactionId": self.transaction_id,
            "status": self.status,
            "startedAt": self.started_at,
            "updatedAt": self.updated_at,
            "request": self.request.as_dict(),
            "completedSteps": list(self.completed_steps),
            "errors": list(self.errors),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> UninstallJournal:
        _exact(
            payload,
            {
                "kind",
                "schemaVersion",
                "transactionId",
                "status",
                "startedAt",
                "updatedAt",
                "request",
                "completedSteps",
                "errors",
            },
            "uninstall journal",
        )
        if payload.get("kind") != UNINSTALL_JOURNAL_KIND:
            raise UninstallContractError(
                "JSON is not a ThemeScheduler uninstall journal."
            )
        if payload.get("schemaVersion") != UNINSTALL_SCHEMA_VERSION:
            raise UninstallContractError("Uninstall journal schema is unsupported.")
        request = payload.get("request")
        completed = payload.get("completedSteps")
        errors = payload.get("errors")
        if not isinstance(request, Mapping):
            raise UninstallContractError("journal request must be an object.")
        if not isinstance(completed, list) or not all(
            isinstance(item, str) for item in completed
        ):
            raise UninstallContractError("completedSteps must be an array of strings.")
        if not isinstance(errors, list) or not all(
            isinstance(item, str) for item in errors
        ):
            raise UninstallContractError("errors must be an array of strings.")
        return cls(
            transaction_id=payload.get("transactionId"),  # type: ignore[arg-type]
            status=payload.get("status"),  # type: ignore[arg-type]
            started_at=payload.get("startedAt"),  # type: ignore[arg-type]
            updated_at=payload.get("updatedAt"),  # type: ignore[arg-type]
            request=UninstallRequest.from_dict(request),
            completed_steps=tuple(completed),
            errors=tuple(errors),
        )


class UninstallJournalStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def create(self, journal: UninstallJournal) -> UninstallJournal:
        atomic_write_json(self.path, journal.as_dict())
        return self.load()

    def load(self) -> UninstallJournal:
        return UninstallJournal.from_dict(load_json_object(self.path))

    def replace(
        self,
        expected: UninstallJournal,
        updated: UninstallJournal,
    ) -> UninstallJournal:
        current = self.load()
        if current != expected:
            raise UninstallContractError(
                "Uninstall journal changed before replacement."
            )
        atomic_write_json(self.path, updated.as_dict(), force=True)
        written = self.load()
        if written != updated:
            raise UninstallContractError("Uninstall journal readback mismatch.")
        return written
