"""Transactional current-user integration for Stage 8.3."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .config import AppConfig
from .errors import ThemeSchedulerRuntimeError
from .lifecycle import (
    InstalledAppRegistration,
    InstallLayout,
)
from .notification_contracts import (
    APP_USER_MODEL_ID,
    TOAST_ACTIVATOR_CLSID,
)
from .protocol_registration import (
    NotificationProtocolBackend,
    ProtocolRegistration,
    RegistryTreeBackup,
)
from .scheduler import (
    DEFAULT_TASK_PATH,
    TaskDefinitionBackup,
    TaskSchedulerBackend,
    TaskSpec,
    build_task_spec,
    compare_task_specs,
    reconcile_task,
)

SHORTCUT_FILE_NAME = "ThemeScheduler.lnk"
START_MENU_FOLDER_NAME = "ThemeScheduler"


class SystemIntegrationError(ThemeSchedulerRuntimeError):
    """Raised when current-user integration or its rollback is incomplete."""


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
        str(right.resolve(strict=False))
    )


def _normalize_shortcut_paths(
    path: Path,
    target: Path,
    working_directory: Path,
    icon_location: str,
) -> tuple[Path, Path, Path, str]:
    path = Path(path)
    target = Path(target)
    working = Path(working_directory)
    if not path.is_absolute() or path.suffix.casefold() != ".lnk":
        raise ValueError("Shortcut path must be an absolute .lnk path.")
    if path.name != SHORTCUT_FILE_NAME:
        raise ValueError("Shortcut file name is not managed by ThemeScheduler.")
    if not target.is_absolute() or target.name != "ThemeScheduler.exe":
        raise ValueError("Shortcut target must be ThemeScheduler.exe.")
    if not working.is_absolute() or not _same_path(working, target.parent):
        raise ValueError("Shortcut working directory must contain its target.")
    if not isinstance(icon_location, str) or "," not in icon_location:
        raise ValueError("Shortcut icon must use the product executable.")
    icon_path, icon_index = icon_location.rsplit(",", 1)
    if icon_index.strip() != "0" or not _same_path(Path(icon_path), target):
        raise ValueError("Shortcut icon must use the product executable.")
    resolved_target = target.resolve(strict=False)
    return (
        path.resolve(strict=False),
        resolved_target,
        working.resolve(strict=False),
        f"{resolved_target},0",
    )


def _validate_shortcut_metadata(
    *,
    arguments: str,
    description: str,
    app_user_model_id: str,
    toast_activator_clsid: str,
) -> None:
    if arguments != "":
        raise ValueError("The product shortcut cannot contain arguments.")
    if not isinstance(description, str) or not description or len(description) > 260:
        raise ValueError("Shortcut description must be non-empty and bounded.")
    if app_user_model_id != APP_USER_MODEL_ID:
        raise ValueError(
            "Shortcut AppUserModelID must use the frozen product identity."
        )
    if toast_activator_clsid != TOAST_ACTIVATOR_CLSID:
        raise ValueError("Shortcut Toast activator CLSID must use the frozen identity.")


@dataclass(frozen=True)
class ShortcutSpec:
    path: Path
    target: Path
    arguments: str
    working_directory: Path
    description: str
    icon_location: str
    app_user_model_id: str = APP_USER_MODEL_ID
    toast_activator_clsid: str = TOAST_ACTIVATOR_CLSID

    def __post_init__(self) -> None:
        _validate_shortcut_metadata(
            arguments=self.arguments,
            description=self.description,
            app_user_model_id=self.app_user_model_id,
            toast_activator_clsid=self.toast_activator_clsid,
        )
        path, target, working, icon = _normalize_shortcut_paths(
            self.path,
            self.target,
            self.working_directory,
            self.icon_location,
        )
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "working_directory", working)
        object.__setattr__(self, "icon_location", icon)

    @classmethod
    def create(cls, path: Path, layout: InstallLayout) -> ShortcutSpec:
        return cls(
            path=path,
            target=layout.executable,
            arguments="",
            working_directory=layout.app,
            description="ThemeScheduler 昼夜主题计划",
            icon_location=f"{layout.executable},0",
            app_user_model_id=APP_USER_MODEL_ID,
            toast_activator_clsid=TOAST_ACTIVATOR_CLSID,
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "path": str(self.path),
            "target": str(self.target),
            "arguments": self.arguments,
            "workingDirectory": str(self.working_directory),
            "description": self.description,
            "iconLocation": self.icon_location,
            "appUserModelId": self.app_user_model_id,
            "toastActivatorClsid": self.toast_activator_clsid,
        }


@dataclass(frozen=True)
class ShortcutPlan:
    start_menu: ShortcutSpec
    desktop: ShortcutSpec
    desktop_enabled: bool

    @classmethod
    def create(
        cls,
        layout: InstallLayout,
        *,
        programs_folder: Path,
        desktop_folder: Path,
        desktop_enabled: bool,
    ) -> ShortcutPlan:
        if not isinstance(desktop_enabled, bool):
            raise ValueError("desktop_enabled must be boolean.")
        programs_folder = Path(programs_folder).resolve(strict=False)
        desktop_folder = Path(desktop_folder).resolve(strict=False)
        if not programs_folder.is_absolute() or not desktop_folder.is_absolute():
            raise ValueError("Known folders must be absolute.")
        return cls(
            start_menu=ShortcutSpec.create(
                programs_folder / START_MENU_FOLDER_NAME / SHORTCUT_FILE_NAME,
                layout,
            ),
            desktop=ShortcutSpec.create(
                desktop_folder / SHORTCUT_FILE_NAME,
                layout,
            ),
            desktop_enabled=desktop_enabled,
        )

    @property
    def managed_paths(self) -> tuple[Path, Path]:
        return self.start_menu.path, self.desktop.path


@dataclass(frozen=True)
class RegistryKeyBackup:
    values: tuple[tuple[str, Any, int], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.values, tuple):
            raise ValueError("Registry backup values must be a tuple.")
        names: list[str] = []
        for item in self.values:
            if (
                not isinstance(item, tuple)
                or len(item) != 3
                or not isinstance(item[0], str)
                or isinstance(item[2], bool)
                or not isinstance(item[2], int)
            ):
                raise ValueError("Registry backup value is malformed.")
            names.append(item[0].casefold())
        if len(names) != len(set(names)):
            raise ValueError("Registry backup contains duplicate value names.")
        if (
            tuple(sorted(self.values, key=lambda item: item[0].casefold()))
            != self.values
        ):
            raise ValueError("Registry backup values must be sorted.")


class InstalledAppRegistryBackend(Protocol):
    def capture(self) -> RegistryKeyBackup | None: ...
    def read(self) -> InstalledAppRegistration | None: ...
    def write(self, registration: InstalledAppRegistration) -> None: ...
    def restore(self, backup: RegistryKeyBackup | None) -> None: ...


class ShortcutBackend(Protocol):
    def capture(self, path: Path) -> bytes | None: ...
    def read(self, path: Path) -> ShortcutSpec | None: ...
    def write(self, shortcut: ShortcutSpec) -> None: ...
    def restore(self, path: Path, backup: bytes | None) -> None: ...


@dataclass(frozen=True)
class SystemIntegrationOutcome:
    result: str
    registration_changed: bool | None
    protocol_changed: bool | None
    shortcuts_changed: bool | None
    task_changed: bool | None
    verified: bool
    rollback_attempted: bool
    rollback_succeeded: bool | None
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "registrationChanged": self.registration_changed,
            "notificationProtocolChanged": self.protocol_changed,
            "shortcutsChanged": self.shortcuts_changed,
            "taskSchedulerChanged": self.task_changed,
            "verified": self.verified,
            "rollbackAttempted": self.rollback_attempted,
            "rollbackSucceeded": self.rollback_succeeded,
            "windowsChanged": any(
                value is True
                for value in (
                    self.registration_changed,
                    self.protocol_changed,
                    self.shortcuts_changed,
                    self.task_changed,
                )
            ),
            "message": self.message,
        }


@dataclass(frozen=True)
class _IntegrationPlan:
    registration: InstalledAppRegistration
    protocol: ProtocolRegistration
    shortcuts: dict[Path, ShortcutSpec | None]
    task: TaskSpec


@dataclass(frozen=True)
class _IntegrationSnapshot:
    registry_backup: RegistryKeyBackup | None
    protocol_backup: RegistryTreeBackup | None
    shortcut_backups: dict[Path, bytes | None]
    task_before: TaskSpec | None
    task_backup: TaskDefinitionBackup | None
    registration_before: InstalledAppRegistration | None
    protocol_before: ProtocolRegistration | None
    shortcuts_before: dict[Path, ShortcutSpec | None]


@dataclass(frozen=True)
class _IntegrationChanges:
    registration: bool
    protocol: bool
    shortcuts: dict[Path, ShortcutSpec | None]

    @property
    def shortcuts_changed(self) -> bool:
        return bool(self.shortcuts)


@dataclass(frozen=True)
class _RollbackStep:
    label: str
    restore: Callable[[], None]


class CurrentUserIntegrationService:
    """Reconcile registration, shortcuts and the scheduled task as one unit."""

    def __init__(
        self,
        layout: InstallLayout,
        registry: InstalledAppRegistryBackend,
        shortcuts: ShortcutBackend,
        tasks: TaskSchedulerBackend,
        protocol: NotificationProtocolBackend | None = None,
    ) -> None:
        self.layout = layout
        self.registry = registry
        self.shortcuts = shortcuts
        self.tasks = tasks
        self.protocol = protocol

    def _build_plan(
        self,
        *,
        registration: InstalledAppRegistration,
        shortcut_plan: ShortcutPlan,
        config: AppConfig,
        user_id: str,
    ) -> _IntegrationPlan:
        if not _same_path(
            Path(registration.install_location),
            self.layout.program_root,
        ):
            raise ValueError("Registration does not belong to this install layout.")
        desired_task = build_task_spec(
            config,
            executable=str(self.layout.executable),
            user_id=user_id,
        )
        desired_protocol = ProtocolRegistration(self.layout.executable)
        desired_shortcuts: dict[Path, ShortcutSpec | None] = {
            shortcut_plan.start_menu.path: shortcut_plan.start_menu,
            shortcut_plan.desktop.path: (
                shortcut_plan.desktop if shortcut_plan.desktop_enabled else None
            ),
        }
        for shortcut in (
            shortcut_plan.start_menu,
            shortcut_plan.desktop,
        ):
            if not _same_path(shortcut.target, self.layout.executable):
                raise ValueError(
                    "Shortcut target does not belong to this install layout."
                )
            if not _same_path(
                shortcut.working_directory,
                self.layout.app,
            ):
                raise ValueError(
                    "Shortcut working directory does not belong to this install layout."
                )
        return _IntegrationPlan(
            registration=registration,
            protocol=desired_protocol,
            shortcuts=desired_shortcuts,
            task=desired_task,
        )

    def _capture_snapshot(self, plan: _IntegrationPlan) -> _IntegrationSnapshot:
        registry_backup = self.registry.capture()
        protocol_backup = self.protocol.capture() if self.protocol is not None else None
        shortcut_backups = {
            path: self.shortcuts.capture(path) for path in plan.shortcuts
        }
        task_before = self.tasks.read(DEFAULT_TASK_PATH)
        task_backup = self.tasks.capture(DEFAULT_TASK_PATH)
        if (task_before is None) != (task_backup is None):
            raise SystemIntegrationError(
                "Task changed while system integration was captured."
            )

        registration_before = self.registry.read()
        protocol_before = self.protocol.read() if self.protocol is not None else None
        shortcut_before = {path: self.shortcuts.read(path) for path in plan.shortcuts}
        snapshot = _IntegrationSnapshot(
            registry_backup=registry_backup,
            protocol_backup=protocol_backup,
            shortcut_backups=shortcut_backups,
            task_before=task_before,
            task_backup=task_backup,
            registration_before=registration_before,
            protocol_before=protocol_before,
            shortcuts_before=shortcut_before,
        )
        self._verify_capture_stable(snapshot)
        return snapshot

    def _verify_capture_stable(self, snapshot: _IntegrationSnapshot) -> None:
        if self.registry.capture() != snapshot.registry_backup:
            raise SystemIntegrationError(
                "Installed-app registration changed during capture."
            )
        if (
            self.protocol is not None
            and self.protocol.capture() != snapshot.protocol_backup
        ):
            raise SystemIntegrationError(
                "Notification protocol changed during capture."
            )
        for path, backup in snapshot.shortcut_backups.items():
            if self.shortcuts.capture(path) != backup:
                raise SystemIntegrationError(f"Shortcut changed during capture: {path}")
        task_after_capture = self.tasks.read(DEFAULT_TASK_PATH)
        if (task_after_capture is None) != (snapshot.task_before is None) or (
            task_after_capture is not None
            and snapshot.task_before is not None
            and compare_task_specs(snapshot.task_before, task_after_capture)
        ):
            raise SystemIntegrationError(
                "Task changed during system-integration capture."
            )

    def _detect_changes(
        self,
        plan: _IntegrationPlan,
        snapshot: _IntegrationSnapshot,
    ) -> _IntegrationChanges:
        protocol_changed = (
            snapshot.protocol_before != plan.protocol
            if self.protocol is not None
            else False
        )
        shortcut_changes = {
            path: expected
            for path, expected in plan.shortcuts.items()
            if (
                snapshot.shortcuts_before[path] != expected
                or (expected is None and snapshot.shortcut_backups[path] is not None)
            )
        }
        return _IntegrationChanges(
            registration=snapshot.registration_before != plan.registration,
            protocol=protocol_changed,
            shortcuts=shortcut_changes,
        )

    def _commit(
        self,
        plan: _IntegrationPlan,
        changes: _IntegrationChanges,
    ) -> bool:
        self._apply_shortcuts(changes.shortcuts)
        if changes.registration:
            self.registry.write(plan.registration)
        if self.registry.read() != plan.registration:
            raise SystemIntegrationError(
                "Installed-app registration readback mismatch."
            )
        if self.protocol is not None and changes.protocol:
            self.protocol.write(plan.protocol)
        if self.protocol is not None and self.protocol.read() != plan.protocol:
            raise SystemIntegrationError("Notification protocol readback mismatch.")
        task_outcome = reconcile_task(self.tasks, plan.task)
        self._verify_shortcuts(plan.shortcuts)
        if self.registry.read() != plan.registration:
            raise SystemIntegrationError(
                "Installed-app registration drifted during commit."
            )
        task_after = self.tasks.read(DEFAULT_TASK_PATH)
        if task_after is None or compare_task_specs(plan.task, task_after):
            raise SystemIntegrationError("Scheduled task final readback mismatch.")
        return task_outcome.changed

    @staticmethod
    def _success_outcome(
        changes: _IntegrationChanges,
        task_changed: bool,
    ) -> SystemIntegrationOutcome:
        changed = (
            changes.registration
            or changes.protocol
            or changes.shortcuts_changed
            or task_changed
        )
        return SystemIntegrationOutcome(
            "changed" if changed else "no-change",
            changes.registration,
            changes.protocol,
            changes.shortcuts_changed,
            task_changed,
            True,
            False,
            None,
            "Current-user integration was applied and verified.",
        )

    def _failure_outcome(
        self,
        exc: Exception,
        snapshot: _IntegrationSnapshot,
    ) -> SystemIntegrationOutcome:
        rollback_succeeded = self._rollback(snapshot)
        return SystemIntegrationOutcome(
            "failed" if rollback_succeeded else "partial",
            None,
            None,
            None,
            None,
            False,
            True,
            rollback_succeeded,
            (
                f"System integration failed: {type(exc).__name__}: {exc}. "
                + (
                    "Previous integration was restored."
                    if rollback_succeeded
                    else "Rollback was incomplete."
                )
            )[:500],
        )

    def apply(
        self,
        *,
        registration: InstalledAppRegistration,
        shortcut_plan: ShortcutPlan,
        config: AppConfig,
        user_id: str,
    ) -> SystemIntegrationOutcome:
        plan = self._build_plan(
            registration=registration,
            shortcut_plan=shortcut_plan,
            config=config,
            user_id=user_id,
        )
        snapshot = self._capture_snapshot(plan)
        changes = self._detect_changes(plan, snapshot)
        try:
            task_changed = self._commit(plan, changes)
        except Exception as exc:
            return self._failure_outcome(exc, snapshot)
        return self._success_outcome(changes, task_changed)

    def _apply_shortcuts(
        self,
        desired: dict[Path, ShortcutSpec | None],
    ) -> None:
        for path, shortcut in desired.items():
            if shortcut is None:
                self.shortcuts.restore(path, None)
            else:
                self.shortcuts.write(shortcut)
        self._verify_shortcuts(desired)

    def _verify_shortcuts(
        self,
        desired: dict[Path, ShortcutSpec | None],
    ) -> None:
        for path, shortcut in desired.items():
            if self.shortcuts.read(path) != shortcut:
                raise SystemIntegrationError(f"Shortcut readback mismatch: {path}")

    def _restore_task(self, snapshot: _IntegrationSnapshot) -> None:
        if snapshot.task_before is None:
            self.tasks.delete(DEFAULT_TASK_PATH)
            if self.tasks.read(DEFAULT_TASK_PATH) is not None:
                raise SystemIntegrationError("Scheduled task rollback mismatch.")
            return
        if snapshot.task_backup is None:
            raise SystemIntegrationError("Scheduled task backup is unavailable.")
        self.tasks.restore(snapshot.task_backup)
        restored = self.tasks.read(DEFAULT_TASK_PATH)
        if restored is None or compare_task_specs(snapshot.task_before, restored):
            raise SystemIntegrationError("Scheduled task rollback mismatch.")

    def _restore_protocol(self, backup: RegistryTreeBackup | None) -> None:
        if self.protocol is None:
            return
        self.protocol.restore(backup)
        if self.protocol.capture() != backup:
            raise SystemIntegrationError("Notification protocol rollback mismatch.")

    def _restore_registry(self, backup: RegistryKeyBackup | None) -> None:
        self.registry.restore(backup)
        if self.registry.capture() != backup:
            raise SystemIntegrationError("Installed-app rollback mismatch.")

    def _restore_shortcut(self, path: Path, backup: bytes | None) -> None:
        self.shortcuts.restore(path, backup)
        if self.shortcuts.capture(path) != backup:
            raise SystemIntegrationError(f"Shortcut rollback mismatch: {path}")

    def _rollback_steps(
        self,
        snapshot: _IntegrationSnapshot,
    ) -> tuple[_RollbackStep, ...]:
        steps = [_RollbackStep("scheduled task", lambda: self._restore_task(snapshot))]
        if self.protocol is not None:
            steps.append(
                _RollbackStep(
                    "notification protocol",
                    lambda: self._restore_protocol(snapshot.protocol_backup),
                )
            )
        steps.append(
            _RollbackStep(
                "installed-app registration",
                lambda: self._restore_registry(snapshot.registry_backup),
            )
        )
        steps.extend(
            _RollbackStep(
                f"shortcut {path}",
                lambda path=path, backup=backup: self._restore_shortcut(path, backup),
            )
            for path, backup in snapshot.shortcut_backups.items()
        )
        return tuple(steps)

    def _rollback(self, snapshot: _IntegrationSnapshot) -> bool:
        failures: list[str] = []
        for step in self._rollback_steps(snapshot):
            try:
                step.restore()
            except Exception:
                failures.append(step.label)
        return not failures
