"""Shared validation primitives for lifecycle contracts."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from ..errors import ContractError

type JsonObject = Mapping[str, Any]

PRODUCT_ID = "ThemeScheduler"
PAYLOAD_MANIFEST_KIND = "themescheduler.payload-manifest"
INSTALLATION_RECORD_KIND = "themescheduler.installation-record"
LIFECYCLE_TRANSACTION_KIND = "themescheduler.lifecycle-transaction"
INSTALL_CONTRACT_SCHEMA_VERSION = 1
UNINSTALL_REGISTRY_KEY = (
    r"HKCU\Software\Microsoft\Windows\CurrentVersion"
    r"\Uninstall\ThemeScheduler"
)

INSTALL_OPERATIONS = frozenset({"install", "upgrade", "reinstall", "uninstall"})
LIFECYCLE_STATUSES = frozenset(
    {
        "planned",
        "prepared",
        "mutating",
        "committed",
        "completed",
        "rolled-back",
        "failed",
        "partial",
    }
)
TERMINAL_LIFECYCLE_STATUSES = frozenset({"completed", "rolled-back", "failed"})
_ALLOWED_LIFECYCLE_TRANSITIONS = {
    "planned": frozenset({"prepared", "failed"}),
    "prepared": frozenset({"mutating", "failed"}),
    "mutating": frozenset({"committed", "rolled-back", "partial"}),
    "committed": frozenset({"completed", "rolled-back", "partial"}),
    "partial": frozenset({"completed", "rolled-back"}),
    "completed": frozenset(),
    "rolled-back": frozenset(),
    "failed": frozenset(),
}

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_VERSION_PATTERN = re.compile(
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"
)
_TRANSACTION_PATTERN = re.compile(r"lifecycle-\d{8}T\d{6}-[0-9a-f]{8}")
_ERROR_PATTERN = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")
_WINDOWS_INVALID_CHARACTERS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)
_REPARSE_POINT_ATTRIBUTE = 0x0400


class InstallContractError(ContractError):
    """Raised when install lifecycle data is malformed or unsafe."""


def _exact(payload: JsonObject, expected: set[str], label: str) -> None:
    if set(payload) != expected:
        raise InstallContractError(f"{label} fields do not match schema.")


def _require_schema_version(payload: JsonObject, label: str) -> None:
    version = payload.get("schemaVersion")
    if isinstance(version, bool) or version != INSTALL_CONTRACT_SCHEMA_VERSION:
        raise InstallContractError(f"Unsupported {label} schemaVersion.")


def _timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise InstallContractError(f"{field} must be ISO 8601.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise InstallContractError(f"{field} must be ISO 8601.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InstallContractError(f"{field} must include a UTC offset.")
    return value


def _parsed_timestamp(value: Any, field: str) -> datetime:
    validated = _timestamp(value, field)
    return datetime.fromisoformat(validated)


def _version(value: Any, field: str = "version") -> str:
    if not isinstance(value, str) or not _VERSION_PATTERN.fullmatch(value):
        raise InstallContractError(f"{field} must use semantic X.Y.Z version syntax.")
    return value


def _version_key(value: str) -> tuple[Any, ...]:
    core, separator, prerelease = value.partition("-")
    core_key = tuple(int(part) for part in core.split("."))
    if not separator:
        return (*core_key, 1, ())
    identifiers: list[tuple[int, int | str]] = []
    for identifier in prerelease.split("."):
        if identifier.isdigit():
            identifiers.append((0, int(identifier)))
        else:
            identifiers.append((1, identifier.casefold()))
    return (*core_key, 0, tuple(identifiers))


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise InstallContractError(f"{field} must be lowercase SHA-256.")
    return value


def json_document_sha256(payload: JsonObject) -> str:
    content = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def file_sha256(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _absolute_path(value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise InstallContractError(f"{field} must be an absolute path.")
    candidate = Path(value)
    if not candidate.is_absolute():
        raise InstallContractError(f"{field} must be an absolute path.")
    return candidate.resolve(strict=False)


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
        str(right.resolve(strict=False))
    )


def _is_relative_to(candidate: Path, root: Path) -> bool:
    return candidate.resolve(strict=False).is_relative_to(root.resolve(strict=False))


def _validate_relative_payload_path(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise InstallContractError("payload file path must be non-empty.")
    if "\\" in value or "\x00" in value:
        raise InstallContractError(
            "payload file path must use canonical forward slashes."
        )
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value:
        raise InstallContractError("payload file path must be canonical and relative.")
    if len(path.parts) < 2 or path.parts[0] not in {"app", "maintenance"}:
        raise InstallContractError(
            "payload file path must be under app/ or maintenance/."
        )
    for part in path.parts:
        if part in {"", ".", ".."}:
            raise InstallContractError("payload file path contains an unsafe segment.")
        if part.endswith((" ", ".")):
            raise InstallContractError(
                "payload file path cannot end a segment with a dot or space."
            )
        if any(ord(character) < 32 for character in part) or any(
            character in _WINDOWS_INVALID_CHARACTERS for character in part
        ):
            raise InstallContractError(
                "payload file path contains a Windows-invalid character."
            )
        stem = part.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED_NAMES:
            raise InstallContractError(
                "payload file path contains a reserved Windows name."
            )
    return value


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & _REPARSE_POINT_ATTRIBUTE)


def _validate_transaction_id(value: Any) -> str:
    if not isinstance(value, str) or not _TRANSACTION_PATTERN.fullmatch(value):
        raise InstallContractError("transactionId is invalid.")
    return value
