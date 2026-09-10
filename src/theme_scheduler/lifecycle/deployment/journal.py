"""Durable deployment journal contract and transition store."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...persistence import atomic_write_json, load_json_object
from .._validation import InstallContractError
from ._shared import (
    _COMPONENTS,
    _JOURNAL_STATUSES,
    _JOURNAL_TRANSITIONS,
    _TRANSACTION_ID_PATTERN,
    DEPLOYMENT_JOURNAL_KIND,
    DEPLOYMENT_JOURNAL_SCHEMA_VERSION,
    _sha256,
    _validate_direct_name,
)


@dataclass(frozen=True)
class DeploymentJournal:
    transaction_id: str
    operation: str
    status: str
    old_entries: tuple[str, ...]
    old_tree_sha256: str
    old_tree_entry_count: int
    old_moved: tuple[str, ...] = ()
    new_activated: tuple[str, ...] = ()
    error_code: str | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        # Reuse the lifecycle transaction ID validator via a harmless layout
        # path construction performed by callers; validate syntax locally too.
        if not isinstance(
            self.transaction_id, str
        ) or not _TRANSACTION_ID_PATTERN.fullmatch(self.transaction_id):
            raise InstallContractError("Deployment transactionId is invalid.")
        if self.operation not in {"install", "upgrade", "reinstall"}:
            raise InstallContractError("Deployment operation is invalid.")
        if self.status not in _JOURNAL_STATUSES:
            raise InstallContractError("Deployment journal status is invalid.")
        for label, values in (
            ("oldEntries", self.old_entries),
            ("oldMoved", self.old_moved),
            ("newActivated", self.new_activated),
        ):
            if not isinstance(values, tuple):
                raise InstallContractError(f"{label} must be an array.")
            if len({value.casefold() for value in values}) != len(values):
                raise InstallContractError(f"{label} contains duplicates.")
            for value in values:
                _validate_direct_name(value)
        if tuple(sorted(self.old_entries, key=str.casefold)) != self.old_entries:
            raise InstallContractError("oldEntries must be sorted.")
        if any(value not in self.old_entries for value in self.old_moved):
            raise InstallContractError("oldMoved must be a subset of oldEntries.")
        expected_activated = tuple(
            component for component in _COMPONENTS if component in self.new_activated
        )
        if self.new_activated != expected_activated:
            raise InstallContractError(
                "newActivated must use canonical component order."
            )
        _sha256(self.old_tree_sha256, "oldTreeSha256")
        if (
            isinstance(self.old_tree_entry_count, bool)
            or not isinstance(self.old_tree_entry_count, int)
            or self.old_tree_entry_count < 0
        ):
            raise InstallContractError(
                "oldTreeEntryCount must be a non-negative integer."
            )
        failed = self.status in {"failed", "partial", "rolled-back"}
        if failed:
            if (
                not isinstance(self.error_code, str)
                or not self.error_code
                or len(self.error_code) > 100
                or not isinstance(self.message, str)
                or not self.message
                or len(self.message) > 500
            ):
                raise InstallContractError(
                    "Failed deployment journal requires bounded error details."
                )
        elif self.error_code is not None or self.message is not None:
            raise InstallContractError(
                "Successful deployment journal cannot contain errors."
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": DEPLOYMENT_JOURNAL_KIND,
            "schemaVersion": DEPLOYMENT_JOURNAL_SCHEMA_VERSION,
            "transactionId": self.transaction_id,
            "operation": self.operation,
            "status": self.status,
            "oldEntries": list(self.old_entries),
            "oldTreeSha256": self.old_tree_sha256,
            "oldTreeEntryCount": self.old_tree_entry_count,
            "oldMoved": list(self.old_moved),
            "newActivated": list(self.new_activated),
            "errorCode": self.error_code,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DeploymentJournal:
        expected = {
            "kind",
            "schemaVersion",
            "transactionId",
            "operation",
            "status",
            "oldEntries",
            "oldTreeSha256",
            "oldTreeEntryCount",
            "oldMoved",
            "newActivated",
            "errorCode",
            "message",
        }
        if set(payload) != expected:
            raise InstallContractError("Deployment journal fields do not match schema.")
        if (
            payload.get("kind") != DEPLOYMENT_JOURNAL_KIND
            or payload.get("schemaVersion") != DEPLOYMENT_JOURNAL_SCHEMA_VERSION
        ):
            raise InstallContractError("Deployment journal identity is invalid.")
        arrays: dict[str, tuple[str, ...]] = {}
        for field in ("oldEntries", "oldMoved", "newActivated"):
            value = payload.get(field)
            if not isinstance(value, list) or any(
                not isinstance(item, str) for item in value
            ):
                raise InstallContractError(
                    f"Deployment journal {field} must be a string array."
                )
            arrays[field] = tuple(value)
        return cls(
            transaction_id=payload.get("transactionId"),  # type: ignore[arg-type]
            operation=payload.get("operation"),  # type: ignore[arg-type]
            status=payload.get("status"),  # type: ignore[arg-type]
            old_entries=arrays["oldEntries"],
            old_tree_sha256=payload.get("oldTreeSha256"),  # type: ignore[arg-type]
            old_tree_entry_count=payload.get("oldTreeEntryCount"),  # type: ignore[arg-type]
            old_moved=arrays["oldMoved"],
            new_activated=arrays["newActivated"],
            error_code=payload.get("errorCode"),
            message=payload.get("message"),
        )


class DeploymentJournalStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def create(self, journal: DeploymentJournal) -> DeploymentJournal:
        atomic_write_json(self.path, journal.as_dict())
        return self.load()

    def load(self) -> DeploymentJournal:
        return DeploymentJournal.from_dict(load_json_object(self.path))

    def save(self, journal: DeploymentJournal) -> DeploymentJournal:
        current = self.load()
        if (
            current.transaction_id != journal.transaction_id
            or current.operation != journal.operation
            or current.old_entries != journal.old_entries
            or current.old_tree_sha256 != journal.old_tree_sha256
            or current.old_tree_entry_count != journal.old_tree_entry_count
        ):
            raise InstallContractError("Deployment journal immutable identity changed.")
        if journal.status not in _JOURNAL_TRANSITIONS[current.status]:
            raise InstallContractError(
                "Illegal deployment journal transition: "
                f"{current.status} -> {journal.status}."
            )
        if not set(current.old_moved).issubset(journal.old_moved) or not set(
            current.new_activated
        ).issubset(journal.new_activated):
            raise InstallContractError(
                "Deployment progress arrays cannot move backwards."
            )
        atomic_write_json(self.path, journal.as_dict(), force=True)
        written = self.load()
        if written != journal:
            raise InstallContractError("Deployment journal readback mismatch.")
        return written
