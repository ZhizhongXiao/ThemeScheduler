"""Strict JavaScript payload parsing for the workbench API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..accent_profile import RgbColor
from ..config import AppConfig
from .contracts import ConfigSummary, WorkbenchBindings


class GuiParsingMixin(WorkbenchBindings):
    @staticmethod
    def _error(action: str, exc: Exception) -> dict[str, Any]:
        return {
            "action": action,
            "result": "failed",
            "message": f"{type(exc).__name__}: {exc}",
            "dataChanged": False,
            "windowsChanged": False,
            "taskSchedulerChanged": False,
        }

    @classmethod
    def _parse_config(cls, payload: Mapping[str, Any]) -> AppConfig:
        if not isinstance(payload, Mapping):
            raise ValueError("Configuration must be an object.")
        actual = set(payload)
        if actual != cls._CONFIG_FIELDS:
            raise ValueError(
                "Configuration fields do not match the GUI contract; "
                f"missing={sorted(cls._CONFIG_FIELDS - actual)}, "
                f"unknown={sorted(actual - cls._CONFIG_FIELDS)}."
            )
        return AppConfig(
            day_start=payload.get("dayStart"),  # type: ignore[arg-type]
            night_start=payload.get("nightStart"),  # type: ignore[arg-type]
            day_apps_theme=payload.get("dayAppsTheme"),  # type: ignore[arg-type]
            night_apps_theme=payload.get("nightAppsTheme"),  # type: ignore[arg-type]
            notify_errors=payload.get("notifyErrors"),  # type: ignore[arg-type]
            notify_status_changes=payload.get(  # type: ignore[arg-type]
                "notifyStatusChanges"
            ),
        )

    @staticmethod
    def _parse_color(
        value: Any,
        location: str,
    ) -> RgbColor:
        if not isinstance(value, Mapping):
            raise ValueError(f"{location} must be an object.")
        expected = {"hex", "red", "green", "blue"}
        if set(value) != expected:
            raise ValueError(
                f"{location} fields do not match schema; "
                f"missing={sorted(expected - set(value))}, "
                f"unknown={sorted(set(value) - expected)}."
            )
        from_hex = RgbColor.from_hex(value.get("hex"))  # type: ignore[arg-type]
        from_channels = RgbColor(
            red=value.get("red"),  # type: ignore[arg-type]
            green=value.get("green"),  # type: ignore[arg-type]
            blue=value.get("blue"),  # type: ignore[arg-type]
        )
        if from_hex != from_channels:
            raise ValueError(f"{location} hexadecimal and decimal RGB values differ.")
        return from_hex

    @classmethod
    def _parse_workspace(
        cls,
        payload: Mapping[str, Any],
    ) -> tuple[AppConfig, dict[str, RgbColor]]:
        if not isinstance(payload, Mapping):
            raise TypeError("Workspace payload must be an object.")
        if set(payload) != cls._WORKSPACE_FIELDS:
            missing = sorted(cls._WORKSPACE_FIELDS - set(payload))
            unknown = sorted(set(payload) - cls._WORKSPACE_FIELDS)
            raise ValueError(
                "Workspace fields do not match schema; "
                f"missing={missing}, unknown={unknown}."
            )
        config = cls._parse_config({name: payload[name] for name in cls._CONFIG_FIELDS})
        colors = {
            "day": cls._parse_color(
                payload["dayColor"],
                "dayColor",
            ),
            "night": cls._parse_color(
                payload["nightColor"],
                "nightColor",
            ),
        }
        return config, colors

    @staticmethod
    def _gui_config(config: AppConfig) -> ConfigSummary:
        return ConfigSummary(
            dayStart=config.day_start,
            nightStart=config.night_start,
            dayAppsTheme=config.day_apps_theme,
            nightAppsTheme=config.night_apps_theme,
            notifyErrors=config.notify_errors,
            notifyStatusChanges=config.notify_status_changes,
        )
