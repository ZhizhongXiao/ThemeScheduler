"""Read-only diagnostics used by the Windows behavior prototypes.

This module deliberately has no registry write API.  The candidate keys below
are observation targets for prototype work and are not yet the frozen accent
snapshot schema used by the future product.
"""

from __future__ import annotations

import os
import platform
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .errors import DataError, ThemeSchedulerRuntimeError
from .persistence import captured_at

SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_KIND = "themescheduler.registry-baseline"
ENVIRONMENT_KIND = "themescheduler.environment"


class UnsupportedPlatformError(ThemeSchedulerRuntimeError):
    """Raised when a Windows-only diagnostic is used elsewhere."""


class InvalidSnapshotError(DataError):
    """Raised when a registry snapshot cannot be compared safely."""


@dataclass(frozen=True)
class RegistryKeySpec:
    root: str
    path: str
    purpose: str


CANDIDATE_THEME_KEYS: tuple[RegistryKeySpec, ...] = (
    RegistryKeySpec(
        "HKEY_CURRENT_USER",
        r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        "system and application theme observation",
    ),
    RegistryKeySpec(
        "HKEY_CURRENT_USER",
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent",
        "Explorer accent observation",
    ),
    RegistryKeySpec(
        "HKEY_CURRENT_USER",
        r"Software\Microsoft\Windows\DWM",
        "window frame and accent observation",
    ),
)

WINDOWS_VERSION_KEY = RegistryKeySpec(
    "HKEY_LOCAL_MACHINE",
    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
    "Windows release identification",
)


def _winreg_module() -> Any:
    if os.name != "nt":
        raise UnsupportedPlatformError("Registry diagnostics require Windows.")
    import winreg

    return winreg


def _registry_type_names(winreg: Any) -> dict[int, str]:
    names = (
        "REG_NONE",
        "REG_SZ",
        "REG_EXPAND_SZ",
        "REG_BINARY",
        "REG_DWORD",
        "REG_DWORD_BIG_ENDIAN",
        "REG_LINK",
        "REG_MULTI_SZ",
        "REG_RESOURCE_LIST",
        "REG_FULL_RESOURCE_DESCRIPTOR",
        "REG_RESOURCE_REQUIREMENTS_LIST",
        "REG_QWORD",
    )
    return {int(getattr(winreg, name)): name for name in names if hasattr(winreg, name)}


def encode_registry_data(value: Any) -> Any:
    """Convert registry data to JSON without losing binary bytes."""

    if isinstance(value, bytes):
        return {"encoding": "hex", "value": value.hex()}
    if isinstance(value, tuple):
        return list(value)
    return value


def read_registry_key(spec: RegistryKeySpec) -> dict[str, Any]:
    """Read all values directly under one allow-listed registry key."""

    winreg = _winreg_module()
    roots = {
        "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
        "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
    }
    if spec.root not in roots:
        raise ValueError(f"Unsupported registry root: {spec.root}")

    result: dict[str, Any] = {
        "root": spec.root,
        "path": spec.path,
        "purpose": spec.purpose,
        "exists": False,
        "values": [],
    }
    type_names = _registry_type_names(winreg)
    try:
        with winreg.OpenKey(roots[spec.root], spec.path, 0, winreg.KEY_READ) as key:
            result["exists"] = True
            _, value_count, _ = winreg.QueryInfoKey(key)
            for index in range(value_count):
                name, value, type_code = winreg.EnumValue(key, index)
                result["values"].append(
                    {
                        "name": name,
                        "typeCode": int(type_code),
                        "typeName": type_names.get(int(type_code), "UNKNOWN"),
                        "data": encode_registry_data(value),
                    }
                )
    except FileNotFoundError:
        return result
    except OSError as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"

    result["values"].sort(key=lambda item: item["name"].casefold())
    return result


RegistryReader = Callable[[RegistryKeySpec], dict[str, Any]]


def _windows_release(reader: RegistryReader) -> dict[str, Any]:
    if os.name != "nt":
        return {"available": False, "reason": "not-windows"}

    key = reader(WINDOWS_VERSION_KEY)
    selected_names = {
        "ProductName",
        "DisplayVersion",
        "CurrentBuild",
        "CurrentBuildNumber",
        "UBR",
        "EditionID",
        "InstallationType",
    }
    selected = {
        value["name"]: value["data"]
        for value in key.get("values", [])
        if value["name"] in selected_names
    }
    result: dict[str, Any] = {"available": bool(key.get("exists")), **selected}
    if "error" in key:
        result["error"] = key["error"]
    return result


