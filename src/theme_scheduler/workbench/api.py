"""Single pywebview object composed from focused workbench services."""

from __future__ import annotations

import threading
from pathlib import Path

from ..core import Clock, SystemClock
from ..scheduler import TaskSchedulerBackend
from ..storage import UserDataLayout
from .configuration import GuiConfigurationMixin
from .contracts import (
    CurrentAppearanceReader,
    HealthServiceFactory,
    IdentityRepairServiceFactory,
    LockFactory,
    MaintenanceServiceFactory,
    ManualAppearanceServiceFactory,
    ShellOpener,
)
from .maintenance import GuiMaintenanceMixin
from .overview import GuiOverviewMixin
from .parsing import GuiParsingMixin
from .shell import ShellActions


class GuiApi(
    GuiParsingMixin,
    GuiOverviewMixin,
    GuiConfigurationMixin,
    GuiMaintenanceMixin,
):
    """The only object exposed to JavaScript by pywebview."""

    _CONFIG_FIELDS = frozenset({
        "dayStart",
        "nightStart",
        "dayAppsTheme",
        "nightAppsTheme",
        "daySystemTheme",
        "nightSystemTheme",
        "dayStartTaskbarAccent",
        "nightStartTaskbarAccent",
        "dayTitleBordersAccent",
        "nightTitleBordersAccent",
        "notifyErrors",
        "notifyStatusChanges",
    })
    _WORKSPACE_FIELDS = _CONFIG_FIELDS | frozenset({
        "dayColor",
        "nightColor",
    })

    def __init__(
        self,
        layout: UserDataLayout,
        *,
        executable: Path,
        scheduler_backend: TaskSchedulerBackend,
        lock_factory: LockFactory,
        maintenance_service_factory: MaintenanceServiceFactory | None = None,
        health_service_factory: HealthServiceFactory | None = None,
        identity_repair_service_factory: (IdentityRepairServiceFactory | None) = None,
        manual_appearance_service_factory: (
            ManualAppearanceServiceFactory | None
        ) = None,
        current_appearance_reader: CurrentAppearanceReader | None = None,
        shell_actions: ShellOpener | None = None,
        clock: Clock | None = None,
        allow_live_writes: bool = False,
    ) -> None:
        self._layout = layout
        self._executable = Path(executable).resolve()
        self._scheduler = scheduler_backend
        self._lock_factory = lock_factory
        self._maintenance_service_factory = maintenance_service_factory
        self._health_service_factory = health_service_factory
        self._identity_repair_service_factory = identity_repair_service_factory
        self._manual_appearance_service_factory = manual_appearance_service_factory
        self._current_appearance_reader = current_appearance_reader
        self._shell = shell_actions or ShellActions(
            layout,
            self._executable,
        )
        self._clock = clock or SystemClock()
        self._allow_live_writes = allow_live_writes
        self._api_lock = threading.RLock()
