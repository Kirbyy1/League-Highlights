from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.config import AppConfig
from app.highlight_event import StoredHighlightEvent
from app.models import ClipInfo, HighlightRequest
from app.services.audio_loopback import LoopbackAudioBuffer
from app.services.discord_profile import target_duration_seconds
from app.services.ffmpeg_tools import FfmpegTools
from app.services.highlight_event_tracker import HighlightEventTracker
from app.services.smart_trim_service import SmartTrimService
from app.services.video_recorder import VideoSegmentRecorder

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class _SegmentTiming:
    path: Path
    duration: float
    start_wall: float
    end_wall: float


class ClipExporter:
    """Turns rolling segments into clips without a second lossy video encode."""

    def __init__(
        self,
        config: AppConfig,
        ffmpeg: FfmpegTools,
        video: VideoSegmentRecorder,
        audio: LoopbackAudioBuffer,
        event_tracker: HighlightEventTracker | None = None,
    ) -> None:
        self.config = config
        self.ffmpeg = ffmpeg
        self.video = video
        self.audio = audio
        self.event_tracker = event_tracker or HighlightEventTracker(config.buffer_seconds + 120)
        self.smart_trim = SmartTrimService()
        self._lock = threading.Lock()

    def export_last_buffer(self, label: str = "MANUAL CLIP") -> ClipInfo:
        return self.export_request(HighlightRequest(label=label))

    def export_request(self, request: HighlightRequest) -> ClipInfo:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A clip is already being saved.")
        try:
            return self._export_locked(request)
        finally:
            self._lock.release()

    def _export_locked(self, request: HighlightRequest) -> ClipInfo:
        if not self.video.running:
            raise RuntimeError("Recording is not active.")

        clean_label = request.clean_label
        use_precise_window = bool(
            request.has_precise_window and self.config.discord_auto_trim_events
        )

        # Objectives are emitted immediately. Wait until the requested post-action
        # footage exists. Kill chains are normally emitted after Riot's multikill
        # settle window, so this wait is usually already complete.
        if use_precise_window and request.desired_end_wall is not None:
            wait_seconds = request.desired_end_wall - time.time()
            if wait_seconds > 0:
                LOGGER.info("Waiting %.1fs for post-event footage", wait_seconds)
                time.sleep(min(wait_seconds + 0.15, self.config.buffer_seconds))

        # Close the newest segment so it is safe to concatenate while recording
        # continues into the next segment.
        self.video.wait_for_next_boundary()
        completed = self.video.completed_segments()
        if not completed:
            raise RuntimeError("No completed video segments are available yet.")

        timeline = self._segment_timeline(completed)
        if not timeline:
            raise RuntimeError("The rolling buffer has no readable video segments.")

        if use_precise_window:
            selected, trim_start, trim_duration = self._select_precise_window(
                timeline,
                request,
            )
            requested_seconds = trim_duration
        else:
            requested_seconds = target_duration_seconds(
                clean_label,
                self.config.buffer_seconds,
                self.config.discord_auto_trim_events,
            )
            selected, trim_start, trim_duration = self._select_tail_window(
                timeline,
                requested_seconds,
            )

        if trim_duration < 3:
            raise RuntimeError(
                f"The recording buffer is still warming up. Try {self.config.hotkey_display} again in a few seconds."
            )

        selected_start_wall = selected[0].start_wall
        selected_end_wall = selected[-1].end_wall
        selected_duration = selected_end_wall - selected_start_wall

        created_at = datetime.now()
        filename_label = re.sub(r"[^A-Z0-9]+", "_", clean_label).strip("_") or "HIGHLIGHT"
        champion_label = re.sub(
            r"[^A-Za-z0-9]+", "_", request.champion_name or ""
        ).strip("_")
        prefix = f"{champion_label}_{filename_label}" if champion_label else filename_label
        filename = created_at.strftime(f"{prefix}_%Y-%m-%d_%H-%M-%S.mp4")
        output_path = self._unique_path(self.config.clip_dir / filename)
        thumbnail_path = output_path.with_suffix(".jpg")
        metadata_path = output_path.with_suffix(".json")

        audio_included = False
        system_audio_included = False
        microphone_included = False
        audio_sources: list[str] = []

        with tempfile.TemporaryDirectory(prefix="league_highlight_") as temp_name:
            temp_dir = Path(temp_name)
            concat_file = temp_dir / "segments.txt"
            joined_video = temp_dir / "joined_video.mkv"
            source_clip = temp_dir / "source_clip.mp4"

            concat_file.write_text(
                "".join(f"file '{self._concat_escape(item.path)}'\n" for item in selected),
                encoding="utf-8",
            )
            assert self.ffmpeg.ffmpeg is not None
            self.ffmpeg.run(
                [
                    str(self.ffmpeg.ffmpeg),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_file),
                    "-c",
                    "copy",
                    "-fflags",
                    "+genpts",
                    "-avoid_negative_ts",
                    "make_zero",
                    str(joined_video),
                ],
                timeout=90,
            )

            # Export system and microphone audio separately, then mix only the
            # enabled sources. Video stays stream-copied, so audio controls do not
            # reduce gameplay image quality.
            try:
                audio_export = self.audio.write_interval(
                    selected_start_wall,
                    selected_end_wall,
                    temp_dir,
                )
                audio_inputs: list[Path] = []
                gains: list[float] = []
                labels: list[str] = []
                if audio_export.system_path is not None:
                    audio_inputs.append(audio_export.system_path)
                    gains.append(self.config.system_audio_volume / 100.0)
                    labels.append("system")
                    system_audio_included = True
                if audio_export.microphone_path is not None:
                    audio_inputs.append(audio_export.microphone_path)
                    gains.append(self.config.microphone_volume / 100.0)
                    labels.append("microphone")
                    microphone_included = True

                if audio_inputs:
                    command = [
                        str(self.ffmpeg.ffmpeg),
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(joined_video),
                    ]
                    for path in audio_inputs:
                        command.extend(["-i", str(path)])

                    command.extend(["-map", "0:v:0", "-c:v", "copy"])
                    # Normalize every captured source to one stable 48 kHz stereo
                    # format before encoding. The old per-callback wall-clock WAV
                    # placement could introduce tiny gaps; aresample only handles
                    # genuine device-clock drift here, not Python scheduling jitter.
                    common_audio = (
                        "aresample=48000:async=1000:first_pts=0,"
                        "aformat=sample_fmts=fltp:sample_rates=48000:"
                        "channel_layouts=stereo"
                    )
                    if len(audio_inputs) == 1:
                        single_filter = f"{common_audio},volume={gains[0]:.3f}"
                        # Only use a limiter when the user boosts above 100%; at
                        # normal gain, avoiding unnecessary dynamics processing
                        # keeps game audio cleaner and more natural.
                        if gains[0] > 1.0:
                            single_filter += ",alimiter=limit=0.98"
                        command.extend(
                            [
                                "-map",
                                "1:a:0",
                                "-af",
                                single_filter,
                            ]
                        )
                    else:
                        filters: list[str] = []
                        mix_labels: list[str] = []
                        for index, gain in enumerate(gains, start=1):
                            output_label = f"a{index}"
                            filters.append(
                                f"[{index}:a]{common_audio},volume={gain:.3f}"
                                f"[{output_label}]"
                            )
                            mix_labels.append(f"[{output_label}]")
                        filters.append(
                            "".join(mix_labels)
                            + f"amix=inputs={len(mix_labels)}:duration=longest:"
                            "dropout_transition=0:normalize=0,"
                            "alimiter=limit=0.94[aout]"
                        )
                        command.extend(
                            [
                                "-filter_complex",
                                ";".join(filters),
                                "-map",
                                "[aout]",
                            ]
                        )

                    command.extend(
                        [
                            "-c:a",
                            "aac",
                            "-b:a",
                            f"{self.config.audio_bitrate_kbps}k",
                            "-ar",
                            "48000",
                            "-ac",
                            "2",
                            "-movflags",
                            "+faststart",
                            str(source_clip),
                        ]
                    )
                    self.ffmpeg.run(command, timeout=120)
                    audio_included = True
                    audio_sources = labels
            except Exception:
                LOGGER.exception("Could not attach configured audio; exporting video-only clip")

            if not audio_included:
                self.ffmpeg.run(
                    [
                        str(self.ffmpeg.ffmpeg),
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(joined_video),
                        "-c:v",
                        "copy",
                        "-an",
                        "-movflags",
                        "+faststart",
                        str(source_clip),
                    ],
                    timeout=90,
                )

            # Do not run the old Discord-size transcode. It downscaled and
            # recompressed fast-moving gameplay. The selected rolling segments are
            # already H.264, so copying this MP4 preserves their original pixels.
            # Precise event selection still happens at the segment level; clips may
            # include a few extra seconds at either edge rather than losing quality.
            shutil.copy2(source_clip, output_path)

        final_duration = self._safe_duration(output_path)
        final_size = output_path.stat().st_size
        discord_ready = final_size < 10_000_000
        clip_window_start_wall = selected_start_wall + trim_start
        clip_window_end_wall = clip_window_start_wall + trim_duration

        # The written MP4 contains the selected complete segments. Event-relative
        # timestamps therefore use ``selected_start_wall`` (the actual first video
        # frame), while clip_window_start_wall remains the requested match position
        # used by the game highlight navigator.
        trigger_wall = (
            request.triggered_at_wall
            or request.event_ended_at
            or request.event_started_at
            or selected_end_wall
        )
        trigger_relative = max(0.0, min(final_duration, float(trigger_wall) - selected_start_wall))
        stored_events = list(
            self.event_tracker.events_for_clip(
                selected_start_wall,
                selected_end_wall,
                match_id=request.match_id,
            )
        )
        expected_type = self._request_event_type(request)
        if expected_type and not any(
            event.event_type == expected_type and abs(event.relative_time - trigger_relative) <= 1.5
            for event in stored_events
        ):
            stored_events.append(
                StoredHighlightEvent(
                    relative_time=trigger_relative,
                    event_type=expected_type,
                    game_time=request.event_game_time,
                )
            )
            stored_events.sort(key=lambda item: item.relative_time)

        suggestion = self.smart_trim.suggest(
            final_duration,
            stored_events,
            trigger_relative,
            manual=request.event_kind == "manual" or not request.automatic,
        )
        self._generate_thumbnail(output_path, thumbnail_path, final_duration)
        metadata_path.write_text(
            json.dumps(
                {
                    "label": clean_label,
                    "created_at": created_at.isoformat(),
                    "duration_seconds": final_duration,
                    "match_id": request.match_id,
                    "player_name": request.player_name,
                    "champion_name": request.champion_name,
                    "game_mode": request.game_mode,
                    "match_started_at": (self._match_started_at(request.match_id)),
                    "event_kind": request.event_kind,
                    "event_game_time": request.event_game_time,
                    "automatic": request.automatic,
                    "trigger_relative_seconds": round(trigger_relative, 3),
                    "events": [event.to_json() for event in stored_events],
                    "suggested_trim_start": round(suggestion.start_seconds, 3),
                    "suggested_trim_end": round(suggestion.end_seconds, 3),
                    "smart_trim_reason": suggestion.reason,
                    "video_buffer_start_wall": selected_start_wall,
                    "video_buffer_end_wall": selected_end_wall,
                    "highlight_score": request.highlight_score,
                    "score_reasons": list(request.score_reasons),
                    "victim_names": list(request.victim_names),
                    "victim_champions": list(request.victim_champions),
                    "assister_names": list(request.assister_names),
                    "event_started_at": request.event_started_at,
                    "event_ended_at": request.event_ended_at,
                    "clip_window_start_wall": clip_window_start_wall,
                    "clip_window_end_wall": clip_window_end_wall,
                    "is_match_reel": False,
                    "rating": "",
                    "requested_duration_seconds": requested_seconds,
                    "selected_buffer_seconds": selected_duration,
                    "trim_start_seconds": trim_start,
                    "precise_event_window": use_precise_window,
                    "audio_included": audio_included,
                    "system_audio_included": system_audio_included,
                    "microphone_included": microphone_included,
                    "audio_sources": audio_sources,
                    "system_audio_device": self.audio.system_device_name,
                    "microphone_device": self.audio.microphone_device_name,
                    "system_audio_volume": self.config.system_audio_volume,
                    "microphone_volume": self.config.microphone_volume,
                    "size_bytes": final_size,
                    "discord_mode": False,
                    "discord_compressed": False,
                    "discord_ready": discord_ready,
                    "discord_target_mb": self.config.discord_target_mb,
                    "discord_resolution": None,
                    "discord_fps": None,
                    "discord_profile": None,
                    "quality_mode": "original_recording",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        LOGGER.info(
            "Saved clip to %s (%s bytes, %.1fs, Discord ready=%s)",
            output_path,
            final_size,
            final_duration,
            discord_ready,
        )
        return ClipInfo(
            path=output_path,
            thumbnail_path=thumbnail_path if thumbnail_path.exists() else None,
            created_at=created_at,
            duration_seconds=final_duration,
            label=clean_label,
            audio_included=audio_included,
            system_audio_included=system_audio_included,
            microphone_included=microphone_included,
            size_bytes=final_size,
            discord_ready=discord_ready,
            match_id=request.match_id,
            player_name=request.player_name,
            champion_name=request.champion_name,
            game_mode=request.game_mode,
            event_kind=request.event_kind,
            trigger_relative_seconds=trigger_relative,
            events=tuple(stored_events),
            suggested_trim_start=suggestion.start_seconds,
            suggested_trim_end=suggestion.end_seconds,
            match_started_at=self._match_started_at(request.match_id),
            highlight_score=request.highlight_score,
            score_reasons=request.score_reasons,
            victim_names=request.victim_names,
            rating="",
            is_match_reel=False,
            clip_window_start_wall=clip_window_start_wall,
            clip_window_end_wall=clip_window_end_wall,
        )

    @staticmethod
    def _request_event_type(request: HighlightRequest) -> str:
        if request.event_kind == "manual" or not request.automatic:
            return "MANUAL_TRIGGER"
        label = request.clean_label
        if "BARON" in label:
            return "BARON_STEAL" if "STEAL" in label else "BARON"
        if "DRAGON" in label:
            return "DRAGON_STEAL" if "STEAL" in label else "DRAGON"
        if any(token in label for token in ("DOUBLE", "TRIPLE", "QUADRA", "PENTA")):
            return "MULTIKILL"
        if "KILL" in label:
            return "CHAMPION_KILL"
        return str(request.event_kind or "HIGHLIGHT").strip().upper()

    @staticmethod
    def _match_started_at(match_id: str) -> float | None:
        if not match_id:
            return None
        try:
            stamp = match_id.split("_", 2)[:2]
            return datetime.strptime("_".join(stamp), "%Y%m%d_%H%M%S").timestamp()
        except (ValueError, IndexError):
            return None

    def _segment_timeline(self, paths: list[Path]) -> list[_SegmentTiming]:
        readable: list[tuple[Path, float]] = []
        for path in paths:
            duration = self._safe_duration(path)
            if duration > 0.1:
                readable.append((path, duration))
        if not readable:
            return []

        # FFmpeg closes segments in order. Reconstruct one continuous wall-clock
        # timeline backwards from the last file closure instead of trusting each
        # individual filesystem timestamp, which can vary by a few milliseconds.
        cursor = readable[-1][0].stat().st_mtime
        reversed_timeline: list[_SegmentTiming] = []
        for path, duration in reversed(readable):
            start = cursor - duration
            reversed_timeline.append(_SegmentTiming(path, duration, start, cursor))
            cursor = start
        return list(reversed(reversed_timeline))

    def _select_precise_window(
        self,
        timeline: list[_SegmentTiming],
        request: HighlightRequest,
    ) -> tuple[list[_SegmentTiming], float, float]:
        desired_start = request.desired_start_wall
        desired_end = request.desired_end_wall
        if desired_start is None or desired_end is None:
            raise RuntimeError("The event timing information is incomplete.")

        available_start = timeline[0].start_wall
        available_end = timeline[-1].end_wall
        start = max(desired_start, available_start)
        end = min(desired_end, available_end)
        if end <= start:
            raise RuntimeError("The event is no longer inside the rolling buffer.")
        if start > desired_start + 0.2:
            LOGGER.warning("The beginning of the event clip was limited by the current buffer")
        if end < desired_end - 0.2:
            LOGGER.warning("The end of the event clip was limited by available footage")

        selected = [item for item in timeline if item.end_wall > start and item.start_wall < end]
        if not selected:
            raise RuntimeError("Could not locate the event inside the rolling buffer.")
        selected_start = selected[0].start_wall
        trim_start = max(0.0, start - selected_start)
        trim_duration = max(0.0, end - start)
        return selected, trim_start, trim_duration

    def _select_tail_window(
        self,
        timeline: list[_SegmentTiming],
        requested_seconds: float,
    ) -> tuple[list[_SegmentTiming], float, float]:
        required = max(1, math.ceil(float(requested_seconds) / self.config.segment_seconds))
        selected = timeline[-required:]
        total = selected[-1].end_wall - selected[0].start_wall
        trim_duration = min(float(requested_seconds), total)
        trim_start = max(0.0, total - trim_duration)
        return selected, trim_start, trim_duration

    def _generate_thumbnail(self, video_path: Path, output_path: Path, duration: float) -> None:
        assert self.ffmpeg.ffmpeg is not None
        seek = max(0.0, min(2.0, duration / 3))
        try:
            self.ffmpeg.run(
                [
                    str(self.ffmpeg.ffmpeg),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{seek:.2f}",
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=480:-2",
                    "-q:v",
                    "3",
                    str(output_path),
                ],
                timeout=30,
            )
        except Exception:
            LOGGER.exception("Could not generate clip thumbnail")

    def _safe_duration(self, path: Path) -> float:
        try:
            return self.ffmpeg.probe_duration(path)
        except Exception:
            LOGGER.debug("Duration probe failed for %s", path, exc_info=True)
            return float(self.config.segment_seconds)

    @staticmethod
    def _concat_escape(path: Path) -> str:
        return path.resolve().as_posix().replace("'", "'\\''")

    @staticmethod
    def _unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        counter = 2
        while True:
            candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
            if not candidate.exists():
                return candidate
            counter += 1
