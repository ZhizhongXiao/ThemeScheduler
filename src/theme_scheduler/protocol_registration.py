"""Strict current-user URI protocol registration contracts."""

from __future__ import annotations

import ntpath
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .notification_contracts import NOTIFICATION_PROTOCOL_SCHEME

PROTOCOL_DISPLAY_NAME = "URL:ThemeScheduler Notification Action"
PROTOCOL_ARGUMENT = "notification-action"


def _absolute_windows_executable(value: Path) -> Path:
    text = str(value)
    drive, tail = ntpath.splitdrive(text)
    if (
        not drive
        or not tail.startswith(("\\", "/"))
        or any(character in text for character in ('"', "\r", "\n"))
        or ntpath.basename(text).casefold() != "themescheduler.exe"
    ):
        raise ValueError(
            "Protocol executable must be an absolute ThemeScheduler.exe path."
        )
    return Path(ntpath.normpath(text))


@dataclass(frozen=True)
class ProtocolRegistration:
    executable: Path
    scheme: str = NOTIFICATION_PROTOCOL_SCHEME
    display_name: str = PROTOCOL_DISPLAY_NAME

    def __post_init__(self) -> None:
        if self.scheme != NOTIFICATION_PROTOCOL_SCHEME:
            raise ValueError("Protocol scheme does not match the product.")
        if self.display_name != PROTOCOL_DISPLAY_NAME:
            raise ValueError("Protocol display name does not match the product.")
        object.__setattr__(
            self,
            "executable",
            _absolute_windows_executable(Path(self.executable)),
        )

    @property
    def command(self) -> str:
        return f'"{self.executable}" {PROTOCOL_ARGUMENT} "%1"'

    @property
    def icon(self) -> str:
        return f'"{self.executable}",0'

    def as_dict(self) -> dict[str, str]:
        return {
            "scheme": self.scheme,
            "displayName": self.display_name,
            "executable": str(self.executable),
            "command": self.command,
            "icon": self.icon,
        }


@dataclass(frozen=True)
class RegistryTreeBackup:
    keys: tuple[str, ...]
    values: tuple[tuple[str, str, Any, int], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.keys, tuple) or "" not in self.keys:
            raise ValueError("Registry tree backup must include its root key.")
        normalized_keys = tuple(
            sorted(
                self.keys,
                key=lambda value: (value.count("\\"), value.casefold()),
            )
        )
        if normalized_keys != self.keys or len(set(self.keys)) != len(self.keys):
            raise ValueError("Registry tree backup keys must be unique and sorted.")
        normalized_values = tuple(
            sorted(
                self.values,
                key=lambda item: (
                    item[0].casefold(),
                    item[1].casefold(),
                ),
            )
        )
        if normalized_values != self.values:
            raise ValueError("Registry tree backup values must be sorted.")
        identities: list[tuple[str, str]] = []
        known_keys = {key.casefold() for key in self.keys}
        for item in self.values:
            if (
                not isinstance(item, tuple)
                or len(item) != 4
                or not isinstance(item[0], str)
                or item[0].casefold() not in known_keys
                or not isinstance(item[1], str)
                or isinstance(item[3], bool)
                or not isinstance(item[3], int)
            ):
                raise ValueError("Registry tree backup value is malformed.")
            identities.append((item[0].casefold(), item[1].casefold()))
        if len(identities) != len(set(identities)):
            raise ValueError("Registry tree backup contains duplicate value names.")


class NotificationProtocolBackend(Protocol):
    def capture(self) -> RegistryTreeBackup | None: ...
    def read(self) -> ProtocolRegistration | None: ...
    def write(self, registration: ProtocolRegistration) -> None: ...
    def restore(self, backup: RegistryTreeBackup | None) -> None: ...
