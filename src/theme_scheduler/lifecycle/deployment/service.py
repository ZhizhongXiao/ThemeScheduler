"""Exact-tree transactional deployment orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from ...persistence import atomic_write_json
from .._validation import InstallContractError
from ..installation import InstallationRecord, InstallationRecordStore
from ..layout import InstallLayout
from ..payload import PayloadManifest
from ..transaction import LifecycleTransaction, LifecycleTransactionStore
from ._shared import (
    _COMPONENTS,
    DeploymentError,
    _aware_timestamp,
    _is_reparse_point,
    _tree_evidence,
    _validate_direct_name,
    new_lifecycle_transaction_id,
    verify_active_payload,
)
from .filesystem import DeploymentFileSystem, LocalDeploymentFileSystem
from .journal import DeploymentJournal, DeploymentJournalStore


@dataclass(frozen=True)
class DeploymentOutcome:
    transaction_id: str
    status: str
    operation: str
    from_version: str | None
    to_version: str
    program_root: Path
    staging: Path
    rollback: Path
    active_verified: bool
    rollback_attempted: bool
    rollback_succeeded: bool | None
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "transactionId": self.transaction_id,
            "status": self.status,
            "operation": self.operation,
            "fromVersion": self.from_version,
            "toVersion": self.to_version,
            "programRoot": str(self.program_root),
            "staging": str(self.staging),
            "rollback": str(self.rollback),
            "activeVerified": self.active_verified,
            "rollbackAttempted": self.rollback_attempted,
            "rollbackSucceeded": self.rollback_succeeded,
            "message": self.message,
            "windowsChanged": False,
            "userDataChanged": False,
        }


@dataclass(frozen=True)
class _DeploymentRequest:
    payload_root: Path
    manifest: PayloadManifest
    operation: str
    from_version: str | None
    transaction_id: str
    staging: Path
    rollback: Path
    started_at: str


type Checkpoint = Callable[[str], None]


class FileDeploymentService:
    """Deploy one exact payload without overlaying the existing installation."""

    def __init__(
        self,
        layout: InstallLayout,
        *,
        filesystem: DeploymentFileSystem | None = None,
        clock: Callable[[], datetime] | None = None,
        checkpoint: Checkpoint | None = None,
    ) -> None:
        self.layout = layout
        self.filesystem = filesystem or LocalDeploymentFileSystem()
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.checkpoint = checkpoint or (lambda _name: None)

    def _lifecycle_store(self, transaction_id: str) -> LifecycleTransactionStore:
        return LifecycleTransactionStore(self.layout.lifecycle_journal(transaction_id))

    def _deployment_store(self, transaction_id: str) -> DeploymentJournalStore:
        return DeploymentJournalStore(self.layout.deployment_journal(transaction_id))

    def _infrastructure_names(self, transaction_id: str) -> frozenset[str]:
        return frozenset(
            {
                self.layout.staging(transaction_id).name,
                self.layout.rollback(transaction_id).name,
                self.layout.lifecycle_journal(transaction_id).name,
                self.layout.deployment_journal(transaction_id).name,
            }
        )

    def _old_entries(self, transaction_id: str) -> tuple[str, ...]:
        excluded = self._infrastructure_names(transaction_id)
        entries: list[str] = []
        for candidate in self.layout.program_root.iterdir():
            if candidate.name in excluded:
                continue
            _validate_direct_name(candidate.name)
            if _is_reparse_point(candidate):
                raise DeploymentError(
                    f"Program root contains a reparse point: {candidate}"
                )
            entries.append(candidate.name)
        return tuple(sorted(entries, key=str.casefold))

    def _ensure_no_unfinished_transaction(self) -> None:
        if not self.layout.program_root.exists():
            return
        for candidate in self.layout.program_root.iterdir():
            name = candidate.name
            if name.startswith((".staging-lifecycle-", ".rollback-lifecycle-")):
                raise DeploymentError(
                    f"Unfinished transaction directory requires recovery: {candidate}"
                )
            if (
                name.startswith(".lifecycle-")
                and name.endswith(".json")
                and not name.endswith(".deployment.json")
            ):
                try:
                    lifecycle = LifecycleTransactionStore(candidate).load()
                except Exception as exc:
                    raise DeploymentError(
                        f"Lifecycle evidence is untrusted: {candidate}"
                    ) from exc
                if lifecycle.status not in {
                    "completed",
                    "rolled-back",
                    "failed",
                }:
                    raise DeploymentError(
                        "Unfinished lifecycle transaction requires recovery: "
                        f"{lifecycle.transaction_id}"
                    )

    def _old_tree_evidence(
        self,
        transaction_id: str,
    ) -> tuple[str, int]:
        return _tree_evidence(
            self.layout.program_root,
            excluded_names=self._infrastructure_names(transaction_id),
        )

    def _prepare_metadata(
        self,
        staging: Path,
        manifest: PayloadManifest,
        *,
        operation: str,
        transaction_id: str,
        installed_at: str,
    ) -> InstallationRecord:
        metadata = staging / "metadata"
        metadata.mkdir()
        atomic_write_json(
            metadata / "payload-manifest.json",
            manifest.as_dict(),
        )
        record = InstallationRecord.create(
            self.layout,
            manifest,
            installed_at=installed_at,
            operation=operation,
            transaction_id=transaction_id,
        )
        InstallationRecordStore(metadata / "installation.json").create(record)
        return record

    def _validate_deployment_request(
        self,
        payload_root: Path,
        manifest: PayloadManifest,
        *,
        operation: str,
        from_version: str | None,
        transaction_id: str | None,
    ) -> _DeploymentRequest:
        if operation not in {"install", "upgrade", "reinstall"}:
            raise DeploymentError("File deployment operation is invalid.")
        if operation == "install" and from_version is not None:
            raise DeploymentError("Install cannot specify from_version.")
        if operation in {"upgrade", "reinstall"} and from_version is None:
            raise DeploymentError(f"{operation} requires from_version.")
        if operation == "reinstall" and from_version != manifest.version:
            raise DeploymentError(
                "Reinstall requires the existing and payload versions to match."
            )
        resolved_id = transaction_id or new_lifecycle_transaction_id(self.clock)
        staging = self.layout.staging(resolved_id)
        rollback = self.layout.rollback(resolved_id)
        source = Path(payload_root).resolve(strict=True)
        self._validate_deployment_roots(source)
        manifest.verify_tree(source)
        self._prepare_program_root()
        self._ensure_no_unfinished_transaction()
        self._validate_existing_installation(
            operation,
            from_version,
            resolved_id,
        )
        for path in (
            staging,
            rollback,
            self.layout.lifecycle_journal(resolved_id),
            self.layout.deployment_journal(resolved_id),
        ):
            if path.exists():
                raise DeploymentError(f"Transaction path already exists: {path}")
        return _DeploymentRequest(
            source,
            manifest,
            operation,
            from_version,
            resolved_id,
            staging,
            rollback,
            _aware_timestamp(self.clock),
        )

    def _validate_deployment_roots(self, payload_root: Path) -> None:
        for protected, label in (
            (self.layout.program_root, "program root"),
            (self.layout.data_root, "user-data root"),
        ):
            if (
                protected == payload_root
                or protected in payload_root.parents
                or payload_root in protected.parents
            ):
                raise DeploymentError(
                    f"Payload source and {label} must be independent."
                )

    def _prepare_program_root(self) -> None:
        self.layout.program_root.parent.mkdir(parents=True, exist_ok=True)
        if self.layout.program_root.exists():
            if not self.layout.program_root.is_dir() or _is_reparse_point(
                self.layout.program_root
            ):
                raise DeploymentError(
                    "Program root must be a regular non-reparse directory."
                )
            return
        self.layout.program_root.mkdir()

    def _validate_existing_installation(
        self,
        operation: str,
        from_version: str | None,
        transaction_id: str,
    ) -> None:
        preexisting_entries = self._old_entries(transaction_id)
        if operation == "install" and preexisting_entries:
            raise DeploymentError(
                "Fresh install requires an empty dedicated program root."
            )
        if (
            operation not in {"upgrade", "reinstall"}
            or not self.layout.installation_record.is_file()
        ):
            return
        try:
            trusted_record = InstallationRecordStore(
                self.layout.installation_record
            ).load()
        except (InstallContractError, OSError, ValueError):
            trusted_record = None
        if trusted_record is not None and trusted_record.version != from_version:
            raise DeploymentError(
                "from_version does not match the trusted installation record."
            )

    def _create_planned_deployment(
        self,
        request: _DeploymentRequest,
    ) -> tuple[
        LifecycleTransaction,
        LifecycleTransactionStore,
        DeploymentJournalStore,
    ]:
        lifecycle = LifecycleTransaction(
            transaction_id=request.transaction_id,
            operation=request.operation,
            status="planned",
            started_at=request.started_at,
            updated_at=request.started_at,
            program_root=str(self.layout.program_root),
            data_root=str(self.layout.data_root),
            from_version=request.from_version,
            to_version=request.manifest.version,
            payload_manifest_sha256=(request.manifest.document_sha256),
        )
        lifecycle_store = self._lifecycle_store(request.transaction_id)
        lifecycle_store.create(lifecycle)
        self.checkpoint("planned")
        return (
            lifecycle,
            lifecycle_store,
            self._deployment_store(request.transaction_id),
        )

    def _prepare_deployment(
        self,
        request: _DeploymentRequest,
        lifecycle: LifecycleTransaction,
        lifecycle_store: LifecycleTransactionStore,
        deployment_store: DeploymentJournalStore,
    ) -> tuple[LifecycleTransaction, DeploymentJournal]:
        self.filesystem.make_directory(request.staging)
        self.filesystem.copy_tree(
            request.payload_root,
            request.staging / "payload",
        )
        request.manifest.verify_tree(request.staging / "payload")
        self.checkpoint("payload-staged")
        self._prepare_metadata(
            request.staging,
            request.manifest,
            operation=request.operation,
            transaction_id=request.transaction_id,
            installed_at=request.started_at,
        )
        old_entries = self._old_entries(request.transaction_id)
        old_digest, old_count = self._old_tree_evidence(request.transaction_id)
        journal = deployment_store.create(
            DeploymentJournal(
                transaction_id=request.transaction_id,
                operation=request.operation,
                status="prepared",
                old_entries=old_entries,
                old_tree_sha256=old_digest,
                old_tree_entry_count=old_count,
            )
        )
        lifecycle = lifecycle_store.save(
            replace(
                lifecycle,
                status="prepared",
                updated_at=_aware_timestamp(self.clock),
            )
        )
        self.checkpoint("prepared")
        return lifecycle, journal

    def _begin_mutation(
        self,
        request: _DeploymentRequest,
        lifecycle: LifecycleTransaction,
        journal: DeploymentJournal,
        lifecycle_store: LifecycleTransactionStore,
        deployment_store: DeploymentJournalStore,
    ) -> tuple[LifecycleTransaction, DeploymentJournal]:
        lifecycle = lifecycle_store.save(
            replace(
                lifecycle,
                status="mutating",
                updated_at=_aware_timestamp(self.clock),
            )
        )
        journal = deployment_store.save(replace(journal, status="mutating"))
        self.filesystem.make_directory(request.rollback)
        self.checkpoint("mutating")
        return lifecycle, journal

    def _move_previous_tree(
        self,
        request: _DeploymentRequest,
        journal: DeploymentJournal,
        deployment_store: DeploymentJournalStore,
    ) -> DeploymentJournal:
        for name in journal.old_entries:
            source = self.layout.program_root / name
            target = request.rollback / name
            if source.exists():
                self.filesystem.move(source, target)
            moved = tuple(
                item
                for item in journal.old_entries
                if (request.rollback / item).exists()
            )
            journal = deployment_store.save(replace(journal, old_moved=moved))
            self.checkpoint(f"old-moved:{name}")
        return journal

    def _activate_new_tree(
        self,
        request: _DeploymentRequest,
        journal: DeploymentJournal,
        deployment_store: DeploymentJournalStore,
    ) -> DeploymentJournal:
        for component in _COMPONENTS:
            source = (
                request.staging / "payload" / component
                if component != "metadata"
                else request.staging / "metadata"
            )
            target = self.layout.program_root / component
            self.filesystem.move(source, target)
            activated = tuple(
                name
                for name in _COMPONENTS
                if (
                    request.staging / "payload" / name
                    if name != "metadata"
                    else request.staging / "metadata"
                ).exists()
                is False
                and (self.layout.program_root / name).exists()
            )
            journal = deployment_store.save(replace(journal, new_activated=activated))
            self.checkpoint(f"new-activated:{component}")
        return journal

    def _commit_deployment(
        self,
        request: _DeploymentRequest,
        lifecycle: LifecycleTransaction,
        journal: DeploymentJournal,
        lifecycle_store: LifecycleTransactionStore,
        deployment_store: DeploymentJournalStore,
    ) -> tuple[LifecycleTransaction, DeploymentJournal]:
        verify_active_payload(self.layout, request.manifest)
        lifecycle = lifecycle_store.save(
            replace(
                lifecycle,
                status="committed",
                updated_at=_aware_timestamp(self.clock),
            )
        )
        journal = deployment_store.save(replace(journal, status="committed"))
        self.checkpoint("committed")
        return lifecycle, journal

    def _complete_deployment(
        self,
        request: _DeploymentRequest,
        lifecycle: LifecycleTransaction,
        journal: DeploymentJournal,
        lifecycle_store: LifecycleTransactionStore,
        deployment_store: DeploymentJournalStore,
    ) -> DeploymentOutcome:
        self._cleanup_directory(request.staging)
        self._cleanup_directory(request.rollback)
        lifecycle = lifecycle_store.save(
            replace(
                lifecycle,
                status="completed",
                updated_at=_aware_timestamp(self.clock),
            )
        )
        deployment_store.save(replace(journal, status="completed"))
        self.checkpoint("completed")
        return self._outcome(
            lifecycle,
            request.manifest,
            active_verified=True,
            rollback_succeeded=None,
            message="Payload deployed and verified.",
        )

    def _mutate_deployment(
        self,
        request: _DeploymentRequest,
        lifecycle: LifecycleTransaction,
        journal: DeploymentJournal,
        lifecycle_store: LifecycleTransactionStore,
        deployment_store: DeploymentJournalStore,
        *,
        defer_completion: bool,
    ) -> DeploymentOutcome:
        try:
            lifecycle, journal = self._begin_mutation(
                request,
                lifecycle,
                journal,
                lifecycle_store,
                deployment_store,
            )
            journal = self._move_previous_tree(request, journal, deployment_store)
            journal = self._activate_new_tree(request, journal, deployment_store)
            lifecycle, journal = self._commit_deployment(
                request,
                lifecycle,
                journal,
                lifecycle_store,
                deployment_store,
            )
            if defer_completion:
                return self._outcome(
                    lifecycle,
                    request.manifest,
                    active_verified=True,
                    rollback_succeeded=None,
                    message=(
                        "Payload committed and verified; completion is "
                        "deferred until the install coordinator finishes."
                    ),
                )
            return self._complete_deployment(
                request,
                lifecycle,
                journal,
                lifecycle_store,
                deployment_store,
            )
        except Exception as exc:
            return self._handle_mutation_failure(
                request,
                lifecycle,
                journal,
                lifecycle_store,
                deployment_store,
                exc,
            )

    def _handle_mutation_failure(
        self,
        request: _DeploymentRequest,
        lifecycle: LifecycleTransaction,
        journal: DeploymentJournal,
        lifecycle_store: LifecycleTransactionStore,
        deployment_store: DeploymentJournalStore,
        failure: Exception,
    ) -> DeploymentOutcome:
        # A helper may durably advance a journal before its checkpoint raises.
        # Reload the latest facts so rollback cannot move progress backwards.
        try:  # noqa: SIM105 - A failed journal reload must not replace the mutation failure.
            lifecycle = lifecycle_store.load()
        except Exception:
            pass
        try:  # noqa: SIM105 - A failed journal reload must not replace the mutation failure.
            journal = deployment_store.load()
        except Exception:
            pass
        if lifecycle.status == "committed":
            return self._resume_committed(
                lifecycle_store,
                deployment_store,
                lifecycle,
                journal,
                request.manifest,
                failure=failure,
            )
        return self._rollback(
            lifecycle_store,
            deployment_store,
            lifecycle,
            journal,
            request.manifest,
            failure,
        )

    def deploy(
        self,
        payload_root: Path,
        manifest: PayloadManifest,
        *,
        operation: str,
        from_version: str | None,
        transaction_id: str | None = None,
        defer_completion: bool = False,
    ) -> DeploymentOutcome:
        request = self._validate_deployment_request(
            payload_root,
            manifest,
            operation=operation,
            from_version=from_version,
            transaction_id=transaction_id,
        )
        lifecycle, lifecycle_store, deployment_store = self._create_planned_deployment(
            request
        )
        try:
            lifecycle, journal = self._prepare_deployment(
                request,
                lifecycle,
                lifecycle_store,
                deployment_store,
            )
        except Exception as exc:
            return self._fail_before_mutation(
                lifecycle_store,
                lifecycle,
                request.transaction_id,
                manifest,
                operation,
                from_version,
                exc,
            )
        return self._mutate_deployment(
            request,
            lifecycle,
            journal,
            lifecycle_store,
            deployment_store,
            defer_completion=defer_completion,
        )

    def recover(
        self,
        manifest: PayloadManifest,
        *,
        transaction_id: str,
    ) -> DeploymentOutcome:
        lifecycle_store = self._lifecycle_store(transaction_id)
        lifecycle = lifecycle_store.load()
        if lifecycle.payload_manifest_sha256 != manifest.document_sha256:
            raise DeploymentError(
                "Recovery manifest does not match the lifecycle transaction."
            )
        if lifecycle.status == "committed":
            journal_store = self._deployment_store(transaction_id)
            return self._resume_committed(
                lifecycle_store,
                journal_store,
                lifecycle,
                journal_store.load(),
                manifest,
            )
        if lifecycle.status in {"mutating", "partial"}:
            journal_store = self._deployment_store(transaction_id)
            return self._rollback(
                lifecycle_store,
                journal_store,
                lifecycle,
                journal_store.load(),
                manifest,
                DeploymentError("Interrupted deployment recovered."),
            )
        if lifecycle.status in {"planned", "prepared"}:
            return self._fail_before_mutation(
                lifecycle_store,
                lifecycle,
                transaction_id,
                manifest,
                lifecycle.operation,
                lifecycle.from_version,
                DeploymentError("Interrupted deployment stopped before mutation."),
            )
        active_verified = False
        if lifecycle.status == "completed":
            verify_active_payload(self.layout, manifest)
            active_verified = True
        return self._outcome(
            lifecycle,
            manifest,
            active_verified=active_verified,
            rollback_succeeded=lifecycle.rollback_succeeded,
            message=f"Lifecycle transaction is already {lifecycle.status}.",
        )

    def complete_committed(
        self,
        manifest: PayloadManifest,
        *,
        transaction_id: str,
    ) -> DeploymentOutcome:
        """Finalize a verified payload after higher-level install checks."""

        lifecycle_store = self._lifecycle_store(transaction_id)
        lifecycle = lifecycle_store.load()
        if lifecycle.status != "committed":
            raise DeploymentError("Only a committed deployment can be completed.")
        if lifecycle.payload_manifest_sha256 != manifest.document_sha256:
            raise DeploymentError(
                "Completion manifest does not match the lifecycle transaction."
            )
        deployment_store = self._deployment_store(transaction_id)
        return self._resume_committed(
            lifecycle_store,
            deployment_store,
            lifecycle,
            deployment_store.load(),
            manifest,
        )

    def rollback_committed(
        self,
        manifest: PayloadManifest,
        *,
        transaction_id: str,
        reason: str,
    ) -> DeploymentOutcome:
        """Restore the exact old tree after a later install step fails."""

        if not isinstance(reason, str) or not reason:
            raise DeploymentError("Rollback reason must be non-empty.")
        lifecycle_store = self._lifecycle_store(transaction_id)
        lifecycle = lifecycle_store.load()
        if lifecycle.status != "committed":
            raise DeploymentError("Only a committed deployment can be rolled back.")
        if lifecycle.payload_manifest_sha256 != manifest.document_sha256:
            raise DeploymentError(
                "Rollback manifest does not match the lifecycle transaction."
            )
        deployment_store = self._deployment_store(transaction_id)
        return self._rollback(
            lifecycle_store,
            deployment_store,
            lifecycle,
            deployment_store.load(),
            manifest,
            DeploymentError(reason),
        )

    def _fail_before_mutation(
        self,
        lifecycle_store: LifecycleTransactionStore,
        lifecycle: LifecycleTransaction,
        transaction_id: str,
        manifest: PayloadManifest,
        operation: str,
        from_version: str | None,
        failure: Exception,
    ) -> DeploymentOutcome:
        self._cleanup_directory(self.layout.staging(transaction_id))
        self._cleanup_directory(self.layout.rollback(transaction_id))
        if lifecycle.status in {"planned", "prepared"}:
            lifecycle = lifecycle_store.save(
                replace(
                    lifecycle,
                    status="failed",
                    updated_at=_aware_timestamp(self.clock),
                    error_code="deployment.prepare-failed",
                    message=str(failure)[:500] or type(failure).__name__,
                    rollback_succeeded=None,
                )
            )
        deployment_path = self.layout.deployment_journal(transaction_id)
        if deployment_path.is_file():
            store = DeploymentJournalStore(deployment_path)
            journal = store.load()
            store.save(
                replace(
                    journal,
                    status="failed",
                    error_code="deployment.prepare-failed",
                    message=str(failure)[:500] or type(failure).__name__,
                )
            )
        return self._outcome(
            lifecycle,
            manifest,
            active_verified=False,
            rollback_succeeded=None,
            message=f"Deployment preparation failed: {failure}",
        )

    def _resume_committed(
        self,
        lifecycle_store: LifecycleTransactionStore,
        deployment_store: DeploymentJournalStore,
        lifecycle: LifecycleTransaction,
        journal: DeploymentJournal,
        manifest: PayloadManifest,
        *,
        failure: Exception | None = None,
    ) -> DeploymentOutcome:
        try:
            verify_active_payload(self.layout, manifest)
            self._cleanup_directory(self.layout.staging(lifecycle.transaction_id))
            self._cleanup_directory(self.layout.rollback(lifecycle.transaction_id))
            lifecycle = lifecycle_store.save(
                replace(
                    lifecycle,
                    status="completed",
                    updated_at=_aware_timestamp(self.clock),
                )
            )
            deployment_store.save(replace(journal, status="completed"))
            return self._outcome(
                lifecycle,
                manifest,
                active_verified=True,
                rollback_succeeded=None,
                message=(
                    "Recovered and completed a committed deployment."
                    if failure is not None
                    else "Committed deployment cleanup completed."
                ),
            )
        except Exception as exc:
            return self._rollback(
                lifecycle_store,
                deployment_store,
                lifecycle,
                journal,
                manifest,
                exc,
            )

    def _rollback(
        self,
        lifecycle_store: LifecycleTransactionStore,
        deployment_store: DeploymentJournalStore,
        lifecycle: LifecycleTransaction,
        journal: DeploymentJournal,
        manifest: PayloadManifest,
        failure: Exception,
    ) -> DeploymentOutcome:
        staging = self.layout.staging(lifecycle.transaction_id)
        rollback = self.layout.rollback(lifecycle.transaction_id)
        try:
            failed_active = staging / "failed-active"
            failed_active.mkdir(parents=True, exist_ok=True)
            for component in reversed(_COMPONENTS):
                active = self.layout.program_root / component
                old = rollback / component
                staged_source = (
                    staging / "payload" / component
                    if component != "metadata"
                    else staging / "metadata"
                )
                if active.exists() and (old.exists() or not staged_source.exists()):
                    target = failed_active / component
                    if target.exists():
                        raise DeploymentError(
                            f"Rollback evidence target already exists: {target}"
                        )
                    self.filesystem.move(active, target)

            for name in journal.old_entries:
                old = rollback / name
                active = self.layout.program_root / name
                if old.exists():
                    if active.exists():
                        raise DeploymentError(
                            f"Rollback target already exists: {active}"
                        )
                    self.filesystem.move(old, active)
                elif not active.exists():
                    raise DeploymentError(f"Rollback lost original entry: {name}")

            digest, count = self._old_tree_evidence(lifecycle.transaction_id)
            if (
                digest != journal.old_tree_sha256
                or count != journal.old_tree_entry_count
            ):
                raise DeploymentError(
                    "Restored program tree does not match pre-deployment evidence."
                )
            self._cleanup_directory(staging)
            self._cleanup_directory(rollback)
            lifecycle = lifecycle_store.save(
                replace(
                    lifecycle,
                    status="rolled-back",
                    updated_at=_aware_timestamp(self.clock),
                    error_code="deployment.failed",
                    message=str(failure)[:500] or type(failure).__name__,
                    rollback_succeeded=True,
                )
            )
            deployment_store.save(
                replace(
                    journal,
                    status="rolled-back",
                    error_code="deployment.failed",
                    message=str(failure)[:500] or type(failure).__name__,
                )
            )
            return self._outcome(
                lifecycle,
                manifest,
                active_verified=False,
                rollback_succeeded=True,
                message=f"Deployment failed and was rolled back: {failure}",
            )
        except Exception as rollback_error:
            status = "partial"
            lifecycle = lifecycle_store.save(
                replace(
                    lifecycle,
                    status=status,
                    updated_at=_aware_timestamp(self.clock),
                    error_code="deployment.rollback-failed",
                    message=(f"{failure}; rollback failed: {rollback_error}")[:500],
                    rollback_succeeded=False,
                )
            )
            deployment_store.save(
                replace(
                    journal,
                    status=status,
                    error_code="deployment.rollback-failed",
                    message=(f"{failure}; rollback failed: {rollback_error}")[:500],
                )
            )
            return self._outcome(
                lifecycle,
                manifest,
                active_verified=False,
                rollback_succeeded=False,
                message=(f"Deployment and rollback failed: {rollback_error}"),
            )

    def _cleanup_directory(self, path: Path) -> None:
        path = Path(path)
        if not path.exists():
            return
        self.layout.validate_owned_target(path)
        if not path.is_dir() or _is_reparse_point(path):
            raise DeploymentError(f"Refusing to clean unsafe transaction path: {path}")
        self.filesystem.remove_tree(path)

    def _outcome(
        self,
        lifecycle: LifecycleTransaction,
        manifest: PayloadManifest,
        *,
        active_verified: bool,
        rollback_succeeded: bool | None,
        message: str,
    ) -> DeploymentOutcome:
        return DeploymentOutcome(
            transaction_id=lifecycle.transaction_id,
            status=lifecycle.status,
            operation=lifecycle.operation,
            from_version=lifecycle.from_version,
            to_version=manifest.version,
            program_root=self.layout.program_root,
            staging=self.layout.staging(lifecycle.transaction_id),
            rollback=self.layout.rollback(lifecycle.transaction_id),
            active_verified=active_verified,
            rollback_attempted=(
                lifecycle.status in {"rolled-back", "partial"}
                or rollback_succeeded is not None
            ),
            rollback_succeeded=rollback_succeeded,
            message=message,
        )
