"""Purpose-oriented user-data paths for ThemeScheduler."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class UserDataLayout:
    root: Path

    @classmethod
    def default(cls) -> UserDataLayout:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise OSError("LOCALAPPDATA is unavailable.")
        return cls(Path(local_app_data) / "ThemeScheduler")

    @property
    def profiles(self) -> Path:
        return self.root / "profiles"

    @property
    def config(self) -> Path:
        return self.root / "config.json"

    @property
    def state(self) -> Path:
        return self.root / "state.json"

    @property
    def initial_setup_marker(self) -> Path:
        return self.root / "initial-setup.json"

    @property
    def runtime(self) -> Path:
        return self.root / "runtime"

    @property
    def pending_switch(self) -> Path:
        return self.runtime / "pending-switch.json"

    @property
    def notification_dedup(self) -> Path:
        return self.runtime / "notification-dedup.json"

    @property
    def backup(self) -> Path:
        return self.root / "backup"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def install_backup_manifest(self) -> Path:
        return self.backup / "install.json"

    @property
    def install_backup_theme(self) -> Path:
        return self.backup / "install.theme"

    @property
    def event_log(self) -> Path:
        return self.logs / "events.jsonl"

    def profile_path(self, profile: str) -> Path:
        if profile not in {"day", "night"}:
            raise ValueError("Profile name must be 'day' or 'night'.")
        return self.profiles / f"{profile}.json"

    def ensure_directories(self) -> None:
        for directory in (self.profiles, self.runtime, self.backup, self.logs):
            directory.mkdir(parents=True, exist_ok=True)

    def new_transaction_directory(self, now: datetime | None = None) -> Path:
        instant = now or datetime.now().astimezone()
        stamp = instant.strftime("%Y%m%dT%H%M%S")
        return self.runtime / f"accent-{stamp}-{uuid4().hex[:8]}"
