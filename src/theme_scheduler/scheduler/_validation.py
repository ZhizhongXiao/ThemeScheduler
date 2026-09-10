"""Shared strict value validation for scheduler contracts."""

from __future__ import annotations

import ntpath
from collections.abc import Mapping
from typing import Any

from .errors import SchedulerContractError


def _exact_keys(payload: Mapping[str, Any], expected: set[str], location: str) -> None:
    actual = set(payload)
    if actual != expected:
        raise SchedulerContractError(
            f"{location} fields do not match schema; "
            f"missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}."
        )


def _mapping(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SchedulerContractError(f"{location} must be an object.")
    return value


def _text(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise SchedulerContractError(f"{location} must be a non-empty string.")
    return value


def _string(value: Any, location: str) -> str:
    if not isinstance(value, str):
        raise SchedulerContractError(f"{location} must be a string.")
    return value


def _boolean(value: Any, location: str) -> bool:
    if not isinstance(value, bool):
        raise SchedulerContractError(f"{location} must be boolean.")
    return value


def _absolute_windows_path(value: Any, location: str) -> str:
    path = _text(value, location)
    if any(character in path for character in ('"', "\r", "\n")):
        raise SchedulerContractError(f"{location} contains unsafe characters.")
    drive, tail = ntpath.splitdrive(path)
    if not drive or not tail.startswith(("\\", "/")):
        raise SchedulerContractError(f"{location} must be an absolute Windows path.")
    normalized = ntpath.normpath(path)
    if normalized in {drive + "\\", drive + "/"}:
        raise SchedulerContractError(f"{location} cannot be a drive root.")
    return normalized
