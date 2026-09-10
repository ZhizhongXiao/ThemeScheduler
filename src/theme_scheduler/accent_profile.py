"""Versioned semantic accent profiles used by the stage-2 product path."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .accent_theme import ThemeFileError, read_visual_state
from .persistence import atomic_write_json, load_json_object

ACCENT_PROFILE_KIND = "themescheduler.accent-profile"
ACCENT_PROFILE_SCHEMA_VERSION = 1
PROFILE_NAMES = frozenset({"day", "night"})
RGB_HEX_PATTERN = re.compile(r"#[0-9A-Fa-f]{6}")


@dataclass(frozen=True)
class RgbColor:
    """One visible 24-bit color, independent from theme semantic alpha."""

    red: int
    green: int
    blue: int

    def __post_init__(self) -> None:
        for name, value in (
            ("red", self.red),
            ("green", self.green),
            ("blue", self.blue),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 255
            ):
                raise ValueError(f"RGB {name} must be an integer from 0 to 255.")

    @property
    def value(self) -> int:
        return self.red << 16 | self.green << 8 | self.blue

    @property
    def hex(self) -> str:
        return f"#{self.value:06X}"

    def as_dict(self) -> dict[str, int | str]:
        return {
            "hex": self.hex,
            "red": self.red,
            "green": self.green,
            "blue": self.blue,
        }

    @classmethod
    def from_hex(cls, value: str) -> RgbColor:
        if not isinstance(value, str) or not RGB_HEX_PATTERN.fullmatch(value):
            raise ValueError("RGB hex color must use #RRGGBB.")
        numeric = int(value[1:], 16)
        return cls(
            red=(numeric >> 16) & 0xFF,
            green=(numeric >> 8) & 0xFF,
            blue=numeric & 0xFF,
        )

    @classmethod
    def from_colorization_color(cls, value: int) -> RgbColor:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= 0xFFFFFFFF
        ):
            raise ValueError("ColorizationColor must be a DWORD.")
        return cls(
            red=(value >> 16) & 0xFF,
            green=(value >> 8) & 0xFF,
            blue=value & 0xFF,
        )

    def replace_colorization_rgb(self, original: int) -> int:
        if (
            isinstance(original, bool)
            or not isinstance(original, int)
            or not 0 <= original <= 0xFFFFFFFF
        ):
            raise ValueError("Original ColorizationColor must be a DWORD.")
        return (original & 0xFF000000) | self.value


@dataclass(frozen=True)
class AccentProfile:
    """The smallest durable accent intent; not a registry snapshot."""

    profile: str
    captured_at: str
    auto_colorization: bool
    colorization_color: int
    windows_build: str

    def __post_init__(self) -> None:
        if not isinstance(self.profile, str) or self.profile not in PROFILE_NAMES:
            raise ValueError("Accent profile must be 'day' or 'night'.")
        if not isinstance(self.captured_at, str) or not self.captured_at:
            raise ValueError("Accent profile capturedAt must be a non-empty string.")
        try:
            parsed = datetime.fromisoformat(self.captured_at)
        except ValueError as exc:
            raise ValueError("Accent profile capturedAt must be ISO 8601.") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Accent profile capturedAt must include a UTC offset.")
        if not isinstance(self.auto_colorization, bool):
            raise ValueError("Accent profile autoColorization must be boolean.")
        if (
            isinstance(self.colorization_color, bool)
            or not isinstance(self.colorization_color, int)
            or not 0 <= self.colorization_color <= 0xFFFFFFFF
        ):
            raise ValueError("Accent profile colorizationColor must be a DWORD.")
        if not isinstance(self.windows_build, str) or not self.windows_build:
            raise ValueError("Accent profile windowsBuild must be a non-empty string.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": ACCENT_PROFILE_KIND,
            "schemaVersion": ACCENT_PROFILE_SCHEMA_VERSION,
            "profile": self.profile,
            "capturedAt": self.captured_at,
            "accent": {
                "autoColorization": self.auto_colorization,
                "colorizationColor": f"0X{self.colorization_color:08X}",
            },
            "environment": {"windowsBuild": self.windows_build},
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AccentProfile:
        if payload.get("kind") != ACCENT_PROFILE_KIND:
            raise ValueError("JSON is not a ThemeScheduler accent profile.")
        if payload.get("schemaVersion") != ACCENT_PROFILE_SCHEMA_VERSION:
            raise ValueError("Unsupported accent profile schemaVersion.")
        expected = {
            "kind",
            "schemaVersion",
            "profile",
            "capturedAt",
            "accent",
            "environment",
        }
        if set(payload) != expected:
            raise ValueError(
                "Accent profile fields do not match schema; "
                f"missing={sorted(expected - set(payload))}, "
                f"unknown={sorted(set(payload) - expected)}."
            )
        accent = payload.get("accent")
        environment = payload.get("environment")
        if not isinstance(accent, Mapping) or not isinstance(environment, Mapping):
            raise ValueError("Accent profile is missing accent or environment data.")
        if set(accent) != {"autoColorization", "colorizationColor"}:
            raise ValueError("Accent profile accent fields do not match schema.")
        if set(environment) != {"windowsBuild"}:
            raise ValueError("Accent profile environment fields do not match schema.")
        raw_color = accent.get("colorizationColor")
        if not isinstance(raw_color, str) or not re.fullmatch(
            r"0[xX][0-9A-Fa-f]{8}", raw_color
        ):
            raise ValueError(
                "Accent profile colorizationColor must be 0X followed by 8 hex digits."
            )
        return cls(
            profile=payload.get("profile"),  # type: ignore[arg-type]
            captured_at=payload.get("capturedAt"),  # type: ignore[arg-type]
            auto_colorization=accent.get("autoColorization"),  # type: ignore[arg-type]
            colorization_color=int(raw_color[2:], 16),
            windows_build=environment.get("windowsBuild"),  # type: ignore[arg-type]
        )


def profile_from_theme(
    content: bytes,
    *,
    profile: str,
    captured_at: str,
    windows_build: str,
) -> AccentProfile:
    try:
        state = read_visual_state(content)
    except ThemeFileError:
        raise
    return AccentProfile(
        profile=profile,
        captured_at=captured_at,
        auto_colorization=state.auto_colorization == "1",
        colorization_color=state.colorization_color,
        windows_build=windows_build,
    )


class AccentProfileStore:
    """Profile writes never replace a missing or damaged profile implicitly."""

    def __init__(self, path: Path, expected_profile: str) -> None:
        if expected_profile not in PROFILE_NAMES:
            raise ValueError("Expected profile must be day or night.")
        self.path = Path(path)
        self.expected_profile = expected_profile

    def _validate_identity(self, profile: AccentProfile) -> AccentProfile:
        if profile.profile != self.expected_profile:
            raise ValueError("Accent profile identity does not match its store path.")
        return profile

    def create(self, profile: AccentProfile) -> AccentProfile:
        self._validate_identity(profile)
        atomic_write_json(self.path, profile.as_dict())
        return self.load()

    def load(self) -> AccentProfile:
        return self._validate_identity(
            AccentProfile.from_dict(load_json_object(self.path))
        )

    def replace_if_valid(self, profile: AccentProfile) -> AccentProfile:
        self._validate_identity(profile)
        self.load()
        atomic_write_json(self.path, profile.as_dict(), force=True)
        written = self.load()
        if written != profile:
            raise OSError(f"Accent profile readback mismatch: {self.path}")
        return written

    def restore_trusted(self, profile: AccentProfile) -> AccentProfile:
        """Restore a previously validated profile during transaction rollback."""

        self._validate_identity(profile)
        atomic_write_json(self.path, profile.as_dict(), force=True)
        written = self.load()
        if written != profile:
            raise OSError(f"Accent profile rollback readback mismatch: {self.path}")
        return written
