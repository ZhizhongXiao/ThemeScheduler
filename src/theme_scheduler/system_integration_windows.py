"""Windows adapters for Stage 8.3 current-user integration."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from .errors import ThemeSchedulerRuntimeError
from .lifecycle import (
    InstallContractError,
    InstalledAppRegistration,
)
from .persistence import atomic_write_bytes
from .resources import resource_path
from .system_integration import (
    SHORTCUT_FILE_NAME,
    START_MENU_FOLDER_NAME,
    RegistryKeyBackup,
    ShortcutSpec,
)
from .windows_subprocess import no_window_options

INSTALLED_APP_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion"
    r"\Uninstall\ThemeScheduler"
)
USER_SHELL_FOLDERS_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion"
    r"\Explorer\User Shell Folders"
)
MAX_SHORTCUT_BYTES = 4 * 1024 * 1024
_REPARSE_POINT_ATTRIBUTE = 0x400


class WindowsSystemIntegrationError(ThemeSchedulerRuntimeError):
    """Raised when a Windows system-integration adapter is unsafe."""


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & _REPARSE_POINT_ATTRIBUTE)


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
        str(right.resolve(strict=False))
    )


class WindowsKnownFolderReader:
    """Resolve the current user's Start Menu Programs and Desktop folders."""

    def __init__(self, registry_module: Any | None = None) -> None:
        if registry_module is None:
            try:
                import winreg as registry_module
            except ImportError as exc:
                raise OSError("Windows registry access requires Windows.") from exc
        self._registry = registry_module

    def _read(self, value_name: str) -> Path:
        registry = self._registry
        try:
            with registry.OpenKey(
                registry.HKEY_CURRENT_USER,
                USER_SHELL_FOLDERS_KEY,
                0,
                registry.KEY_READ,
            ) as key:
                value, value_type = registry.QueryValueEx(key, value_name)
        except OSError as exc:
            raise WindowsSystemIntegrationError(
                f"Cannot resolve current-user known folder {value_name!r}."
            ) from exc
        if value_type not in (registry.REG_SZ, registry.REG_EXPAND_SZ):
            raise WindowsSystemIntegrationError(
                f"Known folder {value_name!r} has an unsupported registry type."
            )
        if not isinstance(value, str) or not value:
            raise WindowsSystemIntegrationError(
                f"Known folder {value_name!r} is empty."
            )
        expanded = Path(os.path.expandvars(value))
        if not expanded.is_absolute():
            raise WindowsSystemIntegrationError(
                f"Known folder {value_name!r} is not absolute."
            )
        return expanded.resolve(strict=False)

    def programs(self) -> Path:
        return self._read("Programs")

    def desktop(self) -> Path:
        return self._read("Desktop")


class WindowsInstalledAppRegistryBackend:
    """Strict HKCU Installed Apps registration adapter."""

    def __init__(self, registry_module: Any | None = None) -> None:
        if registry_module is None:
            try:
                import winreg as registry_module
            except ImportError as exc:
                raise OSError("Windows registry access requires Windows.") from exc
        self._registry = registry_module

    def _open(self, access: int) -> Any | None:
        registry = self._registry
        try:
            return registry.OpenKey(
                registry.HKEY_CURRENT_USER,
                INSTALLED_APP_KEY,
                0,
                access,
            )
        except FileNotFoundError:
            return None

    def _open_read(self) -> Any | None:
        return self._open(self._registry.KEY_READ)

    def _ensure_no_subkeys(self, key: Any) -> None:
        subkey_count, _, _ = self._registry.QueryInfoKey(key)
        if subkey_count == 0:
            return
        child = self._registry.EnumKey(key, 0)
        raise WindowsSystemIntegrationError(
            f"Managed Installed Apps key unexpectedly contains subkey {child!r}."
        )

    def capture(self) -> RegistryKeyBackup | None:
        key = self._open_read()
        if key is None:
            return None
        with key:
            self._ensure_no_subkeys(key)
            values: list[tuple[str, Any, int]] = []
            _, value_count, _ = self._registry.QueryInfoKey(key)
            for index in range(value_count):
                name, data, value_type = self._registry.EnumValue(key, index)
                values.append((name, data, value_type))
        values.sort(key=lambda item: item[0].casefold())
        return RegistryKeyBackup(tuple(values))

    def read(self) -> InstalledAppRegistration | None:
        backup = self.capture()
        if backup is None:
            return None
        values = {name: data for name, data, _ in backup.values}
        try:
            return InstalledAppRegistration.from_registry_values(values)
        except (InstallContractError, TypeError, ValueError):
            return None

    def _clear_values(self, key: Any) -> None:
        self._ensure_no_subkeys(key)
        _, value_count, _ = self._registry.QueryInfoKey(key)
        names = [
            self._registry.EnumValue(key, index)[0] for index in range(value_count)
        ]
        for name in names:
            self._registry.DeleteValue(key, name)

    def _replace_values(self, values: tuple[tuple[str, Any, int], ...]) -> None:
        registry = self._registry
        with registry.CreateKeyEx(
            registry.HKEY_CURRENT_USER,
            INSTALLED_APP_KEY,
            0,
            registry.KEY_READ | registry.KEY_WRITE,
        ) as key:
            self._clear_values(key)
            for name, data, value_type in values:
                registry.SetValueEx(key, name, 0, value_type, data)

    def write(self, registration: InstalledAppRegistration) -> None:
        registry = self._registry
        values = tuple(
            sorted(
                (
                    (
                        name,
                        data,
                        registry.REG_DWORD
                        if isinstance(data, int)
                        else registry.REG_SZ,
                    )
                    for name, data in registration.as_registry_values().items()
                ),
                key=lambda item: item[0].casefold(),
            )
        )
        self._replace_values(values)

    def restore(self, backup: RegistryKeyBackup | None) -> None:
        registry = self._registry
        if backup is not None:
            self._replace_values(backup.values)
            return
        key = self._open(registry.KEY_READ | registry.KEY_WRITE)
        if key is None:
            return
        with key:
            self._clear_values(key)
        registry.DeleteKey(
            registry.HKEY_CURRENT_USER,
            INSTALLED_APP_KEY,
        )


