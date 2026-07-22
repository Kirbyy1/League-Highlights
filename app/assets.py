from __future__ import annotations

import sys
from pathlib import Path


def bundle_root() -> Path:
    """Return the source or PyInstaller bundle root."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def asset_path(name: str) -> Path:
    return bundle_root() / "app" / "assets" / name


def logo_path() -> Path:
    return asset_path("logo.png")


def icon_path() -> Path:
    return asset_path("league_highlights.ico")
