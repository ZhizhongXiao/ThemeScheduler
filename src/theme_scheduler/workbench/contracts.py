"""Typed Python and JSON boundaries for the pywebview workbench."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import (
    Any,
    ClassVar,
    NotRequired,
    Protocol,
    Required,
    TypedDict,
)

from ..accent_profile import RgbColor
from ..appearance import CurrentWindowsAppearance
from ..config import AppConfig
from ..core import Clock, ExecutionLock
from ..scheduler import TaskSchedulerBackend
from ..storage import UserDataLayout


class StructuredOutcome(Protocol):
    def as_dict(self) -> dict[str, object]: ...


class MaintenanceServiceLike(Protocol):
    def restore_install_appearance(self) -> StructuredOutcome: ...


class HealthReportLike(Protocol):
    def as_dict(self) -> HealthReportData: ...


class HealthServiceLike(Protocol):
    def inspect(self) -> HealthReportLike: ...


class IdentityRepairServiceLike(Protocol):
    def repair(self) -> StructuredOutcome: ...


class ManualAppearanceServiceLike(Protocol):
    def apply_current(self) -> StructuredOutcome: ...


class LockFactory(Protocol):
    def __call__(self) -> ExecutionLock: ...


class MaintenanceServiceFactory(Protocol):
    def __call__(self) -> MaintenanceServiceLike: ...


class HealthServiceFactory(Protocol):
    def __call__(self) -> HealthServiceLike: ...


class IdentityRepairServiceFactory(Protocol):
    def __call__(self) -> IdentityRepairServiceLike: ...


class ManualAppearanceServiceFactory(Protocol):
    def __call__(self) -> ManualAppearanceServiceLike: ...


class CurrentAppearanceReader(Protocol):
    def __call__(self) -> CurrentWindowsAppearance: ...


class ShellOpener(Protocol):
    def open(self, target: str) -> None: ...


class ConfigSummary(TypedDict):
    dayStart: str
    nightStart: str
    dayAppsTheme: str
    nightAppsTheme: str
    daySystemTheme: str | None
    nightSystemTheme: str | None
    dayStartTaskbarAccent: bool | None
    nightStartTaskbarAccent: bool | None
    dayTitleBordersAccent: bool | None
    nightTitleBordersAccent: bool | None
    notifyErrors: bool
    notifyStatusChanges: bool


class StateSummary(TypedDict):
    kind: str
    schemaVersion: int
    paused: bool
    activeProfile: str | None
    lastRunAt: str | None
    lastAppliedProfile: str | None
    lastResult: str


class ProfileSummary(TypedDict, total=False):
    valid: Required[bool]
    capturedAt: str
    colorizationColor: str
    color: dict[str, object]
    raw: dict[str, object]
    message: str


class BackupSummary(TypedDict, total=False):
    status: Required[str]
    valid: Required[bool]
    restorable: Required[bool]
    message: Required[str]
    capturedAt: str
    appMode: str
    colorizationColor: str


class TaskSummary(TypedDict):
    available: bool
    exists: bool | None
    valid: bool
    differences: list[dict[str, object]]
    actual: dict[str, object] | None
    desired: dict[str, object] | None
    message: NotRequired[str]


class RecentLogSummary(TypedDict):
    available: bool
    events: list[dict[str, object]]
    message: str


class OperationResult(TypedDict, total=False):
    action: Required[str]
    result: Required[str]
    message: Required[str]
    dataChanged: Required[bool]
    windowsChanged: Required[bool]
    taskSchedulerChanged: Required[bool]


class WorkspaceValidationResult(OperationResult, total=False):
    valid: Required[bool]
    config: ConfigSummary
    colors: dict[str, dict[str, int | str]]


class HealthCheckSummary(TypedDict):
    id: str
    category: str
    status: str
    message: str
    repairAction: str | None


class HealthReportData(TypedDict):
    kind: str
    schemaVersion: int
    capturedAt: str
    status: str
    summary: dict[str, int]
    checks: list[HealthCheckSummary]


class HealthResult(OperationResult, total=False):
    kind: str
    schemaVersion: int
    capturedAt: str
    status: str
    summary: dict[str, int]
    checks: list[HealthCheckSummary]


class AppearanceResult(OperationResult, total=False):
    appMode: str
    systemMode: str
    startTaskbarAccent: bool
    titleBordersAccent: bool
    autoColorization: bool
    colorizationColor: str
    color: dict[str, int | str]
    accentSource: str
    sourcesDiverged: bool
    divergences: list[str]
    themeAppearance: dict[str, str | int] | None


class OverviewResult(OperationResult):
    warnings: list[str]
    now: str
    targetProfile: str
    config: ConfigSummary
    state: StateSummary
    initialSetupPending: bool
    profiles: dict[str, ProfileSummary]
    installBackup: BackupSummary
    task: TaskSummary
    recentLog: RecentLogSummary
    dataRoot: str
    executable: str
    liveWritesEnabled: bool
    currentAppearanceReadEnabled: bool


@dataclass(frozen=True)
class OverviewSections:
    profiles: dict[str, ProfileSummary]
    install_backup: BackupSummary
    task: TaskSummary
    warnings: tuple[str, ...]


class WorkbenchBindings:
    """Attribute contract shared by implementation mixins."""

    _layout: UserDataLayout
    _executable: Path
    _scheduler: TaskSchedulerBackend
    _lock_factory: LockFactory
    _maintenance_service_factory: MaintenanceServiceFactory | None
    _health_service_factory: HealthServiceFactory | None
    _identity_repair_service_factory: IdentityRepairServiceFactory | None
    _manual_appearance_service_factory: ManualAppearanceServiceFactory | None
    _current_appearance_reader: CurrentAppearanceReader | None
    _shell: ShellOpener
    _clock: Clock
    _allow_live_writes: bool
    _api_lock: RLock
    _CONFIG_FIELDS: ClassVar[frozenset[str]]
    _WORKSPACE_FIELDS: ClassVar[frozenset[str]]

    @staticmethod
    def _error(action: str, exc: Exception) -> dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def _gui_config(config: AppConfig) -> ConfigSummary:
        raise NotImplementedError

    @classmethod
    def _parse_config(
        cls,
        payload: Mapping[str, object],
    ) -> AppConfig:
        raise NotImplementedError

    @classmethod
    def _parse_workspace(
        cls,
        payload: Mapping[str, object],
    ) -> tuple[AppConfig, dict[str, RgbColor]]:
        raise NotImplementedError
