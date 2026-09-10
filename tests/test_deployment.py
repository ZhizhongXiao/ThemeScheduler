from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from tests._support import capture_payload
from tests._support import install_layout as _layout
from theme_scheduler.lifecycle import (
    InstallContractError,
    InstallLayout,
    LifecycleTransactionStore,
    PayloadManifest,
)
from theme_scheduler.lifecycle.deployment import (
    DeploymentError,
    DeploymentJournal,
    DeploymentJournalStore,
    FileDeploymentService,
    LocalDeploymentFileSystem,
    SimulatedDeploymentInterruption,
    verify_active_payload,
)

NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
INSTALL_ID = "lifecycle-20260725T120000-11111111"
SECOND_ID = "lifecycle-20260725T120100-22222222"


def _payload(
    root: Path,
    *,
    version: str,
    marker: str,
) -> PayloadManifest:
    return capture_payload(
        root,
        version,
        main=f"main-{marker}".encode(),
        runtime=f"runtime-{marker}".encode(),
        uninstall=f"uninstall-{marker}".encode(),
    )


def _service(
    layout: InstallLayout,
    *,
    checkpoint=None,
    filesystem=None,
) -> FileDeploymentService:
    return FileDeploymentService(
        layout,
        clock=lambda: NOW,
        checkpoint=checkpoint,
        filesystem=filesystem,
    )


def _install_old(root: Path) -> tuple[InstallLayout, PayloadManifest]:
    layout = _layout(root)
    source = root / "payload-old"
    manifest = _payload(source, version="1.0.0", marker="old")
    outcome = _service(layout).deploy(
        source,
        manifest,
        operation="install",
        from_version=None,
        transaction_id=INSTALL_ID,
    )
    if outcome.status != "completed":
        raise AssertionError(outcome)
    return layout, manifest


class DeploymentJournalTests(unittest.TestCase):
    def test_round_trip_and_progress_are_strict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deployment.json"
            journal = DeploymentJournal(
                transaction_id=INSTALL_ID,
                operation="reinstall",
                status="prepared",
                old_entries=("app", "metadata"),
                old_tree_sha256="a" * 64,
                old_tree_entry_count=4,
            )
            store = DeploymentJournalStore(path)
            self.assertEqual(store.create(journal), journal)
            mutating = store.save(replace(journal, status="mutating"))
            progressed = store.save(
                replace(
                    mutating,
                    old_moved=("app",),
                    new_activated=("app",),
                )
            )
            self.assertEqual(progressed.new_activated, ("app",))
            with self.assertRaisesRegex(InstallContractError, "backwards"):
                store.save(replace(progressed, old_moved=()))

            payload = journal.as_dict()
            payload["unknown"] = True
            with self.assertRaisesRegex(InstallContractError, "fields"):
                DeploymentJournal.from_dict(payload)


