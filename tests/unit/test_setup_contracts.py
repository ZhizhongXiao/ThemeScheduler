from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests._support import capture_payload as _payload
from tests._support import install_layout as _layout
from theme_scheduler.lifecycle.deployment import (
    FileDeploymentService,
)
from theme_scheduler.setup_contracts import (
    SetupContractError,
    SetupOptions,
    create_setup_plan,
)


class SetupOptionsTests(unittest.TestCase):
    def test_defaults_round_trip(self) -> None:
        value = SetupOptions.defaults()
        self.assertEqual(SetupOptions.from_dict(value.as_dict()), value)
        self.assertFalse(value.desktop_shortcut)
        self.assertEqual(
            set(value.as_dict()),
            {"kind", "schemaVersion", "desktopShortcut"},
        )
        self.assertEqual(value.config.day_start, "06:15")

    def test_unknown_fields_and_wrong_types_are_rejected(self) -> None:
        payload = SetupOptions.defaults().as_dict()
        payload["unknown"] = True
        with self.assertRaisesRegex(SetupContractError, "fields"):
            SetupOptions.from_dict(payload)
        payload = SetupOptions.defaults().as_dict()
        payload["desktopShortcut"] = "yes"
        with self.assertRaisesRegex(SetupContractError, "boolean"):
            SetupOptions.from_dict(payload)


class SetupPlanTests(unittest.TestCase):
    def test_logs_alone_do_not_count_as_retained_core_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout = _layout(root)
            layout.data_root.mkdir()
            (layout.data_root / "logs").mkdir()
            source = root / "payload"
            manifest = _payload(source, "1.0.0")

            plan = create_setup_plan(layout, manifest)

            self.assertEqual(plan.operation, "install")
            self.assertFalse(plan.retained_data)
            self.assertNotIn("optionsApplyToData", plan.as_dict())

    def test_reinstall_upgrade_and_downgrade_are_classified_strictly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout = _layout(root)
            old_source = root / "old"
            old_manifest = _payload(old_source, "1.0.0")
            outcome = FileDeploymentService(layout).deploy(
                old_source,
                old_manifest,
                operation="install",
                from_version=None,
                transaction_id="lifecycle-20260725T120000-11111111",
            )
            self.assertEqual(outcome.status, "completed")

            same_source = root / "same"
            same = _payload(same_source, "1.0.0")
            newer_source = root / "newer"
            newer = _payload(newer_source, "1.1.0")
            older_source = root / "older"
            older = _payload(older_source, "0.9.0")

            self.assertEqual(
                create_setup_plan(layout, same).operation,
                "reinstall",
            )
            self.assertEqual(
                create_setup_plan(layout, newer).operation,
                "upgrade",
            )
            with self.assertRaisesRegex(SetupContractError, "Downgrade"):
                create_setup_plan(layout, older)

    def test_untrusted_existing_program_tree_is_not_overlaid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout = _layout(root)
            layout.program_root.mkdir(parents=True)
            (layout.program_root / "unknown.exe").write_bytes(b"x")
            source = root / "payload"
            manifest = _payload(source, "1.0.0")
            with self.assertRaisesRegex(
                SetupContractError, "trusted installation record"
            ):
                create_setup_plan(layout, manifest)


if __name__ == "__main__":
    unittest.main()
