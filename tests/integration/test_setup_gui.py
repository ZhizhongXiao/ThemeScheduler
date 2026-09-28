from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"

from theme_scheduler.lifecycle import InstallLayout
from theme_scheduler.setup_contracts import (
    SetupOptions,
    SetupPlan,
)
from theme_scheduler.setup_gui_api import SetupGuiApi
from theme_scheduler.setup_preview import PreviewSetupRuntime
from theme_scheduler.setup_service import SetupOutcome
from theme_scheduler.setup_windows import WindowsSetupRuntime


class FakeRuntime:
    def __init__(
        self,
        *,
        block: bool = False,
        result: str = "success",
        raise_error: bool = False,
    ) -> None:
        self.received: SetupOptions | None = None
        self.block = block
        self.result = result
        self.raise_error = raise_error
        self.launch_count = 0
        self.started = threading.Event()
        self.release = threading.Event()

    def preflight(self) -> SetupPlan:
        return SetupPlan(
            "install",
            "0.1.0",
            None,
            r"C:\Users\Test\AppData\Local\Programs\ThemeScheduler",
            r"C:\Users\Test\AppData\Local\ThemeScheduler",
            False,
        )

    def default_options(self, plan: SetupPlan) -> SetupOptions:
        return SetupOptions(True)

    def launch_installed_app(self) -> None:
        self.launch_count += 1

    def install(
        self,
        options: SetupOptions,
        *,
        progress,
    ) -> SetupOutcome:
        self.received = options
        progress("waiting-for-applications")
        progress("preparing-data")
        self.started.set()
        if self.block and not self.release.wait(2):
            raise TimeoutError("Fake Setup was not released.")
        if self.raise_error:
            raise RuntimeError("injected GUI runtime error")
        progress("deploying-files")
        progress("applying-integration")
        progress("verifying-installation")
        progress("committing-files")
        progress("completed" if self.result.startswith("success") else "failed")
        return SetupOutcome(
            self.result,
            "install",
            "0.1.0",
            self.result.startswith("success"),
            self.result in {"failed", "partial"},
            self.result == "failed",
            False,
            False,
            None,
            "installed",
            "lifecycle-20260725T120000-11111111",
        )


def wait_for_finish(api: SetupGuiApi, operation_id: str) -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = api.get_install_status(operation_id)
        if result["ok"] and result["phase"] == "finished":
            return result
        time.sleep(0.01)
    raise AssertionError("Setup worker did not finish.")


