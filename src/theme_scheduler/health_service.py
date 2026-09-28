"""Read-only-first Stage-9 health inspection orchestration."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import uuid4

from .accent_profile import AccentProfileStore
from .config import AppConfig, ConfigStore
from .control_service import ensure_no_pending_auto_transaction
from .core import Clock, SystemClock
from .health_contracts import (
    HealthCategory,
    HealthCheck,
    HealthReport,
    HealthStatus,
)
from .lifecycle import InstallLayout, PayloadManifest
from .lifecycle.deployment import verify_active_payload
from .notification_contracts import APP_USER_MODEL_ID
from .persistence import load_json_object
from .protocol_registration import (
    NotificationProtocolBackend,
    ProtocolRegistration,
)
from .scheduler import TaskSpec, build_task_spec, inspect_task
from .state import StateStore
from .storage import UserDataLayout
from .switch_override import PendingSwitch, PendingSwitchStore
from .system_integration import ShortcutBackend, ShortcutPlan

PayloadVerifier = Callable[[InstallLayout], None]
CapabilityProbe = Callable[[], None]
IdentityReader = Callable[[], str]


class TaskReader(Protocol):
    def read(self, task_path: str) -> TaskSpec | None: ...


class HealthService:
    """Inspect trusted product state without silently repairing it."""

    def __init__(
        self,
        layout: UserDataLayout,
        install_layout: InstallLayout,
        scheduler: TaskReader,
        shortcuts: ShortcutBackend,
        shortcut_plan: ShortcutPlan,
        protocol: NotificationProtocolBackend,
        *,
        user_id: str,
        windows_probe: CapabilityProbe,
        notification_probe: CapabilityProbe,
        process_identity_reader: IdentityReader,
        payload_verifier: PayloadVerifier | None = None,
        clock: Clock | None = None,
        deep_permissions: bool = True,
    ) -> None:
        self.layout = layout
        self.install_layout = install_layout
        self.scheduler = scheduler
        self.shortcuts = shortcuts
        self.shortcut_plan = shortcut_plan
        self.protocol = protocol
        self.user_id = user_id
        self.windows_probe = windows_probe
        self.notification_probe = notification_probe
        self.process_identity_reader = process_identity_reader
        self.payload_verifier = payload_verifier or self._verify_installed_payload
        self.clock = clock or SystemClock()
        self.deep_permissions = deep_permissions

    @staticmethod
    def _check(
        check_id: str,
        category: HealthCategory,
        status: HealthStatus,
        message: str,
        repair_action: str | None = None,
    ) -> HealthCheck:
        return HealthCheck(
            check_id,
            category,
            status,
            message[:500],
            repair_action,
        )

    def _verify_installed_payload(self, layout: InstallLayout) -> None:
        manifest = PayloadManifest.from_dict(load_json_object(layout.payload_manifest))
        verify_active_payload(layout, manifest)

    def _configuration(self) -> tuple[HealthCheck, AppConfig | None]:
        try:
            config = ConfigStore(self.layout.config).load()
            return (
                self._check(
                    "configuration.config",
                    HealthCategory.CONFIGURATION,
                    HealthStatus.HEALTHY,
                    "Configuration is valid.",
                ),
                config,
            )
        except Exception as exc:
            return (
                self._check(
                    "configuration.config",
                    HealthCategory.CONFIGURATION,
                    HealthStatus.ACTION_REQUIRED,
                    f"Configuration is untrusted: {type(exc).__name__}: {exc}",
                ),
                None,
            )

    def _state(self) -> HealthCheck:
        try:
            StateStore(self.layout.state).load()
            ensure_no_pending_auto_transaction(self.layout.runtime)
            return self._check(
                "data.state",
                HealthCategory.DATA,
                HealthStatus.HEALTHY,
                "State and automatic transaction evidence are valid.",
            )
        except Exception as exc:
            return self._check(
                "data.state",
                HealthCategory.DATA,
                HealthStatus.ACTION_REQUIRED,
                f"State or transaction evidence is untrusted: "
                f"{type(exc).__name__}: {exc}",
            )

    def _profile(self, profile: str) -> HealthCheck:
        try:
            AccentProfileStore(self.layout.profile_path(profile), profile).load()
            return self._check(
                f"data.profile.{profile}",
                HealthCategory.DATA,
                HealthStatus.HEALTHY,
                f"The {profile} accent profile is valid.",
            )
        except Exception as exc:
            return self._check(
                f"data.profile.{profile}",
                HealthCategory.DATA,
                HealthStatus.ACTION_REQUIRED,
                f"The {profile} accent profile is untrusted: "
                f"{type(exc).__name__}: {exc}",
            )

    def _payload(self) -> HealthCheck:
        try:
            self.payload_verifier(self.install_layout)
            return self._check(
                "files.payload",
                HealthCategory.FILES,
                HealthStatus.HEALTHY,
                "Installed payload, manifest, and installation record match.",
            )
        except Exception as exc:
            return self._check(
                "files.payload",
                HealthCategory.FILES,
                HealthStatus.ACTION_REQUIRED,
                f"Installed payload is untrusted; rerun Setup: "
                f"{type(exc).__name__}: {exc}",
            )

    def _data_write(self) -> HealthCheck:
        if not self.deep_permissions:
            return self._check(
                "permissions.data-write",
                HealthCategory.PERMISSIONS,
                HealthStatus.WARNING,
                "Deep data-write probing was not requested.",
            )
        directory = self.layout.runtime / "health"
        created_directory = not directory.exists()
        probe = directory / f"probe-{uuid4().hex}.tmp"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with probe.open("xb") as stream:
                stream.write(b"ThemeScheduler health probe\n")
                stream.flush()
                os.fsync(stream.fileno())
            if probe.read_bytes() != b"ThemeScheduler health probe\n":
                raise OSError("Data-write probe readback mismatch.")
            probe.unlink()
            if probe.exists():
                raise OSError("Data-write probe could not be removed.")
            if created_directory:
                directory.rmdir()
            return self._check(
                "permissions.data-write",
                HealthCategory.PERMISSIONS,
                HealthStatus.HEALTHY,
                "Runtime data can be written, flushed, read, and cleaned.",
            )
        except Exception as exc:
            cleanup = ""
            try:
                probe.unlink(missing_ok=True)
                if created_directory and directory.exists():
                    directory.rmdir()
            except Exception as cleanup_exc:
                cleanup = (
                    f"; cleanup failed: {type(cleanup_exc).__name__}: {cleanup_exc}"
                )
            return self._check(
                "permissions.data-write",
                HealthCategory.PERMISSIONS,
                HealthStatus.ACTION_REQUIRED,
                f"Runtime data-write probe failed: "
                f"{type(exc).__name__}: {exc}{cleanup}",
            )

    def _log_write(self) -> HealthCheck:
        created = not self.layout.event_log.exists()
        try:
            self.layout.logs.mkdir(parents=True, exist_ok=True)
            with self.layout.event_log.open("ab"):
                pass
            if created:
                self.layout.event_log.unlink()
                if self.layout.event_log.exists():
                    raise OSError("Temporary structured log probe was not removed.")
            return self._check(
                "permissions.log-write",
                HealthCategory.PERMISSIONS,
                HealthStatus.HEALTHY,
                "The structured event log is append-accessible.",
            )
        except Exception as exc:
            cleanup = ""
            if created:
                try:
                    self.layout.event_log.unlink(missing_ok=True)
                except Exception as cleanup_exc:
                    cleanup = (
                        f"; cleanup failed: {type(cleanup_exc).__name__}: {cleanup_exc}"
                    )
            return self._check(
                "permissions.log-write",
                HealthCategory.PERMISSIONS,
                HealthStatus.ACTION_REQUIRED,
                f"Structured log access failed: {type(exc).__name__}: {exc}{cleanup}",
            )

    def _trusted_pending(
        self, config: AppConfig, now: datetime
    ) -> PendingSwitch | None:
        store = PendingSwitchStore(self.layout.pending_switch)
        if not store.exists:
            return None
        pending = store.load()
        if not config.notify_status_changes or now >= pending.next_fixed_at:
            return None
        return pending

    def _task(self, config: AppConfig | None, now: datetime) -> HealthCheck:
        if config is None:
            return self._check(
                "task.definition",
                HealthCategory.TASK,
                HealthStatus.ACTION_REQUIRED,
                "Task definition cannot be evaluated with untrusted configuration.",
            )
        try:
            pending = self._trusted_pending(config, now)
            desired = build_task_spec(
                config,
                executable=str(self.install_layout.executable),
                user_id=self.user_id,
                pending_switch=pending,
            )
            inspection = inspect_task(desired, self.scheduler.read(desired.task_path))
            if inspection.valid:
                return self._check(
                    "task.definition",
                    HealthCategory.TASK,
                    HealthStatus.HEALTHY,
                    "The single Task Scheduler v2 definition is valid.",
                )
            return self._check(
                "task.definition",
                HealthCategory.TASK,
                HealthStatus.REPAIRABLE,
                "The scheduled task is missing or differs from the trusted definition.",
                "task.repair",
            )
        except Exception as exc:
            return self._check(
                "task.definition",
                HealthCategory.TASK,
                HealthStatus.ACTION_REQUIRED,
                f"Task inspection failed: {type(exc).__name__}: {exc}",
            )

    def _windows(self) -> HealthCheck:
        try:
            self.windows_probe()
            return self._check(
                "windows.theme-access",
                HealthCategory.WINDOWS,
                HealthStatus.HEALTHY,
                "Windows theme read access and bridge capability are available.",
            )
        except Exception as exc:
            return self._check(
                "windows.theme-access",
                HealthCategory.WINDOWS,
                HealthStatus.ACTION_REQUIRED,
                f"Windows theme capability is unavailable: {type(exc).__name__}: {exc}",
            )

    def _identity(self) -> HealthCheck:
        try:
            process_identity = self.process_identity_reader()
            expected_protocol = ProtocolRegistration(self.install_layout.executable)
            start_actual = self.shortcuts.read(self.shortcut_plan.start_menu.path)
            desktop_bytes = self.shortcuts.capture(self.shortcut_plan.desktop.path)
            desktop_actual = (
                self.shortcuts.read(self.shortcut_plan.desktop.path)
                if desktop_bytes is not None
                else None
            )
            valid = (
                process_identity == APP_USER_MODEL_ID
                and start_actual == self.shortcut_plan.start_menu
                and (
                    desktop_bytes is None
                    or desktop_actual == self.shortcut_plan.desktop
                )
                and self.protocol.read() == expected_protocol
            )
            if valid:
                return self._check(
                    "notification.identity",
                    HealthCategory.NOTIFICATION,
                    HealthStatus.HEALTHY,
                    "Process, shortcut, and protocol notification identity match.",
                )
            return self._check(
                "notification.identity",
                HealthCategory.NOTIFICATION,
                HealthStatus.REPAIRABLE,
                "Notification shortcut or URI protocol identity is missing or drifted.",
                "notification.identity-repair",
            )
        except Exception as exc:
            return self._check(
                "notification.identity",
                HealthCategory.NOTIFICATION,
                HealthStatus.REPAIRABLE,
                f"Notification identity could not be verified: "
                f"{type(exc).__name__}: {exc}",
                "notification.identity-repair",
            )

    def _notification_platform(self) -> HealthCheck:
        try:
            self.notification_probe()
            return self._check(
                "notification.platform",
                HealthCategory.NOTIFICATION,
                HealthStatus.HEALTHY,
                "Inbox WinRT notification capability is available.",
            )
        except Exception as exc:
            return self._check(
                "notification.platform",
                HealthCategory.NOTIFICATION,
                HealthStatus.ACTION_REQUIRED,
                f"Notification bridge or platform probe failed: "
                f"{type(exc).__name__}: {exc}",
            )

    def inspect(self) -> HealthReport:
        now = self.clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Health clock must include a UTC offset.")
        configuration, config = self._configuration()
        checks = (
            configuration,
            self._state(),
            self._profile("day"),
            self._profile("night"),
            self._payload(),
            self._data_write(),
            self._log_write(),
            self._task(config, now),
            self._windows(),
            self._identity(),
            self._notification_platform(),
        )
        return HealthReport(
            now.isoformat(timespec="seconds"),
            checks,
        )
