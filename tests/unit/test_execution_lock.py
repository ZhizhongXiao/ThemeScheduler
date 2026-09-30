from __future__ import annotations

import unittest
from pathlib import Path

from theme_scheduler.execution_lock import (
    WAIT_ABANDONED,
    WAIT_OBJECT_0,
    WAIT_TIMEOUT,
    WindowsNamedMutexLock,
    named_mutex_name_for_path,
)


class FakeMutexApi:
    def __init__(
        self,
        *,
        already_exists: bool = False,
        release_error: Exception | None = None,
        wait_result: int = WAIT_OBJECT_0,
        wait_error: Exception | None = None,
    ) -> None:
        self.already_exists = already_exists
        self.release_error = release_error
        self.wait_result = wait_result
        self.wait_error = wait_error
        self.created: list[str] = []
        self.waited: list[tuple[int, int]] = []
        self.released: list[int] = []
        self.closed: list[int] = []

    def create(self, name: str) -> tuple[int, bool]:
        self.created.append(name)
        return 42, self.already_exists

    def release(self, handle: int) -> None:
        self.released.append(handle)
        if self.release_error is not None:
            raise self.release_error

    def wait(self, handle: int, timeout_ms: int) -> int:
        self.waited.append((handle, timeout_ms))
        if self.wait_error is not None:
            raise self.wait_error
        return self.wait_result

    def close(self, handle: int) -> None:
        self.closed.append(handle)


class WindowsNamedMutexLockTests(unittest.TestCase):
    NAME = r"Local\ThemeScheduler.Auto.0123456789abcdef01234567"

    def test_acquire_and_release_owns_and_closes_handle(self) -> None:
        api = FakeMutexApi()
        lock = WindowsNamedMutexLock(self.NAME, api=api)

        self.assertTrue(lock.acquire())
        self.assertTrue(lock.acquired)
        lock.release()

        self.assertFalse(lock.acquired)
        self.assertEqual(api.released, [42])
        self.assertEqual(api.closed, [42])

    def test_existing_mutex_is_busy_and_handle_is_closed(self) -> None:
        api = FakeMutexApi(already_exists=True)
        lock = WindowsNamedMutexLock(self.NAME, api=api)

        self.assertFalse(lock.acquire())

        self.assertEqual(api.released, [])
        self.assertEqual(api.closed, [42])

    def test_existing_mutex_waits_and_releases_after_acquiring(self) -> None:
        api = FakeMutexApi(already_exists=True, wait_result=WAIT_ABANDONED)
        lock = WindowsNamedMutexLock(self.NAME, api=api, wait_timeout_ms=30000)

        self.assertTrue(lock.acquire())
        self.assertEqual(api.waited, [(42, 30000)])
        self.assertTrue(lock.acquired)
        lock.release()

        self.assertEqual(api.released, [42])
        self.assertEqual(api.closed, [42])

    def test_wait_timeout_closes_handle_without_claiming_ownership(self) -> None:
        api = FakeMutexApi(already_exists=True, wait_result=WAIT_TIMEOUT)
        lock = WindowsNamedMutexLock(self.NAME, api=api, wait_timeout_ms=10)

        self.assertFalse(lock.acquire())

        self.assertFalse(lock.acquired)
        self.assertEqual(api.released, [])
        self.assertEqual(api.closed, [42])

    def test_wait_failure_closes_handle_and_propagates(self) -> None:
        api = FakeMutexApi(already_exists=True, wait_error=OSError("wait failed"))
        lock = WindowsNamedMutexLock(self.NAME, api=api, wait_timeout_ms=10)

        with self.assertRaisesRegex(OSError, "wait failed"):
            lock.acquire()

        self.assertFalse(lock.acquired)
        self.assertEqual(api.closed, [42])

    def test_release_error_still_closes_handle(self) -> None:
        api = FakeMutexApi(release_error=OSError("release failed"))
        lock = WindowsNamedMutexLock(self.NAME, api=api)
        lock.acquire()

        with self.assertRaisesRegex(OSError, "release failed"):
            lock.release()

        self.assertFalse(lock.acquired)
        self.assertEqual(api.closed, [42])

    def test_repeated_acquire_is_rejected(self) -> None:
        lock = WindowsNamedMutexLock(self.NAME, api=FakeMutexApi())
        lock.acquire()

        with self.assertRaisesRegex(RuntimeError, "already"):
            lock.acquire()
        lock.release()

    def test_mutex_name_scope_is_strict(self) -> None:
        lifecycle = (
            r"Local\ThemeScheduler.Lifecycle."
            r"0123456789abcdef01234567"
        )
        self.assertEqual(
            WindowsNamedMutexLock(
                lifecycle,
                api=FakeMutexApi(),
            ).name,
            lifecycle,
        )
        for purpose in ("NotificationDedup", "EventLog", "SchedulerMutation"):
            name = named_mutex_name_for_path(Path("data"), purpose=purpose)
            self.assertTrue(name.startswith(f"Local\\ThemeScheduler.{purpose}."))
            self.assertEqual(
                WindowsNamedMutexLock(name, api=FakeMutexApi()).name,
                name,
            )
        with self.assertRaisesRegex(ValueError, "purpose"):
            named_mutex_name_for_path(Path("data"), purpose="unapproved")
        with self.assertRaisesRegex(ValueError, "scope"):
            WindowsNamedMutexLock(
                r"Global\ThemeScheduler.Auto.invalid", api=FakeMutexApi()
            )
        with self.assertRaisesRegex(ValueError, "scope"):
            WindowsNamedMutexLock(
                r"Local\ThemeScheduler.Auto.not-a-hash",
                api=FakeMutexApi(),
            )
        with self.assertRaisesRegex(ValueError, "scope"):
            WindowsNamedMutexLock(
                r"Local\ThemeScheduler.Other.0123456789abcdef01234567",
                api=FakeMutexApi(),
            )


if __name__ == "__main__":
    unittest.main()