class WindowsShortcutBackend:
    """Allowlisted WSH shortcut adapter with byte-exact rollback."""

    TIMEOUT_SECONDS = 30

    def __init__(
        self,
        allowed_paths: tuple[Path, ...],
        bridge_path: Path | None = None,
    ) -> None:
        if not allowed_paths:
            raise ValueError("At least one shortcut path must be allowed.")
        normalized = tuple(Path(path).resolve(strict=False) for path in allowed_paths)
        if any(
            not path.is_absolute()
            or path.name != SHORTCUT_FILE_NAME
            or path.suffix.casefold() != ".lnk"
            for path in normalized
        ):
            raise ValueError("Allowed shortcut paths must be managed .lnk files.")
        if len({os.path.normcase(str(path)) for path in normalized}) != len(normalized):
            raise ValueError("Allowed shortcut paths must be unique.")
        self._allowed_paths = normalized
        self._bridge_path = Path(bridge_path) if bridge_path else None

    def _allowed(self, path: Path) -> Path:
        normalized = Path(path).resolve(strict=False)
        if not any(
            _same_path(normalized, candidate) for candidate in self._allowed_paths
        ):
            raise WindowsSystemIntegrationError(
                f"Shortcut path is outside the allowlist: {normalized}"
            )
        return normalized

    def _resolve_bridge(self) -> Path:
        bridge = (
            self._bridge_path.resolve()
            if self._bridge_path is not None
            else resource_path("entrypoints", "shortcut_bridge.ps1")
        )
        if not bridge.is_file():
            raise WindowsSystemIntegrationError(f"Shortcut bridge is missing: {bridge}")
        return bridge

    @staticmethod
    def _resolve_powershell() -> Path:
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        powershell = (
            Path(system_root)
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        if not powershell.is_file():
            raise WindowsSystemIntegrationError(
                f"Windows PowerShell is missing: {powershell}"
            )
        return powershell

    def _run(
        self,
        action: str,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        if os.name != "nt":
            raise OSError("Windows shortcut access requires Windows.")
        command = [
            str(self._resolve_powershell()),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self._resolve_bridge()),
            "-Action",
            action,
        ]
        request_text = json.dumps(
            request,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            with tempfile.TemporaryDirectory(
                prefix="themescheduler-shortcut-bridge-"
            ) as temporary_directory:
                request_path = Path(temporary_directory) / "request.json"
                request_path.write_text(
                    request_text,
                    encoding="utf-8-sig",
                    newline="\n",
                )
                command.extend(("-RequestPath", str(request_path)))
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8-sig",
                    errors="replace",
                    timeout=self.TIMEOUT_SECONDS,
                    **no_window_options(),
                )
        except subprocess.TimeoutExpired as exc:
            raise WindowsSystemIntegrationError(
                f"Shortcut bridge timed out after {self.TIMEOUT_SECONDS} seconds."
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise WindowsSystemIntegrationError(
                f"Shortcut bridge exited with {completed.returncode}: "
                f"{detail or 'no output'}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise WindowsSystemIntegrationError(
                "Shortcut bridge returned invalid JSON."
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("ok") is not True
            or payload.get("action") != action
            or payload.get("shortcutChanged") is not (action == "Write")
        ):
            raise WindowsSystemIntegrationError(
                f"Shortcut bridge returned an unexpected result: {payload!r}"
            )
        return payload

    def capture(self, path: Path) -> bytes | None:
        path = self._allowed(path)
        if not path.exists():
            return None
        if _is_reparse_point(path):
            raise WindowsSystemIntegrationError(
                f"Refusing shortcut reparse point: {path}"
            )
        details = path.stat()
        if not stat.S_ISREG(details.st_mode):
            raise WindowsSystemIntegrationError(
                f"Shortcut path is not a regular file: {path}"
            )
        if details.st_size > MAX_SHORTCUT_BYTES:
            raise WindowsSystemIntegrationError(
                f"Shortcut is unexpectedly large: {path}"
            )
        content = path.read_bytes()
        details_after = path.stat()
        if (
            len(content) != details.st_size
            or details_after.st_size != details.st_size
            or details_after.st_mtime_ns != details.st_mtime_ns
        ):
            raise WindowsSystemIntegrationError(
                f"Shortcut changed while being captured: {path}"
            )
        return content

    def read(self, path: Path) -> ShortcutSpec | None:
        path = self._allowed(path)
        payload = self._run("Read", {"path": str(path)})
        exists = payload.get("exists")
        if not isinstance(exists, bool):
            raise WindowsSystemIntegrationError(
                "Shortcut read returned no existence state."
            )
        if not exists:
            if payload.get("shortcut") is not None:
                raise WindowsSystemIntegrationError(
                    "Absent shortcut returned an unexpected definition."
                )
            return None
        readable = payload.get("readable")
        if not isinstance(readable, bool):
            raise WindowsSystemIntegrationError(
                "Shortcut read returned no readability state."
            )
        if not readable:
            if payload.get("shortcut") is not None:
                raise WindowsSystemIntegrationError(
                    "Unreadable shortcut returned a definition."
                )
            return None
        shortcut = payload.get("shortcut")
        if not isinstance(shortcut, Mapping):
            raise WindowsSystemIntegrationError("Shortcut read returned no definition.")
        expected_fields = {
            "path",
            "target",
            "arguments",
            "workingDirectory",
            "description",
            "iconLocation",
            "appUserModelId",
            "toastActivatorClsid",
        }
        if set(shortcut) != expected_fields:
            raise WindowsSystemIntegrationError(
                "Shortcut read returned unexpected fields."
            )
        if not _same_path(Path(str(shortcut["path"])), path):
            raise WindowsSystemIntegrationError("Shortcut readback path mismatch.")
        icon_location = str(shortcut["iconLocation"])
        if "," in icon_location:
            icon_path, icon_index = icon_location.rsplit(",", 1)
            icon_location = f"{icon_path},{icon_index.strip()}"
        try:
            return ShortcutSpec(
                path=path,
                target=Path(str(shortcut["target"])),
                arguments=str(shortcut["arguments"]),
                working_directory=Path(str(shortcut["workingDirectory"])),
                description=str(shortcut["description"]),
                icon_location=icon_location,
                app_user_model_id=str(shortcut["appUserModelId"]),
                toast_activator_clsid=str(shortcut["toastActivatorClsid"]),
            )
        except (TypeError, ValueError):
            return None

    def write(self, shortcut: ShortcutSpec) -> None:
        path = self._allowed(shortcut.path)
        if not _same_path(path, shortcut.path):
            raise WindowsSystemIntegrationError("Shortcut write path mismatch.")
        if path.exists() and _is_reparse_point(path):
            raise WindowsSystemIntegrationError(
                f"Refusing shortcut reparse point: {path}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._run("Write", shortcut.as_dict())
        if payload.get("exists") is not True or not _same_path(
            Path(str(payload.get("path"))), path
        ):
            raise WindowsSystemIntegrationError(
                "Shortcut write returned an invalid path."
            )

    def restore(self, path: Path, backup: bytes | None) -> None:
        path = self._allowed(path)
        if path.exists() and _is_reparse_point(path):
            raise WindowsSystemIntegrationError(
                f"Refusing shortcut reparse point: {path}"
            )
        if backup is None:
            path.unlink(missing_ok=True)
            if path.parent.name == START_MENU_FOLDER_NAME and path.parent.exists():
                with suppress(OSError):
                    path.parent.rmdir()
            return
        if not isinstance(backup, bytes) or len(backup) > MAX_SHORTCUT_BYTES:
            raise WindowsSystemIntegrationError("Shortcut backup is invalid.")
        atomic_write_bytes(path, backup, force=True)
