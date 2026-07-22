from __future__ import annotations

import logging
import threading
import time

from app.services.ffmpeg_tools import FfmpegError, FfmpegTools
from app.services.performance_targets import TARGETS

LOGGER = logging.getLogger(__name__)


class ReliableFfmpegTools(FfmpegTools):
    """Hardware-first encoder selection plus one heavy FFmpeg job at a time."""

    _job_gate = threading.BoundedSemaphore(TARGETS.max_concurrent_ffmpeg_jobs)
    _encoder_order = ("h264_nvenc", "h264_qsv", "h264_amf", "libx264")

    def __init__(self, preferred_dir) -> None:
        super().__init__(preferred_dir)
        self._unhealthy_until: dict[str, float] = {}

    def choose_encoder(self) -> str:
        now = time.monotonic()
        if (
            self.encoder
            and self._unhealthy_until.get(self.encoder, 0.0) <= now
        ):
            return self.encoder

        self.encoder = None
        for encoder in self._encoder_order:
            unhealthy_until = self._unhealthy_until.get(encoder, 0.0)
            if unhealthy_until > now:
                continue
            if self._test_encoder(encoder):
                self.encoder = encoder
                LOGGER.info("Selected healthy video encoder: %s", encoder)
                return encoder

        raise FfmpegError("No usable H.264 encoder was found in FFmpeg.")

    def mark_encoder_unhealthy(
        self,
        encoder: str | None,
        reason: str,
        *,
        retry_after_seconds: float = 300.0,
    ) -> None:
        name = str(encoder or "").strip()
        if not name:
            return
        self._unhealthy_until[name] = time.monotonic() + max(
            30.0,
            float(retry_after_seconds),
        )
        if self.encoder == name:
            self.encoder = None
        LOGGER.warning(
            "Encoder %s marked unhealthy for %.0fs: %s",
            name,
            retry_after_seconds,
            reason,
        )

    def run(
        self,
        command: list[str],
        timeout: float | None = None,
        *,
        low_priority: bool = False,
    ):
        # All export calls already execute on worker threads. Serializing them
        # prevents a thumbnail, filmstrip, Discord export, and clip save from
        # competing with each other while the recorder is active.
        acquired = self._job_gate.acquire(timeout=300.0)
        if not acquired:
            raise TimeoutError("Timed out waiting for the FFmpeg export queue.")
        try:
            return super().run(
                command,
                timeout=timeout,
                low_priority=low_priority,
            )
        finally:
            self._job_gate.release()
