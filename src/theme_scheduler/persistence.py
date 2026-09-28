"""Shared, side-effect-free persistence primitives.

This module is the only owner of generic JSON loading, timestamps, and atomic
JSON replacement.  Product code must not depend on diagnostics for storage.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from contextlib import suppress
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import DataError, ThemeSchedulerRuntimeError


class PersistenceError(ThemeSchedulerRuntimeError):
    """Base error for durable document operations."""


class JsonDocumentError(DataError):
    """Raised when a JSON document cannot be decoded as an object."""


class MigrationError(DataError):
    """Raised when a versioned document cannot be migrated explicitly."""


Migrator = Callable[[Mapping[str, Any]], Mapping[str, Any]]
DocumentValidator = Callable[[Mapping[str, Any]], Any]


def captured_at() -> str:
    """Return a local ISO 8601 timestamp with an explicit UTC offset."""

    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_json_object(path: Path) -> dict[str, Any]:
    """Load one UTF-8 JSON object without applying schema-specific defaults."""

    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise JsonDocumentError(
            f"Invalid JSON in {path}: line {exc.lineno}, column {exc.colno}."
        ) from exc
    if not isinstance(payload, dict):
        raise JsonDocumentError(f"JSON root must be an object: {path}")
    return payload


def atomic_write_json(
    path: Path, payload: Mapping[str, Any], *, force: bool = False
) -> None:
    """Write UTF-8 JSON atomically and refuse overwrites unless requested."""

    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file: {path}")

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    except Exception:
        if temporary_name is not None:
            with suppress(OSError):
                Path(temporary_name).unlink(missing_ok=True)
        raise


def atomic_write_bytes(path: Path, content: bytes, *, force: bool = False) -> None:
    """Write bytes with the same no-overwrite and atomic-replace guarantees."""

    if not isinstance(content, bytes):
        raise TypeError("Atomic byte content must be bytes.")
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file: {path}")

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    except Exception:
        if temporary_name is not None:
            with suppress(OSError):
                Path(temporary_name).unlink(missing_ok=True)
        raise


def migrate_json_object(
    payload: Mapping[str, Any],
    *,
    kind: str,
    current_version: int,
    migrations: Mapping[int, Migrator],
) -> dict[str, Any]:
    """Apply adjacent, explicitly registered migrations in memory."""

    if payload.get("kind") != kind:
        raise MigrationError(f"Cannot migrate document kind {payload.get('kind')!r}.")
    raw_version = payload.get("schemaVersion")
    if (
        isinstance(raw_version, bool)
        or not isinstance(raw_version, int)
        or raw_version < 1
    ):
        raise MigrationError("schemaVersion must be a positive integer.")
    version = raw_version
    if version > current_version:
        raise MigrationError(
            f"Document schemaVersion {version} is newer than supported "
            f"version {current_version}."
        )
    migrated: Mapping[str, Any] = deepcopy(dict(payload))
    while version < current_version:
        migrator = migrations.get(version)
        if migrator is None:
            raise MigrationError(f"No explicit migration from schemaVersion {version}.")
        candidate = migrator(deepcopy(dict(migrated)))
        if not isinstance(candidate, Mapping):
            raise MigrationError(
                f"Migration from schemaVersion {version} did not return an object."
            )
        next_version = candidate.get("schemaVersion")
        if (
            candidate.get("kind") != kind
            or isinstance(next_version, bool)
            or not isinstance(next_version, int)
            or next_version != version + 1
        ):
            raise MigrationError(
                f"Migration from schemaVersion {version} must preserve kind "
                f"and advance exactly one version."
            )
        migrated = candidate
        version = next_version
    return deepcopy(dict(migrated))


def migrate_json_file(
    path: Path,
    *,
    kind: str,
    current_version: int,
    migrations: Mapping[int, Migrator],
    validator: DocumentValidator,
) -> dict[str, Any]:
    """Back up an old document, validate its migration, then replace atomically."""

    path = Path(path)
    original = load_json_object(path)
    source_version = original.get("schemaVersion")
    migrated = migrate_json_object(
        original,
        kind=kind,
        current_version=current_version,
        migrations=migrations,
    )
    validator(migrated)
    if migrated == original:
        return migrated
    backup_path = path.with_name(f"{path.name}.v{source_version}.bak")
    try:
        atomic_write_json(backup_path, original)
    except FileExistsError:
        try:
            existing_backup = load_json_object(backup_path)
        except (OSError, JsonDocumentError) as exc:
            raise MigrationError(
                f"Existing migration backup cannot be verified: {backup_path}"
            ) from exc
        if existing_backup != original:
            raise MigrationError(
                f"Existing migration backup does not match the source document: "
                f"{backup_path}"
            ) from None
    atomic_write_json(path, migrated, force=True)
    written = load_json_object(path)
    validator(written)
    if written != migrated:
        raise PersistenceError(f"Migration readback mismatch: {path}")
    return written


# Short compatibility name for existing command modules and fixtures.
load_json = load_json_object
