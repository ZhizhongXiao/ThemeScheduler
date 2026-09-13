"""Managed-theme construction and guarded IThemeManager2 activation."""

from __future__ import annotations

# Public runtime contracts deliberately reject bool-as-int and untyped callers.
# pyright: strict, reportUnnecessaryIsInstance=false
import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from .errors import DataError, ThemeSchedulerRuntimeError
from .resources import resource_path
from .windows_subprocess import no_window_options

THEMES_PATH = r"Software\Microsoft\Windows\CurrentVersion\Themes"
CURRENT_THEME_VALUE = "CurrentTheme"
COLORIZATION_IDENTIFIER = "dwm.colorizationColor"


class ThemeFileError(DataError):
    """Raised when a theme file cannot be patched or verified safely."""


class LiveThemeApplyError(ThemeSchedulerRuntimeError):
    """Raised when live theme apply fails, with explicit rollback status."""

    def __init__(self, message: str, *, rollback_succeeded: bool) -> None:
        super().__init__(message)
        self.rollback_succeeded = rollback_succeeded


@dataclass(frozen=True)
class ThemeVisualState:
    auto_colorization: str
    colorization_color: int
    app_mode: str
    system_mode: str

    def as_dict(self) -> dict[str, str | int]:
        return {
            "autoColorization": self.auto_colorization,
            "colorizationColor": f"0X{self.colorization_color:08X}",
            "appMode": self.app_mode,
            "systemMode": self.system_mode,
        }


def theme_visual_state_from_dict(
    payload: Mapping[str, Any],
) -> ThemeVisualState:
    expected = {
        "autoColorization",
        "colorizationColor",
        "appMode",
        "systemMode",
    }
    if set(payload) != expected:
        raise ThemeFileError("Theme visual-state fields do not match schema.")
    auto = payload.get("autoColorization")
    color = payload.get("colorizationColor")
    app_mode = payload.get("appMode")
    system_mode = payload.get("systemMode")
    if auto not in {"0", "1"}:
        raise ThemeFileError("Theme visual-state AutoColorization is invalid.")
    if not isinstance(color, str) or not re.fullmatch(r"0[xX][0-9A-Fa-f]{8}", color):
        raise ThemeFileError("Theme visual-state color is invalid.")
    if app_mode not in {"Light", "Dark"}:
        raise ThemeFileError("Theme visual-state AppMode is invalid.")
    if system_mode not in {"Light", "Dark"}:
        raise ThemeFileError("Theme visual-state SystemMode is invalid.")
    return ThemeVisualState(
        auto_colorization=auto,
        colorization_color=int(color[2:], 16),
        app_mode=app_mode,
        system_mode=system_mode,
    )


@dataclass(frozen=True)
class ManagedTheme:
    content: bytes
    theme_id: UUID
    before: ThemeVisualState
    after: ThemeVisualState


class ThemeApplyV2Backend(Protocol):
    def current_theme_path(self) -> Path: ...

    def current_v2_index(self) -> int: ...

    def current_v2_indices(self) -> tuple[int, int]: ...

    def apply_theme_v2(self, path: Path) -> tuple[int, int]: ...

    def set_v2_index(self, index: int) -> int: ...


@dataclass(frozen=True)
class ThemeApplyV2Result:
    active_path: Path
    actual: ThemeVisualState
    index_before: int
    bridge_target_index: int
    index_after: int


def _section_span(content: bytes, section: str) -> tuple[int, int]:
    header = re.compile(
        rb"(?im)^\[" + re.escape(section.encode("ascii")) + rb"\][ \t]*\r?$"
    )
    matches = list(header.finditer(content))
    if len(matches) != 1:
        raise ThemeFileError(
            f"Theme must contain exactly one [{section}] section; found {len(matches)}."
        )
    start = matches[0].end()
    next_section = re.search(rb"(?m)^\[[^\r\n]+\][ \t]*\r?$", content[start:])
    end = start + next_section.start() if next_section else len(content)
    return start, end