class FileDeploymentSuccessTests(unittest.TestCase):
    def test_fresh_install_stages_verifies_switches_and_cleans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout = _layout(root)
            source = root / "payload"
            manifest = _payload(source, version="1.0.0", marker="fresh")

            outcome = _service(layout).deploy(
                source,
                manifest,
                operation="install",
                from_version=None,
                transaction_id=INSTALL_ID,
            )

            self.assertEqual(outcome.status, "completed")
            self.assertTrue(outcome.active_verified)
            self.assertFalse(outcome.staging.exists())
            self.assertFalse(outcome.rollback.exists())
            record = verify_active_payload(layout, manifest)
            self.assertEqual(record.last_operation, "install")
            self.assertEqual(record.last_transaction_id, INSTALL_ID)
            lifecycle = LifecycleTransactionStore(
                layout.lifecycle_journal(INSTALL_ID)
            ).load()
            self.assertEqual(lifecycle.status, "completed")

    def test_reinstall_replaces_damaged_tree_without_overlay_or_user_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout, _old_manifest = _install_old(root)
            (layout.app / "ThemeScheduler.exe").write_text(
                "damaged",
                encoding="utf-8",
            )
            (layout.app / "obsolete.dll").write_text(
                "obsolete",
                encoding="utf-8",
            )
            (layout.program_root / "unknown-old.txt").write_text(
                "old-root-file",
                encoding="utf-8",
            )
            layout.data_root.mkdir()
            user_config = layout.data_root / "config.json"
            user_config.write_text('{"keep":true}', encoding="utf-8")
            source = root / "payload-new"
            new_manifest = _payload(
                source,
                version="1.0.0",
                marker="replacement",
            )

            outcome = _service(layout).deploy(
                source,
                new_manifest,
                operation="reinstall",
                from_version="1.0.0",
                transaction_id=SECOND_ID,
            )

            self.assertEqual(outcome.status, "completed")
            verify_active_payload(layout, new_manifest)
            self.assertFalse((layout.app / "obsolete.dll").exists())
            self.assertFalse((layout.program_root / "unknown-old.txt").exists())
            self.assertEqual(
                user_config.read_text(encoding="utf-8"),
                '{"keep":true}',
            )
            active_files = {
                item.path
                for item in PayloadManifest.capture(
                    source,
                    version="1.0.0",
                ).files
            }
            self.assertEqual(
                active_files,
                {item.path for item in new_manifest.files},
            )

    def test_upgrade_requires_newer_version_and_activates_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout, _ = _install_old(root)
            source = root / "payload-v2"
            manifest = _payload(source, version="1.1.0", marker="v2")

            outcome = _service(layout).deploy(
                source,
                manifest,
                operation="upgrade",
                from_version="1.0.0",
                transaction_id=SECOND_ID,
            )

            self.assertEqual(outcome.status, "completed")
            self.assertEqual(
                verify_active_payload(layout, manifest).version,
                "1.1.0",
            )

    def test_deferred_completion_retains_rollback_until_finalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout, _ = _install_old(root)
            source = root / "payload-new"
            manifest = _payload(
                source,
                version="1.0.0",
                marker="replacement",
            )
            service = _service(layout)

            committed = service.deploy(
                source,
                manifest,
                operation="reinstall",
                from_version="1.0.0",
                transaction_id=SECOND_ID,
                defer_completion=True,
            )

            self.assertEqual(committed.status, "committed")
            self.assertTrue(committed.active_verified)
            self.assertTrue(committed.rollback.exists())
            self.assertTrue((committed.rollback / "app").is_dir())
            verify_active_payload(layout, manifest)

            completed = service.complete_committed(
                manifest,
                transaction_id=SECOND_ID,
            )

            self.assertEqual(completed.status, "completed")
            self.assertTrue(completed.active_verified)
            self.assertFalse(completed.staging.exists())
            self.assertFalse(completed.rollback.exists())

    def test_deferred_commit_can_restore_exact_old_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout, old_manifest = _install_old(root)
            before = {
                path.relative_to(layout.program_root).as_posix(): (
                    path.read_bytes() if path.is_file() else None
                )
                for path in layout.program_root.rglob("*")
            }
            source = root / "payload-new"
            manifest = _payload(
                source,
                version="1.0.0",
                marker="replacement",
            )
            service = _service(layout)
            committed = service.deploy(
                source,
                manifest,
                operation="reinstall",
                from_version="1.0.0",
                transaction_id=SECOND_ID,
                defer_completion=True,
            )
            self.assertEqual(committed.status, "committed")

            rolled_back = service.rollback_committed(
                manifest,
                transaction_id=SECOND_ID,
                reason="Later integration verification failed.",
            )

            self.assertEqual(rolled_back.status, "rolled-back")
            self.assertTrue(rolled_back.rollback_succeeded)
            verify_active_payload(layout, old_manifest)
            after = {
                path.relative_to(layout.program_root).as_posix(): (
                    path.read_bytes() if path.is_file() else None
                )
                for path in layout.program_root.rglob("*")
                if not path.relative_to(layout.program_root).parts[0].startswith(".")
            }
            before_without_infrastructure = {
                path: value
                for path, value in before.items()
                if not Path(path).parts[0].startswith(".")
            }
            self.assertEqual(after, before_without_infrastructure)


class FileDeploymentFailureTests(unittest.TestCase):
    def test_failure_after_partial_activation_restores_exact_old_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout, old_manifest = _install_old(root)
            before = {
                path.relative_to(layout.program_root).as_posix(): (
                    path.read_bytes() if path.is_file() else None
                )
                for path in layout.program_root.rglob("*")
            }
            source = root / "payload-new"
            new_manifest = _payload(
                source,
                version="1.0.0",
                marker="new",
            )

            def checkpoint(name: str) -> None:
                if name == "new-activated:app":
                    raise RuntimeError("injected activation failure")

            outcome = _service(
                layout,
                checkpoint=checkpoint,
            ).deploy(
                source,
                new_manifest,
                operation="reinstall",
                from_version="1.0.0",
                transaction_id=SECOND_ID,
            )

            self.assertEqual(outcome.status, "rolled-back")
            self.assertTrue(outcome.rollback_succeeded)
            verify_active_payload(layout, old_manifest)
            after = {
                path.relative_to(layout.program_root).as_posix(): (
                    path.read_bytes() if path.is_file() else None
                )
                for path in layout.program_root.rglob("*")
                if SECOND_ID not in path.name
            }
            before_without_new_journals = {
                key: value
                for key, value in before.items()
                if SECOND_ID not in Path(key).name
            }
            self.assertEqual(after, before_without_new_journals)

    def test_staged_tamper_is_detected_before_success_and_old_is_restored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout, old_manifest = _install_old(root)
            source = root / "payload-new"
            new_manifest = _payload(
                source,
                version="1.0.0",
                marker="new",
            )

            def checkpoint(name: str) -> None:
                if name == "payload-staged":
                    (
                        layout.staging(SECOND_ID)
                        / "payload"
                        / "app"
                        / "ThemeScheduler.exe"
                    ).write_text("tampered", encoding="utf-8")

            outcome = _service(
                layout,
                checkpoint=checkpoint,
            ).deploy(
                source,
                new_manifest,
                operation="reinstall",
                from_version="1.0.0",
                transaction_id=SECOND_ID,
            )

            self.assertEqual(outcome.status, "rolled-back")
            verify_active_payload(layout, old_manifest)

    def test_unfinished_transaction_blocks_another_deployment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout, _ = _install_old(root)
            source = root / "payload-new"
            manifest = _payload(
                source,
                version="1.0.0",
                marker="new",
            )
            (layout.program_root / ".staging-lifecycle-stale").mkdir()

            with self.assertRaisesRegex(DeploymentError, "requires recovery"):
                _service(layout).deploy(
                    source,
                    manifest,
                    operation="reinstall",
                    from_version="1.0.0",
                    transaction_id=SECOND_ID,
                )

    def test_fresh_install_refuses_nonempty_root_without_modifying_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout = _layout(root)
            layout.program_root.mkdir(parents=True)
            unrelated = layout.program_root / "unrelated.txt"
            unrelated.write_text("keep", encoding="utf-8")
            source = root / "payload"
            manifest = _payload(
                source,
                version="1.0.0",
                marker="fresh",
            )

            with self.assertRaisesRegex(DeploymentError, "empty dedicated"):
                _service(layout).deploy(
                    source,
                    manifest,
                    operation="install",
                    from_version=None,
                    transaction_id=INSTALL_ID,
                )
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")
            self.assertFalse(layout.app.exists())
            self.assertEqual(
                list(layout.program_root.iterdir()),
                [unrelated],
            )

    def test_trusted_record_version_mismatch_stops_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout, old_manifest = _install_old(root)
            source = root / "payload-new"
            manifest = _payload(
                source,
                version="2.0.0",
                marker="new",
            )

            with self.assertRaisesRegex(DeploymentError, "from_version"):
                _service(layout).deploy(
                    source,
                    manifest,
                    operation="upgrade",
                    from_version="1.5.0",
                    transaction_id=SECOND_ID,
                )
            verify_active_payload(layout, old_manifest)


