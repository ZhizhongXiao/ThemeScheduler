from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from uuid import UUID

from tests._accent_theme_support import ScriptedThemeApplyV2Backend
from theme_scheduler.accent import (
    DWM_PATH,
    EXPLORER_ACCENT_PATH,
    PERSONALIZE_PATH,
    AccentAnalysisError,
    AccentApplyError,
    AccentRegistryValue,
    accent_snapshot_from_diagnostic,
    analyze_accent_snapshot,
    apply_accent_snapshot,
)
from theme_scheduler.accent_theme import (
    LiveThemeApplyError,
    ThemeFileError,
    apply_and_verify_theme_v2,
    build_managed_theme,
    normalize_theme_visual_state,
    read_visual_state,
    resolve_current_theme_path,
    write_new_bytes,
)


def _value(name: str, data: object, type_code: int) -> dict[str, object]:
    return {"name": name, "data": data, "typeCode": type_code}


def _snapshot(
    *,
    palette: str = "e6cef000ba9cd4008963b800744da90065419900402775001f0e5400ef695000",
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "kind": "themescheduler.registry-baseline",
        "capturedAt": "2026-07-22T21:41:25+08:00",
        "keys": [
            {
                "root": "HKEY_CURRENT_USER",
                "path": PERSONALIZE_PATH,
                "values": [
                    _value("EnableTransparency", 1, 4),
                    _value("ColorPrevalence", 1, 4),
                ],
            },
            {
                "root": "HKEY_CURRENT_USER",
                "path": EXPLORER_ACCENT_PATH,
                "values": [
                    _value("AccentColorMenu", 0xFFA94D74, 4),
                    _value("StartColorMenu", 0xFF994165, 4),
                    _value("AccentPalette", {"encoding": "hex", "value": palette}, 3),
                ],
            },
            {
                "root": "HKEY_CURRENT_USER",
                "path": DWM_PATH,
                "values": [
                    _value("AccentColor", 0xFFA94D74, 4),
                    _value("ColorizationAfterglow", 0xC4744DA9, 4),
                    _value("ColorizationColor", 0xC4744DA9, 4),
                    _value("ColorPrevalence", 1, 4),
                ],
            },
        ],
    }


def _yellow_snapshot() -> dict[str, object]:
    snapshot = deepcopy(_snapshot())
    explorer = snapshot["keys"][1]["values"]
    explorer[0]["data"] = 0xFF00B9FF
    explorer[1]["data"] = 0xFF009DE1
    explorer[2]["data"]["value"] = (
        "ffe84500ffd52a00ffc20d00ffb90000e19d00009b5d00005c21000000b29400"
    )
    dwm = snapshot["keys"][2]["values"]
    dwm[0]["data"] = 0xFF00B9FF
    dwm[1]["data"] = 0xC4FFB900
    dwm[2]["data"] = 0xC4FFB900
    return snapshot


class FakeAccentBackend:
    def __init__(self, snapshot: dict[str, object]) -> None:
        target = accent_snapshot_from_diagnostic(snapshot)
        self.values = {}
        for field in (
            (EXPLORER_ACCENT_PATH, "AccentColorMenu", "explorer.accentColorMenu"),
            (EXPLORER_ACCENT_PATH, "AccentPalette", "explorer.accentPalette"),
            (EXPLORER_ACCENT_PATH, "StartColorMenu", "explorer.startColorMenu"),
            (DWM_PATH, "AccentColor", "dwm.accentColor"),
            (DWM_PATH, "ColorizationAfterglow", "dwm.colorizationAfterglow"),
            (DWM_PATH, "ColorizationColor", "dwm.colorizationColor"),
        ):
            self.values[(field[0], field[1])] = target.values[field[2]]
        self.writes: list[tuple[str, str, int | bytes | None]] = []
        self.notifications: list[int] = []
        self.notify_failure = False
        self.ignore_names: set[str] = set()

    def read_value(self, path: str, name: str) -> AccentRegistryValue:
        return self.values[(path, name)]

    def write_dword(self, path: str, name: str, value: int) -> None:
        self.writes.append((path, name, value))
        if name not in self.ignore_names:
            self.values[(path, name)] = AccentRegistryValue(True, value, 4)

    def write_binary(self, path: str, name: str, value: bytes) -> None:
        self.writes.append((path, name, value))
        if name not in self.ignore_names:
            self.values[(path, name)] = AccentRegistryValue(True, value, 3)

    def delete_value(self, path: str, name: str) -> None:
        self.writes.append((path, name, None))
        self.values[(path, name)] = AccentRegistryValue(False)

    def notify_accent_change(self, colorization_color: int) -> None:
        self.notifications.append(colorization_color)
        if self.notify_failure and len(self.notifications) == 1:
            raise OSError("simulated notification failure")


