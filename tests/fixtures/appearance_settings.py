from __future__ import annotations

from theme_scheduler.accent_theme import read_visual_state
from theme_scheduler.appearance import (
    AppearanceRegistrySnapshot,
    RegistryValue,
    ThemeMode,
)


class ScriptedAppearanceSettings:
    """Appearance-registry double coupled to a scripted theme backend."""

    def __init__(
        self,
        theme_backend: object,
        *,
        start_taskbar: bool = True,
        title_borders: bool = True,
        fail_verify: bool = False,
        fail_write_after_start: bool = False,
        fail_restore: bool = False,
    ) -> None:
        self.theme_backend = theme_backend
        self.start_taskbar = start_taskbar
        self.title_borders = title_borders
        self.fail_verify = fail_verify
        self.fail_write_after_start = fail_write_after_start
        self.fail_restore = fail_restore
        self.write_calls = 0
        self.restore_calls = 0

    def _visual(self):
        path = self.theme_backend.current_theme_path()  # type: ignore[attr-defined]
        return read_visual_state(path.read_bytes())

    def capture(self) -> AppearanceRegistrySnapshot:
        visual = self._visual()
        return AppearanceRegistrySnapshot(
            apps_theme=RegistryValue(True, int(visual.app_mode == "Light"), 4),
            system_theme=RegistryValue(True, int(visual.system_mode == "Light"), 4),
            start_taskbar_accent=RegistryValue(
                True,
                int(self.start_taskbar),
                4,
            ),
            title_borders_accent=RegistryValue(
                True,
                int(self.title_borders),
                4,
            ),
        )

    def write_accent_surfaces(
        self,
        *,
        start_taskbar: bool | None,
        title_borders: bool | None,
    ) -> None:
        self.write_calls += 1
        if start_taskbar is not None:
            self.start_taskbar = start_taskbar
        if self.fail_write_after_start:
            raise OSError("injected second appearance write failure")
        if title_borders is not None:
            self.title_borders = title_borders

    def verify(
        self,
        *,
        apps_theme: ThemeMode | None,
        system_theme: ThemeMode | None,
        start_taskbar: bool | None,
        title_borders: bool | None,
    ) -> tuple[str, ...]:
        if self.fail_verify:
            return ("injected appearance registry mismatch",)
        visual = self._visual()
        checks = (
            apps_theme is None or visual.app_mode == apps_theme.value.title(),
            system_theme is None or visual.system_mode == system_theme.value.title(),
            start_taskbar is None or self.start_taskbar is start_taskbar,
            title_borders is None or self.title_borders is title_borders,
        )
        return () if all(checks) else ("appearance target mismatch",)

    def restore(self, snapshot: AppearanceRegistrySnapshot) -> None:
        self.restore_calls += 1
        if self.fail_restore:
            raise OSError("injected appearance rollback failure")
        self.start_taskbar = bool(snapshot.start_taskbar_accent.data)
        self.title_borders = bool(snapshot.title_borders_accent.data)
        if self.capture() != snapshot:
            raise OSError("scripted appearance rollback mismatch")
