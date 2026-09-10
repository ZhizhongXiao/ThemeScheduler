"""Public automatic switching service API."""

from .backend import AutoWindowsBackend, WindowsAutoBackend
from .outcome import AutoRunOutcome
from .runner import AutoRunner

__all__ = [
    "AutoRunOutcome",
    "AutoRunner",
    "AutoWindowsBackend",
    "WindowsAutoBackend",
]