def _read_value(content: bytes, section: str, key: str) -> str:
    start, end = _section_span(content, section)
    pattern = re.compile(
        rb"(?im)^" + re.escape(key.encode("ascii")) + rb"[ \t]*=[ \t]*([^\r\n]*)"
    )
    matches = list(pattern.finditer(content, start, end))
    if len(matches) != 1:
        raise ThemeFileError(
            f"Theme [{section}] must contain exactly one {key} value; found {len(matches)}."
        )
    try:
        return matches[0].group(1).strip().decode("ascii")
    except UnicodeDecodeError as exc:
        raise ThemeFileError(f"Theme [{section}] {key} is not ASCII.") from exc


def _replace_value(content: bytes, section: str, key: str, value: str) -> bytes:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ThemeFileError(f"Replacement for {key} must be ASCII.") from exc
    start, end = _section_span(content, section)
    pattern = re.compile(
        rb"(?im)^(" + re.escape(key.encode("ascii")) + rb"[ \t]*=[ \t]*)[^\r\n]*"
    )
    matches = list(pattern.finditer(content, start, end))
    if len(matches) != 1:
        raise ThemeFileError(
            f"Theme [{section}] must contain exactly one {key} value; found {len(matches)}."
        )
    match = matches[0]
    return content[: match.start()] + match.group(1) + encoded + content[match.end() :]


def read_visual_state(content: bytes) -> ThemeVisualState:
    color_raw = _read_value(content, "VisualStyles", "ColorizationColor")
    if not re.fullmatch(r"0[xX][0-9A-Fa-f]{8}", color_raw):
        raise ThemeFileError(
            "Theme [VisualStyles] ColorizationColor must be 0X followed by 8 hex digits."
        )
    auto = _read_value(content, "VisualStyles", "AutoColorization")
    if auto not in {"0", "1"}:
        raise ThemeFileError("Theme AutoColorization must be 0 or 1.")
    return ThemeVisualState(
        auto_colorization=auto,
        colorization_color=int(color_raw[2:], 16),
        app_mode=_read_value(content, "VisualStyles", "AppMode"),
        system_mode=_read_value(content, "VisualStyles", "SystemMode"),
    )


def normalize_theme_visual_state(
    source: bytes,
    colorization_color: int,
    *,
    auto_colorization: bool,
    app_mode: str,
    system_mode: str | None = None,
) -> bytes:
    """Create a semantic recovery copy without changing theme identity."""

    if (
        isinstance(colorization_color, bool)
        or not isinstance(colorization_color, int)
        or not 0 <= colorization_color <= 0xFFFFFFFF
    ):
        raise ThemeFileError("Colorization color must be a DWORD.")
    if not isinstance(auto_colorization, bool):
        raise ThemeFileError("AutoColorization must be boolean.")
    if app_mode not in {"Light", "Dark"}:
        raise ThemeFileError("AppMode must be Light or Dark.")
    if system_mode not in {None, "Light", "Dark"}:
        raise ThemeFileError("SystemMode must be Light, Dark, or omitted.")
    before = read_visual_state(source)
    content = _replace_value(
        source,
        "VisualStyles",
        "AutoColorization",
        "1" if auto_colorization else "0",
    )
    content = _replace_value(
        content,
        "VisualStyles",
        "ColorizationColor",
        f"0X{colorization_color:08X}",
    )
    content = _replace_value(
        content,
        "VisualStyles",
        "AppMode",
        app_mode,
    )
    if system_mode is not None:
        content = _replace_value(
            content,
            "VisualStyles",
            "SystemMode",
            system_mode,
        )
    after = read_visual_state(content)
    expected_system_mode = system_mode or before.system_mode
    if (
        after.auto_colorization != ("1" if auto_colorization else "0")
        or after.colorization_color != colorization_color
        or after.app_mode != app_mode
        or after.system_mode != expected_system_mode
    ):
        raise ThemeFileError(
            "Normalized recovery theme did not match semantic targets."
        )
    return content


