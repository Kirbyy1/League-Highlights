from __future__ import annotations

import logging
import time

from PySide6.QtCore import QTimer

from app.controller import RecorderController
from app.models import RecorderState
from app.services.performance_monitor import PerformanceMonitor
from app.services.performance_targets import TARGETS

LOGGER = logging.getLogger(__name__)


class PerformanceRecorderController(RecorderController):
    """Recorder controller with backoff, watchdogs, and limited self-recovery."""

    ACTIVE_WINDOW_POLL_MS = 1300
    IDLE_WINDOW_POLL_MS = 3000
    MAX_RECOVERY_ATTEMPTS = 3
    RECOVERY_WINDOW_SECONDS = 120.0

    def __init__(self, config) -> None:
        self._recovery_attempts: list[float] = []
        self._recovery_pending = False
        super().__init__(config)

        self.performance_monitor = PerformanceMonitor(
            temp_dir=config.temp_dir,
            is_recording=lambda: self.recording,
            league_is_open=lambda: self.current_window is not None,
        )
        self.performance_monitor.start()

    def _initial_check(self) -> None:
        if not self.ffmpeg.available:
            self._set_state(
                RecorderState.ERROR,
                "The installation is incomplete because the media components "
                "are missing. Reinstall League Highlights.",
            )
            return
        self._set_state(
            RecorderState.WAITING,
            "Start a League game in Borderless mode",
        )
        self._poll()

    def _poll(self) -> None:
        super()._poll()

        target_interval = (
            self.ACTIVE_WINDOW_POLL_MS
            if self.current_window is not None or self.recording
            else self.IDLE_WINDOW_POLL_MS
        )
        if self.poll_timer.interval() != target_interval:
            self.poll_timer.setInterval(target_interval)

        if self.recording:
            self._recovery_attempts.clear()
            self._recovery_pending = False
            return

        if (
            self.state == RecorderState.ERROR
            and self.current_window is not None
            and self.config.auto_start
            and self.ffmpeg.available
            and self._auto_restart_blocked
        ):
            self._schedule_recovery()

    def start_recording(self) -> None:
        super().start_recording()
        if self.recording:
            self._recovery_attempts.clear()
            self._recovery_pending = False
        elif (
            self.state == RecorderState.ERROR
            and self.current_window is not None
            and self.config.auto_start
        ):
            self._schedule_recovery()

    def _schedule_recovery(self) -> None:
        if self._recovery_pending or self._shutdown:
            return

        now = time.monotonic()
        cutoff = now - self.RECOVERY_WINDOW_SECONDS
        self._recovery_attempts = [
            stamp for stamp in self._recovery_attempts if stamp >= cutoff
        ]
        if len(self._recovery_attempts) >= self.MAX_RECOVERY_ATTEMPTS:
            LOGGER.error(
                "Automatic capture recovery stopped after %s attempts",
                self.MAX_RECOVERY_ATTEMPTS,
            )
            return

        delay_ms = 3000 * (len(self._recovery_attempts) + 1)
        self._recovery_pending = True
        LOGGER.warning("Scheduling capture recovery in %.1fs", delay_ms / 1000)
        QTimer.singleShot(delay_ms, self._attempt_recovery)

    def _attempt_recovery(self) -> None:
        self._recovery_pending = False
        if (
            self._shutdown
            or self.current_window is None
            or self.recording
            or not self.config.auto_start
        ):
            return

        self._recovery_attempts.append(time.monotonic())
        self._auto_restart_blocked = False
        self._set_state(
            RecorderState.STARTING,
            "Recovering capture after an unexpected stop",
        )
        self.start_recording()

    def shutdown(self) -> None:
        monitor = getattr(self, "performance_monitor", None)
        if monitor is not None:
            monitor.stop()
        super().shutdown()
