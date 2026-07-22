from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from pathlib import Path

from app.highlight_event import StoredHighlightEvent


class RecorderState(str, Enum):
    WAITING = "WAITING FOR LEAGUE"
    STARTING = "STARTING"
    RECORDING = "RECORDING"
    SAVING = "SAVING CLIP"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


@dataclass(slots=True, frozen=True)
class LeagueWindowInfo:
    hwnd: int
    pid: int
    process_name: str
    title: str
    width: int
    height: int
    monitor_index: int = 0
    offset_x: int = 0
    offset_y: int = 0


@dataclass(slots=True, frozen=True)
class MatchContext:
    match_id: str
    player_name: str = ""
    champion_name: str = ""
    game_mode: str = ""
    map_name: str = ""
    started_at: float = 0.0
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    duration_seconds: float = 0.0
    team: str = "UNKNOWN"


@dataclass(slots=True, frozen=True)
class MatchLifecycleEvent:
    action: str
    context: MatchContext
    result: str = ""


@dataclass(slots=True, frozen=True)
class PlayerIdentity:
    riot_id: str = ""
    game_name: str = ""
    tag_line: str = ""
    summoner_name: str = ""
    champion_name: str = ""
    team: str = "UNKNOWN"
    level: int = 0
    is_dead: bool = False
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    aliases: frozenset[str] = field(default_factory=frozenset)

    @property
    def display_name(self) -> str:
        return self.riot_id or self.game_name or self.summoner_name or "Unknown player"


@dataclass(slots=True, frozen=True)
class PlayerSnapshot:
    wall_time: float
    game_time: float
    health_percent: float | None
    level: int
    is_dead: bool
    kills: int
    deaths: int
    assists: int


def format_file_size(size_bytes: int) -> str:
    size = max(0, int(size_bytes))
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


@dataclass(slots=True, frozen=True)
class HighlightRequest:
    """A clip request with optional precise in-game timing and smart-play metadata."""

    label: str = "MANUAL CLIP"
    event_started_at: float | None = None
    event_ended_at: float | None = None
    pre_seconds: float = 0.0
    post_seconds: float = 0.0

    match_id: str = ""
    player_name: str = ""
    champion_name: str = ""
    game_mode: str = ""
    event_game_time: float | None = None
    event_kind: str = "manual"
    automatic: bool = False
    triggered_at_wall: float | None = None
    triggered_at_monotonic: float | None = None

    highlight_score: int = 0
    score_reasons: tuple[str, ...] = ()
    victim_names: tuple[str, ...] = ()
    victim_champions: tuple[str, ...] = ()
    assister_names: tuple[str, ...] = ()

    @property
    def clean_label(self) -> str:
        return str(self.label or "MANUAL CLIP").strip().upper()

    @property
    def has_precise_window(self) -> bool:
        return (
            self.event_started_at is not None
            and self.event_ended_at is not None
            and self.event_ended_at >= self.event_started_at
        )

    @property
    def desired_start_wall(self) -> float | None:
        if not self.has_precise_window:
            return None
        return float(self.event_started_at) - max(0.0, float(self.pre_seconds))

    @property
    def desired_end_wall(self) -> float | None:
        if not self.has_precise_window:
            return None
        return float(self.event_ended_at) + max(0.0, float(self.post_seconds))

    def with_match_context(self, context: MatchContext | None) -> "HighlightRequest":
        if context is None:
            return self
        return replace(
            self,
            match_id=self.match_id or context.match_id,
            player_name=self.player_name or context.player_name,
            champion_name=self.champion_name or context.champion_name,
            game_mode=self.game_mode or context.game_mode,
        )


