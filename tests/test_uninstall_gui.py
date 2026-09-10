from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from theme_scheduler.cli.uninstall import main as uninstall_main
from theme_scheduler.lifecycle import InstallLayout
from theme_scheduler.uninstall_contracts import (
    AppearanceChoice,
    UninstallOptions,
)
from theme_scheduler.uninstall_gui import (
    _bind_execute_exit_guard,
    _ExecuteExitGuard,
    _run_webview_event_loop,
)
from theme_scheduler.uninstall_gui_api import (
    SelectionUninstallRuntime,
    UninstallGuiApi,
)
from theme_scheduler.uninstall_preview import (
    PreviewUninstallRuntime,
)


class FakeRuntime:
    preview_mode = True
    mode = "selection"

    def __init__(self, layout: InstallLayout) -> None:
        self.layout = layout
        self.calls: list[UninstallOptions] = []

    def get_status(self):
        options = UninstallOptions(
            AppearanceChoice.RESTORE,
            False,
            False,
        )
        return {
            "mode": self.mode,
            "programRoot": str(self.layout.program_root),
            "dataRoot": str(self.layout.data_root),
            "options": options.as_dict(),
            "plan": {},
            "autoStart": False,
        }

    def uninstall(self, options, *, progress):
        self.calls.append(options)
        progress("task-removed")
        progress("completed")
        return {
            "result": "completed",
            "verified": True,
            "completedSteps": ["task-removed"],
            "appearanceRestored": (options.appearance is AppearanceChoice.RESTORE),
            "taskRemoved": True,
            "shortcutsRemoved": True,
            "registrationRemoved": True,
            "programRootRemoved": True,
            "dataCleaned": True,
            "selfCleanupScheduled": True,
            "residualPaths": [],
            "message": "done",
        }


class FakeStager:
    def __init__(self) -> None:
        self.options: list[UninstallOptions] = []

    def stage(self, options: UninstallOptions):
        self.options.append(options)
        return {
            "launched": True,
            "processId": 123,
            "windowsChanged": False,
            "installedFilesChanged": False,
        }


class FakeEvent:
    def __init__(self) -> None:
        self.callbacks = []

    def __iadd__(self, callback):
        self.callbacks.append(callback)
        return self


class FakeExitGuardApi:
    def __init__(self, mode: str, workspace: Path) -> None:
        self.runtime = SimpleNamespace(mode=mode, workspace=workspace)
        self.close_guard = None

    def bind_close_guard(self, callback) -> None:
        self.close_guard = callback


class UninstallGuiApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name).resolve()
        self.layout = InstallLayout(
            root / "Programs" / "ThemeScheduler",
            root / "Data" / "ThemeScheduler",
        )

    def test_all_eight_option_combinations_validate_without_writes(self) -> None:
        runtime = FakeRuntime(self.layout)
        api = UninstallGuiApi(runtime)
        before = set(Path(self.temporary.name).rglob("*"))

        for restore in (False, True):
            for keep_config in (False, True):
                for keep_logs in (False, True):
                    with self.subTest(
                        restore=restore,
                        keep_config=keep_config,
                        keep_logs=keep_logs,
                    ):
                        result = api.validate_options(
                            {
                                "appearance": (
                                    AppearanceChoice.RESTORE.value
                                    if restore
                                    else AppearanceChoice.KEEP.value
                                ),
                                "keepConfigAndProfiles": keep_config,
                                "keepLogs": keep_logs,
                            }
                        )
                        self.assertTrue(result["ok"])
                        plan = result["plan"]
                        self.assertEqual(
                            plan["removeEntireDataRoot"],
                            not keep_config and not keep_logs,
                        )

        self.assertEqual(
            set(Path(self.temporary.name).rglob("*")),
            before,
        )

    def test_unknown_option_is_rejected(self) -> None:
        api = UninstallGuiApi(FakeRuntime(self.layout))
        result = api.validate_options(
            {
                "appearance": AppearanceChoice.KEEP.value,
                "keepConfigAndProfiles": False,
                "keepLogs": False,
                "extra": True,
            }
        )
        self.assertFalse(result["ok"])

    def test_background_operation_reports_progress_and_result(self) -> None:
        runtime = FakeRuntime(self.layout)
        api = UninstallGuiApi(runtime)
        payload = {
            "appearance": AppearanceChoice.RESTORE.value,
            "keepConfigAndProfiles": True,
            "keepLogs": False,
        }

        started = api.start_uninstall(payload)
        self.assertTrue(started["ok"])
        operation_id = started["operationId"]
        for _ in range(100):
            status = api.get_uninstall_status(operation_id)
            if status["phase"] == "finished":
                break
            time.sleep(0.01)
        else:
            self.fail("Uninstall worker did not finish.")

        self.assertEqual(status["outcome"]["result"], "completed")
        self.assertIn("task-removed", status["stages"])
        self.assertIn("completed", status["stages"])
        self.assertEqual(len(runtime.calls), 1)

    def test_selection_runtime_only_stages_and_hands_off(self) -> None:
        stager = FakeStager()
        runtime = SelectionUninstallRuntime(self.layout, stager)
        options = runtime.default_options()
        stages: list[str] = []

        result = runtime.uninstall(options, progress=stages.append)

        self.assertEqual(result["result"], "handoff")
        self.assertTrue(result["launched"])
        self.assertEqual(stager.options, [options])
        self.assertEqual(stages, ["staging-copy", "handoff"])

    def test_exit_guard_is_bound_only_to_execute_runtime(self) -> None:
        closed = FakeEvent()
        window = SimpleNamespace(events=SimpleNamespace(closed=closed))
        workspace = Path(self.temporary.name)
        selection = FakeExitGuardApi("selection", workspace)
        execute = FakeExitGuardApi("execute", workspace)

        self.assertIsNone(_bind_execute_exit_guard(window, selection))
        self.assertEqual(closed.callbacks, [])
        self.assertIsNone(selection.close_guard)
        guard = _bind_execute_exit_guard(window, execute)
        self.assertIsInstance(guard, _ExecuteExitGuard)
        self.assertEqual(len(closed.callbacks), 1)
        self.assertIs(guard, execute.close_guard)
        self.assertIs(closed.callbacks[0], guard)

    def test_execute_guard_is_armed_when_webview_loop_returns(self) -> None:
        webview = SimpleNamespace(start=Mock())
        guard = Mock()

        _run_webview_event_loop(webview, guard)

        webview.start.assert_called_once_with(
            gui="edgechromium",
            debug=False,
            private_mode=True,
        )
        guard.assert_called_once_with()

    def test_execute_guard_is_armed_when_webview_loop_fails(self) -> None:
        webview = SimpleNamespace(start=Mock(side_effect=RuntimeError("closed")))
        guard = Mock()

        with self.assertRaisesRegex(RuntimeError, "closed"):
            _run_webview_event_loop(webview, guard)

        guard.assert_called_once_with()

    @patch("theme_scheduler.uninstall_gui.threading.Timer")
    def test_execute_exit_guard_is_idempotent_and_delayed(
        self,
        timer_factory,
    ) -> None:
        timer = Mock()
        timer_factory.return_value = timer
        workspace = Path(self.temporary.name) / "execute"
        workspace.mkdir()
        guard = _ExecuteExitGuard(workspace)

        guard()
        guard()

        self.assertEqual(
            (workspace / "exit-requested").read_text(encoding="ascii"),
            f"{os.getpid()}\n",
        )
        timer_factory.assert_called_once()
        args, kwargs = timer_factory.call_args
        self.assertEqual(args[0], 0.75)
        self.assertEqual(kwargs["args"], (0,))
        self.assertTrue(timer.daemon)
        timer.start.assert_called_once_with()

    def test_close_window_guard_bypasses_blocking_destroy_callback(self) -> None:
        runtime = FakeRuntime(self.layout)
        closed: list[str] = []
        api = UninstallGuiApi(
            runtime,
            close_window=lambda: closed.append("window"),
            close_guard=lambda: closed.append("process"),
        )

        self.assertEqual(api.close_window(), {"ok": True})
        self.assertEqual(closed, ["process"])

    def test_result_exit_uses_dedicated_guard_without_window_destroy(self) -> None:
        runtime = FakeRuntime(self.layout)
        closed: list[str] = []
        api = UninstallGuiApi(
            runtime,
            close_window=lambda: closed.append("window"),
            close_guard=lambda: closed.append("process"),
        )

        self.assertEqual(api.request_exit(), {"ok": True})
        self.assertEqual(closed, ["process"])

    def test_native_execute_close_is_cancelled_after_arming_guard(self) -> None:
        runtime = FakeRuntime(self.layout)
        closed: list[str] = []
        api = UninstallGuiApi(
            runtime,
            close_window=lambda: closed.append("window"),
            close_guard=lambda: closed.append("process"),
        )

        self.assertFalse(api.window_closing())
        self.assertEqual(closed, ["process"])

    def test_native_selection_close_is_allowed_without_guard(self) -> None:
        api = UninstallGuiApi(FakeRuntime(self.layout))

        self.assertTrue(api.window_closing())

    def test_preview_runtime_has_no_filesystem_side_effect(self) -> None:
        runtime = PreviewUninstallRuntime(layout=self.layout)
        before = set(Path(self.temporary.name).rglob("*"))
        stages: list[str] = []

        result = runtime.uninstall(
            runtime.default_options(),
            progress=stages.append,
        )

        self.assertEqual(result["result"], "completed")
        self.assertTrue(result["verified"])
        self.assertFalse(result["windowsChanged"])
        self.assertFalse(result["filesChanged"])
        self.assertEqual(
            set(Path(self.temporary.name).rglob("*")),
            before,
        )


class UninstallFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (PROJECT_ROOT / "ui" / "html" / "uninstall.html").read_text(
            encoding="utf-8"
        )
        cls.app = (PROJECT_ROOT / "ui" / "js" / "uninstall.js").read_text(
            encoding="utf-8"
        )
        cls.wizard_runtime = (
            PROJECT_ROOT / "ui" / "js" / "wizard_runtime.js"
        ).read_text(encoding="utf-8")
        cls.spec = (PROJECT_ROOT / "packaging" / "ThemeScheduler.spec").read_text(
            encoding="utf-8"
        )

    def test_three_choices_share_one_options_page(self) -> None:
        self.assertIn('id="restore-appearance"', self.html)
        self.assertIn('id="keep-config"', self.html)
        self.assertIn('id="keep-logs"', self.html)
        self.assertEqual(self.html.count('data-page="options"'), 1)

    def test_wizard_has_confirmation_progress_and_result_pages(self) -> None:
        for page in ("welcome", "options", "confirm", "progress", "result"):
            self.assertIn(f'data-page="{page}"', self.html)
        self.assertIn("renderConfirmation()", self.app)
        self.assertIn("renderProgress(response)", self.app)
        self.assertIn("renderResult(response)", self.app)
        self.assertIn('next.textContent = "退出";', self.app)

    def test_frontend_uses_only_narrow_api_and_no_dialog_chaining(self) -> None:
        for action in (
            "get_status",
            "validate_options",
            "start_uninstall",
            "get_uninstall_status",
            "close_window",
            "request_exit",
        ):
            self.assertIn(f"window.pywebview.api.{action}", self.app)
        self.assertIn("await requestExit();", self.app)
        self.assertNotIn("alert(", self.app)
        self.assertNotIn("confirm(", self.app)
        self.assertNotIn("prompt(", self.app)

    def test_uninstaller_bundle_contains_frontend_and_shared_wizard(self) -> None:
        self.assertIn('(str(ui_root), "ui")', self.spec)
        self.assertIn("../css/tokens.css", self.html)
        self.assertIn("../js/brand.js", self.html)
        self.assertIn("../js/wizard_runtime.js", self.html)
        self.assertIn("createNavigation", self.wizard_runtime)
        self.assertIn("const setPage = wizardRuntime.createNavigation", self.app)
        self.assertNotIn("function setPage(page)", self.app)

    def test_production_no_argument_route_launches_gui(self) -> None:
        with (
            patch(
                "theme_scheduler.cli.uninstall.InstallLayout.default",
                return_value=InstallLayout.default(),
            ),
            patch("theme_scheduler.cli.uninstall.IndependentUninstallerStager"),
            patch(
                "theme_scheduler.uninstall_gui.launch_uninstall_window",
                return_value=17,
            ) as launch,
        ):
            result = uninstall_main([])

        self.assertEqual(result, 17)
        launch.assert_called_once()

    def test_execute_route_waits_for_launcher_before_creating_webview(self) -> None:
        request = SimpleNamespace(launcher_process_id=1234)
        workspace = PROJECT_ROOT / "artifacts" / "test-execute-workspace"
        order: list[str] = []
        process_api = SimpleNamespace(
            wait=lambda pid, timeout: order.append(f"wait:{pid}:{timeout}") or True
        )

        with (
            patch(
                "theme_scheduler.cli.uninstall._validate_execute_request",
                return_value=(request, workspace),
            ),
            patch(
                "theme_scheduler.cli.uninstall.WindowsProcessApi",
                return_value=process_api,
            ),
            patch(
                "theme_scheduler.cli.uninstall._create_live_service",
                return_value=object(),
            ),
            patch(
                "theme_scheduler.uninstall_gui.launch_uninstall_window",
                side_effect=lambda api: order.append("launch") or 19,
            ),
        ):
            result = uninstall_main(
                [
                    "execute",
                    "--request",
                    str(workspace / "request.json"),
                    "--authorization-token",
                    "a" * 64,
                ]
            )

        self.assertEqual(result, 19)
        self.assertEqual(order, ["wait:1234:15000", "launch"])


if __name__ == "__main__":
    unittest.main()