class _FailOldAppRestore(LocalDeploymentFileSystem):
    def __init__(self, rollback: Path) -> None:
        self.rollback = rollback
        self.failed_once = False

    def move(self, source: Path, target: Path) -> None:
        if (
            not self.failed_once
            and source == self.rollback / "app"
            and target.name == "app"
        ):
            self.failed_once = True
            raise OSError("injected rollback failure")
        super().move(source, target)


class FileDeploymentRecoveryTests(unittest.TestCase):
    def _crash_case(self, checkpoint_name: str) -> tuple[str, bool]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout, old_manifest = _install_old(root)
            source = root / "payload-new"
            new_manifest = _payload(
                source,
                version="1.0.0",
                marker="new",
            )

            def checkpoint(name: str) -> None:
                if name == checkpoint_name:
                    raise SimulatedDeploymentInterruption(name)

            with self.assertRaises(SimulatedDeploymentInterruption):
                _service(layout, checkpoint=checkpoint).deploy(
                    source,
                    new_manifest,
                    operation="reinstall",
                    from_version="1.0.0",
                    transaction_id=SECOND_ID,
                )

            outcome = _service(layout).recover(
                new_manifest,
                transaction_id=SECOND_ID,
            )
            if checkpoint_name == "committed":
                verify_active_payload(layout, new_manifest)
                expected_new = True
            else:
                verify_active_payload(layout, old_manifest)
                expected_new = False
            self.assertFalse(outcome.staging.exists())
            self.assertFalse(outcome.rollback.exists())
            return outcome.status, expected_new

    def test_every_durable_interruption_point_recovers_without_mixing(self) -> None:
        expectations = {
            "planned": "failed",
            "payload-staged": "failed",
            "prepared": "failed",
            "mutating": "rolled-back",
            "old-moved:app": "rolled-back",
            "new-activated:app": "rolled-back",
            "new-activated:maintenance": "rolled-back",
            "new-activated:metadata": "rolled-back",
            "committed": "completed",
        }
        for checkpoint, expected_status in expectations.items():
            with self.subTest(checkpoint=checkpoint):
                status, _expected_new = self._crash_case(checkpoint)
                self.assertEqual(status, expected_status)

    def test_partial_rollback_keeps_evidence_and_can_be_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout, old_manifest = _install_old(root)
            source = root / "payload-new"
            new_manifest = _payload(
                source,
                version="1.0.0",
                marker="new",
            )

            def checkpoint(name: str) -> None:
                if name == "new-activated:app":
                    raise RuntimeError("activate failed")

            failing_fs = _FailOldAppRestore(layout.rollback(SECOND_ID))
            first = _service(
                layout,
                checkpoint=checkpoint,
                filesystem=failing_fs,
            ).deploy(
                source,
                new_manifest,
                operation="reinstall",
                from_version="1.0.0",
                transaction_id=SECOND_ID,
            )
            self.assertEqual(first.status, "partial")
            self.assertFalse(first.rollback_succeeded)
            self.assertTrue(first.rollback.exists())

            recovered = _service(layout).recover(
                new_manifest,
                transaction_id=SECOND_ID,
            )
            self.assertEqual(recovered.status, "rolled-back")
            self.assertTrue(recovered.rollback_succeeded)
            verify_active_payload(layout, old_manifest)


if __name__ == "__main__":
    unittest.main()
