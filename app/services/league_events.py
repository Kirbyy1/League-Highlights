from __future__ import annotations

import json
import logging
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import AppConfig
from app.highlight_event import HighlightEvent
from app.models import (
    HighlightRequest,
    MatchContext,
    MatchLifecycleEvent,
    PlayerIdentity,
    PlayerSnapshot,
)
from app.services.feedback_profile import FeedbackProfile
from app.services.smart_scoring import PlayCandidate, score_candidate

LOGGER = logging.getLogger(__name__)

LIVE_CLIENT_BASE = "https://127.0.0.1:2999/liveclientdata"


def _normalise_name(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _name_aliases(value: object) -> set[str]:
    normalised = _normalise_name(value)
    if not normalised:
        return set()
    aliases = {normalised}
    if "#" in normalised:
        aliases.add(normalised.split("#", 1)[0].strip())
    return {alias for alias in aliases if alias}


def _event_value(event: dict[str, Any], key: str, default: Any = None) -> Any:
    if key in event:
        return event[key]
    wanted = key.casefold()
    for candidate, value in event.items():
        if str(candidate).casefold() == wanted:
            return value
    return default


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes"}


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")[:36] or "Player"


@dataclass(slots=True, frozen=True)
class _KillRecord:
    wall_time: float
    game_time: float
    victim_name: str
    victim_champion: str
    victim_level: int
    assisters: tuple[str, ...]
    solo: bool


@dataclass(slots=True, frozen=True)
class _AssistRecord:
    wall_time: float
    game_time: float
    killer_name: str
    victim_name: str
    victim_champion: str


class LeagueEventMonitor:
    """Read Riot's local Live Client Data API and emit scored highlight requests.

    Detection is entirely local. Riot events identify kills/objectives exactly;
    player snapshots add context such as level, health, and whether the player died
    immediately after. The scorer remains explainable and configurable.
    """

    KILL_LABELS = {
        1: "SINGLE KILL",
        2: "DOUBLE KILL",
        3: "TRIPLE KILL",
        4: "QUADRA KILL",
        5: "PENTAKILL",
    }

    KILL_SETTING_KEYS = {
        1: "auto_clip_single_kill",
        2: "auto_clip_double_kill",
        3: "auto_clip_triple_kill",
        4: "auto_clip_quadra_kill",
        5: "auto_clip_pentakill",
    }

    DRAGON_NAMES = {
        "air": "CLOUD DRAGON",
        "cloud": "CLOUD DRAGON",
        "earth": "MOUNTAIN DRAGON",
        "mountain": "MOUNTAIN DRAGON",
        "fire": "INFERNAL DRAGON",
        "infernal": "INFERNAL DRAGON",
        "water": "OCEAN DRAGON",
        "ocean": "OCEAN DRAGON",
        "hextech": "HEXTECH DRAGON",
        "chemtech": "CHEMTECH DRAGON",
        "elder": "ELDER DRAGON",
    }

    def __init__(
        self,
        config: AppConfig,
        highlight_callback: Callable[[HighlightRequest], None],
        status_callback: Callable[[str, bool], None],
        match_callback: Callable[[MatchLifecycleEvent], None] | None = None,
        event_callback: Callable[[HighlightEvent], None] | None = None,
        *,
        poll_interval: float = 0.5,
        kill_settle_seconds: float = 10.5,
    ) -> None:
        self.config = config
        self.highlight_callback = highlight_callback
        self.status_callback = status_callback
        self.match_callback = match_callback or (lambda _event: None)
        self.event_callback = event_callback or (lambda _event: None)
        self.poll_interval = poll_interval
        self.kill_settle_seconds = kill_settle_seconds

        self.feedback = FeedbackProfile(Path(getattr(config, "clip_dir", Path.cwd())))

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ssl_context = ssl._create_unverified_context()
        self._last_event_id: int | None = None
        self._connected = False
        self._status_text = ""
        self._last_success_wall = 0.0

        self._active_aliases: set[str] = set()
        self._active_team: str | None = None
        self._active_identity = PlayerIdentity()
        self._identity_by_alias: dict[str, PlayerIdentity] = {}
        self._team_by_alias: dict[str, str] = {}
        self._next_identity_refresh = 0.0

        self._snapshots: deque[PlayerSnapshot] = deque(maxlen=300)
        self._kill_records: list[_KillRecord] = []
        self._assist_records: list[_AssistRecord] = []
        self._multikill_streak = 0
        self._last_player_kill_at = 0.0
        self._last_player_kill_game_time = 0.0
        self._last_assist_at = 0.0
        self._last_assist_game_time = 0.0
        self._last_ace_wall = 0.0

        self._current_match: MatchContext | None = None
        self._match_ended = False
        self._last_game_time = 0.0

    @property
    def current_match(self) -> MatchContext | None:
        return self._current_match

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="LeagueLiveEvents",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        self._set_status("Waiting for active match data", False)
        while not self._stop_event.is_set():
            try:
                now = time.monotonic()
                if now >= self._next_identity_refresh:
                    self._refresh_live_state()
                    self._next_identity_refresh = now + 1.0

                payload = self._fetch_json("/eventdata")
                raw_events = payload.get("Events", []) if isinstance(payload, dict) else []
                events = [event for event in raw_events if isinstance(event, dict)]
                self._consume_snapshot(events)
                self._last_success_wall = time.time()
                self._set_status(self._connected_status_text(), True)
            except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
                self._set_status("Waiting for active match data", False)
                if (
                    self._current_match is not None
                    and not self._match_ended
                    and self._last_success_wall
                    and time.time() - self._last_success_wall > 20.0
                ):
                    self._end_match("UNKNOWN")
            except Exception:
                LOGGER.exception("League live-event monitor failed")
                self._set_status("Live event detection error — retrying", False)

            self._flush_pending_kills_if_ready()
            self._flush_pending_assists_if_ready()
            self._stop_event.wait(self.poll_interval)

    def _fetch_json(self, endpoint: str) -> Any:
        request = urllib.request.Request(
            LIVE_CLIENT_BASE + endpoint,
            headers={"Accept": "application/json", "User-Agent": "LeagueHighlights/0.5"},
        )
        with urllib.request.urlopen(
            request,
            context=self._ssl_context,
            timeout=0.45,
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    def _refresh_live_state(self) -> None:
        active = self._fetch_json("/activeplayer")
        players = self._fetch_json("/playerlist")
        game_stats = self._fetch_json("/gamestats")
        if not isinstance(active, dict) or not isinstance(players, list):
            return

        active_aliases: set[str] = set()
        for key in ("riotId", "riotIdGameName", "summonerName"):
            active_aliases.update(_name_aliases(active.get(key)))

        identity_by_alias: dict[str, PlayerIdentity] = {}
        team_by_alias: dict[str, str] = {}
        active_identity = PlayerIdentity()

        for raw_player in players:
            if not isinstance(raw_player, dict):
                continue
            aliases: set[str] = set()
            for key in ("riotId", "riotIdGameName", "summonerName"):
                aliases.update(_name_aliases(raw_player.get(key)))
            scores = raw_player.get("scores") if isinstance(raw_player.get("scores"), dict) else {}
            identity = PlayerIdentity(
                riot_id=str(raw_player.get("riotId") or ""),
                game_name=str(raw_player.get("riotIdGameName") or ""),
                tag_line=str(raw_player.get("riotIdTagLine") or ""),
                summoner_name=str(raw_player.get("summonerName") or ""),
                champion_name=str(raw_player.get("championName") or ""),
                team=str(raw_player.get("team") or "UNKNOWN").upper(),
                level=_safe_int(raw_player.get("level")),
                is_dead=bool(raw_player.get("isDead", False)),
                kills=_safe_int(scores.get("kills")),
                deaths=_safe_int(scores.get("deaths")),
                assists=_safe_int(scores.get("assists")),
                aliases=frozenset(aliases),
            )
            for alias in aliases:
                identity_by_alias[alias] = identity
                team_by_alias[alias] = identity.team
            if aliases & active_aliases:
                active_identity = identity
                active_aliases.update(aliases)

        if not active_identity.aliases:
            # Structured active-player fields are still useful if playerlist alias
            # matching temporarily fails during loading.
            active_identity = PlayerIdentity(
                riot_id=str(active.get("riotId") or ""),
                game_name=str(active.get("riotIdGameName") or ""),
                tag_line=str(active.get("riotIdTagLine") or ""),
                summoner_name=str(active.get("summonerName") or ""),
                level=_safe_int(active.get("level")),
                aliases=frozenset(active_aliases),
            )

        identity_changed = (
            bool(self._active_aliases)
            and bool(active_aliases)
            and active_aliases != self._active_aliases
        )
        self._active_aliases = active_aliases
        self._active_identity = active_identity
        self._active_team = active_identity.team if active_identity.team != "UNKNOWN" else None
        self._identity_by_alias = identity_by_alias
        self._team_by_alias = team_by_alias

        game_time = _safe_float(game_stats.get("gameTime") if isinstance(game_stats, dict) else 0.0)
        self._update_match_context(game_stats if isinstance(game_stats, dict) else {}, game_time)
        self._append_snapshot(active, active_identity, game_time)

        if identity_changed:
            LOGGER.info("Active League player changed; resetting event baseline")
            self._last_event_id = None
            self._clear_pending_kills()
            self._clear_pending_assists()

    def _update_match_context(self, game_stats: dict[str, Any], game_time: float) -> None:
        now = time.time()
        start_epoch = now - max(0.0, game_time)
        player_name = self._active_identity.display_name
        champion = self._active_identity.champion_name
        game_mode = str(game_stats.get("gameMode") or "")
        map_name = str(game_stats.get("mapName") or "")

        new_game = self._current_match is None
        if self._current_match is not None:
            if game_time + 20.0 < self._last_game_time:
                new_game = True
            elif abs(start_epoch - self._current_match.started_at) > 60.0:
                new_game = True

        if new_game:
            if self._current_match is not None and not self._match_ended:
                self._end_match("UNKNOWN")
            start_dt = datetime.fromtimestamp(start_epoch)
            match_id = f"{start_dt:%Y%m%d_%H%M%S}_{_slug(player_name)}"
            self._current_match = MatchContext(
                match_id=match_id,
                player_name=player_name,
                champion_name=champion,
                game_mode=game_mode,
                map_name=map_name,
                started_at=start_epoch,
                kills=self._active_identity.kills,
                deaths=self._active_identity.deaths,
                assists=self._active_identity.assists,
                duration_seconds=max(0.0, game_time),
                team=self._active_identity.team,
            )
            self._match_ended = False
            self._last_event_id = None
            self._clear_pending_kills()
            self._clear_pending_assists()
            self._snapshots.clear()
            LOGGER.info("Match session started: %s", match_id)
            self._emit_match_event("started", "")
        elif self._current_match is not None:
            self._current_match = MatchContext(
                match_id=self._current_match.match_id,
                player_name=player_name or self._current_match.player_name,
                champion_name=champion or self._current_match.champion_name,
                game_mode=game_mode or self._current_match.game_mode,
                map_name=map_name or self._current_match.map_name,
                started_at=self._current_match.started_at,
                kills=self._active_identity.kills,
                deaths=self._active_identity.deaths,
                assists=self._active_identity.assists,
                duration_seconds=max(0.0, game_time),
                team=self._active_identity.team,
            )

        self._last_game_time = game_time

    def _append_snapshot(
        self,
        active: dict[str, Any],
        identity: PlayerIdentity,
        game_time: float,
    ) -> None:
        stats = active.get("championStats") if isinstance(active.get("championStats"), dict) else {}
        current_health = _safe_float(
            stats.get("currentHealth", stats.get("health", active.get("currentHealth"))),
            -1.0,
        )
        max_health = _safe_float(
            stats.get("maxHealth", stats.get("maximumHealth", active.get("maxHealth"))),
            -1.0,
        )
        health_percent: float | None = None
        if current_health >= 0 and max_health > 0:
            health_percent = max(0.0, min(100.0, current_health / max_health * 100.0))

        self._snapshots.append(
            PlayerSnapshot(
                wall_time=time.time(),
                game_time=game_time,
                health_percent=health_percent,
                level=_safe_int(active.get("level"), identity.level),
                is_dead=identity.is_dead,
                kills=identity.kills,
                deaths=identity.deaths,
                assists=identity.assists,
            )
        )

    def _consume_snapshot(self, events: list[dict[str, Any]]) -> None:
        indexed: list[tuple[int, dict[str, Any]]] = []
        for event in events:
            try:
                event_id = int(_event_value(event, "EventID", -1))
            except (TypeError, ValueError):
                continue
            if event_id >= 0:
                indexed.append((event_id, event))

        if not indexed:
            return
        indexed.sort(key=lambda item: item[0])
        maximum_id = indexed[-1][0]

        if self._last_event_id is None:
            self._last_event_id = maximum_id
            LOGGER.info("Live event baseline established at EventID %s", maximum_id)
            return

        if maximum_id < self._last_event_id:
            LOGGER.info("New League event stream detected; resetting baseline")
            self._last_event_id = maximum_id
            self._clear_pending_kills()
            self._clear_pending_assists()
            return

        for event_id, event in indexed:
            if event_id <= self._last_event_id:
                continue
            self._handle_event(event)
            self._last_event_id = event_id

    def _handle_event(self, event: dict[str, Any]) -> None:
        event_name = str(_event_value(event, "EventName", "")).casefold()
        if not event_name:
            return

        self._track_lightweight_event(event_name, event)

        # Smart Highlights is the single user-facing master switch for all
        # automatic clips. Match lifecycle events still run so games remain
        # grouped correctly, and the manual shortcut is unaffected.
        if (
            not self.config.smart_highlights_enabled
            and event_name in {"championkill", "multikill", "ace", "dragonkill", "baronkill"}
        ):
            if event_name in {"championkill", "multikill"}:
                self._clear_pending_kills()
                self._clear_pending_assists()
            return

        if event_name == "championkill" and self._is_active_player(
            _event_value(event, "KillerName")
        ):
            game_time = self._event_time(event)
            if (
                self._kill_records
                and game_time
                and self._last_player_kill_game_time
                and game_time - self._last_player_kill_game_time > 12.0
            ):
                self._emit_pending_kill()

            victim_raw = _event_value(event, "VictimName", "")
            victim = self._identity_for_name(victim_raw)
            assisters = self._canonical_names(_event_value(event, "Assisters", []))
            wall_time = time.time()
            self._kill_records.append(
                _KillRecord(
                    wall_time=wall_time,
                    game_time=game_time,
                    victim_name=victim.display_name if victim.aliases else str(victim_raw or "Enemy"),
                    victim_champion=victim.champion_name,
                    victim_level=victim.level,
                    assisters=assisters,
                    solo=not assisters,
                )
            )
            self._last_player_kill_at = time.monotonic()
            self._last_player_kill_game_time = game_time
            LOGGER.info(
                "Detected active-player champion kill: %s (%s)",
                victim.display_name,
                victim.champion_name or "unknown champion",
            )
            return

        if event_name == "championkill":
            assisters_raw = _event_value(event, "Assisters", [])
            assister_aliases = {
                alias
                for value in assisters_raw
                for alias in _name_aliases(value)
            } if isinstance(assisters_raw, Iterable) and not isinstance(assisters_raw, (str, bytes, dict)) else set()
            active_assisted = bool(assister_aliases & self._active_aliases)
            if active_assisted and self._event_belongs_to_active_team(event):
                game_time = self._event_time(event)
                if (
                    self._assist_records
                    and game_time
                    and self._last_assist_game_time
                    and game_time - self._last_assist_game_time > 14.0
                ):
                    self._emit_pending_assist()

                victim_raw = _event_value(event, "VictimName", "")
                killer_raw = _event_value(event, "KillerName", "")
                victim = self._identity_for_name(victim_raw)
                killer = self._identity_for_name(killer_raw)
                wall_time = time.time()
                self._assist_records.append(
                    _AssistRecord(
                        wall_time=wall_time,
                        game_time=game_time,
                        killer_name=killer.display_name if killer.aliases else str(killer_raw or "Ally"),
                        victim_name=victim.display_name if victim.aliases else str(victim_raw or "Enemy"),
                        victim_champion=victim.champion_name,
                    )
                )
                self._last_assist_at = time.monotonic()
                self._last_assist_game_time = game_time
                LOGGER.info(
                    "Detected active-player assist: %s killed %s",
                    killer.display_name if killer.aliases else killer_raw,
                    victim.display_name if victim.aliases else victim_raw,
                )
                return

        if event_name == "multikill" and self._is_active_player(
            _event_value(event, "KillerName")
        ):
            streak = max(1, min(5, _safe_int(_event_value(event, "KillStreak", 1), 1)))
            self._multikill_streak = max(self._multikill_streak, streak)
            self._last_player_kill_at = time.monotonic()
            self._last_player_kill_game_time = self._event_time(event)
            LOGGER.info("Detected exact Riot multikill event (streak=%s)", streak)
            return

        if event_name == "ace":
            self._last_ace_wall = time.time()
            return

        if event_name == "dragonkill" and self.config.auto_clip_dragon:
            if self._event_belongs_to_active_team(event):
                self._emit_objective(event, "dragon")
            return

        if event_name == "baronkill" and self.config.auto_clip_baron:
            if self._event_belongs_to_active_team(event):
                self._emit_objective(event, "baron")
            return

        if event_name == "gameend":
            self._flush_pending_kills(force=True)
            self._flush_pending_assists(force=True)
            result = str(_event_value(event, "Result", _event_value(event, "GameResult", "")))
            self._end_match(result.upper() or "UNKNOWN")

    def _track_lightweight_event(self, event_name: str, event: dict[str, Any]) -> None:
        """Forward only small timestamps; never inspect video or audio."""

        event_type = ""
        if event_name == "championkill":
            if self._is_active_player(_event_value(event, "KillerName")):
                event_type = "CHAMPION_KILL"
            elif self._is_active_player(_event_value(event, "VictimName")):
                event_type = "PLAYER_DEATH"
            else:
                assisters = _event_value(event, "Assisters", [])
                aliases = {
                    alias
                    for value in assisters
                    for alias in _name_aliases(value)
                } if isinstance(assisters, Iterable) and not isinstance(assisters, (str, bytes, dict)) else set()
                if aliases & self._active_aliases:
                    event_type = "ASSIST"
        elif event_name == "multikill" and self._is_active_player(
            _event_value(event, "KillerName")
        ):
            event_type = "MULTIKILL"
        elif event_name == "ace":
            event_type = "ACE"
        elif event_name == "dragonkill" and self._event_belongs_to_active_team(event):
            event_type = "DRAGON_STEAL" if _as_bool(_event_value(event, "Stolen")) else "DRAGON"
        elif event_name == "baronkill" and self._event_belongs_to_active_team(event):
            event_type = "BARON_STEAL" if _as_bool(_event_value(event, "Stolen")) else "BARON"

        if not event_type:
            return
        try:
            self.event_callback(
                HighlightEvent(
                    game_time=self._event_time(event),
                    event_type=event_type,
                    detected_at_monotonic=time.monotonic(),
                    detected_at_wall=time.time(),
                    match_id=self._match_id,
                )
            )
        except Exception:
            LOGGER.exception("Highlight timestamp callback failed")

    def _emit_objective(self, event: dict[str, Any], kind: str) -> None:
        if not self.config.smart_highlights_enabled:
            return
        stolen = _as_bool(_event_value(event, "Stolen"))

        # Objective highlights are intentionally limited to steals. Routine team
        # Dragon and Baron secures are useful match events, but they are not
        # automatically clipped because they create too many low-value videos.
        if not stolen:
            LOGGER.info("Ignored routine %s secure; only objective steals are clipped", kind)
            return

        dragon_type = _normalise_name(_event_value(event, "DragonType")) if kind == "dragon" else ""
        elder = dragon_type == "elder"
        if kind == "dragon":
            label = "ELDER DRAGON STEAL" if elder else "DRAGON STEAL"
        else:
            label = "BARON STEAL"

        candidate = PlayCandidate(kind=kind, stolen=True, elder=elder)
        result = score_candidate(
            candidate,
            enabled=self.config.smart_highlights_enabled,
            sensitivity=self.config.smart_sensitivity,
            threshold_adjustment=self.feedback.threshold_adjustment(kind, label),
        )
        if not result.keep:
            LOGGER.info(
                "Smart filter skipped %s (score %s, threshold %s)",
                label,
                result.score,
                result.threshold,
            )
            return

        now = time.time()
        game_time = self._event_time(event)
        LOGGER.info("Automatic objective highlight: %s, smart score %s", label, result.score)
        self.highlight_callback(
            HighlightRequest(
                label=label,
                event_started_at=now,
                event_ended_at=now,
                pre_seconds=8.0,
                post_seconds=6.0,
                match_id=self._match_id,
                player_name=self._active_identity.display_name,
                champion_name=self._active_identity.champion_name,
                game_mode=self._game_mode,
                event_game_time=game_time,
                event_kind=kind,
                automatic=True,
                highlight_score=result.score,
                score_reasons=result.reasons,
                assister_names=self._canonical_names(_event_value(event, "Assisters", [])),
            )
        )

    def _flush_pending_kills_if_ready(self) -> None:
        self._flush_pending_kills(force=False)

    def _flush_pending_kills(self, *, force: bool) -> None:
        if not self._kill_records and not self._multikill_streak:
            return
        if not force:
            if not self._last_player_kill_at:
                return
            if time.monotonic() - self._last_player_kill_at < self.kill_settle_seconds:
                return
        self._emit_pending_kill()

    def _emit_pending_kill(self) -> None:
        if not self._kill_records and not self._multikill_streak:
            return
        if not self.config.smart_highlights_enabled:
            self._clear_pending_kills()
            return

        records = tuple(self._kill_records)
        count = max(1, min(5, max(len(records), self._multikill_streak)))
        first_wall = records[0].wall_time if records else time.time()
        last_wall = records[-1].wall_time if records else first_wall
        first_game_time = records[0].game_time if records else self._last_player_kill_game_time
        self._clear_pending_kills()

        setting_key = self.KILL_SETTING_KEYS[count]
        if not bool(getattr(self.config, setting_key, True)):
            LOGGER.info("Skipped %s because it is disabled", self.KILL_LABELS[count])
            return

        snapshots = [
            snap
            for snap in self._snapshots
            if first_wall - 1.0 <= snap.wall_time <= last_wall + 7.0
        ]
        health_values = [
            snap.health_percent
            for snap in snapshots
            if snap.health_percent is not None and not snap.is_dead
        ]
        min_health = min(health_values) if health_values else None
        died_after = any(
            snap.is_dead and last_wall <= snap.wall_time <= last_wall + 6.0
            for snap in snapshots
        )
        before_snapshots = [snap for snap in snapshots if snap.wall_time <= first_wall + 1.0]
        active_level = before_snapshots[-1].level if before_snapshots else self._active_identity.level
        highest_victim_level = max((record.victim_level for record in records), default=0)
        solo_kills = sum(1 for record in records if record.solo)
        # Riot's event feed lists allied assisters. Two or more kills in the same
        # fight with no allied assister on any kill is a strong, lightweight
        # indication of an outnumbered play (for example a top-lane 2v1).
        outnumbered_kill_count = count if count >= 2 and solo_kills == count else 0
        ace = first_wall <= self._last_ace_wall <= last_wall + 6.0
        action_seconds = max(0.0, last_wall - first_wall)

        label = self.KILL_LABELS[count]
        if count == 2 and solo_kills == 2:
            label = "2V1 DOUBLE KILL"
        result = score_candidate(
            PlayCandidate(
                kind="kill",
                kill_count=count,
                solo_kills=solo_kills,
                outnumbered_kill_count=outnumbered_kill_count,
                active_level=active_level,
                highest_victim_level=highest_victim_level,
                min_health_percent=min_health,
                died_after=died_after,
                ace=ace,
                action_seconds=action_seconds,
            ),
            enabled=self.config.smart_highlights_enabled,
            sensitivity=self.config.smart_sensitivity,
            threshold_adjustment=self.feedback.threshold_adjustment("kill", label),
        )
        if not result.keep:
            LOGGER.info(
                "Smart filter skipped %s (score %s, threshold %s): %s",
                label,
                result.score,
                result.threshold,
                ", ".join(result.reasons),
            )
            return

        if outnumbered_kill_count >= 2:
            pre_seconds = 11.0
            post_seconds = 9.0
        else:
            pre_seconds = 8.0 if count >= 4 else 7.0
            post_seconds = 8.0 if count >= 4 else 7.0
        victims = tuple(record.victim_name for record in records if record.victim_name)
        champions = tuple(record.victim_champion for record in records if record.victim_champion)
        assisters = tuple(dict.fromkeys(name for record in records for name in record.assisters))
        LOGGER.info(
            "Automatic highlight ready: %s (score %s): %s",
            label,
            result.score,
            ", ".join(result.reasons),
        )
        self.highlight_callback(
            HighlightRequest(
                label=label,
                event_started_at=first_wall,
                event_ended_at=last_wall,
                pre_seconds=pre_seconds,
                post_seconds=post_seconds,
                match_id=self._match_id,
                player_name=self._active_identity.display_name,
                champion_name=self._active_identity.champion_name,
                game_mode=self._game_mode,
                event_game_time=first_game_time,
                event_kind="kill",
                automatic=True,
                highlight_score=result.score,
                score_reasons=result.reasons,
                victim_names=victims,
                victim_champions=champions,
                assister_names=assisters,
            )
        )

    def _flush_pending_assists_if_ready(self) -> None:
        self._flush_pending_assists(force=False)

    def _flush_pending_assists(self, *, force: bool) -> None:
        if not self._assist_records:
            return
        if not force:
            if not self._last_assist_at:
                return
            if time.monotonic() - self._last_assist_at < 11.5:
                return
        self._emit_pending_assist()

    def _emit_pending_assist(self) -> None:
        if not self._assist_records:
            return
        if not self.config.smart_highlights_enabled:
            self._clear_pending_assists()
            return

        records = tuple(self._assist_records)
        first_wall = records[0].wall_time
        last_wall = records[-1].wall_time
        first_game_time = records[0].game_time
        self._clear_pending_assists()

        snapshots = [
            snap
            for snap in self._snapshots
            if first_wall - 2.0 <= snap.wall_time <= last_wall + 8.0
        ]
        health_values = [
            snap.health_percent
            for snap in snapshots
            if snap.health_percent is not None and not snap.is_dead
        ]
        min_health = min(health_values) if health_values else None
        died_after = any(
            snap.is_dead and first_wall <= snap.wall_time <= last_wall + 7.0
            for snap in snapshots
        )
        ace = first_wall <= self._last_ace_wall <= last_wall + 7.0
        count = len(records)
        action_seconds = max(0.0, last_wall - first_wall)
        assist_heavy = self._active_identity.assists >= max(3, self._active_identity.kills + 2)

        if ace and count >= 2:
            label = "ACE PARTICIPATION"
        elif min_health is not None and min_health <= 10:
            label = "LOW-HEALTH TEAMFIGHT"
        elif count >= 4:
            label = "TEAMFIGHT IMPACT"
        elif count == 3:
            label = "TRIPLE ASSIST"
        else:
            label = "SUPPORT IMPACT"

        result = score_candidate(
            PlayCandidate(
                kind="assist",
                assist_count=count,
                team_kills=count,
                min_health_percent=min_health,
                died_after=died_after,
                ace=ace,
                action_seconds=action_seconds,
                assist_heavy=assist_heavy,
            ),
            enabled=self.config.smart_highlights_enabled,
            sensitivity=self.config.smart_sensitivity,
            threshold_adjustment=self.feedback.threshold_adjustment("assist", label),
        )
        if not result.keep:
            LOGGER.info(
                "Smart filter skipped %s (score %s, threshold %s): %s",
                label,
                result.score,
                result.threshold,
                ", ".join(result.reasons),
            )
            return

        victims = tuple(record.victim_name for record in records if record.victim_name)
        champions = tuple(record.victim_champion for record in records if record.victim_champion)
        killers = tuple(dict.fromkeys(record.killer_name for record in records if record.killer_name))
        LOGGER.info(
            "Automatic support highlight ready: %s (score %s): %s",
            label,
            result.score,
            ", ".join(result.reasons),
        )
        self.highlight_callback(
            HighlightRequest(
                label=label,
                event_started_at=first_wall,
                event_ended_at=last_wall,
                pre_seconds=10.0,
                post_seconds=9.0,
                match_id=self._match_id,
                player_name=self._active_identity.display_name,
                champion_name=self._active_identity.champion_name,
                game_mode=self._game_mode,
                event_game_time=first_game_time,
                event_kind="assist",
                automatic=True,
                highlight_score=result.score,
                score_reasons=result.reasons,
                victim_names=victims,
                victim_champions=champions,
                assister_names=killers,
            )
        )

    def _clear_pending_assists(self) -> None:
        self._assist_records.clear()
        self._last_assist_at = 0.0
        self._last_assist_game_time = 0.0

    def _clear_pending_kills(self) -> None:
        self._kill_records.clear()
        self._multikill_streak = 0
        self._last_player_kill_at = 0.0
        self._last_player_kill_game_time = 0.0

    def _identity_for_name(self, value: object) -> PlayerIdentity:
        for alias in _name_aliases(value):
            identity = self._identity_by_alias.get(alias)
            if identity is not None:
                return identity
        return PlayerIdentity()

    def _canonical_names(self, values: object) -> tuple[str, ...]:
        if not isinstance(values, Iterable) or isinstance(values, (str, bytes, dict)):
            return ()
        output: list[str] = []
        for value in values:
            identity = self._identity_for_name(value)
            name = identity.display_name if identity.aliases else str(value or "").strip()
            if name and name not in output:
                output.append(name)
        return tuple(output)

    def _is_active_player(self, value: object) -> bool:
        return bool(_name_aliases(value) & self._active_aliases)

    def _event_belongs_to_active_team(self, event: dict[str, Any]) -> bool:
        names: list[object] = [_event_value(event, "KillerName")]
        assisters = _event_value(event, "Assisters", [])
        if isinstance(assisters, Iterable) and not isinstance(assisters, (str, bytes, dict)):
            names.extend(assisters)

        aliases: set[str] = set()
        for name in names:
            aliases.update(_name_aliases(name))

        if aliases & self._active_aliases:
            return True
        if not self._active_team:
            return False
        return any(self._team_by_alias.get(alias) == self._active_team for alias in aliases)

    @property
    def _match_id(self) -> str:
        return self._current_match.match_id if self._current_match else ""

    @property
    def _game_mode(self) -> str:
        return self._current_match.game_mode if self._current_match else ""

    @staticmethod
    def _event_time(event: dict[str, Any]) -> float:
        return _safe_float(_event_value(event, "EventTime", 0.0))

    def _connected_status_text(self) -> str:
        identity = self._active_identity
        if identity.display_name != "Unknown player":
            champion = f" • {identity.champion_name}" if identity.champion_name else ""
            return f"Connected as {identity.display_name}{champion}"
        return "Live event detection connected"

    def _emit_match_event(self, action: str, result: str) -> None:
        if self._current_match is None:
            return
        try:
            self.match_callback(MatchLifecycleEvent(action, self._current_match, result))
        except Exception:
            LOGGER.exception("Match lifecycle callback failed")

    def _end_match(self, result: str) -> None:
        if self._current_match is None or self._match_ended:
            return
        self._flush_pending_kills(force=True)
        self._match_ended = True
        LOGGER.info("Match session ended: %s (%s)", self._current_match.match_id, result)
        self._emit_match_event("ended", result)

    def _set_status(self, text: str, connected: bool) -> None:
        if text == self._status_text and connected == self._connected:
            return
        self._status_text = text
        self._connected = connected
        try:
            self.status_callback(text, connected)
        except Exception:
            LOGGER.exception("League event status callback failed")
