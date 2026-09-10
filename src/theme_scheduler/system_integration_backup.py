"""Durable acceptance backup for current-user system integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import ThemeSchedulerRuntimeError
from .persistence import atomic_write_json, captured_at, load_json_object
from .scheduler import (
    DEFAULT_TASK_PATH,
    TaskDefinitionBackup,
    TaskSchedulerBackend,
    TaskSpec,
    compare_task_specs,
)
from .system_integration import (
    SHORTCUT_FILE_NAME,
    InstalledAppRegistryBackend,
    RegistryKeyBackup,
    ShortcutBackend,
)

SYSTEM_INTEGRATION_BACKUP_KIND = "themescheduler.system-integration-backup"
SYSTEM_INTEGRATION_BACKUP_SCHEMA_VERSION = 1
MAX_BACKUP_SHORTCUT_BYTES = 4 * 1024 * 1024


class SystemIntegrationBackupError(ThemeSchedulerRuntimeError):
    """Raised when a system-integration backup is malformed or stale."""


def _exact(
    payload: Mapping[str, Any],
    expected: set[str],
    label: str,
) -> None:
    if set(payload) != expected:
        raise SystemIntegrationBackupError(f"{label} fields do not match schema.")


def _timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise SystemIntegrationBackupError("capturedAt is invalid.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SystemIntegrationBackupError("capturedAt is not ISO 8601.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SystemIntegrationBackupError("capturedAt must contain a UTC offset.")
    return value


def _encode_registry_data(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"encoding": "hex", "value": value.hex()}
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            raise SystemIntegrationBackupError(
                "Registry list data must contain only strings."
            )
        return {"encoding": "string-list", "value": value}
    if value is None or isinstance(value, (str, int)):
        if isinstance(value, bool):
            raise SystemIntegrationBackupError("Boolean registry data is unsupported.")
        return {"encoding": "scalar", "value": value}
    raise SystemIntegrationBackupError(
        f"Unsupported registry data type: {type(value).__name__}"
    )


def _decode_registry_data(value: Any) -> Any:
    if not isinstance(value, Mapping):
        raise SystemIntegrationBackupError("Encoded registry data must be an object.")
    _exact(value, {"encoding", "value"}, "registry data")
    encoding = value.get("encoding")
    encoded = value.get("value")
    if encoding == "hex":
        if not isinstance(encoded, str):
            raise SystemIntegrationBackupError("Hex registry data must be a string.")
        try:
            return bytes.fromhex(encoded)
        except ValueError as exc:
            raise SystemIntegrationBackupError("Hex registry data is invalid.") from exc
    if encoding == "string-list":
        if not isinstance(encoded, list) or not all(
            isinstance(item, str) for item in encoded
        ):
            raise SystemIntegrationBackupError("Registry string-list data is invalid.")
        return list(encoded)
    if encoding == "scalar":
        if isinstance(encoded, bool) or not (
            encoded is None or isinstance(encoded, (str, int))
        ):
            raise SystemIntegrationBackupError("Registry scalar data is invalid.")
        return encoded
    raise SystemIntegrationBackupError("Registry data encoding is unsupported.")


@dataclass(frozen=True)
class ShortcutRawBackup:
    path: Path
    content: bytes | None

    def __post_init__(self) -> None:
        path = Path(self.path)
        if (
            not path.is_absolute()
            or path.name != SHORTCUT_FILE_NAME
            or path.suffix.casefold() != ".lnk"
        ):
            raise SystemIntegrationBackupError("Shortcut backup path is not managed.")
        if self.content is not None and (
            not isinstance(self.content, bytes)
            or len(self.content) > MAX_BACKUP_SHORTCUT_BYTES
        ):
            raise SystemIntegrationBackupError("Shortcut backup content is invalid.")
        object.__setattr__(self, "path", path.resolve(strict=False))

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "exists": self.content is not None,
            "contentHex": (self.content.hex() if self.content is not None else None),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ShortcutRawBackup:
        _exact(
            payload,
            {"path", "exists", "contentHex"},
            "shortcut backup",
        )
        exists = payload.get("exists")
        content_hex = payload.get("contentHex")
        if not isinstance(exists, bool):
            raise SystemIntegrationBackupError(
                "Shortcut backup exists field is invalid."
            )
        if exists:
            if not isinstance(content_hex, str):
                raise SystemIntegrationBackupError(
                    "Shortcut backup contentHex is invalid."
                )
            try:
                content = bytes.fromhex(content_hex)
            except ValueError as exc:
                raise SystemIntegrationBackupError(
                    "Shortcut backup contentHex is malformed."
                ) from exc
        else:
            if content_hex is not None:
                raise SystemIntegrationBackupError(
                    "Absent shortcut backup cannot contain bytes."
                )
            content = None
        return cls(Path(str(payload.get("path"))), content)


@dataclass(frozen=True)
class SystemIntegrationBackup:
    captured_at: str
    registration: RegistryKeyBackup | None
    shortcuts: tuple[ShortcutRawBackup, ...]
    task_before: TaskSpec | None
    task_definition: TaskDefinitionBackup | None

    def __post_init__(self) -> None:
        _timestamp(self.captured_at)
        if not isinstance(self.shortcuts, tuple) or not self.shortcuts:
            raise SystemIntegrationBackupError(
                "At least one shortcut backup is required."
            )
        normalized_paths = tuple(str(item.path).casefold() for item in self.shortcuts)
        if len(set(normalized_paths)) != len(normalized_paths):
            raise SystemIntegrationBackupError("Shortcut backup paths must be unique.")
        if tuple(sorted(normalized_paths)) != normalized_paths:
            raise SystemIntegrationBackupError("Shortcut backups must be path-sorted.")
        if (self.task_before is None) != (self.task_definition is None):
            raise SystemIntegrationBackupError(
                "Task model and full definition must have matching presence."
            )
        if (
            self.task_before is not None
            and self.task_definition is not None
            and (
                self.task_before.task_path != DEFAULT_TASK_PATH
                or self.task_definition.task_path != DEFAULT_TASK_PATH
            )
        ):
            raise SystemIntegrationBackupError(
                "Backup contains an unmanaged task path."
            )

    def as_dict(self) -> dict[str, Any]:
        registration = None
        if self.registration is not None:
            registration = [
                {
                    "name": name,
                    "typeCode": value_type,
                    "data": _encode_registry_data(data),
                }
                for name, data, value_type in self.registration.values
            ]
        return {
            "kind": SYSTEM_INTEGRATION_BACKUP_KIND,
            "schemaVersion": (SYSTEM_INTEGRATION_BACKUP_SCHEMA_VERSION),
            "capturedAt": self.captured_at,
            "registration": registration,
            "shortcuts": [shortcut.as_dict() for shortcut in self.shortcuts],
            "taskBefore": (
                self.task_before.as_dict() if self.task_before is not None else None
            ),
            "taskDefinition": (
                self.task_definition.as_dict()
                if self.task_definition is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SystemIntegrationBackup:
        _exact(
            payload,
            {
                "kind",
                "schemaVersion",
                "capturedAt",
                "registration",
                "shortcuts",
                "taskBefore",
                "taskDefinition",
            },
            "system-integration backup",
        )
        if payload.get("kind") != SYSTEM_INTEGRATION_BACKUP_KIND:
            raise SystemIntegrationBackupError(
                "JSON is not a system-integration backup."
            )
        if payload.get("schemaVersion") != SYSTEM_INTEGRATION_BACKUP_SCHEMA_VERSION:
            raise SystemIntegrationBackupError(
                "System-integration backup schema is unsupported."
            )
        raw_registration = payload.get("registration")
        if raw_registration is None:
            registration = None
        else:
            if not isinstance(raw_registration, list):
                raise SystemIntegrationBackupError(
                    "Registration backup must be an array or null."
                )
            values: list[tuple[str, Any, int]] = []
            for item in raw_registration:
                if not isinstance(item, Mapping):
                    raise SystemIntegrationBackupError(
                        "Registration backup item must be an object."
                    )
                _exact(
                    item,
                    {"name", "typeCode", "data"},
                    "registration backup item",
                )
                name = item.get("name")
                value_type = item.get("typeCode")
                if (
                    not isinstance(name, str)
                    or isinstance(value_type, bool)
                    or not isinstance(value_type, int)
                ):
                    raise SystemIntegrationBackupError(
                        "Registration backup item is invalid."
                    )
                values.append(
                    (
                        name,
                        _decode_registry_data(item.get("data")),
                        value_type,
                    )
                )
            registration = RegistryKeyBackup(tuple(values))
        raw_shortcuts = payload.get("shortcuts")
        if not isinstance(raw_shortcuts, list):
            raise SystemIntegrationBackupError("Shortcut backups must be an array.")
        shortcuts = tuple(
            ShortcutRawBackup.from_dict(item)
            for item in raw_shortcuts
            if isinstance(item, Mapping)
        )
        if len(shortcuts) != len(raw_shortcuts):
            raise SystemIntegrationBackupError(
                "Shortcut backup item must be an object."
            )
        raw_task = payload.get("taskBefore")
        raw_definition = payload.get("taskDefinition")
        if raw_task is not None and not isinstance(raw_task, Mapping):
            raise SystemIntegrationBackupError("taskBefore must be an object or null.")
        if raw_definition is not None and not isinstance(raw_definition, Mapping):
            raise SystemIntegrationBackupError(
                "taskDefinition must be an object or null."
            )
        return cls(
            captured_at=_timestamp(payload.get("capturedAt")),
            registration=registration,
            shortcuts=shortcuts,
            task_before=(
                TaskSpec.from_dict(raw_task) if raw_task is not None else None
            ),
            task_definition=(
                TaskDefinitionBackup.from_dict(raw_definition)
                if raw_definition is not None
                else None
            ),
        )

    def save(self, path: Path) -> None:
        atomic_write_json(path, self.as_dict())

    @classmethod
    def load(cls, path: Path) -> SystemIntegrationBackup:
        return cls.from_dict(load_json_object(path))

    def state_equals(self, other: SystemIntegrationBackup) -> bool:
        return (
            self.registration == other.registration
            and self.shortcuts == other.shortcuts
            and self.task_before == other.task_before
            and self.task_definition == other.task_definition
        )


def capture_system_integration(
    registry: InstalledAppRegistryBackend,
    shortcuts: ShortcutBackend,
    tasks: TaskSchedulerBackend,
    shortcut_paths: tuple[Path, ...],
) -> SystemIntegrationBackup:
    normalized_paths = tuple(
        sorted(
            (Path(path).resolve(strict=False) for path in shortcut_paths),
            key=lambda path: str(path).casefold(),
        )
    )
    registration = registry.capture()
    shortcut_values = tuple(
        ShortcutRawBackup(path, shortcuts.capture(path)) for path in normalized_paths
    )
    task_before = tasks.read(DEFAULT_TASK_PATH)
    task_definition = tasks.capture(DEFAULT_TASK_PATH)
    if registry.capture() != registration:
        raise SystemIntegrationBackupError(
            "Registration changed while the backup was captured."
        )
    for shortcut in shortcut_values:
        if shortcuts.capture(shortcut.path) != shortcut.content:
            raise SystemIntegrationBackupError(
                f"Shortcut changed while captured: {shortcut.path}"
            )
    task_after = tasks.read(DEFAULT_TASK_PATH)
    if (
        (task_before is None) != (task_definition is None)
        or (task_before is None) != (task_after is None)
        or (
            task_before is not None
            and task_after is not None
            and compare_task_specs(task_before, task_after)
        )
    ):
        raise SystemIntegrationBackupError(
            "Task changed while the backup was captured."
        )
    return SystemIntegrationBackup(
        captured_at=captured_at(),
        registration=registration,
        shortcuts=shortcut_values,
        task_before=task_before,
        task_definition=task_definition,
    )


@dataclass(frozen=True)
class SystemIntegrationRestoreOutcome:
    result: str
    task_restored: bool
    registration_restored: bool
    shortcuts_restored: bool
    errors: tuple[str, ...]

    @property
    def verified(self) -> bool:
        return (
            self.task_restored
            and self.registration_restored
            and self.shortcuts_restored
            and not self.errors
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "taskRestored": self.task_restored,
            "registrationRestored": self.registration_restored,
            "shortcutsRestored": self.shortcuts_restored,
            "verified": self.verified,
            "errors": list(self.errors),
            "windowsChanged": True,
        }


def restore_system_integration(
    backup: SystemIntegrationBackup,
    registry: InstalledAppRegistryBackend,
    shortcuts: ShortcutBackend,
    tasks: TaskSchedulerBackend,
) -> SystemIntegrationRestoreOutcome:
    errors: list[str] = []
    task_restored = False
    registration_restored = False
    shortcuts_restored = False
    try:
        if backup.task_before is None:
            tasks.delete(DEFAULT_TASK_PATH)
        elif backup.task_definition is None:
            raise SystemIntegrationBackupError(
                "Existing task has no restorable definition."
            )
        else:
            tasks.restore(backup.task_definition)
        actual = tasks.read(DEFAULT_TASK_PATH)
        task_restored = (
            actual is None
            if backup.task_before is None
            else actual is not None
            and not compare_task_specs(backup.task_before, actual)
        )
        if not task_restored:
            errors.append("task readback mismatch")
    except Exception as exc:
        errors.append(f"task: {type(exc).__name__}: {exc}")
    try:
        registry.restore(backup.registration)
        registration_restored = registry.capture() == backup.registration
        if not registration_restored:
            errors.append("registration readback mismatch")
    except Exception as exc:
        errors.append(f"registration: {type(exc).__name__}: {exc}")
    shortcut_results: list[bool] = []
    for item in backup.shortcuts:
        try:
            shortcuts.restore(item.path, item.content)
            matched = shortcuts.capture(item.path) == item.content
            shortcut_results.append(matched)
            if not matched:
                errors.append(f"shortcut readback mismatch: {item.path}")
        except Exception as exc:
            shortcut_results.append(False)
            errors.append(f"shortcut {item.path}: {type(exc).__name__}: {exc}")
    shortcuts_restored = bool(shortcut_results) and all(shortcut_results)
    verified = (
        task_restored and registration_restored and shortcuts_restored and not errors
    )
    return SystemIntegrationRestoreOutcome(
        "restored" if verified else "partial",
        task_restored,
        registration_restored,
        shortcuts_restored,
        tuple(errors),
    )
