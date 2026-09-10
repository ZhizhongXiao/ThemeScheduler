"""Windows adapter used by the automatic switching core."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Protocol

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
    read_visual_state,
)
from ..storage import UserDataLayout
from ..theme import ThemeMode


class AutoWindowsBackend(Protocol):
    def probe(self) -> None: ...

    def capture_profile(self, profile: str, timestamp: str) -> AccentProfile: ...

    def apply_profile(
        self,
        profile: AccentProfile,
        apps_theme: ThemeMode,
        transaction_directory: Path,
    ) -> AccentApplyOutcome: ...

    def read_visual_state(self) -> ThemeVisualState: ...

    def rollback(self, transaction_directory: Path) -> bool: ...


class WindowsAutoBackend:
    """Production adapter around the already verified stage-2 theme bridge."""

    def __init__(
        self,
        layout: UserDataLayout,
        *,
        theme_backend: ThemeApplyV2Backend | None = None,
        windows_build: str | None = None,
        settle_seconds: float = 2.0,
    ) -> None:
        if os.name != "nt":
            raise OSError("Automatic Windows theme writes require Windows.")
        self.layout = layout
        self.theme_backend = theme_backend or WindowsThemeApplyBackend()
        self.windows_build = windows_build or str(sys.getwindowsversion().build)
        self.settle_seconds = settle_seconds

    def capture_profile(self, profile: str, timestamp: str) -> AccentProfile:
        return capture_live_profile(
            profile,
            self.windows_build,
            backend=self.theme_backend,
            timestamp=timestamp,
        )

    def probe(self) -> None:
        active = self.theme_backend.current_theme_path()
        read_visual_state(active.read_bytes())
        current, custom = self.theme_backend.current_v2_indices()
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (current, custom)
        ):
            raise OSError("IThemeManager2 capability probe returned invalid indices.")

    def apply_profile(
        self,
        profile: AccentProfile,
        apps_theme: ThemeMode,
        transaction_directory: Path,
    ) -> AccentApplyOutcome:
        return apply_accent_profile(
            profile,
            self.layout,
            apps_theme=apps_theme,
            transaction_directory=transaction_directory,
            backend=self.theme_backend,
            settle_seconds=self.settle_seconds,
        )

    def read_visual_state(self) -> ThemeVisualState:
        path = self.theme_backend.current_theme_path()
        return read_visual_state(path.read_bytes())

    def rollback(self, transaction_directory: Path) -> bool:
        return rollback_accent_transaction(
            transaction_directory,
            backend=self.theme_backend,
            settle_seconds=self.settle_seconds,
        )
