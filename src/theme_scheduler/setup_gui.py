"""Single-file Setup pywebview launcher."""

from __future__ import annotations

from .resources import resource_path
from .setup_gui_api import SetupGuiApi
from .webview_runtime import (
    detect_webview2_runtime,
    show_native_webview2_error,
)


def _launch_setup_window(
    api: SetupGuiApi,
    *,
    title: str,
) -> int:
    runtime = detect_webview2_runtime()
    if not runtime.available:
        show_native_webview2_error(
            runtime.message + "\n\n请安装 Microsoft Edge WebView2 Runtime 后重试。"
        )
        return 2
    entry = resource_path("ui", "html", "setup.html")
    if not entry.is_file():
        show_native_webview2_error(f"安装界面资源缺失：{entry}")  # noqa: RUF001 - Preserve native Chinese UI punctuation.
        return 2

    # Delayed import keeps the native dependency error path independent.
    import webview

    window = webview.create_window(
        title,
        url=entry.as_uri(),
        js_api=api,
        width=720,
        height=540,
        min_size=(720, 540),
        resizable=False,
        background_color="#0b1020",
    )
    if window is None:
        raise RuntimeError("pywebview did not create the Setup window.")
    api.bind_close_window(window.destroy)
    window.events.closing += api.window_close_allowed
    webview.start(
        gui="edgechromium",
        debug=False,
        private_mode=True,
    )
    return 0


def launch_setup() -> int:
    return _launch_setup_window(
        SetupGuiApi(),
        title="ThemeScheduler 安装程序",
    )


def launch_setup_preview(api: SetupGuiApi) -> int:
    return _launch_setup_window(
        api,
        title="ThemeScheduler 安装程序 · 安全预览",
    )
