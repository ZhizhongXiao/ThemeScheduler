from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import Mock, patch

from theme_scheduler.gui import launch_gui
from theme_scheduler.gui_activation import (
    gui_activation_event_name,
    signal_existing_gui,
)
from theme_scheduler.webview_runtime import WebView2RuntimeStatus


class _Window:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def restore(self) -> None:
        self.calls.append(("restore", None))

    def show(self) -> None:
        self.calls.append(("show", None))

    def evaluate_js(self, script: str) -> None:
        self.calls.append(("evaluate_js", script))


class _Activation:
    instances: ClassVar[list[_Activation]] = []

    def __init__(self, event_name: str) -> None:
        self.event_name = event_name
        self.closed = False
        self.callback = None
        self.__class__.instances.append(self)

    def start(self, callback) -> None:
        self.callback = callback
        callback()

    def close(self) -> None:
        self.closed = True


class GuiRuntimeRiskTests(unittest.TestCase):
    def setUp(self) -> None:
        _Activation.instances.clear()

    def test_missing_webview2_stops_before_frontend_or_api_creation(self) -> None:
        missing = WebView2RuntimeStatus(False, None, None, "runtime missing")
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("theme_scheduler.gui.detect_webview2_runtime", return_value=missing),
            patch("theme_scheduler.gui.show_native_webview2_error") as show_error,
            patch("theme_scheduler.gui.frontend_entry") as frontend,
        ):
            result = launch_gui(data_root=Path(directory), installed=False)

        self.assertEqual(result, 2)
        frontend.assert_not_called()
        show_error.assert_called_once()
        self.assertIn("runtime missing", show_error.call_args.args[0])

    def test_window_creation_failure_is_explicit(self) -> None:
        available = WebView2RuntimeStatus(True, "1", "test", "available")
        webview = SimpleNamespace(create_window=Mock(return_value=None), start=Mock())
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "theme_scheduler.gui.detect_webview2_runtime", return_value=available
            ),
            patch(
                "theme_scheduler.workbench.create_live_gui_api", return_value=object()
            ),
            patch.dict(sys.modules, {"webview": webview}),
            self.assertRaisesRegex(RuntimeError, "did not create"),
        ):
            launch_gui(data_root=Path(directory), installed=False)

        webview.start.assert_not_called()

    def test_activation_focuses_window_and_listener_closes_after_exit(self) -> None:
        available = WebView2RuntimeStatus(True, "1", "test", "available")
        window = _Window()

        def start(*, func, **kwargs) -> None:
            func()

        webview = SimpleNamespace(create_window=Mock(return_value=window), start=start)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "theme_scheduler.gui.detect_webview2_runtime", return_value=available
            ),
            patch(
                "theme_scheduler.workbench.create_live_gui_api", return_value=object()
            ),
            patch("theme_scheduler.gui.WindowsGuiActivationListener", _Activation),
            patch.dict(sys.modules, {"webview": webview}),
        ):
            root = Path(directory)
            self.assertEqual(launch_gui(data_root=root, installed=False), 0)

        self.assertEqual(window.calls[0:2], [("restore", None), ("show", None)])
        script = window.calls[2][1]
        assert script is not None
        self.assertIn("themeSchedulerOpenHealth", script)
        self.assertEqual(
            _Activation.instances[0].event_name, gui_activation_event_name(root)
        )
        self.assertTrue(_Activation.instances[0].closed)

    def test_start_failure_still_closes_activation_listener(self) -> None:
        available = WebView2RuntimeStatus(True, "1", "test", "available")
        window = _Window()

        def fail_start(*, func, **kwargs) -> None:
            func()
            raise RuntimeError("webview initialization failed")

        webview = SimpleNamespace(
            create_window=Mock(return_value=window),
            start=fail_start,
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "theme_scheduler.gui.detect_webview2_runtime", return_value=available
            ),
            patch(
                "theme_scheduler.workbench.create_live_gui_api", return_value=object()
            ),
            patch("theme_scheduler.gui.WindowsGuiActivationListener", _Activation),
            patch.dict(sys.modules, {"webview": webview}),
            self.assertRaisesRegex(RuntimeError, "initialization failed"),
        ):
            launch_gui(data_root=Path(directory), installed=False)

        self.assertTrue(_Activation.instances[0].closed)

    def test_existing_gui_signal_never_creates_a_listener(self) -> None:
        kernel = Mock()
        kernel.OpenEventW.return_value = 42
        kernel.SetEvent.return_value = True
        with patch("theme_scheduler.gui_activation._kernel32", return_value=kernel):
            self.assertTrue(signal_existing_gui("Local\\ThemeScheduler.Test"))

        kernel.CreateEventW.assert_not_called()
        kernel.SetEvent.assert_called_once_with(42)
        kernel.CloseHandle.assert_called_once_with(42)


if __name__ == "__main__":
    unittest.main()
