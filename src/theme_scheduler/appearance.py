"""Formal Windows appearance types and read-only installation capture adapters."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
APPS_THEME_VALUE = "AppsUseLightTheme"
DWM_KEY = r"Software\Microsoft\Windows\DWM"
COLORIZATION_COLOR_VALUE = "ColorizationColor"
REG_DWORD = 4


class ThemeMode(str, Enum):
    DARK = "dark"
    LIGHT = "light"


@dataclass(frozen=True)
class RegistryValue:
    exists: bool
    data: Any = None
    type_code: int | None = None


class AppsThemeReader(Protocol):
    def read_value(self, name: str) -> RegistryValue: ...


class WindowsAppearanceReader:
    """Read only the registry facts required by the immutable install backup."""

    @staticmethod
    def _winreg() -> Any:
        if os.name != "nt":
            raise OSError("Windows appearance access requires Windows.")
        import winreg

        return winreg

    def read_value(self, name: str) -> RegistryValue:
        if name != APPS_THEME_VALUE:
            raise ValueError("Only AppsUseLightTheme can be read by this adapter.")
        winreg = self._winreg()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                PERSONALIZE_KEY,
                0,
                winreg.KEY_READ,
            ) as key:
                data, type_code = winreg.QueryValueEx(key, name)
                return RegistryValue(True, data, int(type_code))
        except FileNotFoundError:
            return RegistryValue(False)

    def read_colorization_color(self) -> int:
        winreg = self._winreg()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                DWM_KEY,
                0,
                winreg.KEY_READ,
            ) as key:
                data, type_code = winreg.QueryValueEx(
                    key,
                    COLORIZATION_COLOR_VALUE,
                )
        except FileNotFoundError as exc:
            raise OSError("DWM ColorizationColor is missing.") from exc
        if (
            int(type_code) != REG_DWORD
            or isinstance(data, bool)
            or not isinstance(data, int)
            or not 0 <= data <= 0xFFFFFFFF
        ):
            raise OSError("DWM ColorizationColor is not a valid REG_DWORD.")
        return data
