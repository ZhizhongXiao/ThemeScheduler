"""Shared subprocess options for invisible Windows helper processes."""

from __future__ import annotations

import os
import subprocess
from typing import Any


def no_window_options() -> dict[str, Any]:
    """Return subprocess options that prevent helper-console flashing."""

    if os.name != "nt":
        return {}
    startup_info = subprocess.STARTUPINFO()
    startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup_info.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startup_info,
    }
