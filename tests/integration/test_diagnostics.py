from __future__ import annotations

import unittest
from unittest.mock import patch

from theme_scheduler.diagnostics import (
    RegistryKeySpec,
    collect_environment,
    encode_registry_data,
    read_registry_key,
)


class RegistryEncodingTests(unittest.TestCase):
    def test_binary_data_is_hex_encoded(self) -> None:
        self.assertEqual(
            encode_registry_data(b"\x00\x7f\xff"),
            {"encoding": "hex", "value": "007fff"},
        )

    def test_tuple_data_becomes_json_array(self) -> None:
        self.assertEqual(encode_registry_data(("a", "b")), ["a", "b"])


class EnvironmentCollectionTests(unittest.TestCase):
    def test_non_windows_environment_does_not_invoke_registry_reader(self) -> None:
        def reader(_spec: RegistryKeySpec) -> dict[str, object]:
            self.fail("registry reader must not run outside Windows")

        with patch("theme_scheduler.diagnostics.os.name", "posix"):
            result = collect_environment(reader)

        self.assertEqual(result["kind"], "themescheduler.environment")
        self.assertEqual(result["schemaVersion"], 1)
        self.assertEqual(
            result["windowsRelease"],
            {"available": False, "reason": "not-windows"},
        )

    def test_windows_release_uses_injected_read_only_reader(self) -> None:
        calls: list[RegistryKeySpec] = []

        def reader(spec: RegistryKeySpec) -> dict[str, object]:
            calls.append(spec)
            return {
                "exists": True,
                "values": [
                    {"name": "ProductName", "data": "Windows Test"},
                    {"name": "DisplayVersion", "data": "25H2"},
                    {"name": "Ignored", "data": "not exported"},
                ],
            }

        with patch("theme_scheduler.diagnostics.os.name", "nt"):
            result = collect_environment(reader)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].root, "HKEY_LOCAL_MACHINE")
        self.assertEqual(result["windowsRelease"]["ProductName"], "Windows Test")
        self.assertNotIn("Ignored", result["windowsRelease"])


class _RegistryKey:
    def __enter__(self) -> _RegistryKey:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Winreg:
    HKEY_CURRENT_USER = object()
    HKEY_LOCAL_MACHINE = object()
    KEY_READ = 1
    REG_SZ = 1
    REG_BINARY = 3
    REG_DWORD = 4

    def __init__(self, values: list[tuple[str, object, int]]) -> None:
        self.values = values
        self.open_error: OSError | None = None

    def OpenKey(self, *_args: object) -> _RegistryKey:  # noqa: N802
        if self.open_error:
            raise self.open_error
        return _RegistryKey()

    def QueryInfoKey(self, _key: _RegistryKey) -> tuple[int, int, int]:  # noqa: N802
        return 0, len(self.values), 0

    def EnumValue(  # noqa: N802
        self, _key: _RegistryKey, index: int
    ) -> tuple[str, object, int]:
        return self.values[index]


class RegistryReadTests(unittest.TestCase):
    def test_reads_and_sorts_values_with_type_names(self) -> None:
        backend = _Winreg(
            [
                ("Zulu", b"\x01", 3),
                ("Alpha", 1, 4),
            ]
        )
        spec = RegistryKeySpec("HKEY_CURRENT_USER", r"Software\Example", "test")
        with patch("theme_scheduler.diagnostics._winreg_module", return_value=backend):
            result = read_registry_key(spec)
        self.assertTrue(result["exists"])
        self.assertEqual([item["name"] for item in result["values"]], ["Alpha", "Zulu"])
        self.assertEqual(result["values"][1]["typeName"], "REG_BINARY")

    def test_missing_and_access_error_are_reported_without_mutation(self) -> None:
        spec = RegistryKeySpec("HKEY_LOCAL_MACHINE", r"Software\Example", "test")
        missing = _Winreg([])
        missing.open_error = FileNotFoundError("missing")
        with patch("theme_scheduler.diagnostics._winreg_module", return_value=missing):
            self.assertFalse(read_registry_key(spec)["exists"])
        denied = _Winreg([])
        denied.open_error = PermissionError("denied")
        with patch("theme_scheduler.diagnostics._winreg_module", return_value=denied):
            result = read_registry_key(spec)
        self.assertIn("PermissionError", result["error"])

    def test_unknown_root_is_rejected(self) -> None:
        with (
            patch(
                "theme_scheduler.diagnostics._winreg_module",
                return_value=_Winreg([]),
            ),
            self.assertRaisesRegex(ValueError, "Unsupported registry root"),
        ):
            read_registry_key(RegistryKeySpec("HKCR", "Example", "test"))


if __name__ == "__main__":
    unittest.main()