@dataclass(slots=True)
class ClipInfo:
    path: Path
    thumbnail_path: Path | None
    created_at: datetime
    duration_seconds: float
    label: str = "MANUAL CLIP"
    audio_included: bool = True
    system_audio_included: bool = True
    microphone_included: bool = False
    size_bytes: int = 0
    discord_ready: bool = False

    match_id: str = ""
    player_name: str = ""
    champion_name: str = ""
    game_mode: str = ""
    highlight_score: int = 0
    score_reasons: tuple[str, ...] = ()
    victim_names: tuple[str, ...] = ()
    rating: str = ""
    is_match_reel: bool = False
    map_name: str = ""
    match_result: str = ""
    match_started_at: float | None = None
    match_ended_at: float | None = None
    clip_window_start_wall: float | None = None
    clip_window_end_wall: float | None = None
    event_game_time: float | None = None
    match_kills: int = 0
    match_deaths: int = 0
    match_assists: int = 0
    match_duration_seconds: float = 0.0
    team: str = "UNKNOWN"
    event_kind: str = "manual"
    trigger_relative_seconds: float | None = None
    events: tuple[StoredHighlightEvent, ...] = ()
    suggested_trim_start: float | None = None
    suggested_trim_end: float | None = None

    @property
    def duration_text(self) -> str:
        seconds = max(0, int(round(self.duration_seconds)))
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    @property
    def file_size_text(self) -> str:
        return format_file_size(self.size_bytes)

    @property
    def audio_summary_text(self) -> str:
        if self.system_audio_included and self.microphone_included:
            return "System + microphone"
        if self.microphone_included:
            return "Microphone"
        if self.system_audio_included:
            return "System audio"
        return "Video only"

    @property
    def share_status_text(self) -> str:
        return "Discord ready" if self.discord_ready else "Large file"

    @property
    def date_text(self) -> str:
        today = datetime.now().date()
        if self.created_at.date() == today:
            return "Today"
        return self.created_at.strftime("%d/%m/%Y")

    @property
    def time_text(self) -> str:
        return self.created_at.strftime("%H:%M")

    @property
    def context_text(self) -> str:
        parts = [part for part in (self.champion_name, self.player_name) if part]
        return " • ".join(parts)

    @property
    def score_text(self) -> str:
        return f"Smart score {self.highlight_score}" if self.highlight_score else ""

    @property
    def match_time_text(self) -> str:
        value = self.event_game_time
        if value is None and self.match_started_at and self.clip_window_start_wall:
            value = max(0.0, self.clip_window_start_wall - self.match_started_at)
        if value is None:
            return "--:--"
        seconds = max(0, int(round(value)))
        return f"{seconds // 60:02d}:{seconds % 60:02d}"


@dataclass(slots=True)
class GameHighlights:
    match_id: str
    clips: list[ClipInfo]
    started_at: datetime
    player_name: str = ""
    champion_name: str = ""
    game_mode: str = ""
    map_name: str = ""
    result: str = ""
    is_ungrouped: bool = False
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    duration_seconds: float = 0.0
    team: str = "UNKNOWN"

    @property
    def clip_count(self) -> int:
        return len(self.clips)

    @property
    def total_size_bytes(self) -> int:
        return sum(clip.size_bytes for clip in self.clips)

    @property
    def total_duration_seconds(self) -> float:
        return sum(clip.duration_seconds for clip in self.clips)

    @property
    def total_size_text(self) -> str:
        return format_file_size(self.total_size_bytes)

    @property
    def total_duration_text(self) -> str:
        seconds = max(0, int(round(self.total_duration_seconds)))
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    @property
    def date_text(self) -> str:
        today = datetime.now().date()
        if self.started_at.date() == today:
            return "Today"
        return self.started_at.strftime("%d/%m/%Y")

    @property
    def time_text(self) -> str:
        return self.started_at.strftime("%H:%M")

    @property
    def title_text(self) -> str:
        if self.is_ungrouped:
            return "Older ungrouped clips"
        return self.champion_name or "League match"

    @property
    def subtitle_text(self) -> str:
        if self.is_ungrouped:
            return "Clips created before game grouping was added"
        parts = [part for part in (self.player_name, self.game_mode, self.result) if part]
        return " • ".join(parts) or "Saved game highlights"

    @property
    def thumbnail_path(self) -> Path | None:
        ranked = sorted(
            self.clips,
            key=lambda clip: (clip.highlight_score, clip.created_at.timestamp()),
            reverse=True,
        )
        for clip in ranked:
            if clip.thumbnail_path and clip.thumbnail_path.exists():
                return clip.thumbnail_path
        return None

    @property
    def highlight_labels_text(self) -> str:
        labels: list[str] = []
        for clip in self.clips:
            label = clip.label.replace("_", " ").title()
            if label not in labels:
                labels.append(label)
        if len(labels) > 3:
            return " • ".join(labels[:3]) + f" • +{len(labels) - 3} more"
        return " • ".join(labels)

    @property
    def kda_text(self) -> str:
        if self.is_ungrouped or not any((self.kills, self.deaths, self.assists)):
            return ""
        return f"{self.kills} / {self.deaths} / {self.assists}"

    @property
    def match_duration_text(self) -> str:
        value = self.duration_seconds
        if value <= 0:
            endings = [c.match_ended_at for c in self.clips if c.match_ended_at]
            starts = [c.match_started_at for c in self.clips if c.match_started_at]
            if endings and starts:
                value = max(0.0, max(endings) - min(starts))
        seconds = max(0, int(round(value)))
        return f"{seconds // 60:02d}:{seconds % 60:02d}" if seconds else ""

    @property
    def normalized_result(self) -> str:
        value = self.result.strip().upper()
        if value in {"WIN", "WON", "VICTORY"}:
            return "Victory"
        if value in {"LOSE", "LOSS", "LOST", "DEFEAT"}:
            return "Defeat"
        return self.result.title() if self.result else ""

    @property
    def timeline_duration_seconds(self) -> float:
        values = [c.event_game_time for c in self.clips if c.event_game_time is not None]
        return max(self.duration_seconds, max(values, default=0.0), 1.0)
