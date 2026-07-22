from __future__ import annotations

import os
import sys


def main() -> int:
    print("League Highlights diagnostics")
    print("=" * 32)
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    print(f"Platform: {sys.platform} / {os.name}")
    if os.name != "nt":
        print("ERROR: This recorder only runs on Windows.")
        return 1

    failures = 0
    try:
        import win32gui  # noqa: F401
        import psutil  # noqa: F401
        from PySide6 import QtCore  # noqa: F401
        import pyaudiowpatch as pyaudio
        print("Python dependencies: OK")
    except Exception as exc:
        print(f"ERROR: Python dependency import failed: {exc}")
        failures += 1
        return failures

    from app.config import AppConfig
    from app.services.ffmpeg_tools import FfmpegTools
    from app.services.league_window import LeagueWindowDetector

    config = AppConfig.create_default()
    ffmpeg = FfmpegTools(config.ffmpeg_dir)
    print(f"FFmpeg: {ffmpeg.ffmpeg or 'MISSING'}")
    print(f"FFprobe: {ffmpeg.ffprobe or 'MISSING'}")
    if not ffmpeg.available:
        print(r"ERROR: Run scripts\download_ffmpeg.ps1")
        failures += 1
    else:
        try:
            encoder = ffmpeg.choose_encoder()
            print(f"Selected encoder: {encoder}")
            print(f"ddagrab available: {ffmpeg.supports_filter('ddagrab')}")
        except Exception as exc:
            print(f"ERROR: FFmpeg encoder test failed: {exc}")
            failures += 1

    try:
        with pyaudio.PyAudio() as manager:
            helper = getattr(manager, "get_default_wasapi_loopback", None)
            if callable(helper):
                device = helper()
                print(f"WASAPI loopback: {device['name']}")
            else:
                print("WASAPI loopback helper unavailable; the app will use its compatibility search.")
    except Exception as exc:
        print(f"ERROR: WASAPI loopback test failed: {exc}")
        failures += 1

    window = LeagueWindowDetector().find()
    if window:
        print(
            f"League window: {window.title} | {window.width}x{window.height} | "
            f"HWND 0x{window.hwnd:X} | display index {window.monitor_index}"
        )
    else:
        print("League window: not found (normal when no game is open)")

    print("=" * 32)
    print("PASS" if failures == 0 else f"FAIL ({failures} issue(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
