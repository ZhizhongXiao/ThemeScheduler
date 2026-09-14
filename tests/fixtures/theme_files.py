"""Schema-focused Windows theme fixtures with user-specific data removed."""

from __future__ import annotations


def windows_11_variant_theme() -> bytes:
    """A captured Windows 11 custom-theme shape without VisualStyles."""

    return (
        b"[Control Panel\\Cursors]\r\n"
        b"DefaultValue=Windows Default\r\n\r\n"
        b"[Control Panel\\Cursors.A]\r\n"
        b"DefaultValue=Windows Default\r\n\r\n"
        b"[Control Panel\\Cursors.W]\r\n"
        b"DefaultValue=Windows Default\r\n\r\n"
        b"[Theme]\r\n"
        b"DisplayName=Custom\r\n\r\n"
        b"[Theme.A]\r\n"
        b"DisplayName=Custom\r\n\r\n"
        b"[Theme.W]\r\n"
        b"DisplayName=Custom\r\n"
    )


def spotlight_theme_without_id() -> bytes:
    """A standard system-theme shape whose Theme section has no ThemeId."""

    return (
        b"[Theme]\r\n"
        b"DisplayName=@%SystemRoot%\\System32\\themeui.dll,-2320\r\n\r\n"
        b"[Control Panel\\Desktop]\r\n"
        b"Wallpaper=%SystemRoot%\\web\\wallpaper\\spotlight\\img50.jpg\r\n\r\n"
        b"[VisualStyles]\r\n"
        b"Path=%ResourceDir%\\Themes\\Aero\\Aero.msstyles\r\n"
        b"ColorStyle=NormalColor\r\n"
        b"Size=NormalSize\r\n"
        b"AutoColorization=0\r\n"
        b"ColorizationColor=0XC40078D4\r\n"
        b"SystemMode=Light\r\n"
        b"AppMode=Light\r\n\r\n"
        b"[MasterThemeSelector]\r\n"
        b"MTSM=RJSPBS\r\n"
    )
