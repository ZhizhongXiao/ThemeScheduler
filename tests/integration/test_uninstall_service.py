from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from theme_scheduler.lifecycle import InstallLayout
from theme_scheduler.uninstall_contracts import (
    AppearanceChoice,
    UninstallOptions,
    UninstallRequest,
)
from theme_scheduler.uninstall_service import (
    IndependentUninstallService,
    RecordingSelfCleanupScheduler,
)

STAMP = "2026-07-25T14:00:00+08:00"
TRANSACTION = "uninstall-" + "d" * 32


class FixedClock:
    def now(self) -> datetime:
        return datetime.fromisoformat(STAMP)


class FakeLock:
    def __init__(
        self,
        *,
        acquired: bool = True,
        release_error: Exception | None = None,
    ) -> None:
        self.acquired = acquired
        self.release_error = release_error
        self.acquire_calls = 0
        self.release_calls = 0

    def acquire(self) -> bool:
        self.acquire_calls += 1
        return self.acquired

    def release(self) -> None:
        self.release_calls += 1
        if self.release_error is not None:
            raise self.release_error


class FakeTasks:
    def __init__(
        self,
        *,
        exists: bool = True,
        delete_error: Exception | None = None,
    ) -> None:
        self.value = object() if exists else None
        self.delete_error = delete_error
        self.delete_calls = 0

    def read(self, task_path: str):
        return self.value

    def delete(self, task_path: str) -> None:
        self.delete_calls += 1
        if self.delete_error is not None:
            raise self.delete_error
        self.value = None


class FakeRegistry:
    def __init__(self, exists: bool = True) -> None:
        self.value = object() if exists else None

    def capture(self):
        return self.value

    def restore(self, backup) -> None:
        self.value = backup


class FakeShortcuts:
    def __init__(self, paths: tuple[Path, ...]) -> None:
        self.values = dict.fromkeys(paths, b"shortcut")

    def capture(self, path: Path):
        return self.values.get(path)

    def restore(self, path: Path, backup) -> None:
        if backup is None:
            self.values.pop(path, None)
        else:
            self.values[path] = backup


@dataclass
class AppearanceResult:
    appearance_applied: bool | None = True
    windows_verified: bool = True
    system_mode_preserved: bool | None = True
    paused_after: bool | None = True
    message: str = "appearance result"


class FakeProcessGuard:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[Path] = []

    def ensure_idle(self, executable: Path) -> None:
        self.calls.append(executable)
        if self.error is not None:
            raise self.error


class FailingCleanupScheduler:
    def schedule(self, workspace: Path, *, wait_pid: int) -> None:
        raise OSError("cleanup launch failed")


class IndependentUninstallServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name).resolve()
        self.layout = InstallLayout(
            root / "Programs" / "ThemeScheduler",
            root / "ThemeScheduler",
        )
        self.workspace = root / "Temp" / "ThemeScheduler" / TRANSACTION
        self.shortcuts = (
            root / "Start Menu" / "ThemeScheduler" / "ThemeScheduler.lnk",
            root / "Desktop" / "ThemeScheduler.lnk",
        )

    def populate(self, *, damaged: str | None = None) -> None:
        for directory in (
            self.layout.app / "_internal",
            self.layout.maintenance,
            self.layout.metadata,
            self.layout.data_root / "profiles",
            self.layout.data_root / "backup",
            self.layout.data_root / "logs",
            self.layout.data_root / "runtime",
            self.layout.data_root / "WebView2",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        (self.layout.executable).write_bytes(b"main")
        (self.layout.app / "_internal" / "python.dll").write_bytes(b"runtime")
        self.layout.uninstaller.write_bytes(b"independent")
        self.layout.installation_record.write_text(
            "{}",
            encoding="utf-8",
        )
        self.layout.payload_manifest.write_text(
            "{}",
            encoding="utf-8",
        )
        for name in ("config.json", "state.json"):
            (self.layout.data_root / name).write_text(
                name,
                encoding="utf-8",
            )
        (self.layout.data_root / "profiles" / "day.json").write_text(
            "profile",
            encoding="utf-8",
        )
        (self.layout.data_root / "backup" / "install.theme").write_text(
            "theme",
            encoding="utf-8",
        )
        (self.layout.data_root / "logs" / "events.jsonl").write_text(
            "log",
            encoding="utf-8",
        )
        (self.layout.data_root / "runtime" / "transaction.json").write_text(
            "runtime",
            encoding="utf-8",
        )
        (self.layout.data_root / "WebView2" / "cache.bin").write_bytes(b"cache")
        (self.layout.data_root / "unknown.bin").write_bytes(b"unknown")
        if damaged == "main":
            self.layout.executable.unlink()
        elif damaged == "internal":
            (self.layout.app / "_internal" / "python.dll").unlink()
        elif damaged == "bridge":
            (self.layout.app / "_internal" / "bridge.ps1").write_text(
                "corrupt",
                encoding="utf-8",
            )
        elif damaged == "manifest":
            self.layout.payload_manifest.write_text(
                "not-json",
                encoding="utf-8",
            )

    def request(
        self,
        *,
        appearance: AppearanceChoice = AppearanceChoice.KEEP,
        keep_config: bool = True,
        keep_logs: bool = True,
    ) -> UninstallRequest:
        return UninstallRequest(
            transaction_id=TRANSACTION,
            created_at=STAMP,
            program_root=str(self.layout.program_root),
            data_root=str(self.layout.data_root),
            launcher_process_id=1234,
            source_uninstaller_sha256="e" * 64,
            cleanup_script_sha256="d" * 64,
            authorization_token="f" * 64,
            options=UninstallOptions(
                appearance,
                keep_config,
                keep_logs,
            ),
        )

    def service(
        self,
        request: UninstallRequest,
        *,
        tasks: FakeTasks | None = None,
        registry: FakeRegistry | None = None,
        protocol: FakeRegistry | None = None,
        shortcuts: FakeShortcuts | None = None,
        appearance=None,
        process_guard: FakeProcessGuard | None = None,
        cleanup=None,
        lifecycle_lock: FakeLock | None = None,
        auto_lock: FakeLock | None = None,
    ) -> IndependentUninstallService:
        return IndependentUninstallService(
            request,
            self.workspace,
            lifecycle_lock or FakeLock(),
            auto_lock or FakeLock(),
            registry or FakeRegistry(),
            shortcuts or FakeShortcuts(self.shortcuts),
            tasks or FakeTasks(),
            self.shortcuts,
            protocol=protocol,
            appearance_restorer=appearance,
            process_guard=process_guard,
            self_cleanup=cleanup or RecordingSelfCleanupScheduler(),
            clock=FixedClock(),
            process_id=1234,
        )

    def test_keep_data_removes_program_and_caches_only(self) -> None:
        self.populate()
        cleanup = RecordingSelfCleanupScheduler()

        outcome = self.service(
            self.request(),
            cleanup=cleanup,
        ).run()

        self.assertEqual(outcome.result, "completed")
        self.assertTrue(outcome.verified)
        self.assertFalse(self.layout.program_root.exists())
        self.assertFalse((self.layout.data_root / "runtime").exists())
        self.assertFalse((self.layout.data_root / "WebView2").exists())
        self.assertTrue((self.layout.data_root / "config.json").is_file())
        self.assertTrue((self.layout.data_root / "profiles").is_dir())
        self.assertTrue((self.layout.data_root / "backup").is_dir())
        self.assertTrue((self.layout.data_root / "logs").is_dir())
        self.assertTrue((self.layout.data_root / "unknown.bin").is_file())
        self.assertEqual(cleanup.calls, [(self.workspace, 1234)])

    def test_uninstall_removes_notification_protocol_registration(
        self,
    ) -> None:
        self.populate()
        protocol = FakeRegistry()

        outcome = self.service(
            self.request(),
            protocol=protocol,
        ).run()

        self.assertEqual(outcome.result, "completed")
        self.assertIsNone(protocol.capture())
        self.assertTrue(outcome.registration_removed)

    def test_restore_and_remove_all_deletes_whole_data_root(self) -> None:
        self.populate()
        appearance_calls = []

        outcome = self.service(
            self.request(
                appearance=AppearanceChoice.RESTORE,
                keep_config=False,
                keep_logs=False,
            ),
            appearance=lambda: appearance_calls.append(True) or AppearanceResult(),
        ).run()

        self.assertEqual(outcome.result, "completed")
        self.assertTrue(outcome.appearance_restored)
        self.assertEqual(appearance_calls, [True])
        self.assertFalse(self.layout.data_root.exists())

    def test_task_failure_stops_before_other_mutations(self) -> None:
        self.populate()
        tasks = FakeTasks(delete_error=OSError("task denied"))
        registry = FakeRegistry()
        shortcuts = FakeShortcuts(self.shortcuts)

        outcome = self.service(
            self.request(),
            tasks=tasks,
            registry=registry,
            shortcuts=shortcuts,
        ).run()

        self.assertEqual(outcome.result, "partial")
        self.assertEqual(outcome.completed_steps, ())
        self.assertIsNotNone(registry.capture())
        self.assertTrue(all(shortcuts.capture(path) for path in self.shortcuts))
        self.assertTrue(self.layout.program_root.is_dir())

    def test_appearance_failure_keeps_program_and_integration(self) -> None:
        self.populate()
        registry = FakeRegistry()
        shortcuts = FakeShortcuts(self.shortcuts)

        outcome = self.service(
            self.request(appearance=AppearanceChoice.RESTORE),
            registry=registry,
            shortcuts=shortcuts,
            appearance=lambda: AppearanceResult(windows_verified=False),
        ).run()

        self.assertEqual(outcome.result, "partial")
        self.assertEqual(outcome.completed_steps, ("task-removed",))
        self.assertTrue(self.layout.program_root.is_dir())
        self.assertIsNotNone(registry.capture())
        self.assertTrue(all(shortcuts.capture(path) for path in self.shortcuts))

    def test_transient_appearance_failure_is_retried_once(self) -> None:
        self.populate()
        results = iter(
            (
                AppearanceResult(
                    appearance_applied=None,
                    windows_verified=False,
                    message="first verification was transient",
                ),
                AppearanceResult(message="retry verified"),
            )
        )
        calls = []

        def restore_appearance() -> AppearanceResult:
            calls.append(True)
            return next(results)

        outcome = self.service(
            self.request(
                appearance=AppearanceChoice.RESTORE,
                keep_config=False,
                keep_logs=False,
            ),
            appearance=restore_appearance,
        ).run()

        self.assertEqual(outcome.result, "completed")
        self.assertEqual(calls, [True, True])
        self.assertTrue(outcome.appearance_restored)
        self.assertFalse(self.layout.data_root.exists())

    def test_persistent_appearance_failure_stops_after_one_retry(self) -> None:
        self.populate()
        calls = []

        def restore_appearance() -> AppearanceResult:
            calls.append(True)
            return AppearanceResult(
                appearance_applied=None,
                windows_verified=False,
                message=f"attempt {len(calls)} failed",
            )

        outcome = self.service(
            self.request(appearance=AppearanceChoice.RESTORE),
            appearance=restore_appearance,
        ).run()

        self.assertEqual(outcome.result, "partial")
        self.assertEqual(calls, [True, True])
        self.assertIn("First attempt: attempt 1 failed", outcome.message)
        self.assertIn("Retry: attempt 2 failed", outcome.message)
        self.assertTrue(self.layout.program_root.exists())

    def test_unsafe_pause_state_is_not_retried(self) -> None:
        self.populate()
        calls = []

        def restore_appearance() -> AppearanceResult:
            calls.append(True)
            return AppearanceResult(
                appearance_applied=None,
                windows_verified=False,
                paused_after=None,
                message="pause state is unknown",
            )

        outcome = self.service(
            self.request(appearance=AppearanceChoice.RESTORE),
            appearance=restore_appearance,
        ).run()

        self.assertEqual(outcome.result, "partial")
        self.assertEqual(calls, [True])
        self.assertIn("pause state is unknown", outcome.message)
        self.assertTrue(self.layout.program_root.exists())

    def test_running_process_stops_before_program_deletion(self) -> None:
        self.populate()
        guard = FakeProcessGuard(OSError("main still running"))

        outcome = self.service(
            self.request(),
            process_guard=guard,
        ).run()

        self.assertEqual(outcome.result, "partial")
        self.assertEqual(
            outcome.completed_steps,
            (
                "task-removed",
                "appearance-handled",
                "shortcuts-removed",
                "registration-removed",
            ),
        )
        self.assertTrue(self.layout.program_root.exists())

    def test_self_cleanup_failure_can_resume_from_journal(self) -> None:
        self.populate()
        request = self.request(
            keep_config=False,
            keep_logs=False,
        )

        first = self.service(
            request,
            cleanup=FailingCleanupScheduler(),
        ).run()
        self.assertEqual(first.result, "partial")
        self.assertFalse(self.layout.program_root.exists())
        self.assertFalse(self.layout.data_root.exists())

        cleanup = RecordingSelfCleanupScheduler()
        second = self.service(
            request,
            tasks=FakeTasks(exists=False),
            registry=FakeRegistry(exists=False),
            shortcuts=FakeShortcuts(()),
            cleanup=cleanup,
        ).run()

        self.assertEqual(second.result, "completed")
        self.assertEqual(cleanup.calls, [(self.workspace, 1234)])

    def test_lock_release_failure_does_not_schedule_self_cleanup(self) -> None:
        self.populate()
        cleanup = RecordingSelfCleanupScheduler()

        outcome = self.service(
            self.request(
                keep_config=False,
                keep_logs=False,
            ),
            cleanup=cleanup,
            auto_lock=FakeLock(release_error=OSError("release failed")),
        ).run()

        self.assertEqual(outcome.result, "partial")
        self.assertEqual(cleanup.calls, [])

    def test_reparse_point_refusal_precedes_recursive_delete(self) -> None:
        self.populate()
        dangerous = self.layout.program_root / "unknown-link"
        dangerous.write_text("link", encoding="utf-8")

        with patch(
            "theme_scheduler.uninstall_service._is_reparse_point",
            side_effect=lambda path: path == dangerous,
        ):
            outcome = self.service(self.request()).run()

        self.assertEqual(outcome.result, "partial")
        self.assertTrue(self.layout.program_root.exists())

    def test_damage_matrix_does_not_depend_on_main_payload(self) -> None:
        for damage in ("main", "internal", "bridge", "manifest"):
            with self.subTest(damage=damage):
                self.temporary.cleanup()
                self.setUp()
                self.populate(damaged=damage)

                outcome = self.service(
                    self.request(
                        keep_config=False,
                        keep_logs=False,
                    )
                ).run()

                self.assertEqual(outcome.result, "completed")
                self.assertFalse(self.layout.program_root.exists())


if __name__ == "__main__":
    unittest.main()
