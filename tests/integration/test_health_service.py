from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from theme_scheduler.accent_profile import (
    AccentProfile,
    AccentProfileStore,
)
from theme_scheduler.config import AppConfig, ConfigStore
from theme_scheduler.health_contracts import HealthStatus
from theme_scheduler.health_service import HealthService
from theme_scheduler.lifecycle import InstallLayout
from theme_scheduler.notification_contracts import (
    APP_USER_MODEL_ID,
)
from theme_scheduler.protocol_registration import (
    ProtocolRegistration,
)
from theme_scheduler.scheduler import (
    TaskDefinitionBackup,
    TaskSpec,
    build_task_spec,
)
from theme_scheduler.state import AppState, StateStore
from theme_scheduler.storage import UserDataLayout
from theme_scheduler.system_integration import (
    ShortcutPlan,
)

UTC8 = timezone(timedelta(hours=8))
NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC8)
USER_ID = "S-1-5-21-1000-1000-1000-1001"


class FixedClock:
    def now(self) -> datetime:
        return NOW


class MemoryTask:
    def __init__(self, task: TaskSpec | None) -> None:
        self.task = task

    def read(self, task_path: str):
        if self.task is None or self.task.task_path != task_path:
            return None
        return self.task

    def register(self, task: TaskSpec) -> None:
        self.task = task

    def capture(self, task_path: str):
        if self.task is None:
            return None
        return TaskDefinitionBackup(
            task_path,
            json.dumps(self.task.as_dict()),
            self.task.enabled,
        )

    def restore(self, backup: TaskDefinitionBackup) -> None:
        self.task = TaskSpec.from_dict(json.loads(backup.definition_xml))

    def delete(self, task_path: str) -> None:
        self.task = None


class MemoryShortcuts:
    def __init__(self, plan: ShortcutPlan) -> None:
        self.values = {
            plan.start_menu.path: plan.start_menu,
        }

    def capture(self, path: Path):
        return b"shortcut" if path in self.values else None

    def read(self, path: Path):
        return self.values.get(path)

    def write(self, shortcut) -> None:
        self.values[shortcut.path] = shortcut

    def restore(self, path: Path, backup) -> None:
        if backup is None:
            self.values.pop(path, None)


class MemoryProtocol:
    def __init__(self, registration: ProtocolRegistration | None) -> None:
        self.registration = registration

    def capture(self):
        return object() if self.registration is not None else None

    def read(self):
        return self.registration

    def write(self, registration) -> None:
        self.registration = registration

    def restore(self, backup) -> None:
        if backup is None:
            self.registration = None


class HealthServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name).resolve()
        self.install = InstallLayout(
            root / "Programs" / "ThemeScheduler",
            root / "ThemeScheduler",
        )
        self.layout = UserDataLayout(self.install.data_root)
        self.layout.ensure_directories()
        self.config = AppConfig.defaults()
        ConfigStore(self.layout.config).initialize(self.config)
        StateStore(self.layout.state).initialize(AppState.initial())
        for profile, color in (
            ("day", 0xC4744DA9),
            ("night", 0xC4FFB900),
        ):
            AccentProfileStore(self.layout.profile_path(profile), profile).create(
                AccentProfile(
                    profile,
                    "2026-07-26T10:00:00+08:00",
                    False,
                    color,
                    "26200",
                )
            )
        programs = root / "Start Menu" / "Programs"
        desktop = root / "Desktop"
        self.plan = ShortcutPlan.create(
            self.install,
            programs_folder=programs,
            desktop_folder=desktop,
            desktop_enabled=False,
        )
        self.task = MemoryTask(
            build_task_spec(
                self.config,
                executable=str(self.install.executable),
                user_id=USER_ID,
            )
        )
        self.shortcuts = MemoryShortcuts(self.plan)
        self.protocol = MemoryProtocol(ProtocolRegistration(self.install.executable))

    def service(
        self,
        *,
        payload_error: Exception | None = None,
        notification_error: Exception | None = None,
        identity: str = APP_USER_MODEL_ID,
    ) -> HealthService:
        def payload(_layout: InstallLayout) -> None:
            if payload_error is not None:
                raise payload_error

        def notification() -> None:
            if notification_error is not None:
                raise notification_error

        return HealthService(
            self.layout,
            self.install,
            self.task,
            self.shortcuts,
            self.plan,
            self.protocol,
            user_id=USER_ID,
            windows_probe=lambda: None,
            notification_probe=notification,
            process_identity_reader=lambda: identity,
            payload_verifier=payload,
            clock=FixedClock(),
        )

    def test_healthy_report_covers_frozen_matrix_and_cleans_probe(self) -> None:
        report = self.service().inspect()

        self.assertIs(report.status, HealthStatus.HEALTHY)
        self.assertEqual(len(report.checks), 11)
        self.assertFalse((self.layout.runtime / "health").exists())
        self.assertFalse(self.layout.event_log.exists())
        self.assertEqual(
            {check.check_id for check in report.checks},
            {
                "configuration.config",
                "data.state",
                "data.profile.day",
                "data.profile.night",
                "files.payload",
                "permissions.data-write",
                "permissions.log-write",
                "task.definition",
                "windows.theme-access",
                "notification.identity",
                "notification.platform",
            },
        )

    def test_task_and_identity_drift_are_explicitly_repairable(self) -> None:
        self.task.task = None
        self.protocol.registration = None

        report = self.service().inspect()
        checks = {check.check_id: check for check in report.checks}

        self.assertIs(report.status, HealthStatus.REPAIRABLE)
        self.assertEqual(checks["task.definition"].repair_action, "task.repair")
        self.assertEqual(
            checks["notification.identity"].repair_action,
            "notification.identity-repair",
        )

    def test_untrusted_data_payload_and_platform_require_user_action(
        self,
    ) -> None:
        self.layout.config.write_text("not-json", encoding="utf-8")

        report = self.service(
            payload_error=OSError("payload mismatch"),
            notification_error=OSError("bridge missing"),
        ).inspect()
        checks = {check.check_id: check for check in report.checks}

        self.assertIs(report.status, HealthStatus.ACTION_REQUIRED)
        self.assertIs(
            checks["configuration.config"].status,
            HealthStatus.ACTION_REQUIRED,
        )
        self.assertIs(
            checks["files.payload"].status,
            HealthStatus.ACTION_REQUIRED,
        )
        self.assertIs(
            checks["notification.platform"].status,
            HealthStatus.ACTION_REQUIRED,
        )
        self.assertIs(
            checks["task.definition"].status,
            HealthStatus.ACTION_REQUIRED,
        )


if __name__ == "__main__":
    unittest.main()
