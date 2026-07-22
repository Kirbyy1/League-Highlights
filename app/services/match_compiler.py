from __future__ import annotations

import json
import logging
import re
import tempfile
from datetime import datetime
from pathlib import Path

from app.config import AppConfig
from app.models import ClipInfo, MatchContext
from app.services.clip_library import ClipLibrary
from app.services.ffmpeg_tools import FfmpegTools

LOGGER = logging.getLogger(__name__)


class MatchCompilationService:
    """Create one chronological reel containing only a match's saved highlights."""

    def __init__(
        self,
        config: AppConfig,
        ffmpeg: FfmpegTools,
        library: ClipLibrary,
    ) -> None:
        self.config = config
        self.ffmpeg = ffmpeg
        self.library = library

    def compile_match(self, context: MatchContext, result: str = "") -> ClipInfo | None:
        if not self.config.match_reel_enabled:
            return None

        clips = self.library.clips_for_match(
            context.match_id,
            include_manual=self.config.match_reel_include_manual,
        )
        if len(clips) < 2:
            LOGGER.info(
                "Match %s has %s accepted clip(s); no reel is needed",
                context.match_id,
                len(clips),
            )
            return None

        entries = self._remove_overlapping_footage(clips)
        if len(entries) < 2:
            LOGGER.info("Match reel collapsed to fewer than two unique moments")
            return None

        assert self.ffmpeg.ffmpeg is not None
        created_at = datetime.now()
        safe_match = re.sub(r"[^A-Za-z0-9_]+", "_", context.match_id).strip("_")
        output_path = self.config.clip_dir / f"MATCH_HIGHLIGHTS_{safe_match}.mp4"
        thumbnail_path = output_path.with_suffix(".jpg")
        metadata_path = output_path.with_suffix(".json")

        for path in (output_path, thumbnail_path, metadata_path):
            if path.exists():
                path.unlink()

        with tempfile.TemporaryDirectory(prefix="league_match_reel_") as temp_name:
            temp_dir = Path(temp_name)
            normalized: list[Path] = []
            source_names: list[str] = []
            for index, (clip, trim_start, duration) in enumerate(entries):
                normalized_path = temp_dir / f"moment_{index:03d}.mp4"
                self._normalize_clip(clip, normalized_path, trim_start, duration)
                normalized.append(normalized_path)
                source_names.append(clip.path.name)

            concat_file = temp_dir / "reel.txt"
            concat_file.write_text(
                "".join(f"file '{self._concat_escape(path)}'\n" for path in normalized),
                encoding="utf-8",
            )
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
                    "-movflags",
                    "+faststart",
                    str(output_path),
                ],
                timeout=max(240, sum(item[2] for item in entries) * 12),
                low_priority=True,
            )

        duration = self.ffmpeg.probe_duration(output_path)
        size_bytes = output_path.stat().st_size
        self._generate_thumbnail(output_path, thumbnail_path, duration)
        metadata_path.write_text(
            json.dumps(
                {
                    "label": "MATCH HIGHLIGHTS",
                    "created_at": created_at.isoformat(),
                    "duration_seconds": duration,
                    "audio_included": True,
                    "size_bytes": size_bytes,
                    "discord_ready": size_bytes < 10_000_000,
                    "match_id": context.match_id,
                    "player_name": context.player_name,
                    "champion_name": context.champion_name,
                    "game_mode": context.game_mode,
                    "map_name": context.map_name,
                    "match_result": result,
                    "is_match_reel": True,
                    "rating": "",
                    "highlight_score": max((clip.highlight_score for clip in clips), default=0),
                    "score_reasons": [f"{len(entries)} important moments"],
                    "victim_names": [],
                    "source_clips": source_names,
                    "clip_window_start_wall": entries[0][0].clip_window_start_wall,
                    "clip_window_end_wall": entries[-1][0].clip_window_end_wall,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        reel = ClipInfo(
            path=output_path,
            thumbnail_path=thumbnail_path if thumbnail_path.exists() else None,
            created_at=created_at,
            duration_seconds=duration,
            label="MATCH HIGHLIGHTS",
            audio_included=True,
            size_bytes=size_bytes,
            discord_ready=size_bytes < 10_000_000,
            match_id=context.match_id,
            player_name=context.player_name,
            champion_name=context.champion_name,
            game_mode=context.game_mode,
            highlight_score=max((clip.highlight_score for clip in clips), default=0),
            score_reasons=(f"{len(entries)} important moments",),
            is_match_reel=True,
            clip_window_start_wall=entries[0][0].clip_window_start_wall,
            clip_window_end_wall=entries[-1][0].clip_window_end_wall,
        )

        if not self.config.match_reel_keep_individual:
            for clip in clips:
                self.library.delete(clip)

        LOGGER.info(
            "Created match reel %s from %s moments (%.1fs)",
            output_path,
            len(entries),
            duration,
        )
        return reel

    @staticmethod
    def _remove_overlapping_footage(
        clips: list[ClipInfo],
    ) -> list[tuple[ClipInfo, float, float]]:
        """Trim duplicate beginnings when two saved clips cover the same fight."""

        entries: list[tuple[ClipInfo, float, float]] = []
        previous_end: float | None = None
        for clip in clips:
            trim_start = 0.0
            duration = max(0.0, clip.duration_seconds)
            start = clip.clip_window_start_wall
            end = clip.clip_window_end_wall
            if start is not None and end is not None and previous_end is not None:
                overlap = max(0.0, previous_end - start)
                if overlap >= duration - 1.0:
                    previous_end = max(previous_end, end)
                    continue
                trim_start = overlap
                duration -= overlap
            if duration >= 2.0:
                entries.append((clip, trim_start, duration))
            if end is not None:
                previous_end = end if previous_end is None else max(previous_end, end)
        return entries

    def _normalize_clip(
        self,
        clip: ClipInfo,
        output_path: Path,
        trim_start: float,
        duration: float,
    ) -> None:
        assert self.ffmpeg.ffmpeg is not None
        video_filter = (
            "scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2:black,fps=30,format=yuv420p"
        )
        base = [
            str(self.ffmpeg.ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{max(0.0, trim_start):.3f}",
            "-i",
            str(clip.path),
        ]
        if clip.audio_included:
            command = [
                *base,
                "-t",
                f"{duration:.3f}",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-vf",
                video_filter,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-g",
                "60",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-af",
                "aresample=async=1:first_pts=0",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        else:
            command = [
                *base,
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-t",
                f"{duration:.3f}",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-vf",
                video_filter,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-g",
                "60",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        self.ffmpeg.run(command, timeout=max(180, duration * 10), low_priority=True)

    def _generate_thumbnail(self, video_path: Path, thumbnail_path: Path, duration: float) -> None:
        assert self.ffmpeg.ffmpeg is not None
        seek = max(0.2, min(duration * 0.35, max(0.2, duration - 0.2)))
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
                "scale=640:-2",
                "-q:v",
                "3",
                str(thumbnail_path),
            ],
            timeout=45,
        )

    @staticmethod
    def _concat_escape(path: Path) -> str:
        return str(path.resolve()).replace("'", "'\\''").replace("\\", "/")
