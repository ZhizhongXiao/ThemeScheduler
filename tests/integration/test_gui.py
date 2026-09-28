from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Never
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"

from theme_scheduler.accent_profile import (
    AccentProfile,
    AccentProfileStore,
)
from theme_scheduler.accent_theme import ThemeVisualState
from theme_scheduler.appearance import CurrentWindowsAppearance
from theme_scheduler.config import AppConfig, ConfigStore
from theme_scheduler.gui import (
    frontend_entry,
    launch_gui,
    validate_live_executable,
)
from theme_scheduler.initial_setup import (
    create_initial_setup_marker,
)
from theme_scheduler.scheduler import (
    TaskDefinitionBackup,
    TaskSpec,
    build_task_spec,
)
from theme_scheduler.state import AppState, StateStore
from theme_scheduler.storage import UserDataLayout
from theme_scheduler.webview_runtime import (
    WEBVIEW2_CLIENT_ID,
    detect_webview2_runtime,
)
from theme_scheduler.workbench import (
    GuiApi,
    ShellActions,
    create_live_gui_api,
)
from theme_scheduler.workbench.contracts import HealthReportData

USER_ID = r"DESKTOP-TEST\Example"


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


class FakeLock:
    def acquire(self) -> bool:
        return True

    def release(self) -> None:
        return None


class MemoryScheduler:
    def __init__(self, task: TaskSpec | None = None) -> None:
        self.task = task
        self.register_count = 0

    def current_user_id(self) -> str:
        return USER_ID

    def read(self, task_path: str) -> TaskSpec | None:
        if self.task is None or self.task.task_path != task_path:
            return None
        return self.task

    def register(self, task: TaskSpec) -> None:
        self.register_count += 1
        self.task = task

    def capture(self, task_path: str) -> TaskDefinitionBackup | None:
        task = self.read(task_path)
        if task is None:
            return None
        return TaskDefinitionBackup(
            task_path,
            json.dumps(task.as_dict()),
            task.enabled,
        )

    def restore(self, backup: TaskDefinitionBackup) -> None:
        self.task = TaskSpec.from_dict(json.loads(backup.definition_xml))

    def delete(self, task_path: str) -> None:
        self.task = None


class FailingReadScheduler(MemoryScheduler):
    def current_user_id(self) -> str:
        raise OSError("scheduler unavailable")


class FakeShell:
    def __init__(self) -> None:
        self.opened: list[str] = []

    def open(self, target: str) -> None:
        self.opened.append(target)


@dataclass
class FakeMaintenanceOutcome:
    def as_dict(self):
        return {
            "action": "restore-install-appearance",
            "result": "restored",
            "pausedAfter": True,
            "windowsVerified": True,
            "systemModePreserved": True,
            "dataChanged": True,
            "windowsChanged": True,
            "taskSchedulerChanged": False,
            "message": "restored",
        }


class FakeMaintenanceService:
    def __init__(self) -> None:
        self.calls = 0

    def restore_install_appearance(self):
        self.calls += 1
        return FakeMaintenanceOutcome()


class FakeHealthReport:
    def as_dict(self) -> HealthReportData:
        return {
            "kind": "themescheduler.health-report",
            "schemaVersion": 1,
            "capturedAt": "2026-07-25T12:00:00+08:00",
            "status": "repairable",
            "summary": {
                "healthy": 1,
                "warning": 0,
                "repairable": 1,
                "action-required": 0,
            },
            "checks": [
                {
                    "id": "task.definition",
                    "category": "task",
                    "status": "repairable",
                    "message": "Task drifted.",
                    "repairAction": "task.repair",
                }
            ],
        }


class FakeHealthService:
    def inspect(self):
        return FakeHealthReport()


class FakeIdentityRepairOutcome:
    def as_dict(self):
        return {
            "action": "notification-identity-repair",
            "result": "changed",
            "message": "Notification identity is consistent.",
            "dataChanged": True,
            "windowsChanged": True,
            "taskSchedulerChanged": False,
        }


