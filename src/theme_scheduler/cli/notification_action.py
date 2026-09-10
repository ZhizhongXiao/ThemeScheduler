"""Installed no-GUI protocol activation entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from ..core import mutex_name_for_data_root
from ..execution_lock import WindowsNamedMutexLock
from ..gui_activation import (
    gui_activation_event_name,
    signal_existing_gui,
)
from ..notification_action_service import NotificationActionService
from ..notification_protocol import is_health_activation_uri
from ..scheduler_windows import WindowsTaskSchedulerBackend
from ..storage import UserDataLayout


def _installed_executable() -> Path:
    executable = Path(sys.executable).resolve()
    if (
        not bool(getattr(sys, "frozen", False))
        or executable.name.casefold() != "themescheduler.exe"
    ):
        raise OSError(
            "Notification protocol activation requires installed ThemeScheduler.exe."
        )
    return executable


def run_installed_notification_action(uri: str) -> int:
    try:
        layout = UserDataLayout.default()
        executable = _installed_executable()
        if is_health_activation_uri(uri):
            event_name = gui_activation_event_name(layout.root)
            if signal_existing_gui(event_name):
                return 0
            from .gui import launch_gui

            return launch_gui(
                data_root=None,
                installed=True,
                initial_section="health",
            )
        tasks = WindowsTaskSchedulerBackend()
        user_id = tasks.current_user_id()
        service = NotificationActionService(
            layout,
            WindowsNamedMutexLock(mutex_name_for_data_root(layout.root)),
            tasks,
            executable=str(executable),
            user_id=user_id,
        )
        return int(service.handle(uri).exit_code)
    except (OSError, ValueError, RuntimeError):
        return 2
