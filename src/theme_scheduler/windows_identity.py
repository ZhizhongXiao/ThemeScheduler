"""Explicit process AppUserModelID adapter for the installed Windows product."""

from __future__ import annotations

import ctypes
import os
from typing import Protocol

from .errors import ThemeSchedulerRuntimeError
from .notification_contracts import APP_USER_MODEL_ID


class WindowsIdentityError(ThemeSchedulerRuntimeError):
    """Raised when the process identity cannot be set and verified."""


class ProcessIdentityApi(Protocol):
    def set_current(self, app_user_model_id: str) -> None: ...

    def get_current(self) -> str: ...


class _CtypesProcessIdentityApi:
    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Explicit AppUserModelID requires Windows.")
        self._shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        self._ole32 = ctypes.OleDLL("ole32")
        self._set = self._shell32.SetCurrentProcessExplicitAppUserModelID
        self._set.argtypes = [ctypes.c_wchar_p]
        self._set.restype = ctypes.c_long
        self._get = self._shell32.GetCurrentProcessExplicitAppUserModelID
        self._get.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        self._get.restype = ctypes.c_long
        self._free = self._ole32.CoTaskMemFree
        self._free.argtypes = [ctypes.c_void_p]
        self._free.restype = None

    @staticmethod
    def _check_hresult(result: int, action: str) -> None:
        if result < 0:
            raise WindowsIdentityError(
                f"{action} failed with HRESULT 0x{result & 0xFFFFFFFF:08X}."
            )

    def set_current(self, app_user_model_id: str) -> None:
        self._check_hresult(
            int(self._set(app_user_model_id)),
            "SetCurrentProcessExplicitAppUserModelID",
        )

    def get_current(self) -> str:
        pointer = ctypes.c_void_p()
        self._check_hresult(
            int(self._get(ctypes.byref(pointer))),
            "GetCurrentProcessExplicitAppUserModelID",
        )
        if not pointer.value:
            raise WindowsIdentityError(
                "Windows returned an empty explicit AppUserModelID."
            )
        try:
            return ctypes.wstring_at(pointer.value)
        finally:
            self._free(pointer)


def configure_current_process_identity(
    api: ProcessIdentityApi | None = None,
) -> str:
    """Set and read back the one frozen product identity."""

    backend = api or _CtypesProcessIdentityApi()
    backend.set_current(APP_USER_MODEL_ID)
    actual = backend.get_current()
    if actual != APP_USER_MODEL_ID:
        raise WindowsIdentityError(
            "Explicit AppUserModelID readback did not match the product."
        )
    return actual


def read_current_process_identity(
    api: ProcessIdentityApi | None = None,
) -> str:
    """Read the explicit identity without changing process or shell state."""

    backend = api or _CtypesProcessIdentityApi()
    actual = backend.get_current()
    if actual != APP_USER_MODEL_ID:
        raise WindowsIdentityError(
            "Explicit AppUserModelID does not match the product."
        )
    return actual