class AccentAnalysisTests(unittest.TestCase):
    def test_decodes_baseline_color_palette_and_switches(self) -> None:
        result = analyze_accent_snapshot(_snapshot())

        self.assertEqual(result["colors"]["accentColorMenu"], "#744DA9")
        self.assertEqual(result["colors"]["dwmAccentColor"], "#744DA9")
        self.assertEqual(result["colors"]["colorizationColor"], "#744DA9")
        self.assertEqual(result["colors"]["palette"][3]["color"], "#744DA9")
        self.assertTrue(result["visibleSettings"]["transparencyEnabled"])
        self.assertTrue(result["visibleSettings"]["startAndTaskbarAccentEnabled"])
        self.assertTrue(result["visibleSettings"]["titleBarsAndBordersAccentEnabled"])

    def test_rejects_palette_with_unexpected_length(self) -> None:
        with self.assertRaisesRegex(AccentAnalysisError, "32 bytes"):
            analyze_accent_snapshot(_snapshot(palette="744da900"))

    def test_rejects_wrong_dword_type(self) -> None:
        snapshot = _snapshot()
        snapshot["keys"][0]["values"][0]["typeCode"] = 1
        with self.assertRaisesRegex(AccentAnalysisError, "REG_DWORD"):
            analyze_accent_snapshot(snapshot)

    def test_rejects_non_boolean_switch_value(self) -> None:
        snapshot = _snapshot()
        snapshot["keys"][0]["values"][0]["data"] = 2
        with self.assertRaisesRegex(AccentAnalysisError, "must be 0 or 1"):
            analyze_accent_snapshot(snapshot)

    def test_missing_candidates_are_reported_as_unknown(self) -> None:
        snapshot = _snapshot()
        snapshot["keys"] = []
        result = analyze_accent_snapshot(snapshot)
        self.assertIsNone(result["colors"]["accentColorMenu"])
        self.assertIsNone(result["colors"]["palette"])
        self.assertIsNone(result["visibleSettings"]["transparencyEnabled"])