class FakeIdentityRepairService:
    def __init__(self) -> None:
        self.calls = 0

    def repair(self):
        self.calls += 1
        return FakeIdentityRepairOutcome()


class FakeManualAppearanceOutcome:
    def __init__(self, result: str = "applied") -> None:
        self.result = result

    def as_dict(self):
        return {
            "result": self.result,
            "exitCode": 0 if self.result == "applied" else 30,
            "targetProfile": "day",
            "transactionDirectory": None,
            "learnedProfile": None,
            "windowsChanged": self.result == "applied",
            "stateChanged": self.result == "applied",
            "recovered": False,
            "message": self.result,
        }


class FakeManualAppearanceService:
    def __init__(self) -> None:
        self.calls = 0
        self.result = "applied"

    def apply_current(self):
        self.calls += 1
        return FakeManualAppearanceOutcome(self.result)


def payload(**changes):
    value = {
        "dayStart": "06:15",
        "nightStart": "23:45",
        "dayAppsTheme": "light",
        "nightAppsTheme": "dark",
        "daySystemTheme": "light",
        "nightSystemTheme": "dark",
        "dayStartTaskbarAccent": False,
        "nightStartTaskbarAccent": True,
        "dayTitleBordersAccent": False,
        "nightTitleBordersAccent": True,
        "notifyErrors": True,
        "notifyStatusChanges": True,
    }
    value.update(changes)
    return value


def workspace_payload(**changes):
    value = {
        **payload(),
        "dayColor": {
            "hex": "#744DA9",
            "red": 116,
            "green": 77,
            "blue": 169,
        },
        "nightColor": {
            "hex": "#FFB900",
            "red": 255,
            "green": 185,
            "blue": 0,
        },
    }
    value.update(changes)
    return value


class GuiApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.layout = UserDataLayout(Path(self.temporary.name))
        self.layout.ensure_directories()
        ConfigStore(self.layout.config).initialize(AppConfig.defaults())
        StateStore(self.layout.state).initialize(AppState.initial())
        self.executable = Path(self.temporary.name) / "ThemeScheduler.exe"
        self.executable.touch()
        desired = build_task_spec(
            AppConfig.defaults(),
            executable=str(self.executable.resolve()),
            user_id=USER_ID,
        )
        self.scheduler = MemoryScheduler(desired)
        self.shell = FakeShell()
        self.maintenance = FakeMaintenanceService()
        self.identity_repair = FakeIdentityRepairService()
        self.manual_appearance = FakeManualAppearanceService()
        self.api = GuiApi(
            self.layout,
            executable=self.executable,
            scheduler_backend=self.scheduler,
            lock_factory=FakeLock,
            maintenance_service_factory=lambda: self.maintenance,
            health_service_factory=FakeHealthService,
            identity_repair_service_factory=(lambda: self.identity_repair),
            manual_appearance_service_factory=(lambda: self.manual_appearance),
            current_appearance_reader=lambda: CurrentWindowsAppearance(
                visual=ThemeVisualState(
                    "0",
                    0xD0744DA9,
                    "Light",
                    "Dark",
                ),
                accent_source="winrt-ui-settings",
            ),
            shell_actions=self.shell,
            clock=FixedClock(),
            allow_live_writes=True,
        )

    def create_profiles(self) -> None:
        for name, color in (
            ("day", 0xD0744DA9),
            ("night", 0xC4FFB900),
        ):
            AccentProfileStore(
                self.layout.profile_path(name),
                name,
            ).create(
                AccentProfile(
                    name,
                    "2026-07-23T10:00:00+08:00",
                    False,
                    color,
                    "26200",
                )
            )

    def test_overview_combines_config_state_profiles_and_task(self) -> None:
        result = self.api.get_overview()

        self.assertEqual(result["result"], "success")
        self.assertEqual(result["targetProfile"], "day")
        self.assertTrue(result["task"]["available"])
        self.assertTrue(result["task"]["valid"])
        self.assertTrue(result["currentAppearanceReadEnabled"])
        self.assertIsNotNone(result["task"]["desired"])
        self.assertFalse(result["profiles"]["day"]["valid"])
        self.assertFalse(result["profiles"]["night"]["valid"])
        self.assertTrue(result["recentLog"]["available"])
        self.assertEqual(result["recentLog"]["events"], [])
        self.assertFalse(result["initialSetupPending"])
        self.assertEqual(
            result["installBackup"]["status"],
            "absent",
        )
        self.assertFalse(result["windowsChanged"])

    def test_current_windows_appearance_read_is_draft_only(self) -> None:
        before_config = ConfigStore(self.layout.config).load()

        result = self.api.read_current_windows_appearance()

        self.assertEqual(result["result"], "success")
        self.assertEqual(result.get("appMode"), "light")
        self.assertEqual(result.get("systemMode"), "dark")
        self.assertEqual(result.get("color", {}).get("hex"), "#744DA9")
        self.assertEqual(result.get("colorizationColor"), "0XD0744DA9")
        self.assertEqual(result.get("accentSource"), "winrt-ui-settings")
        self.assertFalse(result.get("sourcesDiverged"))
        self.assertEqual(result.get("divergences"), [])
        self.assertIsNone(result.get("themeAppearance"))
        self.assertFalse(result["dataChanged"])
        self.assertFalse(result["windowsChanged"])
        self.assertFalse(result["taskSchedulerChanged"])
        self.assertEqual(ConfigStore(self.layout.config).load(), before_config)
        self.assertEqual(self.scheduler.register_count, 0)

    def test_overview_exposes_valid_profile_raw_data_for_debug_page(
        self,
    ) -> None:
        self.create_profiles()

        result = self.api.get_overview()

        self.assertTrue(result["profiles"]["day"]["valid"])
        profile = result["profiles"]["day"]
        raw_profile = profile.get("raw")
        assert isinstance(raw_profile, dict)
        accent = raw_profile.get("accent")
        assert isinstance(accent, dict)
        self.assertEqual(accent.get("colorizationColor"), "0XD0744DA9")

    def test_overview_keeps_core_status_when_scheduler_is_unavailable(self) -> None:
        api = GuiApi(
            self.layout,
            executable=self.executable,
            scheduler_backend=FailingReadScheduler(),
            lock_factory=FakeLock,
            shell_actions=self.shell,
            clock=FixedClock(),
        )

        result = api.get_overview()

        self.assertEqual(result["result"], "partial")
        self.assertEqual(result["config"]["dayStart"], "06:15")
        self.assertFalse(result["task"]["available"])
        self.assertIn("scheduler unavailable", result["message"])

    def test_overview_reports_incomplete_install_backup_without_hiding_core(
        self,
    ) -> None:
        self.layout.install_backup_manifest.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self.layout.install_backup_manifest.write_text(
            "{}",
            encoding="utf-8",
        )

        result = self.api.get_overview()

        self.assertEqual(result["result"], "partial")
        self.assertEqual(
            result["installBackup"]["status"],
            "invalid",
        )
        self.assertEqual(result["config"]["dayStart"], "06:15")
        self.assertIn("incomplete", result["message"])

    def test_workspace_validation_requires_matching_hex_and_rgb(self) -> None:
        valid = self.api.validate_workspace(workspace_payload())
        invalid = self.api.validate_workspace(
            workspace_payload(
                dayColor={
                    "hex": "#744DA9",
                    "red": 117,
                    "green": 77,
                    "blue": 169,
                }
            )
        )

        self.assertTrue(valid["valid"])
        colors = valid.get("colors")
        assert isinstance(colors, dict)
        day_color = colors.get("day")
        assert isinstance(day_color, dict)
        self.assertEqual(day_color.get("hex"), "#744DA9")
        self.assertFalse(invalid["valid"])
        self.assertIn("differ", invalid["message"])
        self.assertEqual(self.scheduler.register_count, 0)

    def test_workspace_validation_rejects_each_structural_boundary(self) -> None:
        not_object = self.api.validate_workspace(None)  # type: ignore[arg-type]
        missing = workspace_payload()
        missing.pop("nightColor")
        missing_result = self.api.validate_workspace(missing)
        color_not_object = self.api.validate_workspace(
            workspace_payload(dayColor="purple")
        )
        color_wrong_fields = self.api.validate_workspace(
            workspace_payload(dayColor={"hex": "#744DA9"})
        )

        for result in (
            not_object,
            missing_result,
            color_not_object,
            color_wrong_fields,
        ):
            self.assertFalse(result["valid"])

    def test_save_and_repair_require_confirmation(self) -> None:
        workspace_blocked = self.api.save_workspace(workspace_payload())
        apply_blocked = self.api.save_workspace_and_apply(workspace_payload())
        repair_blocked = self.api.repair_task()
        identity_blocked = self.api.repair_notification_identity()
        reset_blocked = self.api.reset_preferences()
        restore_blocked = self.api.restore_install_appearance()
        uninstall_blocked = self.api.launch_uninstaller()

        self.assertEqual(workspace_blocked["result"], "blocked")
        self.assertEqual(apply_blocked["result"], "blocked")
        self.assertEqual(repair_blocked["result"], "blocked")
        self.assertEqual(identity_blocked["result"], "blocked")
        self.assertEqual(reset_blocked["result"], "blocked")
        self.assertEqual(restore_blocked["result"], "blocked")
        self.assertEqual(uninstall_blocked["result"], "blocked")
        self.assertEqual(ConfigStore(self.layout.config).load(), AppConfig.defaults())
        self.assertEqual(self.scheduler.register_count, 0)

    def test_development_preview_blocks_live_task_and_windows_changes(self) -> None:
        preview = GuiApi(
            self.layout,
            executable=self.executable,
            scheduler_backend=self.scheduler,
            lock_factory=FakeLock,
            shell_actions=self.shell,
            clock=FixedClock(),
        )

        workspace = preview.save_workspace(workspace_payload(), True)
        apply = preview.save_workspace_and_apply(workspace_payload(), True)
        appearance_read = preview.read_current_windows_appearance()
        repair = preview.repair_task(True)
        identity = preview.repair_notification_identity(True)
        reset = preview.reset_preferences(True)
        restore = preview.restore_install_appearance(True)
        uninstall = preview.launch_uninstaller(True)

        self.assertEqual(workspace["result"], "blocked")
        self.assertEqual(apply["result"], "blocked")
        self.assertEqual(appearance_read["result"], "blocked")
        self.assertEqual(repair["result"], "blocked")
        self.assertEqual(identity["result"], "blocked")
        self.assertEqual(reset["result"], "blocked")
        self.assertEqual(restore["result"], "blocked")
        self.assertEqual(uninstall["result"], "blocked")
        self.assertEqual(self.scheduler.register_count, 0)

    def test_system_read_preview_enables_import_but_keeps_writes_blocked(
        self,
    ) -> None:
        preview = GuiApi(
            self.layout,
            executable=self.executable,
            scheduler_backend=self.scheduler,
            lock_factory=FakeLock,
            current_appearance_reader=lambda: CurrentWindowsAppearance(
                visual=ThemeVisualState(
                    "0",
                    0xC4FFB900,
                    "Dark",
                    "Dark",
                ),
                accent_source="winrt-ui-settings",
            ),
            shell_actions=self.shell,
            clock=FixedClock(),
            allow_live_writes=False,
        )

        imported = preview.read_current_windows_appearance()
        saved = preview.save_workspace(workspace_payload(), True)

        self.assertEqual(imported["result"], "success")
        self.assertEqual(imported.get("appMode"), "dark")
        self.assertEqual(imported.get("color", {}).get("hex"), "#FFB900")
        self.assertFalse(imported["dataChanged"])
        self.assertFalse(imported["windowsChanged"])
        self.assertEqual(saved["result"], "blocked")
        self.assertEqual(self.scheduler.register_count, 0)

    def test_confirmed_workspace_save_updates_profiles_without_windows(
        self,
    ) -> None:
        self.create_profiles()
        target = workspace_payload(
            dayStart="07:00",
            dayColor={
                "hex": "#102030",
                "red": 16,
                "green": 32,
                "blue": 48,
            },
        )

        result = self.api.save_workspace(target, True)

        self.assertEqual(result["result"], "changed")
        self.assertEqual(result["action"], "save-workspace")
        self.assertTrue(result["profilesVerified"])
        self.assertFalse(result["windowsChanged"])
        self.assertEqual(
            AccentProfileStore(self.layout.profile_path("day"), "day")
            .load()
            .colorization_color,
            0xD0102030,
        )

    def test_save_and_apply_uses_committed_workspace_and_reports_target(self) -> None:
        self.create_profiles()
        result = self.api.save_workspace_and_apply(
            workspace_payload(dayStart="07:00"),
            True,
        )

        self.assertEqual(result["action"], "save-workspace-and-apply")
        self.assertEqual(result["result"], "changed")
        self.assertTrue(result["planSaved"])
        self.assertEqual(result["targetProfile"], "day")
        self.assertTrue(result["windowsChanged"])
        self.assertEqual(self.manual_appearance.calls, 1)
        self.assertEqual(ConfigStore(self.layout.config).load().day_start, "07:00")

    def test_save_and_apply_failure_keeps_verified_plan(self) -> None:
        self.create_profiles()
        self.manual_appearance.result = "apply-failed-rolled-back"

        result = self.api.save_workspace_and_apply(
            workspace_payload(dayStart="07:00"),
            True,
        )

        self.assertEqual(result["result"], "partial-failure")
        self.assertTrue(result["planSaved"])
        self.assertFalse(result["windowsChanged"])
        self.assertEqual(ConfigStore(self.layout.config).load().day_start, "07:00")

    def test_save_failure_does_not_start_manual_application(self) -> None:
        result = self.api.save_workspace_and_apply(
            workspace_payload(dayStart="invalid"),
            True,
        )

        self.assertNotEqual(result["result"], "changed")
        self.assertEqual(self.manual_appearance.calls, 0)

    def test_pending_transaction_failure_keeps_gui_flags_boolean(self) -> None:
        self.create_profiles()
        with patch(
            "theme_scheduler.configuration_service.ensure_no_pending_auto_transaction",
            side_effect=RuntimeError("pending transaction"),
        ):
            result = self.api.save_workspace_and_apply(
                workspace_payload(dayStart="07:00"),
                True,
            )

        self.assertEqual(result["action"], "save-workspace")
        self.assertEqual(result["result"], "data-untrusted")
        self.assertIn("pending transaction", result["message"])
        self.assertIs(result["dataChanged"], False)
        self.assertIs(result["windowsChanged"], False)
        self.assertIs(result["taskSchedulerChanged"], False)
        self.assertEqual(self.manual_appearance.calls, 0)

    def test_saved_plan_reports_unavailable_manual_application(self) -> None:
        self.create_profiles()
        self.api._manual_appearance_service_factory = None
        result = self.api.save_workspace_and_apply(workspace_payload(), True)
        self.assertEqual(result["result"], "partial-failure")
        self.assertTrue(result["planSaved"])
        self.assertNotIn("application", result)

    def test_manual_application_exception_is_a_partial_failure(self) -> None:
        self.create_profiles()

        def fail() -> Never:
            raise OSError("manual apply failed")

        self.api._manual_appearance_service_factory = fail
        result = self.api.save_workspace_and_apply(workspace_payload(), True)
        self.assertEqual(result["result"], "partial-failure")
        self.assertTrue(result["planSaved"])
        self.assertEqual(result["application"]["result"], "fatal-failure")

    def test_first_workspace_save_enables_pending_initial_setup_without_sync(
        self,
    ) -> None:
        self.create_profiles()
        StateStore(self.layout.state).save(AppState.pending_initial_setup())
        create_initial_setup_marker(self.layout.initial_setup_marker)

        before = self.api.get_overview()
        result = self.api.save_workspace(workspace_payload(), True)
        after = self.api.get_overview()

        self.assertTrue(before["initialSetupPending"])
        self.assertEqual(result["result"], "changed")
        self.assertTrue(result["initialSetupActivated"])
        self.assertFalse(StateStore(self.layout.state).load().paused)
        self.assertFalse(self.layout.initial_setup_marker.exists())
        self.assertFalse(after["initialSetupPending"])
        self.assertFalse(result["windowsChanged"])

    def test_first_run_marker_blocks_manual_resume_before_save(self) -> None:
        StateStore(self.layout.state).save(AppState.pending_initial_setup())
        create_initial_setup_marker(self.layout.initial_setup_marker)

        result = self.api.set_paused(False, True)

        self.assertEqual(result["result"], "blocked")
        self.assertTrue(StateStore(self.layout.state).load().paused)

    def test_pause_has_an_explicit_confirmation_gate(self) -> None:
        self.assertEqual(self.api.set_paused(True)["result"], "blocked")
        paused = self.api.set_paused(True, True)
        self.assertTrue(StateStore(self.layout.state).load().paused)
        self.assertEqual(paused["result"], "changed")

    def test_task_check_and_repair_use_current_frozen_definition(
        self,
    ) -> None:
        checked = self.api.check_task()
        self.scheduler.task = None
        drifted = self.api.check_task()
        repaired = self.api.repair_task(True)

        self.assertEqual(checked["result"], "success")
        self.assertEqual(drifted["result"], "drift")
        self.assertEqual(repaired["result"], "changed")
        self.assertTrue(repaired["taskVerified"])

    def test_health_check_and_identity_repair_are_separate_actions(
        self,
    ) -> None:
        checked = self.api.check_health()
        repaired = self.api.repair_notification_identity(True)

        self.assertEqual(checked["result"], "repairable")
        self.assertEqual(checked.get("status"), "repairable")
        self.assertFalse(checked["windowsChanged"])
        self.assertEqual(repaired["result"], "changed")
        self.assertEqual(self.identity_repair.calls, 1)

    def test_reset_preferences_preserves_complete_appearance_and_repairs_task(
        self,
    ) -> None:
        custom = AppConfig(
            "08:00",
            "20:00",
            "dark",
            "light",
            False,
            False,
            day_system_theme="dark",
            night_system_theme="light",
            day_start_taskbar_accent=True,
            night_start_taskbar_accent=False,
            day_title_borders_accent=True,
            night_title_borders_accent=False,
        )
        self.create_profiles()
        profiles_before = {
            name: AccentProfileStore(self.layout.profile_path(name), name).load()
            for name in ("day", "night")
        }
        ConfigStore(self.layout.config).save(custom)
        self.scheduler.task = build_task_spec(
            custom,
            executable=str(self.executable.resolve()),
            user_id=USER_ID,
        )

        result = self.api.reset_preferences(True)

        actual = ConfigStore(self.layout.config).load()
        self.assertEqual(result["result"], "changed")
        self.assertEqual(actual.day_start, "06:15")
        self.assertEqual(actual.night_start, "23:45")
        self.assertTrue(actual.notify_errors)
        self.assertTrue(actual.notify_status_changes)
        self.assertEqual(actual.day_apps_theme, "dark")
        self.assertEqual(actual.night_apps_theme, "light")
        self.assertEqual(actual.day_system_theme, "dark")
        self.assertEqual(actual.night_system_theme, "light")
        self.assertTrue(actual.day_start_taskbar_accent)
        self.assertFalse(actual.night_start_taskbar_accent)
        self.assertTrue(actual.day_title_borders_accent)
        self.assertFalse(actual.night_title_borders_accent)
        self.assertEqual(
            {
                name: AccentProfileStore(self.layout.profile_path(name), name).load()
                for name in ("day", "night")
            },
            profiles_before,
        )
        self.assertEqual(
            {item.local_time for item in self.scheduler.task.triggers},  # type: ignore[union-attr]
            {"06:10", "06:15", "23:40", "23:45"},
        )

    def test_restore_and_uninstaller_are_separately_confirmed(self) -> None:
        restored = self.api.restore_install_appearance(True)
        launched = self.api.launch_uninstaller(True)

        self.assertEqual(restored["result"], "restored")
        self.assertTrue(restored["systemModePreserved"])
        self.assertEqual(self.maintenance.calls, 1)
        self.assertEqual(launched["result"], "success")
        self.assertEqual(self.shell.opened, ["uninstaller"])

    def test_shell_navigation_is_allowlisted(self) -> None:
        opened = self.api.open_target("colors")
        rejected = self.api.open_target("arbitrary-path")

        self.assertEqual(opened["result"], "success")
        self.assertEqual(self.shell.opened, ["colors"])
        self.assertEqual(rejected["result"], "failed")


