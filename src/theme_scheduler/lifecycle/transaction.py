"""Lifecycle transaction state and transition contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..persistence import atomic_write_json, load_json_object
from ._validation import (
    _ALLOWED_LIFECYCLE_TRANSITIONS,
    _ERROR_PATTERN,
    INSTALL_CONTRACT_SCHEMA_VERSION,
    INSTALL_OPERATIONS,
    LIFECYCLE_STATUSES,
    LIFECYCLE_TRANSACTION_KIND,
    InstallContractError,
    JsonObject,
    _absolute_path,
    _exact,
    _parsed_timestamp,
    _require_schema_version,
    _sha256,
    _validate_transaction_id,
    _version,
    _version_key,
)
from .layout import InstallLayout


@dataclass(frozen=True)
class LifecycleTransaction:
    transaction_id: str
    operation: str
    status: str
    started_at: str
    updated_at: str
    program_root: str
    data_root: str
    from_version: str | None
    to_version: str | None
    payload_manifest_sha256: str | None
    error_code: str | None = None
    message: str | None = None
    rollback_succeeded: bool | None = None

    def __post_init__(self) -> None:
        _validate_transaction_id(self.transaction_id)
        if self.operation not in INSTALL_OPERATIONS:
            raise InstallContractError("Lifecycle operation is invalid.")
        if self.status not in LIFECYCLE_STATUSES:
            raise InstallContractError("Lifecycle status is invalid.")
        started = _parsed_timestamp(self.started_at, "startedAt")
        updated = _parsed_timestamp(self.updated_at, "updatedAt")
        if updated < started:
            raise InstallContractError("updatedAt cannot precede startedAt.")
        InstallLayout(
            _absolute_path(self.program_root, "programRoot"),
            _absolute_path(self.data_root, "dataRoot"),
        )
        if self.operation == "install":
            if self.from_version is not None or self.to_version is None:
                raise InstallContractError(
                    "install requires null fromVersion and a toVersion."
                )
        elif self.operation == "uninstall":
            if self.from_version is None or self.to_version is not None:
                raise InstallContractError(
                    "uninstall requires fromVersion and null toVersion."
                )
        else:
            if self.from_version is None or self.to_version is None:
                raise InstallContractError(f"{self.operation} requires both versions.")
            if self.operation == "reinstall" and self.from_version != self.to_version:
                raise InstallContractError("reinstall requires identical versions.")
            if self.operation == "upgrade" and self.from_version == self.to_version:
                raise InstallContractError("upgrade requires different versions.")
        if self.from_version is not None:
            _version(self.from_version, "fromVersion")
        if self.to_version is not None:
            _version(self.to_version, "toVersion")
        if (
            self.operation == "upgrade"
            and self.from_version is not None
            and self.to_version is not None
            and _version_key(self.to_version) <= _version_key(self.from_version)
        ):
            raise InstallContractError("upgrade requires a newer toVersion.")
        if self.operation == "uninstall":
            if self.payload_manifest_sha256 is not None:
                raise InstallContractError(
                    "uninstall cannot contain payloadManifestSha256."
                )
        else:
            _sha256(
                self.payload_manifest_sha256,
                "payloadManifestSha256",
            )
        failure_state = self.status in {"failed", "partial", "rolled-back"}
        if failure_state:
            if not isinstance(self.error_code, str) or not _ERROR_PATTERN.fullmatch(
                self.error_code
            ):
                raise InstallContractError(
                    f"{self.status} requires a stable errorCode."
                )
            if (
                not isinstance(self.message, str)
                or not self.message
                or len(self.message) > 500
            ):
                raise InstallContractError(f"{self.status} requires a bounded message.")
        elif any(
            value is not None
            for value in (
                self.error_code,
                self.message,
                self.rollback_succeeded,
            )
        ):
            raise InstallContractError(
                "Successful lifecycle states cannot contain failure fields."
            )
        if self.rollback_succeeded not in {None, True, False}:
            raise InstallContractError("rollbackSucceeded must be boolean or null.")
        if self.status == "rolled-back" and self.rollback_succeeded is not True:
            raise InstallContractError("rolled-back requires rollbackSucceeded=true.")
        if self.status == "partial" and self.rollback_succeeded is True:
            raise InstallContractError("partial cannot claim successful rollback.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": LIFECYCLE_TRANSACTION_KIND,
            "schemaVersion": INSTALL_CONTRACT_SCHEMA_VERSION,
            "transactionId": self.transaction_id,
            "operation": self.operation,
            "status": self.status,
            "startedAt": self.started_at,
            "updatedAt": self.updated_at,
            "programRoot": self.program_root,
            "dataRoot": self.data_root,
            "fromVersion": self.from_version,
            "toVersion": self.to_version,
            "payloadManifestSha256": self.payload_manifest_sha256,
            "errorCode": self.error_code,
            "message": self.message,
            "rollbackSucceeded": self.rollback_succeeded,
        }

    @classmethod
    def from_dict(cls, payload: JsonObject) -> LifecycleTransaction:
        _exact(
            payload,
            {
                "kind",
                "schemaVersion",
                "transactionId",
                "operation",
                "status",
                "startedAt",
                "updatedAt",
                "programRoot",
                "dataRoot",
                "fromVersion",
                "toVersion",
                "payloadManifestSha256",
                "errorCode",
                "message",
                "rollbackSucceeded",
            },
            "lifecycle transaction",
        )
        if payload.get("kind") != LIFECYCLE_TRANSACTION_KIND:
            raise InstallContractError(
                "JSON is not a ThemeScheduler lifecycle transaction."
            )
        _require_schema_version(payload, "lifecycle transaction")
        return cls(
            transaction_id=payload["transactionId"],
            operation=payload["operation"],
            status=payload["status"],
            started_at=payload["startedAt"],
            updated_at=payload["updatedAt"],
            program_root=payload["programRoot"],
            data_root=payload["dataRoot"],
            from_version=payload["fromVersion"],
            to_version=payload["toVersion"],
            payload_manifest_sha256=payload["payloadManifestSha256"],
            error_code=payload["errorCode"],
            message=payload["message"],
            rollback_succeeded=payload["rollbackSucceeded"],
        )


class LifecycleTransactionStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def create(self, transaction: LifecycleTransaction) -> LifecycleTransaction:
        if transaction.status != "planned":
            raise InstallContractError("A new lifecycle transaction must be planned.")
        atomic_write_json(self.path, transaction.as_dict())
        return self.load()

    def load(self) -> LifecycleTransaction:
        return LifecycleTransaction.from_dict(load_json_object(self.path))

    def save(self, transaction: LifecycleTransaction) -> LifecycleTransaction:
        current = self.load()
        if transaction.status not in _ALLOWED_LIFECYCLE_TRANSITIONS[current.status]:
            raise InstallContractError(
                "Illegal lifecycle transaction transition: "
                f"{current.status} -> {transaction.status}."
            )
        current_payload = current.as_dict()
        next_payload = transaction.as_dict()
        for field in (
            "transactionId",
            "operation",
            "startedAt",
            "programRoot",
            "dataRoot",
            "fromVersion",
            "toVersion",
            "payloadManifestSha256",
        ):
            if current_payload[field] != next_payload[field]:
                raise InstallContractError(
                    f"Lifecycle transaction field is immutable: {field}."
                )
        atomic_write_json(self.path, next_payload, force=True)
        written = self.load()
        if written != transaction:
            raise InstallContractError("Lifecycle transaction readback mismatch.")
        return written
