"""HKCU-only Windows adapter for the notification action protocol."""

from __future__ import annotations

import ntpath
import re
from pathlib import Path
from typing import Any

from .errors import ThemeSchedulerRuntimeError
from .notification_contracts import NOTIFICATION_PROTOCOL_SCHEME
from .protocol_registration import (
    PROTOCOL_ARGUMENT,
    PROTOCOL_DISPLAY_NAME,
    ProtocolRegistration,
    RegistryTreeBackup,
)

PROTOCOL_KEY = rf"Software\Classes\{NOTIFICATION_PROTOCOL_SCHEME}"
_COMMAND_PATTERN = re.compile(rf'^"([^"\r\n]+)" {re.escape(PROTOCOL_ARGUMENT)} "%1"$')
_ICON_PATTERN = re.compile(r'^"([^"\r\n]+)",0$')


class WindowsProtocolRegistrationError(ThemeSchedulerRuntimeError):
    """Raised when the fixed current-user protocol tree is unsafe."""


class WindowsNotificationProtocolBackend:
    def __init__(self, registry_module: Any | None = None) -> None:
        if registry_module is None:
            try:
                import winreg as registry_module
            except ImportError as exc:
                raise OSError("Windows registry access requires Windows.") from exc
        self._registry = registry_module

    def _open(self, path: str, access: int):
        return self._registry.OpenKey(
            self._registry.HKEY_CURRENT_USER,
            path,
            0,
            access,
        )

    def _walk(self, relative: str = ""):
        registry = self._registry
        path = PROTOCOL_KEY if not relative else PROTOCOL_KEY + "\\" + relative
        try:
            with self._open(path, registry.KEY_READ) as key:
                values = []
                index = 0
                while True:
                    try:
                        name, data, value_type = registry.EnumValue(key, index)
                    except OSError:
                        break
                    values.append((relative, name, data, value_type))
                    index += 1
                subkeys = []
                index = 0
                while True:
                    try:
                        subkeys.append(registry.EnumKey(key, index))
                    except OSError:
                        break
                    index += 1
        except FileNotFoundError:
            if not relative:
                return None
            raise
        result_keys = [relative]
        result_values = values
        for name in subkeys:
            child = name if not relative else relative + "\\" + name
            walked = self._walk(child)
            if walked is None:
                raise WindowsProtocolRegistrationError(
                    "Protocol registry tree changed while captured."
                )
            child_keys, child_values = walked
            result_keys.extend(child_keys)
            result_values.extend(child_values)
        return result_keys, result_values

    def capture(self) -> RegistryTreeBackup | None:
        walked = self._walk()
        if walked is None:
            return None
        keys, values = walked
        return RegistryTreeBackup(
            tuple(
                sorted(
                    keys,
                    key=lambda value: (
                        value.count("\\"),
                        value.casefold(),
                    ),
                )
            ),
            tuple(
                sorted(
                    values,
                    key=lambda item: (
                        item[0].casefold(),
                        item[1].casefold(),
                    ),
                )
            ),
        )

    @staticmethod
    def _value_map(
        backup: RegistryTreeBackup,
    ) -> dict[tuple[str, str], tuple[Any, int]]:
        return {
            (relative.casefold(), name.casefold()): (data, value_type)
            for relative, name, data, value_type in backup.values
        }

    def read(self) -> ProtocolRegistration | None:
        backup = self.capture()
        if backup is None:
            return None
        expected_keys = {
            "",
            "DefaultIcon",
            "shell",
            r"shell\open",
            r"shell\open\command",
        }
        if {key.casefold() for key in backup.keys} != {
            key.casefold() for key in expected_keys
        }:
            return None
        values = self._value_map(backup)
        registry = self._registry

        def text(relative: str, name: str) -> str | None:
            value = values.get((relative.casefold(), name.casefold()))
            if (
                value is None
                or value[1] != registry.REG_SZ
                or not isinstance(value[0], str)
            ):
                return None
            return value[0]

        if len(values) != 4:
            return None
        display = text("", "")
        marker = text("", "URL Protocol")
        icon = text("DefaultIcon", "")
        command = text(r"shell\open\command", "")
        if (
            display != PROTOCOL_DISPLAY_NAME
            or marker != ""
            or icon is None
            or command is None
        ):
            return None
        icon_match = _ICON_PATTERN.fullmatch(icon)
        command_match = _COMMAND_PATTERN.fullmatch(command)
        if icon_match is None or command_match is None:
            return None
        if ntpath.normcase(icon_match.group(1)) != ntpath.normcase(
            command_match.group(1)
        ):
            return None
        try:
            return ProtocolRegistration(Path(command_match.group(1)))
        except ValueError:
            return None

    def _delete_tree(self, path: str = PROTOCOL_KEY) -> None:
        registry = self._registry
        try:
            with self._open(path, registry.KEY_READ) as key:
                children = []
                index = 0
                while True:
                    try:
                        children.append(registry.EnumKey(key, index))
                    except OSError:
                        break
                    index += 1
        except FileNotFoundError:
            return
        for child in children:
            self._delete_tree(path + "\\" + child)
        registry.DeleteKey(registry.HKEY_CURRENT_USER, path)

    def _write_tree(self, backup: RegistryTreeBackup) -> None:
        registry = self._registry
        for relative in backup.keys:
            path = PROTOCOL_KEY if not relative else PROTOCOL_KEY + "\\" + relative
            with registry.CreateKeyEx(
                registry.HKEY_CURRENT_USER,
                path,
                0,
                registry.KEY_WRITE,
            ):
                pass
        for relative, name, data, value_type in backup.values:
            path = PROTOCOL_KEY if not relative else PROTOCOL_KEY + "\\" + relative
            with self._open(path, registry.KEY_SET_VALUE) as key:
                registry.SetValueEx(key, name, 0, value_type, data)

    def write(self, registration: ProtocolRegistration) -> None:
        registry = self._registry
        backup = RegistryTreeBackup(
            (
                "",
                "DefaultIcon",
                "shell",
                r"shell\open",
                r"shell\open\command",
            ),
            tuple(
                sorted(
                    (
                        ("", "", registration.display_name, registry.REG_SZ),
                        ("", "URL Protocol", "", registry.REG_SZ),
                        (
                            "DefaultIcon",
                            "",
                            registration.icon,
                            registry.REG_SZ,
                        ),
                        (
                            r"shell\open\command",
                            "",
                            registration.command,
                            registry.REG_SZ,
                        ),
                    ),
                    key=lambda item: (
                        item[0].casefold(),
                        item[1].casefold(),
                    ),
                )
            ),
        )
        self._delete_tree()
        self._write_tree(backup)

    def restore(self, backup: RegistryTreeBackup | None) -> None:
        self._delete_tree()
        if backup is not None:
            self._write_tree(backup)
