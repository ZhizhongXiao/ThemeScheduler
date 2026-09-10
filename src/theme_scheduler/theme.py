"""Windows app light-mode prototype with verification and rollback.

The registry values used here are phase-1 prototype candidates.  They remain
subject to live validation on supported Windows 11 builds before the design is
frozen.  Importing this module has no system side effects.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from .errors import ThemeSchedulerRuntimeError

PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
SYSTEM_THEME_VALUE = "SystemUsesLightTheme"
APPS_THEME_VALUE = "AppsUseLightTheme"
REG_DWORD = 4


class ThemeMode(str, Enum):
    DARK = "dark"
    LIGHT = "light"

    @property
    def registry_data(self) -> int:
        return 1 if self is ThemeMode.LIGHT else 0

    @classmethod
    def from_registry_data(cls, value: int) -> ThemeMode:
        if value == 0:
            return cls.DARK
        if value == 1:
            return cls.LIGHT
        raise ValueError(f"Theme registry value must be 0 or 1, got {value!r}.")


@dataclass(frozen=True)
class ThemeProfile:
    apps: ThemeMode

    def as_dict(self) -> dict[str, str]:
        return {"appsTheme": self.apps.value}


@dataclass(frozen=True)
class ThemeState:
    """Observed state; None means the registry value is absent, not light/dark."""

    windows: ThemeMode | None
    apps: ThemeMode | None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "windowsTheme": self.windows.value if self.windows is not None else None,
            "appsTheme": self.apps.value if self.apps is not None else None,
        }

    def matches(self, profile: ThemeProfile) -> bool:
        return self.apps is profile.apps


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


@dataclass(frozen=True)
class ThemeSnapshot:
    state: ThemeState
    values: dict[str, RegistryValue]

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.as_dict(),
            "registryKey": f"HKEY_CURRENT_USER\\{PERSONALIZE_KEY}",
            "values": {
                name: value.as_dict() for name, value in sorted(self.values.items())
            },
        }


@dataclass(frozen=True)
class ThemeApplyResult:
    before: ThemeState
    requested: ThemeProfile
    after: ThemeState
    changed_values: tuple[str, ...]
    notification_sent: bool
    verified: bool

    @property
    def changed(self) -> bool:
        return bool(self.changed_values)

    def as_dict(self) -> dict[str, Any]:
        return {
            "before": self.before.as_dict(),
            "requested": self.requested.as_dict(),
            "after": self.after.as_dict(),
            "changed": self.changed,
            "changedValues": list(self.changed_values),
            "notificationSent": self.notification_sent,
            "verified": self.verified,
        }


@dataclass(frozen=True)
class ThemeRestoreResult:
    before: ThemeState
    after: ThemeState
    changed_values: tuple[str, ...]
    notification_sent: bool
    verified: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "before": self.before.as_dict(),
            "after": self.after.as_dict(),
            "changed": bool(self.changed_values),
            "changedValues": list(self.changed_values),
            "notificationSent": self.notification_sent,
            "verified": self.verified,
        }


class ThemeError(ThemeSchedulerRuntimeError):
    """Base class for theme prototype failures."""


class ThemeReadError(ThemeError):
    """Raised when the current theme cannot be interpreted safely."""


class ThemeVerificationError(ThemeError):
    """Raised when registry readback does not match the requested profile."""


class ThemeApplyError(ThemeError):
    """Raised when apply fails, with explicit rollback status."""

    def __init__(
        self,
        message: str,
        *,
        rollback_succeeded: bool,
        rollback_errors: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.rollback_succeeded = rollback_succeeded
        self.rollback_errors = rollback_errors


class ThemeBackend(Protocol):
    def read_value(self, name: str) -> RegistryValue: ...

    def write_dword(self, name: str, value: int) -> None: ...

    def delete_value(self, name: str) -> None: ...

    def notify_theme_change(self) -> None: ...


class WindowsThemeBackend:
    """HKCU registry and Win32 notification adapter for the phase-1 prototype."""

    # The message itself is documented, while this lParam string is a prototype
    # candidate that must be confirmed through live Shell/application behavior.
    SETTING_CHANGE_SECTION = "ImmersiveColorSet"
    NOTIFY_TIMEOUT_MS = 2_000

    @staticmethod
    def _winreg() -> Any:
        if os.name != "nt":
            raise OSError("Windows theme access requires Windows.")
        import winreg

        return winreg

    def read_value(self, name: str) -> RegistryValue:
        winreg = self._winreg()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY, 0, winreg.KEY_READ
            ) as key:
                data, type_code = winreg.QueryValueEx(key, name)
                return RegistryValue(True, data, int(type_code))
        except FileNotFoundError:
            return RegistryValue(False)

    def write_dword(self, name: str, value: int) -> None:
        if value not in (0, 1):
            raise ValueError(f"Theme DWORD must be 0 or 1, got {value!r}.")
        winreg = self._winreg()
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
            winreg.FlushKey(key)

    def delete_value(self, name: str) -> None:
        winreg = self._winreg()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, name)
                winreg.FlushKey(key)
        except FileNotFoundError:
            # Absence is already the desired rollback state.
            return

    def notify_theme_change(self) -> None:
        if os.name != "nt":
            raise OSError("Windows theme notification requires Windows.")

        import ctypes
        from ctypes import wintypes

        hwnd_broadcast = 0xFFFF
        wm_settingchange = 0x001A
        smto_abortifhung = 0x0002
        smto_erroronexit = 0x0020

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        send_message_timeout = user32.SendMessageTimeoutW
        send_message_timeout.argtypes = (
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        )
        send_message_timeout.restype = wintypes.LPARAM

        section = ctypes.c_wchar_p(self.SETTING_CHANGE_SECTION)
        section_pointer = ctypes.cast(section, ctypes.c_void_p).value or 0
        message_result = ctypes.c_size_t()
        ctypes.set_last_error(0)
        status = send_message_timeout(
            hwnd_broadcast,
            wm_settingchange,
            0,
            section_pointer,
            smto_abortifhung | smto_erroronexit,
            self.NOTIFY_TIMEOUT_MS,
            ctypes.byref(message_result),
        )
        if status == 0:
            error_code = ctypes.get_last_error()
            if error_code:
                raise ctypes.WinError(error_code)
            raise OSError("WM_SETTINGCHANGE broadcast failed or timed out.")


def _decode_value(name: str, value: RegistryValue) -> ThemeMode | None:
    if not value.exists:
        return None
    if value.type_code != REG_DWORD:
        raise ThemeReadError(
            f"Registry value {name} must be REG_DWORD ({REG_DWORD}), "
            f"got type {value.type_code!r}."
        )
    if isinstance(value.data, bool) or not isinstance(value.data, int):
        raise ThemeReadError(f"Registry value {name} is not an integer DWORD.")
    try:
        return ThemeMode.from_registry_data(value.data)
    except ValueError as exc:
        raise ThemeReadError(
            f"Registry value {name} is invalid: {value.data!r}."
        ) from exc


def read_theme_snapshot(backend: ThemeBackend | None = None) -> ThemeSnapshot:
    """Read and strictly validate the two candidate theme registry values."""

    backend = backend or WindowsThemeBackend()
    values: dict[str, RegistryValue] = {}
    for name in (SYSTEM_THEME_VALUE, APPS_THEME_VALUE):
        try:
            values[name] = backend.read_value(name)
        except OSError as exc:
            raise ThemeReadError(
                f"Failed to read registry value {name}: {exc}"
            ) from exc

    return ThemeSnapshot(
        state=ThemeState(
            windows=_decode_value(SYSTEM_THEME_VALUE, values[SYSTEM_THEME_VALUE]),
            apps=_decode_value(APPS_THEME_VALUE, values[APPS_THEME_VALUE]),
        ),
        values=values,
    )


def _desired_values(profile: ThemeProfile) -> dict[str, int]:
    # Product switching deliberately leaves SystemUsesLightTheme untouched.
    return {APPS_THEME_VALUE: profile.apps.registry_data}


def theme_snapshot_from_dict(payload: Any) -> ThemeSnapshot:
    """Parse a strictly scoped theme snapshot from a diagnostic backup."""

    if not isinstance(payload, dict):
        raise ThemeReadError("Theme snapshot must be an object.")
    expected_key = f"HKEY_CURRENT_USER\\{PERSONALIZE_KEY}"
    if payload.get("registryKey") != expected_key:
        raise ThemeReadError("Theme snapshot registry key is unexpected.")
    raw_values = payload.get("values")
    if not isinstance(raw_values, dict):
        raise ThemeReadError("Theme snapshot values must be an object.")

    values: dict[str, RegistryValue] = {}
    for name in (SYSTEM_THEME_VALUE, APPS_THEME_VALUE):
        raw = raw_values.get(name)
        if not isinstance(raw, dict) or not isinstance(raw.get("exists"), bool):
            raise ThemeReadError(f"Theme snapshot value is invalid: {name}")
        if raw["exists"]:
            value = RegistryValue(True, raw.get("data"), raw.get("typeCode"))
            _decode_value(name, value)
        else:
            value = RegistryValue(False)
        values[name] = value

    return ThemeSnapshot(
        state=ThemeState(
            windows=_decode_value(SYSTEM_THEME_VALUE, values[SYSTEM_THEME_VALUE]),
            apps=_decode_value(APPS_THEME_VALUE, values[APPS_THEME_VALUE]),
        ),
        values=values,
    )


def _rollback(
    backend: ThemeBackend,
    before: ThemeSnapshot,
    names: tuple[str, ...],
) -> tuple[bool, tuple[str, ...]]:
    errors: list[str] = []
    for name in reversed(names):
        original = before.values[name]
        try:
            if original.exists:
                backend.write_dword(name, int(original.data))
            else:
                backend.delete_value(name)
        except Exception as exc:  # rollback must preserve every failure detail
            errors.append(f"restore {name}: {type(exc).__name__}: {exc}")

    if names:
        try:
            backend.notify_theme_change()
        except Exception as exc:
            errors.append(f"notify rollback: {type(exc).__name__}: {exc}")

    if not errors:
        try:
            restored = read_theme_snapshot(backend)
            if restored.values != before.values:
                errors.append(
                    "rollback readback does not match the original registry state"
                )
        except Exception as exc:
            errors.append(f"verify rollback: {type(exc).__name__}: {exc}")
    return not errors, tuple(errors)


def apply_theme_profile(
    profile: ThemeProfile,
    backend: ThemeBackend | None = None,
) -> ThemeApplyResult:
    """Apply only the app mode, leaving the Windows mode untouched."""

    backend = backend or WindowsThemeBackend()
    before = read_theme_snapshot(backend)
    desired = _desired_values(profile)
    changed_names = tuple(
        name
        for name, value in desired.items()
        if not before.values[name].exists or before.values[name].data != value
    )

    if not changed_names:
        return ThemeApplyResult(
            before=before.state,
            requested=profile,
            after=before.state,
            changed_values=(),
            notification_sent=False,
            verified=True,
        )

    try:
        for name in changed_names:
            backend.write_dword(name, desired[name])
        backend.notify_theme_change()
        after = read_theme_snapshot(backend)
        if not after.state.matches(profile):
            raise ThemeVerificationError(
                f"Readback mismatch: requested {profile.as_dict()}, "
                f"got {after.state.as_dict()}."
            )
    except Exception as exc:
        rollback_succeeded, rollback_errors = _rollback(backend, before, changed_names)
        message = f"Theme apply failed: {type(exc).__name__}: {exc}"
        if not rollback_succeeded:
            message += "; rollback was incomplete"
        raise ThemeApplyError(
            message,
            rollback_succeeded=rollback_succeeded,
            rollback_errors=rollback_errors,
        ) from exc

    return ThemeApplyResult(
        before=before.state,
        requested=profile,
        after=after.state,
        changed_values=changed_names,
        notification_sent=True,
        verified=True,
    )


def restore_theme_snapshot(
    target: ThemeSnapshot,
    backend: ThemeBackend | None = None,
) -> ThemeRestoreResult:
    """Restore only the app-mode value, including its prior absence."""

    backend = backend or WindowsThemeBackend()
    for name in (APPS_THEME_VALUE,):
        if name not in target.values:
            raise ThemeReadError(f"Restore target is missing registry value: {name}")
        _decode_value(name, target.values[name])
    before = read_theme_snapshot(backend)
    changed_names = tuple(
        name
        for name in (APPS_THEME_VALUE,)
        if before.values[name] != target.values[name]
    )
    if not changed_names:
        return ThemeRestoreResult(
            before=before.state,
            after=before.state,
            changed_values=(),
            notification_sent=False,
            verified=True,
        )

    try:
        for name in changed_names:
            desired = target.values[name]
            if desired.exists:
                backend.write_dword(name, int(desired.data))
            else:
                backend.delete_value(name)
        backend.notify_theme_change()
        after = read_theme_snapshot(backend)
        if after.values[APPS_THEME_VALUE] != target.values[APPS_THEME_VALUE]:
            raise ThemeVerificationError(
                "App-mode restore readback does not match the requested registry state."
            )
    except Exception as exc:
        rollback_succeeded, rollback_errors = _rollback(backend, before, changed_names)
        message = f"Theme restore failed: {type(exc).__name__}: {exc}"
        if not rollback_succeeded:
            message += "; rollback was incomplete"
        raise ThemeApplyError(
            message,
            rollback_succeeded=rollback_succeeded,
            rollback_errors=rollback_errors,
        ) from exc

    return ThemeRestoreResult(
        before=before.state,
        after=after.state,
        changed_values=changed_names,
        notification_sent=True,
        verified=True,
    )
