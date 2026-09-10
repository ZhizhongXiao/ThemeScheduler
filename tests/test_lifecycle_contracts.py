from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests._support import install_layout as _layout
from tests._support import write_payload_tree
from theme_scheduler.lifecycle import (
    UNINSTALL_REGISTRY_KEY,
    InstallationRecord,
    InstallationRecordStore,
    InstallContractError,
    InstalledAppRegistration,
    InstallLayout,
    LifecycleTransaction,
    LifecycleTransactionStore,
    PayloadFile,
    PayloadManifest,
    json_document_sha256,
)

TRANSACTION_ID = "lifecycle-20260725T120000-1234abcd"
STARTED_AT = "2026-07-25T12:00:00+08:00"


def _write_payload(root: Path) -> None:
    write_payload_tree(
        root,
        main=b"main-exe",
        runtime=b"runtime",
        uninstall=b"uninstaller",
    )


def _manifest(root: Path) -> PayloadManifest:
    _write_payload(root)
    return PayloadManifest.capture(root, version="1.2.3")


def _planned(root: Path, manifest: PayloadManifest) -> LifecycleTransaction:
    layout = _layout(root)
    return LifecycleTransaction(
        transaction_id=TRANSACTION_ID,
        operation="install",
        status="planned",
        started_at=STARTED_AT,
        updated_at=STARTED_AT,
        program_root=str(layout.program_root),
        data_root=str(layout.data_root),
        from_version=None,
        to_version=manifest.version,
        payload_manifest_sha256=manifest.document_sha256,
    )


class InstallLayoutTests(unittest.TestCase):
    def test_layout_freezes_program_data_and_transaction_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = _layout(Path(directory))

            self.assertEqual(
                layout.executable,
                layout.program_root / "app" / "ThemeScheduler.exe",
            )
            self.assertEqual(
                layout.uninstaller,
                layout.program_root / "maintenance" / "Uninstall.exe",
            )
            self.assertEqual(
                layout.staging(TRANSACTION_ID),
                layout.program_root / f".staging-{TRANSACTION_ID}",
            )
            self.assertEqual(
                layout.rollback(TRANSACTION_ID),
                layout.program_root / f".rollback-{TRANSACTION_ID}",
            )
            self.assertEqual(
                layout.validate_owned_target(layout.app),
                layout.app,
            )
            with self.assertRaisesRegex(InstallContractError, "allow_root"):
                layout.validate_owned_target(layout.program_root)
            self.assertEqual(
                layout.validate_owned_target(
                    layout.program_root,
                    allow_root=True,
                ),
                layout.program_root,
            )

    def test_layout_rejects_relative_broad_and_nested_roots(self) -> None:
        with self.assertRaisesRegex(InstallContractError, "absolute"):
            InstallLayout(
                Path("Programs/ThemeScheduler"),
                Path("ThemeScheduler"),
            )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with self.assertRaisesRegex(InstallContractError, "Programs"):
                InstallLayout(root / "ThemeScheduler", root / "Data" / "ThemeScheduler")
            with self.assertRaisesRegex(InstallContractError, "separate"):
                InstallLayout(
                    root / "Programs" / "ThemeScheduler",
                    root / "Programs" / "ThemeScheduler",
                )
            layout = _layout(root)
            with self.assertRaisesRegex(InstallContractError, "outside"):
                layout.validate_owned_target(root / "unrelated")


