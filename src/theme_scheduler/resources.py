"""Resolve source-tree and PyInstaller-bundled read-only resources."""

from __future__ import annotations

import sys
from pathlib import Path


def resource_path(*parts: str) -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    root = (
        Path(bundle_root)
        if bundle_root is not None
        else Path(__file__).resolve().parents[2]
    )
    return root.joinpath(*parts).resolve()
