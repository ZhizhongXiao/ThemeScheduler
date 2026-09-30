"""Strict, backward-compatible user configuration contract."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import DataError
from .persistence import (
    Migrator,
    atomic_write_json,
    load_json_object,
    migrate_json_file,
)

CONFIG_KIND = "themescheduler.config"
CONFIG_SCHEMA_VERSION = 2
THEME_MODES = frozenset({"light", "dark"})
_TIME_PATTERN = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


class ConfigValidationError(DataError):
    """Raised when configuration is missing, damaged, or unsupported."""


def _exact_keys(payload: Mapping[str, Any], expected: set[str], location: str) -> None:
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ConfigValidationError(
            f"{location} fields do not match schema; "
            f"missing={missing}, unknown={unknown}."
        )


def _time_minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


@dataclass(frozen=True)
class AppConfig:
    day_start: str
    night_start: str
    day_apps_theme: str
    night_apps_theme: str
    notify_errors: bool
    notify_status_changes: bool
    day_system_theme: str | None = None
    night_system_theme: str | None = None
    day_start_taskbar_accent: bool | None = None
    night_start_taskbar_accent: bool | None = None
    day_title_borders_accent: bool | None = None
    night_title_borders_accent: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.day_start, str) or not _TIME_PATTERN.fullmatch(
            self.day_start
        ):
            raise ConfigValidationError("schedule.dayStart must use HH:mm.")
        if not isinstance(self.night_start, str) or not _TIME_PATTERN.fullmatch(
            self.night_start
        ):
            raise ConfigValidationError("schedule.nightStart must use HH:mm.")
        day_minutes = _time_minutes(self.day_start)
        night_minutes = _time_minutes(self.night_start)
        if self.day_start == self.night_start:
            raise ConfigValidationError("Day and night start times must differ.")
        delta = abs(day_minutes - night_minutes)
        if min(delta, 1440 - delta) <= 5:
            raise ConfigValidationError(
                "Day and night start times must be more than five minutes apart."
            )
        if (
            not isinstance(self.day_apps_theme, str)
            or self.day_apps_theme not in THEME_MODES
        ):
            raise ConfigValidationError("profiles.day.appsTheme is unsupported.")
        if (
            not isinstance(self.night_apps_theme, str)
            or self.night_apps_theme not in THEME_MODES
        ):
            raise ConfigValidationError("profiles.night.appsTheme is unsupported.")
        if not isinstance(self.notify_errors, bool):
            raise ConfigValidationError("notifications.errors must be boolean.")
        if not isinstance(self.notify_status_changes, bool):
            raise ConfigValidationError("notifications.statusChanges must be boolean.")
        for profile, value in (
            ("day", self.day_system_theme),
            ("night", self.night_system_theme),
        ):
            if value is not None and value not in THEME_MODES:
                raise ConfigValidationError(
                    f"profiles.{profile}.systemTheme is unsupported."
                )
        for location, value in (
            ("profiles.day.accentSurfaces.startTaskbar", self.day_start_taskbar_accent),
            (
                "profiles.night.accentSurfaces.startTaskbar",
                self.night_start_taskbar_accent,
            ),
            (
                "profiles.day.accentSurfaces.titleBarsAndWindowBorders",
                self.day_title_borders_accent,
            ),
            (
                "profiles.night.accentSurfaces.titleBarsAndWindowBorders",
                self.night_title_borders_accent,
            ),
        ):
            if value is not None and not isinstance(value, bool):
                raise ConfigValidationError(f"{location} must be boolean or null.")

    @classmethod
    def defaults(cls) -> AppConfig:
        return cls(
            "06:15",
            "23:45",
            "light",
            "dark",
            True,
            True,
            day_system_theme="light",
            night_system_theme="dark",
            day_start_taskbar_accent=False,
            night_start_taskbar_accent=True,
            day_title_borders_accent=False,
            night_title_borders_accent=True,
        )

    def profile_appearance(
        self, profile: str
    ) -> tuple[str, str | None, bool | None, bool | None]:
        """Return the complete non-color appearance intent for one period."""

        if profile == "day":
            return (
                self.day_apps_theme,
                self.day_system_theme,
                self.day_start_taskbar_accent,
                self.day_title_borders_accent,
            )
        if profile == "night":
            return (
                self.night_apps_theme,
                self.night_system_theme,
                self.night_start_taskbar_accent,
                self.night_title_borders_accent,
            )
        raise ValueError("Profile must be day or night.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": CONFIG_KIND,
            "schemaVersion": CONFIG_SCHEMA_VERSION,
            "schedule": {
                "dayStart": self.day_start,
                "nightStart": self.night_start,
            },
            "profiles": {
                "day": {
                    "appsTheme": self.day_apps_theme,
                    "systemTheme": self.day_system_theme,
                    "accentSurfaces": {
                        "startTaskbar": self.day_start_taskbar_accent,
                        "titleBarsAndWindowBorders": self.day_title_borders_accent,
                    },
                },
                "night": {
                    "appsTheme": self.night_apps_theme,
                    "systemTheme": self.night_system_theme,
                    "accentSurfaces": {
                        "startTaskbar": self.night_start_taskbar_accent,
                        "titleBarsAndWindowBorders": self.night_title_borders_accent,
                    },
                },
            },
            "notifications": {
                "errors": self.notify_errors,
                "statusChanges": self.notify_status_changes,
            },
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AppConfig:
        _exact_keys(
            payload,
            {"kind", "schemaVersion", "schedule", "profiles", "notifications"},
            "config",
        )
        if payload.get("kind") != CONFIG_KIND:
            raise ConfigValidationError("JSON is not a ThemeScheduler config.")
        schema_version = payload.get("schemaVersion")
        if schema_version not in {1, CONFIG_SCHEMA_VERSION}:
            raise ConfigValidationError("Unsupported config schemaVersion.")
        schedule = payload.get("schedule")
        profiles = payload.get("profiles")
        notifications = payload.get("notifications")
        if not all(
            isinstance(value, Mapping) for value in (schedule, profiles, notifications)
        ):
            raise ConfigValidationError(
                "schedule, profiles, and notifications must be objects."
            )
        assert isinstance(schedule, Mapping)
        assert isinstance(profiles, Mapping)
        assert isinstance(notifications, Mapping)
        _exact_keys(schedule, {"dayStart", "nightStart"}, "config.schedule")
        _exact_keys(profiles, {"day", "night"}, "config.profiles")
        _exact_keys(
            notifications,
            {"errors", "statusChanges"},
            "config.notifications",
        )
        day = profiles.get("day")
        night = profiles.get("night")
        if not isinstance(day, Mapping) or not isinstance(night, Mapping):
            raise ConfigValidationError("Day and night profiles must be objects.")
        if schema_version == 1:
            _exact_keys(day, {"appsTheme"}, "config.profiles.day")
            _exact_keys(night, {"appsTheme"}, "config.profiles.night")
            day_system_theme = None
            night_system_theme = None
            day_start_taskbar_accent = None
            night_start_taskbar_accent = None
            day_title_borders_accent = None
            night_title_borders_accent = None
        else:
            expected_profile = {"appsTheme", "systemTheme", "accentSurfaces"}
            _exact_keys(day, expected_profile, "config.profiles.day")
            _exact_keys(night, expected_profile, "config.profiles.night")
            day_surfaces = day.get("accentSurfaces")
            night_surfaces = night.get("accentSurfaces")
            if not isinstance(day_surfaces, Mapping) or not isinstance(
                night_surfaces, Mapping
            ):
                raise ConfigValidationError(
                    "Day and night accentSurfaces must be objects."
                )
            expected_surfaces = {"startTaskbar", "titleBarsAndWindowBorders"}
            _exact_keys(
                day_surfaces,
                expected_surfaces,
                "config.profiles.day.accentSurfaces",
            )
            _exact_keys(
                night_surfaces,
                expected_surfaces,
                "config.profiles.night.accentSurfaces",
            )
            day_system_theme = day.get("systemTheme")
            night_system_theme = night.get("systemTheme")
            day_start_taskbar_accent = day_surfaces.get("startTaskbar")
            night_start_taskbar_accent = night_surfaces.get("startTaskbar")
            day_title_borders_accent = day_surfaces.get("titleBarsAndWindowBorders")
            night_title_borders_accent = night_surfaces.get("titleBarsAndWindowBorders")
        return cls(
            day_start=schedule.get("dayStart"),  # type: ignore[arg-type]
            night_start=schedule.get("nightStart"),  # type: ignore[arg-type]
            day_apps_theme=day.get("appsTheme"),  # type: ignore[arg-type]
            night_apps_theme=night.get("appsTheme"),  # type: ignore[arg-type]
            notify_errors=notifications.get("errors"),  # type: ignore[arg-type]
            notify_status_changes=notifications.get(  # type: ignore[arg-type]
                "statusChanges"
            ),
            day_system_theme=day_system_theme,
            night_system_theme=night_system_theme,
            day_start_taskbar_accent=day_start_taskbar_accent,
            night_start_taskbar_accent=night_start_taskbar_accent,
            day_title_borders_accent=day_title_borders_accent,
            night_title_borders_accent=night_title_borders_accent,
        )


def load_config(path: Path) -> AppConfig:
    """Load strictly; callers must not substitute defaults on failure."""

    return AppConfig.from_dict(load_json_object(path))


class ConfigStore:
    """Explicit initialization, strict load, safe update, and explicit migration."""

    def __init__(
        self, path: Path, *, migrations: Mapping[int, Migrator] | None = None
    ) -> None:
        self.path = Path(path)
        self.migrations = dict(migrations or {})

    def initialize(self, config: AppConfig | None = None) -> AppConfig:
        value = config or AppConfig.defaults()
        atomic_write_json(self.path, value.as_dict())
        return self.load()

    def load(self) -> AppConfig:
        return load_config(self.path)

    def save(self, config: AppConfig) -> AppConfig:
        self.load()
        atomic_write_json(self.path, config.as_dict(), force=True)
        written = self.load()
        if written != config:
            raise OSError(f"Config readback mismatch: {self.path}")
        return written

    def migrate(self) -> AppConfig:
        payload = migrate_json_file(
            self.path,
            kind=CONFIG_KIND,
            current_version=CONFIG_SCHEMA_VERSION,
            migrations=self.migrations,
            validator=AppConfig.from_dict,
        )
        return AppConfig.from_dict(payload)
