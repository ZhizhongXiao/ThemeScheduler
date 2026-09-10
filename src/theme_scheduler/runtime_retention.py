"""Plan and execute bounded cleanup of stage-2/3 accent transactions."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .log_policy import (
    FAILED_TRANSACTION_MINIMUM_KEEP,
    FAILED_TRANSACTION_RETENTION_DAYS,
    SUCCESSFUL_TRANSACTION_KEEP,
)
from .persistence import load_json_object


@dataclass(frozen=True)
class RuntimeCleanupPlan:
    delete: tuple[Path, ...]
    keep: tuple[Path, ...]


def _timestamp(directory: Path, payload: dict[str, object] | None) -> datetime:
    if payload is not None:
        for field in ("completedAt", "preparedAt"):
            raw = payload.get(field)
            if isinstance(raw, str):
                try:
                    parsed = datetime.fromisoformat(raw)
                except ValueError:
                    continue
                if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                    return parsed
    return datetime.fromtimestamp(directory.stat().st_mtime).astimezone()


def _classify(directory: Path) -> tuple[str, datetime]:
    try:
        payload = load_json_object(directory / "journal.json")
    except (OSError, ValueError):
        return "failed", _timestamp(directory, None)
    status = payload.get("status")
    category = "success" if status == "applied" else "failed"
    return category, _timestamp(directory, payload)


def plan_runtime_cleanup(
    runtime_root: Path,
    *,
    now: datetime | None = None,
    protected: tuple[Path, ...] = (),
) -> RuntimeCleanupPlan:
    """Return a deterministic plan; no files are changed."""

    root = Path(runtime_root).resolve()
    if not root.exists():
        return RuntimeCleanupPlan((), ())
    if not root.is_dir():
        raise NotADirectoryError(root)
    reference_time = now or datetime.now().astimezone()
    if reference_time.tzinfo is None or reference_time.utcoffset() is None:
        raise ValueError("Runtime cleanup reference time must include a UTC offset.")
    protected_resolved = {Path(path).resolve() for path in protected}
    entries: list[tuple[Path, str, datetime]] = []
    for child in root.iterdir():
        resolved_child = child.resolve()
        if (
            child.is_dir()
            and child.name.startswith("accent-")
            and resolved_child.parent == root
            and resolved_child not in protected_resolved
        ):
            category, stamp = _classify(child)
            entries.append((child, category, stamp))

    successes = sorted(
        (entry for entry in entries if entry[1] == "success"),
        key=lambda entry: (entry[2], entry[0].name),
        reverse=True,
    )
    failures = sorted(
        (entry for entry in entries if entry[1] == "failed"),
        key=lambda entry: (entry[2], entry[0].name),
        reverse=True,
    )
    keep: set[Path] = set(protected_resolved)
    delete: set[Path] = set()

    for index, (path, _, _) in enumerate(successes):
        (keep if index < SUCCESSFUL_TRANSACTION_KEEP else delete).add(path.resolve())

    cutoff = reference_time - timedelta(days=FAILED_TRANSACTION_RETENTION_DAYS)
    for index, (path, _, stamp) in enumerate(failures):
        resolved = path.resolve()
        if index < FAILED_TRANSACTION_MINIMUM_KEEP or stamp >= cutoff:
            keep.add(resolved)
        else:
            delete.add(resolved)

    return RuntimeCleanupPlan(
        tuple(sorted(delete, key=lambda path: path.name)),
        tuple(sorted(keep, key=lambda path: path.name)),
    )


def execute_runtime_cleanup(
    runtime_root: Path, plan: RuntimeCleanupPlan
) -> tuple[Path, ...]:
    """Delete only validated direct accent transaction directories."""

    root = Path(runtime_root).resolve()
    removed: list[Path] = []
    for candidate in plan.delete:
        resolved = Path(candidate).resolve()
        if (
            resolved.parent != root
            or not resolved.name.startswith("accent-")
            or resolved in plan.keep
        ):
            raise ValueError(f"Unsafe runtime cleanup target: {resolved}")
        if resolved.exists():
            shutil.rmtree(resolved)
            removed.append(resolved)
    return tuple(removed)
