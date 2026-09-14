"""Formal Windows appearance types and read-only installation capture adapters."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from .accent_profile import RgbColor
from .accent_theme import ThemeVisualState
from .errors import ThemeSchedulerRuntimeError
from .resources import resource_path
from .windows_subprocess import no_window_options

PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
APPS_THEME_VALUE = "AppsUseLightTheme"
SYSTEM_THEME_VALUE = "SystemUsesLightTheme"
COLOR_PREVALENCE_VALUE = "ColorPrevalence"
DESKTOP_KEY = r"Control Panel\Desktop"
AUTO_COLORIZATION_VALUE = "AutoColorization"
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "data": self.data,
            "typeCode": self.type_code,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RegistryValue:
        if set(payload) != {"exists", "data", "typeCode"}:
            raise ValueError("Appearance registry value fields do not match schema.")
        exists = payload.get("exists")
        type_code = payload.get("typeCode")
        if not isinstance(exists, bool):
            raise TypeError("Appearance registry existence flag must be boolean.")
        if type_code is not None and (
            isinstance(type_code, bool) or not isinstance(type_code, int)
        ):
            raise TypeError("Appearance registry type code must be integer or null.")
        if exists and type_code is None:
            raise ValueError("Existing appearance registry value needs a type code.")
        return cls(exists, payload.get("data"), type_code)


@dataclass(frozen=True)
class AppearanceRegistrySnapshot:
    apps_theme: RegistryValue
    system_theme: RegistryValue
    start_taskbar_accent: RegistryValue
    title_borders_accent: RegistryValue

    def __post_init__(self) -> None:
        for name, value in (
            ("AppsUseLightTheme", self.apps_theme),
            ("SystemUsesLightTheme", self.system_theme),
            ("start/taskbar ColorPrevalence", self.start_taskbar_accent),
            ("title/borders ColorPrevalence", self.title_borders_accent),
        ):
            if not isinstance(value, RegistryValue):
                raise TypeError(f"Appearance snapshot {name} is invalid.")
            if value.exists:
                if (
                    value.type_code != REG_DWORD
                    or isinstance(value.data, bool)
                    or value.data not in {0, 1}
                ):
                    raise ValueError(
                        f"Appearance snapshot {name} must be REG_DWORD 0 or 1."
                    )
            elif value.data is not None or value.type_code is not None:
                raise ValueError(
                    f"Absent appearance snapshot {name} cannot carry data or type."
                )

    def as_dict(self) -> dict[str, Any]:
        return {
            "appsTheme": self.apps_theme.as_dict(),
            "systemTheme": self.system_theme.as_dict(),
            "startTaskbarAccent": self.start_taskbar_accent.as_dict(),
            "titleBordersAccent": self.title_borders_accent.as_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AppearanceRegistrySnapshot:
        expected = {
            "appsTheme",
            "systemTheme",
            "startTaskbarAccent",
            "titleBordersAccent",
        }
        if set(payload) != expected:
            raise ValueError("Appearance registry snapshot fields do not match schema.")
        values: dict[str, RegistryValue] = {}
        for name in expected:
            value = payload.get(name)
            if not isinstance(value, Mapping):
                raise TypeError(f"Appearance registry snapshot {name} is invalid.")
            values[name] = RegistryValue.from_dict(value)
        return cls(
            apps_theme=values["appsTheme"],
            system_theme=values["systemTheme"],
            start_taskbar_accent=values["startTaskbarAccent"],
            title_borders_accent=values["titleBordersAccent"],
        )


class AppearanceSettingsBackend(Protocol):
    def read_visual_state(self) -> ThemeVisualState: ...

    def capture(self) -> AppearanceRegistrySnapshot: ...

    def write_accent_surfaces(
        self,
        *,
        start_taskbar: bool | None,
        title_borders: bool | None,
    ) -> None: ...

    def verify(
        self,
        *,
        apps_theme: ThemeMode | None,
        system_theme: ThemeMode | None,
        start_taskbar: bool | None,
        title_borders: bool | None,
    ) -> tuple[str, ...]: ...

    def restore(self, snapshot: AppearanceRegistrySnapshot) -> None: ...


class AppsThemeReader(Protocol):
    def read_value(self, name: str) -> RegistryValue: ...


class CurrentAppearanceReadError(ThemeSchedulerRuntimeError):
    """Raised when authoritative current Windows appearance cannot be read."""


@dataclass(frozen=True)
class CurrentWindowsAppearance:
    """Authoritative live appearance plus non-authoritative theme diagnostics."""

    visual: ThemeVisualState
    accent_source: str
    divergences: tuple[str, ...] = ()
    theme_visual: ThemeVisualState | None = None
    start_taskbar_accent: bool = False
    title_borders_accent: bool = False

    @property
    def sources_diverged(self) -> bool:
        return bool(self.divergences)


class AccentColorReader(Protocol):
    def read_color(self) -> RgbColor: ...


class WindowsAccentColorReader:
    """Read the selected Windows accent through the public WinRT UISettings API."""

    TIMEOUT_SECONDS = 10

    def __init__(self, bridge_path: Path | None = None) -> None:
        self._bridge_path = Path(bridge_path) if bridge_path else None

    def _resolve_bridge(self) -> Path:
        bridge = (
            self._bridge_path.resolve()
            if self._bridge_path is not None
            else resource_path("entrypoints", "current_appearance_bridge.ps1")
        )
        if not bridge.is_file():
            raise CurrentAppearanceReadError(
                f"Current appearance bridge is missing: {bridge}"
            )
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
            raise CurrentAppearanceReadError(
                f"Windows PowerShell is missing: {powershell}"
            )
        return powershell

    def read_color(self) -> RgbColor:
        if os.name != "nt":
            raise OSError("Windows accent access requires Windows.")
        command = [
            str(self._resolve_powershell()),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self._resolve_bridge()),
            "-Action",
            "Read",
        ]
        try:
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
            raise CurrentAppearanceReadError(
                "Current appearance bridge timed out after "
                f"{self.TIMEOUT_SECONDS} seconds."
            ) from exc
        if completed.returncode != 0:
            detail = " ".join((completed.stderr or completed.stdout).split())
            raise CurrentAppearanceReadError(
                "Current appearance bridge exited with "
                f"{completed.returncode}: {detail[:320] or 'no output'}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise CurrentAppearanceReadError(
                "Current appearance bridge returned invalid JSON."
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("ok") is not True
            or payload.get("action") != "Read"
            or payload.get("source") != "winrt-ui-settings"
        ):
            raise CurrentAppearanceReadError(
                "Current appearance bridge returned an unexpected result."
            )
        color = payload.get("color")
        if not isinstance(color, dict) or set(color) != {
            "alpha",
            "red",
            "green",
            "blue",
        }:
            raise CurrentAppearanceReadError(
                "Current appearance bridge returned an invalid color object."
            )
        alpha = color["alpha"]
        if (
            isinstance(alpha, bool)
            or not isinstance(alpha, int)
            or not 0 <= alpha <= 255
        ):
            raise CurrentAppearanceReadError(
                "Current appearance bridge returned an invalid alpha channel."
            )
        try:
            return RgbColor(
                red=color["red"],
                green=color["green"],
                blue=color["blue"],
            )
        except (TypeError, ValueError) as exc:
            raise CurrentAppearanceReadError(
                "Current appearance bridge returned invalid RGB channels."
            ) from exc


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
        return self._read_exact(PERSONALIZE_KEY, name)

    def _read_exact(self, key_path: str, name: str) -> RegistryValue:
        winreg = self._winreg()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                key_path,
                0,
                winreg.KEY_READ,
            ) as key:
                data, type_code = winreg.QueryValueEx(key, name)
                return RegistryValue(True, data, int(type_code))
        except FileNotFoundError:
            return RegistryValue(False)

    def _read_required_binary_value(self, key_path: str, name: str) -> bool:
        winreg = self._winreg()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                key_path,
                0,
                winreg.KEY_READ,
            ) as key:
                data, type_code = winreg.QueryValueEx(key, name)
        except FileNotFoundError as exc:
            raise CurrentAppearanceReadError(
                f"Windows appearance value is missing: {name}."
            ) from exc
        if int(type_code) != REG_DWORD or isinstance(data, bool) or data not in {0, 1}:
            raise CurrentAppearanceReadError(
                f"Windows appearance value is not a valid REG_DWORD 0 or 1: {name}."
            )
        return bool(data)

    def read_app_mode(self) -> ThemeMode:
        return (
            ThemeMode.LIGHT
            if self._read_required_binary_value(PERSONALIZE_KEY, APPS_THEME_VALUE)
            else ThemeMode.DARK
        )

    def read_system_mode(self) -> ThemeMode:
        return (
            ThemeMode.LIGHT
            if self._read_required_binary_value(PERSONALIZE_KEY, SYSTEM_THEME_VALUE)
            else ThemeMode.DARK
        )

    def read_auto_colorization(self) -> bool:
        return self._read_required_binary_value(
            DESKTOP_KEY,
            AUTO_COLORIZATION_VALUE,
        )

    def read_start_taskbar_accent(self) -> bool:
        return self._read_required_binary_value(
            PERSONALIZE_KEY,
            COLOR_PREVALENCE_VALUE,
        )

    def read_title_borders_accent(self) -> bool:
        return self._read_required_binary_value(
            DWM_KEY,
            COLOR_PREVALENCE_VALUE,
        )

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


class WindowsAppearanceSettingsBackend(WindowsAppearanceReader):
    """Write and exactly restore the four scheduled Windows appearance values."""

    _LOCATIONS = (
        ("apps_theme", PERSONALIZE_KEY, APPS_THEME_VALUE),
        ("system_theme", PERSONALIZE_KEY, SYSTEM_THEME_VALUE),
        ("start_taskbar_accent", PERSONALIZE_KEY, COLOR_PREVALENCE_VALUE),
        ("title_borders_accent", DWM_KEY, COLOR_PREVALENCE_VALUE),
    )

    def read_visual_state(self) -> ThemeVisualState:
        """Read the live appearance from authoritative Windows settings."""

        accent = WindowsAccentColorReader().read_color()
        alpha = (self.read_colorization_color() >> 24) & 0xFF
        return ThemeVisualState(
            auto_colorization="1" if self.read_auto_colorization() else "0",
            colorization_color=(alpha << 24) | accent.value,
            app_mode=self.read_app_mode().value.title(),
            system_mode=self.read_system_mode().value.title(),
        )

    def capture(self) -> AppearanceRegistrySnapshot:
        return AppearanceRegistrySnapshot(
            apps_theme=self._read_exact(PERSONALIZE_KEY, APPS_THEME_VALUE),
            system_theme=self._read_exact(PERSONALIZE_KEY, SYSTEM_THEME_VALUE),
            start_taskbar_accent=self._read_exact(
                PERSONALIZE_KEY,
                COLOR_PREVALENCE_VALUE,
            ),
            title_borders_accent=self._read_exact(DWM_KEY, COLOR_PREVALENCE_VALUE),
        )

    def _write_binary(self, key_path: str, name: str, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise TypeError(f"Windows appearance target {name} must be boolean.")
        winreg = self._winreg()
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            key_path,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(enabled))

    def write_accent_surfaces(
        self,
        *,
        start_taskbar: bool | None,
        title_borders: bool | None,
    ) -> None:
        if start_taskbar is not None:
            self._write_binary(
                PERSONALIZE_KEY,
                COLOR_PREVALENCE_VALUE,
                start_taskbar,
            )
        if title_borders is not None:
            self._write_binary(
                DWM_KEY,
                COLOR_PREVALENCE_VALUE,
                title_borders,
            )

    @staticmethod
    def _expected_binary(mode: ThemeMode) -> int:
        return int(mode is ThemeMode.LIGHT)

    def verify(
        self,
        *,
        apps_theme: ThemeMode | None,
        system_theme: ThemeMode | None,
        start_taskbar: bool | None,
        title_borders: bool | None,
    ) -> tuple[str, ...]:
        actual = self.capture()
        checks = (
            (
                "AppsUseLightTheme",
                actual.apps_theme,
                None if apps_theme is None else self._expected_binary(apps_theme),
            ),
            (
                "SystemUsesLightTheme",
                actual.system_theme,
                None if system_theme is None else self._expected_binary(system_theme),
            ),
            (
                "Start/taskbar ColorPrevalence",
                actual.start_taskbar_accent,
                None if start_taskbar is None else int(start_taskbar),
            ),
            (
                "Title/borders ColorPrevalence",
                actual.title_borders_accent,
                None if title_borders is None else int(title_borders),
            ),
        )
        failures: list[str] = []
        for label, value, expected in checks:
            if expected is None:
                continue
            if (
                not value.exists
                or value.type_code != REG_DWORD
                or value.data != expected
            ):
                failures.append(f"{label} did not match the scheduled target.")
        return tuple(failures)

    def _restore_value(
        self,
        key_path: str,
        name: str,
        value: RegistryValue,
    ) -> None:
        winreg = self._winreg()
        if value.exists:
            assert value.type_code is not None
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                key_path,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.SetValueEx(key, name, 0, value.type_code, value.data)
            return
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                key_path,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass

    def restore(self, snapshot: AppearanceRegistrySnapshot) -> None:
        if not isinstance(snapshot, AppearanceRegistrySnapshot):
            raise TypeError("Appearance rollback snapshot is invalid.")
        for attribute, key_path, name in self._LOCATIONS:
            self._restore_value(key_path, name, getattr(snapshot, attribute))
        if self.capture() != snapshot:
            raise OSError("Appearance registry rollback verification failed.")


class WindowsCurrentAppearanceReader:
    """Combine authoritative Windows settings and optional theme diagnostics."""

    def __init__(
        self,
        *,
        registry: WindowsAppearanceReader | None = None,
        accent: AccentColorReader | None = None,
        theme_visual_reader: Callable[[], ThemeVisualState] | None = None,
    ) -> None:
        self._registry = registry or WindowsAppearanceReader()
        self._accent = accent or WindowsAccentColorReader()
        self._theme_visual_reader = theme_visual_reader

    def read(self) -> CurrentWindowsAppearance:
        app_mode = self._registry.read_app_mode()
        system_mode = self._registry.read_system_mode()
        auto_colorization = self._registry.read_auto_colorization()
        start_taskbar_accent = self._registry.read_start_taskbar_accent()
        title_borders_accent = self._registry.read_title_borders_accent()
        accent = self._accent.read_color()

        theme_visual: ThemeVisualState | None = None
        divergences: list[str] = []
        if self._theme_visual_reader is not None:
            try:
                theme_visual = self._theme_visual_reader()
            except (OSError, ValueError):
                divergences.append("active-theme-unavailable")

        alpha = (
            (theme_visual.colorization_color >> 24) & 0xFF
            if theme_visual is not None
            else 0xFF
        )
        visual = ThemeVisualState(
            auto_colorization="1" if auto_colorization else "0",
            colorization_color=(alpha << 24) | accent.value,
            app_mode=app_mode.value.title(),
            system_mode=system_mode.value.title(),
        )
        if theme_visual is not None:
            if theme_visual.app_mode != visual.app_mode:
                divergences.append("app-mode")
            if theme_visual.system_mode != visual.system_mode:
                divergences.append("system-mode")
            if theme_visual.auto_colorization != visual.auto_colorization:
                divergences.append("auto-colorization")
            if (
                theme_visual.colorization_color & 0x00FFFFFF
                != visual.colorization_color & 0x00FFFFFF
            ):
                divergences.append("accent-color")

        return CurrentWindowsAppearance(
            visual=visual,
            accent_source="winrt-ui-settings",
            divergences=tuple(divergences),
            theme_visual=theme_visual,
            start_taskbar_accent=start_taskbar_accent,
            title_borders_accent=title_borders_accent,
        )
