from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "League Highlights"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def startup_command() -> str:
    """Return the command Windows should execute at sign-in."""
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable)
        return f'"{executable}" --startup'

    python_executable = Path(sys.executable)
    project_main = Path(__file__).resolve().parents[2] / "main.py"
    return f'"{python_executable}" "{project_main}" --startup'


def is_enabled() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
        return bool(str(value).strip())
    except (FileNotFoundError, OSError):
        return False


def set_enabled(enabled: bool) -> None:
    """Enable or disable per-user launch at Windows sign-in."""
    if os.name != "nt":
        raise RuntimeError("Windows startup is only available on Windows.")

    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
