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

DISCORD_TARGET_BYTES = int(9.3 * 1024 * 1024)
AUDIO_BITRATE_BPS = 96_000
CONTAINER_SAFETY_RATIO = 0.96


class DiscordExportError(RuntimeError):
    pass


class DiscordExportCancelled(DiscordExportError):
    pass


class ClipTooLongForDiscord(DiscordExportError):
    pass


@dataclass(slots=True, frozen=True)
class DiscordProfile:
    width: int
    height: int
    fps: int
    minimum_video_bitrate_bps: int
    maximum_video_bitrate_bps: int
    label: str


@dataclass(slots=True, frozen=True)
class DiscordExportPlan:
    duration_seconds: float
    target_bytes: int
    profile: DiscordProfile
    video_bitrate_bps: int
    audio_bitrate_bps: int = AUDIO_BITRATE_BPS

    @property
    def estimated_size_bytes(self) -> int:
        media_bits = (self.video_bitrate_bps + self.audio_bitrate_bps) * self.duration_seconds
        return min(self.target_bytes, int(media_bits / 8 / CONTAINER_SAFETY_RATIO))


@dataclass(slots=True, frozen=True)
class DiscordExportResult:
    output_path: Path
    actual_size_bytes: int
    actual_duration_seconds: float
    plan: DiscordExportPlan
    retried: bool
    sent_to_discord: bool = False
    discord_message_id: str = ""
    send_error: str = ""


PROFILES: tuple[DiscordProfile, ...] = (
    DiscordProfile(1920, 1080, 60, 4_500_000, 14_000_000, "1080p60"),
    DiscordProfile(1280, 720, 60, 2_500_000, 9_000_000, "720p60"),
    DiscordProfile(1280, 720, 30, 1_500_000, 6_000_000, "720p30"),
)


