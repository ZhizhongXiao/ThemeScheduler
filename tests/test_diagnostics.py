from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from theme_scheduler.diagnostics import (
    InvalidSnapshotError,
    RegistryKeySpec,
    capture_registry_snapshot,
    diff_registry_snapshots,
    encode_registry_data,
)
from theme_scheduler.persistence import atomic_write_json, load_json

FIXTURES = Path(__file__).parent / "fixtures"


class RegistryEncodingTests(unittest.TestCase):
    def test_binary_data_is_hex_encoded(self) -> None:
        self.assertEqual(
            encode_registry_data(b"\x00\x7f\xff"),
            {"encoding": "hex", "value": "007fff"},
        )

    def test_tuple_data_becomes_json_array(self) -> None:
        self.assertEqual(encode_registry_data(("a", "b")), ["a", "b"])


class SnapshotCaptureTests(unittest.TestCase):
    def test_capture_uses_injected_read_only_reader(self) -> None:
        calls: list[RegistryKeySpec] = []

        def reader(spec: RegistryKeySpec) -> dict[str, object]:
            calls.append(spec)
            return {
                "root": spec.root,
                "path": spec.path,
                "purpose": spec.purpose,
                "exists": True,
                "values": [],
            }

        spec = RegistryKeySpec("HKEY_CURRENT_USER", r"Software\Example", "test")
        snapshot = capture_registry_snapshot((spec,), reader)

        self.assertEqual(snapshot["kind"], "themescheduler.registry-baseline")
        self.assertEqual(snapshot["keys"][0]["path"], r"Software\Example")
        self.assertIn(spec, calls)


class SnapshotDiffTests(unittest.TestCase):
    def test_fixture_diff_reports_added_and_changed_values(self) -> None:
        before = load_json(FIXTURES / "registry_before.json")
        after = load_json(FIXTURES / "registry_after.json")

        result = diff_registry_snapshots(before, after)

        self.assertEqual(result["changeCount"], 2)
        self.assertEqual(
            [change["change"] for change in result["changes"]],
            ["value-changed", "value-added"],
        )
        self.assertEqual(
            [change["name"] for change in result["changes"]],
            ["AppsUseLightTheme", "SystemUsesLightTheme"],
        )

    def test_identical_snapshot_has_no_changes(self) -> None:
        snapshot = load_json(FIXTURES / "registry_before.json")
        result = diff_registry_snapshots(snapshot, snapshot)
        self.assertEqual(result["changeCount"], 0)
        self.assertEqual(result["changes"], [])

    def test_removed_value_is_reported(self) -> None:
        before = load_json(FIXTURES / "registry_before.json")
        after = deepcopy(before)
        after["keys"][0]["values"] = [after["keys"][0]["values"][0]]

        result = diff_registry_snapshots(before, after)

        self.assertEqual(result["changeCount"], 1)
        self.assertEqual(result["changes"][0]["change"], "value-removed")
        self.assertEqual(result["changes"][0]["name"], "Palette")

    def test_added_and_removed_keys_are_reported(self) -> None:
        populated = load_json(FIXTURES / "registry_before.json")
        empty = deepcopy(populated)
        empty["keys"] = []

        removed = diff_registry_snapshots(populated, empty)
        added = diff_registry_snapshots(empty, populated)

        self.assertEqual(removed["changes"][0]["change"], "key-removed")
        self.assertEqual(added["changes"][0]["change"], "key-added")

    def test_registry_read_error_change_is_reported(self) -> None:
        before = load_json(FIXTURES / "registry_before.json")
        after = deepcopy(before)
        before["keys"][0]["error"] = "PermissionError: denied"

        result = diff_registry_snapshots(before, after)

        self.assertEqual(result["changeCount"], 1)
        self.assertEqual(result["changes"][0]["change"], "key-read-status-changed")

    def test_unknown_schema_is_rejected(self) -> None:
        snapshot = load_json(FIXTURES / "registry_before.json")
        snapshot["schemaVersion"] = 99
        with self.assertRaises(InvalidSnapshotError):
            diff_registry_snapshots(snapshot, snapshot)


class JsonFileTests(unittest.TestCase):
    def test_atomic_write_round_trip_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "snapshot.json"
            payload = {"schemaVersion": 1, "value": "测试"}

            atomic_write_json(path, payload)
            self.assertEqual(load_json(path), payload)
            with self.assertRaises(FileExistsError):
                atomic_write_json(path, {"schemaVersion": 2})

    def test_atomic_write_force_replaces_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            atomic_write_json(path, {"value": "before"})
            atomic_write_json(path, {"value": "after"}, force=True)

            with path.open("r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), {"value": "after"})


if __name__ == "__main__":
    unittest.main()
