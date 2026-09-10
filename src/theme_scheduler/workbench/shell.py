"""Restricted shell navigation used by the workbench."""

from __future__ import annotations

import os
from pathlib import Path

from ..storage import UserDataLayout


class ShellActions:
    """Allow only frozen navigation targets and the installed uninstaller."""

    def __init__(
        self,
        layout: UserDataLayout,
        executable: Path | None = None,
    ) -> None:
        self.layout = layout
        self.executable = Path(executable).resolve() if executable is not None else None

    def open(self, target: str) -> None:
        if os.name != "nt":
            raise OSError("Shell navigation requires Windows.")
        if target == "colors":
            os.startfile("ms-settings:colors")
            return
        if target == "uninstaller":
            if self.executable is None:
                raise ValueError(
                    "Installed executable is required for uninstall launch."
                )
            if (
                self.executable.name != "ThemeScheduler.exe"
                or self.executable.parent.name != "app"
            ):
                raise ValueError("Executable does not match the installed layout.")
            program_root = self.executable.parent.parent
            if (
                program_root.name != "ThemeScheduler"
                or program_root.parent.name.casefold() != "programs"
            ):
                raise ValueError("Executable is outside the frozen program root.")
            uninstaller = program_root / "maintenance" / "Uninstall.exe"
            if not uninstaller.is_file():
                raise FileNotFoundError(
                    f"Independent uninstaller is missing: {uninstaller}"
                )
            os.startfile(str(uninstaller))
            return
        paths = {
            "logs": self.layout.logs,
            "data": self.layout.root,
        }
        path = paths.get(target)
        if path is None:
            raise ValueError("Unsupported shell target.")
        if not path.is_dir():
            raise FileNotFoundError(f"Directory does not exist: {path}")
        os.startfile(str(path.resolve()))
