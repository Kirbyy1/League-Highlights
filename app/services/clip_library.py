from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from app.highlight_event import StoredHighlightEvent
from app.models import ClipInfo, GameHighlights, MatchContext
from app.services.ffmpeg_tools import FfmpegTools

LOGGER = logging.getLogger(__name__)


class ClipLibrary:
    def __init__(self, clip_dir: Path, ffmpeg: FfmpegTools) -> None:
        self.clip_dir = clip_dir
        self.ffmpeg = ffmpeg

    def scan(self, *, include_match_reels: bool = False) -> list[ClipInfo]:
        clips: list[ClipInfo] = []
        for video_path in self.clip_dir.glob("*.mp4"):
            metadata_path = video_path.with_suffix(".json")
            thumb = video_path.with_suffix(".jpg")
            metadata = self._read_metadata(metadata_path)
            stem_lower = video_path.stem.lower()
            if (
                bool(metadata.get("is_discord_copy", False))
                or bool(metadata.get("is_share_copy", False))
                or "_discord" in stem_lower
                or stem_lower.endswith("_share")
                or "_share_" in stem_lower
            ):
                continue
            is_match_reel = bool(metadata.get("is_match_reel", False))
            if is_match_reel and not include_match_reels:
                continue

            created_at = datetime.fromtimestamp(video_path.stat().st_mtime)
            if metadata.get("created_at"):
                try:
                    created_at = datetime.fromisoformat(str(metadata["created_at"]))
                except ValueError:
                    pass
            try:
                duration = float(metadata.get("duration_seconds") or self.ffmpeg.probe_duration(video_path))
            except Exception:
                duration = 0.0

            stored_events = tuple(
                event
                for raw in metadata.get("events", [])
                if (event := StoredHighlightEvent.from_json(raw)) is not None
            )

            clips.append(
                ClipInfo(
                    path=video_path,
                    thumbnail_path=thumb if thumb.exists() else None,
                    created_at=created_at,
                    duration_seconds=duration,
                    label=str(metadata.get("label", "MANUAL CLIP")),
                    audio_included=bool(metadata.get("audio_included", True)),
                    system_audio_included=bool(
                        metadata.get(
                            "system_audio_included",
                            metadata.get("audio_included", True),
                        )
                    ),
                    microphone_included=bool(metadata.get("microphone_included", False)),
                    size_bytes=video_path.stat().st_size,
                    discord_ready=bool(metadata.get("discord_ready", video_path.stat().st_size < 10_000_000)),
                    match_id=str(metadata.get("match_id", "")),
                    player_name=str(metadata.get("player_name", "")),
                    champion_name=str(metadata.get("champion_name", "")),
                    game_mode=str(metadata.get("game_mode", "")),
                    highlight_score=int(metadata.get("highlight_score", 0) or 0),
                    score_reasons=tuple(str(x) for x in metadata.get("score_reasons", []) if x),
                    victim_names=tuple(str(x) for x in metadata.get("victim_names", []) if x),
                    rating=str(metadata.get("rating", "")),
                    is_match_reel=is_match_reel,
                    map_name=str(metadata.get("map_name", "")),
                    match_result=str(metadata.get("match_result", "")),
                    match_started_at=self._optional_float(metadata.get("match_started_at")),
                    match_ended_at=self._optional_float(metadata.get("match_ended_at")),
                    clip_window_start_wall=self._optional_float(metadata.get("clip_window_start_wall")),
                    clip_window_end_wall=self._optional_float(metadata.get("clip_window_end_wall")),
                    event_game_time=self._optional_float(metadata.get("event_game_time")),
                    match_kills=int(metadata.get("match_kills", 0) or 0),
                    match_deaths=int(metadata.get("match_deaths", 0) or 0),
                    match_assists=int(metadata.get("match_assists", 0) or 0),
                    match_duration_seconds=float(metadata.get("match_duration_seconds", 0.0) or 0.0),
                    team=str(metadata.get("team", "UNKNOWN")),
                    event_kind=str(metadata.get("event_kind", "manual")),
                    trigger_relative_seconds=self._optional_float(
                        metadata.get("trigger_relative_seconds")
                    ),
                    events=stored_events,
                    suggested_trim_start=self._optional_float(
                        metadata.get("suggested_trim_start")
                    ),
                    suggested_trim_end=self._optional_float(
                        metadata.get("suggested_trim_end")
                    ),
                )
            )
        return sorted(clips, key=lambda clip: clip.created_at, reverse=True)

    def games(self) -> list[GameHighlights]:
        grouped: dict[str, list[ClipInfo]] = defaultdict(list)
        for clip in self.scan():
            key = clip.match_id or "__ungrouped__"
            grouped[key].append(clip)

        games: list[GameHighlights] = []
        for key, clips in grouped.items():
            clips = sorted(
                clips,
                key=lambda clip: (
                    clip.clip_window_start_wall
                    if clip.clip_window_start_wall is not None
                    else clip.created_at.timestamp()
                ),
            )
            first = clips[0]
            started_epoch = next((c.match_started_at for c in clips if c.match_started_at), None)
            started_at = datetime.fromtimestamp(started_epoch) if started_epoch else min(c.created_at for c in clips)
            games.append(
                GameHighlights(
                    match_id=key,
                    clips=clips,
                    started_at=started_at,
                    player_name=next((c.player_name for c in clips if c.player_name), ""),
                    champion_name=next((c.champion_name for c in clips if c.champion_name), ""),
                    game_mode=next((c.game_mode for c in clips if c.game_mode), ""),
                    map_name=next((c.map_name for c in clips if c.map_name), ""),
                    result=next((c.match_result for c in clips if c.match_result), ""),
                    is_ungrouped=key == "__ungrouped__",
                    kills=next((c.match_kills for c in clips if c.match_kills or c.match_deaths or c.match_assists), 0),
                    deaths=next((c.match_deaths for c in clips if c.match_kills or c.match_deaths or c.match_assists), 0),
                    assists=next((c.match_assists for c in clips if c.match_kills or c.match_deaths or c.match_assists), 0),
                    duration_seconds=next((c.match_duration_seconds for c in clips if c.match_duration_seconds > 0), 0.0),
                    team=next((c.team for c in clips if c.team and c.team != "UNKNOWN"), "UNKNOWN"),
                )
            )
        return sorted(games, key=lambda game: game.started_at, reverse=True)

    def clips_for_match(self, match_id: str, *, include_manual: bool = True) -> list[ClipInfo]:
        clips = [clip for clip in self.scan() if clip.match_id == match_id]
        if not include_manual:
            clips = [clip for clip in clips if clip.label != "MANUAL CLIP"]
        return sorted(
            clips,
            key=lambda clip: (
                clip.clip_window_start_wall if clip.clip_window_start_wall is not None else clip.created_at.timestamp()
            ),
        )

    def finalize_match(self, context: MatchContext, result: str = "") -> None:
        ended_at = datetime.now().timestamp()
        changed = 0
        for metadata_path in self.clip_dir.glob("*.json"):
            metadata = self._read_metadata(metadata_path)
            if str(metadata.get("match_id", "")) != context.match_id:
                continue
            if bool(metadata.get("is_match_reel", False)):
                continue
            metadata["player_name"] = metadata.get("player_name") or context.player_name
            metadata["champion_name"] = metadata.get("champion_name") or context.champion_name
            metadata["game_mode"] = metadata.get("game_mode") or context.game_mode
            metadata["map_name"] = context.map_name
            metadata["match_result"] = result
            metadata["match_started_at"] = context.started_at
            metadata["match_ended_at"] = ended_at
            metadata["match_kills"] = context.kills
            metadata["match_deaths"] = context.deaths
            metadata["match_assists"] = context.assists
            metadata["match_duration_seconds"] = context.duration_seconds or max(0.0, ended_at - context.started_at)
            metadata["team"] = context.team
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            changed += 1
        LOGGER.info("Finalized game group %s with %s clip(s)", context.match_id, changed)

    def set_rating(self, clip: ClipInfo, rating: str) -> None:
        if rating not in {"", "good", "bad"}:
            raise ValueError("Rating must be good, bad, or empty.")
        metadata_path = clip.path.with_suffix(".json")
        metadata = self._read_metadata(metadata_path)
        metadata["rating"] = rating
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    @staticmethod
    def delete(clip: ClipInfo) -> None:
        for path in (clip.path, clip.path.with_suffix(".jpg"), clip.path.with_suffix(".json")):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                LOGGER.exception("Could not delete %s", path)

    @staticmethod
    def _read_metadata(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            LOGGER.warning("Invalid clip metadata: %s", path)
            return {}

    @staticmethod
    def _optional_float(value: object) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
