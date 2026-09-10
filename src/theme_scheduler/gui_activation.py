"""Current-session activation channel for an on-demand installed GUI."""

from __future__ import annotations

import ctypes
import os
import threading
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path

_EVENT_MODIFY_STATE = 0x0002
_INFINITE = 0xFFFFFFFF
_WAIT_OBJECT_0 = 0


def gui_activation_event_name(data_root: Path) -> str:
    """Derive a stable, path-private event name for one data root."""

    normalized = str(Path(data_root).resolve()).casefold().encode("utf-8")
    digest = sha256(normalized).hexdigest()[:32]
    return rf"Local\ThemeScheduler.GuiActivation.{digest}"


def _kernel32():
    if os.name != "nt":
        raise OSError("GUI activation requires Windows.")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateEventW.argtypes = (
        ctypes.c_void_p,
        ctypes.c_bool,
        ctypes.c_bool,
        ctypes.c_wchar_p,
    )
    kernel32.CreateEventW.restype = ctypes.c_void_p
    kernel32.OpenEventW.argtypes = (
        ctypes.c_uint32,
        ctypes.c_bool,
        ctypes.c_wchar_p,
    )
    kernel32.OpenEventW.restype = ctypes.c_void_p
    kernel32.SetEvent.argtypes = (ctypes.c_void_p,)
    kernel32.SetEvent.restype = ctypes.c_bool
    kernel32.WaitForSingleObject.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint32,
    )
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_bool
    return kernel32


def signal_existing_gui(event_name: str) -> bool:
    """Signal an already-running GUI without creating a phantom listener."""

    kernel32 = _kernel32()
    handle = kernel32.OpenEventW(
        _EVENT_MODIFY_STATE,
        False,
        event_name,
    )
    if not handle:
        return False
    try:
        if not kernel32.SetEvent(handle):
            raise ctypes.WinError(ctypes.get_last_error())
        return True
    finally:
        kernel32.CloseHandle(handle)


class WindowsGuiActivationListener:
    """Block on a named event while the on-demand GUI is open."""

    def __init__(self, event_name: str) -> None:
        self._kernel32 = _kernel32()
        self._handle = self._kernel32.CreateEventW(
            None,
            False,
            False,
            event_name,
        )
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._closing = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, callback: Callable[[], None]) -> None:
        if self._thread is not None:
            raise RuntimeError("GUI activation listener is already running.")
        if not callable(callback):
            raise TypeError("GUI activation callback must be callable.")

        def wait_loop() -> None:
            while not self._closing.is_set():
                result = self._kernel32.WaitForSingleObject(
                    self._handle,
                    _INFINITE,
                )
                if result != _WAIT_OBJECT_0 or self._closing.is_set():
                    return
                try:
                    callback()
                except Exception:
                    # Activation is advisory. A failed focus request must not
                    # terminate the GUI or expose a background traceback.
                    continue

        self._thread = threading.Thread(
            target=wait_loop,
            name="ThemeSchedulerGuiActivation",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        if not self._handle:
            return
        self._closing.set()
        self._kernel32.SetEvent(self._handle)
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._kernel32.CloseHandle(self._handle)
        self._handle = None