class PayloadManifestTests(unittest.TestCase):
    def test_capture_round_trip_hash_and_exact_tree_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _manifest(root)

            self.assertEqual(
                PayloadManifest.from_dict(manifest.as_dict()),
                manifest,
            )
            self.assertEqual(len(manifest.document_sha256), 64)
            manifest.verify_tree(root)

            (root / "app" / "_internal" / "runtime.bin").write_bytes(b"different-size")
            with self.assertRaisesRegex(InstallContractError, "size mismatch"):
                manifest.verify_tree(root)

            (root / "app" / "_internal" / "runtime.bin").write_bytes(b"changed")
            with self.assertRaisesRegex(InstallContractError, "SHA-256 mismatch"):
                manifest.verify_tree(root)

    def test_exact_tree_rejects_extra_and_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _manifest(root)
            (root / "app" / "unexpected.dll").write_bytes(b"extra")
            with self.assertRaisesRegex(InstallContractError, "file set"):
                manifest.verify_tree(root)

            (root / "app" / "unexpected.dll").unlink()
            (root / "maintenance" / "Uninstall.exe").unlink()
            with self.assertRaisesRegex(InstallContractError, "required file"):
                manifest.verify_tree(root)

    def test_paths_reject_escape_reserved_invalid_and_case_collisions(self) -> None:
        valid_hash = "a" * 64
        invalid_paths = (
            "../ThemeScheduler.exe",
            "app\\ThemeScheduler.exe",
            "app/../ThemeScheduler.exe",
            "other/file.bin",
            "app/CON.txt",
            "app/trailing.",
            "app/bad?.dll",
        )
        for path in invalid_paths:
            with (
                self.subTest(path=path),
                self.assertRaises(InstallContractError),
            ):
                PayloadFile(path, 1, valid_hash)

        files = (
            PayloadFile("app/ThemeScheduler.exe", 1, valid_hash),
            PayloadFile("app/themescheduler.EXE", 1, "b" * 64),
            PayloadFile("maintenance/Uninstall.exe", 1, "c" * 64),
        )
        with self.assertRaisesRegex(InstallContractError, "duplicate"):
            PayloadManifest("1.0.0", files)

    def test_manifest_requires_order_entrypoints_and_exact_fields(self) -> None:
        files = (
            PayloadFile("maintenance/Uninstall.exe", 1, "b" * 64),
            PayloadFile("app/ThemeScheduler.exe", 1, "a" * 64),
        )
        with self.assertRaisesRegex(InstallContractError, "order"):
            PayloadManifest("1.0.0", files)

        with self.assertRaisesRegex(InstallContractError, "required"):
            PayloadManifest(
                "1.0.0",
                (PayloadFile("app/ThemeScheduler.exe", 1, "a" * 64),),
            )

        payload = {
            "kind": "themescheduler.payload-manifest",
            "schemaVersion": 1,
            "productId": "ThemeScheduler",
            "version": "1.0.0",
            "files": [],
            "unknown": True,
        }
        with self.assertRaisesRegex(InstallContractError, "fields"):
            PayloadManifest.from_dict(payload)
        payload.pop("unknown")
        payload["schemaVersion"] = True
        with self.assertRaisesRegex(InstallContractError, "schemaVersion"):
            PayloadManifest.from_dict(payload)

    def test_capture_rejects_reparse_point_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_payload(root)
            suspect = root / "app" / "link.dll"
            suspect.write_bytes(b"not-a-real-link")

            with (
                patch(
                    "theme_scheduler.lifecycle.payload._is_reparse_point",
                    side_effect=lambda path: Path(path).name == "link.dll",
                ),
                self.assertRaisesRegex(InstallContractError, "reparse point"),
            ):
                PayloadManifest.capture(root, version="1.0.0")


class InstallationRecordTests(unittest.TestCase):
    def test_record_round_trip_binds_layout_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload_root = root / "payload"
            manifest = _manifest(payload_root)
            layout = _layout(root)
            record = InstallationRecord.create(
                layout,
                manifest,
                installed_at=STARTED_AT,
                operation="install",
                transaction_id=TRANSACTION_ID,
            )

            self.assertEqual(
                InstallationRecord.from_dict(record.as_dict()),
                record,
            )
            self.assertEqual(
                record.payload_manifest_sha256,
                manifest.document_sha256,
            )
            with self.assertRaisesRegex(InstallContractError, "frozen install layout"):
                replace(record, executable_path=str(root / "wrong.exe"))

    def test_record_store_uses_compare_and_replace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload_root = root / "payload"
            manifest = _manifest(payload_root)
            layout = _layout(root)
            record = InstallationRecord.create(
                layout,
                manifest,
                installed_at=STARTED_AT,
                operation="install",
                transaction_id=TRANSACTION_ID,
            )
            store = InstallationRecordStore(root / "record" / "installation.json")
            self.assertEqual(store.create(record), record)

            expected_hash = json_document_sha256(record.as_dict())
            updated = replace(
                record,
                installed_at="2026-07-25T12:01:00+08:00",
                last_operation="reinstall",
            )
            self.assertEqual(
                store.replace(
                    updated,
                    expected_current_sha256=expected_hash,
                ),
                updated,
            )
            with self.assertRaisesRegex(InstallContractError, "changed before"):
                store.replace(
                    record,
                    expected_current_sha256=expected_hash,
                )