class DiscordExportService:
    """Create a size-targeted Discord copy only when explicitly requested."""

    def __init__(self, ffmpeg: FfmpegTools) -> None:
        self.ffmpeg = ffmpeg
        self._lock = threading.Lock()
        self._active: dict[Path, tuple[threading.Event, subprocess.Popen[str] | None]] = {}

    @staticmethod
    def available_video_bitrate_bps(duration_seconds: float, target_bytes: int) -> int:
        duration = float(duration_seconds)
        if duration <= 0:
            raise ValueError("Selected duration must be positive.")
        available_total_bits = int(target_bytes) * 8
        return int(
            (available_total_bits * CONTAINER_SAFETY_RATIO / duration)
            - AUDIO_BITRATE_BPS
        )

    def plan(self, duration_seconds: float, target_bytes: int = DISCORD_TARGET_BYTES) -> DiscordExportPlan:
        duration = float(duration_seconds)
        target = max(1_000_000, int(target_bytes))
        available = self.available_video_bitrate_bps(duration, target)
        for profile in PROFILES:
            if available >= profile.minimum_video_bitrate_bps:
                bitrate = min(available, profile.maximum_video_bitrate_bps)
                return DiscordExportPlan(duration, target, profile, bitrate)
        raise ClipTooLongForDiscord(
            "This clip is too long to export clearly under Discord’s free upload limit. "
            "Trim the clip further and try again."
        )

    @staticmethod
    def retry_bitrate(previous_bitrate: int, target_bytes: int, actual_output_bytes: int) -> int:
        if previous_bitrate <= 0 or target_bytes <= 0 or actual_output_bytes <= 0:
            raise ValueError("Bitrate and file sizes must be positive.")
        return int(previous_bitrate * target_bytes / actual_output_bytes * 0.97)

    @staticmethod
    def output_path_for(source: Path) -> Path:
        base = source.with_name(f"{source.stem}_discord.mp4")
        if not base.exists():
            return base
        index = 2
        while True:
            candidate = source.with_name(f"{source.stem}_discord_{index}.mp4")
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
            try:
                process.terminate()
            except OSError:
                pass
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
        target_bytes: int = DISCORD_TARGET_BYTES,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> DiscordExportResult:
        self.ffmpeg.require()
        assert self.ffmpeg.ffmpeg is not None

        source = Path(source)
        if not source.exists():
            raise DiscordExportError("The original highlight file could not be found.")
        start = max(0.0, float(start_seconds))
        end = float(end_seconds)
        duration = end - start
        if duration < 0.25:
            raise DiscordExportError("Select at least 0.25 seconds before exporting.")

        plan = self.plan(duration, target_bytes)
        output = self.output_path_for(source)
        working = output.with_name(f"{output.stem}.working{output.suffix}")
        key = source.resolve()
        cancel_event = threading.Event()
        with self._lock:
            if key in self._active:
                raise DiscordExportError("A Discord export is already running for this clip.")
            self._active[key] = (cancel_event, None)

        try:
            required_free = max(plan.target_bytes * 3, 64 * 1024 * 1024)
            if shutil.disk_usage(output.parent).free < required_free:
                raise DiscordExportError("Not enough free disk space to create the Discord copy.")

            retried = False
            bitrate = plan.video_bitrate_bps
            for attempt in range(2):
                working.unlink(missing_ok=True)
                current_plan = DiscordExportPlan(
                    duration,
                    plan.target_bytes,
                    plan.profile,
                    bitrate,
                    plan.audio_bitrate_bps,
                )
                self._run_ffmpeg(
                    source,
                    working,
                    start,
                    duration,
                    current_plan,
                    cancel_event,
                    progress_callback,
                )
                self._validate_duration(working, duration)
                actual_size = working.stat().st_size
                if actual_size <= plan.target_bytes:
                    os.replace(working, output)
                    actual_duration = self.ffmpeg.probe_duration(output)
                    output.with_suffix(".json").write_text(
                        json.dumps(
                            {
                                "is_discord_copy": True,
                                "source_file": source.name,
                                "created_at": datetime.now().isoformat(timespec="seconds"),
                                "duration_seconds": actual_duration,
                                "size_bytes": actual_size,
                                "discord_ready": True,
                                "discord_profile": current_plan.profile.label,
                                "discord_video_bitrate_bps": current_plan.video_bitrate_bps,
                                "discord_audio_bitrate_bps": current_plan.audio_bitrate_bps,
                                "discord_target_bytes": current_plan.target_bytes,
                                "trim_start_seconds": start,
                                "trim_end_seconds": end,
                            },
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    if progress_callback:
                        progress_callback(100, "Discord copy ready")
                    return DiscordExportResult(
                        output,
                        actual_size,
                        actual_duration,
                        current_plan,
                        retried,
                    )

                if attempt == 0:
                    adjusted = self.retry_bitrate(bitrate, plan.target_bytes, actual_size)
                    if adjusted < plan.profile.minimum_video_bitrate_bps:
                        raise ClipTooLongForDiscord(
                            "This clip is too long to export clearly under Discord’s free upload limit. "
                            "Trim the clip further and try again."
                        )
                    bitrate = adjusted
                    retried = True
                    if progress_callback:
                        progress_callback(0, "Adjusting bitrate and retrying once…")
                    continue

                raise DiscordExportError(
                    "The Discord copy was still above the configured upload limit after retrying."
                )

            raise DiscordExportError("Discord export failed.")
        except DiscordExportCancelled:
            raise
        except Exception:
            LOGGER.exception("Discord export failed for %s", source)
            raise
        finally:
            working.unlink(missing_ok=True)
            with self._lock:
                self._active.pop(key, None)

    def _run_ffmpeg(
        self,
        source: Path,
        output: Path,
        start: float,
        duration: float,
        plan: DiscordExportPlan,
        cancel_event: threading.Event,
        progress_callback: Callable[[int, str], None] | None,
    ) -> None:
        assert self.ffmpeg.ffmpeg is not None
        profile = plan.profile
        width = profile.width - profile.width % 2
        height = profile.height - profile.height % 2
        filter_value = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
        bitrate = int(plan.video_bitrate_bps)
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
            "-b:v",
            str(bitrate),
            "-maxrate",
            str(bitrate),
            "-bufsize",
            str(bitrate * 2),
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(profile.fps),
            "-vf",
            filter_value,
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-movflags",
            "+faststart",
            "-progress",
            "pipe:1",
            "-nostats",
            str(output),
        ]

        creationflags = CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            creationflags=creationflags,
        )
        key = source.resolve()
        with self._lock:
            if key in self._active:
                self._active[key] = (cancel_event, process)

        stderr_lines: list[str] = []

        def read_stderr() -> None:
            if process.stderr is None:
                return
            for line in process.stderr:
                stderr_lines.append(line.rstrip())

        stderr_thread = threading.Thread(target=read_stderr, name="DiscordExportStderr", daemon=True)
        stderr_thread.start()

        try:
            if process.stdout is not None:
                for raw_line in process.stdout:
                    if cancel_event.is_set():
                        self._terminate_process(process)
                        raise DiscordExportCancelled("Discord export cancelled.")
                    key_name, _, raw_value = raw_line.strip().partition("=")
                    if key_name in {"out_time_ms", "out_time_us"}:
                        try:
                            microseconds = int(raw_value)
                        except ValueError:
                            continue
                        percent = min(99, max(0, int(microseconds / 1_000_000 / duration * 100)))
                        if progress_callback:
                            progress_callback(percent, f"Encoding {profile.label}…")
            return_code = process.wait()
            stderr_thread.join(timeout=1.0)
            if cancel_event.is_set():
                raise DiscordExportCancelled("Discord export cancelled.")
            if return_code != 0:
                error_text = "\n".join(stderr_lines[-30:]).strip()
                raise DiscordExportError(error_text or f"FFmpeg exited with code {return_code}.")
        finally:
            if process.poll() is None:
                self._terminate_process(process)

    def _validate_duration(self, output: Path, wanted_duration: float) -> None:
        if not output.exists() or output.stat().st_size <= 0:
            raise DiscordExportError("FFmpeg did not create a usable Discord copy.")
        actual = self.ffmpeg.probe_duration(output)
        tolerance = max(0.75, min(2.0, wanted_duration * 0.06))
        if abs(actual - wanted_duration) > tolerance:
            raise DiscordExportError(
                f"The exported duration was invalid ({actual:.2f}s instead of {wanted_duration:.2f}s)."
            )

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
