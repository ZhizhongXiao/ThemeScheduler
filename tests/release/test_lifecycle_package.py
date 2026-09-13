from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"

from theme_scheduler.lifecycle import (
    InstallationRecord,
    InstallLayout,
    LifecycleTransaction,
    PayloadManifest,
    file_sha256,
)


class LifecyclePackageTests(unittest.TestCase):
    def test_public_contracts_live_in_focused_modules(self) -> None:
        self.assertEqual(
            InstallLayout.__module__,
            "theme_scheduler.lifecycle.layout",
        )
        self.assertEqual(
            PayloadManifest.__module__,
            "theme_scheduler.lifecycle.payload",
        )
        self.assertEqual(
            InstallationRecord.__module__,
            "theme_scheduler.lifecycle.installation",
        )
        self.assertEqual(
            LifecycleTransaction.__module__,
            "theme_scheduler.lifecycle.transaction",
        )

    def test_file_hash_matches_standard_library_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "payload.bin"
            content = b"ThemeScheduler lifecycle package"
            target.write_bytes(content)

            self.assertEqual(
                file_sha256(target),
                hashlib.sha256(content).hexdigest(),
            )

    def test_removed_legacy_module_is_not_present(self) -> None:
        package_root = SOURCE_ROOT / "theme_scheduler"
        self.assertFalse((package_root / "install_contracts.py").exists())


if __name__ == "__main__":
    unittest.main()