def build_managed_theme(
    source: bytes,
    colorization_color: int,
    *,
    auto_colorization: bool = False,
    app_mode: str | None = None,
    system_mode: str | None = None,
    theme_id: UUID | None = None,
    display_name: str = "ThemeScheduler Accent Prototype",
) -> ManagedTheme:
    if (
        isinstance(colorization_color, bool)
        or not 0 <= colorization_color <= 0xFFFFFFFF
    ):
        raise ThemeFileError("Colorization color must be a DWORD.")
    if not isinstance(auto_colorization, bool):
        raise ThemeFileError("AutoColorization must be boolean.")
    if app_mode not in {None, "Light", "Dark"}:
        raise ThemeFileError("AppMode must be Light, Dark, or omitted.")
    if system_mode not in {None, "Light", "Dark"}:
        raise ThemeFileError("SystemMode must be Light, Dark, or omitted.")
    before = read_visual_state(source)
    identifier = theme_id or uuid4()
    content = _replace_value(source, "Theme", "DisplayName", display_name)
    content = _replace_value(
        content, "Theme", "ThemeId", "{" + str(identifier).upper() + "}"
    )
    content = _replace_value(
        content,
        "VisualStyles",
        "AutoColorization",
        "1" if auto_colorization else "0",
    )
    content = _replace_value(
        content,
        "VisualStyles",
        "ColorizationColor",
        f"0X{colorization_color:08X}",
    )
    if app_mode is not None:
        content = _replace_value(content, "VisualStyles", "AppMode", app_mode)
    if system_mode is not None:
        content = _replace_value(content, "VisualStyles", "SystemMode", system_mode)
    after = read_visual_state(content)
    expected_app_mode = app_mode or before.app_mode
    expected_system_mode = system_mode or before.system_mode
    if after.app_mode != expected_app_mode:
        raise ThemeFileError("Managed theme AppMode did not match the target.")
    if after.system_mode != expected_system_mode:
        raise ThemeFileError("Managed theme SystemMode did not match the target.")
    return ManagedTheme(content, identifier, before, after)


