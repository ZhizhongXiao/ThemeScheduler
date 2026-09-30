from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from tests._accent_theme_support import ScriptedThemeApplyV2Backend
from tests.fixtures.theme_files import (
    spotlight_theme_without_id,
    windows_11_variant_theme,
)
from theme_scheduler.accent_theme import (
    LiveThemeApplyError,
    ManagedTheme,
    ThemeFileError,
    apply_and_verify_theme_v2,
    build_managed_theme,
    materialize_theme_visual_state,
    normalize_theme_visual_state,
    read_visual_state,
    resolve_current_theme_path,
    write_new_bytes,
)


def theme_bytes(color: str = "0XC4FFB900") -> bytes:
    return (
        b"[Theme]\r\n"
        b"DisplayName=Original\r\n"
        b"ThemeId={65CC0448-76B8-4EB2-ADF7-D3186669AAC9}\r\n\r\n"
        b"[VisualStyles]\r\n"
        b"AutoColorization=0\r\n"
        + f"ColorizationColor={color}\r\n".encode("ascii")
        + b"SystemMode=Dark\r\n"
        b"AppMode=Dark\r\n\r\n"
        b"[Sounds]\r\nSchemeName=Default\r\n"
    )


class ManagedThemeFileTests(unittest.TestCase):
    def test_materializes_windows_11_variant_without_losing_source_sections(
        self,
    ) -> None:
        source = windows_11_variant_theme()
        current = read_visual_state(theme_bytes())

        materialized = materialize_theme_visual_state(source, current)

        self.assertTrue(materialized.startswith(source))
        self.assertEqual(materialized.count(b"[VisualStyles]"), 1)
        self.assertEqual(materialized.count(b"[MasterThemeSelector]"), 1)
        self.assertIn(b"[Theme.A]\r\nDisplayName=Custom", materialized)
        self.assertIn(b"[Theme.W]\r\nDisplayName=Custom", materialized)
        self.assertEqual(read_visual_state(materialized), current)

    def test_build_accepts_variant_and_system_theme_without_theme_id(self) -> None:
        current = read_visual_state(theme_bytes())
        identifier = UUID("11111111-2222-3333-4444-555555555555")

        variant = build_managed_theme(
            windows_11_variant_theme(),
            0xC4744DA9,
            app_mode="Light",
            theme_id=identifier,
            current_state=current,
        )
        spotlight = build_managed_theme(
            spotlight_theme_without_id(),
            0xC4744DA9,
            app_mode="Dark",
            theme_id=identifier,
            current_state=current,
        )

        expected_id = b"ThemeId={11111111-2222-3333-4444-555555555555}"
        self.assertIn(expected_id, variant.content)
        self.assertIn(expected_id, spotlight.content)
        self.assertEqual(variant.after.app_mode, "Light")
        self.assertEqual(spotlight.after.app_mode, "Dark")
        self.assertIn(b"Wallpaper=%SystemRoot%", spotlight.content)

    def test_materialization_preserves_existing_master_selector(self) -> None:
        source = theme_bytes() + b"\r\n[MasterThemeSelector]\r\nMTSM=DABJDKT\r\n"

        materialized = materialize_theme_visual_state(
            source,
            read_visual_state(source),
        )

        self.assertIn(b"MTSM=DABJDKT", materialized)
        self.assertNotIn(b"MTSM=RJSPBS", materialized)

    def test_build_combines_app_mode_and_color_without_changing_system_mode(
        self,
    ) -> None:
        managed = build_managed_theme(
            theme_bytes(),
            0xC4744DA9,
            app_mode="Light",
            theme_id=UUID("11111111-2222-3333-4444-555555555555"),
        )
        self.assertEqual(managed.before.app_mode, "Dark")
        self.assertEqual(managed.after.app_mode, "Light")
        self.assertEqual(managed.after.system_mode, "Dark")
        self.assertEqual(managed.after.colorization_color, 0xC4744DA9)

    def test_build_can_target_app_and_windows_modes_together(self) -> None:
        managed = build_managed_theme(
            theme_bytes(),
            0xC4744DA9,
            app_mode="Light",
            system_mode="Light",
            theme_id=UUID("11111111-2222-3333-4444-555555555555"),
        )

        self.assertEqual(managed.before.system_mode, "Dark")
        self.assertEqual(managed.after.app_mode, "Light")
        self.assertEqual(managed.after.system_mode, "Light")

    def test_normalize_preserves_theme_identity(self) -> None:
        normalized = normalize_theme_visual_state(
            theme_bytes(),
            0xC4744DA9,
            auto_colorization=True,
            app_mode="Light",
        )
        self.assertIn(b"DisplayName=Original", normalized)
        self.assertIn(b"ThemeId={65CC0448-76B8-4EB2-ADF7-D3186669AAC9}", normalized)
        self.assertEqual(read_visual_state(normalized).auto_colorization, "1")

    def test_invalid_app_mode_and_duplicate_value_are_rejected(self) -> None:
        with self.assertRaisesRegex(ThemeFileError, "AppMode"):
            build_managed_theme(theme_bytes(), 0xC4744DA9, app_mode="Automatic")
        duplicate = theme_bytes().replace(
            b"AutoColorization=0\r\n",
            b"AutoColorization=0\r\nAutoColorization=1\r\n",
        )
        with self.assertRaisesRegex(ThemeFileError, "exactly one"):
            read_visual_state(duplicate)

    def test_fallback_path_and_exclusive_write_contract(self) -> None:
        self.assertEqual(
            resolve_current_theme_path(None, r"C:\Users\tester\AppData\Local"),
            Path(
                r"C:\Users\tester\AppData\Local\Microsoft\Windows\Themes\Custom.theme"
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "managed.theme"
            write_new_bytes(target, b"first")
            with self.assertRaisesRegex(ThemeFileError, "Refusing to overwrite"):
                write_new_bytes(target, b"second")


class ManagedThemeApplyTests(unittest.TestCase):
    def _fixture(
        self, root: Path
    ) -> tuple[Path, Path, ManagedTheme, ScriptedThemeApplyV2Backend]:
        backup = root / "before.theme"
        target = root / "managed.theme"
        backup.write_bytes(theme_bytes())
        managed = build_managed_theme(
            backup.read_bytes(),
            0xC4744DA9,
            app_mode="Light",
        )
        target.write_bytes(managed.content)
        return backup, target, managed, ScriptedThemeApplyV2Backend(backup, target)

    def test_success_verifies_visual_state_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _backup, target, managed, backend = self._fixture(Path(directory))
            result = apply_and_verify_theme_v2(
                target,
                managed.after,
                managed.before,
                backend=backend,
                settle_seconds=0,
                verification_timeout_seconds=0.05,
            )
        self.assertEqual(result.actual.app_mode, "Light")
        self.assertEqual(result.index_after, 13)
        self.assertTrue(backend.bridge_budgets)
        self.assertTrue(
            all(budget is not None and budget > 0 for budget in backend.bridge_budgets)
        )

    def test_expired_apply_budget_does_not_start_apply_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _backup, target, managed, backend = self._fixture(Path(directory))
            with self.assertRaises(ThemeFileError):
                apply_and_verify_theme_v2(
                    target,
                    managed.after,
                    managed.before,
                    backend=backend,
                    settle_seconds=0,
                    operation_timeout_seconds=0,
                )

        self.assertEqual(backend.apply_count, 0)
        self.assertNotIn("apply_theme_v2", [name for name, _ in backend.calls])

    def test_verification_polls_until_visual_state_converges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _backup, target, managed, backend = self._fixture(Path(directory))
            reads = iter((managed.before, managed.after))

            def read_visual_state():
                return next(reads, managed.after)

            result = apply_and_verify_theme_v2(
                target,
                managed.after,
                managed.before,
                backend=backend,
                settle_seconds=0,
                verification_timeout_seconds=0.1,
                verification_poll_interval_seconds=0.001,
                visual_state_reader=read_visual_state,
            )

        diagnostics = result.verification_diagnostics
        samples = diagnostics["samples"]
        self.assertIsInstance(samples, list)
        assert isinstance(samples, list)
        self.assertEqual(len(samples), 2)
        first_sample = samples[0]
        final_sample = samples[-1]
        assert isinstance(first_sample, dict)
        assert isinstance(final_sample, dict)
        self.assertIn("observedAt", first_sample)
        self.assertEqual(
            first_sample["actual"],
            managed.before.as_dict(),
        )
        self.assertEqual(final_sample["actual"], managed.after.as_dict())
        self.assertEqual(final_sample["activeThemePath"], str(target))
        self.assertEqual(final_sample["currentIndex"], 13)
        self.assertEqual(final_sample["failures"], [])

    def test_persistent_visual_mismatch_records_readback_before_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup, target, managed, backend = self._fixture(Path(directory))
            with self.assertRaises(LiveThemeApplyError) as caught:
                apply_and_verify_theme_v2(
                    target,
                    managed.after,
                    managed.before,
                    backend=backend,
                    settle_seconds=0,
                    verification_timeout_seconds=0.5,
                    verification_poll_interval_seconds=0.001,
                    visual_state_reader=lambda: managed.before,
                )

        error = caught.exception
        self.assertTrue(error.rollback_succeeded)
        self.assertIn("did not converge", str(error))
        self.assertIn("ColorizationColor", str(error))
        diagnostics = error.verification_diagnostics
        self.assertIsNotNone(diagnostics)
        assert diagnostics is not None
        self.assertEqual(diagnostics["expected"], managed.after.as_dict())
        samples = diagnostics["samples"]
        self.assertIsInstance(samples, list)
        assert isinstance(samples, list)
        self.assertGreater(len(samples), 1)
        last_sample = samples[-1]
        assert isinstance(last_sample, dict)
        self.assertEqual(last_sample["actual"], managed.before.as_dict())
        self.assertEqual(last_sample["activeThemePath"], str(target))
        index_samples = [sample for sample in samples if "currentIndex" in sample]
        self.assertTrue(index_samples)
        self.assertEqual(index_samples[-1]["currentIndex"], 13)
        self.assertIn("observedAt", last_sample)
        self.assertEqual(backend.current_theme_path(), backup)

    def test_visual_failure_restores_original_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup, target, managed, backend = self._fixture(Path(directory))
            backend.apply_active_paths.append(backup)
            with self.assertRaises(LiveThemeApplyError) as caught:
                apply_and_verify_theme_v2(
                    target,
                    managed.after,
                    managed.before,
                    backend=backend,
                    settle_seconds=0,
                    verification_timeout_seconds=0.05,
                    operation_timeout_seconds=2,
                    rollback_timeout_seconds=3,
                )
        self.assertTrue(caught.exception.rollback_succeeded)
        self.assertIn(("set_v2_index", 6), backend.calls)

    def test_backup_fallback_runs_when_index_restore_is_insufficient(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup, target, managed, backend = self._fixture(root)
            wrong = root / "wrong.theme"
            wrong.write_bytes(theme_bytes("0XC40078D4"))
            backend.apply_active_paths.append(wrong)
            backend.restore_original_on_set = False
            with self.assertRaises(LiveThemeApplyError) as caught:
                apply_and_verify_theme_v2(
                    target,
                    managed.after,
                    managed.before,
                    rollback_path=backup,
                    backend=backend,
                    settle_seconds=0,
                    verification_timeout_seconds=0.05,
                    operation_timeout_seconds=2,
                    rollback_timeout_seconds=3,
                )
        self.assertTrue(caught.exception.rollback_succeeded)
        self.assertEqual(
            [call[0] for call in backend.calls].count("apply_theme_v2"),
            2,
        )
        self.assertEqual(len(backend.apply_budgets), 2)
        first_budget = backend.apply_budgets[0]
        second_budget = backend.apply_budgets[1]
        assert first_budget is not None
        assert second_budget is not None
        self.assertGreater(first_budget, 0)
        self.assertLessEqual(first_budget, 2)
        self.assertGreater(second_budget, 0)
        self.assertLessEqual(second_budget, 3)


if __name__ == "__main__":
    unittest.main()
