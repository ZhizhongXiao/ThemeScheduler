"""WebView2 Runtime detection that does not import pywebview."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

WEBVIEW2_CLIENT_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_REGISTRY_PATH = rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}"


class RegistryVersionReader(Protocol):
    def versions(self) -> list[tuple[str, str]]: ...


class WindowsWebView2RegistryReader:
    """Read only the documented Edge Update client version marker."""

    def versions(self) -> list[tuple[str, str]]:
        if os.name != "nt":
            return []
        import winreg

        locations = (
            ("current-user", winreg.HKEY_CURRENT_USER, 0),
            (
                "machine-32",
                winreg.HKEY_LOCAL_MACHINE,
                getattr(winreg, "KEY_WOW64_32KEY", 0),
            ),
            (
                "machine-64",
                winreg.HKEY_LOCAL_MACHINE,
                getattr(winreg, "KEY_WOW64_64KEY", 0),
            ),
        )
        found: list[tuple[str, str]] = []
        for label, hive, view in locations:
            try:
                with winreg.OpenKey(
                    hive,
                    WEBVIEW2_REGISTRY_PATH,
                    0,
                    winreg.KEY_READ | view,
                ) as key:
                    version, value_type = winreg.QueryValueEx(key, "pv")
            except OSError:
                continue
            if (
                value_type == winreg.REG_SZ
                and isinstance(version, str)
                and version
                and version != "0.0.0.0"
            ):
                found.append((label, version))
        return found


@dataclass(frozen=True)
class WebView2RuntimeStatus:
    available: bool
    version: str | None
    scope: str | None
    message: str

    def as_dict(self) -> dict[str, str | bool | None]:
        return {
            "available": self.available,
            "version": self.version,
            "scope": self.scope,
            "message": self.message,
        }


def detect_webview2_runtime(
    reader: RegistryVersionReader | None = None,
) -> WebView2RuntimeStatus:
    if os.name != "nt" and reader is None:
        return WebView2RuntimeStatus(
            False,
            None,
            None,
            "WebView2 Runtime detection requires Windows.",
        )
    try:
        versions = (reader or WindowsWebView2RegistryReader()).versions()
    except Exception as exc:
        return WebView2RuntimeStatus(
            False,
            None,
            None,
            f"WebView2 Runtime detection failed: {type(exc).__name__}: {exc}",
        )
    if not versions:
        return WebView2RuntimeStatus(
            False,
            None,
            None,
            "Microsoft Edge WebView2 Runtime was not found.",
        )
    scope, version = versions[0]
    return WebView2RuntimeStatus(
        True,
        version,
        scope,
        f"Microsoft Edge WebView2 Runtime {version} is available.",
    )


def show_native_webview2_error(message: str) -> None:
    """Show a dependency error without importing pywebview."""

    if os.name != "nt":
        return
    import ctypes

    ctypes.windll.user32.MessageBoxW(
        None,
        message,
        "ThemeScheduler",
        0x00000010,
    )
