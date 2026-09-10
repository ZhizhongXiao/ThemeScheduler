from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theme_scheduler.lifecycle import (
    InstalledAppRegistration,
    InstallLayout,
)
from theme_scheduler.system_integration import (
    RegistryKeyBackup,
    ShortcutSpec,
)
from theme_scheduler.system_integration_windows import (
    INSTALLED_APP_KEY,
    USER_SHELL_FOLDERS_KEY,
    WindowsInstalledAppRegistryBackend,
    WindowsKnownFolderReader,
    WindowsShortcutBackend,
    WindowsSystemIntegrationError,
)

BRIDGE = Path(__file__).resolve().parents[1] / "entrypoints" / "shortcut_bridge.ps1"


class FakeKey:
    def __init__(self, registry, path: str, access: int) -> None:
        self.registry = registry
        self.path = path
        self.access = access

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeWinReg:
    HKEY_CURRENT_USER = object()
    KEY_READ = 0x1
    KEY_WRITE = 0x2
    KEY_SET_VALUE = 0x2
    REG_SZ = 1
    REG_EXPAND_SZ = 2
    REG_BINARY = 3
    REG_DWORD = 4

    def __init__(self) -> None:
        self.keys: dict[str, dict[str, tuple[object, int]]] = {}
        self.subkeys: dict[str, list[str]] = {}

    def OpenKey(self, root, path, reserved=0, access=0):
        if path not in self.keys:
            raise FileNotFoundError(path)
        return FakeKey(self, path, access)

    def CreateKeyEx(self, root, path, reserved=0, access=0):
        self.keys.setdefault(path, {})
        if "\\" in path:
            parent, child = path.rsplit("\\", 1)
            if parent in self.keys:
                children = self.subkeys.setdefault(parent, [])
                if child not in children:
                    children.append(child)
        return FakeKey(self, path, access)

    def QueryValueEx(self, key, name):
        return self.keys[key.path][name]

    def EnumValue(self, key, index):
        items = list(self.keys[key.path].items())
        if index >= len(items):
            raise OSError("no more values")
        name, (data, value_type) = items[index]
        return name, data, value_type

    def EnumKey(self, key, index):
        children = self.subkeys.get(key.path, [])
        if index >= len(children):
            raise OSError("no more keys")
        return children[index]

    def QueryInfoKey(self, key):
        return (
            len(self.subkeys.get(key.path, [])),
            len(self.keys[key.path]),
            0,
        )

    def SetValueEx(self, key, name, reserved, value_type, data):
        self.keys[key.path][name] = (data, value_type)

    def DeleteValue(self, key, name):
        if not (key.access & self.KEY_WRITE):
            raise PermissionError("write access required")
        del self.keys[key.path][name]

    def DeleteKey(self, root, path):
        if self.subkeys.get(path):
            raise OSError("key has children")
        del self.keys[path]
        self.subkeys.pop(path, None)
        if "\\" in path:
            parent, child = path.rsplit("\\", 1)
            if child in self.subkeys.get(parent, []):
                self.subkeys[parent].remove(child)


def completed(action: str, **fields) -> subprocess.CompletedProcess[str]:
    payload = {"ok": True, "action": action, **fields}
    return subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(payload),
        stderr="",
    )


class WindowsRegistryIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = FakeWinReg()
        self.backend = WindowsInstalledAppRegistryBackend(self.registry)
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.layout = InstallLayout(
            root / "Programs" / "ThemeScheduler",
            root / "ThemeScheduler",
        )
        self.registration = InstalledAppRegistration.create(
            self.layout,
            version="0.1.0",
            publisher="ThemeScheduler",
            estimated_size_kib=2048,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_write_read_capture_and_exact_restore(self) -> None:
        prior = RegistryKeyBackup(
            (
                ("Binary", b"\x01\x02", self.registry.REG_BINARY),
                ("Legacy", "preserve", self.registry.REG_SZ),
            )
        )
        self.backend.restore(prior)

        self.backend.write(self.registration)

        self.assertEqual(self.backend.read(), self.registration)
        self.assertNotIn("Legacy", self.registry.keys[INSTALLED_APP_KEY])
        self.backend.restore(prior)
        self.assertEqual(self.backend.capture(), prior)

    def test_stale_extra_value_is_drift_not_a_valid_registration(self) -> None:
        self.backend.write(self.registration)
        self.registry.keys[INSTALLED_APP_KEY]["Unexpected"] = (
            "old",
            self.registry.REG_SZ,
        )

        self.assertIsNone(self.backend.read())

    def test_absent_restore_deletes_key_and_subkeys_are_refused(self) -> None:
        self.backend.write(self.registration)
        self.backend.restore(None)
        self.assertNotIn(INSTALLED_APP_KEY, self.registry.keys)

        self.registry.keys[INSTALLED_APP_KEY] = {}
        self.registry.subkeys[INSTALLED_APP_KEY] = ["Foreign"]
        with self.assertRaisesRegex(
            WindowsSystemIntegrationError,
            "subkey",
        ):
            self.backend.capture()

    def test_known_folders_are_read_from_current_user_shell_values(
        self,
    ) -> None:
        root = Path(self.temporary.name)
        self.registry.keys[USER_SHELL_FOLDERS_KEY] = {
            "Programs": (
                str(root / "Start Menu" / "Programs"),
                self.registry.REG_EXPAND_SZ,
            ),
            "Desktop": (
                str(root / "Desktop"),
                self.registry.REG_SZ,
            ),
        }
        reader = WindowsKnownFolderReader(self.registry)

        self.assertEqual(
            reader.programs(),
            (root / "Start Menu" / "Programs").resolve(),
        )
        self.assertEqual(
            reader.desktop(),
            (root / "Desktop").resolve(),
        )


class WindowsShortcutIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.layout = InstallLayout(
            root / "Programs" / "ThemeScheduler",
            root / "ThemeScheduler",
        )
        self.path = (
            root / "Start Menu" / "Programs" / "ThemeScheduler" / "ThemeScheduler.lnk"
        ).resolve()
        self.shortcut = ShortcutSpec.create(self.path, self.layout)
        self.backend = WindowsShortcutBackend((self.path,), BRIDGE)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_raw_capture_restore_and_allowlist(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"original shortcut")

        backup = self.backend.capture(self.path)
        self.path.write_bytes(b"changed")
        self.backend.restore(self.path, backup)

        self.assertEqual(self.path.read_bytes(), b"original shortcut")
        foreign = self.path.parent / "Other.lnk"
        with self.assertRaisesRegex(
            WindowsSystemIntegrationError,
            "allowlist",
        ):
            self.backend.capture(foreign)

    @unittest.skipUnless(
        os.name == "nt",
        "The real shortcut identity bridge requires Windows.",
    )
    def test_real_bridge_round_trips_frozen_app_user_model_id(
        self,
    ) -> None:
        self.backend.write(self.shortcut)

        actual = self.backend.read(self.path)

        self.assertEqual(actual, self.shortcut)
        self.assertEqual(
            actual.app_user_model_id,
            "ThemeScheduler.ThemeScheduler",
        )
        self.assertEqual(
            actual.toast_activator_clsid,
            "403DE4CE-F3F9-4335-B847-8B2297DC9B6F",
        )
        backup = self.backend.capture(self.path)
        self.assertGreater(len(backup or b""), 0)
        self.path.write_bytes(b"damaged shortcut")

        self.backend.restore(self.path, backup)

        self.assertEqual(self.backend.read(self.path), self.shortcut)

    @patch("theme_scheduler.system_integration_windows.subprocess.run")
    def test_bridge_read_write_use_strict_json_and_no_window(self, run) -> None:
        requests: list[dict[str, object]] = []

        def respond(command, **kwargs):
            request_path = Path(command[command.index("-RequestPath") + 1])
            request = json.loads(request_path.read_text(encoding="utf-8-sig"))
            requests.append(request)
            action = command[command.index("-Action") + 1]
            if action == "Read":
                return completed(
                    "Read",
                    exists=True,
                    readable=True,
                    shortcut=self.shortcut.as_dict(),
                    shortcutChanged=False,
                )
            return completed(
                "Write",
                exists=True,
                path=str(self.path),
                shortcutChanged=True,
            )

        run.side_effect = respond

        actual = self.backend.read(self.path)
        self.backend.write(self.shortcut)

        self.assertEqual(actual, self.shortcut)
        self.assertEqual(
            set(requests[0]),
            {"path"},
        )
        self.assertEqual(requests[1], self.shortcut.as_dict())
        if os.name == "nt":
            self.assertEqual(
                run.call_args.kwargs["creationflags"],
                subprocess.CREATE_NO_WINDOW,
            )

    @patch("theme_scheduler.system_integration_windows.subprocess.run")
    def test_malformed_existing_shortcut_is_reported_as_drift(self, run) -> None:
        stale = self.shortcut.as_dict()
        stale["arguments"] = "--foreign"
        run.return_value = completed(
            "Read",
            exists=True,
            readable=True,
            shortcut=stale,
            shortcutChanged=False,
        )

        self.assertIsNone(self.backend.read(self.path))

    @patch("theme_scheduler.system_integration_windows.subprocess.run")
    def test_foreign_app_user_model_id_is_repairable_drift(self, run) -> None:
        stale = self.shortcut.as_dict()
        stale["appUserModelId"] = "Foreign.Product"
        run.return_value = completed(
            "Read",
            exists=True,
            readable=True,
            shortcut=stale,
            shortcutChanged=False,
        )

        self.assertIsNone(self.backend.read(self.path))

    @patch("theme_scheduler.system_integration_windows.subprocess.run")
    def test_unreadable_existing_shortcut_is_repairable_drift(self, run) -> None:
        run.return_value = completed(
            "Read",
            exists=True,
            readable=False,
            shortcut=None,
            shortcutChanged=False,
        )

        self.assertIsNone(self.backend.read(self.path))

    @patch("theme_scheduler.system_integration_windows.subprocess.run")
    def test_bridge_failure_and_invalid_json_are_wrapped(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=5,
            stdout="",
            stderr="denied",
        )
        with self.assertRaisesRegex(
            WindowsSystemIntegrationError,
            "denied",
        ):
            self.backend.read(self.path)

        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="not-json",
            stderr="",
        )
        with self.assertRaisesRegex(
            WindowsSystemIntegrationError,
            "invalid JSON",
        ):
            self.backend.read(self.path)

    def test_bridge_script_is_local_wsh_only_and_strict(self) -> None:
        script = BRIDGE.read_text(encoding="utf-8")

        self.assertIn("WScript.Shell", script)
        self.assertIn("Assert-ExactProperties", script)
        self.assertIn("ThemeScheduler.lnk", script)
        self.assertNotIn("cmd.exe", script.casefold())


if __name__ == "__main__":
    unittest.main()
