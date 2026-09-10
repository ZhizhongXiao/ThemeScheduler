from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from theme_scheduler.lifecycle import InstallLayout
from theme_scheduler.uninstall_contracts import (
    UNINSTALL_STEPS,
    AppearanceChoice,
    UninstallContractError,
    UninstallJournal,
    UninstallJournalStore,
    UninstallOptions,
    UninstallRequest,
    build_uninstall_plan,
)

STAMP = "2026-07-25T14:00:00+08:00"
TRANSACTION = "uninstall-" + "a" * 32


class UninstallContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.layout = InstallLayout(
            (root / "Programs" / "ThemeScheduler").resolve(),
            (root / "ThemeScheduler").resolve(),
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
            source_uninstaller_sha256="b" * 64,
            cleanup_script_sha256="d" * 64,
            authorization_token="c" * 64,
            options=UninstallOptions(
                appearance,
                keep_config,
                keep_logs,
            ),
        )

    def test_request_round_trip_and_strict_fields(self) -> None:
        request = self.request()

        self.assertEqual(
            UninstallRequest.from_dict(request.as_dict()),
            request,
        )
        malformed = request.as_dict()
        malformed["extra"] = True
        with self.assertRaises(UninstallContractError):
            UninstallRequest.from_dict(malformed)
        with self.assertRaises(UninstallContractError):
            replace(request, authorization_token="short")

    def test_keep_everything_only_removes_caches_and_preserves_backup(self) -> None:
        plan = build_uninstall_plan(
            self.layout,
            UninstallOptions(AppearanceChoice.KEEP, True, True),
        )

        self.assertFalse(plan.remove_entire_data_root)
        self.assertTrue(plan.unknown_data_preserved)
        self.assertEqual(
            set(plan.data_delete_targets),
            {
                self.layout.data_root / "runtime",
                self.layout.data_root / "WebView2",
            },
        )
        self.assertIn(
            self.layout.data_root / "backup",
            plan.data_preserve_targets,
        )
        self.assertIn(
            self.layout.data_root / "initial-setup.json",
            plan.data_preserve_targets,
        )

    def test_restore_deletes_backup_even_when_config_is_preserved(self) -> None:
        plan = build_uninstall_plan(
            self.layout,
            UninstallOptions(AppearanceChoice.RESTORE, True, True),
        )

        self.assertIn(
            self.layout.data_root / "backup",
            plan.data_delete_targets,
        )
        self.assertNotIn(
            self.layout.data_root / "backup",
            plan.data_preserve_targets,
        )

    def test_remove_all_uses_whole_data_root(self) -> None:
        plan = build_uninstall_plan(
            self.layout,
            UninstallOptions(AppearanceChoice.KEEP, False, False),
        )

        self.assertTrue(plan.remove_entire_data_root)
        self.assertFalse(plan.unknown_data_preserved)
        self.assertEqual(
            plan.data_delete_targets,
            (self.layout.data_root,),
        )
        self.assertEqual(plan.data_preserve_targets, ())

    def test_partial_preservation_keeps_unknown_data(self) -> None:
        plan = build_uninstall_plan(
            self.layout,
            UninstallOptions(AppearanceChoice.KEEP, False, True),
        )

        self.assertFalse(plan.remove_entire_data_root)
        self.assertTrue(plan.unknown_data_preserved)
        self.assertIn(
            self.layout.data_root / "logs",
            plan.data_preserve_targets,
        )
        self.assertIn(
            self.layout.data_root / "profiles",
            plan.data_delete_targets,
        )
        self.assertIn(
            self.layout.data_root / "initial-setup.json",
            plan.data_delete_targets,
        )

    def test_journal_enforces_order_and_round_trip(self) -> None:
        journal = UninstallJournal.create(self.request())
        with self.assertRaises(UninstallContractError):
            journal.advance("registration-removed", updated_at=STAMP)

        journal = journal.with_status("prepared", updated_at=STAMP)
        for step in UNINSTALL_STEPS:
            journal = journal.advance(step, updated_at=STAMP)
        self.assertEqual(journal.status, "completed")

        store = UninstallJournalStore(self.layout.data_root.parent / "journal.json")
        written = store.create(UninstallJournal.create(self.request()))
        advanced = written.with_status("prepared", updated_at=STAMP)
        self.assertEqual(store.replace(written, advanced), advanced)

    def test_partial_requires_error_and_is_terminal(self) -> None:
        journal = UninstallJournal.create(self.request()).fail(
            "task deletion failed",
            updated_at=STAMP,
        )

        self.assertEqual(journal.status, "partial")
        with self.assertRaises(UninstallContractError):
            journal.advance("task-removed", updated_at=STAMP)
        with self.assertRaises(UninstallContractError):
            replace(journal, errors=())


if __name__ == "__main__":
    unittest.main()