class AccentApplyTests(unittest.TestCase):
    def test_applies_all_six_changed_fields_and_notifies(self) -> None:
        backend = FakeAccentBackend(_snapshot())
        target = accent_snapshot_from_diagnostic(_yellow_snapshot())

        result = apply_accent_snapshot(target, backend)

        self.assertTrue(result.verified)
        self.assertEqual(len(result.changed_fields), 6)
        self.assertEqual(len(backend.writes), 6)
        self.assertEqual(backend.notifications, [0xC4FFB900])

    def test_repeated_apply_is_idempotent(self) -> None:
        backend = FakeAccentBackend(_yellow_snapshot())
        target = accent_snapshot_from_diagnostic(_yellow_snapshot())

        result = apply_accent_snapshot(target, backend)

        self.assertFalse(result.changed)
        self.assertEqual(backend.writes, [])
        self.assertEqual(backend.notifications, [])

    def test_notification_failure_rolls_back_original_snapshot(self) -> None:
        backend = FakeAccentBackend(_snapshot())
        backend.notify_failure = True
        target = accent_snapshot_from_diagnostic(_yellow_snapshot())

        with self.assertRaises(AccentApplyError) as caught:
            apply_accent_snapshot(target, backend)

        self.assertTrue(caught.exception.rollback_succeeded)
        self.assertEqual(backend.notifications[-1], 0xC4744DA9)
        original = accent_snapshot_from_diagnostic(_snapshot())
        for path, name, identifier in (
            (EXPLORER_ACCENT_PATH, "AccentColorMenu", "explorer.accentColorMenu"),
            (EXPLORER_ACCENT_PATH, "AccentPalette", "explorer.accentPalette"),
            (EXPLORER_ACCENT_PATH, "StartColorMenu", "explorer.startColorMenu"),
            (DWM_PATH, "AccentColor", "dwm.accentColor"),
            (DWM_PATH, "ColorizationAfterglow", "dwm.colorizationAfterglow"),
            (DWM_PATH, "ColorizationColor", "dwm.colorizationColor"),
        ):
            self.assertEqual(backend.values[(path, name)], original.values[identifier])

    def test_readback_mismatch_rolls_back(self) -> None:
        backend = FakeAccentBackend(_snapshot())
        backend.ignore_names.add("AccentPalette")
        target = accent_snapshot_from_diagnostic(_yellow_snapshot())

        with self.assertRaises(AccentApplyError) as caught:
            apply_accent_snapshot(target, backend)

        self.assertTrue(caught.exception.rollback_succeeded)


def _theme_bytes(color: str = "0XC4FFB900") -> bytes:
    """
    return (
        b"; Copyright \xa9 Microsoft Corp.\r\n\r\n"
        b"[Theme]\r\n"
        b"DisplayName=\xce´±£´æµÄÖ÷Ìâ\r\n"
        b"ThemeId={65CC0448-76B8-4EB2-ADF7-D3186669AAC9}\r\n\r\n"
        b"[VisualStyles]\r\n"
        b"Path=%SystemRoot%\\resources\\themes\\Aero\\Aero.msstyles\r\n"
        b"AutoColorization=0\r\n"
        + f"ColorizationColor={color}\r\n".encode("ascii")
        + b"SystemMode=Dark\r\n"
        b"AppMode=Dark\r\n"
        b"VisualStyleVersion=10\r\n\r\n"
        b"[Sounds]\r\nSchemeName=@mmres.dll,-800\r\n"
    )
    """
    return (
        b"; Copyright \xa9 Microsoft Corp.\r\n\r\n"
        b"[Theme]\r\n"
        b"DisplayName=" + bytes.fromhex("ceb4b1a3b4e6b5c4d6f7cce2") + b"\r\n"
        b"ThemeId={65CC0448-76B8-4EB2-ADF7-D3186669AAC9}\r\n\r\n"
        b"[VisualStyles]\r\n"
        b"Path=%SystemRoot%\\resources\\themes\\Aero\\Aero.msstyles\r\n"
        b"AutoColorization=0\r\n"
        + f"ColorizationColor={color}\r\n".encode("ascii")
        + b"SystemMode=Dark\r\n"
        b"AppMode=Dark\r\n"
        b"VisualStyleVersion=10\r\n\r\n"
        b"[Sounds]\r\nSchemeName=@mmres.dll,-800\r\n"
    )


