"""Classic single-window pywebview launcher for independent uninstall."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from .resources import resource_path
from .uninstall_gui_api import UninstallGuiApi
from .webview_runtime import (
    detect_webview2_runtime,
    show_native_webview2_error,
)

_EXECUTE_EXIT_GRACE_SECONDS = 0.75
_EXECUTE_EXIT_SIGNAL = "exit-requested"


class _ExecuteExitGuard:
    """Schedule one hard-exit fallback for the temporary execute process."""

    def __init__(self, workspace: Path) -> None:
        self._scheduled = threading.Event()
        self._exit_signal = Path(workspace) / _EXECUTE_EXIT_SIGNAL

    def __call__(self) -> None:
        if self._scheduled.is_set():
            return
        try:
            self._exit_signal.write_text(
                f"{os.getpid()}\n",
                encoding="ascii",
                newline="\n",
            )
        except OSError:
            # The external cleanup process also proceeds once both exact
            # staged processes have exited, even if the workspace vanished
            # before this final signal could be written.
            pass
        self._scheduled.set()
        timer = threading.Timer(
            _EXECUTE_EXIT_GRACE_SECONDS,
            os._exit,
            args=(0,),
        )
        timer.daemon = True
        timer.start()


def _bind_execute_exit_guard(
    window: Any,
    api: UninstallGuiApi,
) -> _ExecuteExitGuard | None:
    """Bind the hard-exit fallback only to the temporary execute window."""

    if getattr(api.runtime, "mode", None) != "execute":
        return None
    workspace = getattr(api.runtime, "workspace", None)
    if workspace is None:
        raise RuntimeError("Execute uninstaller workspace is unavailable.")
    guard = _ExecuteExitGuard(Path(workspace))
    api.bind_close_guard(guard)
    window.events.closed += guard
    return guard


def _run_webview_event_loop(
    webview: Any,
    exit_guard: _ExecuteExitGuard | None,
) -> None:
    """Run WebView and always arm execute-process cleanup on return."""
    try:
        webview.start(
            gui="edgechromium",
            debug=False,
            private_mode=True,
        )
    finally:
        if exit_guard is not None:
            exit_guard()


def launch_uninstall_window(
    api: UninstallGuiApi,
    *,
    preview: bool = False,
) -> int:
    runtime = detect_webview2_runtime()
    if not runtime.available:
        show_native_webview2_error(
            runtime.message + "\n\n请安装 Microsoft Edge WebView2 Runtime 后重试。"
        )
        return 2
    entry = resource_path("ui", "html", "uninstall.html")
    if not entry.is_file():
        show_native_webview2_error(f"卸载界面资源缺失：{entry}")
        return 2

    import webview

    window = webview.create_window(
        (
            "ThemeScheduler 卸载程序 · 安全预览"
            if preview
            else "ThemeScheduler 卸载程序"
        ),
        url=entry.as_uri(),
        js_api=api,
        width=720,
        height=540,
        min_size=(720, 540),
        resizable=False,
        background_color="#0b1020",
    )
    if window is None:
        raise RuntimeError("pywebview did not create the Uninstall window.")
    api.bind_close_window(window.destroy)
    exit_guard = _bind_execute_exit_guard(window, api)
    window.events.closing += api.window_closing
    _run_webview_event_loop(webview, exit_guard)
    return 0
