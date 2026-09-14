"""Transactional Stage 8.5 independent-uninstall orchestration."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .core import Clock, ExecutionLock, SystemClock
from .errors import ThemeSchedulerRuntimeError
from .protocol_registration import NotificationProtocolBackend
from .scheduler import DEFAULT_TASK_PATH, TaskSchedulerBackend
from .system_integration import (
    InstalledAppRegistryBackend,
    ShortcutBackend,
)
from .uninstall_contracts import (
    AppearanceChoice,
    UninstallContractError,
    UninstallJournal,
    UninstallJournalStore,
    UninstallPlan,
    UninstallRequest,
    build_uninstall_plan,
)

_REPARSE_POINT_ATTRIBUTE = 0x400


def lifecycle_mutex_name_for_program_root(program_root: Path) -> str:
    root = Path(program_root)
    if not root.is_absolute():
        raise ValueError("Lifecycle mutex root must be absolute.")
    normalized = os.path.normcase(str(root.resolve(strict=False)))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"Local\\ThemeScheduler.Lifecycle.{digest}"


class UninstallServiceError(ThemeSchedulerRuntimeError):
    """Raised when a mandatory uninstall step cannot be verified."""


class AppearanceRestoreOutcome(Protocol):
    @property
    def appearance_applied(self) -> bool | None: ...

    @property
    def windows_verified(self) -> bool: ...

    @property
    def system_mode_preserved(self) -> bool | None: ...

    @property
    def paused_after(self) -> bool | None: ...

    @property
    def message(self) -> str: ...


class InstalledProcessGuard(Protocol):
    def ensure_idle(self, executable: Path) -> None: ...


class SelfCleanupScheduler(Protocol):
    def schedule(
        self,
        workspace: Path,
        *,
        wait_pid: int,
    ) -> None: ...


AppearanceRestorer = Callable[[], AppearanceRestoreOutcome]


@dataclass(frozen=True)
class UninstallOutcome:
    result: str
    completed_steps: tuple[str, ...]
    appearance_restored: bool
    task_removed: bool
    shortcuts_removed: bool
    registration_removed: bool
    program_root_removed: bool
    data_cleaned: bool
    self_cleanup_scheduled: bool
    journal_path: Path
    residual_paths: tuple[Path, ...]
    message: str

    @property
    def verified(self) -> bool:
        return self.result == "completed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "completedSteps": list(self.completed_steps),
            "appearanceRestored": self.appearance_restored,
            "taskRemoved": self.task_removed,
            "shortcutsRemoved": self.shortcuts_removed,
            "registrationRemoved": self.registration_removed,
            "programRootRemoved": self.program_root_removed,
            "dataCleaned": self.data_cleaned,
            "selfCleanupScheduled": self.self_cleanup_scheduled,
            "journalPath": str(self.journal_path.resolve(strict=False)),
            "residualPaths": [
                str(path.resolve(strict=False)) for path in self.residual_paths
            ],
            "verified": self.verified,
            "windowsChanged": any(
                (
                    self.task_removed,
                    self.shortcuts_removed,
                    self.registration_removed,
                    self.appearance_restored,
                )
            ),
            "filesChanged": (self.program_root_removed or self.data_cleaned),
            "message": self.message,
        }


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction):
        try:
            if is_junction():
                return True
        except OSError:
            return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & _REPARSE_POINT_ATTRIBUTE)


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
        str(right.resolve(strict=False))
    )


class StrictUninstallFileCleaner:
    """Delete only frozen owned roots, refusing every reparse point."""

    @staticmethod
    def _assert_regular_tree(root: Path) -> None:
        if _is_reparse_point(root):
            raise UninstallServiceError(
                f"Refusing non-directory or reparse cleanup root: {root}"
            )
        if not root.exists():
            return
        if not root.is_dir():
            raise UninstallServiceError(
                f"Refusing non-directory or reparse cleanup root: {root}"
            )
        for current, directories, files in os.walk(
            root,
            topdown=True,
            followlinks=False,
        ):
            current_path = Path(current)
            for name in (*directories, *files):
                candidate = current_path / name
                if _is_reparse_point(candidate):
                    raise UninstallServiceError(
                        f"Refusing cleanup tree with reparse point: {candidate}"
                    )
                try:
                    mode = candidate.lstat().st_mode
                except OSError as exc:
                    raise UninstallServiceError(
                        f"Cannot inspect cleanup target: {candidate}"
                    ) from exc
                if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                    raise UninstallServiceError(
                        f"Cleanup target is not a regular file/directory: {candidate}"
                    )

    @classmethod
    def _remove_target(cls, target: Path) -> bool:
        if _is_reparse_point(target):
            raise UninstallServiceError(f"Refusing reparse cleanup target: {target}")
        if not target.exists():
            return False
        if target.is_dir():
            cls._assert_regular_tree(target)
            shutil.rmtree(target)
        elif target.is_file():
            target.unlink()
        else:
            raise UninstallServiceError(
                f"Refusing non-regular cleanup target: {target}"
            )
        if target.exists():
            raise UninstallServiceError(f"Cleanup target still exists: {target}")
        return True

    def remove_program_root(self, plan: UninstallPlan) -> bool:
        layout = plan.layout
        target = layout.validate_owned_target(
            layout.program_root,
            allow_root=True,
        )
        if not _same_path(target, layout.program_root):
            raise UninstallServiceError(
                "Program cleanup target changed during validation."
            )
        return self._remove_target(target)

    def cleanup_user_data(self, plan: UninstallPlan) -> bool:
        root = plan.layout.data_root.resolve(strict=False)
        if root.name != "ThemeScheduler":
            raise UninstallServiceError(
                "User-data root is outside the frozen product boundary."
            )
        changed = False
        for target in plan.data_delete_targets:
            resolved = target.resolve(strict=False)
            if not (_same_path(resolved, root) or resolved.is_relative_to(root)):
                raise UninstallServiceError(
                    f"User-data cleanup target escaped its root: {resolved}"
                )
            changed = self._remove_target(resolved) or changed
        for preserved in plan.data_preserve_targets:
            if not preserved.resolve(strict=False).is_relative_to(root):
                raise UninstallServiceError("Preserved data target escaped its root.")
        if (
            root.exists()
            and not plan.remove_entire_data_root
            and not any(root.iterdir())
        ):
            root.rmdir()
            changed = True
        if plan.remove_entire_data_root and root.exists():
            raise UninstallServiceError(
                "Whole user-data cleanup did not remove its root."
            )
        return changed

    @staticmethod
    def residual_paths(plan: UninstallPlan) -> tuple[Path, ...]:
        candidates = (
            plan.layout.program_root,
            *plan.data_delete_targets,
        )
        return tuple(path for path in candidates if path.exists())


class NoRunningInstalledProcess:
    """Workspace/offline guard that assumes no installed executable is active."""

    def ensure_idle(self, executable: Path) -> None:
        return None


class RecordingSelfCleanupScheduler:
    """Offline scheduler used by workspace acceptance."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, int]] = []

    def schedule(self, workspace: Path, *, wait_pid: int) -> None:
        self.calls.append((Path(workspace), wait_pid))