class InstalledAppRegistrationTests(unittest.TestCase):
    def test_registration_freezes_current_user_change_and_uninstall(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = _layout(Path(directory))
            registration = InstalledAppRegistration.create(
                layout,
                version="1.2.3",
                publisher="ThemeScheduler Project",
                estimated_size_kib=12345,
            )
            values = registration.as_registry_values()

            self.assertEqual(
                UNINSTALL_REGISTRY_KEY,
                (
                    r"HKCU\Software\Microsoft\Windows\CurrentVersion"
                    r"\Uninstall\ThemeScheduler"
                ),
            )
            self.assertEqual(values["NoModify"], 0)
            self.assertEqual(values["NoRepair"], 1)
            self.assertEqual(
                values["ModifyPath"],
                f'"{layout.executable}" maintenance',
            )
            self.assertEqual(
                values["UninstallString"],
                f'"{layout.uninstaller}"',
            )
            self.assertNotIn("QuietUninstallString", values)
            self.assertEqual(
                InstalledAppRegistration.from_registry_values(values),
                registration,
            )
            drifted = dict(values)
            drifted["ModifyPath"] = '"C:\\Wrong.exe" maintenance'
            with self.assertRaisesRegex(InstallContractError, "maintenance entry"):
                InstalledAppRegistration.from_registry_values(drifted)
            drifted = dict(values)
            drifted["NoModify"] = False
            with self.assertRaisesRegex(InstallContractError, "flags"):
                InstalledAppRegistration.from_registry_values(drifted)


class LifecycleTransactionTests(unittest.TestCase):
    def test_operation_version_and_failure_invariants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _manifest(root / "payload")
            planned = _planned(root, manifest)

            self.assertEqual(
                LifecycleTransaction.from_dict(planned.as_dict()),
                planned,
            )
            with self.assertRaisesRegex(InstallContractError, "newer"):
                replace(
                    planned,
                    operation="upgrade",
                    from_version="2.0.0",
                    to_version="1.0.0",
                )
            with self.assertRaisesRegex(InstallContractError, "identical"):
                replace(
                    planned,
                    operation="reinstall",
                    from_version="1.0.0",
                    to_version="1.0.1",
                )
            with self.assertRaisesRegex(InstallContractError, "errorCode"):
                replace(planned, status="partial")
            with self.assertRaisesRegex(InstallContractError, "precede"):
                replace(
                    planned,
                    updated_at="2026-07-25T11:59:59+08:00",
                )
            rolled_back = replace(
                planned,
                status="rolled-back",
                error_code="install.failed",
                message="Installation failed and was rolled back.",
                rollback_succeeded=True,
            )
            self.assertTrue(rolled_back.rollback_succeeded)

            uninstall = replace(
                planned,
                operation="uninstall",
                from_version="1.2.3",
                to_version=None,
                payload_manifest_sha256=None,
            )
            self.assertEqual(uninstall.operation, "uninstall")

    def test_store_enforces_transitions_and_immutable_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _manifest(root / "payload")
            planned = _planned(root, manifest)
            store = LifecycleTransactionStore(root / "transaction.json")

            current = store.create(planned)
            current = store.save(
                replace(
                    current,
                    status="prepared",
                    updated_at="2026-07-25T12:00:01+08:00",
                )
            )
            current = store.save(
                replace(
                    current,
                    status="mutating",
                    updated_at="2026-07-25T12:00:02+08:00",
                )
            )
            current = store.save(
                replace(
                    current,
                    status="committed",
                    updated_at="2026-07-25T12:00:03+08:00",
                )
            )
            completed = store.save(
                replace(
                    current,
                    status="completed",
                    updated_at="2026-07-25T12:00:04+08:00",
                )
            )
            self.assertEqual(completed.status, "completed")
            with self.assertRaisesRegex(InstallContractError, "Illegal"):
                store.save(replace(completed, status="completed"))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _manifest(root / "payload")
            store = LifecycleTransactionStore(root / "transaction.json")
            planned = store.create(_planned(root, manifest))
            with self.assertRaisesRegex(InstallContractError, "immutable"):
                store.save(
                    replace(
                        planned,
                        status="prepared",
                        to_version="1.2.4",
                    )
                )


if __name__ == "__main__":
    unittest.main()
