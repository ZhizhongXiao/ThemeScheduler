"""Minimal injectable filesystem used by deployment transactions."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Protocol


class DeploymentFileSystem(Protocol):
    def copy_tree(self, source: Path, target: Path) -> None: ...
    def make_directory(self, path: Path) -> None: ...
    def move(self, source: Path, target: Path) -> None: ...
    def remove_tree(self, path: Path) -> None: ...


class LocalDeploymentFileSystem:
    def copy_tree(self, source: Path, target: Path) -> None:
        shutil.copytree(source, target)

    def make_directory(self, path: Path) -> None:
        Path(path).mkdir(parents=False, exist_ok=False)

    def move(self, source: Path, target: Path) -> None:
        os.replace(source, target)

    def remove_tree(self, path: Path) -> None:
        shutil.rmtree(path)
