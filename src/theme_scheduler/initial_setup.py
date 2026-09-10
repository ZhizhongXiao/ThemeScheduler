"""Explicit transient marker for an unfinished first-run setup."""

from __future__ import annotations

from pathlib import Path

from .errors import ThemeSchedulerRuntimeError
from .persistence import atomic_write_json, load_json_object

INITIAL_SETUP_KIND = "themescheduler.initial-setup"
INITIAL_SETUP_SCHEMA_VERSION = 1


class InitialSetupMarkerError(ThemeSchedulerRuntimeError):
    """Raised when the first-run marker cannot be trusted or committed."""


def _validate(path: Path) -> None:
    try:
        payload = load_json_object(path)
    except Exception as exc:
        raise InitialSetupMarkerError("Initial-setup marker is unreadable.") from exc
    if set(payload) != {"kind", "schemaVersion"}:
        raise InitialSetupMarkerError(
            "Initial-setup marker fields do not match schema."
        )
    if payload.get("kind") != INITIAL_SETUP_KIND:
        raise InitialSetupMarkerError("Initial-setup marker kind is invalid.")
    if payload.get("schemaVersion") != INITIAL_SETUP_SCHEMA_VERSION:
        raise InitialSetupMarkerError(
            "Initial-setup marker schemaVersion is unsupported."
        )


def create_initial_setup_marker(path: Path) -> None:
    candidate = Path(path)
    atomic_write_json(
        candidate,
        {
            "kind": INITIAL_SETUP_KIND,
            "schemaVersion": INITIAL_SETUP_SCHEMA_VERSION,
        },
    )
    _validate(candidate)


def initial_setup_pending(path: Path) -> bool:
    candidate = Path(path)
    if not candidate.exists():
        return False
    if not candidate.is_file() or candidate.is_symlink():
        raise InitialSetupMarkerError("Initial-setup marker is not a regular file.")
    _validate(candidate)
    return True


def clear_initial_setup_marker(path: Path) -> bool:
    candidate = Path(path)
    if not initial_setup_pending(candidate):
        return False
    try:
        candidate.unlink()
    except OSError as exc:
        raise InitialSetupMarkerError(
            "Initial-setup marker could not be removed."
        ) from exc
    if candidate.exists():
        raise InitialSetupMarkerError(
            "Initial-setup marker removal could not be verified."
        )
    return True
