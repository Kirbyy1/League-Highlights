from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import threading

from app.services.performance_targets import TARGETS
from app.services.video_recorder import VideoSegmentRecorder

LOGGER = logging.getLogger(__name__)

BELOW_NORMAL_PRIORITY = (
    getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    if os.name == "nt"
    else 0
)


class ReliableVideoSegmentRecorder(VideoSegmentRecorder):
    """Recorder with hardware fallback, low priority, and bounded disk use."""

    def start(self, window) -> None:
        attempted: set[str] = set()
        last_error: Exception | None = None

        while len(attempted) < 4:
            try:
                super().start(window)
                if self.running:
                    return
            except Exception as exc:
                last_error = exc
                failed_encoder = self.encoder or getattr(self.ffmpeg, "encoder", None)
                self.stop()

                if (
                    not failed_encoder
                    or failed_encoder in attempted
                    or failed_encoder == "libx264"
                    or not hasattr(self.ffmpeg, "mark_encoder_unhealthy")
                ):
                    raise

                attempted.add(failed_encoder)
                self.ffmpeg.mark_encoder_unhealthy(
                    failed_encoder,
                    str(exc),
                )
                self.encoder = None
                LOGGER.warning(
                    "Capture failed with %s; retrying with another encoder",
                    failed_encoder,
                )
                continue

        raise RuntimeError(str(last_error or "Capture could not start."))

    def _launch(self, command: list[str]) -> None:
        # Keep capture responsive without allowing FFmpeg to take priority over
        # League itself.
        LOGGER.info("Starting low-priority FFmpeg capture: %s", " ".join(command))
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | BELOW_NORMAL_PRIORITY
            ),
        )
        process = self.process
        self._log_thread = threading.Thread(
            target=self._read_logs_for_process,
            args=(process,),
            name="FFmpegLog",
            daemon=True,
        )
        self._log_thread.start()

    def _watch(self) -> None:
        # Keep the configured buffer plus a small safety window for a clip export
        # that has just crossed a segment boundary. The former implementation kept
        # an extra 30 seconds indefinitely.
        retained_seconds = (
            self.config.buffer_seconds
            + TARGETS.rolling_buffer_safety_seconds
        )
        max_files = (
            math.ceil(retained_seconds / self.config.segment_seconds) + 2
        )
        minimum_free = TARGETS.minimum_free_disk_mib * 1024 * 1024

        while not self._stop_event.wait(0.75):
            process = self.process
            if process is None:
                return
            if process.poll() is not None:
                tail = " | ".join(self._stderr_tail)
                self.last_error = self.last_error or (
                    f"FFmpeg stopped unexpectedly with code {process.returncode}."
                    + (f" Last message: {tail}" if tail else "")
                )
                LOGGER.error(self.last_error)
                return

            try:
                free_bytes = shutil.disk_usage(self.config.temp_dir).free
            except OSError:
                free_bytes = minimum_free + 1

            if free_bytes < minimum_free:
                self.last_error = (
                    "Recording stopped because the drive has less than "
                    f"{TARGETS.minimum_free_disk_mib} MiB free."
                )
                LOGGER.error(self.last_error)
                try:
                    process.terminate()
                except OSError:
                    pass
                return

            files = self.all_segments()
            completed = files[:-1] if len(files) > 1 else []
            overflow = len(completed) - max_files
            for path in completed[: max(0, overflow)]:
                try:
                    path.unlink()
                except OSError:
                    LOGGER.debug(
                        "Could not delete rolling segment %s",
                        path,
                        exc_info=True,
                    )
