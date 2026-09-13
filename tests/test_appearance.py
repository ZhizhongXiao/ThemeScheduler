from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from theme_scheduler.accent_profile import RgbColor
from theme_scheduler.accent_theme import ThemeVisualState
from theme_scheduler.appearance import (
    APPS_THEME_VALUE,
    AUTO_COLORIZATION_VALUE,
    COLOR_PREVALENCE_VALUE,
    REG_DWORD,
    SYSTEM_THEME_VALUE,
    AppearanceRegistrySnapshot,
    CurrentAppearanceReadError,
    RegistryValue,
    ThemeMode,
    WindowsAccentColorReader,
    WindowsAppearanceReader,
    WindowsAppearanceSettingsBackend,
    WindowsCurrentAppearanceReader,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BRIDGE = PROJECT_ROOT / "entrypoints" / "current_appearance_bridge.ps1"


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


class _AccentReader:
    def __init__(self, color: RgbColor) -> None:
        self.color = color

    def read_color(self) -> RgbColor:
        return self.color


class _MutableKey(_Key):
    def __init__(self, path: str) -> None:
        self.path = path


class _MutableWinreg:
    HKEY_CURRENT_USER = object()
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_DWORD = REG_DWORD

    def __init__(self, values: dict[tuple[str, str], tuple[object, int]]) -> None:
        self.values = values

    def OpenKey(  # noqa: N802
        self,
        _hive: object,
        path: str,
        *_args: object,
    ) -> _MutableKey:
        if not any(key_path == path for key_path, _name in self.values):
            raise FileNotFoundError(path)
        return _MutableKey(path)

    def CreateKeyEx(  # noqa: N802
        self,
        _hive: object,
        path: str,
        *_args: object,
    ) -> _MutableKey:
        return _MutableKey(path)

    def QueryValueEx(  # noqa: N802
        self,
        key: _MutableKey,
        name: str,
    ) -> tuple[object, int]:
        try:
            return self.values[(key.path, name)]
        except KeyError as exc:
            raise FileNotFoundError(name) from exc

    def SetValueEx(  # noqa: N802
        self,
        key: _MutableKey,
        name: str,
        _reserved: int,
        type_code: int,
        data: object,
    ) -> None:
        self.values[(key.path, name)] = (data, type_code)

    def DeleteValue(self, key: _MutableKey, name: str) -> None:  # noqa: N802
        try:
            del self.values[(key.path, name)]
        except KeyError as exc:
            raise FileNotFoundError(name) from exc


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

    def test_reads_authoritative_modes_and_auto_colorization(self) -> None:
        backend = _Winreg(
            {
                APPS_THEME_VALUE: (0, REG_DWORD),
                SYSTEM_THEME_VALUE: (1, REG_DWORD),
                AUTO_COLORIZATION_VALUE: (1, REG_DWORD),
                COLOR_PREVALENCE_VALUE: (1, REG_DWORD),
            }
        )
        with patch.object(WindowsAppearanceReader, "_winreg", return_value=backend):
            reader = WindowsAppearanceReader()
            self.assertEqual(reader.read_app_mode().value, "dark")
            self.assertEqual(reader.read_system_mode().value, "light")
            self.assertTrue(reader.read_auto_colorization())
            self.assertTrue(reader.read_start_taskbar_accent())
            self.assertTrue(reader.read_title_borders_accent())

    def test_rejects_missing_or_invalid_authoritative_mode(self) -> None:
        with (
            patch.object(
                WindowsAppearanceReader,
                "_winreg",
                return_value=_Winreg({APPS_THEME_VALUE: (2, REG_DWORD)}),
            ),
            self.assertRaises(CurrentAppearanceReadError),
        ):
            WindowsAppearanceReader().read_app_mode()

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


class WindowsAppearanceSettingsBackendTests(unittest.TestCase):
    def test_snapshot_rejects_untrusted_registry_shapes(self) -> None:
        valid = RegistryValue(True, 1, REG_DWORD)
        with self.assertRaisesRegex(ValueError, "REG_DWORD 0 or 1"):
            AppearanceRegistrySnapshot(
                apps_theme=valid,
                system_theme=RegistryValue(True, "light", 1),
                start_taskbar_accent=valid,
                title_borders_accent=valid,
            )
        with self.assertRaisesRegex(ValueError, "cannot carry data"):
            AppearanceRegistrySnapshot(
                apps_theme=valid,
                system_theme=valid,
                start_taskbar_accent=RegistryValue(False, 0, REG_DWORD),
                title_borders_accent=valid,
            )

    def test_writes_verifies_and_exactly_restores_accent_surfaces(self) -> None:
        from theme_scheduler.appearance import DWM_KEY, PERSONALIZE_KEY

        fake = _MutableWinreg(
            {
                (PERSONALIZE_KEY, APPS_THEME_VALUE): (1, REG_DWORD),
                (PERSONALIZE_KEY, SYSTEM_THEME_VALUE): (0, REG_DWORD),
                (PERSONALIZE_KEY, COLOR_PREVALENCE_VALUE): (1, REG_DWORD),
                (DWM_KEY, COLOR_PREVALENCE_VALUE): (0, REG_DWORD),
            }
        )
        with patch.object(
            WindowsAppearanceSettingsBackend,
            "_winreg",
            return_value=fake,
        ):
            backend = WindowsAppearanceSettingsBackend()
            before = backend.capture()
            backend.write_accent_surfaces(
                start_taskbar=False,
                title_borders=True,
            )

            self.assertEqual(
                backend.verify(
                    apps_theme=ThemeMode.LIGHT,
                    system_theme=ThemeMode.DARK,
                    start_taskbar=False,
                    title_borders=True,
                ),
                (),
            )
            backend.restore(before)
            self.assertEqual(backend.capture(), before)

    def test_restore_deletes_values_that_were_originally_absent(self) -> None:
        from theme_scheduler.appearance import PERSONALIZE_KEY

        fake = _MutableWinreg(
            {
                (PERSONALIZE_KEY, APPS_THEME_VALUE): (1, REG_DWORD),
                (PERSONALIZE_KEY, SYSTEM_THEME_VALUE): (0, REG_DWORD),
            }
        )
        with patch.object(
            WindowsAppearanceSettingsBackend,
            "_winreg",
            return_value=fake,
        ):
            backend = WindowsAppearanceSettingsBackend()
            before = backend.capture()
            backend.write_accent_surfaces(
                start_taskbar=True,
                title_borders=True,
            )
            backend.restore(before)

            self.assertFalse(backend.capture().start_taskbar_accent.exists)
            self.assertFalse(backend.capture().title_borders_accent.exists)

    def test_reports_mismatches_and_rejects_invalid_mutation_inputs(self) -> None:
        from theme_scheduler.appearance import DWM_KEY, PERSONALIZE_KEY

        fake = _MutableWinreg(
            {
                (PERSONALIZE_KEY, APPS_THEME_VALUE): (0, REG_DWORD),
                (PERSONALIZE_KEY, SYSTEM_THEME_VALUE): (1, REG_DWORD),
                (PERSONALIZE_KEY, COLOR_PREVALENCE_VALUE): (0, REG_DWORD),
                (DWM_KEY, COLOR_PREVALENCE_VALUE): (0, REG_DWORD),
            }
        )
        with patch.object(
            WindowsAppearanceSettingsBackend,
            "_winreg",
            return_value=fake,
        ):
            backend = WindowsAppearanceSettingsBackend()
            failures = backend.verify(
                apps_theme=ThemeMode.LIGHT,
                system_theme=ThemeMode.DARK,
                start_taskbar=True,
                title_borders=True,
            )
            self.assertEqual(len(failures), 4)
            with self.assertRaises(TypeError):
                backend._write_binary(PERSONALIZE_KEY, "test", 1)  # type: ignore[arg-type]
            with self.assertRaises(TypeError):
                backend.restore(object())  # type: ignore[arg-type]


class WindowsAccentColorReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reader = WindowsAccentColorReader(BRIDGE)

    @patch("theme_scheduler.appearance.subprocess.run")
    def test_reads_public_winrt_accent_without_a_console(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "action": "Read",
                    "source": "winrt-ui-settings",
                    "color": {
                        "alpha": 255,
                        "red": 116,
                        "green": 77,
                        "blue": 169,
                    },
                }
            ),
            stderr="",
        )

        color = self.reader.read_color()

        self.assertEqual(color.hex, "#744DA9")
        self.assertIn("-NonInteractive", run.call_args.args[0])
        self.assertEqual(
            run.call_args.kwargs["creationflags"],
            subprocess.CREATE_NO_WINDOW,
        )

    @patch("theme_scheduler.appearance.subprocess.run")
    def test_rejects_malformed_or_failed_bridge_results(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"ok":true,"action":"Read","color":{}}',
            stderr="",
        )
        with self.assertRaises(CurrentAppearanceReadError):
            self.reader.read_color()

        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=5,
            stdout="",
            stderr="first line\r\nsecond line",
        )
        with self.assertRaisesRegex(CurrentAppearanceReadError, "first line second"):
            self.reader.read_color()

    @patch("theme_scheduler.appearance.subprocess.run")
    def test_rejects_invalid_alpha_channel(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "action": "Read",
                    "source": "winrt-ui-settings",
                    "color": {
                        "alpha": True,
                        "red": 116,
                        "green": 77,
                        "blue": 169,
                    },
                }
            ),
            stderr="",
        )

        with self.assertRaisesRegex(CurrentAppearanceReadError, "alpha channel"):
            self.reader.read_color()

    def test_bridge_uses_only_public_ui_settings_for_accent(self) -> None:
        script = BRIDGE.read_text(encoding="utf-8")
        self.assertIn("Windows.UI.ViewManagement.UISettings", script)
        self.assertIn("UIColorType]::Accent", script)
        self.assertNotIn("AccentColorMenu", script)
        self.assertNotIn("AccentPalette", script)
        self.assertNotIn("CurrentTheme", script)