def write_new_bytes(path: Path, content: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise ThemeFileError(f"Refusing to overwrite existing file: {path}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def resolve_current_theme_path(
    registry_value: str | None, local_app_data: str | None
) -> Path:
    if registry_value:
        return Path(os.path.expandvars(registry_value))
    if not local_app_data:
        raise ThemeFileError(
            "CurrentTheme is absent and LOCALAPPDATA is unavailable for the Custom.theme fallback."
        )
    return Path(local_app_data) / "Microsoft" / "Windows" / "Themes" / "Custom.theme"


class WindowsThemeApplyBackend:
    """Windows current-theme reader and isolated .NET COM bridge adapter."""

    APPLY_TIMEOUT_SECONDS = 30

    def current_theme_path(self) -> Path:
        if os.name != "nt":
            raise OSError("Windows theme access requires Windows.")
        import winreg

        value: str | None = None
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, THEMES_PATH, 0, winreg.KEY_READ
        ) as key:
            try:
                raw_value, type_code = winreg.QueryValueEx(key, CURRENT_THEME_VALUE)
                if type_code not in {
                    winreg.REG_SZ,
                    winreg.REG_EXPAND_SZ,
                } or not isinstance(raw_value, str):
                    raise ThemeFileError("CurrentTheme is not a registry string.")
                value = raw_value
            except FileNotFoundError:
                value = None
        local_app_data = os.environ.get("LOCALAPPDATA")
        path = resolve_current_theme_path(value, local_app_data)
        if not path.is_file() and value:
            fallback = resolve_current_theme_path(None, local_app_data)
            if fallback.is_file():
                path = fallback
        if path.suffix.casefold() != ".theme" or not path.is_file():
            raise ThemeFileError(
                f"Neither CurrentTheme nor the Custom.theme fallback identifies an existing .theme file: {path}"
            )
        return path

    def _run_bridge(
        self,
        action: str,
        *,
        theme_path: Path | None = None,
        theme_index: int | None = None,
    ) -> dict[str, Any]:
        if os.name != "nt":
            raise OSError("Windows theme apply requires Windows.")
        bridge = resource_path("entrypoints", "theme_manager_bridge.ps1")
        if not bridge.is_file():
            raise ThemeFileError(f"Theme manager bridge is missing: {bridge}")
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        powershell = (
            Path(system_root)
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        if not powershell.is_file():
            raise ThemeFileError(f"Windows PowerShell is missing: {powershell}")
        command = [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(bridge),
            "-Action",
            action,
        ]
        if theme_path is not None:
            resolved = Path(theme_path).resolve()
            if resolved.suffix.casefold() != ".theme" or not resolved.is_file():
                raise ThemeFileError(f"Managed theme does not exist: {resolved}")
            command.extend(("-ThemePath", str(resolved)))
        if theme_index is not None:
            command.extend(("-ThemeIndex", str(theme_index)))
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8-sig",
                errors="replace",
                timeout=self.APPLY_TIMEOUT_SECONDS,
                **no_window_options(),
            )
        except subprocess.TimeoutExpired as exc:
            raise OSError(
                f"Theme manager bridge timed out after {self.APPLY_TIMEOUT_SECONDS} seconds."
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise OSError(
                f"Theme manager bridge exited with {completed.returncode}: {detail or 'no output'}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise OSError(
                f"Theme manager bridge returned invalid JSON: {completed.stdout.strip()!r}"
            ) from exc
        if payload.get("ok") is not True or payload.get("action") != action:
            raise OSError(
                f"Theme manager bridge returned an unexpected result: {payload!r}"
            )
        return payload

    def current_v2_index(self) -> int:
        return self.current_v2_indices()[0]

    def current_v2_indices(self) -> tuple[int, int]:
        payload = self._run_bridge("CurrentV2")
        index = payload.get("currentIndex")
        custom = payload.get("customIndex")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or isinstance(custom, bool)
            or not isinstance(custom, int)
            or custom < 0
        ):
            raise OSError(
                f"Theme manager bridge returned invalid current indices: {payload!r}"
            )
        return index, custom

    def apply_theme_v2(self, path: Path) -> tuple[int, int]:
        payload = self._run_bridge("ApplyV2", theme_path=path)
        before = payload.get("beforeIndex")
        current = payload.get("currentIndex")
        if (
            isinstance(before, bool)
            or not isinstance(before, int)
            or before < 0
            or isinstance(current, bool)
            or not isinstance(current, int)
            or current < 0
        ):
            raise OSError(
                f"Theme manager bridge returned invalid V2 indices: {payload!r}"
            )
        return before, current

    def set_v2_index(self, index: int) -> int:
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError(f"Theme index is invalid: {index!r}")
        payload = self._run_bridge("SetV2", theme_index=index)
        current = payload.get("currentIndex")
        if isinstance(current, bool) or not isinstance(current, int) or current < 0:
            raise OSError(
                f"Theme manager bridge returned an invalid current index: {current!r}"
            )
        return current


def _settle(seconds: float) -> None:
    if seconds:
        time.sleep(seconds)


def _read_active_visual_state(
    backend: ThemeApplyV2Backend,
) -> tuple[Path, ThemeVisualState]:
    active_path = backend.current_theme_path()
    return active_path, read_visual_state(active_path.read_bytes())


