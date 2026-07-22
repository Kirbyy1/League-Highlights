from __future__ import annotations

import json
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from app.highlight_event import StoredHighlightEvent
from app.models import ClipInfo
from app.services.ffmpeg_tools import FfmpegTools
from app.services.smart_trim_service import SmartTrimService

LOGGER = logging.getLogger(__name__)


class ClipTrimmer:
    """Create a trimmed clip while preserving the original video/audio streams."""

    def __init__(self, ffmpeg: FfmpegTools) -> None:
        self.ffmpeg = ffmpeg

    def trim(
        self,
        clip: ClipInfo,
        start_seconds: float,
        end_seconds: float,
        *,
        replace_original: bool = False,
    ) -> Path:
        self.ffmpeg.require()
        start = max(0.0, float(start_seconds))
        end = min(float(clip.duration_seconds), float(end_seconds))
        if end - start < 0.25:
            raise ValueError("The trimmed clip must be at least 0.25 seconds long.")

        source = clip.path
        if replace_original:
            output = source.with_name(f"{source.stem}.trim-working{source.suffix}")
        else:
            output = self._next_copy_path(source)

        command = [
            str(self.ffmpeg.ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(source),
            "-t",
            f"{end - start:.3f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            "-movflags",
            "+faststart",
            str(output),
        ]
        self.ffmpeg.run(command, timeout=180, low_priority=True)

        actual_duration = self.ffmpeg.probe_duration(output)
        if actual_duration <= 0:
            output.unlink(missing_ok=True)
            raise RuntimeError("FFmpeg created an empty trimmed clip.")

        if replace_original:
            backup = source.with_name(f"{source.stem}.trim-backup{source.suffix}")
            try:
                self._replace_with_retry(source, backup)
                self._replace_with_retry(output, source)
                backup.unlink(missing_ok=True)
            except Exception:
                if backup.exists() and not source.exists():
                    try:
                        self._replace_with_retry(backup, source)
                    except Exception:
                        LOGGER.exception("Could not restore original clip from %s", backup)
                output.unlink(missing_ok=True)
                raise
            final_path = source
        else:
            final_path = output

        self._write_metadata(clip, final_path, start, end, actual_duration)
        self._write_thumbnail(final_path, min(max(0.1, actual_duration * 0.45), max(0.1, actual_duration - 0.1)))

        if replace_original:
            old_thumb = source.with_suffix(".jpg")
            # The new thumbnail already uses the same path. Nothing else to clean.
            if not old_thumb.exists():
                LOGGER.warning("No thumbnail was generated for %s", source)

        return final_path

    @staticmethod
    def _replace_with_retry(source: Path, target: Path, timeout_seconds: float = 4.0) -> None:
        """Atomically replace a file, tolerating short-lived Windows media locks."""

        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        delay = 0.08
        while True:
            try:
                os.replace(source, target)
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(delay)
                delay = min(0.25, delay * 1.35)

    @staticmethod
    def _next_copy_path(source: Path) -> Path:
        candidate = source.with_name(f"{source.stem}_trimmed{source.suffix}")
        index = 2
        while candidate.exists():
            candidate = source.with_name(f"{source.stem}_trimmed_{index}{source.suffix}")
            index += 1
        return candidate

    def _write_metadata(
        self,
        clip: ClipInfo,
        output: Path,
        start: float,
        end: float,
        actual_duration: float,
    ) -> None:
        source_metadata_path = clip.path.with_suffix(".json")
        metadata: dict = {}
        if source_metadata_path.exists():
            try:
                parsed = json.loads(source_metadata_path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    metadata = parsed
            except (OSError, json.JSONDecodeError):
                LOGGER.warning("Could not read metadata for %s", clip.path)

        original_events = [
            event
            for raw in metadata.get("events", [])
            if (event := StoredHighlightEvent.from_json(raw)) is not None
        ]
        shifted_events = [
            StoredHighlightEvent(
                relative_time=event.relative_time - start,
                event_type=event.event_type,
                game_time=event.game_time,
            )
            for event in original_events
            if start <= event.relative_time <= end
        ]
        raw_trigger = metadata.get("trigger_relative_seconds")
        try:
            trigger = float(raw_trigger) - start if raw_trigger is not None else actual_duration
        except (TypeError, ValueError):
            trigger = actual_duration
        trigger = max(0.0, min(float(actual_duration), trigger))
        suggestion = SmartTrimService().suggest(
            actual_duration,
            shifted_events,
            trigger,
            manual=str(metadata.get("event_kind", "manual")) == "manual",
        )

        metadata.update(
            {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "duration_seconds": round(float(actual_duration), 3),
                "trimmed": True,
                "trim_start_seconds": round(start, 3),
                "trim_end_seconds": round(end, 3),
                "trimmed_from": clip.path.name,
                "discord_ready": output.stat().st_size < 10_000_000,
                "events": [event.to_json() for event in shifted_events],
                "trigger_relative_seconds": round(trigger, 3),
                "suggested_trim_start": round(suggestion.start_seconds, 3),
                "suggested_trim_end": round(suggestion.end_seconds, 3),
                "smart_trim_reason": suggestion.reason,
            }
        )

        if output == clip.path:
            # Preserve the clip's original creation/group ordering when replacing it.
            metadata["created_at"] = clip.created_at.isoformat(timespec="seconds")
            if clip.clip_window_start_wall is not None:
                metadata["clip_window_start_wall"] = clip.clip_window_start_wall + start
            if clip.clip_window_end_wall is not None:
                metadata["clip_window_end_wall"] = min(
                    clip.clip_window_end_wall,
                    (clip.clip_window_start_wall or clip.clip_window_end_wall) + end,
                )
        else:
            if clip.clip_window_start_wall is not None:
                metadata["clip_window_start_wall"] = clip.clip_window_start_wall + start
            if clip.clip_window_start_wall is not None:
                metadata["clip_window_end_wall"] = clip.clip_window_start_wall + end

        output.with_suffix(".json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    def _write_thumbnail(self, video: Path, at_seconds: float) -> None:
        assert self.ffmpeg.ffmpeg is not None
        thumbnail = video.with_suffix(".jpg")
        command = [
            str(self.ffmpeg.ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{at_seconds:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(thumbnail),
        ]
        try:
            self.ffmpeg.run(command, timeout=45, low_priority=True)
        except Exception:
            LOGGER.exception("Could not create thumbnail for trimmed clip %s", video)
            if video != thumbnail:
                thumbnail.unlink(missing_ok=True)
