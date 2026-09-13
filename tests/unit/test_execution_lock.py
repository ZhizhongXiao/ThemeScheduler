from __future__ import annotations

import unittest

from theme_scheduler.execution_lock import WindowsNamedMutexLock


class FakeMutexApi:
    def __init__(
        self,
        *,
        already_exists: bool = False,
        release_error: Exception | None = None,
    ) -> None:
        self.already_exists = already_exists
        self.release_error = release_error
        self.created: list[str] = []
        self.released: list[int] = []
        self.closed: list[int] = []

    def create(self, name: str) -> tuple[int, bool]:
        self.created.append(name)
        return 42, self.already_exists

    def release(self, handle: int) -> None:
        self.released.append(handle)
        if self.release_error is not None:
            raise self.release_error

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