def collect_environment(reader: RegistryReader = read_registry_key) -> dict[str, Any]:
    """Collect non-identifying runtime and Windows release information."""

    return {
        "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "kind": ENVIRONMENT_KIND,
        "capturedAt": captured_at(),
        "runtime": {
            "pythonVersion": platform.python_version(),
            "pythonImplementation": platform.python_implementation(),
            "executableBits": 64 if sys.maxsize > 2**32 else 32,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "windowsRelease": _windows_release(reader),
    }


def capture_registry_snapshot(
    specs: Iterable[RegistryKeySpec] = CANDIDATE_THEME_KEYS,
    reader: RegistryReader = read_registry_key,
) -> dict[str, Any]:
    """Capture a read-only baseline of candidate personalization keys."""

    return {
        "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "kind": SNAPSHOT_KIND,
        "capturedAt": captured_at(),
        "environment": collect_environment(reader),
        "keys": [reader(spec) for spec in specs],
    }


def validate_snapshot(snapshot: Mapping[str, Any]) -> None:
    if snapshot.get("kind") != SNAPSHOT_KIND:
        raise InvalidSnapshotError("Unexpected snapshot kind.")
    if snapshot.get("schemaVersion") != SNAPSHOT_SCHEMA_VERSION:
        raise InvalidSnapshotError("Unsupported snapshot schema version.")
    if not isinstance(snapshot.get("keys"), list):
        raise InvalidSnapshotError("Snapshot keys must be a list.")


def _key_map(snapshot: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    for key in snapshot["keys"]:
        if not isinstance(key, Mapping):
            raise InvalidSnapshotError("Each registry key must be an object.")
        root = key.get("root")
        path = key.get("path")
        if not isinstance(root, str) or not isinstance(path, str):
            raise InvalidSnapshotError("Registry key identity is missing.")
        result[(root.casefold(), path.casefold())] = key
    return result


def _value_map(key: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    values = key.get("values", [])
    if not isinstance(values, list):
        raise InvalidSnapshotError("Registry values must be a list.")
    result: dict[str, Mapping[str, Any]] = {}
    for value in values:
        if not isinstance(value, Mapping) or not isinstance(value.get("name"), str):
            raise InvalidSnapshotError("Registry value identity is missing.")
        result[value["name"].casefold()] = value
    return result


def diff_registry_snapshots(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, Any]:
    """Return deterministic key and value changes between two snapshots."""

    validate_snapshot(before)
    validate_snapshot(after)
    before_keys = _key_map(before)
    after_keys = _key_map(after)
    changes: list[dict[str, Any]] = []

    for identity in sorted(set(before_keys) | set(after_keys)):
        old_key = before_keys.get(identity)
        new_key = after_keys.get(identity)
        display_key = new_key or old_key
        assert display_key is not None
        base = {"root": display_key["root"], "path": display_key["path"]}

        if old_key is None:
            changes.append({**base, "change": "key-added", "after": new_key})
            continue
        if new_key is None:
            changes.append({**base, "change": "key-removed", "before": old_key})
            continue
        if bool(old_key.get("exists")) != bool(new_key.get("exists")):
            changes.append(
                {
                    **base,
                    "change": "key-existence-changed",
                    "before": bool(old_key.get("exists")),
                    "after": bool(new_key.get("exists")),
                }
            )
        if old_key.get("error") != new_key.get("error"):
            changes.append(
                {
                    **base,
                    "change": "key-read-status-changed",
                    "before": old_key.get("error"),
                    "after": new_key.get("error"),
                }
            )

        old_values = _value_map(old_key)
        new_values = _value_map(new_key)
        for value_name in sorted(set(old_values) | set(new_values)):
            old_value = old_values.get(value_name)
            new_value = new_values.get(value_name)
            display_value = new_value or old_value
            assert display_value is not None
            value_base = {**base, "name": display_value["name"]}
            if old_value is None:
                changes.append(
                    {**value_base, "change": "value-added", "after": new_value}
                )
            elif new_value is None:
                changes.append(
                    {**value_base, "change": "value-removed", "before": old_value}
                )
            elif old_value != new_value:
                changes.append(
                    {
                        **value_base,
                        "change": "value-changed",
                        "before": old_value,
                        "after": new_value,
                    }
                )

    return {
        "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "kind": "themescheduler.registry-diff",
        "comparedAt": captured_at(),
        "beforeCapturedAt": before.get("capturedAt"),
        "afterCapturedAt": after.get("capturedAt"),
        "changeCount": len(changes),
        "changes": changes,
    }
