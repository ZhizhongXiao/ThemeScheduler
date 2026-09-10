"""On-demand pywebview control panel launcher."""

from __future__ import annotations

import sys
from pathlib import Path

from .gui_activation import (
    WindowsGuiActivationListener,
    gui_activation_event_name,
)
from .resources import resource_path
from .storage import UserDataLayout
from .webview_runtime import (
    detect_webview2_runtime,
    show_native_webview2_error,
)


def frontend_entry() -> Path:
    candidate = resource_path("ui", "html", "workbench.html")
    if not candidate.is_file():
        raise FileNotFoundError(f"GUI frontend is missing: {candidate}")
    return candidate.resolve()


def validate_live_executable(executable: Path) -> Path:
    """Reject interpreter and unresolved targets before enabling live task writes."""

    candidate = Path(executable).resolve()
    if not candidate.is_file():
        raise FileNotFoundError(f"Live-write executable does not exist: {candidate}")
    if candidate.suffix.casefold() != ".exe":
        raise ValueError("Live-write executable must be a Windows .exe file.")
    if candidate.name.casefold() in {
        "python.exe",
        "pythonw.exe",
        "py.exe",
    } or any(part.casefold() == ".venv" for part in candidate.parts):
        raise ValueError(
            "A Python interpreter or virtual-environment executable cannot be "
            "registered as the ThemeScheduler task target."
        )
    return candidate


def launch_gui(
    *,
    data_root: Path | None,
    installed: bool,
    executable: Path | None = None,
    allow_live_writes: bool = False,
    allow_system_reads: bool = False,
    initial_section: str | None = None,
) -> int:
    if initial_section not in {None, "maintenance", "health"}:
        raise ValueError("Unknown initial GUI section.")
    if data_root is not None and installed:
        raise ValueError("--data-root and --installed cannot be used together.")
    if installed:
        layout = UserDataLayout.default()
    elif data_root is not None:
        layout = UserDataLayout(Path(data_root).resolve())
    else:
        raise ValueError(
            "Source GUI launch requires --data-root; installed mode requires "
            "--installed."
        )
    if installed:
        allow_live_writes = True
        allow_system_reads = True
    elif allow_live_writes and executable is None:
        raise ValueError(
            "Development live writes require an explicit --executable target."
        )
    program = Path(executable or sys.executable).resolve()
    if allow_live_writes:
        program = validate_live_executable(program)

    runtime = detect_webview2_runtime()
    if not runtime.available:
        show_native_webview2_error(
            runtime.message
            + "\n\nInstall Microsoft Edge WebView2 Runtime and try again."
        )
        return 2

    entry = frontend_entry()
    entry_url = entry.as_uri()
    if initial_section is not None:
        entry_url = f"{entry_url}#{initial_section}"
    from .workbench import create_live_gui_api

    api = create_live_gui_api(
        layout,
        program,
        allow_live_writes=allow_live_writes,
        allow_system_reads=allow_system_reads,
    )

    # Deliberately delayed: the automatic entry point never imports pywebview.
    import webview

    window = webview.create_window(
        "ThemeScheduler",
        url=entry_url,
        js_api=api,
        width=1160,
        height=840,
        min_size=(820, 620),
        resizable=True,
        background_color="#0b1020",
    )
    if window is None:
        raise RuntimeError("pywebview did not create the ThemeScheduler window.")
    activation = WindowsGuiActivationListener(gui_activation_event_name(layout.root))

    def show_health() -> None:
        window.restore()
        window.show()
        window.evaluate_js(
            "window.themeSchedulerOpenHealth && window.themeSchedulerOpenHealth();"
        )

    try:
        webview.start(
            func=lambda: activation.start(show_health),
            gui="edgechromium",
            debug=False,
            private_mode=False,
            storage_path=str(layout.root / "WebView2"),
        )
    finally:
        activation.close()
    return 0
