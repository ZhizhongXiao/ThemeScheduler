from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from theme_scheduler.config import AppConfig
from theme_scheduler.lifecycle import (
    InstalledAppRegistration,
    InstallLayout,
)
from theme_scheduler.protocol_registration import (
    ProtocolRegistration,
    RegistryTreeBackup,
)
from theme_scheduler.scheduler import (
    TaskDefinitionBackup,
    TaskSpec,
)
from theme_scheduler.system_integration import (
    CurrentUserIntegrationService,
    RegistryKeyBackup,
    ShortcutPlan,
    ShortcutSpec,
)
from theme_scheduler.system_integration_backup import (
    SystemIntegrationBackup,
    capture_system_integration,
    restore_system_integration,
)

USER_ID = r"DESKTOP-TEST\Example"


class MemoryRegistry:
    def __init__(
        self,
        backup: RegistryKeyBackup | None = None,
        *,
        write_error: bool = False,
        drift_after_write: bool = False,
        restore_error: bool = False,
    ) -> None:
        self.backup = backup
        self.write_error = write_error
        self.drift_after_write = drift_after_write
        self.restore_error = restore_error
        self.write_count = 0

    @staticmethod
    def encode(
        registration: InstalledAppRegistration,
    ) -> RegistryKeyBackup:
        return RegistryKeyBackup(
            tuple(
                sorted(
                    (
                        (
                            name,
                            value,
                            4 if isinstance(value, int) else 1,
                        )
                        for name, value in (registration.as_registry_values().items())
                    ),
                    key=lambda item: item[0].casefold(),
                )
            )
        )

    def capture(self) -> RegistryKeyBackup | None:
        return self.backup

    def read(self) -> InstalledAppRegistration | None:
        if self.backup is None:
            return None
        if self.drift_after_write and self.write_count:
            self.drift_after_write = False
            return None
        values = {name: data for name, data, _ in self.backup.values}
        try:
            return InstalledAppRegistration.from_registry_values(values)
        except Exception:
            return None

    def write(self, registration: InstalledAppRegistration) -> None:
        self.write_count += 1
        self.backup = self.encode(registration)
        if self.write_error:
            self.write_error = False
            raise OSError("registry write failed")

    def restore(self, backup: RegistryKeyBackup | None) -> None:
        if self.restore_error:
            raise OSError("registry restore failed")
        self.backup = backup


