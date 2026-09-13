from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path

from theme_scheduler.accent_profile import (
    AccentProfile,
    AccentProfileStore,
)
from theme_scheduler.cli.auto import _FixedClock
from theme_scheduler.cli.auto import main as auto_cli_main
from theme_scheduler.config import AppConfig, ConfigStore
from theme_scheduler.state import AppState, StateStore
from theme_scheduler.storage import UserDataLayout


class AutoCliTests(unittest.TestCase):
    def test_fixed_clock_returns_the_injected_instant(self) -> None:
        instant = datetime.fromisoformat("2026-09-13T12:00:00+08:00")
        self.assertIs(_FixedClock(instant).now(), instant)

    def _layout(self, root: Path, *, active: str = "night") -> UserDataLayout:
        layout = UserDataLayout(root / "data")
        layout.ensure_directories()
        ConfigStore(layout.config).initialize(AppConfig.defaults())
        StateStore(layout.state).initialize(
            AppState(
                False,
                active,
                "2026-07-23T23:45:00+00:00",
                active,
                "success",
            )
        )
        for name, color in (
            ("day", 0xC4744DA9),
            ("night", 0xC4FFB900),
        ):
            AccentProfileStore(layout.profile_path(name), name).create(
                AccentProfile(
                    name,
                    "2026-07-24T00:00:00+00:00",
                    False,
                    color,
                    "26200",
                )
            )
        return layout

    def test_plan_is_read_only_and_reports_cross_profile_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            before = {
                path.relative_to(layout.root): path.read_bytes()
                for path in layout.root.rglob("*")
                if path.is_file()
            }
            output = StringIO()

            with redirect_stdout(output):
                exit_code = auto_cli_main(
                    [
                        "plan",
                        "--data-root",
                        str(layout.root),
                        "--at",
                        "2026-07-24T08:00:00+00:00",
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["plan"]["kind"], "apply")
            self.assertEqual(payload["plan"]["targetProfile"], "day")
            self.assertIsNone(payload["plan"]["learnProfile"])
            self.assertEqual(payload["plan"]["targetSystemTheme"], "light")
            self.assertFalse(payload["plan"]["targetStartTaskbarAccent"])
            self.assertFalse(payload["plan"]["targetTitleBordersAccent"])
            self.assertFalse(payload["windowsChanged"])
            self.assertFalse(payload["dataChanged"])
            after = {
                path.relative_to(layout.root): path.read_bytes()
                for path in layout.root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)

    def test_plan_rejects_naive_time_and_missing_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory))
            with redirect_stderr(StringIO()):
                naive = auto_cli_main(
                    [
                        "plan",
                        "--data-root",
                        str(layout.root),
                        "--at",
                        "2026-07-24T08:00:00",
                    ]
                )
            self.assertEqual(naive, 2)

            layout.profile_path("day").unlink()
            with redirect_stderr(StringIO()):
                missing = auto_cli_main(
                    [
                        "plan",
                        "--data-root",
                        str(layout.root),
                        "--at",
                        "2026-07-24T08:00:00+00:00",
                    ]
                )
            self.assertEqual(missing, 2)

    def test_plan_for_current_profile_and_paused_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = self._layout(Path(directory), active="day")
            output = StringIO()

            with redirect_stdout(output):
                exit_code = auto_cli_main(
                    [
                        "plan",
                        "--data-root",
                        str(layout.root),
                        "--at",
                        "2026-07-24T08:00:00+00:00",
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["plan"]["kind"], "no-change")
            self.assertEqual(payload["targetAccentProfile"]["profile"], "day")

            current = StateStore(layout.state).load()
            StateStore(layout.state).save(
                AppState(
                    True,
                    current.active_profile,
                    current.last_run_at,
                    current.last_applied_profile,
                    current.last_result,
                )
            )
            paused_output = StringIO()
            with redirect_stdout(paused_output):
                paused_exit = auto_cli_main(
                    [
                        "plan",
                        "--data-root",
                        str(layout.root),
                        "--at",
                        "2026-07-24T08:00:00+00:00",
                    ]
                )
            paused = json.loads(paused_output.getvalue())
            self.assertEqual(paused_exit, 0)
            self.assertEqual(paused["plan"]["kind"], "paused")
            self.assertIsNone(paused["targetAccentProfile"])

    def test_live_run_without_confirmation_creates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory) / "absent"

            with redirect_stderr(StringIO()):
                exit_code = auto_cli_main(["run", "--data-root", str(data_root)])

            self.assertEqual(exit_code, 3)
            self.assertFalse(data_root.exists())

    def test_live_time_override_requires_second_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory) / "absent"

            with redirect_stderr(StringIO()):
                exit_code = auto_cli_main(
                    [
                        "run",
                        "--data-root",
                        str(data_root),
                        "--confirm-live-auto-write",
                        "--at",
                        "2026-07-24T08:00:00+08:00",
                    ]
                )

            self.assertEqual(exit_code, 3)
            self.assertFalse(data_root.exists())


if __name__ == "__main__":
    unittest.main()
