"""Read-only analysis for phase-2 Windows accent snapshots."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .diagnostics import validate_snapshot
from .errors import ContractError, ThemeSchedulerRuntimeError

PERSONALIZE_PATH = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
EXPLORER_ACCENT_PATH = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent"
DWM_PATH = r"Software\Microsoft\Windows\DWM"

REG_BINARY = 3
REG_DWORD = 4


class AccentAnalysisError(ContractError):
    """Raised when an accent candidate cannot be decoded safely."""


class AccentApplyError(ThemeSchedulerRuntimeError):
    """Raised when accent apply fails, with explicit rollback status."""

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


@dataclass(frozen=True)
class AccentField:
    identifier: str
    path: str
    name: str
    type_code: int


ACCENT_FIELDS = (
    AccentField(
        "explorer.accentColorMenu", EXPLORER_ACCENT_PATH, "AccentColorMenu", REG_DWORD
    ),
    AccentField(
        "explorer.accentPalette", EXPLORER_ACCENT_PATH, "AccentPalette", REG_BINARY
    ),
    AccentField(
        "explorer.startColorMenu", EXPLORER_ACCENT_PATH, "StartColorMenu", REG_DWORD
    ),
    AccentField("dwm.accentColor", DWM_PATH, "AccentColor", REG_DWORD),
    AccentField(
        "dwm.colorizationAfterglow", DWM_PATH, "ColorizationAfterglow", REG_DWORD
    ),
    AccentField("dwm.colorizationColor", DWM_PATH, "ColorizationColor", REG_DWORD),
)


@dataclass(frozen=True)
class AccentRegistryValue:
    exists: bool
    data: int | bytes | None = None
    type_code: int | None = None

    def as_dict(self) -> dict[str, Any]:
        data: Any = self.data
        if isinstance(data, bytes):
            data = {"encoding": "hex", "value": data.hex()}
        return {"exists": self.exists, "data": data, "typeCode": self.type_code}


@dataclass(frozen=True)
class AccentSnapshot:
    values: dict[str, AccentRegistryValue]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "values": {
                field.identifier: {
                    "path": field.path,
                    "name": field.name,
                    **self.values[field.identifier].as_dict(),
                }
                for field in ACCENT_FIELDS
            },
        }


@dataclass(frozen=True)
class AccentApplyResult:
    changed_fields: tuple[str, ...]
    notification_sent: bool
    verified: bool

    @property
    def changed(self) -> bool:
        return bool(self.changed_fields)

    def as_dict(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "changedFields": list(self.changed_fields),
            "notificationSent": self.notification_sent,
            "verified": self.verified,
        }


class AccentBackend(Protocol):
    def read_value(self, path: str, name: str) -> AccentRegistryValue: ...

    def write_dword(self, path: str, name: str, value: int) -> None: ...

    def write_binary(self, path: str, name: str, value: bytes) -> None: ...

    def delete_value(self, path: str, name: str) -> None: ...

    def notify_accent_change(self, colorization_color: int) -> None: ...


class WindowsAccentBackend:
    """Current-user registry adapter for the guarded phase-2 prototype."""

    NOTIFY_TIMEOUT_MS = 2_000

    @staticmethod
    def _winreg() -> Any:
        if os.name != "nt":
            raise OSError("Windows accent access requires Windows.")
        import winreg

        return winreg

    def read_value(self, path: str, name: str) -> AccentRegistryValue:
        winreg = self._winreg()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ
            ) as key:
                data, type_code = winreg.QueryValueEx(key, name)
                return AccentRegistryValue(True, data, int(type_code))
        except FileNotFoundError:
            return AccentRegistryValue(False)

    def write_dword(self, path: str, name: str, value: int) -> None:
        if isinstance(value, bool) or not 0 <= value <= 0xFFFFFFFF:
            raise ValueError(f"Accent DWORD is invalid: {value!r}")
        winreg = self._winreg()
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
            winreg.FlushKey(key)

    def write_binary(self, path: str, name: str, value: bytes) -> None:
        winreg = self._winreg()
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, value)
            winreg.FlushKey(key)

    def delete_value(self, path: str, name: str) -> None:
        winreg = self._winreg()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, name)
                winreg.FlushKey(key)
        except FileNotFoundError:
            return

    @classmethod
    def _send_message(cls, message: int, wparam: int, lparam: int) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        send = user32.SendMessageTimeoutW
        send.argtypes = (
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        )
        send.restype = wintypes.LPARAM
        result = ctypes.c_size_t()
        ctypes.set_last_error(0)
        status = send(
            0xFFFF,
            message,
            wparam,
            lparam,
            0x0002 | 0x0020,
            cls.NOTIFY_TIMEOUT_MS,
            ctypes.byref(result),
        )
        if status == 0:
            error_code = ctypes.get_last_error()
            if error_code:
                raise ctypes.WinError(error_code)
            raise OSError(f"Windows message 0x{message:04X} failed or timed out.")

    def notify_accent_change(self, colorization_color: int) -> None:
        if os.name != "nt":
            raise OSError("Windows accent notification requires Windows.")
        import ctypes

        section = ctypes.c_wchar_p("ImmersiveColorSet")
        pointer = ctypes.cast(section, ctypes.c_void_p).value or 0
        self._send_message(0x001A, 0, pointer)  # WM_SETTINGCHANGE
        self._send_message(
            0x0320, colorization_color, 0
        )  # WM_DWMCOLORIZATIONCOLORCHANGED

    def notify_system_color_change(self) -> None:
        """Broadcast WM_SYSCOLORCHANGE without writing any registry value."""

        if os.name != "nt":
            raise OSError("Windows system-color notification requires Windows.")
        self._send_message(0x0015, 0, 0)  # WM_SYSCOLORCHANGE

    def notify_theme_change(self) -> None:
        """Broadcast WM_THEMECHANGED without writing any registry value."""

        if os.name != "nt":
            raise OSError("Windows theme notification requires Windows.")
        self._send_message(0x031A, 0, 0)  # WM_THEMECHANGED


def _snapshot_values(
    snapshot: Mapping[str, Any], path: str
) -> dict[str, Mapping[str, Any]]:
    for key in snapshot["keys"]:
        if (
            isinstance(key, Mapping)
            and key.get("root") == "HKEY_CURRENT_USER"
            and isinstance(key.get("path"), str)
            and key["path"].casefold() == path.casefold()
        ):
            values = key.get("values", [])
            if not isinstance(values, list):
                raise AccentAnalysisError(f"Registry values are invalid: {path}")
            return {
                value["name"].casefold(): value
                for value in values
                if isinstance(value, Mapping) and isinstance(value.get("name"), str)
            }
    return {}


def _dword(values: Mapping[str, Mapping[str, Any]], name: str) -> int | None:
    value = values.get(name.casefold())
    if value is None:
        return None
    if value.get("typeCode") != REG_DWORD:
        raise AccentAnalysisError(f"{name} must be REG_DWORD.")
    data = value.get("data")
    if (
        isinstance(data, bool)
        or not isinstance(data, int)
        or not 0 <= data <= 0xFFFFFFFF
    ):
        raise AccentAnalysisError(f"{name} must contain an unsigned 32-bit integer.")
    return data


def _binary(values: Mapping[str, Mapping[str, Any]], name: str) -> bytes | None:
    value = values.get(name.casefold())
    if value is None:
        return None
    if value.get("typeCode") != REG_BINARY:
        raise AccentAnalysisError(f"{name} must be REG_BINARY.")
    data = value.get("data")
    if not isinstance(data, Mapping) or data.get("encoding") != "hex":
        raise AccentAnalysisError(f"{name} must use lossless hexadecimal encoding.")
    encoded = data.get("value")
    if not isinstance(encoded, str):
        raise AccentAnalysisError(f"{name} hexadecimal data is invalid.")
    try:
        return bytes.fromhex(encoded)
    except ValueError as exc:
        raise AccentAnalysisError(f"{name} hexadecimal data is invalid.") from exc


def _switch(value: int | None, name: str) -> bool | None:
    if value is None:
        return None
    if value not in (0, 1):
        raise AccentAnalysisError(f"{name} switch must be 0 or 1.")
    return bool(value)


def _abgr_color(value: int | None) -> str | None:
    """Decode a DWORD stored as 0xAABBGGRR into #RRGGBB."""

    if value is None:
        return None
    return f"#{value & 0xFF:02X}{(value >> 8) & 0xFF:02X}{(value >> 16) & 0xFF:02X}"


