from __future__ import annotations

import logging
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from app.services.performance_targets import TARGETS

LOGGER = logging.getLogger(__name__)

try:
    import psutil
except Exception:  # packaged builds should include psutil, but monitoring is optional
    psutil = None


class PerformanceMonitor:
    """Low-frequency internal watchdog.

    It records warnings in the diagnostic log only. It never interrupts a match
    because a target was missed.
    """

    SAMPLE_SECONDS = 30.0
    STALE_TEMP_SECONDS = 6 * 60 * 60

    def __init__(
        self,
        *,
        temp_dir: Path,
        is_recording: Callable[[], bool],
        league_is_open: Callable[[], bool],
    ) -> None:
        self.temp_dir = Path(temp_dir)
        self.is_recording = is_recording
        self.league_is_open = league_is_open
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._baseline_rss = 0
        self._consecutive_idle_cpu_warnings = 0

    def start(self) -> None:
        self.cleanup_stale_temp_files()
        if psutil is None or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="PerformanceMonitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
        self._thread = None

    def cleanup_stale_temp_files(self) -> None:
        now = time.time()

        # App rolling segments from an interrupted previous session.
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        for path in self.temp_dir.glob("segment_*.mkv"):
            try:
                if now - path.stat().st_mtime > self.STALE_TEMP_SECONDS:
                    path.unlink()
            except OSError:
                LOGGER.debug("Could not remove stale segment %s", path, exc_info=True)

        # TemporaryDirectory cannot clean up after a hard crash. Only remove
        # directories with prefixes owned by League Highlights.
        system_temp = Path(tempfile.gettempdir())
        for pattern in ("league_highlight_*", "lh-inline-filmstrip-*"):
            for path in system_temp.glob(pattern):
                try:
                    if (
                        path.is_dir()
                        and now - path.stat().st_mtime > self.STALE_TEMP_SECONDS
                    ):
                        shutil.rmtree(path, ignore_errors=True)
                except OSError:
                    LOGGER.debug("Could not inspect stale temp path %s", path, exc_info=True)

    def _run(self) -> None:
        process = psutil.Process()
        process.cpu_percent(interval=None)
        try:
            self._baseline_rss = int(process.memory_info().rss)
        except Exception:
            self._baseline_rss = 0

        while not self._stop.wait(self.SAMPLE_SECONDS):
            try:
                cpu = float(process.cpu_percent(interval=None))
                rss = int(process.memory_info().rss)
                segment_files = list(self.temp_dir.glob("segment_*.mkv"))
                segment_bytes = sum(
                    path.stat().st_size for path in segment_files if path.is_file()
                )
            except Exception:
                LOGGER.debug("Performance sample failed", exc_info=True)
                continue

            idle = not self.is_recording() and not self.league_is_open()
            if idle and cpu > TARGETS.idle_cpu_percent:
                self._consecutive_idle_cpu_warnings += 1
                if self._consecutive_idle_cpu_warnings >= 3:
                    LOGGER.warning(
                        "Performance target: idle CPU %.2f%% is above %.2f%%",
                        cpu,
                        TARGETS.idle_cpu_percent,
                    )
                    self._consecutive_idle_cpu_warnings = 0
            else:
                self._consecutive_idle_cpu_warnings = 0

            growth = max(0, rss - self._baseline_rss)
            if growth > TARGETS.ram_growth_warning_mib * 1024 * 1024:
                LOGGER.warning(
                    "Performance target: process RAM grew by %.1f MiB since startup",
                    growth / (1024 * 1024),
                )
                # Move the baseline forward so one condition does not spam logs.
                self._baseline_rss = rss

            LOGGER.debug(
                "Performance sample: CPU %.2f%%, RAM %.1f MiB, segments %s / %.1f MiB",
                cpu,
                rss / (1024 * 1024),
                len(segment_files),
                segment_bytes / (1024 * 1024),
            )