def _visual_state_failures(
    actual: ThemeVisualState,
    expected: ThemeVisualState,
) -> tuple[str, ...]:
    failures: list[str] = []
    if actual.auto_colorization != expected.auto_colorization:
        failures.append("Active theme AutoColorization did not match the target.")
    if actual.colorization_color != expected.colorization_color:
        failures.append("Active theme ColorizationColor did not match the target.")
    if actual.app_mode != expected.app_mode:
        failures.append("Active theme AppMode did not match the target.")
    if actual.system_mode != expected.system_mode:
        failures.append("Active theme SystemMode did not match the target.")
    return tuple(failures)


def _verify_bridge_before(
    *,
    index_before: int,
    bridge_before: int,
) -> None:
    if bridge_before != index_before:
        raise ThemeFileError(
            f"Current theme index changed during apply: "
            f"{index_before} -> {bridge_before}."
        )


def _verify_current_index(
    *,
    target_index: int,
    current_index: int,
    custom_index: int,
) -> None:
    if current_index not in {target_index, custom_index}:
        raise ThemeFileError(
            f"Current theme index {current_index} matched neither "
            f"target {target_index} nor custom {custom_index}."
        )


def _try_restore_original_index(
    backend: ThemeApplyV2Backend,
    *,
    index_before: int,
    before: ThemeVisualState,
    settle_seconds: float,
) -> bool:
    try:
        if backend.set_v2_index(index_before) != index_before:
            return False
        _settle(settle_seconds)
        _, actual = _read_active_visual_state(backend)
        return not _visual_state_failures(
            actual,
            before,
        )
    except Exception:
        return False


def _try_restore_theme_backup(
    backend: ThemeApplyV2Backend,
    rollback_path: Path,
    *,
    before: ThemeVisualState,
    settle_seconds: float,
) -> bool:
    try:
        backend.apply_theme_v2(rollback_path)
        _settle(settle_seconds)
        _, actual = _read_active_visual_state(backend)
        return not _visual_state_failures(
            actual,
            before,
        )
    except Exception:
        return False


def _rollback_theme_v2(
    backend: ThemeApplyV2Backend,
    *,
    index_before: int,
    before: ThemeVisualState,
    rollback_path: Path | None,
    settle_seconds: float,
) -> bool:
    if _try_restore_original_index(
        backend,
        index_before=index_before,
        before=before,
        settle_seconds=settle_seconds,
    ):
        return True
    return rollback_path is not None and _try_restore_theme_backup(
        backend,
        rollback_path,
        before=before,
        settle_seconds=settle_seconds,
    )


def apply_and_verify_theme_v2(
    managed_path: Path,
    expected: ThemeVisualState,
    before: ThemeVisualState,
    *,
    rollback_path: Path | None = None,
    backend: ThemeApplyV2Backend | None = None,
    settle_seconds: float = 2.0,
) -> ThemeApplyV2Result:
    backend = backend or WindowsThemeApplyBackend()
    before_index = backend.current_v2_index()
    try:
        bridge_before, target_index = backend.apply_theme_v2(managed_path)
        _verify_bridge_before(
            index_before=before_index,
            bridge_before=bridge_before,
        )
        _settle(settle_seconds)
        current_index, custom_index = backend.current_v2_indices()
        _verify_current_index(
            target_index=target_index,
            current_index=current_index,
            custom_index=custom_index,
        )
        active_path, actual = _read_active_visual_state(backend)
        failures = _visual_state_failures(
            actual,
            expected,
        )
        if failures:
            raise ThemeFileError(failures[0])
        return ThemeApplyV2Result(
            active_path,
            actual,
            before_index,
            target_index,
            current_index,
        )
    except Exception as exc:
        rollback_succeeded = _rollback_theme_v2(
            backend,
            index_before=before_index,
            before=before,
            rollback_path=rollback_path,
            settle_seconds=settle_seconds,
        )
        raise LiveThemeApplyError(
            f"Managed theme V2 apply failed: {exc}",
            rollback_succeeded=rollback_succeeded,
        ) from exc
