"""Conservative user-data initialization and rollback for Setup."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from .accent_profile import AccentProfileStore, profile_from_theme
from .backup import (
    InstallBackup,
    InstallBackupStore,
    capture_install_backup,
)
from .config import ConfigStore
from .errors import ThemeSchedulerRuntimeError
from .initial_setup import (
    create_initial_setup_marker,
    initial_setup_pending,
)
from .setup_contracts import SetupOptions
from .state import AppState, StateStore
from .storage import UserDataLayout


class SetupDataError(ThemeSchedulerRuntimeError):
    """Raised when retained data is incomplete, damaged, or unsafe."""


@dataclass(frozen=True)
class SetupDataOutcome:
    retained: bool
    created_files: tuple[Path, ...]
    created_directories: tuple[Path, ...]


BackupCapture = Callable[..., InstallBackup]


class SetupDataService:
    """Initialize missing core data once or strictly preserve all of it."""

    def __init__(
        self,
        layout: UserDataLayout,
        *,
        backup_capture: BackupCapture = capture_install_backup,
    ) -> None:
        self.layout = layout
        self.backup_capture = backup_capture

    @property
    def _core_paths(self) -> tuple[Path, ...]:
        return (
            self.layout.config,
            self.layout.state,
            self.layout.profile_path("day"),
            self.layout.profile_path("night"),
            self.layout.install_backup_manifest,
            self.layout.install_backup_theme,
        )

    def _validate_retained(self) -> None:
        missing = [path for path in self._core_paths if not path.is_file()]
        if missing:
            raise SetupDataError(
                "Retained user data is incomplete; missing: "
                + ", ".join(path.name for path in missing)
            )
        ConfigStore(self.layout.config).load()
        StateStore(self.layout.state).load()
        for profile in ("day", "night"):
            AccentProfileStore(
                self.layout.profile_path(profile),
                profile,
            ).load()
        InstallBackupStore(
            self.layout.install_backup_manifest,
            self.layout.install_backup_theme,
        ).load_verified()
        if self.layout.initial_setup_marker.exists():
            initial_setup_pending(self.layout.initial_setup_marker)

    def validate_retained(self) -> None:
        """Validate an existing six-file core without changing it."""

        self._validate_retained()

    def prepare(
        self,
        options: SetupOptions,
        *,
        target_version: str,
        windows_build: str,
    ) -> SetupDataOutcome:
        existing = tuple(path.exists() for path in self._core_paths)
        if any(existing):
            if not all(existing):
                self._validate_retained()
            else:
                self._validate_retained()
            return SetupDataOutcome(True, (), ())

        created_directories: list[Path] = []
        created_files: list[Path] = []
        directories = (
            self.layout.root,
            self.layout.profiles,
            self.layout.runtime,
            self.layout.backup,
            self.layout.logs,
        )
        try:
            for directory in directories:
                if directory.exists():
                    if not directory.is_dir() or directory.is_symlink():
                        raise SetupDataError(
                            f"User-data path is not a regular directory: {directory}"
                        )
                    continue
                directory.mkdir()
                created_directories.append(directory)

            ConfigStore(self.layout.config).initialize(options.config)
            created_files.append(self.layout.config)
            StateStore(self.layout.state).initialize(AppState.pending_initial_setup())
            created_files.append(self.layout.state)
            create_initial_setup_marker(self.layout.initial_setup_marker)
            created_files.append(self.layout.initial_setup_marker)

            backup_store = InstallBackupStore(
                self.layout.install_backup_manifest,
                self.layout.install_backup_theme,
            )
            backup = self.backup_capture(
                backup_store,
                created_by_version=target_version,
                windows_build=windows_build,
            )
            created_files.extend(
                (
                    self.layout.install_backup_theme,
                    self.layout.install_backup_manifest,
                )
            )
            theme_content = self.layout.install_backup_theme.read_bytes()
            for profile in ("day", "night"):
                profile_path = self.layout.profile_path(profile)
                AccentProfileStore(profile_path, profile).create(
                    profile_from_theme(
                        theme_content,
                        profile=profile,
                        captured_at=backup.captured_at,
                        windows_build=windows_build,
                    )
                )
                created_files.append(profile_path)
            self._validate_retained()
            return SetupDataOutcome(
                False,
                tuple(created_files),
                tuple(created_directories),
            )
        except Exception:
            self.rollback(
                SetupDataOutcome(
                    False,
                    tuple(created_files),
                    tuple(created_directories),
                )
            )
            raise

    def rollback(self, outcome: SetupDataOutcome) -> None:
        if outcome.retained:
            return
        for path in reversed(outcome.created_files):
            with suppress(OSError):
                path.unlink(missing_ok=True)
        for path in reversed(outcome.created_directories):
            with suppress(OSError):
                path.rmdir()
