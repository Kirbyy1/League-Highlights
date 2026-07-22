from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

LOGGER = logging.getLogger(__name__)
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
BELOW_NORMAL_PRIORITY = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) if os.name == "nt" else 0


class FfmpegError(RuntimeError):
    pass


class FfmpegTools:
    def __init__(self, preferred_dir: Path) -> None:
        self.ffmpeg = self._find_binary("ffmpeg.exe", preferred_dir)
        self.ffprobe = self._find_binary("ffprobe.exe", preferred_dir)
        self.encoder: str | None = None
        self._filters_text: str | None = None

    @staticmethod
    def _find_binary(name: str, preferred_dir: Path) -> Path | None:
        bundled = preferred_dir / name
        if bundled.exists():
            return bundled
        found = shutil.which(name)
        return Path(found) if found else None

    @property
    def available(self) -> bool:
        return self.ffmpeg is not None and self.ffprobe is not None

    def require(self) -> None:
        if not self.available:
            raise FfmpegError(
                "FFmpeg is missing. Run scripts\\download_ffmpeg.ps1, then restart the app."
            )

    def choose_encoder(self) -> str:
        self.require()
        if self.encoder:
            return self.encoder

        candidates = ("h264_nvenc", "h264_qsv", "h264_amf", "libx264")
        for encoder in candidates:
            if self._test_encoder(encoder):
                self.encoder = encoder
                LOGGER.info("Selected video encoder: %s", encoder)
                return encoder
        raise FfmpegError("No usable H.264 encoder was found in FFmpeg.")

    def _test_encoder(self, encoder: str) -> bool:
        assert self.ffmpeg is not None
        command = [
            str(self.ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=128x128:r=30",
            "-frames:v",
            "2",
            "-c:v",
            encoder,
            "-f",
            "null",
            "-",
        ]
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
                creationflags=CREATE_NO_WINDOW,
                check=False,
            )
            if completed.returncode == 0:
                return True
            reason = (completed.stderr or "").strip().splitlines()
            LOGGER.warning(
                "Encoder %s is unavailable: %s",
                encoder,
                reason[-1] if reason else f"FFmpeg exited with code {completed.returncode}",
            )
            return False
        except subprocess.TimeoutExpired:
            LOGGER.warning("Encoder %s test timed out", encoder)
            return False
        except OSError as exc:
            LOGGER.warning("Encoder %s test failed: %s", encoder, exc)
            return False


    def supports_filter(self, filter_name: str) -> bool:
        self.require()
        assert self.ffmpeg is not None
        if self._filters_text is None:
            completed = subprocess.run(
                [str(self.ffmpeg), "-hide_banner", "-filters"],
                capture_output=True,
                text=True,
                timeout=20,
                creationflags=CREATE_NO_WINDOW,
                check=False,
            )
            self._filters_text = completed.stdout + completed.stderr
        for line in self._filters_text.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == filter_name:
                return True
        return False

    def probe_duration(self, media_path: Path) -> float:
        self.require()
        assert self.ffprobe is not None
        command = [
            str(self.ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
        if completed.returncode != 0:
            raise FfmpegError(completed.stderr.strip() or f"Could not probe {media_path}")
        try:
            return float(completed.stdout.strip())
        except ValueError as exc:
            raise FfmpegError(f"FFprobe returned an invalid duration for {media_path}") from exc

    def run(
        self,
        command: list[str],
        timeout: float | None = None,
        *,
        low_priority: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        creationflags = CREATE_NO_WINDOW
        if low_priority:
            creationflags |= BELOW_NORMAL_PRIORITY
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=creationflags,
            check=False,
        )
        if completed.returncode != 0:
            raise FfmpegError(completed.stderr.strip() or "FFmpeg command failed")
        return completed
