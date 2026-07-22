from __future__ import annotations

import logging
import math
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from collections import deque
from pathlib import Path

from app.config import AppConfig
from app.models import LeagueWindowInfo
from app.services.ffmpeg_tools import CREATE_NO_WINDOW, FfmpegTools

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RecorderDiagnostics:
    encoder: str = "Not started"
    capture_backend: str = "Idle"
    hardware_encoder: bool = False
    frame: int = 0
    fps: float = 0.0
    duplicated_frames: int = 0
    dropped_frames: int = 0
    drop_rate: float = 0.0
    speed: float = 0.0
    updated_at: float = 0.0

    @property
    def health(self) -> str:
        if self.updated_at <= 0:
            return "Waiting"
        if self.drop_rate >= 2.0 or (self.fps > 0 and self.fps < 0.85):
            return "Poor"
        if self.drop_rate >= 0.5 or (self.fps > 0 and self.fps < 0.95):
            return "Warning"
        return "Good"


class VideoSegmentRecorder:
    """Captures one window into short, keyframe-aligned Matroska segments."""

    def __init__(self, config: AppConfig, ffmpeg: FfmpegTools) -> None:
        self.config = config
        self.ffmpeg = ffmpeg
        self.process: subprocess.Popen[str] | None = None
        self.window: LeagueWindowInfo | None = None
        self.encoder: str | None = None
        self._log_thread: threading.Thread | None = None
        self._watch_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.last_error: str | None = None
        self._stderr_tail: deque[str] = deque(maxlen=12)
        self._stats_lock = threading.Lock()
        self._progress: dict[str, str] = {}
        self._capture_backend = "Idle"
        self._diagnostics = RecorderDiagnostics()

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, window: LeagueWindowInfo) -> None:
        if self.running:
            return
        self.ffmpeg.require()
        self.encoder = self.ffmpeg.choose_encoder()
        self.window = window
        self.last_error = None
        self._stderr_tail.clear()
        with self._stats_lock:
            self._progress.clear()
            self._capture_backend = "Starting"
            self._diagnostics = RecorderDiagnostics(
                encoder=self.encoder or "Unknown",
                capture_backend="Starting",
                hardware_encoder=(self.encoder or "") != "libx264",
            )
        self._stop_event.clear()
        self._clear_segments()

        # GDI window capture cannot reliably see DirectX-rendered game surfaces and
        # commonly produces a black video. Desktop Duplication captures the composed
        # monitor image instead, so use it even when the encoder is software-based.
        use_ddagrab = self.ffmpeg.supports_filter("ddagrab")

        if use_ddagrab:
            LOGGER.info(
                "Using Desktop Duplication capture (ddagrab) on output %s at %sx%s+%s+%s",
                window.monitor_index,
                window.width,
                window.height,
                window.offset_x,
                window.offset_y,
            )
            self._capture_backend = "Desktop Duplication"
            self._launch(
                self._build_command(
                    self._ddagrab_capture_args(window),
                    capture_kind="ddagrab",
                    window=window,
                )
            )
            time.sleep(1.2)
            if self.process is not None and self.process.poll() is not None:
                LOGGER.warning(
                    "ddagrab failed; retrying with GDI desktop-region capture "
                    "(never HWND capture, because DirectX windows can be black)"
                )
                self._close_finished_process()
                self.last_error = None
                self._clear_segments()
                self._capture_backend = "GDI desktop fallback"
                self._launch(
                    self._build_command(
                        self._gdi_desktop_capture_args(window),
                        capture_kind="gdi-desktop",
                        window=window,
                    )
                )
        else:
            LOGGER.warning(
                "This FFmpeg build has no ddagrab filter; using GDI desktop-region capture"
            )
            self._capture_backend = "GDI desktop fallback"
            self._launch(
                self._build_command(
                    self._gdi_desktop_capture_args(window),
                    capture_kind="gdi-desktop",
                    window=window,
                )
            )

        time.sleep(0.9)
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError(self.last_error or "FFmpeg stopped immediately after capture started.")

        self._watch_thread = threading.Thread(target=self._watch, name="SegmentWatch", daemon=True)
        self._watch_thread.start()

    def _launch(self, command: list[str]) -> None:
        LOGGER.info("Starting FFmpeg capture: %s", " ".join(command))
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        process = self.process
        self._log_thread = threading.Thread(
            target=self._read_logs_for_process,
            args=(process,),
            name="FFmpegLog",
            daemon=True,
        )
        self._log_thread.start()

    def _build_command(
        self,
        capture_args: list[str],
        *,
        capture_kind: str,
        window: LeagueWindowInfo,
    ) -> list[str]:
        assert self.ffmpeg.ffmpeg is not None
        assert self.encoder is not None
        output_pattern = self.config.temp_dir / "segment_%06d.mkv"
        return [
            str(self.ffmpeg.ffmpeg),
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostats",
            "-stats_period",
            "1",
            "-progress",
            "pipe:2",
            "-y",
            *capture_args,
            *self._capture_filter_arguments(capture_kind, window),
            "-fps_mode",
            "cfr",
            *self._encoder_arguments(self.encoder),
            "-g",
            str(self.config.fps * self.config.segment_seconds),
            "-keyint_min",
            str(self.config.fps * self.config.segment_seconds),
            "-sc_threshold",
            "0",
            "-force_key_frames",
            f"expr:gte(t,n_forced*{self.config.segment_seconds})",
            "-an",
            "-f",
            "segment",
            "-segment_format",
            "matroska",
            "-segment_time",
            str(self.config.segment_seconds),
            "-segment_time_delta",
            "0.05",
            "-reset_timestamps",
            "1",
            "-flush_packets",
            "1",
            str(output_pattern),
        ]

    def _ddagrab_capture_args(self, window: LeagueWindowInfo) -> list[str]:
        return [
            "-f",
            "lavfi",
            "-i",
            (
                f"ddagrab=output_idx={window.monitor_index}:"
                f"framerate={self.config.fps}:"
                f"draw_mouse={1 if self.config.draw_mouse else 0}:"
                f"video_size={window.width}x{window.height}:"
                f"offset_x={window.offset_x}:offset_y={window.offset_y}"
            ),
        ]

    def _gdi_desktop_capture_args(self, window: LeagueWindowInfo) -> list[str]:
        # Capture the visible desktop region instead of the HWND. GDI can read the
        # desktop composition in borderless/windowed mode, while a DirectX HWND
        # often returns only black pixels.
        return [
            "-rtbufsize",
            "512M",
            "-f",
            "gdigrab",
            "-framerate",
            str(self.config.fps),
            "-draw_mouse",
            "1" if self.config.draw_mouse else "0",
            "-offset_x",
            str(window.offset_x),
            "-offset_y",
            str(window.offset_y),
            "-video_size",
            f"{window.width}x{window.height}",
            "-i",
            "desktop",
        ]

    def _capture_filter_arguments(
        self,
        capture_kind: str,
        window: LeagueWindowInfo,
    ) -> list[str]:
        assert self.encoder is not None

        resize_needed = (
            abs(window.width - self.config.width) > 2
            or abs(window.height - self.config.height) > 2
        )
        scale_chain = (
            f"scale={self.config.width}:{self.config.height}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={self.config.width}:{self.config.height}:"
            "(ow-iw)/2:(oh-ih)/2:black,format=yuv420p"
        )

        if capture_kind == "ddagrab":
            # ddagrab outputs D3D11 hardware frames. NVENC can consume them
            # directly when no resize is required. Software encoders (and the
            # other compatibility paths) must explicitly download them first.
            if self.encoder == "h264_nvenc" and not resize_needed:
                return []
            return ["-vf", f"hwdownload,format=bgra,{scale_chain}"]

        return ["-vf", scale_chain]

    def _close_finished_process(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
        if process.stderr:
            process.stderr.close()
        if process.stdin:
            process.stdin.close()
        if self._log_thread and self._log_thread.is_alive():
            self._log_thread.join(timeout=1)

    def stop(self) -> None:
        self._stop_event.set()
        process = self.process
        self.process = None
        if process is None:
            return
        try:
            if process.poll() is None and process.stdin is not None:
                process.stdin.write("q\n")
                process.stdin.flush()
                process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        finally:
            if process.stderr:
                process.stderr.close()
            if process.stdin:
                process.stdin.close()
            with self._stats_lock:
                current = self._diagnostics
                self._capture_backend = "Idle"
                self._diagnostics = RecorderDiagnostics(
                    encoder=current.encoder,
                    capture_backend="Idle",
                    hardware_encoder=current.hardware_encoder,
                    frame=current.frame,
                    fps=current.fps,
                    duplicated_frames=current.duplicated_frames,
                    dropped_frames=current.dropped_frames,
                    drop_rate=current.drop_rate,
                    speed=current.speed,
                    updated_at=current.updated_at,
                )

    def diagnostics_snapshot(self) -> RecorderDiagnostics:
        with self._stats_lock:
            return self._diagnostics

    def all_segments(self) -> list[Path]:
        return sorted(self.config.temp_dir.glob("segment_*.mkv"))

    def completed_segments(self) -> list[Path]:
        files = self.all_segments()
        if self.running and files:
            return files[:-1]
        return files

    def wait_for_next_boundary(self, timeout: float | None = None) -> bool:
        timeout = timeout or (self.config.segment_seconds + 3)
        initial_files = self.all_segments()
        initial_latest = initial_files[-1] if initial_files else None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.running:
            files = self.all_segments()
            if files and initial_latest is not None and files[-1] != initial_latest:
                return True
            if initial_latest is None and len(files) >= 2:
                return True
            time.sleep(0.15)
        return False

    def enough_buffer(self) -> bool:
        required = math.ceil(self.config.buffer_seconds / self.config.segment_seconds)
        return len(self.completed_segments()) >= required

    def _clear_segments(self) -> None:
        self.config.temp_dir.mkdir(parents=True, exist_ok=True)
        for path in self.config.temp_dir.glob("segment_*.mkv"):
            try:
                path.unlink()
            except OSError:
                LOGGER.warning("Could not delete old buffer segment %s", path)

    def _watch(self) -> None:
        max_files = math.ceil((self.config.buffer_seconds + 30) / self.config.segment_seconds) + 2
        while not self._stop_event.wait(1.0):
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

            files = self.all_segments()
            completed = files[:-1] if len(files) > 1 else []
            overflow = len(completed) - max_files
            for path in completed[: max(0, overflow)]:
                try:
                    path.unlink()
                except OSError:
                    LOGGER.debug("Could not delete rolling segment %s", path, exc_info=True)

    def _read_logs_for_process(self, process: subprocess.Popen[str]) -> None:
        if process.stderr is None:
            return
        for raw_line in process.stderr:
            line = raw_line.strip()
            if not line:
                continue
            if self._consume_progress_line(line):
                continue
            self._stderr_tail.append(line)
            LOGGER.warning("FFmpeg: %s", line)
            lowered = line.lower()
            if "error" in lowered or "failed" in lowered or "cannot" in lowered:
                self.last_error = line

    def _consume_progress_line(self, line: str) -> bool:
        if "=" not in line:
            return False
        key, value = line.split("=", 1)
        known = {
            "frame", "fps", "dup_frames", "drop_frames", "speed",
            "out_time_us", "out_time_ms", "out_time", "progress",
            "bitrate", "total_size", "stream_0_0_q",
        }
        if key not in known:
            return False
        with self._stats_lock:
            self._progress[key] = value
            if key == "progress":
                self._publish_progress_locked()
        return True

    def _publish_progress_locked(self) -> None:
        def as_int(key: str) -> int:
            try:
                return max(0, int(float(self._progress.get(key, "0"))))
            except (TypeError, ValueError):
                return 0

        def as_float(key: str) -> float:
            value = self._progress.get(key, "0").strip().lower().removesuffix("x")
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                return 0.0

        frame = as_int("frame")
        dropped = as_int("drop_frames")
        duplicated = as_int("dup_frames")
        expected = max(1, frame + dropped)
        drop_rate = (dropped / expected) * 100.0
        self._diagnostics = RecorderDiagnostics(
            encoder=self.encoder or "Unknown",
            capture_backend=self._capture_backend,
            hardware_encoder=(self.encoder or "") != "libx264",
            frame=frame,
            fps=as_float("fps"),
            duplicated_frames=duplicated,
            dropped_frames=dropped,
            drop_rate=drop_rate,
            speed=as_float("speed"),
            updated_at=time.time(),
        )

    def _encoder_arguments(self, encoder: str) -> list[str]:
        if encoder == "h264_nvenc":
            return [
                "-c:v",
                encoder,
                "-preset",
                "p4",
                "-tune",
                "hq",
                "-rc",
                "vbr",
                "-cq",
                str(self.config.quality),
                "-b:v",
                "0",
                "-maxrate",
                "20M",
                "-bufsize",
                "40M",
            ]
        if encoder == "h264_qsv":
            return ["-c:v", encoder, "-preset", "medium", "-global_quality", str(self.config.quality)]
        if encoder == "h264_amf":
            return [
                "-c:v",
                encoder,
                "-quality",
                "speed",
                "-rc",
                "cqp",
                "-qp_i",
                str(self.config.quality),
                "-qp_p",
                str(self.config.quality),
            ]
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(self.config.quality)]
