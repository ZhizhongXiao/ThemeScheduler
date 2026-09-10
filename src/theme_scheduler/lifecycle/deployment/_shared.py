"""Shared deployment validation, evidence, and public errors."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ...errors import ThemeSchedulerRuntimeError
from ...persistence import load_json_object
from .._validation import InstallContractError, file_sha256
from ..installation import InstallationRecord, InstallationRecordStore
from ..layout import InstallLayout
from ..payload import PayloadFile, PayloadManifest

DEPLOYMENT_JOURNAL_KIND = "themescheduler.deployment-journal"
DEPLOYMENT_JOURNAL_SCHEMA_VERSION = 1
_COMPONENTS = ("app", "maintenance", "metadata")
_JOURNAL_STATUSES = frozenset(
    {
        "prepared",
        "mutating",
        "committed",
        "completed",
        "rolled-back",
        "partial",
        "failed",
    }
)
_REPARSE_POINT_ATTRIBUTE = 0x0400
_TRANSACTION_ID_PATTERN = re.compile(r"lifecycle-\d{8}T\d{6}-[0-9a-f]{8}")
_JOURNAL_TRANSITIONS = {
    "prepared": frozenset({"prepared", "mutating", "failed"}),
    "mutating": frozenset({"mutating", "committed", "rolled-back", "partial"}),
    "committed": frozenset({"completed", "rolled-back", "partial"}),
    "partial": frozenset({"partial", "rolled-back"}),
    "completed": frozenset(),
    "rolled-back": frozenset(),
    "failed": frozenset(),
}


class DeploymentError(ThemeSchedulerRuntimeError):
    """Raised when a file deployment cannot be completed safely."""


class SimulatedDeploymentInterruption(BaseException):
    """Test-only interruption that intentionally bypasses normal rollback."""


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & _REPARSE_POINT_ATTRIBUTE)


def _aware_timestamp(clock: Callable[[], datetime]) -> str:
    instant = clock()
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise DeploymentError("Deployment clock must include a UTC offset.")
    return instant.isoformat(timespec="seconds")


def new_lifecycle_transaction_id(
    clock: Callable[[], datetime] | None = None,
) -> str:
    instant = (clock or (lambda: datetime.now().astimezone()))()
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise DeploymentError("Deployment clock must include a UTC offset.")
    return f"lifecycle-{instant.strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"


def _validate_direct_name(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or Path(value).name != value
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise InstallContractError("Deployment entry name is unsafe.")
    return value


def _sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise InstallContractError(f"{label} must be lowercase SHA-256.")
    return value


def _tree_evidence(
    root: Path,
    *,
    excluded_names: frozenset[str] = frozenset(),
) -> tuple[str, int]:
    """Hash a complete regular tree, including empty directories."""

    root = Path(root)
    if not root.exists():
        entries: list[dict[str, Any]] = []
    else:
        if not root.is_dir() or _is_reparse_point(root):
            raise DeploymentError(
                f"Deployment tree must be a regular directory: {root}"
            )
        entries = []
        for candidate in sorted(
            root.rglob("*"),
            key=lambda item: item.relative_to(root).as_posix().casefold(),
        ):
            relative_path = candidate.relative_to(root)
            if relative_path.parts[0] in excluded_names:
                continue
            if _is_reparse_point(candidate):
                raise DeploymentError(
                    f"Deployment tree contains a reparse point: {candidate}"
                )
            relative = relative_path.as_posix()
            if candidate.is_dir():
                entries.append({"kind": "directory", "path": relative})
            elif candidate.is_file():
                entries.append(
                    {
                        "kind": "file",
                        "path": relative,
                        "size": candidate.stat().st_size,
                        "sha256": file_sha256(candidate),
                    }
                )
            else:
                raise DeploymentError(
                    f"Deployment tree contains a non-regular entry: {candidate}"
                )
    content = json.dumps(
        entries,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest(), len(entries)


def _capture_component_manifest(
    root: Path,
    *,
    version: str,
) -> PayloadManifest:
    root = Path(root).resolve(strict=True)
    files: list[PayloadFile] = []
    for component in ("app", "maintenance"):
        component_root = root / component
        if not component_root.is_dir() or _is_reparse_point(component_root):
            raise DeploymentError(
                f"Active payload component is missing or unsafe: {component}"
            )
        for candidate in component_root.rglob("*"):
            if _is_reparse_point(candidate):
                raise DeploymentError(
                    f"Active payload contains a reparse point: {candidate}"
                )
            if candidate.is_dir():
                continue
            if not candidate.is_file():
                raise DeploymentError(
                    f"Active payload contains a non-regular file: {candidate}"
                )
            files.append(
                PayloadFile(
                    candidate.relative_to(root).as_posix(),
                    candidate.stat().st_size,
                    file_sha256(candidate),
                )
            )
    return PayloadManifest(
        version,
        tuple(sorted(files, key=lambda item: item.path.casefold())),
    )


def verify_active_payload(
    layout: InstallLayout,
    manifest: PayloadManifest,
) -> InstallationRecord:
    actual = _capture_component_manifest(
        layout.program_root,
        version=manifest.version,
    )
    if actual != manifest:
        raise DeploymentError(
            "Active app/maintenance tree does not match the payload manifest."
        )
    manifest_payload = load_json_object(layout.payload_manifest)
    written_manifest = PayloadManifest.from_dict(manifest_payload)
    if written_manifest != manifest:
        raise DeploymentError("Installed payload manifest readback mismatch.")
    record = InstallationRecordStore(layout.installation_record).load()
    if (
        record.version != manifest.version
        or record.payload_manifest_sha256 != manifest.document_sha256
    ):
        raise DeploymentError(
            "Installed record does not bind the active payload manifest."
        )
    return record