class GuiAssetsAndRuntimeTests(unittest.TestCase):
    def test_gui_module_delays_workbench_import_until_launch(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; import theme_scheduler.gui; "
                    "assert 'theme_scheduler.workbench' not in sys.modules"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    @patch("theme_scheduler.workbench.shell.os.startfile", create=True)
    @patch("theme_scheduler.workbench.shell.os.name", "nt")
    def test_uninstaller_launch_is_derived_from_frozen_layout(self, startfile) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout = UserDataLayout(root / "Data" / "ThemeScheduler")
            executable = (
                root / "Programs" / "ThemeScheduler" / "app" / "ThemeScheduler.exe"
            )
            uninstaller = (
                root / "Programs" / "ThemeScheduler" / "maintenance" / "Uninstall.exe"
            )
            executable.parent.mkdir(parents=True)
            executable.touch()
            uninstaller.parent.mkdir(parents=True)
            uninstaller.touch()

            ShellActions(layout, executable).open("uninstaller")

            startfile.assert_called_once_with(str(uninstaller))

    def test_frontend_is_local_and_references_only_packaged_assets(self) -> None:
        entry = frontend_entry()
        html = entry.read_text(encoding="utf-8")
        script = (entry.parent.parent / "js" / "workbench.js").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertIn('href="../css/workbench.css"', html)
        self.assertIn('src="../js/workbench.js"', html)
        self.assertIn("window.pywebview.api.save_workspace", script)
        self.assertNotIn("save_and_apply_workspace", script)
        self.assertIn(
            "window.pywebview.api.read_current_windows_appearance",
            script,
        )
        self.assertIn("window.pywebview.api.validate_workspace", script)
        self.assertNotIn("sync_now", script)
        self.assertIn(
            "window.pywebview.api.restore_install_appearance",
            script,
        )
        self.assertIn(
            "window.pywebview.api.reset_preferences",
            script,
        )
        self.assertIn(
            "window.pywebview.api.launch_uninstaller",
            script,
        )
        self.assertIn("window.themeSchedulerOpenHealth", script)
        self.assertIn('id="task-report"', html)
        self.assertIn('id="state-profile-report"', html)
        self.assertIn('id="recent-log-report"', html)
        self.assertIn('id="copy-diagnostic-button"', html)
        self.assertIn("需要修复", script)
        self.assertIn("scrollIntoView", script)
        self.assertIn('id="install-backup-state"', html)
        self.assertIn('id="initial-setup-banner"', html)
        self.assertIn('id="run-actions"', html)
        self.assertIn("initialSetupPending", script)
        self.assertIn('class="command-group run-actions" id="run-actions" hidden', html)
        self.assertNotIn('$("#run-actions").hidden =', script)
        self.assertIn("保存并启用", script)

    def test_source_launch_requires_explicit_data_root(self) -> None:
        with self.assertRaises(ValueError):
            launch_gui(data_root=None, installed=False)

    def test_development_live_writes_require_explicit_executable(self) -> None:
        with self.assertRaises(ValueError):
            launch_gui(
                data_root=PROJECT_ROOT / "artifacts" / "acceptance" / "stage7",
                installed=False,
                allow_live_writes=True,
            )

    def test_preview_factory_does_not_require_installed_program_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = UserDataLayout(Path(directory))
            api = create_live_gui_api(
                layout,
                Path(sys.executable),
                allow_live_writes=False,
            )

            self.assertEqual(
                api.launch_uninstaller(True)["result"],
                "blocked",
            )
            self.assertEqual(
                api.repair_notification_identity(True)["result"],
                "blocked",
            )

    def test_preview_factory_can_enable_only_system_appearance_reads(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            layout = UserDataLayout(Path(directory))
            api = create_live_gui_api(
                layout,
                Path(sys.executable),
                allow_live_writes=False,
                allow_system_reads=True,
            )

            self.assertIsNotNone(api._current_appearance_reader)
            self.assertEqual(
                api.save_workspace(workspace_payload(), True)["result"],
                "blocked",
            )

    def test_live_factory_composes_manual_current_appearance_service(self) -> None:
        class KnownFolders:
            @staticmethod
            def programs() -> Path:
                return Path("Programs")

            @staticmethod
            def desktop() -> Path:
                return Path("Desktop")

        class ShortcutDefinition:
            managed_paths: tuple[Path, ...] = ()

        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "theme_scheduler.workbench.factory.WindowsTaskSchedulerBackend",
                return_value=MemoryScheduler(),
            ),
            patch(
                "theme_scheduler.workbench.factory.WindowsKnownFolderReader",
                return_value=KnownFolders(),
            ),
            patch(
                "theme_scheduler.workbench.factory.ShortcutPlan.create",
                return_value=ShortcutDefinition(),
            ),
            patch("theme_scheduler.workbench.factory.WindowsAutoBackend"),
        ):
            root = Path(directory)
            executable = (
                root / "Programs" / "ThemeScheduler" / "app" / "ThemeScheduler.exe"
            )
            executable.parent.mkdir(parents=True)
            executable.touch()
            api = create_live_gui_api(
                UserDataLayout(root / "LocalAppData" / "ThemeScheduler"),
                executable,
                allow_live_writes=True,
            )
            factory = api._manual_appearance_service_factory
            assert factory is not None
            service = factory()

        self.assertEqual(type(service).__name__, "ManualAppearanceService")

    def test_live_executable_rejects_interpreter_and_missing_target(self) -> None:
        with self.assertRaisesRegex(ValueError, "interpreter"):
            validate_live_executable(Path(sys.executable))
        with self.assertRaises(FileNotFoundError):
            validate_live_executable(PROJECT_ROOT / "missing.exe")

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "ThemeScheduler.exe"
            target.touch()
            self.assertEqual(validate_live_executable(target), target.resolve())

    def test_auto_entry_does_not_import_gui_or_webview(self) -> None:
        source = (SOURCE_ROOT / "theme_scheduler" / "cli" / "auto.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("theme_scheduler.gui", source)
        self.assertNotIn("import webview", source)

        formal_source = (SOURCE_ROOT / "theme_scheduler" / "cli" / "app.py").read_text(
            encoding="utf-8"
        )
        auto_branch = formal_source.split('if command == "auto":', 1)[1].split(
            'if command in {"gui", "maintenance"}:', 1
        )[0]
        self.assertNotIn("theme_scheduler.gui", auto_branch)
        self.assertNotIn("import webview", formal_source)

    def test_maintenance_fragment_is_packaged_local_content(self) -> None:
        html = frontend_entry().read_text(encoding="utf-8")
        self.assertIn('id="maintenance"', html)

    def test_runtime_detection_accepts_nonzero_version_and_rejects_missing(
        self,
    ) -> None:
        class Present:
            def versions(self):
                return [("current-user", "135.0.3179.98")]

        class Missing:
            def versions(self):
                return []

        present = detect_webview2_runtime(Present())
        missing = detect_webview2_runtime(Missing())

        self.assertTrue(present.available)
        self.assertEqual(present.version, "135.0.3179.98")
        self.assertFalse(missing.available)
        self.assertEqual(
            WEBVIEW2_CLIENT_ID,
            "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        )
