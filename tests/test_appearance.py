from __future__ import annotations

import unittest
from unittest.mock import patch

from theme_scheduler.appearance import (
    APPS_THEME_VALUE,
    REG_DWORD,
    RegistryValue,
    WindowsAppearanceReader,
)


class _Key:
    def __enter__(self) -> _Key:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Winreg:
    HKEY_CURRENT_USER = object()
    KEY_READ = 1

    def __init__(self, values: dict[str, tuple[object, int]]) -> None:
        self.values = values

    def OpenKey(self, *_args: object) -> _Key:  # noqa: N802
        return _Key()

    def QueryValueEx(  # noqa: N802
        self, _key: _Key, name: str
    ) -> tuple[object, int]:
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name]


class WindowsAppearanceReaderTests(unittest.TestCase):
    def test_reads_only_apps_theme_value(self) -> None:
        backend = _Winreg({APPS_THEME_VALUE: (1, REG_DWORD)})
        with patch.object(WindowsAppearanceReader, "_winreg", return_value=backend):
            actual = WindowsAppearanceReader().read_value(APPS_THEME_VALUE)
        self.assertEqual(actual, RegistryValue(True, 1, REG_DWORD))

    def test_missing_apps_theme_value_is_explicit(self) -> None:
        with patch.object(
            WindowsAppearanceReader,
            "_winreg",
            return_value=_Winreg({}),
        ):
            actual = WindowsAppearanceReader().read_value(APPS_THEME_VALUE)
        self.assertEqual(actual, RegistryValue(False))

    def test_rejects_unrelated_registry_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "Only AppsUseLightTheme"):
            WindowsAppearanceReader().read_value("SystemUsesLightTheme")

    def test_reads_valid_semantic_color(self) -> None:
        backend = _Winreg({"ColorizationColor": (0xC4744DA9, REG_DWORD)})
        with patch.object(WindowsAppearanceReader, "_winreg", return_value=backend):
            actual = WindowsAppearanceReader().read_colorization_color()
        self.assertEqual(actual, 0xC4744DA9)

    def test_rejects_invalid_semantic_color(self) -> None:
        backend = _Winreg({"ColorizationColor": ("purple", REG_DWORD)})
        with (
            patch.object(WindowsAppearanceReader, "_winreg", return_value=backend),
            self.assertRaisesRegex(OSError, "not a valid REG_DWORD"),
        ):
            WindowsAppearanceReader().read_colorization_color()


if __name__ == "__main__":
    unittest.main()
