"""Immutable version-1 installation backup manifest contract."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .accent_theme import (
    ThemeApplyV2Backend,
    WindowsThemeApplyBackend,
    normalize_theme_visual_state,
    read_visual_state,
)
from .appearance import (
    APPS_THEME_VALUE,
    REG_DWORD,
    AppearanceRegistrySnapshot,
    AppearanceSettingsBackend,
    AppsThemeReader,
    RegistryValue,
    WindowsAppearanceReader,
    WindowsAppearanceSettingsBackend,
)
from .errors import DataError
from .persistence import (
    atomic_write_bytes,
    atomic_write_json,
    captured_at,
    load_json_object,
)

INSTALL_BACKUP_KIND = "themescheduler.install-backup"
INSTALL_BACKUP_SCHEMA_VERSION = 2
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_COLOR_PATTERN = re.compile(r"0X[0-9A-F]{8}")


class InstallBackupValidationError(DataError):
    """Raised when the first-install recovery manifest is not trustworthy."""


def _require_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise InstallBackupValidationError("capturedAt must be an ISO 8601 string.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise InstallBackupValidationError("capturedAt is invalid.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InstallBackupValidationError("capturedAt must include a UTC offset.")
    return value


def _exact(payload: Mapping[str, Any], expected: set[str], location: str) -> None:
    if set(payload) != expected:
        raise InstallBackupValidationError(
            f"{location} fields do not match the frozen schema."
        )


@dataclass(frozen=True)
class InstallBackup:
    captured_at: str
    created_by_version: str
    windows_build: str
    apps_value_exists: bool
    apps_value_type_code: int | None
    apps_value_data: int | None
    source_theme_path: str
    theme_sha256: str
    auto_colorization: bool
    colorization_color: str
    app_mode: str
    system_mode: str
    appearance_registry: AppearanceRegistrySnapshot | None = None

    def __post_init__(self) -> None:
        _require_timestamp(self.captured_at)
        if not isinstance(self.created_by_version, str) or not self.created_by_version:
            raise InstallBackupValidationError(
                "createdByVersion must be a non-empty string."
            )
        if not isinstance(self.windows_build, str) or not self.windows_build:
            raise InstallBackupValidationError(
                "environment.windowsBuild must be a non-empty string."
            )
        if not isinstance(self.apps_value_exists, bool):
            raise InstallBackupValidationError(
                "appsUseLightTheme.exists must be boolean."
            )
        if self.apps_value_exists:
            if (
                self.apps_value_type_code != 4
                or isinstance(self.apps_value_data, bool)
                or not isinstance(self.apps_value_data, int)
                or self.apps_value_data not in {0, 1}
            ):
                raise InstallBackupValidationError(
                    "Existing AppsUseLightTheme must be REG_DWORD 0 or 1."
                )
        elif self.apps_value_type_code is not None or self.apps_value_data is not None:
            raise InstallBackupValidationError(
                "Missing AppsUseLightTheme requires null typeCode and data."
            )
        if not isinstance(self.source_theme_path, str) or not self.source_theme_path:
            raise InstallBackupValidationError(
                "activeTheme.sourcePath must be a non-empty string."
            )
        if not isinstance(self.theme_sha256, str) or not _SHA256_PATTERN.fullmatch(
            self.theme_sha256
        ):
            raise InstallBackupValidationError(
                "activeTheme.sha256 must be lowercase SHA-256."
            )
        if not isinstance(self.auto_colorization, bool):
            raise InstallBackupValidationError(
                "activeTheme.autoColorization must be boolean."
            )
        if not isinstance(self.colorization_color, str) or not _COLOR_PATTERN.fullmatch(
            self.colorization_color
        ):
            raise InstallBackupValidationError(
                "activeTheme.colorizationColor must be 0XAARRGGBB."
            )
        if not isinstance(self.app_mode, str) or self.app_mode not in {
            "Light",
            "Dark",
        }:
            raise InstallBackupValidationError("activeTheme.appMode is unsupported.")
        if not isinstance(self.system_mode, str) or self.system_mode not in {
            "Light",
            "Dark",
        }:
            raise InstallBackupValidationError("activeTheme.systemMode is unsupported.")
        if self.apps_value_exists:
            expected_mode = "Light" if self.apps_value_data == 1 else "Dark"
            if self.app_mode != expected_mode:
                raise InstallBackupValidationError(
                    "AppsUseLightTheme data does not match activeTheme.appMode."
                )
        if self.appearance_registry is not None:
            if not isinstance(
                self.appearance_registry,
                AppearanceRegistrySnapshot,
            ):
                raise InstallBackupValidationError(
                    "appearanceRegistry must be an exact registry snapshot."
                )
            if self.appearance_registry.apps_theme != RegistryValue(
                self.apps_value_exists,
                self.apps_value_data,
                self.apps_value_type_code,
            ):
                raise InstallBackupValidationError(
                    "appearanceRegistry AppsUseLightTheme does not match legacy binding."
                )
            system_value = self.appearance_registry.system_theme
            if system_value.exists:
                expected_system_mode = "Light" if system_value.data == 1 else "Dark"
                if self.system_mode != expected_system_mode:
                    raise InstallBackupValidationError(
                        "appearanceRegistry SystemUsesLightTheme does not match "
                        "activeTheme.systemMode."
                    )

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "kind": INSTALL_BACKUP_KIND,
            "schemaVersion": (
                INSTALL_BACKUP_SCHEMA_VERSION
                if self.appearance_registry is not None
                else 1
            ),
            "capturedAt": self.captured_at,
            "createdByVersion": self.created_by_version,
            "environment": {"windowsBuild": self.windows_build},
            "appsUseLightTheme": {
                "exists": self.apps_value_exists,
                "typeCode": self.apps_value_type_code,
                "data": self.apps_value_data,
            },
            "activeTheme": {
                "sourcePath": self.source_theme_path,
                "backupPath": "install.theme",
                "sha256": self.theme_sha256,
                "autoColorization": self.auto_colorization,
                "colorizationColor": self.colorization_color,
                "appMode": self.app_mode,
                "systemMode": self.system_mode,
            },
        }
        if self.appearance_registry is not None:
            payload["appearanceRegistry"] = self.appearance_registry.as_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> InstallBackup:
        schema_version = payload.get("schemaVersion")
        if schema_version not in {1, INSTALL_BACKUP_SCHEMA_VERSION}:
            raise InstallBackupValidationError(
                "Unsupported install backup schemaVersion."
            )
        expected = {
            "kind",
            "schemaVersion",
            "capturedAt",
            "createdByVersion",
            "environment",
            "appsUseLightTheme",
            "activeTheme",
        }
        if schema_version == INSTALL_BACKUP_SCHEMA_VERSION:
            expected.add("appearanceRegistry")
        _exact(payload, expected, "install backup")
        if payload.get("kind") != INSTALL_BACKUP_KIND:
            raise InstallBackupValidationError(
                "JSON is not a ThemeScheduler install backup."
            )
        environment = payload.get("environment")
        apps = payload.get("appsUseLightTheme")
        theme = payload.get("activeTheme")
        if not all(isinstance(value, Mapping) for value in (environment, apps, theme)):
            raise InstallBackupValidationError(
                "Backup environment, app value, and active theme must be objects."
            )
        assert isinstance(environment, Mapping)
        assert isinstance(apps, Mapping)
        assert isinstance(theme, Mapping)
        _exact(environment, {"windowsBuild"}, "backup.environment")
        _exact(apps, {"exists", "typeCode", "data"}, "backup.appsUseLightTheme")
        _exact(
            theme,
            {
                "sourcePath",
                "backupPath",
                "sha256",
                "autoColorization",
                "colorizationColor",
                "appMode",
                "systemMode",
            },
            "backup.activeTheme",
        )
        if theme.get("backupPath") != "install.theme":
            raise InstallBackupValidationError(
                "activeTheme.backupPath must be install.theme."
            )
        appearance_registry: AppearanceRegistrySnapshot | None = None
        if schema_version == INSTALL_BACKUP_SCHEMA_VERSION:
            appearance = payload.get("appearanceRegistry")
            if not isinstance(appearance, Mapping):
                raise InstallBackupValidationError(
                    "appearanceRegistry must be an object."
                )
            try:
                appearance_registry = AppearanceRegistrySnapshot.from_dict(appearance)
            except (TypeError, ValueError) as exc:
                raise InstallBackupValidationError(
                    f"appearanceRegistry is invalid: {exc}"
                ) from exc
        return cls(
            captured_at=payload.get("capturedAt"),  # type: ignore[arg-type]
            created_by_version=payload.get("createdByVersion"),  # type: ignore[arg-type]
            windows_build=environment.get("windowsBuild"),  # type: ignore[arg-type]
            apps_value_exists=apps.get("exists"),  # type: ignore[arg-type]
            apps_value_type_code=apps.get("typeCode"),
            apps_value_data=apps.get("data"),
            source_theme_path=theme.get("sourcePath"),  # type: ignore[arg-type]
            theme_sha256=theme.get("sha256"),  # type: ignore[arg-type]
            auto_colorization=theme.get("autoColorization"),  # type: ignore[arg-type]
            colorization_color=theme.get("colorizationColor"),  # type: ignore[arg-type]
            app_mode=theme.get("appMode"),  # type: ignore[arg-type]
            system_mode=theme.get("systemMode"),  # type: ignore[arg-type]
            appearance_registry=appearance_registry,
        )


def load_install_backup(path: Path) -> InstallBackup:
    """Load strictly; callers must never replace a damaged install backup."""

    return InstallBackup.from_dict(load_json_object(path))


class InstallBackupStore:
    """Create once, resume only an identical orphan theme, and always verify."""

    def __init__(self, manifest_path: Path, theme_path: Path) -> None:
        self.manifest_path = Path(manifest_path)
        self.theme_path = Path(theme_path)
        if self.manifest_path.name != "install.json":
            raise ValueError("Install backup manifest must be named install.json.")
        if self.theme_path.name != "install.theme":
            raise ValueError("Install backup theme must be named install.theme.")
        if self.manifest_path.parent.resolve() != self.theme_path.parent.resolve():
            raise ValueError("Install backup manifest and theme must be siblings.")

    @staticmethod
    def _sha256(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _verify_theme_state(manifest: InstallBackup, content: bytes) -> None:
        state = read_visual_state(content)
        if (
            (state.auto_colorization == "1") != manifest.auto_colorization
            or f"0X{state.colorization_color:08X}" != manifest.colorization_color
            or state.app_mode != manifest.app_mode
            or state.system_mode != manifest.system_mode
        ):
            raise InstallBackupValidationError(
                "install.theme visual state does not match install.json."
            )

    def create(self, manifest: InstallBackup, theme_content: bytes) -> InstallBackup:
        if self.manifest_path.exists():
            raise FileExistsError(
                f"Refusing to overwrite install backup: {self.manifest_path}"
            )
        actual_hash = self._sha256(theme_content)
        if actual_hash != manifest.theme_sha256:
            raise InstallBackupValidationError(
                "install.theme content does not match manifest SHA-256."
            )
        self._verify_theme_state(manifest, theme_content)

        created_theme = False
        if self.theme_path.exists():
            existing = self.theme_path.read_bytes()
            if existing != theme_content:
                raise FileExistsError(
                    "An unmatched orphan install.theme already exists."
                )
        else:
            atomic_write_bytes(self.theme_path, theme_content)
            created_theme = True
        try:
            atomic_write_json(self.manifest_path, manifest.as_dict())
        except Exception:
            if created_theme and not self.manifest_path.exists():
                with suppress(OSError):
                    self.theme_path.unlink(missing_ok=True)
            raise
        return self.load_verified()

    def load_verified(self) -> InstallBackup:
        manifest = load_install_backup(self.manifest_path)
        content = self.theme_path.read_bytes()
        if self._sha256(content) != manifest.theme_sha256:
            raise InstallBackupValidationError(
                "install.theme SHA-256 does not match install.json."
            )
        self._verify_theme_state(manifest, content)
        return manifest


def capture_install_backup(
    store: InstallBackupStore,
    *,
    created_by_version: str,
    windows_build: str,
    theme_backend: ThemeApplyV2Backend | None = None,
    app_backend: AppsThemeReader | None = None,
    appearance_backend: AppearanceSettingsBackend | None = None,
    colorization_reader: Callable[[], int] | None = None,
    timestamp: str | None = None,
) -> InstallBackup:
    """Create the immutable first-install backup from current Windows state."""

    active_theme = theme_backend or WindowsThemeApplyBackend()
    settings = (
        appearance_backend
        if appearance_backend is not None
        else (None if app_backend is not None else WindowsAppearanceSettingsBackend())
    )
    registry = app_backend or settings or WindowsAppearanceReader()
    source_path = active_theme.current_theme_path()
    if not source_path.is_absolute() or not source_path.is_file():
        raise InstallBackupValidationError(
            "Current theme does not identify an existing absolute file."
        )
    content = source_path.read_bytes()
    visual = read_visual_state(content)
    appearance_registry = settings.capture() if settings is not None else None
    apps = (
        appearance_registry.apps_theme
        if appearance_registry is not None
        else registry.read_value(APPS_THEME_VALUE)  # type: ignore[attr-defined]
    )
    read_colorization = colorization_reader or (
        WindowsAppearanceReader().read_colorization_color
    )
    colorization_color = read_colorization()
    source_after = active_theme.current_theme_path()
    appearance_after = settings.capture() if settings is not None else None
    apps_after = (
        appearance_after.apps_theme
        if appearance_after is not None
        else registry.read_value(APPS_THEME_VALUE)  # type: ignore[attr-defined]
    )
    colorization_after = read_colorization()
    if (
        source_after.resolve(strict=False) != source_path.resolve(strict=False)
        or source_after.read_bytes() != content
        or apps_after != apps
        or appearance_after != appearance_registry
        or colorization_after != colorization_color
    ):
        raise InstallBackupValidationError(
            "Windows appearance changed while the install backup was captured."
        )
    if apps.exists:
        if (
            apps.type_code != REG_DWORD
            or isinstance(apps.data, bool)
            or apps.data not in {0, 1}
        ):
            raise InstallBackupValidationError(
                "Current AppsUseLightTheme is not a valid REG_DWORD."
            )
        type_code: int | None = apps.type_code
        data: int | None = int(apps.data)
    else:
        type_code = None
        data = None
    app_mode = (
        ("Light" if data == 1 else "Dark") if data is not None else visual.app_mode
    )
    system_mode = visual.system_mode
    if appearance_registry is not None:
        system_value = appearance_registry.system_theme
        if system_value.exists:
            if (
                system_value.type_code != REG_DWORD
                or isinstance(system_value.data, bool)
                or system_value.data not in {0, 1}
            ):
                raise InstallBackupValidationError(
                    "Current SystemUsesLightTheme is not a valid REG_DWORD."
                )
            system_mode = "Light" if system_value.data == 1 else "Dark"
    recovery_content = normalize_theme_visual_state(
        content,
        colorization_color,
        auto_colorization=visual.auto_colorization == "1",
        app_mode=app_mode,
        system_mode=system_mode,
    )
    recovery_visual = read_visual_state(recovery_content)
    manifest = InstallBackup(
        captured_at=timestamp or captured_at(),
        created_by_version=created_by_version,
        windows_build=windows_build,
        apps_value_exists=apps.exists,
        apps_value_type_code=type_code,
        apps_value_data=data,
        source_theme_path=str(source_path.resolve()),
        theme_sha256=hashlib.sha256(recovery_content).hexdigest(),
        auto_colorization=(recovery_visual.auto_colorization == "1"),
        colorization_color=(f"0X{recovery_visual.colorization_color:08X}"),
        app_mode=recovery_visual.app_mode,
        system_mode=recovery_visual.system_mode,
        appearance_registry=appearance_registry,
    )
    return store.create(manifest, recovery_content)
