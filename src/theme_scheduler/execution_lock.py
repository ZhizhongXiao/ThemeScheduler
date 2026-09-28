"""Current-session Windows named mutex used by the automatic core."""

from __future__ import annotations

import os
import re
from hashlib import sha256
from pathlib import Path
from typing import Protocol

ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
_MUTEX_NAME_PATTERN = re.compile(
    r"Local\\ThemeScheduler\.(?:Auto|Lifecycle|NotificationDedup|EventLog)\."
    r"[0-9a-f]{24}\Z"
)
_MUTEX_PURPOSES = frozenset({"Auto", "Lifecycle", "NotificationDedup", "EventLog"})


class MutexApi(Protocol):
    def create(self, name: str) -> tuple[int, bool]: ...

    def wait(self, handle: int, timeout_ms: int) -> int: ...

    def release(self, handle: int) -> None: ...

    def close(self, handle: int) -> None: ...


class CtypesMutexApi:
    """Small Win32 adapter isolated for deterministic tests."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows named mutexes are only available on Windows.")
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateMutexW.argtypes = (
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        self._kernel32.CreateMutexW.restype = wintypes.HANDLE
        self._kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        self._kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self._kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        self._kernel32.ReleaseMutex.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def create(self, name: str) -> tuple[int, bool]:
        self._ctypes.set_last_error(0)
        handle = self._kernel32.CreateMutexW(None, True, name)
        error = self._ctypes.get_last_error()
        if not handle:
            raise self._ctypes.WinError(error)
        return int(handle), error == ERROR_ALREADY_EXISTS

    def release(self, handle: int) -> None:
        if not self._kernel32.ReleaseMutex(handle):
            raise self._ctypes.WinError(self._ctypes.get_last_error())

    def wait(self, handle: int, timeout_ms: int) -> int:
        result = int(self._kernel32.WaitForSingleObject(handle, timeout_ms))
        if result == WAIT_FAILED:
            raise self._ctypes.WinError(self._ctypes.get_last_error())
        return result

    def close(self, handle: int) -> None:
        if not self._kernel32.CloseHandle(handle):
            raise self._ctypes.WinError(self._ctypes.get_last_error())


class WindowsNamedMutexLock:
    """Named mutex with explicit ownership and handle cleanup."""

    def __init__(
        self,
        name: str,
        *,
        api: MutexApi | None = None,
        wait_timeout_ms: int | None = None,
    ) -> None:
        if not isinstance(name, str) or _MUTEX_NAME_PATTERN.fullmatch(name) is None:
            raise ValueError("Named mutex is outside the frozen ThemeScheduler scope.")
        if wait_timeout_ms is not None and (
            isinstance(wait_timeout_ms, bool)
            or not isinstance(wait_timeout_ms, int)
            or not 0 <= wait_timeout_ms < WAIT_FAILED
        ):
            raise ValueError("Mutex wait timeout must be a Windows DWORD value.")
        self.name = name
        self._api = api or CtypesMutexApi()
        self._wait_timeout_ms = wait_timeout_ms
        self._handle: int | None = None
        self._owned = False

    @property
    def acquired(self) -> bool:
        return self._owned

    def acquire(self) -> bool:
        if self._handle is not None:
            raise RuntimeError("Execution lock has already been acquired or attempted.")
        handle, already_exists = self._api.create(self.name)
        self._handle = handle
        if already_exists:
            if self._wait_timeout_ms is None:
                self._api.close(handle)
                self._handle = None
                return False
            try:
                result = self._api.wait(handle, self._wait_timeout_ms)
            except Exception:
                self._close_after_failed_acquire(handle)
                raise
            if result == WAIT_TIMEOUT:
                self._close_after_failed_acquire(handle)
                return False
            if result not in {WAIT_OBJECT_0, WAIT_ABANDONED}:
                self._close_after_failed_acquire(handle)
                raise OSError(f"Unexpected named mutex wait result: {result:#x}.")
        self._owned = True
        return True

    def _close_after_failed_acquire(self, handle: int) -> None:
        self._handle = None
        self._owned = False
        self._api.close(handle)

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        release_error: Exception | None = None
        try:
            if self._owned:
                self._api.release(handle)
        except Exception as exc:
            release_error = exc
        finally:
            self._owned = False
            self._handle = None
            try:
                self._api.close(handle)
            except Exception:
                if release_error is None:
                    raise
        if release_error is not None:
            raise release_error

    def __enter__(self) -> WindowsNamedMutexLock:
        if not self.acquire():
            raise RuntimeError("Another automatic run already owns the mutex.")
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def named_mutex_name_for_path(path: Path, *, purpose: str) -> str:
    """Derive a non-sensitive current-session mutex name for one product path."""

    if purpose not in _MUTEX_PURPOSES:
        raise ValueError("Mutex purpose is outside the frozen ThemeScheduler scope.")
    normalized = str(Path(path).resolve()).replace("/", "\\").casefold()
    digest = sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"Local\\ThemeScheduler.{purpose}.{digest}"
