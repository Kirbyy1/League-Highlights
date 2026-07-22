from __future__ import annotations

import logging
from collections.abc import Callable

import psutil
import re

import os

from app.models import LeagueWindowInfo

IS_WINDOWS = os.name == "nt"
if IS_WINDOWS:
    import win32api
    import win32con
    import win32gui
    import win32process


LOGGER = logging.getLogger(__name__)

_GAME_PROCESS_NAMES = {
    "league of legends.exe",
    "leagueoflegends.exe",
}


class LeagueWindowDetector:
    """Finds the visible League game window, not the launcher/client window."""

    def find(self) -> LeagueWindowInfo | None:
        if not IS_WINDOWS:
            return None

        candidates: list[LeagueWindowInfo] = []

        def enum_callback(hwnd: int, _: object) -> bool:
            candidate = self._inspect_window(hwnd)
            if candidate is not None:
                candidates.append(candidate)
            return True

        try:
            win32gui.EnumWindows(enum_callback, None)
        except Exception:
            LOGGER.exception("Failed while enumerating Windows windows")
            return None

        if not candidates:
            return None
        return max(candidates, key=lambda item: item.width * item.height)

    @staticmethod
    def _inspect_window(hwnd: int) -> LeagueWindowInfo | None:
        if not IS_WINDOWS:
            return None

        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return None

        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return None

        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process = psutil.Process(pid)
            process_name = process.name()
        except (psutil.Error, OSError):
            return None

        if process_name.lower() not in _GAME_PROCESS_NAMES:
            return None

        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        except win32gui.error:
            return None

        width = right - left
        height = bottom - top
        if width < 640 or height < 360:
            return None

        monitor_index = 0
        offset_x = 0
        offset_y = 0
        try:
            monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
            monitor_info = win32api.GetMonitorInfo(monitor)
            monitor_left, monitor_top, _, _ = monitor_info["Monitor"]
            device_name = str(monitor_info.get("Device", ""))
            match = re.search(r"DISPLAY(\d+)$", device_name, re.IGNORECASE)
            if match:
                monitor_index = max(0, int(match.group(1)) - 1)
            offset_x = max(0, left - monitor_left)
            offset_y = max(0, top - monitor_top)
        except Exception:
            LOGGER.debug("Could not map League window to DXGI output", exc_info=True)

        return LeagueWindowInfo(
            hwnd=hwnd,
            pid=pid,
            process_name=process_name,
            title=title,
            width=width,
            height=height,
            monitor_index=monitor_index,
            offset_x=offset_x,
            offset_y=offset_y,
        )