class MemoryShortcuts:
    def __init__(
        self,
        files: dict[Path, bytes] | None = None,
        *,
        write_error_at: int | None = None,
        restore_error: bool = False,
    ) -> None:
        self.files = dict(files or {})
        self.write_error_at = write_error_at
        self.restore_error = restore_error
        self.write_count = 0

    @staticmethod
    def encode(shortcut: ShortcutSpec) -> bytes:
        return json.dumps(
            shortcut.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")

    @staticmethod
    def decode(content: bytes) -> ShortcutSpec | None:
        try:
            payload = json.loads(content)
            return ShortcutSpec(
                path=Path(payload["path"]),
                target=Path(payload["target"]),
                arguments=payload["arguments"],
                working_directory=Path(payload["workingDirectory"]),
                description=payload["description"],
                icon_location=payload["iconLocation"],
                app_user_model_id=payload["appUserModelId"],
                toast_activator_clsid=payload["toastActivatorClsid"],
            )
        except Exception:
            return None

    def capture(self, path: Path) -> bytes | None:
        return self.files.get(path)

    def read(self, path: Path) -> ShortcutSpec | None:
        content = self.files.get(path)
        return None if content is None else self.decode(content)

    def write(self, shortcut: ShortcutSpec) -> None:
        self.write_count += 1
        self.files[shortcut.path] = self.encode(shortcut)
        if self.write_error_at == self.write_count:
            raise OSError("shortcut write failed")

    def restore(self, path: Path, backup: bytes | None) -> None:
        if self.restore_error:
            raise OSError("shortcut restore failed")
        if backup is None:
            self.files.pop(path, None)
        else:
            self.files[path] = backup


class MemoryTasks:
    def __init__(
        self,
        task: TaskSpec | None = None,
        *,
        register_error: bool = False,
    ) -> None:
        self.task = task
        self.register_error = register_error
        self.register_count = 0

    def read(self, task_path: str) -> TaskSpec | None:
        if self.task is None or self.task.task_path != task_path:
            return None
        return self.task

    def register(self, task: TaskSpec) -> None:
        self.register_count += 1
        self.task = task
        if self.register_error:
            self.register_error = False
            raise OSError("task registration failed")

    def capture(self, task_path: str) -> TaskDefinitionBackup | None:
        task = self.read(task_path)
        if task is None:
            return None
        return TaskDefinitionBackup(
            task_path,
            json.dumps(task.as_dict()),
            task.enabled,
        )

    def restore(self, backup: TaskDefinitionBackup) -> None:
        self.task = TaskSpec.from_dict(json.loads(backup.definition_xml))

    def delete(self, task_path: str) -> None:
        self.task = None


class MemoryProtocol:
    def __init__(
        self,
        backup: RegistryTreeBackup | None = None,
        *,
        write_error: bool = False,
    ) -> None:
        self.backup = backup
        self.registration = None
        self.write_error = write_error

    def capture(self):
        return self.backup

    def read(self):
        return self.registration

    def write(self, registration: ProtocolRegistration) -> None:
        self.registration = registration
        self.backup = RegistryTreeBackup(
            ("",),
            (("", "", registration.command, 1),),
        )
        if self.write_error:
            self.write_error = False
            raise OSError("protocol write failed")

    def restore(self, backup):
        self.backup = backup
        self.registration = None


class CurrentUserIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.layout = InstallLayout(
            root / "Programs" / "ThemeScheduler",
            root / "ThemeScheduler",
        )
        self.plan = ShortcutPlan.create(
            self.layout,
            programs_folder=root / "Start Menu" / "Programs",
            desktop_folder=root / "Desktop",
            desktop_enabled=True,
        )
        self.registration = InstalledAppRegistration.create(
            self.layout,
            version="0.1.0",
            publisher="ThemeScheduler",
            estimated_size_kib=1024,
        )
        self.config = AppConfig.defaults()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def service(
        self,
        registry: MemoryRegistry | None = None,
        shortcuts: MemoryShortcuts | None = None,
        tasks: MemoryTasks | None = None,
    ) -> tuple[
        CurrentUserIntegrationService,
        MemoryRegistry,
        MemoryShortcuts,
        MemoryTasks,
    ]:
        registry = registry or MemoryRegistry()
        shortcuts = shortcuts or MemoryShortcuts()
        tasks = tasks or MemoryTasks()
        return (
            CurrentUserIntegrationService(
                self.layout,
                registry,
                shortcuts,
                tasks,
            ),
            registry,
            shortcuts,
            tasks,
        )

    def apply(self, service: CurrentUserIntegrationService):
        return service.apply(
            registration=self.registration,
            shortcut_plan=self.plan,
            config=self.config,
            user_id=USER_ID,
        )

    def test_shortcut_plan_freezes_paths_and_product_target(self) -> None:
        self.assertEqual(
            self.plan.start_menu.path.parent.name,
            "ThemeScheduler",
        )
        self.assertEqual(self.plan.desktop.path.parent.name, "Desktop")
        self.assertEqual(
            self.plan.start_menu.target,
            self.layout.executable,
        )
        self.assertEqual(self.plan.start_menu.arguments, "")
        self.assertEqual(
            self.plan.start_menu.working_directory,
            self.layout.app,
        )

    def test_fresh_apply_creates_and_verifies_all_three_integrations(
        self,
    ) -> None:
        service, registry, shortcuts, tasks = self.service()

        outcome = self.apply(service)

        self.assertEqual(outcome.result, "changed")
        self.assertTrue(outcome.verified)
        self.assertEqual(registry.read(), self.registration)
        self.assertEqual(
            shortcuts.read(self.plan.start_menu.path),
            self.plan.start_menu,
        )
        self.assertEqual(
            shortcuts.read(self.plan.desktop.path),
            self.plan.desktop,
        )
        self.assertIsNotNone(tasks.task)

    def test_protocol_registration_is_committed_and_rolled_back_with_unit(
        self,
    ) -> None:
        registry = MemoryRegistry()
        shortcuts = MemoryShortcuts()
        tasks = MemoryTasks()
        protocol = MemoryProtocol()
        service = CurrentUserIntegrationService(
            self.layout,
            registry,
            shortcuts,
            tasks,
            protocol,
        )

        outcome = self.apply(service)

        self.assertEqual(outcome.result, "changed")
        self.assertTrue(outcome.protocol_changed)
        self.assertEqual(
            protocol.read(),
            ProtocolRegistration(self.layout.executable),
        )

        failing = MemoryProtocol(write_error=True)
        service = CurrentUserIntegrationService(
            self.layout,
            MemoryRegistry(),
            MemoryShortcuts(),
            MemoryTasks(),
            failing,
        )
        failed = self.apply(service)
        self.assertEqual(failed.result, "failed")
        self.assertTrue(failed.rollback_succeeded)
        self.assertIsNone(failing.capture())

    def test_repeated_apply_is_byte_stable_and_does_not_write(self) -> None:
        service, registry, shortcuts, tasks = self.service()
        self.apply(service)
        registry_writes = registry.write_count
        shortcut_writes = shortcuts.write_count
        task_writes = tasks.register_count

        outcome = self.apply(service)

        self.assertEqual(outcome.result, "no-change")
        self.assertFalse(outcome.as_dict()["windowsChanged"])
        self.assertEqual(registry.write_count, registry_writes)
        self.assertEqual(shortcuts.write_count, shortcut_writes)
        self.assertEqual(tasks.register_count, task_writes)

    def test_disabling_desktop_removes_even_malformed_managed_shortcut(
        self,
    ) -> None:
        disabled = replace(self.plan, desktop_enabled=False)
        shortcuts = MemoryShortcuts({disabled.desktop.path: b"stale shortcut"})
        service, _, shortcuts, _ = self.service(shortcuts=shortcuts)

        outcome = service.apply(
            registration=self.registration,
            shortcut_plan=disabled,
            config=self.config,
            user_id=USER_ID,
        )

        self.assertEqual(outcome.result, "changed")
        self.assertIsNone(shortcuts.capture(disabled.desktop.path))

    def test_registry_failure_rolls_back_partially_written_shortcuts(
        self,
    ) -> None:
        registry = MemoryRegistry(write_error=True)
        service, registry, shortcuts, tasks = self.service(registry=registry)

        outcome = self.apply(service)

        self.assertEqual(outcome.result, "failed")
        self.assertTrue(outcome.rollback_succeeded)
        self.assertIsNone(registry.capture())
        self.assertEqual(shortcuts.files, {})
        self.assertIsNone(tasks.task)

    def test_task_failure_restores_registry_and_preexisting_raw_shortcuts(
        self,
    ) -> None:
        prior_registry = RegistryKeyBackup((("LegacyValue", "keep exactly", 1),))
        prior_start = b"unparseable legacy shortcut bytes"
        registry = MemoryRegistry(prior_registry)
        shortcuts = MemoryShortcuts({self.plan.start_menu.path: prior_start})
        tasks = MemoryTasks(register_error=True)
        service, registry, shortcuts, tasks = self.service(
            registry,
            shortcuts,
            tasks,
        )

        outcome = self.apply(service)

        self.assertEqual(outcome.result, "failed")
        self.assertTrue(outcome.rollback_succeeded)
        self.assertEqual(registry.capture(), prior_registry)
        self.assertEqual(
            shortcuts.capture(self.plan.start_menu.path),
            prior_start,
        )
        self.assertIsNone(shortcuts.capture(self.plan.desktop.path))
        self.assertIsNone(tasks.task)

    def test_registration_readback_drift_rolls_everything_back(self) -> None:
        registry = MemoryRegistry(drift_after_write=True)
        service, registry, shortcuts, tasks = self.service(registry=registry)

        outcome = self.apply(service)

        self.assertEqual(outcome.result, "failed")
        self.assertTrue(outcome.rollback_succeeded)
        self.assertIsNone(registry.capture())
        self.assertEqual(shortcuts.files, {})
        self.assertIsNone(tasks.task)

    def test_rollback_failure_is_reported_as_partial(self) -> None:
        registry = MemoryRegistry(
            write_error=True,
            restore_error=True,
        )
        service, _, _, _ = self.service(registry=registry)

        outcome = self.apply(service)

        self.assertEqual(outcome.result, "partial")
        self.assertFalse(outcome.rollback_succeeded)
        self.assertIn("Rollback was incomplete", outcome.message)

    def test_foreign_layout_shortcut_is_rejected_before_capture(self) -> None:
        foreign_layout = InstallLayout(
            Path(self.temporary.name) / "Other" / "Programs" / "ThemeScheduler",
            Path(self.temporary.name) / "Other" / "ThemeScheduler",
        )
        foreign_plan = ShortcutPlan.create(
            foreign_layout,
            programs_folder=self.plan.start_menu.path.parents[1],
            desktop_folder=self.plan.desktop.path.parent,
            desktop_enabled=True,
        )
        service, registry, shortcuts, tasks = self.service()

        with self.assertRaisesRegex(ValueError, "does not belong"):
            service.apply(
                registration=self.registration,
                shortcut_plan=foreign_plan,
                config=self.config,
                user_id=USER_ID,
            )

        self.assertIsNone(registry.capture())
        self.assertEqual(shortcuts.files, {})
        self.assertIsNone(tasks.task)

    def test_acceptance_backup_round_trip_preserves_binary_and_absence(
        self,
    ) -> None:
        registry = MemoryRegistry(
            RegistryKeyBackup(
                (
                    ("", b"\x00\xff", 3),
                    ("Legacy", ["a", "b"], 7),
                )
            )
        )
        shortcuts = MemoryShortcuts({self.plan.start_menu.path: b"\x00original-link"})
        tasks = MemoryTasks()
        backup = capture_system_integration(
            registry,
            shortcuts,
            tasks,
            self.plan.managed_paths,
        )
        backup_path = Path(self.temporary.name) / "integration-backup.json"

        backup.save(backup_path)
        loaded = SystemIntegrationBackup.load(backup_path)

        self.assertTrue(loaded.state_equals(backup))
        self.assertEqual(
            loaded.registration,
            registry.capture(),
        )
        desktop = next(
            item for item in loaded.shortcuts if item.path == self.plan.desktop.path
        )
        self.assertIsNone(desktop.content)

    def test_acceptance_backup_restores_pretest_state(self) -> None:
        service, registry, shortcuts, tasks = self.service()
        backup = capture_system_integration(
            registry,
            shortcuts,
            tasks,
            self.plan.managed_paths,
        )
        self.apply(service)

        outcome = restore_system_integration(
            backup,
            registry,
            shortcuts,
            tasks,
        )

        self.assertEqual(outcome.result, "restored")
        self.assertTrue(outcome.verified)
        self.assertIsNone(registry.capture())
        self.assertEqual(shortcuts.files, {})
        self.assertIsNone(tasks.task)

    def test_acceptance_restore_reports_each_incomplete_area(self) -> None:
        registry = MemoryRegistry()
        shortcuts = MemoryShortcuts()
        tasks = MemoryTasks()
        backup = capture_system_integration(
            registry,
            shortcuts,
            tasks,
            self.plan.managed_paths,
        )
        self.apply(
            CurrentUserIntegrationService(
                self.layout,
                registry,
                shortcuts,
                tasks,
            )
        )
        registry.restore_error = True
        shortcuts.restore_error = True

        outcome = restore_system_integration(
            backup,
            registry,
            shortcuts,
            tasks,
        )

        self.assertEqual(outcome.result, "partial")
        self.assertTrue(outcome.task_restored)
        self.assertFalse(outcome.registration_restored)
        self.assertFalse(outcome.shortcuts_restored)
        self.assertGreaterEqual(len(outcome.errors), 3)


if __name__ == "__main__":
    unittest.main()
