from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from app.services.ffmpeg_tools import BELOW_NORMAL_PRIORITY, CREATE_NO_WINDOW, FfmpegTools

LOGGER = logging.getLogger(__name__)


class ShareExportError(RuntimeError):
    pass


class ShareExportCancelled(ShareExportError):
    pass


@dataclass(slots=True, frozen=True)
class ShareExportResult:
    output_path: Path
    actual_size_bytes: int
    actual_duration_seconds: float


class ShareExportService:
    """Create a high-quality, broadly compatible copy of the selected trim."""

    def __init__(self, ffmpeg: FfmpegTools) -> None:
        self.ffmpeg = ffmpeg
        self._lock = threading.Lock()
        self._active: dict[Path, tuple[threading.Event, subprocess.Popen[str] | None]] = {}

    @staticmethod
    def output_path_for(source: Path) -> Path:
        base = source.with_name(f"{source.stem}_share.mp4")
        if not base.exists():
            return base
        index = 2
        while True:
            candidate = source.with_name(f"{source.stem}_share_{index}.mp4")
            if not candidate.exists():
                return candidate
            index += 1

    def is_exporting(self, source: Path) -> bool:
        key = Path(source).resolve()
        with self._lock:
            return key in self._active

    def cancel(self, source: Path) -> bool:
        key = Path(source).resolve()
        with self._lock:
            active = self._active.get(key)
            if active is None:
                return False
            cancel_event, process = active
            cancel_event.set()
        if process is not None and process.poll() is None:
            self._terminate_process(process)
        return True

    def cancel_all(self) -> None:
        with self._lock:
            sources = list(self._active)
        for source in sources:
            self.cancel(source)

    def export(
        self,
        source: Path,
        start_seconds: float,
        end_seconds: float,
        *,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> ShareExportResult:
        self.ffmpeg.require()
        assert self.ffmpeg.ffmpeg is not None

        source = Path(source)
        if not source.exists():
            raise ShareExportError("The original highlight file could not be found.")
        start = max(0.0, float(start_seconds))
        end = float(end_seconds)
        duration = end - start
        if duration < 0.25:
            raise ShareExportError("Select at least 0.25 seconds before exporting.")

        output = self.output_path_for(source)
        working = output.with_name(f"{output.stem}.working{output.suffix}")
        key = source.resolve()
        cancel_event = threading.Event()
        with self._lock:
            if key in self._active:
                raise ShareExportError("A share export is already running for this clip.")
            self._active[key] = (cancel_event, None)

        try:
            source_size = max(source.stat().st_size, 1)
            required_free = max(source_size * 2, 128 * 1024 * 1024)
            if shutil.disk_usage(output.parent).free < required_free:
                raise ShareExportError("Not enough free disk space to create the share copy.")

            working.unlink(missing_ok=True)
            command = [
                str(self.ffmpeg.ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{duration:.3f}",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-movflags",
                "+faststart",
                "-progress",
                "pipe:1",
                "-nostats",
                str(working),
            ]
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                creationflags=CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY,
            )
            with self._lock:
                if key in self._active:
                    self._active[key] = (cancel_event, process)

            stderr_lines: list[str] = []

            def read_stderr() -> None:
                if process.stderr is None:
                    return
                for line in process.stderr:
                    stderr_lines.append(line.rstrip())

            stderr_thread = threading.Thread(target=read_stderr, name="ShareExportStderr", daemon=True)
            stderr_thread.start()
            try:
                if process.stdout is not None:
                    for raw_line in process.stdout:
                        if cancel_event.is_set():
                            self._terminate_process(process)
                            raise ShareExportCancelled("Share export cancelled.")
                        key_name, _, raw_value = raw_line.strip().partition("=")
                        if key_name in {"out_time_ms", "out_time_us"}:
                            try:
                                microseconds = int(raw_value)
                            except ValueError:
                                continue
                            percent = min(99, max(0, int(microseconds / 1_000_000 / duration * 100)))
                            if progress_callback:
                                progress_callback(percent, "Creating high-quality share copy…")
                return_code = process.wait()
                stderr_thread.join(timeout=1.0)
                if cancel_event.is_set():
                    raise ShareExportCancelled("Share export cancelled.")
                if return_code != 0:
                    error_text = "\n".join(stderr_lines[-30:]).strip()
                    raise ShareExportError(error_text or f"FFmpeg exited with code {return_code}.")
            finally:
                if process.poll() is None:
                    self._terminate_process(process)

            if not working.exists() or working.stat().st_size <= 0:
                raise ShareExportError("FFmpeg did not create a usable share copy.")
            actual_duration = self.ffmpeg.probe_duration(working)
            tolerance = max(0.75, min(2.0, duration * 0.06))
            if abs(actual_duration - duration) > tolerance:
                raise ShareExportError(
                    f"The exported duration was invalid ({actual_duration:.2f}s instead of {duration:.2f}s)."
                )
            os.replace(working, output)
            actual_size = output.stat().st_size
            output.with_suffix(".json").write_text(
                json.dumps(
                    {
                        "is_share_copy": True,
                        "source_file": source.name,
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "duration_seconds": actual_duration,
                        "size_bytes": actual_size,
                        "trim_start_seconds": start,
                        "trim_end_seconds": end,
                        "video_codec": "h264",
                        "audio_codec": "aac",
                        "quality": "high",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            if progress_callback:
                progress_callback(100, "Share copy ready")
            return ShareExportResult(output, actual_size, actual_duration)
        except ShareExportCancelled:
            raise
        except Exception:
            LOGGER.exception("Share export failed for %s", source)
            raise
        finally:
            working.unlink(missing_ok=True)
            with self._lock:
                self._active.pop(key, None)

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        try:
            process.terminate()
            process.wait(timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
