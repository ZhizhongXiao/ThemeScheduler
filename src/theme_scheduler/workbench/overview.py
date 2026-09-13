"""Read-only workbench summaries and Windows appearance drafts."""

from __future__ import annotations

import json
from typing import Any

from ..accent_profile import AccentProfileStore, RgbColor
from ..appearance import CurrentWindowsAppearance
from ..backup import InstallBackupStore
from ..config import AppConfig, ConfigStore
from ..core import target_profile_at
from ..initial_setup import initial_setup_pending
from ..log_policy import LogEvent
from ..scheduler import build_task_spec, inspect_task
from ..state import StateStore
from .contracts import (
    AppearanceResult,
    BackupSummary,
    OverviewResult,
    OverviewSections,
    ProfileSummary,
    RecentLogSummary,
    StateSummary,
    TaskSummary,
    WorkbenchBindings,
)


class GuiOverviewMixin(WorkbenchBindings):
    def _recent_log_summary(self, limit: int = 12) -> RecentLogSummary:
        path = self._layout.event_log
        if not path.exists():
            return {
                "available": True,
                "events": [],
                "message": "No structured events have been recorded.",
            }
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            events = [
                LogEvent.from_dict(json.loads(line)).as_dict()
                for line in lines[-limit:]
                if line.strip()
            ]
            return {
                "available": True,
                "events": events,
                "message": f"Loaded {len(events)} recent structured events.",
            }
        except Exception as exc:
            return {
                "available": False,
                "events": [],
                "message": f"{type(exc).__name__}: {exc}",
            }

    def _build_profiles_summary(
        self,
    ) -> dict[str, ProfileSummary]:
        profiles: dict[str, ProfileSummary] = {}
        for name in ("day", "night"):
            path = self._layout.profile_path(name)
            try:
                profile = AccentProfileStore(path, name).load()
                profiles[name] = ProfileSummary(
                    valid=True,
                    capturedAt=profile.captured_at,
                    colorizationColor=(f"0X{profile.colorization_color:08X}"),
                    color=dict(
                        RgbColor.from_colorization_color(
                            profile.colorization_color
                        ).as_dict()
                    ),
                    raw=dict(profile.as_dict()),
                )
            except Exception as exc:
                profiles[name] = ProfileSummary(
                    valid=False,
                    message=f"{type(exc).__name__}: {exc}",
                )
        return profiles

    def _build_backup_summary(
        self,
    ) -> tuple[BackupSummary, str | None]:
        manifest_exists = self._layout.install_backup_manifest.exists()
        theme_exists = self._layout.install_backup_theme.exists()
        if not manifest_exists and not theme_exists:
            return (
                BackupSummary(
                    status="absent",
                    valid=False,
                    restorable=False,
                    message=("No first-install appearance backup is present."),
                ),
                None,
            )
        if manifest_exists != theme_exists:
            warning = "First-install appearance backup is incomplete."
            return (
                BackupSummary(
                    status="invalid",
                    valid=False,
                    restorable=False,
                    message=warning,
                ),
                warning,
            )
        return self._load_verified_backup_summary()

    def _load_verified_backup_summary(
        self,
    ) -> tuple[BackupSummary, str | None]:
        try:
            backup = InstallBackupStore(
                self._layout.install_backup_manifest,
                self._layout.install_backup_theme,
            ).load_verified()
            return (
                BackupSummary(
                    status="valid",
                    valid=True,
                    restorable=True,
                    capturedAt=backup.captured_at,
                    appMode=backup.app_mode,
                    colorizationColor=backup.colorization_color,
                    message=("First-install appearance backup is valid."),
                ),
                None,
            )
        except Exception as exc:
            warning = (
                "First-install appearance backup is invalid: "
                f"{type(exc).__name__}: {exc}"
            )
            return (
                BackupSummary(
                    status="invalid",
                    valid=False,
                    restorable=False,
                    message=warning,
                ),
                warning,
            )

    def _available_task_summary(
        self,
        config: AppConfig,
    ) -> TaskSummary:
        user_id = self._scheduler.current_user_id()
        desired = build_task_spec(
            config,
            executable=str(self._executable),
            user_id=user_id,
        )
        inspection = inspect_task(
            desired,
            self._scheduler.read(desired.task_path),
        )
        return TaskSummary(
            available=True,
            exists=inspection.exists,
            valid=inspection.valid,
            differences=[
                dict(difference.as_dict()) for difference in inspection.differences
            ],
            actual=(
                dict(inspection.actual.as_dict())
                if inspection.actual is not None
                else None
            ),
            desired=dict(desired.as_dict()),
        )

    def _build_task_summary(
        self,
        config: AppConfig,
    ) -> tuple[TaskSummary, str | None]:
        try:
            return self._available_task_summary(config), None
        except Exception as exc:
            warning = (
                f"Task Scheduler status is unavailable: {type(exc).__name__}: {exc}"
            )
            return (
                TaskSummary(
                    available=False,
                    exists=None,
                    valid=False,
                    differences=[],
                    actual=None,
                    desired=None,
                    message=warning,
                ),
                warning,
            )

    def _build_overview_sections(
        self,
        config: AppConfig,
    ) -> OverviewSections:
        backup, backup_warning = self._build_backup_summary()
        task, task_warning = self._build_task_summary(config)
        warnings = tuple(
            warning for warning in (backup_warning, task_warning) if warning is not None
        )
        return OverviewSections(
            self._build_profiles_summary(),
            backup,
            task,
            warnings,
        )

    def get_overview(self) -> OverviewResult | dict[str, Any]:
        with self._api_lock:
            try:
                config = ConfigStore(self._layout.config).load()
                state = StateStore(self._layout.state).load()
                now = self._clock.now()
                sections = self._build_overview_sections(config)
                warnings = list(sections.warnings)
                return OverviewResult(
                    action="overview",
                    result="partial" if warnings else "success",
                    message=(warnings[0] if warnings else "Status is current."),
                    warnings=warnings,
                    now=now.isoformat(timespec="seconds"),
                    targetProfile=target_profile_at(config, now),
                    config=self._gui_config(config),
                    state=StateSummary(**state.as_dict()),
                    initialSetupPending=initial_setup_pending(
                        self._layout.initial_setup_marker
                    ),
                    profiles=sections.profiles,
                    installBackup=sections.install_backup,
                    task=sections.task,
                    recentLog=self._recent_log_summary(),
                    dataRoot=str(self._layout.root.resolve()),
                    executable=str(self._executable),
                    liveWritesEnabled=self._allow_live_writes,
                    currentAppearanceReadEnabled=(
                        self._current_appearance_reader is not None
                    ),
                    dataChanged=False,
                    windowsChanged=False,
                    taskSchedulerChanged=False,
                )
            except Exception as exc:
                return self._error("overview", exc)

    def read_current_windows_appearance(self) -> AppearanceResult:
        """Read the active Windows appearance into a page draft only."""

        with self._api_lock:
            if self._current_appearance_reader is None:
                return AppearanceResult(
                    action="read-current-windows-appearance",
                    result="blocked",
                    message=(
                        "Current Windows appearance reading is unavailable "
                        "in the isolated safety preview."
                    ),
                    dataChanged=False,
                    windowsChanged=False,
                    taskSchedulerChanged=False,
                )
            try:
                appearance = self._current_appearance_reader()
                if not isinstance(appearance, CurrentWindowsAppearance):
                    raise TypeError(
                        "Current appearance reader returned an invalid value."
                    )
                visual = appearance.visual
                color = RgbColor.from_colorization_color(visual.colorization_color)
                return AppearanceResult(
                    action="read-current-windows-appearance",
                    result="success",
                    message=(
                        "The complete current Windows appearance was read "
                        "into the page draft; nothing was saved."
                    ),
                    appMode=visual.app_mode.casefold(),
                    systemMode=visual.system_mode.casefold(),
                    startTaskbarAccent=appearance.start_taskbar_accent,
                    titleBordersAccent=appearance.title_borders_accent,
                    autoColorization=(visual.auto_colorization == "1"),
                    colorizationColor=(f"0X{visual.colorization_color:08X}"),
                    color=color.as_dict(),
                    accentSource=appearance.accent_source,
                    sourcesDiverged=appearance.sources_diverged,
                    divergences=list(appearance.divergences),
                    themeAppearance=(
                        appearance.theme_visual.as_dict()
                        if appearance.theme_visual is not None
                        else None
                    ),
                    dataChanged=False,
                    windowsChanged=False,
                    taskSchedulerChanged=False,
                )
            except Exception as exc:
                return AppearanceResult(
                    **self._error("read-current-windows-appearance", exc),
                )