@dataclass
class _OwnedLocks:
    lifecycle: bool = False
    automatic: bool = False


@dataclass(frozen=True)
class _UninstallStep:
    name: str
    action: Callable[[], None]


class IndependentUninstallService:
    """Execute fixed uninstall steps and preserve exact partial evidence."""

    def __init__(
        self,
        request: UninstallRequest,
        workspace: Path,
        lifecycle_lock: ExecutionLock,
        auto_lock: ExecutionLock,
        registry: InstalledAppRegistryBackend,
        shortcuts: ShortcutBackend,
        tasks: TaskSchedulerBackend,
        shortcut_paths: tuple[Path, ...],
        *,
        protocol: NotificationProtocolBackend | None = None,
        appearance_restorer: AppearanceRestorer | None = None,
        process_guard: InstalledProcessGuard | None = None,
        file_cleaner: StrictUninstallFileCleaner | None = None,
        self_cleanup: SelfCleanupScheduler | None = None,
        clock: Clock | None = None,
        process_id: int | None = None,
    ) -> None:
        self.request = request
        self.workspace = Path(workspace).resolve(strict=False)
        if (
            not self.workspace.is_absolute()
            or self.workspace.name != request.transaction_id
            or self.workspace.parent.name != "ThemeScheduler"
        ):
            raise UninstallContractError(
                "Uninstall workspace does not match its transaction."
            )
        self.layout = request.layout
        self.plan = build_uninstall_plan(
            self.layout,
            request.options,
        )
        self.lifecycle_lock = lifecycle_lock
        self.auto_lock = auto_lock
        self.registry = registry
        self.shortcuts = shortcuts
        self.tasks = tasks
        self.shortcut_paths = tuple(
            Path(path).resolve(strict=False) for path in shortcut_paths
        )
        self.protocol = protocol
        if not self.shortcut_paths:
            raise UninstallContractError(
                "At least one managed shortcut path is required."
            )
        self.appearance_restorer = appearance_restorer
        self.process_guard = process_guard or NoRunningInstalledProcess()
        self.file_cleaner = file_cleaner or StrictUninstallFileCleaner()
        self.self_cleanup = self_cleanup or RecordingSelfCleanupScheduler()
        self.clock = clock or SystemClock()
        self.process_id = process_id if process_id is not None else os.getpid()
        if (
            isinstance(self.process_id, bool)
            or not isinstance(self.process_id, int)
            or self.process_id <= 0
        ):
            raise UninstallContractError("process_id is invalid.")
        self.journal_store = UninstallJournalStore(self.workspace / "journal.json")

    def _now(self) -> str:
        instant = self.clock.now()
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise UninstallContractError("Uninstall clock must include a UTC offset.")
        return instant.isoformat(timespec="seconds")

    def _save(
        self,
        before: UninstallJournal,
        after: UninstallJournal,
    ) -> UninstallJournal:
        return self.journal_store.replace(before, after)

    def _initial_journal(self) -> UninstallJournal:
        if self.journal_store.path.exists():
            journal = self.journal_store.load()
            if journal.request != self.request:
                raise UninstallContractError(
                    "Existing uninstall journal belongs to another request."
                )
            if journal.status == "completed":
                return journal
            if journal.status == "partial":
                resumed = journal.resume(updated_at=self._now())
                return self._save(journal, resumed)
            return journal
        if _is_reparse_point(self.workspace):
            raise UninstallContractError("Existing uninstall workspace is unsafe.")
        if self.workspace.exists():
            if not self.workspace.is_dir():
                raise UninstallContractError("Existing uninstall workspace is unsafe.")
        else:
            self.workspace.mkdir(parents=True, exist_ok=False)
        return self.journal_store.create(UninstallJournal.create(self.request))

    def _fail(
        self,
        journal: UninstallJournal,
        exc: Exception,
    ) -> UninstallOutcome:
        message = (f"{type(exc).__name__}: {exc}")[:500]
        try:
            failed = journal.fail(message, updated_at=self._now())
            journal = self._save(journal, failed)
        except Exception as journal_exc:
            message = (
                f"{message}; journal update failed: "
                f"{type(journal_exc).__name__}: {journal_exc}"
            )[:500]
        return self._outcome(journal, "partial", message)

    def _outcome(
        self,
        journal: UninstallJournal,
        result: str,
        message: str,
    ) -> UninstallOutcome:
        steps = set(journal.completed_steps)
        residual = self.file_cleaner.residual_paths(self.plan)
        return UninstallOutcome(
            result=result,
            completed_steps=journal.completed_steps,
            appearance_restored=(
                self.request.options.appearance is AppearanceChoice.RESTORE
                and "appearance-handled" in steps
            ),
            task_removed="task-removed" in steps,
            shortcuts_removed="shortcuts-removed" in steps,
            registration_removed="registration-removed" in steps,
            program_root_removed="program-root-removed" in steps,
            data_cleaned="user-data-cleaned" in steps,
            self_cleanup_scheduled=("self-cleanup-scheduled" in steps),
            journal_path=self.journal_store.path,
            residual_paths=residual,
            message=message,
        )

    @staticmethod
    def _acquire(lock: ExecutionLock, label: str) -> None:
        try:
            acquired = lock.acquire()
        except Exception as exc:
            raise UninstallServiceError(
                f"{label} lock failed: {type(exc).__name__}: {exc}"
            ) from exc
        if not acquired:
            raise UninstallServiceError(f"{label} lock is owned by another operation.")

    @staticmethod
    def _release(lock: ExecutionLock, label: str) -> None:
        try:
            lock.release()
        except Exception as exc:
            raise UninstallServiceError(
                f"{label} lock release failed: {type(exc).__name__}: {exc}"
            ) from exc

    def _advance(
        self,
        journal: UninstallJournal,
        step: str,
    ) -> UninstallJournal:
        advanced = journal.advance(step, updated_at=self._now())
        return self._save(journal, advanced)

    def _remove_task(self) -> None:
        self.tasks.delete(DEFAULT_TASK_PATH)
        if self.tasks.read(DEFAULT_TASK_PATH) is not None:
            raise UninstallServiceError("Scheduled task still exists after deletion.")

    def _handle_appearance(self) -> None:
        if self.request.options.appearance is AppearanceChoice.KEEP:
            return
        if self.appearance_restorer is None:
            raise UninstallServiceError(
                "Appearance restore was requested but is unavailable."
            )
        first = self.appearance_restorer()
        if self._appearance_restore_verified(first):
            return
        if not self._appearance_restore_retry_is_safe(first):
            raise UninstallServiceError(
                f"Install appearance was not safely restored: {first.message}"
            )
        second = self.appearance_restorer()
        if self._appearance_restore_verified(second):
            return
        raise UninstallServiceError(
            "Install appearance was not safely restored after one retry. "
            f"First attempt: {first.message} Retry: {second.message}"
        )

    @staticmethod
    def _appearance_restore_verified(outcome: AppearanceRestoreOutcome) -> bool:
        return bool(
            outcome.appearance_applied is True
            and outcome.windows_verified
            and outcome.system_mode_preserved is True
            and outcome.paused_after is True
        )

    @staticmethod
    def _appearance_restore_retry_is_safe(
        outcome: AppearanceRestoreOutcome,
    ) -> bool:
        if outcome.paused_after is True:
            return True
        return bool(
            outcome.paused_after is None
            and outcome.appearance_applied is False
            and not outcome.windows_verified
        )

    def _remove_shortcuts(self) -> None:
        for path in self.shortcut_paths:
            self.shortcuts.restore(path, None)
        for path in self.shortcut_paths:
            if self.shortcuts.capture(path) is not None:
                raise UninstallServiceError(
                    f"Shortcut still exists after deletion: {path}"
                )

    def _remove_registration(self) -> None:
        if self.protocol is not None:
            self.protocol.restore(None)
            if self.protocol.capture() is not None:
                raise UninstallServiceError(
                    "Notification protocol still exists after deletion."
                )
        self.registry.restore(None)
        if self.registry.capture() is not None:
            raise UninstallServiceError(
                "Installed-app registration still exists after deletion."
            )

    def _verify_completed_prefix(self, journal: UninstallJournal) -> None:
        steps = set(journal.completed_steps)
        if "task-removed" in steps and self.tasks.read(DEFAULT_TASK_PATH) is not None:
            raise UninstallServiceError("Previously removed task has reappeared.")
        if "shortcuts-removed" in steps:
            for path in self.shortcut_paths:
                if self.shortcuts.capture(path) is not None:
                    raise UninstallServiceError(
                        "Previously removed shortcut has reappeared."
                    )
        if "registration-removed" in steps and self.registry.capture() is not None:
            raise UninstallServiceError(
                "Previously removed registration has reappeared."
            )
        if (
            "registration-removed" in steps
            and self.protocol is not None
            and self.protocol.capture() is not None
        ):
            raise UninstallServiceError(
                "Previously removed notification protocol has reappeared."
            )
        if "program-root-removed" in steps and self.layout.program_root.exists():
            raise UninstallServiceError(
                "Previously removed program root has reappeared."
            )

    @staticmethod
    def _report_progress(
        progress: Callable[[str], None] | None,
        stage: str,
    ) -> bool:
        if progress is None:
            return False
        try:
            progress(stage)
        except Exception:
            return False
        return True

    def _advance_and_report(
        self,
        journal: UninstallJournal,
        step: str,
        progress: Callable[[str], None] | None,
    ) -> UninstallJournal:
        updated = self._advance(journal, step)
        self._report_progress(progress, step)
        return updated

    def _prepare_journal(self, journal: UninstallJournal) -> UninstallJournal:
        self._verify_completed_prefix(journal)
        if journal.status == "planned":
            prepared = journal.with_status(
                "prepared",
                updated_at=self._now(),
            )
            journal = self._save(journal, prepared)
        if journal.status == "prepared":
            mutating = journal.with_status(
                "mutating",
                updated_at=self._now(),
            )
            journal = self._save(journal, mutating)
        return journal

    def _acquire_owned_locks(self, locks: _OwnedLocks) -> None:
        self._acquire(self.lifecycle_lock, "Lifecycle")
        locks.lifecycle = True
        self._acquire(self.auto_lock, "Automatic")
        locks.automatic = True

    def _release_owned_locks(self, locks: _OwnedLocks) -> None:
        if locks.automatic:
            self._release(self.auto_lock, "Automatic")
            locks.automatic = False
        if locks.lifecycle:
            self._release(self.lifecycle_lock, "Lifecycle")
            locks.lifecycle = False

    def _release_remaining_locks(self, locks: _OwnedLocks) -> tuple[str, ...]:
        errors: list[str] = []
        if locks.automatic:
            try:
                self._release(self.auto_lock, "Automatic")
                locks.automatic = False
            except Exception as exc:
                errors.append(str(exc))
        if locks.lifecycle:
            try:
                self._release(self.lifecycle_lock, "Lifecycle")
                locks.lifecycle = False
            except Exception as exc:
                errors.append(str(exc))
        return tuple(errors)

    def _remove_program_root(self) -> None:
        self.process_guard.ensure_idle(self.layout.executable)
        self.file_cleaner.remove_program_root(self.plan)
        if self.layout.program_root.exists():
            raise UninstallServiceError("Program root still exists after deletion.")

    def _cleanup_user_data(self) -> None:
        self.file_cleaner.cleanup_user_data(self.plan)
        residual = self.file_cleaner.residual_paths(self.plan)
        if residual:
            raise UninstallServiceError(
                "Mandatory cleanup targets remain: "
                + ", ".join(str(path) for path in residual)
            )

    def _mutation_steps(self) -> tuple[_UninstallStep, ...]:
        return (
            _UninstallStep("task-removed", self._remove_task),
            _UninstallStep("appearance-handled", self._handle_appearance),
            _UninstallStep("shortcuts-removed", self._remove_shortcuts),
            _UninstallStep("registration-removed", self._remove_registration),
            _UninstallStep("program-root-removed", self._remove_program_root),
            _UninstallStep("user-data-cleaned", self._cleanup_user_data),
        )

    def _schedule_self_cleanup(
        self,
        journal: UninstallJournal,
        progress: Callable[[str], None] | None,
    ) -> UninstallJournal:
        if "self-cleanup-scheduled" in journal.completed_steps:
            return journal
        self.self_cleanup.schedule(
            self.workspace,
            wait_pid=self.process_id,
        )
        return self._advance_and_report(
            journal,
            "self-cleanup-scheduled",
            progress,
        )

    def _run_mutating(
        self,
        journal: UninstallJournal,
        progress: Callable[[str], None] | None,
    ) -> UninstallOutcome:
        locks = _OwnedLocks()
        try:
            self._acquire_owned_locks(locks)
            completed = set(journal.completed_steps)
            for step in self._mutation_steps():
                if step.name in completed:
                    continue
                step.action()
                journal = self._advance_and_report(journal, step.name, progress)
                completed.add(step.name)
            self._release_owned_locks(locks)
            journal = self._schedule_self_cleanup(journal, progress)
        except Exception as exc:
            self._report_progress(progress, "failed")
            release_errors = self._release_remaining_locks(locks)
            if release_errors:
                exc = UninstallServiceError(f"{exc}; {'; '.join(release_errors)}")
            return self._fail(journal, exc)
        self._report_progress(progress, "completed")
        return self._outcome(
            journal,
            "completed",
            "Independent uninstall completed and was verified.",
        )

    def run(
        self,
        progress: Callable[[str], None] | None = None,
    ) -> UninstallOutcome:
        self._report_progress(progress, "starting")
        try:
            journal = self._initial_journal()
        except Exception as exc:
            self._report_progress(progress, "failed")
            fallback = UninstallJournal.create(self.request)
            return self._outcome(
                fallback,
                "partial",
                f"Cannot initialize uninstall journal: {exc}"[:500],
            )
        if journal.status == "completed":
            self._report_progress(progress, "completed")
            return self._outcome(
                journal,
                "completed",
                "Uninstall was already completed and verified.",
            )
        try:
            journal = self._prepare_journal(journal)
        except Exception as exc:
            self._report_progress(progress, "failed")
            return self._fail(journal, exc)
        return self._run_mutating(journal, progress)
