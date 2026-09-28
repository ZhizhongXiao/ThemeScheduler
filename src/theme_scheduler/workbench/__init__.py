"""Application workbench API and Windows composition root."""

from .api import GuiApi
from .contracts import (
    AppearanceResult,
    ConfigSummary,
    CurrentAppearanceReader,
    HealthResult,
    HealthServiceFactory,
    IdentityRepairServiceFactory,
    LockFactory,
    MaintenanceServiceFactory,
    OverviewResult,
    ProfileSummary,
    StateSummary,
    TaskSummary,
    WorkspaceValidationResult,
)
from .factory import create_live_gui_api
from .shell import ShellActions

__all__ = [
    "AppearanceResult",
    "ConfigSummary",
    "CurrentAppearanceReader",
    "GuiApi",
    "HealthResult",
    "HealthServiceFactory",
    "IdentityRepairServiceFactory",
    "LockFactory",
    "MaintenanceServiceFactory",
    "OverviewResult",
    "ProfileSummary",
    "ShellActions",
    "StateSummary",
    "TaskSummary",
    "WorkspaceValidationResult",
    "create_live_gui_api",
]
