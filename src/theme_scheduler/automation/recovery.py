"""Recovery of interrupted automatic theme transactions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

from ..accent_theme import theme_visual_state_from_dict
from ..auto_transaction import (
    AUTO_TRANSACTION_FILE_NAME,
    AutoTransaction,
    AutoTransactionError,
    AutoTransactionStore,
    file_sha256,
)
from ..core import AutoResultKind
from ..persistence import captured_at, load_json_object
from .contracts import AutoRunnerBindings
from .outcome import AutoRunOutcome


def _string_key_mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    unknown_mapping = cast(Mapping[object, object], value)
    if not all(isinstance(key, str) for key in unknown_mapping):
        return None
    return cast(Mapping[str, object], unknown_mapping)


class AutoRecoveryMixin(AutoRunnerBindings):
    def _pending_transaction(
        self,
    ) -> tuple[Path, AutoTransactionStore, AutoTransaction] | None:
        pending: list[tuple[Path, AutoTransactionStore, AutoTransaction]] = []
        if not self.layout.runtime.exists():
            return None
        for directory in self.layout.runtime.iterdir():
            if not directory.is_dir() or not directory.name.startswith("accent-"):
                continue
            path = directory / AUTO_TRANSACTION_FILE_NAME
            if not path.exists():
                continue
            store = AutoTransactionStore(path)
            transaction = store.load()
            if transaction.transaction_id != directory.name:
                raise AutoTransactionError(
                    "Automatic transaction directory identity mismatch."
                )
            if transaction.status in {
                "planned",
                "windows-verified",
                "state-committed",
                "partial",
            }:
                pending.append((directory, store, transaction))
        if len(pending) > 1:
            raise AutoTransactionError(
                "Multiple unfinished automatic transactions require repair."
            )
        return pending[0] if pending else None

    def _recover_pending(self) -> AutoRunOutcome | None:
        try:
            pending = self._pending_transaction()
        except Exception as exc:
            return self._outcome(
                AutoResultKind.DATA_UNTRUSTED,
                f"Automatic transaction evidence is untrusted: {exc}",
            )
        if pending is None:
            return None
        return self._recover_transaction(*pending)

    def _recover_transaction(
        self,
        directory: Path,
        store: AutoTransactionStore,
        transaction: AutoTransaction,
    ) -> AutoRunOutcome | None:
        if transaction.status == "planned":
            resolved = self._resolve_planned_interruption(directory, store, transaction)
            if not isinstance(resolved, AutoTransaction):
                return resolved
            transaction = resolved

        if transaction.status == "partial":
            return self._recover_partial(directory, store, transaction)
        if transaction.status == "windows-verified":
            return self._recover_windows_verified(directory, store, transaction)
        if transaction.status == "state-committed":
            return self._recover_state_committed(
                directory,
                store,
                transaction,
                state_changed=False,
            )
        return None

    def _recover_partial(
        self,
        directory: Path,
        store: AutoTransactionStore,
        transaction: AutoTransaction,
    ) -> AutoRunOutcome:
        if transaction.error_code not in {
            "cleanup.failed",
            "log.write-failed",
            "transaction.complete-failed",
        }:
            return self._outcome(
                AutoResultKind.DATA_UNTRUSTED,
                "A partial automatic transaction requires explicit repair.",
                target=transaction.target_profile,
                transaction=directory,
            )
        try:
            if file_sha256(self.layout.state) != transaction.state_after_sha256:
                raise AutoTransactionError(
                    "Committed state hash no longer matches the transaction."
                )
            if transaction.error_code == "cleanup.failed":
                self.cleanup_callback(directory)
            self._append_recovery_event(
                transaction,
                "Recovered a post-commit automatic transaction.",
            )
            store.save(
                replace(
                    transaction,
                    status="completed",
                    updated_at=captured_at(),
                    error_code=None,
                    message=None,
                    rollback_succeeded=None,
                )
            )
        except Exception as exc:
            return self._outcome(
                AutoResultKind.PARTIAL_FAILURE,
                f"Post-commit recovery failed: {exc}",
                target=transaction.target_profile,
                transaction=directory,
                recovered=True,
            )
        return self._outcome(
            AutoResultKind.APPLIED,
            "Recovered a previously committed automatic transaction.",
            target=transaction.target_profile,
            transaction=directory,
            recovered=True,
        )

    def _recover_windows_verified(
        self,
        directory: Path,
        store: AutoTransactionStore,
        transaction: AutoTransaction,
    ) -> AutoRunOutcome:
        try:
            if transaction.windows_target is None:
                raise AutoTransactionError(
                    "Windows-verified transaction has no windowsTarget."
                )
            failures = self.windows.verify_transaction_target(
                directory,
                transaction.windows_target,
            )
            if failures:
                raise AutoTransactionError(failures[0])
            current_state_hash = file_sha256(self.layout.state)
            state_changed = False
            if current_state_hash == transaction.state_before_sha256:
                self.state_store.save(transaction.state_after)
                state_changed = True
            elif current_state_hash != transaction.state_after_sha256:
                raise AutoTransactionError(
                    "State hash matches neither transaction boundary."
                )
            transaction = store.save(
                replace(
                    transaction,
                    status="state-committed",
                    updated_at=captured_at(),
                )
            )
        except Exception as exc:
            return self._outcome(
                AutoResultKind.DATA_UNTRUSTED,
                f"Windows-verified recovery is unsafe: {exc}",
                target=transaction.target_profile,
                transaction=directory,
                recovered=True,
            )
        return self._recover_state_committed(
            directory,
            store,
            transaction,
            state_changed=state_changed,
        )

    def _recover_state_committed(
        self,
        directory: Path,
        store: AutoTransactionStore,
        transaction: AutoTransaction,
        *,
        state_changed: bool,
    ) -> AutoRunOutcome:
        try:
            if file_sha256(self.layout.state) != transaction.state_after_sha256:
                raise AutoTransactionError(
                    "Committed state hash does not match stateAfter."
                )
        except Exception as exc:
            return self._outcome(
                AutoResultKind.DATA_UNTRUSTED,
                f"State-committed recovery is unsafe: {exc}",
                target=transaction.target_profile,
                transaction=directory,
                state_changed=state_changed,
                recovered=True,
            )
        try:
            self._append_recovery_event(
                transaction,
                "Completed an interrupted automatic transaction.",
            )
        except Exception as exc:
            marker_error = self._try_mark_partial(
                store,
                transaction,
                error_code="log.write-failed",
                error=exc,
            )
            return self._outcome(
                AutoResultKind.PARTIAL_FAILURE,
                f"Recovery logging failed: {exc}{marker_error}",
                target=transaction.target_profile,
                transaction=directory,
                state_changed=state_changed,
                recovered=True,
            )
        try:
            store.save(
                replace(
                    transaction,
                    status="completed",
                    updated_at=captured_at(),
                )
            )
        except Exception as exc:
            marker_error = self._try_mark_partial(
                store,
                transaction,
                error_code="transaction.complete-failed",
                error=exc,
            )
            return self._outcome(
                AutoResultKind.PARTIAL_FAILURE,
                f"State-committed recovery failed: {exc}{marker_error}",
                target=transaction.target_profile,
                transaction=directory,
                state_changed=state_changed,
                recovered=True,
            )
        return self._outcome(
            AutoResultKind.APPLIED,
            "Recovered and completed an interrupted automatic transaction.",
            target=transaction.target_profile,
            transaction=directory,
            state_changed=state_changed,
            recovered=True,
        )

    def _append_recovery_event(
        self,
        transaction: AutoTransaction,
        message: str,
    ) -> None:
        self._append_event(
            timestamp=captured_at(),
            level="WARNING",
            event="auto.recovered",
            result="success",
            target=transaction.target_profile,
            transaction=transaction.transaction_id,
            message=message,
        )

    @staticmethod
    def _try_mark_partial(
        store: AutoTransactionStore,
        transaction: AutoTransaction,
        *,
        error_code: str,
        error: Exception,
    ) -> str:
        try:
            store.save(
                replace(
                    transaction,
                    status="partial",
                    updated_at=captured_at(),
                    error_code=error_code,
                    message=f"{type(error).__name__}: {error}"[:500],
                )
            )
        except Exception as marker_exc:
            return (
                "; transaction marker failed: "
                f"{type(marker_exc).__name__}: {marker_exc}"
            )
        return ""

    def _resolve_planned_interruption(
        self,
        directory: Path,
        store: AutoTransactionStore,
        transaction: AutoTransaction,
    ) -> AutoTransaction | AutoRunOutcome | None:
        accent_path = directory / "journal.json"
        if not accent_path.exists():
            store.save(
                replace(
                    transaction,
                    status="failed",
                    updated_at=captured_at(),
                    error_code="interrupted.before-windows",
                    message="Run stopped before the Windows transaction started.",
                )
            )
            return None
        try:
            accent = load_json_object(accent_path)
            if accent.get("kind") != "themescheduler.accent-transaction":
                raise AutoTransactionError("Sibling accent journal kind is invalid.")
            target_payload = _string_key_mapping(
                accent.get("actual") or accent.get("target")
            )
            before_payload = _string_key_mapping(accent.get("before"))
            if target_payload is None or before_payload is None:
                raise AutoTransactionError(
                    "Sibling accent journal has no visual boundaries."
                )
            target = theme_visual_state_from_dict(target_payload)
            before = theme_visual_state_from_dict(before_payload)
            current = self.windows.read_visual_state()
            target_failures = self.windows.verify_transaction_target(
                directory,
                target,
            )
            if current == target and not target_failures:
                return store.save(
                    replace(
                        transaction,
                        status="windows-verified",
                        updated_at=captured_at(),
                        windows_target=target,
                    )
                )
            if current == before and (accent.get("status") in {"prepared", "failed"}):
                store.save(
                    replace(
                        transaction,
                        status="failed",
                        updated_at=captured_at(),
                        error_code="interrupted.rolled-back",
                        message="Interrupted Windows transaction is at its before state.",
                        rollback_succeeded=True,
                    )
                )
                return None
            partial = store.save(
                replace(
                    transaction,
                    status="partial",
                    updated_at=captured_at(),
                    windows_target=target,
                    error_code="interrupted.ambiguous-windows",
                    message="Interrupted Windows state matches neither boundary.",
                    rollback_succeeded=False,
                )
            )
            return self._outcome(
                AutoResultKind.DATA_UNTRUSTED,
                partial.message or "Interrupted Windows state is ambiguous.",
                target=transaction.target_profile,
                transaction=directory,
                recovered=True,
            )
        except Exception as exc:
            return self._outcome(
                AutoResultKind.DATA_UNTRUSTED,
                f"Interrupted transaction evidence is untrusted: {exc}",
                target=transaction.target_profile,
                transaction=directory,
                recovered=True,
            )