class SetupGuiApiTests(unittest.TestCase):
    def test_status_exposes_only_frozen_plan_defaults_and_paths(self) -> None:
        result = SetupGuiApi(FakeRuntime()).get_status()
        self.assertTrue(result["ok"])
        self.assertEqual(result["plan"]["operation"], "install")
        self.assertTrue(result["defaults"]["desktopShortcut"])
        self.assertEqual(
            set(result),
            {
                "ok",
                "installRoot",
                "dataRoot",
                "plan",
                "defaults",
                "previewMode",
            },
        )
        self.assertFalse(result["previewMode"])

    def test_validate_options_normalizes_without_running_install(self) -> None:
        runtime = FakeRuntime()
        payload = SetupOptions.defaults().as_dict()

        result = SetupGuiApi(runtime).validate_options(payload)

        self.assertTrue(result["ok"])
        self.assertEqual(result["options"], payload)
        self.assertIsNone(runtime.received)

    def test_background_install_reports_real_stages_and_writes_log(self) -> None:
        runtime = FakeRuntime()
        options = SetupOptions(True)
        with tempfile.TemporaryDirectory() as temporary:
            log_root = Path(temporary) / "logs"
            api = SetupGuiApi(
                runtime,
                log_root=log_root,
            )

            started = api.start_install(options.as_dict())
            result = wait_for_finish(api, started["operationId"])

            self.assertTrue(started["ok"])
            self.assertEqual(runtime.received, options)
            self.assertEqual(result["outcome"]["result"], "success")
            self.assertIn("deploying-files", result["stages"])
            self.assertIn("committing-files", result["stages"])
            self.assertTrue(result["logAvailable"])
            logs = list(log_root.glob("setup-*.json"))
            self.assertEqual(len(logs), 1)
            payload = json.loads(logs[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["operationId"], started["operationId"])
            self.assertEqual(payload["outcome"]["result"], "success")

    def test_invalid_frontend_payload_never_reaches_runtime(self) -> None:
        runtime = FakeRuntime()
        payload = SetupOptions.defaults().as_dict()
        payload["installRoot"] = r"C:\Other"

        result = SetupGuiApi(runtime).start_install(payload)

        self.assertFalse(result["ok"])
        self.assertIsNone(runtime.received)

    def test_running_install_rejects_duplicate_and_all_window_closes(self) -> None:
        runtime = FakeRuntime(block=True)
        closed: list[bool] = []
        with tempfile.TemporaryDirectory() as temporary:
            api = SetupGuiApi(
                runtime,
                close_window=lambda: closed.append(True),
                log_root=Path(temporary),
            )
            first = api.start_install(SetupOptions.defaults().as_dict())
            self.assertTrue(runtime.started.wait(1))

            duplicate = api.start_install(SetupOptions.defaults().as_dict())
            self.assertFalse(duplicate["ok"])
            self.assertFalse(api.window_close_allowed())
            self.assertFalse(api.can_close()["allowed"])
            self.assertFalse(api.close_window()["ok"])
            self.assertEqual(closed, [])

            runtime.release.set()
            wait_for_finish(api, first["operationId"])
            self.assertTrue(api.window_close_allowed())
            self.assertEqual(api.close_window(), {"ok": True})
            self.assertEqual(closed, [True])

    def test_unexpected_worker_error_finishes_and_keeps_window_recoverable(
        self,
    ) -> None:
        runtime = FakeRuntime(raise_error=True)
        with tempfile.TemporaryDirectory() as temporary:
            api = SetupGuiApi(
                runtime,
                log_root=Path(temporary),
            )
            started = api.start_install(SetupOptions.defaults().as_dict())
            result = wait_for_finish(api, started["operationId"])

            self.assertIsNone(result["outcome"])
            self.assertIn("injected GUI runtime error", result["error"])
            self.assertTrue(result["logAvailable"])
            self.assertTrue(api.window_close_allowed())

    def test_log_opener_has_no_frontend_path_parameter(self) -> None:
        runtime = FakeRuntime()
        opened: list[Path] = []
        with tempfile.TemporaryDirectory() as temporary:
            api = SetupGuiApi(
                runtime,
                log_root=Path(temporary),
                open_path=opened.append,
            )
            started = api.start_install(SetupOptions.defaults().as_dict())
            wait_for_finish(api, started["operationId"])

            self.assertEqual(api.open_setup_log(), {"ok": True})
            self.assertEqual(len(opened), 1)
            self.assertEqual(opened[0].parent, Path(temporary))

    def test_close_before_window_binding_reports_not_ready(self) -> None:
        api = SetupGuiApi(FakeRuntime())
        result = api.close_window()
        self.assertFalse(result["ok"])
        error = result["error"]
        assert isinstance(error, str)
        self.assertIn("尚未就绪", error)

    def test_success_completion_can_launch_gui_or_only_close(self) -> None:
        runtime = FakeRuntime()
        closed: list[bool] = []
        with tempfile.TemporaryDirectory() as temporary:
            api = SetupGuiApi(
                runtime,
                close_window=lambda: closed.append(True),
                log_root=Path(temporary),
            )
            started = api.start_install(SetupOptions.defaults().as_dict())
            wait_for_finish(api, started["operationId"])

            result = api.complete(True)

            self.assertTrue(result["ok"])
            self.assertTrue(result["launched"])
            self.assertEqual(runtime.launch_count, 1)
            self.assertEqual(closed, [True])

        runtime = FakeRuntime()
        closed = []
        with tempfile.TemporaryDirectory() as temporary:
            api = SetupGuiApi(
                runtime,
                close_window=lambda: closed.append(True),
                log_root=Path(temporary),
            )
            started = api.start_install(SetupOptions.defaults().as_dict())
            wait_for_finish(api, started["operationId"])

            result = api.complete(False)

            self.assertTrue(result["ok"])
            self.assertFalse(result["launched"])
            self.assertEqual(runtime.launch_count, 0)
            self.assertEqual(closed, [True])

    @patch("theme_scheduler.setup_windows.subprocess.Popen")
    def test_installed_gui_launch_is_visible_and_console_free_by_subsystem(
        self,
        popen,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            layout = InstallLayout(
                root / "Programs" / "ThemeScheduler",
                root / "Data" / "ThemeScheduler",
            )
            layout.executable.parent.mkdir(parents=True)
            layout.executable.touch()
            runtime = object.__new__(WindowsSetupRuntime)
            runtime.layout = layout

            runtime.launch_installed_app()

        popen.assert_called_once_with(
            [str(layout.executable.resolve(strict=False))],
            cwd=str(layout.executable.parent.resolve(strict=False)),
            close_fds=True,
        )
        _args, kwargs = popen.call_args
        self.assertNotIn("startupinfo", kwargs)
        self.assertNotIn("creationflags", kwargs)

    def test_preview_runtime_exercises_pages_without_windows_adapters(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = PreviewSetupRuntime(
                operation="upgrade",
                result="failed",
                layout=InstallLayout(
                    (root / "Programs" / "ThemeScheduler").resolve(),
                    (root / "ThemeScheduler").resolve(),
                ),
            )
            runtime._stage = lambda progress, stage: progress(stage)
            api = SetupGuiApi(runtime, log_root=root / "preview-logs")

            status = api.get_status()
            started = api.start_install(status["defaults"])
            result = wait_for_finish(api, started["operationId"])

            self.assertTrue(status["previewMode"])
            self.assertEqual(status["plan"]["operation"], "upgrade")
            self.assertEqual(result["outcome"]["result"], "failed")
            self.assertIn("rolling-back-files", result["stages"])
            self.assertFalse((root / "Programs").exists())
            self.assertFalse((root / "ThemeScheduler").exists())


class SetupFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (PROJECT_ROOT / "ui" / "html" / "setup.html").read_text(
            encoding="utf-8"
        )
        cls.app = (PROJECT_ROOT / "ui" / "js" / "setup.js").read_text(encoding="utf-8")
        cls.wizard_runtime = (
            PROJECT_ROOT / "ui" / "js" / "wizard_runtime.js"
        ).read_text(encoding="utf-8")
        cls.shared_styles = (PROJECT_ROOT / "ui" / "css" / "wizard.css").read_text(
            encoding="utf-8"
        )
        cls.setup_styles = (PROJECT_ROOT / "ui" / "css" / "setup.css").read_text(
            encoding="utf-8"
        )
        cls.tokens = (PROJECT_ROOT / "ui" / "css" / "tokens.css").read_text(
            encoding="utf-8"
        )
        cls.launcher = (SOURCE_ROOT / "theme_scheduler" / "setup_gui.py").read_text(
            encoding="utf-8"
        )
        cls.setup_spec = (
            PROJECT_ROOT / "packaging" / "ThemeSchedulerSetup.spec"
        ).read_text(encoding="utf-8")

    def test_five_pages_left_steps_and_fixed_footer_exist(self) -> None:
        for page in ("welcome", "options", "confirm", "progress", "result"):
            self.assertIn(f'data-page="{page}"', self.html)
            self.assertIn(f'data-step="{page}"', self.html)
        self.assertIn('class="wizard-footer"', self.html)
        self.assertIn("grid-template-columns: 180px", self.shared_styles)

    def test_writes_start_only_from_confirmation_page(self) -> None:
        self.assertIn('state.page === "confirm"', self.app)
        self.assertIn("window.pywebview.api.start_install(", self.app)
        self.assertIn("window.pywebview.api.get_install_status(", self.app)
        self.assertNotIn("window.pywebview.api.install(", self.app)
        self.assertNotIn("window.confirm(", self.app)
        self.assertNotIn("window.alert(", self.app)

    def test_setup_has_no_accent_editor_or_manual_theme_toggle(self) -> None:
        self.assertNotIn("color-hex", self.html)
        self.assertNotIn("ui-theme-toggle", self.html)
        self.assertNotIn('id="day-start"', self.html)
        self.assertNotIn('id="night-start"', self.html)
        self.assertNotIn('id="day-theme"', self.html)
        self.assertNotIn('id="night-theme"', self.html)
        self.assertNotIn('id="immediate-sync"', self.html)
        self.assertIn("@media (prefers-color-scheme: light)", self.shared_styles)
        self.assertIn("ThemeScheduler Source Han Sans", self.tokens)

    def test_completion_offers_open_gui_and_exit(self) -> None:
        self.assertIn('id="finish-only-button"', self.html)
        self.assertIn("完成并打开 ThemeScheduler", self.app)
        self.assertIn(">退出</button>", self.html)
        self.assertIn("window.pywebview.api.complete(", self.app)

    def test_retained_data_install_is_labeled_as_restore_install(self) -> None:
        self.assertIn('"恢复安装"', self.app)
        self.assertIn('operation === "install" && retainedData', self.app)
        self.assertGreaterEqual(
            self.app.count("status.plan.retainedData"),
            2,
        )

    def test_window_is_fixed_to_confirmed_size_and_closing_is_guarded(self) -> None:
        self.assertIn("width=720", self.launcher)
        self.assertIn("height=540", self.launcher)
        self.assertIn("resizable=False", self.launcher)
        self.assertIn(
            "window.events.closing += api.window_close_allowed",
            self.launcher,
        )

    def test_setup_bundle_contains_shared_wizard_and_embedded_font(self) -> None:
        self.assertIn('(str(ui_root), "ui")', self.setup_spec)
        self.assertIn("../css/tokens.css", self.html)
        self.assertIn("../css/wizard.css", self.html)
        self.assertIn("../fonts/ThemeSchedulerSourceHanSans.otf", self.tokens)
        self.assertIn("../js/brand.js", self.html)
        self.assertIn("../js/wizard_runtime.js", self.html)

    def test_wizard_navigation_and_common_components_have_one_owner(self) -> None:
        self.assertIn("createNavigation", self.wizard_runtime)
        self.assertIn("const setPage = wizardRuntime.createNavigation", self.app)
        self.assertNotIn("function setPage(page)", self.app)
        for selector in (
            ".welcome-facts {",
            ".confirmation-list, .result-facts {",
            ".progress-track {",
            ".progress-list {",
        ):
            self.assertIn(selector, self.shared_styles)
            self.assertNotIn(selector, self.setup_styles)

    def test_source_preview_is_visibly_separate_from_live_setup(self) -> None:
        self.assertIn("status.previewMode", self.app)
        self.assertIn('"演示安装"', self.app)
        preview_cli = (
            SOURCE_ROOT / "theme_scheduler" / "cli" / "preview.py"
        ).read_text(encoding="utf-8")
        self.assertIn("PreviewSetupRuntime", preview_cli)
        self.assertIn('commands.add_parser("setup"', preview_cli)
        self.assertIn('"artifacts" / "preview" / "setup" / "logs"', preview_cli)


if __name__ == "__main__":
    unittest.main()
