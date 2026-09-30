"""Stage-2 accent profile capture and transactional theme application."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeGuard

from .accent_profile import AccentProfile, profile_from_theme
from .accent_theme import (
    LiveThemeApplyError,
    ThemeApplyV2Backend,
    ThemeVisualState,
    WindowsThemeApplyBackend,
    apply_and_verify_theme_v2,
    build_managed_theme,
    materialize_theme_visual_state,
    normalize_theme_visual_state,
    read_visual_state,
    sha256_bytes,
    theme_visual_state_from_dict,
    write_new_bytes,
)
from .appearance import (
    AppearanceRegistrySnapshot,
    AppearanceSettingsBackend,
    ThemeMode,
    WindowsAppearanceSettingsBackend,
)
from .backup import InstallBackup
from .persistence import atomic_write_json, captured_at, load_json_object
from .storage import UserDataLayout


def _require_install_backup(value: object) -> InstallBackup:
    if not isinstance(value, InstallBackup):
        raise TypeError("Install appearance target must be InstallBackup.")
    return value


def _is_object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _is_string_mapping(value: object) -> TypeGuard[Mapping[str, object]]:
    return _is_object_mapping(value) and all(isinstance(key, str) for key in value)


@dataclass(frozen=True)
class AccentApplyOutcome:
    transaction_directory: Path
    active_theme_before: Path
    active_theme_after: Path
    before: dict[str, str | int]
    target: dict[str, str | int]
    actual: dict[str, str | int]
    index_before: int
    bridge_target_index: int
    index_after: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "transactionDirectory": str(self.transaction_directory.resolve()),
            "activeThemeBefore": str(self.active_theme_before),
            "activeThemeAfter": str(self.active_theme_after),
            "before": self.before,
            "target": self.target,
            "actual": self.actual,
            "themeManager": "IThemeManager2",
            "themeIndexBefore": self.index_before,
            "themeIndexBridgeTarget": self.bridge_target_index,
            "themeIndexAfter": self.index_after,
            "verified": True,
            "verificationScope": (
                "managed theme color, AutoColorization, AppMode/SystemMode, "
                "and IThemeManager2 transition; visible Shell color requires acceptance"
            ),
        }


@dataclass(frozen=True)
class _InstallAppearanceIntent:
    profile: str
    captured_at: str
    auto_colorization: bool
    colorization_color: int
    windows_build: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "themescheduler.install-appearance-target",
            "schemaVersion": 1,
            "source": "first-install-backup",
            "capturedAt": self.captured_at,
            "accent": {
                "autoColorization": self.auto_colorization,
                "colorizationColor": (f"0X{self.colorization_color:08X}"),
            },
            "environment": {
                "windowsBuild": self.windows_build,
            },
        }


def capture_live_profile(
    profile: str,
    windows_build: str,
    *,
    backend: ThemeApplyV2Backend | None = None,
    visual_state_reader: Callable[[], ThemeVisualState] | None = None,
    timestamp: str | None = None,
) -> AccentProfile:
    theme_backend = backend or WindowsThemeApplyBackend()
    active = theme_backend.current_theme_path()
    content = active.read_bytes()
    if visual_state_reader is not None:
        content = materialize_theme_visual_state(content, visual_state_reader())
    return profile_from_theme(
        content,
        profile=profile,
        captured_at=timestamp or captured_at(),
        windows_build=windows_build,
    )


def apply_accent_profile(
    profile: AccentProfile | _InstallAppearanceIntent,
    layout: UserDataLayout,
    *,
    apps_theme: ThemeMode | None = None,
    system_theme: ThemeMode | None = None,
    start_taskbar_accent: bool | None = None,
    title_borders_accent: bool | None = None,
    transaction_directory: Path | None = None,
    backend: ThemeApplyV2Backend | None = None,
    appearance_backend: AppearanceSettingsBackend | None = None,
    settle_seconds: float = 2.0,
    verification_timeout_seconds: float = 5.0,
) -> AccentApplyOutcome:
    """Apply the scheduled appearance in one rollback-capable transaction."""

    theme_backend = backend or WindowsThemeApplyBackend()
    layout.ensure_directories()
    if transaction_directory is None:
        transaction = layout.new_transaction_directory()
        transaction.mkdir(parents=False, exist_ok=False)
    else:
        transaction = Path(transaction_directory)
        runtime = layout.runtime.resolve()
        resolved = transaction.resolve()
        if (
            resolved.parent != runtime
            or not resolved.name.startswith("accent-")
            or not resolved.is_dir()
        ):
            raise ValueError(
                "Provided transaction directory is not a direct accent-* runtime directory."
            )
        transaction = resolved
    before_path = transaction / "before.theme"
    rollback_path = transaction / "rollback.theme"
    managed_path = transaction / "managed.theme"
    journal_path = transaction / "journal.json"
    for output in (before_path, rollback_path, managed_path, journal_path):
        if output.exists():
            raise FileExistsError(
                f"Refusing to overwrite existing transaction file: {output}"
            )

    active_before_path = theme_backend.current_theme_path()
    active_before_content = active_before_path.read_bytes()
    appearance_targets = (
        system_theme,
        start_taskbar_accent,
        title_borders_accent,
    )
    settings = appearance_backend
    if settings is None and (
        any(value is not None for value in appearance_targets) or backend is None
    ):
        settings = WindowsAppearanceSettingsBackend()
    settings_before = settings.capture() if settings is not None else None
    visual_state_reader = settings.read_visual_state if settings is not None else None
    current_state = (
        visual_state_reader()
        if visual_state_reader is not None
        else read_visual_state(active_before_content)
    )
    managed = build_managed_theme(
        active_before_content,
        profile.colorization_color,
        auto_colorization=profile.auto_colorization,
        app_mode=apps_theme.value.title() if apps_theme is not None else None,
        system_mode=system_theme.value.title() if system_theme is not None else None,
        display_name=f"ThemeScheduler {profile.profile.title()} Accent",
        current_state=current_state,
    )
    rollback_content = normalize_theme_visual_state(
        active_before_content,
        current_state.colorization_color,
        auto_colorization=current_state.auto_colorization == "1",
        app_mode=current_state.app_mode,
        system_mode=current_state.system_mode,
        current_state=current_state,
    )
    write_new_bytes(before_path, active_before_content)
    write_new_bytes(rollback_path, rollback_content)
    write_new_bytes(managed_path, managed.content)

    journal: dict[str, Any] = {
        "kind": "themescheduler.accent-transaction",
        "schemaVersion": 2,
        "status": "prepared",
        "preparedAt": captured_at(),
        "profile": profile.as_dict(),
        "files": {
            "sourcePath": str(active_before_path),
            "beforeTheme": str(before_path.resolve()),
            "beforeSha256": sha256_bytes(active_before_content),
            "rollbackTheme": str(rollback_path.resolve()),
            "rollbackSha256": sha256_bytes(rollback_content),
            "managedTheme": str(managed_path.resolve()),
            "managedSha256": sha256_bytes(managed.content),
        },
        "before": managed.before.as_dict(),
        "target": managed.after.as_dict(),
        "settingsBefore": (
            settings_before.as_dict() if settings_before is not None else None
        ),
        "settingsTarget": {
            "appsTheme": apps_theme.value if apps_theme is not None else None,
            "systemTheme": system_theme.value if system_theme is not None else None,
            "startTaskbarAccent": start_taskbar_accent,
            "titleBordersAccent": title_borders_accent,
        },
    }
    atomic_write_json(journal_path, journal)
    try:
        if settings is not None:
            settings.write_accent_surfaces(
                start_taskbar=start_taskbar_accent,
                title_borders=title_borders_accent,
            )
        applied = apply_and_verify_theme_v2(
            managed_path,
            managed.after,
            managed.before,
            rollback_path=rollback_path,
            backend=theme_backend,
            settle_seconds=settle_seconds,
            verification_timeout_seconds=verification_timeout_seconds,
            visual_state_reader=visual_state_reader,
        )
        journal.update(
            {
                "status": "theme-applied",
                "actual": applied.actual.as_dict(),
                "verificationDiagnostics": applied.verification_diagnostics,
                "themeManager": {
                    "name": "IThemeManager2",
                    "indexBefore": applied.index_before,
                    "bridgeTargetIndex": applied.bridge_target_index,
                    "indexAfter": applied.index_after,
                },
            }
        )
        atomic_write_json(journal_path, journal, force=True)
        if settings is not None:
            failures = settings.verify(
                apps_theme=apps_theme,
                system_theme=system_theme,
                start_taskbar=start_taskbar_accent,
                title_borders=title_borders_accent,
            )
            if failures:
                raise OSError(failures[0])
            journal["settingsActual"] = settings.capture().as_dict()
    except LiveThemeApplyError as exc:
        settings_rollback = _restore_appearance_settings(settings, settings_before)
        rollback_succeeded = exc.rollback_succeeded and settings_rollback
        journal.update(
            {
                "status": "failed",
                "completedAt": captured_at(),
                "error": str(exc),
                "rollbackSucceeded": rollback_succeeded,
            }
        )
        if exc.verification_diagnostics is not None:
            journal["verificationDiagnostics"] = exc.verification_diagnostics
        atomic_write_json(journal_path, journal, force=True)
        if rollback_succeeded == exc.rollback_succeeded:
            raise
        raise LiveThemeApplyError(
            f"{exc}; appearance registry rollback failed.",
            rollback_succeeded=False,
            verification_diagnostics=exc.verification_diagnostics,
        ) from exc
    except Exception as exc:
        rollback_succeeded = rollback_accent_transaction(
            transaction,
            backend=theme_backend,
            appearance_backend=settings,
            settle_seconds=settle_seconds,
            visual_state_reader=visual_state_reader,
        )
        journal.update(
            {
                "status": "failed",
                "completedAt": captured_at(),
                "error": str(exc),
                "rollbackSucceeded": rollback_succeeded,
            }
        )
        atomic_write_json(journal_path, journal, force=True)
        raise LiveThemeApplyError(
            f"Scheduled appearance verification failed: {exc}",
            rollback_succeeded=rollback_succeeded,
        ) from exc

    journal.update(
        {
            "status": "applied",
            "completedAt": captured_at(),
            "actual": applied.actual.as_dict(),
            "verificationDiagnostics": applied.verification_diagnostics,
            "themeManager": {
                "name": "IThemeManager2",
                "indexBefore": applied.index_before,
                "bridgeTargetIndex": applied.bridge_target_index,
                "indexAfter": applied.index_after,
            },
        }
    )
    atomic_write_json(journal_path, journal, force=True)
    return AccentApplyOutcome(
        transaction,
        active_before_path,
        applied.active_path,
        managed.before.as_dict(),
        managed.after.as_dict(),
        applied.actual.as_dict(),
        applied.index_before,
        applied.bridge_target_index,
        applied.index_after,
    )


def apply_install_backup_appearance(
    backup: InstallBackup,
    layout: UserDataLayout,
    *,
    backend: ThemeApplyV2Backend | None = None,
    appearance_backend: AppearanceSettingsBackend | None = None,
    settle_seconds: float = 2.0,
) -> AccentApplyOutcome:
    """Restore the complete install-time appearance when the backup supports it."""

    backup = _require_install_backup(backup)
    target = _InstallAppearanceIntent(
        profile="install-backup",
        captured_at=backup.captured_at,
        auto_colorization=backup.auto_colorization,
        colorization_color=int(
            backup.colorization_color[2:],
            16,
        ),
        windows_build=backup.windows_build,
    )
    apps_theme = ThemeMode.LIGHT if backup.app_mode == "Light" else ThemeMode.DARK
    system_theme: ThemeMode | None = None
    start_taskbar: bool | None = None
    title_borders: bool | None = None
    if backup.appearance_registry is not None:
        system_theme = (
            ThemeMode.LIGHT if backup.system_mode == "Light" else ThemeMode.DARK
        )
        start_value = backup.appearance_registry.start_taskbar_accent
        title_value = backup.appearance_registry.title_borders_accent
        if start_value.exists and start_value.data in {0, 1}:
            start_taskbar = bool(start_value.data)
        if title_value.exists and title_value.data in {0, 1}:
            title_borders = bool(title_value.data)
    outcome = apply_accent_profile(
        target,
        layout,
        apps_theme=apps_theme,
        system_theme=system_theme,
        start_taskbar_accent=start_taskbar,
        title_borders_accent=title_borders,
        backend=backend,
        appearance_backend=appearance_backend,
        settle_seconds=settle_seconds,
    )
    if (
        outcome.actual.get("appMode") != backup.app_mode
        or outcome.actual.get("colorizationColor") != backup.colorization_color
        or outcome.actual.get("systemMode")
        != (
            backup.system_mode
            if backup.appearance_registry is not None
            else outcome.before.get("systemMode")
        )
    ):
        raise RuntimeError("Install appearance restore final verification mismatch.")
    return outcome


def rollback_accent_transaction(
    transaction_directory: Path,
    *,
    backend: ThemeApplyV2Backend | None = None,
    appearance_backend: AppearanceSettingsBackend | None = None,
    settle_seconds: float = 2.0,
    visual_state_reader: Callable[[], ThemeVisualState] | None = None,
) -> bool:
    """Restore the complete before.theme after a later core-stage failure."""

    if visual_state_reader is None and appearance_backend is not None:
        visual_state_reader = appearance_backend.read_visual_state

    transaction = Path(transaction_directory)
    before_path = transaction / "before.theme"
    journal_path = transaction / "journal.json"
    journal = load_json_object(journal_path)
    if journal.get("kind") != "themescheduler.accent-transaction":
        raise ValueError("Rollback journal is not an accent transaction.")
    files = journal.get("files")
    if not _is_string_mapping(files):
        raise ValueError("Rollback journal has no file binding.")
    before_payload = journal.get("before")
    if not _is_string_mapping(before_payload):
        raise ValueError("Rollback journal has no visual-state binding.")
    expected = theme_visual_state_from_dict(before_payload)
    before_content = before_path.read_bytes()
    if files.get("beforeSha256") != sha256_bytes(before_content):
        raise ValueError("Rollback before.theme hash does not match its journal.")

    recovery_path = before_path
    recovery_name = files.get("rollbackTheme")
    recovery_hash = files.get("rollbackSha256")
    has_recovery_name = isinstance(recovery_name, str)
    has_recovery_hash = isinstance(recovery_hash, str)
    if has_recovery_name != has_recovery_hash:
        raise ValueError("Rollback theme binding is incomplete.")
    if has_recovery_name and has_recovery_hash:
        assert isinstance(recovery_name, str)
        assert isinstance(recovery_hash, str)
        candidate = Path(recovery_name)
        if candidate.parent.resolve() != transaction.resolve():
            raise ValueError("Rollback theme escapes its accent transaction.")
        recovery_content = candidate.read_bytes()
        if sha256_bytes(recovery_content) != recovery_hash:
            raise ValueError("Rollback theme hash does not match its journal.")
        recovery_path = candidate

    theme_backend = backend or WindowsThemeApplyBackend()
    theme_restored = False
    try:
        actual = (
            visual_state_reader()
            if visual_state_reader is not None
            else read_visual_state(theme_backend.current_theme_path().read_bytes())
        )
        theme_restored = actual == expected
    except Exception:
        theme_restored = False
    manager = journal.get("themeManager")
    if not theme_restored and _is_string_mapping(manager):
        before_index = manager.get("indexBefore")
        if isinstance(before_index, int) and not isinstance(before_index, bool):
            try:
                theme_backend.set_v2_index(before_index)
                if settle_seconds:
                    time.sleep(settle_seconds)
                actual = (
                    visual_state_reader()
                    if visual_state_reader is not None
                    else read_visual_state(
                        theme_backend.current_theme_path().read_bytes()
                    )
                )
                if actual == expected:
                    theme_restored = True
            except Exception:
                pass
    if not theme_restored:
        try:
            theme_backend.apply_theme_v2(recovery_path)
            if settle_seconds:
                time.sleep(settle_seconds)
            actual = (
                visual_state_reader()
                if visual_state_reader is not None
                else read_visual_state(theme_backend.current_theme_path().read_bytes())
            )
            theme_restored = actual == expected
        except Exception:
            theme_restored = False

    settings_restored = True
    settings_payload = journal.get("settingsBefore")
    if settings_payload is not None:
        try:
            if not _is_string_mapping(settings_payload):
                raise TypeError("Appearance rollback snapshot is invalid.")
            snapshot = AppearanceRegistrySnapshot.from_dict(settings_payload)
            settings = appearance_backend or WindowsAppearanceSettingsBackend()
            settings.restore(snapshot)
        except Exception:
            settings_restored = False
    return theme_restored and settings_restored


def _restore_appearance_settings(
    backend: AppearanceSettingsBackend | None,
    snapshot: AppearanceRegistrySnapshot | None,
) -> bool:
    if backend is None or snapshot is None:
        return True
    try:
        backend.restore(snapshot)
        return True
    except Exception:
        return False
