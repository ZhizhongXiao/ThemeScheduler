from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from theme_scheduler.lifecycle import InstallLayout
from theme_scheduler.notification_identity_service import (
    NotificationIdentityRepairService,
)
from theme_scheduler.protocol_registration import (
    ProtocolRegistration,
    RegistryTreeBackup,
)
from theme_scheduler.storage import UserDataLayout
from theme_scheduler.system_integration import ShortcutPlan

UTC8 = timezone(timedelta(hours=8))


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 26, 15, 0, tzinfo=UTC8)


class FakeLock:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.released = 0

    def acquire(self) -> bool:
        return self.available

    def release(self) -> None:
        self.released += 1


class MemoryLog:
    def __init__(self) -> None:
        self.events = []

    def append(self, event) -> None:
        self.events.append(event)


class MemoryShortcuts:
    def __init__(
        self,
        plan: ShortcutPlan,
        *,
        drift_start: bool = False,
        desktop_exists: bool = False,
        fail_write: bool = False,
    ) -> None:
        self.plan = plan
        self.fail_write = fail_write
        self.raw = {
            plan.start_menu.path: (b"drift-start" if drift_start else b"start"),
        }
        self.values = {
            plan.start_menu.path: (None if drift_start else plan.start_menu),
        }
        if desktop_exists:
            self.raw[plan.desktop.path] = b"desktop"
            self.values[plan.desktop.path] = plan.desktop

    def capture(self, path: Path):
        return self.raw.get(path)

    def read(self, path: Path):
        return self.values.get(path)

    def write(self, shortcut) -> None:
        if self.fail_write:
            raise OSError("injected shortcut failure")
        self.values[shortcut.path] = shortcut
        self.raw[shortcut.path] = (
            b"start" if shortcut.path == self.plan.start_menu.path else b"desktop"
        )

    def restore(self, path: Path, backup) -> None:
        if backup is None:
            self.raw.pop(path, None)
            self.values.pop(path, None)
            return
        self.raw[path] = backup
        if backup == b"start":
            self.values[path] = self.plan.start_menu
        elif backup == b"desktop":
            self.values[path] = self.plan.desktop
        else:
            self.values[path] = None


class MemoryProtocol:
    BACKUP = RegistryTreeBackup(
        ("",),
        (("", "", "drift", 1),),
    )

    def __init__(
        self,
        expected: ProtocolRegistration,
        *,
        drift: bool = False,
    ) -> None:
        self.expected = expected
        self.registration = None if drift else expected

    def capture(self):
        return (
            self.BACKUP
            if self.registration is None
            else RegistryTreeBackup(
                ("",),
                (("", "", "expected", 1),),
            )
        )

    def read(self):
        return self.registration

    def write(self, registration) -> None:
        self.registration = registration

    def restore(self, backup) -> None:
        self.registration = self.expected if backup != self.BACKUP else None


class NotificationIdentityRepairTests(unittest.TestCase):
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
        self.plan = ShortcutPlan.create(
            self.install,
            programs_folder=root / "Start Menu" / "Programs",
            desktop_folder=root / "Desktop",
            desktop_enabled=False,
        )
        self.expected_protocol = ProtocolRegistration(self.install.executable)

    def service(
        self,
        shortcuts: MemoryShortcuts,
        protocol: MemoryProtocol,
        *,
        lock: FakeLock | None = None,
    ) -> NotificationIdentityRepairService:
        return NotificationIdentityRepairService(
            self.layout,
            lock or FakeLock(),
            shortcuts,
            self.plan,
            protocol,
            event_log=MemoryLog(),
            clock=FixedClock(),
        )

    def test_repairs_start_identity_and_protocol_without_creating_desktop(
        self,
    ) -> None:
        shortcuts = MemoryShortcuts(self.plan, drift_start=True)
        protocol = MemoryProtocol(self.expected_protocol, drift=True)

        outcome = self.service(shortcuts, protocol).repair()

        self.assertEqual(outcome.result, "changed")
        self.assertTrue(outcome.shortcuts_changed)
        self.assertTrue(outcome.protocol_changed)
        self.assertEqual(
            shortcuts.read(self.plan.start_menu.path),
            self.plan.start_menu,
        )
        self.assertIsNone(shortcuts.capture(self.plan.desktop.path))
        self.assertEqual(protocol.read(), self.expected_protocol)

    def test_repeated_repair_is_no_change(self) -> None:
        shortcuts = MemoryShortcuts(self.plan)
        protocol = MemoryProtocol(self.expected_protocol)

        outcome = self.service(shortcuts, protocol).repair()

        self.assertEqual(outcome.result, "no-change")
        self.assertFalse(outcome.shortcuts_changed)
        self.assertFalse(outcome.protocol_changed)
        self.assertTrue(outcome.verified)

    def test_failure_restores_exact_shortcut_and_protocol_backup(self) -> None:
        shortcuts = MemoryShortcuts(
            self.plan,
            drift_start=True,
            fail_write=True,
        )
        protocol = MemoryProtocol(self.expected_protocol, drift=True)
        shortcut_before = shortcuts.capture(self.plan.start_menu.path)
        protocol_before = protocol.capture()

        outcome = self.service(shortcuts, protocol).repair()

        self.assertEqual(outcome.result, "fatal-failure")
        self.assertTrue(outcome.rollback_attempted)
        self.assertTrue(outcome.rollback_succeeded)
        self.assertEqual(
            shortcuts.capture(self.plan.start_menu.path),
            shortcut_before,
        )
        self.assertEqual(protocol.capture(), protocol_before)

    def test_busy_lock_causes_no_capture_or_write(self) -> None:
        shortcuts = MemoryShortcuts(self.plan, drift_start=True)
        protocol = MemoryProtocol(self.expected_protocol, drift=True)

        outcome = self.service(
            shortcuts,
            protocol,
            lock=FakeLock(False),
        ).repair()

        self.assertEqual(outcome.result, "already-running")
        self.assertEqual(
            shortcuts.capture(self.plan.start_menu.path),
            b"drift-start",
        )


if __name__ == "__main__":
    unittest.main()
