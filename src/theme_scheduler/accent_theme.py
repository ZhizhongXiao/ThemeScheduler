"""Managed-theme construction and guarded IThemeManager2 activation."""

from __future__ import annotations

# Public runtime contracts deliberately reject bool-as-int and untyped callers.
# pyright: strict, reportUnnecessaryIsInstance=false
import hashlib
import json
import math
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
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

    def __init__(
        self,
        message: str,
        *,
        rollback_succeeded: bool,
        verification_diagnostics: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.rollback_succeeded = rollback_succeeded
        self.verification_diagnostics = verification_diagnostics


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

    def current_v2_index(self, *, timeout_seconds: float | None = None) -> int: ...

    def current_v2_indices(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[int, int]: ...

    def apply_theme_v2(
        self,
        path: Path,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[int, int]: ...

    def set_v2_index(
        self,
        index: int,
        *,
        timeout_seconds: float | None = None,
    ) -> int: ...


@dataclass(frozen=True)
class ThemeApplyV2Result:
    active_path: Path
    actual: ThemeVisualState
    index_before: int
    bridge_target_index: int
    index_after: int
    verification_diagnostics: dict[str, object]


class _ThemeVerificationError(ThemeFileError):
    def __init__(
        self,
        message: str,
        *,
        diagnostics: dict[str, object],
    ) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


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


def _section_count(content: bytes, section: str) -> int:
    header = re.compile(
        rb"(?im)^\[" + re.escape(section.encode("ascii")) + rb"\][ \t]*\r?$"
    )
    return len(list(header.finditer(content)))


def _preferred_newline(content: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in content else b"\n"


def _append_section(
    content: bytes,
    section: str,
    values: Mapping[str, str],
) -> bytes:
    newline = _preferred_newline(content)
    prefix = content
    if prefix and not prefix.endswith((b"\n", b"\r")):
        prefix += newline
    if prefix and not prefix.endswith(newline * 2):
        prefix += newline
    body = [f"[{section}]".encode("ascii")]
    body.extend(f"{key}={value}".encode("ascii") for key, value in values.items())
    return prefix + newline.join(body) + newline


def _ensure_section(content: bytes, section: str) -> bytes:
    count = _section_count(content, section)
    if count > 1:
        raise ThemeFileError(
            f"Theme must contain at most one [{section}] section; found {count}."
        )
    return content if count == 1 else _append_section(content, section, {})


def _set_or_add_value(content: bytes, section: str, key: str, value: str) -> bytes:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ThemeFileError(f"Replacement for {key} must be ASCII.") from exc
    start, end = _section_span(content, section)
    pattern = re.compile(
        rb"(?im)^(" + re.escape(key.encode("ascii")) + rb"[ \t]*=[ \t]*)[^\r\n]*"
    )
    matches = list(pattern.finditer(content, start, end))
    if len(matches) > 1:
        raise ThemeFileError(
            f"Theme [{section}] must contain at most one {key} value; "
            f"found {len(matches)}."
        )
    if matches:
        match = matches[0]
        return (
            content[: match.start()] + match.group(1) + encoded + content[match.end() :]
        )
    newline = _preferred_newline(content)
    prefix = content[:end]
    if prefix and not prefix.endswith((b"\n", b"\r")):
        prefix += newline
    return prefix + key.encode("ascii") + b"=" + encoded + newline + content[end:]


def _add_value_if_missing(content: bytes, section: str, key: str, value: str) -> bytes:
    start, end = _section_span(content, section)
    pattern = re.compile(
        rb"(?im)^" + re.escape(key.encode("ascii")) + rb"[ \t]*=[ \t]*[^\r\n]*"
    )
    matches = list(pattern.finditer(content, start, end))
    if len(matches) > 1:
        raise ThemeFileError(
            f"Theme [{section}] must contain at most one {key} value; "
            f"found {len(matches)}."
        )
    return content if matches else _set_or_add_value(content, section, key, value)


def _validate_visual_state(state: ThemeVisualState) -> None:
    theme_visual_state_from_dict(state.as_dict())


def materialize_theme_visual_state(
    source: bytes,
    current_state: ThemeVisualState,
) -> bytes:
    """Return an applyable theme copy while preserving every source byte possible."""

    _validate_visual_state(current_state)
    count = _section_count(source, "VisualStyles")
    if count > 1:
        raise ThemeFileError(
            f"Theme must contain at most one [VisualStyles] section; found {count}."
        )
    content = source
    if count == 0:
        content = _append_section(
            content,
            "VisualStyles",
            {
                "Path": r"%ResourceDir%\Themes\Aero\Aero.msstyles",
                "ColorStyle": "NormalColor",
                "Size": "NormalSize",
                "AutoColorization": current_state.auto_colorization,
                "ColorizationColor": f"0X{current_state.colorization_color:08X}",
                "SystemMode": current_state.system_mode,
                "AppMode": current_state.app_mode,
                "VisualStyleVersion": "10",
            },
        )
    else:
        for key, value in (
            ("AutoColorization", current_state.auto_colorization),
            ("ColorizationColor", f"0X{current_state.colorization_color:08X}"),
            ("SystemMode", current_state.system_mode),
            ("AppMode", current_state.app_mode),
        ):
            content = _set_or_add_value(content, "VisualStyles", key, value)

    content = _ensure_section(content, "MasterThemeSelector")
    content = _add_value_if_missing(
        content,
        "MasterThemeSelector",
        "MTSM",
        "RJSPBS",
    )
    return content


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
    current_state: ThemeVisualState | None = None,
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
    before = current_state or read_visual_state(source)
    content = materialize_theme_visual_state(source, before)
    content = _set_or_add_value(
        content,
        "VisualStyles",
        "AutoColorization",
        "1" if auto_colorization else "0",
    )
    content = _set_or_add_value(
        content,
        "VisualStyles",
        "ColorizationColor",
        f"0X{colorization_color:08X}",
    )
    content = _set_or_add_value(
        content,
        "VisualStyles",
        "AppMode",
        app_mode,
    )
    content = _set_or_add_value(
        content,
        "VisualStyles",
        "SystemMode",
        system_mode or before.system_mode,
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
    current_state: ThemeVisualState | None = None,
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
    before = current_state or read_visual_state(source)
    content = materialize_theme_visual_state(source, before)
    identifier = theme_id or uuid4()
    content = _ensure_section(content, "Theme")
    content = _set_or_add_value(content, "Theme", "DisplayName", display_name)
    content = _set_or_add_value(
        content, "Theme", "ThemeId", "{" + str(identifier).upper() + "}"
    )
    content = _set_or_add_value(
        content,
        "VisualStyles",
        "AutoColorization",
        "1" if auto_colorization else "0",
    )
    content = _set_or_add_value(
        content,
        "VisualStyles",
        "ColorizationColor",
        f"0X{colorization_color:08X}",
    )
    content = _set_or_add_value(
        content,
        "VisualStyles",
        "AppMode",
        app_mode or before.app_mode,
    )
    content = _set_or_add_value(
        content,
        "VisualStyles",
        "SystemMode",
        system_mode or before.system_mode,
    )
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
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        timeout = (
            self.APPLY_TIMEOUT_SECONDS
            if timeout_seconds is None
            else min(self.APPLY_TIMEOUT_SECONDS, timeout_seconds)
        )
        if not math.isfinite(timeout) or timeout <= 0:
            raise TimeoutError("Theme manager bridge deadline has expired.")
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
                timeout=timeout,
                **no_window_options(),
            )
        except subprocess.TimeoutExpired as exc:
            raise OSError(
                f"Theme manager bridge timed out after {timeout:.3f} seconds."
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

    def current_v2_index(self, *, timeout_seconds: float | None = None) -> int:
        return self.current_v2_indices(timeout_seconds=timeout_seconds)[0]

    def current_v2_indices(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[int, int]:
        payload = self._run_bridge("CurrentV2", timeout_seconds=timeout_seconds)
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

    def apply_theme_v2(
        self,
        path: Path,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[int, int]:
        payload = self._run_bridge(
            "ApplyV2",
            theme_path=path,
            timeout_seconds=timeout_seconds,
        )
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

    def set_v2_index(
        self,
        index: int,
        *,
        timeout_seconds: float | None = None,
    ) -> int:
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError(f"Theme index is invalid: {index!r}")
        payload = self._run_bridge(
            "SetV2",
            theme_index=index,
            timeout_seconds=timeout_seconds,
        )
        current = payload.get("currentIndex")
        if isinstance(current, bool) or not isinstance(current, int) or current < 0:
            raise OSError(
                f"Theme manager bridge returned an invalid current index: {current!r}"
            )
        return current


def _read_active_visual_state(
    backend: ThemeApplyV2Backend,
    visual_state_reader: Callable[[], ThemeVisualState] | None = None,
) -> tuple[Path, ThemeVisualState]:
    active_path = backend.current_theme_path()
    actual = (
        visual_state_reader()
        if visual_state_reader is not None
        else read_visual_state(active_path.read_bytes())
    )
    return active_path, actual


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


def _verification_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _settle_before_deadline(seconds: float, deadline: float) -> None:
    if seconds <= 0:
        return
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Theme transaction deadline expired before settling.")
    time.sleep(min(seconds, remaining))
    if time.monotonic() >= deadline and seconds > remaining:
        raise TimeoutError("Theme transaction deadline expired while settling.")


def _poll_theme_visual_state(
    backend: ThemeApplyV2Backend,
    expected: ThemeVisualState,
    *,
    visual_state_reader: Callable[[], ThemeVisualState] | None,
    target_index: int,
    index_before: int,
    timeout_seconds: float,
    poll_interval_seconds: float,
    operation_deadline: float,
) -> tuple[Path, ThemeVisualState, int, dict[str, object]]:
    started_at = _verification_timestamp()
    started_monotonic = time.monotonic()
    deadline = min(
        operation_deadline,
        started_monotonic + timeout_seconds,
    )
    samples: list[dict[str, object]] = []
    last_failures: list[str] = []

    while True:
        active_path: Path | None = None
        actual: ThemeVisualState | None = None
        current_index: int | None = None
        custom_index: int | None = None
        failures: list[str] = []
        sample: dict[str, object] = {"sampleStartedAt": _verification_timestamp()}

        try:
            active_path, actual = _read_active_visual_state(
                backend,
                visual_state_reader,
            )
            sample["activeThemePath"] = str(active_path)
            sample["actual"] = actual.as_dict()
            failures.extend(_visual_state_failures(actual, expected))
        except Exception as exc:
            failures.append(f"Active visual-state read failed: {exc}")

        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Theme index verification deadline expired.")
            current_index, custom_index = backend.current_v2_indices(
                timeout_seconds=remaining
            )
            sample["currentIndex"] = current_index
            sample["customIndex"] = custom_index
            try:
                _verify_current_index(
                    target_index=target_index,
                    current_index=current_index,
                    custom_index=custom_index,
                )
            except ThemeFileError as exc:
                failures.append(str(exc))
        except Exception as exc:
            failures.append(f"Theme index read failed: {exc}")

        sample["observedAt"] = _verification_timestamp()
        if time.monotonic() > deadline:
            failures.append("Theme readback completed after its deadline.")
        sample["failures"] = failures
        samples.append(sample)
        if (
            not failures
            and active_path is not None
            and actual is not None
            and current_index is not None
        ):
            diagnostics = _theme_verification_diagnostics(
                expected=expected,
                samples=samples,
                started_at=started_at,
                started_monotonic=started_monotonic,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
                index_before=index_before,
                target_index=target_index,
            )
            return active_path, actual, current_index, diagnostics

        last_failures = failures
        remaining = deadline - time.monotonic()
        if remaining <= 0 or poll_interval_seconds <= 0:
            break
        time.sleep(min(poll_interval_seconds, remaining))

    diagnostics = _theme_verification_diagnostics(
        expected=expected,
        samples=samples,
        started_at=started_at,
        started_monotonic=started_monotonic,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        index_before=index_before,
        target_index=target_index,
    )
    final_observation = samples[-1].get("observedAt", "unknown time")
    detail = "; ".join(last_failures) or "The active theme did not match."
    raise _ThemeVerificationError(
        f"Theme verification did not converge within {timeout_seconds:.3f}s; "
        f"last observation at {final_observation}: {detail}",
        diagnostics=diagnostics,
    )


def _theme_verification_diagnostics(
    *,
    expected: ThemeVisualState,
    samples: list[dict[str, object]],
    started_at: str,
    started_monotonic: float,
    timeout_seconds: float,
    poll_interval_seconds: float,
    index_before: int,
    target_index: int,
) -> dict[str, object]:
    return {
        "kind": "themescheduler.theme-verification",
        "startedAt": started_at,
        "completedAt": _verification_timestamp(),
        "elapsedSeconds": round(time.monotonic() - started_monotonic, 3),
        "timeoutSeconds": timeout_seconds,
        "pollIntervalSeconds": poll_interval_seconds,
        "expected": expected.as_dict(),
        "indexBefore": index_before,
        "bridgeTargetIndex": target_index,
        "samples": samples,
    }


def _try_restore_original_index(
    backend: ThemeApplyV2Backend,
    *,
    index_before: int,
    before: ThemeVisualState,
    settle_seconds: float,
    visual_state_reader: Callable[[], ThemeVisualState] | None,
    timeout_seconds: float,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    try:
        if (
            backend.set_v2_index(index_before, timeout_seconds=remaining)
            != index_before
        ):
            return False
        _settle_before_deadline(settle_seconds, deadline)
        _, actual = _read_active_visual_state(backend, visual_state_reader)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        current_index, _ = backend.current_v2_indices(timeout_seconds=remaining)
        return (
            current_index == index_before
            and time.monotonic() <= deadline
            and not _visual_state_failures(actual, before)
        )
    except Exception:
        return False


def _try_restore_theme_backup(
    backend: ThemeApplyV2Backend,
    rollback_path: Path,
    *,
    before: ThemeVisualState,
    settle_seconds: float,
    visual_state_reader: Callable[[], ThemeVisualState] | None,
    timeout_seconds: float,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    try:
        _, target_index = backend.apply_theme_v2(
            rollback_path,
            timeout_seconds=remaining,
        )
        _settle_before_deadline(settle_seconds, deadline)
        _, actual = _read_active_visual_state(backend, visual_state_reader)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        current_index, custom_index = backend.current_v2_indices(
            timeout_seconds=remaining
        )
        return (
            current_index in {target_index, custom_index}
            and time.monotonic() <= deadline
            and not _visual_state_failures(actual, before)
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
    visual_state_reader: Callable[[], ThemeVisualState] | None,
    timeout_seconds: float,
) -> bool:
    if _try_restore_original_index(
        backend,
        index_before=index_before,
        before=before,
        settle_seconds=settle_seconds,
        visual_state_reader=visual_state_reader,
        timeout_seconds=timeout_seconds,
    ):
        return True
    return rollback_path is not None and _try_restore_theme_backup(
        backend,
        rollback_path,
        before=before,
        settle_seconds=settle_seconds,
        visual_state_reader=visual_state_reader,
        timeout_seconds=timeout_seconds,
    )


def apply_and_verify_theme_v2(
    managed_path: Path,
    expected: ThemeVisualState,
    before: ThemeVisualState,
    *,
    rollback_path: Path | None = None,
    backend: ThemeApplyV2Backend | None = None,
    settle_seconds: float = 2.0,
    operation_timeout_seconds: float = 35.0,
    rollback_timeout_seconds: float = 35.0,
    verification_timeout_seconds: float = 5.0,
    verification_poll_interval_seconds: float = 0.25,
    visual_state_reader: Callable[[], ThemeVisualState] | None = None,
) -> ThemeApplyV2Result:
    for name, value in (
        ("settle_seconds", settle_seconds),
        ("operation_timeout_seconds", operation_timeout_seconds),
        ("rollback_timeout_seconds", rollback_timeout_seconds),
        ("verification_timeout_seconds", verification_timeout_seconds),
        ("verification_poll_interval_seconds", verification_poll_interval_seconds),
    ):
        if isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be a finite non-negative number.")

    backend = backend or WindowsThemeApplyBackend()
    if operation_timeout_seconds <= 0:
        raise ThemeFileError("Theme apply deadline has expired; no bridge was started.")
    operation_deadline = time.monotonic() + operation_timeout_seconds
    remaining = operation_deadline - time.monotonic()
    if remaining <= 0:
        raise ThemeFileError("Theme apply deadline has expired; no bridge was started.")
    before_index = backend.current_v2_index(timeout_seconds=remaining)
    remaining = operation_deadline - time.monotonic()
    if remaining <= 0:
        raise ThemeFileError("Theme apply deadline expired before ApplyV2.")
    verification_diagnostics: dict[str, object] | None = None
    try:
        bridge_before, target_index = backend.apply_theme_v2(
            managed_path,
            timeout_seconds=remaining,
        )
        _verify_bridge_before(
            index_before=before_index,
            bridge_before=bridge_before,
        )
        _settle_before_deadline(settle_seconds, operation_deadline)
        active_path, actual, current_index, verification_diagnostics = (
            _poll_theme_visual_state(
                backend,
                expected,
                visual_state_reader=visual_state_reader,
                target_index=target_index,
                index_before=before_index,
                timeout_seconds=verification_timeout_seconds,
                poll_interval_seconds=verification_poll_interval_seconds,
                operation_deadline=operation_deadline,
            )
        )
        return ThemeApplyV2Result(
            active_path,
            actual,
            before_index,
            target_index,
            current_index,
            verification_diagnostics,
        )
    except Exception as exc:
        if isinstance(exc, _ThemeVerificationError):
            verification_diagnostics = exc.diagnostics
        rollback_succeeded = _rollback_theme_v2(
            backend,
            index_before=before_index,
            before=before,
            rollback_path=rollback_path,
            settle_seconds=settle_seconds,
            visual_state_reader=visual_state_reader,
            timeout_seconds=rollback_timeout_seconds,
        )
        raise LiveThemeApplyError(
            f"Managed theme V2 apply failed: {exc}",
            rollback_succeeded=rollback_succeeded,
            verification_diagnostics=verification_diagnostics,
        ) from exc
