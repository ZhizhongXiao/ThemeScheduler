"""Windows adapter used by the automatic switching core."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, TypeGuard

from ..accent_profile import AccentProfile
from ..accent_service import (
    AccentApplyOutcome,
    apply_accent_profile,
    capture_live_profile,
    rollback_accent_transaction,
)
from ..accent_theme import (
    ThemeApplyV2Backend,
    ThemeVisualState,
    WindowsThemeApplyBackend,
    materialize_theme_visual_state,
)
from ..appearance import (
    AppearanceSettingsBackend,
    ThemeMode,
    WindowsAppearanceSettingsBackend,
)
from ..persistence import load_json_object
from ..storage import UserDataLayout


class AutoWindowsBackend(Protocol):
    def probe(self) -> None: ...

    def capture_profile(self, profile: str, timestamp: str) -> AccentProfile: ...

    def apply_profile(
        self,
        profile: AccentProfile,
        apps_theme: ThemeMode,
        system_theme: ThemeMode | None,
        start_taskbar_accent: bool | None,
        title_borders_accent: bool | None,
        transaction_directory: Path,
    ) -> AccentApplyOutcome: ...

    def read_visual_state(self) -> ThemeVisualState: ...

    def verify_transaction_target(
        self,
        transaction_directory: Path,
        expected: ThemeVisualState,
    ) -> tuple[str, ...]: ...

    def rollback(self, transaction_directory: Path) -> bool: ...


class WindowsAutoBackend:
    """Production adapter around the already verified stage-2 theme bridge."""

    def __init__(
        self,
        layout: UserDataLayout,
        *,
        theme_backend: ThemeApplyV2Backend | None = None,
        appearance_backend: AppearanceSettingsBackend | None = None,
        windows_build: str | None = None,
        settle_seconds: float = 2.0,
    ) -> None:
        if os.name != "nt":
            raise OSError("Automatic Windows theme writes require Windows.")
        self.layout = layout
        self.theme_backend = theme_backend or WindowsThemeApplyBackend()
        self.appearance_backend = (
            appearance_backend or WindowsAppearanceSettingsBackend()
        )
        self.windows_build = windows_build or str(sys.getwindowsversion().build)
        self.settle_seconds = settle_seconds

    def capture_profile(self, profile: str, timestamp: str) -> AccentProfile:
        return capture_live_profile(
            profile,
            self.windows_build,
            backend=self.theme_backend,
            visual_state_reader=self.appearance_backend.read_visual_state,
            timestamp=timestamp,
        )

    def probe(self) -> None:
        active = self.theme_backend.current_theme_path()
        materialize_theme_visual_state(
            active.read_bytes(),
            self.appearance_backend.read_visual_state(),
        )
        current, custom = self.theme_backend.current_v2_indices()
        if not all(_is_valid_theme_index(value) for value in (current, custom)):
            raise OSError("IThemeManager2 capability probe returned invalid indices.")

    def apply_profile(
        self,
        profile: AccentProfile,
        apps_theme: ThemeMode,
        system_theme: ThemeMode | None,
        start_taskbar_accent: bool | None,
        title_borders_accent: bool | None,
        transaction_directory: Path,
    ) -> AccentApplyOutcome:
        return apply_accent_profile(
            profile,
            self.layout,
            apps_theme=apps_theme,
            system_theme=system_theme,
            start_taskbar_accent=start_taskbar_accent,
            title_borders_accent=title_borders_accent,
            transaction_directory=transaction_directory,
            backend=self.theme_backend,
            appearance_backend=self.appearance_backend,
            settle_seconds=self.settle_seconds,
        )

    def read_visual_state(self) -> ThemeVisualState:
        return self.appearance_backend.read_visual_state()

    def verify_transaction_target(
        self,
        transaction_directory: Path,
        expected: ThemeVisualState,
    ) -> tuple[str, ...]:
        failures: list[str] = []
        if self.read_visual_state() != expected:
            failures.append("Current Windows theme does not match windowsTarget.")
        journal = load_json_object(transaction_directory / "journal.json")
        settings_target = journal.get("settingsTarget")
        if settings_target is None:
            return tuple(failures)
        if not _is_string_mapping(settings_target):
            return (*failures, "Appearance settingsTarget is malformed.")
        try:
            apps_theme = _optional_theme_mode(settings_target.get("appsTheme"))
            system_theme = _optional_theme_mode(settings_target.get("systemTheme"))
            start_taskbar = _optional_bool(settings_target.get("startTaskbarAccent"))
            title_borders = _optional_bool(settings_target.get("titleBordersAccent"))
        except (TypeError, ValueError) as exc:
            return (*failures, f"Appearance settingsTarget is invalid: {exc}")
        failures.extend(
            self.appearance_backend.verify(
                apps_theme=apps_theme,
                system_theme=system_theme,
                start_taskbar=start_taskbar,
                title_borders=title_borders,
            )
        )
        return tuple(failures)

    def rollback(self, transaction_directory: Path) -> bool:
        return rollback_accent_transaction(
            transaction_directory,
            backend=self.theme_backend,
            appearance_backend=self.appearance_backend,
            settle_seconds=self.settle_seconds,
            visual_state_reader=self.appearance_backend.read_visual_state,
        )


def _optional_theme_mode(value: object) -> ThemeMode | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("theme mode must be a string or null")
    return ThemeMode(value)


def _is_valid_theme_index(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _is_string_mapping(value: object) -> TypeGuard[Mapping[str, object]]:
    return _is_object_mapping(value) and all(isinstance(key, str) for key in value)


def _optional_bool(value: object) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise TypeError("accent surface value must be a boolean or null")