class WindowsCurrentAppearanceReaderTests(unittest.TestCase):
    def _registry(self) -> WindowsAppearanceReader:
        backend = _Winreg(
            {
                APPS_THEME_VALUE: (0, REG_DWORD),
                SYSTEM_THEME_VALUE: (0, REG_DWORD),
                AUTO_COLORIZATION_VALUE: (0, REG_DWORD),
                COLOR_PREVALENCE_VALUE: (1, REG_DWORD),
            }
        )
        patcher = patch.object(WindowsAppearanceReader, "_winreg", return_value=backend)
        patcher.start()
        self.addCleanup(patcher.stop)
        return WindowsAppearanceReader()

    def test_live_state_wins_when_active_theme_is_stale(self) -> None:
        theme = ThemeVisualState("0", 0xC4FF8C00, "Light", "Dark")
        reader = WindowsCurrentAppearanceReader(
            registry=self._registry(),
            accent=_AccentReader(RgbColor.from_hex("#744DA9")),
            theme_visual_reader=lambda: theme,
        )

        result = reader.read()

        self.assertEqual(result.visual.app_mode, "Dark")
        self.assertEqual(result.visual.system_mode, "Dark")
        self.assertEqual(result.visual.colorization_color, 0xC4744DA9)
        self.assertEqual(result.accent_source, "winrt-ui-settings")
        self.assertTrue(result.start_taskbar_accent)
        self.assertTrue(result.title_borders_accent)
        self.assertTrue(result.sources_diverged)
        self.assertEqual(result.divergences, ("app-mode", "accent-color"))
        self.assertEqual(result.theme_visual, theme)

    def test_unreadable_theme_does_not_replace_authoritative_live_state(self) -> None:
        def fail_theme() -> ThemeVisualState:
            raise OSError("theme unavailable")

        reader = WindowsCurrentAppearanceReader(
            registry=self._registry(),
            accent=_AccentReader(RgbColor.from_hex("#744DA9")),
            theme_visual_reader=fail_theme,
        )

        result = reader.read()

        self.assertEqual(result.visual.colorization_color, 0xFF744DA9)
        self.assertEqual(result.divergences, ("active-theme-unavailable",))
        self.assertIsNone(result.theme_visual)


if __name__ == "__main__":
    unittest.main()
