"""Stage-2 accent profile capture and transactional theme application."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .accent_profile import AccentProfile, profile_from_theme
from .accent_theme import (
    LiveThemeApplyError,
    ThemeApplyV2Backend,
    WindowsThemeApplyBackend,
    apply_and_verify_theme_v2,
    build_managed_theme,
    read_visual_state,
    sha256_bytes,
    write_new_bytes,
)
from .backup import InstallBackup
from .persistence import atomic_write_json, captured_at, load_json_object
from .storage import UserDataLayout
from .theme import ThemeMode


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
    timestamp: str | None = None,
) -> AccentProfile:
    theme_backend = backend or WindowsThemeApplyBackend()
    active = theme_backend.current_theme_path()
    return profile_from_theme(
        active.read_bytes(),
        profile=profile,
        captured_at=timestamp or captured_at(),
        windows_build=windows_build,
    )


def apply_accent_profile(
    profile: AccentProfile | _InstallAppearanceIntent,
    layout: UserDataLayout,
    *,
    apps_theme: ThemeMode | None = None,
    transaction_directory: Path | None = None,
    backend: ThemeApplyV2Backend | None = None,
    settle_seconds: float = 2.0,
) -> AccentApplyOutcome:
    """Apply accent and optional app mode in one rollback-capable theme transaction."""

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
    managed_path = transaction / "managed.theme"
    journal_path = transaction / "journal.json"
    for output in (before_path, managed_path, journal_path):
        if output.exists():
            raise FileExistsError(
                f"Refusing to overwrite existing transaction file: {output}"
            )

    active_before_path = theme_backend.current_theme_path()
    active_before_content = active_before_path.read_bytes()
    managed = build_managed_theme(
        active_before_content,
        profile.colorization_color,
        auto_colorization=profile.auto_colorization,
        app_mode=apps_theme.value.title() if apps_theme is not None else None,
        display_name=f"ThemeScheduler {profile.profile.title()} Accent",
    )
    write_new_bytes(before_path, active_before_content)
    write_new_bytes(managed_path, managed.content)

    journal: dict[str, Any] = {
        "kind": "themescheduler.accent-transaction",
        "schemaVersion": 1,
        "status": "prepared",
        "preparedAt": captured_at(),
        "profile": profile.as_dict(),
        "files": {
            "sourcePath": str(active_before_path),
            "beforeTheme": str(before_path.resolve()),
            "beforeSha256": sha256_bytes(active_before_content),
            "managedTheme": str(managed_path.resolve()),
            "managedSha256": sha256_bytes(managed.content),
        },
        "before": managed.before.as_dict(),
        "target": managed.after.as_dict(),
    }
    atomic_write_json(journal_path, journal)
    try:
        applied = apply_and_verify_theme_v2(
            managed_path,
            managed.after,
            managed.before,
            rollback_path=before_path,
            backend=theme_backend,
            settle_seconds=settle_seconds,
        )
    except LiveThemeApplyError as exc:
        journal.update(
            {
                "status": "failed",
                "completedAt": captured_at(),
                "error": str(exc),
                "rollbackSucceeded": exc.rollback_succeeded,
            }
        )
        atomic_write_json(journal_path, journal, force=True)
        raise

    journal.update(
        {
            "status": "applied",
            "completedAt": captured_at(),
            "actual": applied.actual.as_dict(),
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
    settle_seconds: float = 2.0,
) -> AccentApplyOutcome:
    """Restore install-time semantic appearance while preserving SystemMode."""

    if not isinstance(backup, InstallBackup):
        raise TypeError("Install appearance target must be InstallBackup.")
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
    outcome = apply_accent_profile(
        target,
        layout,
        apps_theme=apps_theme,
        backend=backend,
        settle_seconds=settle_seconds,
    )
    if (
        outcome.actual.get("appMode") != backup.app_mode
        or outcome.actual.get("colorizationColor") != backup.colorization_color
        or outcome.actual.get("systemMode") != outcome.before.get("systemMode")
    ):
        raise RuntimeError("Install appearance restore final verification mismatch.")
    return outcome


def rollback_accent_transaction(
    transaction_directory: Path,
    *,
    backend: ThemeApplyV2Backend | None = None,
    settle_seconds: float = 2.0,
) -> bool:
    """Restore the complete before.theme after a later core-stage failure."""

    transaction = Path(transaction_directory)
    before_path = transaction / "before.theme"
    journal_path = transaction / "journal.json"
    before_content = before_path.read_bytes()
    expected = read_visual_state(before_content)
    journal = load_json_object(journal_path)
    if journal.get("kind") != "themescheduler.accent-transaction":
        raise ValueError("Rollback journal is not an accent transaction.")
    files = journal.get("files")
    if not isinstance(files, dict):
        raise ValueError("Rollback journal has no file binding.")
    if files.get("beforeSha256") != sha256_bytes(before_content):
        raise ValueError("Rollback before.theme hash does not match its journal.")

    theme_backend = backend or WindowsThemeApplyBackend()
    manager = journal.get("themeManager")
    if isinstance(manager, dict):
        before_index = manager.get("indexBefore")
        if isinstance(before_index, int) and not isinstance(before_index, bool):
            try:
                theme_backend.set_v2_index(before_index)
                if settle_seconds:
                    time.sleep(settle_seconds)
                actual = read_visual_state(
                    theme_backend.current_theme_path().read_bytes()
                )
                if actual == expected:
                    return True
            except Exception:
                pass
    try:
        theme_backend.apply_theme_v2(before_path)
        if settle_seconds:
            time.sleep(settle_seconds)
        actual = read_visual_state(theme_backend.current_theme_path().read_bytes())
        return actual == expected
    except Exception:
        return False