class ManagedThemeFileTests(unittest.TestCase):
    def test_normalize_visual_state_preserves_theme_identity_and_system_mode(
        self,
    ) -> None:
        source = _theme_bytes()

        normalized = normalize_theme_visual_state(
            source,
            0xC4744DA9,
            auto_colorization=True,
            app_mode="Light",
        )

        self.assertIn(
            b"DisplayName=" + bytes.fromhex("ceb4b1a3b4e6b5c4d6f7cce2"),
            normalized,
        )
        self.assertIn(
            b"ThemeId={65CC0448-76B8-4EB2-ADF7-D3186669AAC9}",
            normalized,
        )
        visual = read_visual_state(normalized)
        self.assertEqual(visual.auto_colorization, "1")
        self.assertEqual(visual.colorization_color, 0xC4744DA9)
        self.assertEqual(visual.app_mode, "Light")
        self.assertEqual(visual.system_mode, "Dark")

    def test_missing_current_theme_uses_custom_theme_fallback(self) -> None:
        path = resolve_current_theme_path(None, r"C:\Users\tester\AppData\Local")
        self.assertEqual(
            path,
            Path(
                r"C:\Users\tester\AppData\Local\Microsoft\Windows\Themes\Custom.theme"
            ),
        )

    def test_build_changes_only_metadata_and_visual_color_targets(self) -> None:
        source = _theme_bytes()
        identifier = UUID("11111111-2222-3333-4444-555555555555")

        managed = build_managed_theme(source, 0xC4744DA9, theme_id=identifier)

        self.assertIn(b"DisplayName=ThemeScheduler Accent Prototype", managed.content)
        self.assertIn(
            b"ThemeId={11111111-2222-3333-4444-555555555555}", managed.content
        )
        self.assertIn(b"ColorizationColor=0XC4744DA9", managed.content)
        """
        self.assertIn(b"\xce´±£", source)
        """
        self.assertIn(bytes.fromhex("ceb4b1a3"), source)
        self.assertEqual(managed.before.app_mode, "Dark")
        self.assertEqual(managed.after.app_mode, "Dark")
        self.assertEqual(managed.after.system_mode, "Dark")
        self.assertEqual(managed.after.colorization_color, 0xC4744DA9)
        self.assertIn(b"SchemeName=@mmres.dll,-800", managed.content)

    def test_build_can_preserve_enabled_auto_colorization_intent(self) -> None:
        managed = build_managed_theme(
            _theme_bytes(),
            0xC4744DA9,
            auto_colorization=True,
        )

        self.assertEqual(managed.after.auto_colorization, "1")

    def test_build_can_combine_target_app_mode_and_accent(self) -> None:
        managed = build_managed_theme(
            _theme_bytes(),
            0xC4744DA9,
            app_mode="Light",
        )

        self.assertEqual(managed.before.app_mode, "Dark")
        self.assertEqual(managed.after.app_mode, "Light")
        self.assertEqual(managed.after.system_mode, "Dark")
        self.assertEqual(managed.after.colorization_color, 0xC4744DA9)

    def test_build_rejects_unknown_app_mode(self) -> None:
        with self.assertRaisesRegex(ThemeFileError, "AppMode"):
            build_managed_theme(
                _theme_bytes(),
                0xC4744DA9,
                app_mode="Automatic",
            )

    def test_read_rejects_duplicate_colorization_value(self) -> None:
        source = _theme_bytes().replace(
            b"ColorizationColor=0XC4FFB900\r\n",
            b"ColorizationColor=0XC4FFB900\r\nColorizationColor=0XC4744DA9\r\n",
        )
        with self.assertRaisesRegex(ThemeFileError, "exactly one"):
            read_visual_state(source)

    def test_write_new_bytes_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "managed.theme"
            write_new_bytes(path, b"first")
            with self.assertRaisesRegex(ThemeFileError, "Refusing to overwrite"):
                write_new_bytes(path, b"second")
            self.assertEqual(path.read_bytes(), b"first")


