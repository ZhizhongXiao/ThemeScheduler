from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theme_scheduler.persistence import (
    JsonDocumentError,
    MigrationError,
    atomic_write_bytes,
    atomic_write_json,
    captured_at,
    load_json_object,
    migrate_json_file,
    migrate_json_object,
)


class PersistenceTests(unittest.TestCase):
    def test_public_json_api_round_trip_after_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "document.json"
            atomic_write_json(path, {"schemaVersion": 1, "value": "before"})

            document = load_json_object(path)
            document["value"] = "after"
            atomic_write_json(path, document, force=True)

            self.assertEqual(
                load_json_object(path),
                {"schemaVersion": 1, "value": "after"},
            )

    def test_round_trip_requires_an_object_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "valid.json"
            array = root / "array.json"
            valid.write_text('{"value": "测试"}', encoding="utf-8")
            array.write_text("[]", encoding="utf-8")

            self.assertEqual(load_json_object(valid), {"value": "测试"})
            with self.assertRaisesRegex(JsonDocumentError, "root must be an object"):
                load_json_object(array)

    def test_invalid_json_reports_location_without_replacing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text('{"value":', encoding="utf-8")

            with self.assertRaisesRegex(JsonDocumentError, "line 1"):
                load_json_object(path)
            self.assertEqual(path.read_text(encoding="utf-8"), '{"value":')

    def test_replace_failure_preserves_original_and_removes_temporary_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "state.json"
            atomic_write_json(path, {"value": "before"})

            with (
                patch(
                    "theme_scheduler.persistence.os.replace",
                    side_effect=OSError("simulated replace failure"),
                ),
                self.assertRaisesRegex(OSError, "replace failure"),
            ):
                atomic_write_json(path, {"value": "after"}, force=True)

            self.assertEqual(load_json_object(path), {"value": "before"})
            self.assertEqual(list(root.glob(".*.tmp")), [])

    def test_serialization_failure_leaves_no_target_or_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config.json"

            with self.assertRaises(TypeError):
                atomic_write_json(path, {"invalid": {1, 2, 3}})

            self.assertFalse(path.exists())
            self.assertEqual(list(root.glob(".*.tmp")), [])

    def test_timestamp_has_explicit_utc_offset(self) -> None:
        stamp = captured_at()
        self.assertRegex(stamp, r"[+-]\d\d:\d\d$")

    def test_atomic_bytes_replace_failure_preserves_original(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "install.theme"
            atomic_write_bytes(path, b"before")

            with (
                patch(
                    "theme_scheduler.persistence.os.replace",
                    side_effect=OSError("simulated byte replace failure"),
                ),
                self.assertRaises(OSError),
            ):
                atomic_write_bytes(path, b"after", force=True)

            self.assertEqual(path.read_bytes(), b"before")
            self.assertEqual(list(root.glob(".*.tmp")), [])

    def test_explicit_adjacent_migration_is_validated(self) -> None:
        source = {"kind": "example", "schemaVersion": 1, "old": "value"}

        migrated = migrate_json_object(
            source,
            kind="example",
            current_version=2,
            migrations={
                1: lambda payload: {
                    "kind": payload["kind"],
                    "schemaVersion": 2,
                    "new": payload["old"],
                }
            },
        )

        self.assertEqual(
            migrated, {"kind": "example", "schemaVersion": 2, "new": "value"}
        )
        self.assertEqual(source["schemaVersion"], 1)

    def test_missing_or_skipped_migration_is_rejected(self) -> None:
        source = {"kind": "example", "schemaVersion": 1}
        with self.assertRaisesRegex(MigrationError, "No explicit migration"):
            migrate_json_object(
                source, kind="example", current_version=2, migrations={}
            )
        with self.assertRaisesRegex(MigrationError, "advance exactly one"):
            migrate_json_object(
                source,
                kind="example",
                current_version=2,
                migrations={
                    1: lambda payload: {
                        "kind": payload["kind"],
                        "schemaVersion": 3,
                    }
                },
            )

    def test_future_schema_version_is_rejected_by_public_migrator(self) -> None:
        with self.assertRaisesRegex(MigrationError, "newer than supported"):
            migrate_json_object(
                {"kind": "example", "schemaVersion": 3},
                kind="example",
                current_version=2,
                migrations={},
            )

    def test_file_migration_preserves_original_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            original = {"kind": "example", "schemaVersion": 1, "old": 7}
            atomic_write_json(path, original)

            migrated = migrate_json_file(
                path,
                kind="example",
                current_version=2,
                migrations={
                    1: lambda payload: {
                        "kind": payload["kind"],
                        "schemaVersion": 2,
                        "new": payload["old"],
                    }
                },
                validator=lambda payload: (
                    None
                    if set(payload) == {"kind", "schemaVersion", "new"}
                    else (_ for _ in ()).throw(ValueError("invalid"))
                ),
            )

            self.assertEqual(load_json_object(path), migrated)
            self.assertEqual(
                load_json_object(path.with_name("config.json.v1.bak")), original
            )

    def test_file_migration_replace_failure_keeps_source_and_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config.json"
            original = {"kind": "example", "schemaVersion": 1, "old": 7}
            atomic_write_json(path, original)
            real_replace = os.replace
            calls = 0

            def fail_target_replace(
                source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            ) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated migrated target failure")
                real_replace(source, destination)

            with (
                patch(
                    "theme_scheduler.persistence.os.replace",
                    side_effect=fail_target_replace,
                ),
                self.assertRaisesRegex(OSError, "target failure"),
            ):
                migrate_json_file(
                    path,
                    kind="example",
                    current_version=2,
                    migrations={
                        1: lambda payload: {
                            "kind": payload["kind"],
                            "schemaVersion": 2,
                            "new": payload["old"],
                        }
                    },
                    validator=lambda payload: None,
                )

            self.assertEqual(load_json_object(path), original)
            self.assertEqual(
                load_json_object(path.with_name("config.json.v1.bak")), original
            )
            self.assertEqual(list(root.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
