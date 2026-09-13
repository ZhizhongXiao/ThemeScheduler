from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from theme_scheduler.config import AppConfig
from theme_scheduler.core import (
    EXIT_CODE_BY_RESULT,
    AutoExitCode,
    AutoPlanKind,
    AutoResultKind,
    RunIntent,
    mutex_name_for_data_root,
    plan_auto_run,
    target_profile_at,
)
from theme_scheduler.state import AppState


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 7, 24, hour, minute, tzinfo=UTC)


def _successful_state(active: str) -> AppState:
    return AppState(
        False,
        active,
        "2026-07-24T06:15:00+08:00",
        active,
        "success",
    )


class TimeDecisionTests(unittest.TestCase):
    def test_default_boundaries_and_adjacent_minutes(self) -> None:
        config = AppConfig.defaults()
        expected = {
            (6, 14): "night",
            (6, 15): "day",
            (6, 16): "day",
            (23, 44): "day",
            (23, 45): "night",
            (23, 46): "night",
        }

        for parts, profile in expected.items():
            with self.subTest(parts=parts):
                self.assertEqual(target_profile_at(config, _at(*parts)), profile)

    def test_day_interval_can_cross_midnight(self) -> None:
        config = AppConfig("22:00", "06:00", "dark", "light", True, True)

        self.assertEqual(target_profile_at(config, _at(21, 59)), "night")
        self.assertEqual(target_profile_at(config, _at(22, 0)), "day")
        self.assertEqual(target_profile_at(config, _at(0, 0)), "day")
        self.assertEqual(target_profile_at(config, _at(5, 59)), "day")
        self.assertEqual(target_profile_at(config, _at(6, 0)), "night")

    def test_naive_datetime_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "aware"):
            target_profile_at(AppConfig.defaults(), datetime(2026, 7, 24, 6, 15))


class ExplicitPlanDecisionTests(unittest.TestCase):
    def test_paused_plan_contains_no_write_targets(self) -> None:
        state = AppState(
            True,
            "night",
            "2026-07-24T06:15:00+08:00",
            "night",
            "success",
        )

        plan = plan_auto_run(AppConfig.defaults(), state, _at(8, 0))

        self.assertIs(plan.kind, AutoPlanKind.PAUSED)
        self.assertIsNone(plan.target_profile)
        self.assertIsNone(plan.target_apps_theme)
        self.assertIsNone(plan.target_system_theme)
        self.assertIsNone(plan.target_start_taskbar_accent)
        self.assertIsNone(plan.target_title_borders_accent)

    def test_apply_plan_contains_complete_explicit_appearance(self) -> None:
        plan = plan_auto_run(
            AppConfig.defaults(), _successful_state("night"), _at(8, 0)
        )

        self.assertIs(plan.kind, AutoPlanKind.APPLY)
        self.assertEqual(plan.target_profile, "day")
        self.assertEqual(plan.target_apps_theme, "light")
        self.assertEqual(plan.target_system_theme, "light")
        self.assertFalse(plan.target_start_taskbar_accent)
        self.assertFalse(plan.target_title_borders_accent)

    def test_successful_same_profile_is_no_change_and_does_not_learn(self) -> None:
        plan = plan_auto_run(AppConfig.defaults(), _successful_state("day"), _at(8, 0))

        self.assertIs(plan.kind, AutoPlanKind.NO_CHANGE)
        self.assertEqual(plan.target_profile, "day")
        self.assertEqual(plan.target_apps_theme, "light")
        self.assertEqual(plan.target_system_theme, "light")

    def test_manual_current_plan_applies_without_learning_or_pause_block(self) -> None:
        plan = plan_auto_run(
            AppConfig.defaults(),
            _successful_state("day"),
            _at(8, 0),
            intent=RunIntent.MANUAL_CURRENT,
        )

        self.assertIs(plan.kind, AutoPlanKind.APPLY)
        self.assertEqual(plan.target_profile, "day")
        self.assertEqual(plan.target_apps_theme, "light")

        paused = AppState(
            True,
            "day",
            "2026-07-24T06:15:00+08:00",
            "day",
            "success",
        )
        self.assertIs(
            plan_auto_run(
                AppConfig.defaults(),
                paused,
                _at(8, 0),
                intent=RunIntent.MANUAL_CURRENT,
            ).kind,
            AutoPlanKind.APPLY,
        )


class ExitAndLockContractTests(unittest.TestCase):
    def test_success_and_no_change_share_zero_only(self) -> None:
        self.assertEqual(EXIT_CODE_BY_RESULT[AutoResultKind.APPLIED], 0)
        self.assertEqual(EXIT_CODE_BY_RESULT[AutoResultKind.NO_CHANGE], 0)
        non_success = set(AutoResultKind) - {
            AutoResultKind.APPLIED,
            AutoResultKind.NO_CHANGE,
        }
        self.assertTrue(all(EXIT_CODE_BY_RESULT[result] != 0 for result in non_success))
        self.assertEqual(
            EXIT_CODE_BY_RESULT[AutoResultKind.PARTIAL_FAILURE],
            AutoExitCode.PARTIAL_FAILURE,
        )

    def test_mutex_name_is_stable_scoped_and_does_not_leak_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = mutex_name_for_data_root(Path(directory) / "one")
            repeated = mutex_name_for_data_root(Path(directory) / "one")
            second = mutex_name_for_data_root(Path(directory) / "two")

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith(r"Local\ThemeScheduler.Auto."))
        self.assertNotIn("one", first.casefold())


if __name__ == "__main__":
    unittest.main()