class ManagedThemeApplyTests(unittest.TestCase):
    def test_v2_apply_accepts_combined_app_mode_and_accent_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "before.theme"
            managed_path = root / "managed.theme"
            backup.write_bytes(_theme_bytes())
            managed = build_managed_theme(
                backup.read_bytes(),
                0xC4744DA9,
                app_mode="Light",
            )
            managed_path.write_bytes(managed.content)
            backend = ScriptedThemeApplyV2Backend(backup, managed_path)

            result = apply_and_verify_theme_v2(
                managed_path,
                managed.after,
                managed.before,
                backend=backend,
                settle_seconds=0,
            )

            self.assertEqual(result.actual.app_mode, "Light")
            self.assertEqual(result.actual.system_mode, "Dark")
            self.assertEqual(result.actual.colorization_color, 0xC4744DA9)

    def test_v2_apply_accepts_windows_normalizing_to_custom_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "before.theme"
            managed_path = root / "managed.theme"
            backup.write_bytes(_theme_bytes())
            managed = build_managed_theme(
                backup.read_bytes(),
                0xC4744DA9,
                theme_id=UUID("11111111-2222-3333-4444-555555555555"),
            )
            managed_path.write_bytes(managed.content)
            backend = ScriptedThemeApplyV2Backend(
                backup, managed_path, normalize_to_custom=True
            )

            result = apply_and_verify_theme_v2(
                managed_path,
                managed.after,
                managed.before,
                backend=backend,
                settle_seconds=0,
            )

            self.assertEqual(result.actual.colorization_color, 0xC4744DA9)
            self.assertEqual(
                (
                    result.index_before,
                    result.bridge_target_index,
                    result.index_after,
                ),
                (6, 13, 0),
            )
            self.assertNotIn("set_v2_index", [call[0] for call in backend.calls])

    def test_v2_apply_verifies_new_theme_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "before.theme"
            managed_path = root / "managed.theme"
            backup.write_bytes(_theme_bytes())
            managed = build_managed_theme(
                backup.read_bytes(),
                0xC4744DA9,
                theme_id=UUID("11111111-2222-3333-4444-555555555555"),
            )
            managed_path.write_bytes(managed.content)
            backend = ScriptedThemeApplyV2Backend(backup, managed_path)

            result = apply_and_verify_theme_v2(
                managed_path,
                managed.after,
                managed.before,
                backend=backend,
                settle_seconds=0,
            )

            self.assertEqual(result.active_path, managed_path)
            self.assertEqual(result.actual.colorization_color, 0xC4744DA9)
            self.assertEqual((result.index_before, result.bridge_target_index), (6, 13))
            self.assertEqual(result.index_after, 13)
            self.assertNotIn("set_v2_index", [call[0] for call in backend.calls])

    def test_v2_verification_failure_restores_original_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "before.theme"
            managed_path = root / "managed.theme"
            backup.write_bytes(_theme_bytes())
            managed = build_managed_theme(
                backup.read_bytes(),
                0xC4744DA9,
                theme_id=UUID("11111111-2222-3333-4444-555555555555"),
            )
            managed_path.write_bytes(managed.content)
            backend = ScriptedThemeApplyV2Backend(backup, backup)

            with self.assertRaises(LiveThemeApplyError) as caught:
                apply_and_verify_theme_v2(
                    managed_path,
                    managed.after,
                    managed.before,
                    backend=backend,
                    settle_seconds=0,
                )

            self.assertTrue(caught.exception.rollback_succeeded)
            self.assertEqual(
                [call for call in backend.calls if call[0] == "set_v2_index"],
                [("set_v2_index", 6)],
            )

    def test_v2_rollback_uses_complete_theme_backup_when_index_is_insufficient(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "before.theme"
            managed_path = root / "managed.theme"
            wrong_path = root / "wrong.theme"
            backup.write_bytes(_theme_bytes())
            managed = build_managed_theme(
                backup.read_bytes(),
                0xC4744DA9,
                theme_id=UUID("11111111-2222-3333-4444-555555555555"),
            )
            managed_path.write_bytes(managed.content)
            wrong_path.write_bytes(_theme_bytes("0XC40078D4"))

            backend = ScriptedThemeApplyV2Backend(backup, managed_path)
            backend.apply_active_paths.append(wrong_path)
            backend.restore_original_on_set = False
            with self.assertRaises(LiveThemeApplyError) as caught:
                apply_and_verify_theme_v2(
                    managed_path,
                    managed.after,
                    managed.before,
                    rollback_path=backup,
                    backend=backend,
                    settle_seconds=0,
                )

            self.assertTrue(caught.exception.rollback_succeeded)
            self.assertEqual(
                [call[0] for call in backend.calls].count("apply_theme_v2"),
                2,
            )
            self.assertEqual(
                read_visual_state(backend.active.read_bytes()), managed.before
            )

    def test_v2_initial_index_failure_does_not_mutate_or_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "before.theme"
            managed_path = root / "managed.theme"
            backup.write_bytes(_theme_bytes())
            managed_path.write_bytes(_theme_bytes("0XC4744DA9"))
            backend = ScriptedThemeApplyV2Backend(backup, managed_path)
            backend.fail_next("current_v2_index", OSError("index unavailable"))

            with self.assertRaisesRegex(OSError, "index unavailable"):
                apply_and_verify_theme_v2(
                    managed_path,
                    read_visual_state(managed_path.read_bytes()),
                    read_visual_state(backup.read_bytes()),
                    backend=backend,
                    settle_seconds=0,
                )

            self.assertEqual(backend.calls, [("current_v2_index", None)])

    def test_v2_bridge_before_drift_rolls_back_before_further_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "before.theme"
            managed_path = root / "managed.theme"
            backup.write_bytes(_theme_bytes())
            managed = build_managed_theme(backup.read_bytes(), 0xC4744DA9)
            managed_path.write_bytes(managed.content)
            backend = ScriptedThemeApplyV2Backend(backup, managed_path)
            backend.bridge_before = 5

            with self.assertRaises(LiveThemeApplyError) as caught:
                apply_and_verify_theme_v2(
                    managed_path,
                    managed.after,
                    managed.before,
                    backend=backend,
                    settle_seconds=0,
                )

            self.assertIn("6 -> 5", str(caught.exception))
            self.assertTrue(caught.exception.rollback_succeeded)
            operations = [call[0] for call in backend.calls]
            self.assertNotIn("current_v2_indices", operations)
            self.assertIn("set_v2_index", operations)

    def test_v2_rejects_index_outside_target_and_custom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "before.theme"
            managed_path = root / "managed.theme"
            backup.write_bytes(_theme_bytes())
            managed = build_managed_theme(backup.read_bytes(), 0xC4744DA9)
            managed_path.write_bytes(managed.content)
            backend = ScriptedThemeApplyV2Backend(backup, managed_path)
            backend.current_indices_override = (12, 0)

            with self.assertRaises(LiveThemeApplyError) as caught:
                apply_and_verify_theme_v2(
                    managed_path,
                    managed.after,
                    managed.before,
                    backend=backend,
                    settle_seconds=0,
                )

            self.assertIn(
                "matched neither target 13 nor custom 0", str(caught.exception)
            )
            self.assertTrue(caught.exception.rollback_succeeded)

    def test_v2_reports_each_visual_mismatch_and_restores_index(self) -> None:
        replacements = (
            (
                b"AutoColorization=0",
                b"AutoColorization=1",
                "AutoColorization",
            ),
            (
                b"ColorizationColor=0XC4744DA9",
                b"ColorizationColor=0XC40078D4",
                "ColorizationColor",
            ),
            (b"AppMode=Dark", b"AppMode=Light", "AppMode"),
            (b"SystemMode=Dark", b"SystemMode=Light", "SystemMode"),
        )
        for original, replacement, message in replacements:
            with (
                self.subTest(message=message),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                backup = root / "before.theme"
                managed_path = root / "managed.theme"
                wrong_path = root / "wrong.theme"
                backup.write_bytes(_theme_bytes())
                managed = build_managed_theme(backup.read_bytes(), 0xC4744DA9)
                managed_path.write_bytes(managed.content)
                wrong_path.write_bytes(managed.content.replace(original, replacement))
                backend = ScriptedThemeApplyV2Backend(backup, managed_path)
                backend.apply_active_paths.append(wrong_path)

                with self.assertRaises(LiveThemeApplyError) as caught:
                    apply_and_verify_theme_v2(
                        managed_path,
                        managed.after,
                        managed.before,
                        backend=backend,
                        settle_seconds=0,
                    )

                self.assertIn(message, str(caught.exception))
                self.assertTrue(caught.exception.rollback_succeeded)

    def test_v2_index_restore_exception_uses_complete_theme_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "before.theme"
            managed_path = root / "managed.theme"
            wrong_path = root / "wrong.theme"
            backup.write_bytes(_theme_bytes())
            managed = build_managed_theme(backup.read_bytes(), 0xC4744DA9)
            managed_path.write_bytes(managed.content)
            wrong_path.write_bytes(_theme_bytes("0XC40078D4"))
            backend = ScriptedThemeApplyV2Backend(backup, managed_path)
            backend.apply_active_paths.append(wrong_path)
            backend.fail_next("set_v2_index", OSError("set failed"))

            with self.assertRaises(LiveThemeApplyError) as caught:
                apply_and_verify_theme_v2(
                    managed_path,
                    managed.after,
                    managed.before,
                    rollback_path=backup,
                    backend=backend,
                    settle_seconds=0,
                )

            self.assertTrue(caught.exception.rollback_succeeded)
            self.assertEqual(
                [call[0] for call in backend.calls].count("apply_theme_v2"),
                2,
            )

    def test_v2_failed_fallback_preserves_primary_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "before.theme"
            managed_path = root / "managed.theme"
            wrong_path = root / "wrong.theme"
            backup.write_bytes(_theme_bytes())
            managed = build_managed_theme(backup.read_bytes(), 0xC4744DA9)
            managed_path.write_bytes(managed.content)
            wrong_path.write_bytes(_theme_bytes("0XC40078D4"))
            backend = ScriptedThemeApplyV2Backend(backup, managed_path)
            backend.apply_active_paths.append(wrong_path)
            backend.restore_original_on_set = False
            backend.allow_next("apply_theme_v2")
            backend.fail_next("apply_theme_v2", OSError("fallback failed"))

            with self.assertRaises(LiveThemeApplyError) as caught:
                apply_and_verify_theme_v2(
                    managed_path,
                    managed.after,
                    managed.before,
                    rollback_path=backup,
                    backend=backend,
                    settle_seconds=0,
                )

            self.assertFalse(caught.exception.rollback_succeeded)
            self.assertIsInstance(caught.exception.__cause__, ThemeFileError)
            self.assertIn("ColorizationColor", str(caught.exception.__cause__))
            self.assertNotIn("fallback failed", str(caught.exception))

    def test_v2_failed_index_restore_without_backup_reports_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "before.theme"
            managed_path = root / "managed.theme"
            wrong_path = root / "wrong.theme"
            backup.write_bytes(_theme_bytes())
            managed = build_managed_theme(backup.read_bytes(), 0xC4744DA9)
            managed_path.write_bytes(managed.content)
            wrong_path.write_bytes(_theme_bytes("0XC40078D4"))
            backend = ScriptedThemeApplyV2Backend(backup, managed_path)
            backend.apply_active_paths.append(wrong_path)
            backend.restore_original_on_set = False

            with self.assertRaises(LiveThemeApplyError) as caught:
                apply_and_verify_theme_v2(
                    managed_path,
                    managed.after,
                    managed.before,
                    backend=backend,
                    settle_seconds=0,
                )

            self.assertFalse(caught.exception.rollback_succeeded)
            self.assertEqual(
                [call[0] for call in backend.calls].count("apply_theme_v2"),
                1,
            )


class ThemeManagerBridgeContractTests(unittest.TestCase):
    def test_bridge_exposes_only_the_production_v2_actions(self) -> None:
        bridge = (
            Path(__file__).resolve().parents[1]
            / "entrypoints"
            / "theme_manager_bridge.ps1"
        ).read_text(encoding="utf-8-sig")

        self.assertIn(
            "[ValidateSet('CurrentV2', 'ApplyV2', 'SetV2')]",
            bridge,
        )
        self.assertNotIn("ProbeV2", bridge)
        self.assertNotIn("ApplyTheme", bridge)
        self.assertNotIn("action = 'Apply'", bridge)
        self.assertIn("action = 'ApplyV2'", bridge)


if __name__ == "__main__":
    unittest.main()