def _argb_color(value: int | None) -> str | None:
    """Decode a DWORD stored as 0xAARRGGBB into #RRGGBB."""

    if value is None:
        return None
    return f"#{(value >> 16) & 0xFF:02X}{(value >> 8) & 0xFF:02X}{value & 0xFF:02X}"


def _palette_colors(value: bytes | None) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    if len(value) != 32:
        raise AccentAnalysisError(
            f"AccentPalette must contain 32 bytes, got {len(value)}."
        )
    return [
        {
            "index": index,
            "color": f"#{red:02X}{green:02X}{blue:02X}",
            "alpha": alpha,
        }
        for index, (red, green, blue, alpha) in enumerate(
            zip(
                value[0::4],
                value[1::4],
                value[2::4],
                value[3::4],
                strict=True,
            )
        )
    ]


def analyze_accent_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Decode candidate accent fields without modifying Windows settings."""

    validate_snapshot(snapshot)
    personalize = _snapshot_values(snapshot, PERSONALIZE_PATH)
    explorer = _snapshot_values(snapshot, EXPLORER_ACCENT_PATH)
    dwm = _snapshot_values(snapshot, DWM_PATH)

    transparency = _dword(personalize, "EnableTransparency")
    start_taskbar = _dword(personalize, "ColorPrevalence")
    title_borders = _dword(dwm, "ColorPrevalence")
    accent_menu = _dword(explorer, "AccentColorMenu")
    start_menu = _dword(explorer, "StartColorMenu")
    dwm_accent = _dword(dwm, "AccentColor")
    colorization = _dword(dwm, "ColorizationColor")
    palette = _binary(explorer, "AccentPalette")

    return {
        "schemaVersion": 1,
        "kind": "themescheduler.accent-analysis",
        "sourceCapturedAt": snapshot.get("capturedAt"),
        "visibleSettings": {
            "transparencyEnabled": _switch(transparency, "EnableTransparency"),
            "startAndTaskbarAccentEnabled": _switch(
                start_taskbar, "Personalize.ColorPrevalence"
            ),
            "titleBarsAndBordersAccentEnabled": _switch(
                title_borders, "DWM.ColorPrevalence"
            ),
        },
        "colors": {
            "accentColorMenu": _abgr_color(accent_menu),
            "startColorMenu": _abgr_color(start_menu),
            "dwmAccentColor": _abgr_color(dwm_accent),
            "colorizationColor": _argb_color(colorization),
            "palette": _palette_colors(palette),
        },
        "raw": {
            "accentColorMenu": accent_menu,
            "startColorMenu": start_menu,
            "dwmAccentColor": dwm_accent,
            "colorizationColor": colorization,
            "accentPaletteHex": palette.hex() if palette is not None else None,
        },
    }


def _validate_registry_value(
    field: AccentField, value: AccentRegistryValue, *, require_exists: bool
) -> None:
    if not value.exists:
        if require_exists:
            raise AccentAnalysisError(f"Accent snapshot is missing {field.identifier}.")
        return
    if value.type_code != field.type_code:
        expected = "REG_BINARY" if field.type_code == REG_BINARY else "REG_DWORD"
        raise AccentAnalysisError(f"{field.identifier} must be {expected}.")
    if field.type_code == REG_DWORD:
        if (
            isinstance(value.data, bool)
            or not isinstance(value.data, int)
            or not 0 <= value.data <= 0xFFFFFFFF
        ):
            raise AccentAnalysisError(f"{field.identifier} has an invalid DWORD.")
    elif not isinstance(value.data, bytes):
        raise AccentAnalysisError(f"{field.identifier} has invalid binary data.")
    if field.name == "AccentPalette" and (
        not isinstance(value.data, bytes) or len(value.data) != 32
    ):
        raise AccentAnalysisError("AccentPalette must contain 32 bytes.")


def read_accent_snapshot(backend: AccentBackend | None = None) -> AccentSnapshot:
    """Read the six phase-2 candidate fields without changing Windows."""

    backend = backend or WindowsAccentBackend()
    values: dict[str, AccentRegistryValue] = {}
    for field in ACCENT_FIELDS:
        try:
            value = backend.read_value(field.path, field.name)
        except OSError as exc:
            raise AccentAnalysisError(
                f"Failed to read {field.identifier}: {exc}"
            ) from exc
        _validate_registry_value(field, value, require_exists=False)
        values[field.identifier] = value
    return AccentSnapshot(values)


def accent_snapshot_from_diagnostic(
    snapshot: Mapping[str, Any],
) -> AccentSnapshot:
    """Extract an apply-ready six-field snapshot from a diagnostic capture."""

    validate_snapshot(snapshot)
    by_path = {
        EXPLORER_ACCENT_PATH: _snapshot_values(snapshot, EXPLORER_ACCENT_PATH),
        DWM_PATH: _snapshot_values(snapshot, DWM_PATH),
    }
    decoded: dict[str, AccentRegistryValue] = {}
    for field in ACCENT_FIELDS:
        raw = by_path[field.path].get(field.name.casefold())
        if raw is None:
            value = AccentRegistryValue(False)
        elif field.type_code == REG_DWORD:
            value = AccentRegistryValue(
                True,
                _dword(by_path[field.path], field.name),
                raw.get("typeCode"),
            )
        else:
            value = AccentRegistryValue(
                True,
                _binary(by_path[field.path], field.name),
                raw.get("typeCode"),
            )
        _validate_registry_value(field, value, require_exists=True)
        decoded[field.identifier] = value
    return AccentSnapshot(decoded)


def _write_value(
    backend: AccentBackend, field: AccentField, value: AccentRegistryValue
) -> None:
    if not value.exists:
        backend.delete_value(field.path, field.name)
    elif field.type_code == REG_DWORD:
        assert isinstance(value.data, int) and not isinstance(value.data, bool)
        backend.write_dword(field.path, field.name, value.data)
    else:
        assert isinstance(value.data, bytes)
        backend.write_binary(field.path, field.name, value.data)


def _colorization_value(snapshot: AccentSnapshot) -> int:
    value = snapshot.values["dwm.colorizationColor"]
    if not value.exists or not isinstance(value.data, int):
        return 0
    return value.data


def _rollback_accent(
    backend: AccentBackend,
    before: AccentSnapshot,
    changed: tuple[AccentField, ...],
) -> tuple[bool, tuple[str, ...]]:
    errors: list[str] = []
    for field in reversed(changed):
        try:
            _write_value(backend, field, before.values[field.identifier])
        except Exception as exc:
            errors.append(f"restore {field.identifier}: {type(exc).__name__}: {exc}")
    try:
        backend.notify_accent_change(_colorization_value(before))
    except Exception as exc:
        errors.append(f"notify rollback: {type(exc).__name__}: {exc}")
    if not errors:
        try:
            after = read_accent_snapshot(backend)
            if after.values != before.values:
                errors.append(
                    "rollback readback does not match the original accent state"
                )
        except Exception as exc:
            errors.append(f"verify rollback: {type(exc).__name__}: {exc}")
    return not errors, tuple(errors)


def apply_accent_snapshot(
    target: AccentSnapshot, backend: AccentBackend | None = None
) -> AccentApplyResult:
    """Apply six accent fields, notify, verify, and roll back on failure."""

    backend = backend or WindowsAccentBackend()
    for field in ACCENT_FIELDS:
        if field.identifier not in target.values:
            raise AccentAnalysisError(f"Accent snapshot is missing {field.identifier}.")
        _validate_registry_value(
            field, target.values[field.identifier], require_exists=True
        )
    before = read_accent_snapshot(backend)
    changed = tuple(
        field
        for field in ACCENT_FIELDS
        if before.values[field.identifier] != target.values[field.identifier]
    )
    if not changed:
        return AccentApplyResult((), False, True)

    try:
        for field in changed:
            _write_value(backend, field, target.values[field.identifier])
        backend.notify_accent_change(_colorization_value(target))
        after = read_accent_snapshot(backend)
        if after.values != target.values:
            raise AccentAnalysisError(
                "Accent registry readback does not match the target snapshot."
            )
    except Exception as exc:
        rollback_succeeded, rollback_errors = _rollback_accent(backend, before, changed)
        message = f"Accent apply failed: {type(exc).__name__}: {exc}"
        if not rollback_succeeded:
            message += "; rollback was incomplete"
        raise AccentApplyError(
            message,
            rollback_succeeded=rollback_succeeded,
            rollback_errors=rollback_errors,
        ) from exc

    return AccentApplyResult(tuple(field.identifier for field in changed), True, True)
