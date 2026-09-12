"""Read-only runtime and Windows release diagnostics."""

from __future__ import annotations

import os
import platform
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .errors import ThemeSchedulerRuntimeError
from .persistence import captured_at

ENVIRONMENT_SCHEMA_VERSION = 1
ENVIRONMENT_KIND = "themescheduler.environment"


class UnsupportedPlatformError(ThemeSchedulerRuntimeError):
    """Raised when a Windows-only diagnostic is used elsewhere."""


@dataclass(frozen=True)
class RegistryKeySpec:
    root: str
    path: str
    purpose: str


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
        "schemaVersion": ENVIRONMENT_SCHEMA_VERSION,
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
