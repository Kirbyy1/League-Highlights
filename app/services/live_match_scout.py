from __future__ import annotations

import json
import logging
import math
import ssl
import threading
import time
from itertools import permutations
from statistics import pstdev
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from PySide6.QtCore import QObject, QStandardPaths, QTimer, Signal

from app.config import AppConfig
from app.services.lcu_game_detector import LeagueClientConnection
from app.services.riot_rate_limiter import RiotRateLimiter
from app.services.live_match_algorithms import (
    champion_intelligence_tags,
    derive_session_metrics,
    filter_previous_encounters,
    make_evidence_tag,
    matchup_tag,
    most_valid_tags,
    pair_lane_opponents,
    premade_pair_confidence,
    premade_role_label,
    prioritize_tags,
    role_timeline_tags,
    session_tags,
    summarize_encounters,
)
from app.services.live_match_intelligence import (
    ChampionCatalog,
    EncounterStore,
    LocalBaselineStore,
    PlayerProfileDiskCache,
    RankHistoryStore,
    normalize_name,
)


_LOCAL_BASE = "https://127.0.0.1:2999/liveclientdata"
_ROLE_ORDER = {
    "TOP": 0,
    "JUNGLE": 1,
    "MIDDLE": 2,
    "BOTTOM": 3,
    "UTILITY": 4,
    "": 9,
}
_ROLE_NAMES = {
    "TOP": "Top",
    "JUNGLE": "Jungle",
    "MIDDLE": "Mid",
    "BOTTOM": "AD Carry",
    "UTILITY": "Support",
    "": "Unknown role",
}
_PLATFORM_TO_ROUTE = {
    "br1": "americas",
    "la1": "americas",
    "la2": "americas",
    "na1": "americas",
    "eun1": "europe",
    "euw1": "europe",
    "me1": "europe",
    "ru": "europe",
    "tr1": "europe",
    "jp1": "asia",
    "kr": "asia",
    "oc1": "sea",
    "ph2": "sea",
    "sg2": "sea",
    "th2": "sea",
    "tw2": "sea",
    "vn2": "sea",
}


class RiotApiError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = int(status)


@dataclass(slots=True)
class _CacheEntry:
    created_at: float
    payload: dict[str, Any]


LIVE_MATCH_PATCH_BUILD = "V21-UNCAPPED-STRICT-LARGE-SAMPLE-TAGS"


class LiveMatchScout(QObject):
    """Detect the live roster and calculate compact scouting profiles."""

    roster_changed = Signal(object)
    player_stats_changed = Signal(str, object)
    status_changed = Signal(str, str)
    poll_interval_changed = Signal(int)

    POLL_INTERVAL_MS = 1200
    IDLE_POLL_INTERVAL_MS = 2500
    READY_POLL_INTERVAL_MS = 5000
    SPECTATOR_RETRY_SECONDS = 8.0
    SPECTATOR_ROSTER_CACHE_SECONDS = 12 * 60
    PLAYER_CACHE_SECONDS = 15 * 60
    FAST_SAMPLE_SIZE = 5
    MATCH_SAMPLE_SIZE = 20
    HISTORY_MATCH_ID_COUNT = 50
    TIMELINE_SAMPLE_SIZE = 2
    MAX_CONCURRENT_PLAYERS = 3

    def __init__(self, config: AppConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        logging.info("Live Match engine %s loaded from %s", LIVE_MATCH_PATCH_BUILD, __file__)
        self._busy = False
        self._generation = 0
        self._last_roster_signature = ""
        self._last_completed_signature = ""
        self._pending_encounter_game: dict[str, Any] | None = None
        self._player_cache: dict[tuple[str, str, str], _CacheEntry] = {}
        self._match_cache: dict[str, dict[str, Any]] = {}
        self._timeline_cache: dict[str, dict[str, Any]] = {}
        self._match_inflight: dict[str, threading.Event] = {}
        self._timeline_inflight: dict[str, threading.Event] = {}
        self._cache_lock = threading.RLock()
        cache_location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation
        )
        cache_root = Path(cache_location) if cache_location else Path.home() / ".league_highlights" / "cache"
        self._live_cache_root = cache_root / "live_match"
        self._match_cache_dir = self._live_cache_root / "matches"
        self._timeline_cache_dir = self._live_cache_root / "timelines"
        self._match_cache_dir.mkdir(parents=True, exist_ok=True)
        self._timeline_cache_dir.mkdir(parents=True, exist_ok=True)
        self._profile_disk_cache = PlayerProfileDiskCache(
            self._live_cache_root / "players",
            ttl_seconds=5 * 60,
        )
        self._champion_catalog = ChampionCatalog(
            self._live_cache_root / "champion_catalog.json"
        )
        self._baseline_store = LocalBaselineStore(
            self._live_cache_root / "local_baselines.json"
        )
        self._encounter_store = EncounterStore(
            self._live_cache_root / "encounters.json"
        )
        self._rank_history_store = RankHistoryStore(
            self._live_cache_root / "rank_history.json"
        )
        self._lcu = LeagueClientConnection()
        self._riot_limiter = RiotRateLimiter()
        self._last_spectator_attempt = 0.0
        self._spectator_roster_cache: dict[str, Any] = {}
        self._spectator_roster_cached_at = 0.0
        self._last_known_self_puuid = ""
        self._last_gameflow_phase = ""
        self._last_gameflow_phase_at = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        self.poll_interval_changed.connect(self._timer.setInterval)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
        self.refresh(force=True)

    def stop(self) -> None:
        self._timer.stop()
        self._generation += 1

    def update_credentials(self) -> None:
        self._player_cache.clear()
        self._last_roster_signature = ""
        self._last_completed_signature = ""
        self._pending_encounter_game = None
        self._spectator_roster_cache = {}
        self._spectator_roster_cached_at = 0.0
        self._riot_limiter.reset()
        self._generation += 1
        self.refresh(force=True)

    def refresh(self, force: bool = False) -> None:
        if self._busy:
            return
        self._busy = True
        self._generation += 1
        generation = self._generation
        threading.Thread(
            target=self._run_cycle,
            args=(generation, force),
            name="LeagueHighlightsLiveMatch",
            daemon=True,
        ).start()

    def _run_cycle(self, generation: int, force: bool) -> None:
        try:
            api_key = str(getattr(self.config, "riot_api_key", "") or "").strip()
            platform = str(
                getattr(self.config, "riot_platform", "euw1") or "euw1"
            ).casefold()

            roster = self._discover_roster(platform, api_key)
            if generation != self._generation:
                return

            if not roster["players"]:
                phase = str(roster.get("gameflow_phase", "") or "")
                loading_phase = phase in {"GameStart", "InProgress", "Reconnect"}
                self.poll_interval_changed.emit(
                    self.POLL_INTERVAL_MS if loading_phase else self.IDLE_POLL_INTERVAL_MS
                )

                # Keep an existing loading-screen roster visible during a short
                # Spectator-v5 delay instead of flashing back to an empty state.
                if not loading_phase:
                    self._flush_pending_encounters()
                    self._last_completed_signature = ""
                if not loading_phase or not self._last_roster_signature:
                    self._last_roster_signature = ""
                    self.roster_changed.emit(roster)

                if loading_phase:
                    if not api_key:
                        self.status_changed.emit(
                            "key_missing",
                            "Loading screen detected — add a Riot API key to fetch the roster",
                        )
                    elif roster.get("spectator_rate_limited"):
                        self.status_changed.emit(
                            "rate_limited",
                            "Loading screen detected — Riot API rate limit reached",
                        )
                    else:
                        self.status_changed.emit(
                            "loading_screen",
                            "Loading screen detected — waiting for Riot's active-game roster",
                        )
                elif phase == "ChampSelect":
                    self.status_changed.emit("champ_select", "Champion select detected")
                else:
                    self.status_changed.emit("waiting", "Waiting for a League match")
                return

            self.poll_interval_changed.emit(self.READY_POLL_INTERVAL_MS)

            signature = self._stable_roster_signature(roster)
            roster_changed = signature != self._last_roster_signature
            if force or roster_changed:
                if roster_changed:
                    if (
                        self._pending_encounter_game
                        and str(self._pending_encounter_game.get("signature", ""))
                        != signature
                    ):
                        self._flush_pending_encounters()
                    self._player_cache.clear()
                    self._last_completed_signature = ""
                self._last_roster_signature = signature
                self.roster_changed.emit(roster)
            elif self._analysis_is_current(signature, force):
                # Keep the already populated cards untouched.  Port 2999 taking
                # over from Spectator no longer rebuilds all ten cards midgame.
                self.status_changed.emit("ready", "Live match ready — analysis cached")
                return

            if not api_key:
                self.status_changed.emit(
                    "key_missing",
                    "Players detected — add a Riot API key for ranks and scouting tags",
                )
                return

            total = len(roster["players"])
            completed = 0
            profiles: dict[str, dict[str, Any]] = {}

            self.status_changed.emit(
                "loading",
                f"Loading live scouting for {total} players…",
            )

            with ThreadPoolExecutor(
                max_workers=max(1, min(self.MAX_CONCURRENT_PLAYERS, total)),
                thread_name_prefix="LiveScoutWorker",
            ) as executor:
                future_to_player = {
                    executor.submit(self._player_profile, player, platform, api_key): player
                    for player in roster["players"]
                }

                for future in as_completed(future_to_player):
                    if generation != self._generation:
                        return

                    player = future_to_player[future]
                    player_key = str(player.get("player_key", ""))
                    try:
                        stats = future.result()
                    except RiotApiError as exc:
                        if exc.status in {401, 403}:
                            self.status_changed.emit(
                                "key_invalid",
                                "Riot API key was rejected or has expired",
                            )
                            return
                        if exc.status == 429:
                            self.status_changed.emit(
                                "rate_limited",
                                "Riot API rate limit reached — refresh again shortly",
                            )
                            return
                        stats = {"state": "error", "message": str(exc)}
                    except Exception as exc:
                        logging.debug("Live Match player analysis failed", exc_info=True)
                        stats = {"state": "error", "message": str(exc)}

                    profiles[player_key] = stats
                    completed += 1
                    self.status_changed.emit(
                        "loading",
                        f"Analysing players… {completed}/{total}",
                    )

            # Team-wide role assignment prevents impossible results such as
            # three mids on one team when the Live Client leaves positions empty.
            self._assign_team_roles(roster, profiles)

            # Pair assigned roles across teams and compare only historical
            # player profiles. This is not champion-counter advice.
            self._apply_lane_matchups(roster, profiles)

            # Shared match history is used conservatively for premade groups.
            self._apply_premade_groups(roster, profiles)

            # Show whether the local player has met someone before, including
            # whether they were previously an ally or an enemy.
            self._apply_encounter_history(roster, profiles)

            # Learn local role benchmarks over time. Percentile tags are only
            # enabled after enough profiles have been collected.
            self._record_local_baselines(profiles)

            # Apply every finding that independently clears the strict large-sample
            # validation rules. Cards are still updated once, after role assignment,
            # matchup pairing, premades and encounters are complete, so the visible
            # list does not churn while analysis progresses.
            for player_key, stats in profiles.items():
                stats["tags"] = most_valid_tags(
                    list(stats.get("tags", ()))
                )
                stats["state"] = (
                    "ready"
                    if str(stats.get("state", "")) in {"fast", "ready"}
                    else str(stats.get("state", "unavailable"))
                )
                self.player_stats_changed.emit(player_key, stats)

            # Keep the current roster pending.  It is persisted only when the
            # game ends or a genuinely new roster appears, so current players can
            # never become their own "previous encounter" during a refresh.
            self._stage_live_encounters(roster, profiles, signature)
            self._last_completed_signature = signature

            self.status_changed.emit(
                "ready",
                f"Live match ready — {completed} players analysed",
            )
        except (URLError, TimeoutError, ConnectionError, json.JSONDecodeError, OSError):
            self._last_roster_signature = ""
            self.roster_changed.emit(
                {"players": [], "allies": [], "enemies": [], "active_team": ""}
            )
            self.status_changed.emit("waiting", "Waiting for an active League match")
        except Exception:
            logging.exception("Live Match refresh failed")
            self.status_changed.emit("error", "Live Match could not refresh")
        finally:
            self._busy = False

    @staticmethod
    def _stable_roster_signature(roster: dict[str, Any]) -> str:
        """Create a source-independent game identity.

        Spectator uses PUUID player keys while port 2999 uses Riot IDs.  The old
        signature therefore changed as soon as the match process became ready,
        rebuilding the page midgame.  Team + champion composition is stable
        across both sources and is reset whenever gameflow leaves the match.
        """

        parts: list[str] = []
        for player in roster.get("players", ()):
            if not isinstance(player, dict):
                continue
            team = str(player.get("team", "") or "").upper()
            champion = normalize_name(
                str(player.get("champion", "") or "unknown")
            )
            identity = normalize_name(
                str(
                    player.get("riot_id", "")
                    or player.get("game_name", "")
                    or ""
                )
            )
            # Spectator identities are resolved from PUUID before this point,
            # while port 2999 exposes the Riot ID directly.  Using the Riot ID
            # keeps the signature identical across the source hand-off and also
            # distinguishes consecutive matches with similar champion drafts.
            stable_player = identity or champion
            parts.append(f"{team}:{stable_player}:{champion}")
        return "|".join(sorted(parts))

    def _analysis_is_current(self, signature: str, force: bool = False) -> bool:
        return bool(
            signature
            and not force
            and signature == self._last_completed_signature
        )

    def _discover_roster(self, platform: str, api_key: str) -> dict[str, Any]:
        """Prefer port 2999, then use LCU + Spectator-v5 during loading."""
        try:
            roster = self._read_local_roster()
            if roster.get("players"):
                roster["roster_source"] = "live_client"
                roster["gameflow_phase"] = "InProgress"
                return roster
        except Exception:
            pass

        phase = ""
        current_summoner: dict[str, Any] = {}
        try:
            phase = self._lcu.gameflow_phase()
            self._last_gameflow_phase = phase
            self._last_gameflow_phase_at = time.monotonic()
        except Exception:
            if time.monotonic() - self._last_gameflow_phase_at <= 8.0:
                phase = self._last_gameflow_phase
            else:
                phase = ""

        if phase not in {"GameStart", "InProgress", "Reconnect"}:
            self._spectator_roster_cache = {}
            self._spectator_roster_cached_at = 0.0
            return {
                "players": [],
                "allies": [],
                "enemies": [],
                "active_team": "",
                "game_started_at": 0,
                "gameflow_phase": phase,
                "roster_source": "lcu",
            }

        try:
            current_summoner = self._lcu.current_summoner()
        except Exception:
            current_summoner = {}

        self_puuid = str(
            current_summoner.get("puuid", "")
            or current_summoner.get("playerUuid", "")
            or self._last_known_self_puuid
            or ""
        )
        if self_puuid:
            self._last_known_self_puuid = self_puuid

        if not api_key or not self_puuid:
            return {
                "players": [],
                "allies": [],
                "enemies": [],
                "active_team": "",
                "game_started_at": 0,
                "gameflow_phase": phase,
                "roster_source": "lcu",
            }

        now = time.monotonic()
        if (
            self._spectator_roster_cache.get("players")
            and now - self._spectator_roster_cached_at
            <= self.SPECTATOR_ROSTER_CACHE_SECONDS
        ):
            cached_roster = dict(self._spectator_roster_cache)
            cached_roster["gameflow_phase"] = phase
            cached_roster["roster_source"] = "spectator_cache"
            return cached_roster
        if now - self._last_spectator_attempt < self.SPECTATOR_RETRY_SECONDS:
            return {
                "players": [],
                "allies": [],
                "enemies": [],
                "active_team": "",
                "game_started_at": 0,
                "gameflow_phase": phase,
                "roster_source": "spectator_wait",
            }
        self._last_spectator_attempt = now

        try:
            roster = self._read_spectator_roster(
                self_puuid=self_puuid,
                platform=platform,
                api_key=api_key,
            )
        except RiotApiError as exc:
            if exc.status == 404:
                roster = {}
            elif exc.status == 429:
                return {
                    "players": [],
                    "allies": [],
                    "enemies": [],
                    "active_team": "",
                    "game_started_at": 0,
                    "gameflow_phase": phase,
                    "roster_source": "spectator",
                    "spectator_rate_limited": True,
                }
            else:
                raise

        if roster.get("players"):
            roster["gameflow_phase"] = phase
            roster["roster_source"] = "spectator"
            self._spectator_roster_cache = dict(roster)
            self._spectator_roster_cached_at = time.monotonic()
            return roster

        return {
            "players": [],
            "allies": [],
            "enemies": [],
            "active_team": "",
            "game_started_at": 0,
            "gameflow_phase": phase,
            "roster_source": "spectator",
        }

    def _read_spectator_roster(
        self,
        self_puuid: str,
        platform: str,
        api_key: str,
    ) -> dict[str, Any]:
        url = (
            f"https://{platform}.api.riotgames.com/lol/spectator/v5/"
            f"active-games/by-summoner/{quote(self_puuid, safe='')}"
        )
        payload = self._riot_json(url, api_key)
        if not isinstance(payload, dict):
            return {}

        participants = payload.get("participants", [])
        if not isinstance(participants, list):
            participants = []

        players: list[dict[str, Any]] = []
        active_team = ""
        for index, raw in enumerate(participants):
            if not isinstance(raw, dict):
                continue

            puuid = str(raw.get("puuid", "") or raw.get("encryptedPUUID", "") or "")
            riot_id = str(raw.get("riotId", "") or "").strip()
            game_name = str(raw.get("gameName", "") or raw.get("riotIdGameName", "") or "").strip()
            tag_line = str(raw.get("tagLine", "") or raw.get("riotIdTagLine", "") or "").strip()
            if not riot_id and game_name and tag_line:
                riot_id = f"{game_name}#{tag_line}"
            if not game_name and "#" in riot_id:
                game_name, tag_line = riot_id.rsplit("#", 1)
            if not riot_id:
                riot_id = str(raw.get("summonerName", "") or "").strip()
            if not riot_id:
                riot_id = f"Player {index + 1}"

            champion_id = int(raw.get("championId", 0) or 0)
            champion = (
                self._champion_catalog.champion_name(champion_id)
                if champion_id
                else "Unknown"
            )

            team_id = int(raw.get("teamId", 0) or 0)
            team = "ORDER" if team_id == 100 else "CHAOS" if team_id == 200 else str(team_id)
            spell_ids = {
                int(raw.get("spell1Id", 0) or 0),
                int(raw.get("spell2Id", 0) or 0),
            }
            role = str(raw.get("teamPosition", "") or raw.get("position", "") or "").upper()
            if not role and 11 in spell_ids:
                role = "JUNGLE"

            is_active = bool(puuid and puuid == self_puuid)
            if is_active:
                active_team = team

            player_key = (puuid or riot_id or f"{team}:{champion}:{index}").casefold()
            players.append(
                {
                    "player_key": player_key,
                    "puuid": puuid,
                    "riot_id": riot_id,
                    "game_name": game_name,
                    "tag_line": tag_line,
                    "champion": champion,
                    "champion_id": champion_id,
                    "role": role,
                    "team": team,
                    "is_active": is_active,
                    "spells": ["Smite"] if 11 in spell_ids else [],
                    "roster_source": "spectator",
                }
            )

        # Spectator-v5 may omit Riot ID strings. Resolve only missing identities;
        # profile analysis can otherwise use the PUUID directly.
        missing = [p for p in players if p.get("puuid") and not p.get("game_name")]
        if missing:
            route = _PLATFORM_TO_ROUTE.get(platform, "europe")
            with ThreadPoolExecutor(max_workers=min(4, len(missing))) as executor:
                future_map = {
                    executor.submit(
                        self._account_by_puuid,
                        str(player.get("puuid", "")),
                        route,
                        api_key,
                    ): player
                    for player in missing
                }
                for future in as_completed(future_map):
                    player = future_map[future]
                    try:
                        account = future.result()
                    except Exception:
                        continue
                    game_name = str(account.get("gameName", "") or "").strip()
                    tag_line = str(account.get("tagLine", "") or "").strip()
                    if game_name:
                        player["game_name"] = game_name
                        player["tag_line"] = tag_line
                        player["riot_id"] = (
                            f"{game_name}#{tag_line}" if tag_line else game_name
                        )
                        player["player_key"] = str(player.get("puuid", "") or player["riot_id"]).casefold()

        players.sort(
            key=lambda player: (
                0 if player.get("team") == active_team else 1,
                _ROLE_ORDER.get(str(player.get("role", "")), 8),
                str(player.get("riot_id", "")).casefold(),
            )
        )
        allies = [p for p in players if p.get("team") == active_team] if active_team else [p for p in players if p.get("team") == "ORDER"]
        enemies = [p for p in players if p.get("team") != active_team] if active_team else [p for p in players if p.get("team") == "CHAOS"]

        game_start_ms = int(payload.get("gameStartTime", 0) or 0)
        game_started_at = game_start_ms // 1000 if game_start_ms > 0 else 0
        game_started_at = (game_started_at // 60) * 60 if game_started_at else 0
        return {
            "players": players,
            "allies": allies,
            "enemies": enemies,
            "active_team": active_team,
            "game_started_at": game_started_at,
            "game_id": str(payload.get("gameId", "") or ""),
            "queue_id": int(payload.get("gameQueueConfigId", 0) or 0),
        }

    def _account_by_puuid(
        self,
        puuid: str,
        route: str,
        api_key: str,
    ) -> dict[str, Any]:
        url = (
            f"https://{route}.api.riotgames.com/riot/account/v1/accounts/by-puuid/"
            f"{quote(puuid, safe='')}"
        )
        payload = self._riot_json(url, api_key)
        return payload if isinstance(payload, dict) else {}

    def _read_local_roster(self) -> dict[str, Any]:
        players_raw = self._local_json("playerlist")
        try:
            active_raw = self._local_json("activeplayer")
        except Exception:
            active_raw = {}
        try:
            game_stats = self._local_json("gamestats")
        except Exception:
            game_stats = {}

        game_time = float(game_stats.get("gameTime", 0) or 0) if isinstance(game_stats, dict) else 0.0
        game_started_at = int(time.time() - game_time) if game_time > 0 else 0
        # A minute bucket remains stable during refreshes but changes next match.
        game_started_at = (game_started_at // 60) * 60 if game_started_at else 0

        active_riot_id = str(active_raw.get("riotId", "") or "").casefold()
        active_name = str(active_raw.get("riotIdGameName", "") or "").casefold()
        active_tag = str(active_raw.get("riotIdTagLine", "") or "").casefold()

        players: list[dict[str, Any]] = []
        active_team = ""

        for index, raw in enumerate(players_raw if isinstance(players_raw, list) else []):
            game_name = str(raw.get("riotIdGameName", "") or "").strip()
            tag_line = str(raw.get("riotIdTagLine", "") or "").strip()
            riot_id = str(raw.get("riotId", "") or "").strip()
            if not riot_id and game_name and tag_line:
                riot_id = f"{game_name}#{tag_line}"
            if not game_name and "#" in riot_id:
                game_name, tag_line = riot_id.rsplit("#", 1)
            if not game_name:
                game_name = str(raw.get("summonerName", "") or "Unknown player").strip()

            team = str(raw.get("team", "") or "").upper()
            champion = str(raw.get("championName", "") or "Unknown").strip()
            role = str(raw.get("position", "") or "").upper()
            spell_names = self._spell_names(raw)
            if not role and any("smite" in name.casefold() for name in spell_names):
                role = "JUNGLE"

            player_key = (riot_id or f"{team}:{champion}:{index}").casefold()
            is_active = bool(
                (riot_id and riot_id.casefold() == active_riot_id)
                or (
                    game_name.casefold() == active_name
                    and tag_line.casefold() == active_tag
                    and active_name
                )
            )
            if is_active:
                active_team = team

            players.append(
                {
                    "player_key": player_key,
                    "riot_id": riot_id or game_name,
                    "game_name": game_name,
                    "tag_line": tag_line,
                    "champion": champion,
                    "role": role,
                    "team": team,
                    "is_active": is_active,
                    "spells": spell_names,
                }
            )

        players.sort(
            key=lambda player: (
                0 if player.get("team") == active_team else 1,
                _ROLE_ORDER.get(str(player.get("role", "")), 8),
                str(player.get("riot_id", "")).casefold(),
            )
        )

        if active_team:
            allies = [p for p in players if p.get("team") == active_team]
            enemies = [p for p in players if p.get("team") != active_team]
        else:
            allies = [p for p in players if p.get("team") == "ORDER"]
            enemies = [p for p in players if p.get("team") == "CHAOS"]

        return {
            "players": players,
            "allies": allies,
            "enemies": enemies,
            "active_team": active_team,
            "game_started_at": game_started_at,
        }

    @staticmethod
    def _spell_names(raw: dict[str, Any]) -> list[str]:
        names: list[str] = []
        spells = raw.get("summonerSpells", {})
        if isinstance(spells, dict):
            for value in spells.values():
                if isinstance(value, dict):
                    name = str(
                        value.get("displayName", "")
                        or value.get("rawDisplayName", "")
                        or ""
                    ).strip()
                    if name:
                        names.append(name)
        return names

    def _player_profile(
        self,
        player: dict[str, Any],
        platform: str,
        api_key: str,
    ) -> dict[str, Any]:
        player_key = str(player.get("player_key", ""))
        champion = str(player.get("champion", "") or "")
        cache_key = (platform, player_key, champion.casefold())
        now = time.monotonic()
        cached = self._player_cache.get(cache_key)
        if cached and now - cached.created_at < self.PLAYER_CACHE_SECONDS:
            return dict(cached.payload)

        game_name = str(player.get("game_name", "") or "").strip()
        tag_line = str(player.get("tag_line", "") or "").strip()
        route = _PLATFORM_TO_ROUTE.get(platform, "europe")
        puuid = str(player.get("puuid", "") or "").strip()

        if not puuid:
            if not game_name or not tag_line:
                payload = {"state": "unavailable", "message": "Riot ID unavailable"}
                self._player_cache[cache_key] = _CacheEntry(now, payload)
                return payload
            account_url = (
                f"https://{route}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/"
                f"{quote(game_name, safe='')}/{quote(tag_line, safe='')}"
            )
            account = self._riot_json(account_url, api_key)
            puuid = str(account.get("puuid", "") or "")
            if not puuid:
                raise RiotApiError(502, "Riot account response did not include a PUUID")

        if bool(player.get("is_active")) and puuid:
            self._last_known_self_puuid = puuid

        disk_cached = self._profile_disk_cache.load(puuid, champion)
        if disk_cached and int(disk_cached.get("profile_schema", 0) or 0) >= 21:
            disk_cached["current_role"] = str(player.get("role", "") or "")
            disk_cached["puuid"] = puuid
            for dynamic_key in (
                "premade_size",
                "premade_members",
                "premade_games_together",
                "premade_win_rate",
                "encounter_count",
                "encounter_local_ally_count",
                "encounter_local_enemy_count",
                "encounter_ranked_ally_count",
                "encounter_ranked_enemy_count",
                "encounter_last_seen",
                "encounter_history",
                "encounter_history_count",
                "encounter_wins",
                "encounter_losses",
                "encounter_win_rate",
                "encounter_ally_count",
                "encounter_enemy_count",
                "encounter_ally_wins",
                "encounter_enemy_wins",
                "lane_opponent",
                "premade_confidence",
                "premade_sessions",
                "premade_consecutive_games",
                "premade_role_pair",
                "premade_pair_details",
                "premade_evidence_scope",
            ):
                disk_cached.pop(dynamic_key, None)
            disk_cached["tags"] = [
                tag
                for tag in list(disk_cached.get("tags", ()))
                if not (
                    isinstance(tag, dict)
                    and (
                        str(tag.get("text", "")).startswith("PREMADE ")
                        or str(tag.get("text", "")).startswith("SEEN ")
                        or str(tag.get("text", ""))
                        in {
                            "ALLY BEFORE",
                            "ENEMY BEFORE",
                            "PLAYED BEFORE",
                        }
                    )
                )
            ]
            self._player_cache[cache_key] = _CacheEntry(now, disk_cached)
            return disk_cached

        # These are lightweight profile calls compared with downloading match
        # details, so show them before the deeper analysis starts.
        summoner_profile = self._summoner_profile(puuid, platform, api_key)
        ranked = self._ranked_entry(puuid, platform, api_key)
        tracked_previous = self._rank_history_store.previous(puuid)
        ranked = self._merge_previous_season(ranked, tracked_previous)
        self._rank_history_store.record_current(puuid, ranked)
        mastery = self._champion_mastery(puuid, champion, platform, api_key)

        current_role = str(player.get("role", "") or "")

        # Fetch more IDs than detailed matches. The extra IDs improve premade and
        # previous-encounter detection without multiplying match-detail requests.
        match_ids = self._recent_ranked_match_ids(
            puuid,
            route,
            api_key,
            self.HISTORY_MATCH_ID_COUNT,
        )

        samples: list[dict[str, Any]] = []
        fast_limit = min(self.FAST_SAMPLE_SIZE, len(match_ids))
        for match_id in match_ids[:fast_limit]:
            sample = self._sample_for_match(match_id, puuid, route, api_key)
            if sample is not None:
                samples.append(sample)

        riot_previous = self._previous_season_from_samples(samples)
        ranked = self._merge_previous_season(ranked, riot_previous)

        fast_analysis = self._analyse_samples(samples, champion)
        fast_role = current_role or str(fast_analysis.get("main_role", "") or "")
        fast_role_status = self._role_status(fast_role, fast_analysis)
        fast_percentiles = self._baseline_store.percentiles(fast_role, fast_analysis)
        fast_tags = self._build_tags(
            ranked,
            fast_analysis,
            fast_role,
            {},
            mastery,
            fast_percentiles,
        )
        if fast_role_status["role_state"] not in {"unclear", "main"}:
            fast_tags.insert(0, self._role_tag(fast_role, fast_analysis, fast_role_status))

        fast_payload = self._compose_profile_payload(
            state="fast",
            puuid=puuid,
            current_role=current_role,
            inferred_role=fast_role,
            summoner_profile=summoner_profile,
            ranked=ranked,
            mastery=mastery,
            analysis=fast_analysis,
            timeline={},
            role_status=fast_role_status,
            match_ids=match_ids,
            tags=fast_tags,
            local_percentiles=fast_percentiles,
        )
        # Keep the UI stable while the deep profile is still being built.
        # The completed profile is applied once after team-wide annotations.

        # Continue to the deeper 20-game profile. Already downloaded fast samples
        # are reused, and immutable match data comes from the disk cache thereafter.
        for match_id in match_ids[fast_limit : self.MATCH_SAMPLE_SIZE]:
            sample = self._sample_for_match(match_id, puuid, route, api_key)
            if sample is not None:
                samples.append(sample)

        riot_previous = self._previous_season_from_samples(samples)
        ranked = self._merge_previous_season(ranked, riot_previous)

        analysis = self._analyse_samples(samples, champion)
        inferred_role = current_role or str(analysis.get("main_role", "") or "")
        role_status = self._role_status(inferred_role, analysis)

        timeline_sample_size = (
            min(3, len(samples))
            if inferred_role == "JUNGLE"
            else min(self.TIMELINE_SAMPLE_SIZE, len(samples))
        )
        timeline = self._analyse_timelines(
            samples[:timeline_sample_size],
            puuid,
            inferred_role,
            route,
            api_key,
        )

        local_percentiles = self._baseline_store.percentiles(
            inferred_role,
            analysis,
        )
        tags = self._build_tags(
            ranked,
            analysis,
            inferred_role,
            timeline,
            mastery,
            local_percentiles,
        )
        if role_status["role_state"] not in {"unclear", "main"}:
            tags.insert(0, self._role_tag(inferred_role, analysis, role_status))

        payload = self._compose_profile_payload(
            state="ready",
            puuid=puuid,
            current_role=current_role,
            inferred_role=inferred_role,
            summoner_profile=summoner_profile,
            ranked=ranked,
            mastery=mastery,
            analysis=analysis,
            timeline=timeline,
            role_status=role_status,
            match_ids=match_ids,
            tags=tags,
            local_percentiles=local_percentiles,
        )
        self._player_cache[cache_key] = _CacheEntry(now, payload)
        self._profile_disk_cache.save(puuid, champion, payload)
        return dict(payload)

    def _sample_for_match(
        self,
        match_id: str,
        puuid: str,
        route: str,
        api_key: str,
    ) -> dict[str, Any] | None:
        match = self._match(match_id, route, api_key)
        info = match.get("info", {}) if isinstance(match, dict) else {}
        if int(info.get("queueId", 0) or 0) != 420:
            # Do not allow stale or incorrectly cached non-ranked matches to
            # influence win rate, tags, champion stats or role analysis.
            return None
        participant = self._participant(match, puuid)
        if participant is None:
            return None
        return {
            "match_id": match_id,
            "participant": participant,
            "info": match.get("info", {}) if isinstance(match, dict) else {},
        }

    def _compose_profile_payload(
        self,
        *,
        state: str,
        puuid: str,
        current_role: str,
        inferred_role: str,
        summoner_profile: dict[str, Any],
        ranked: dict[str, Any],
        mastery: dict[str, Any],
        analysis: dict[str, Any],
        timeline: dict[str, Any],
        role_status: dict[str, Any],
        match_ids: list[str],
        tags: list[dict[str, Any]],
        local_percentiles: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "profile_schema": 21,
            "state": state,
            "puuid": puuid,
            "ranked_only": True,
            "ranked_queue": "RANKED_SOLO_5x5",
            **summoner_profile,
            **ranked,
            "ranked_wins": int(ranked.get("wins", 0) or 0),
            "ranked_losses": int(ranked.get("losses", 0) or 0),
            "ranked_games": int(ranked.get("games", 0) or 0),
            "ranked_win_rate": ranked.get("win_rate"),
            **mastery,
            **analysis,
            **timeline,
            "current_role": current_role,
            "inferred_role": inferred_role,
            "assigned_role": inferred_role,
            "role_name": _ROLE_NAMES.get(inferred_role, "Unknown role"),
            "main_role_name": _ROLE_NAMES.get(
                str(analysis.get("main_role", "")),
                "Unknown",
            ),
            "secondary_role_name": _ROLE_NAMES.get(
                str(analysis.get("secondary_role", "")),
                "Unknown",
            ),
            **role_status,
            "recent_match_ids": list(match_ids),
            "local_percentiles": dict(local_percentiles),
            "tags": list(tags[:8]),
        }

    @staticmethod
    def _role_tag(
        current_role: str,
        analysis: dict[str, Any],
        role_status: dict[str, Any],
    ) -> dict[str, Any]:
        sample = int(analysis.get("sample_games", 0) or 0)
        return make_evidence_tag(
            str(role_status["role_status_label"]),
            str(role_status["role_status_tone"]),
            (
                f"Assigned current role: {_ROLE_NAMES.get(current_role, 'Unknown')} · "
                f"Recent main role: "
                f"{_ROLE_NAMES.get(str(analysis.get('main_role', '')), 'Unknown')} "
                f"({float(analysis.get('role_share', 0) or 0) * 100:.0f}% of games)"
            ),
            priority=94 if str(role_status.get("role_state", "")) == "off_role" else 82,
            group="role_status",
            category="role",
            evidence_games=sample,
        )

    def _summoner_profile(self, puuid: str, platform: str, api_key: str) -> dict[str, Any]:
        url = (
            f"https://{platform}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/"
            f"{quote(puuid, safe='')}"
        )
        try:
            payload = self._riot_json(url, api_key)
        except RiotApiError as exc:
            if exc.status in {400, 404}:
                return {"account_level": None, "profile_icon_id": None}
            raise

        return {
            "account_level": int(payload.get("summonerLevel", 0) or 0),
            "profile_icon_id": int(payload.get("profileIconId", 0) or 0),
        }

    def _champion_mastery(
        self,
        puuid: str,
        champion_name: str,
        platform: str,
        api_key: str,
    ) -> dict[str, Any]:
        champion_id = self._champion_catalog.champion_id(champion_name)
        url = (
            f"https://{platform}.api.riotgames.com/lol/champion-mastery/v4/"
            f"champion-masteries/by-puuid/{quote(puuid, safe='')}"
        )
        try:
            payload = self._riot_json(url, api_key)
        except RiotApiError as exc:
            if exc.status in {400, 404}:
                payload = []
            else:
                raise

        entries = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
        entries.sort(
            key=lambda item: int(item.get("championPoints", 0) or 0),
            reverse=True,
        )

        current: dict[str, Any] | None = None
        if champion_id is not None:
            current = next(
                (
                    item
                    for item in entries
                    if int(item.get("championId", 0) or 0) == champion_id
                ),
                None,
            )

        mastery_rank = None
        if current is not None:
            mastery_rank = entries.index(current) + 1

        last_play_ms = int(current.get("lastPlayTime", 0) or 0) if current else 0
        last_play_days = None
        if last_play_ms:
            last_play_days = max(
                0,
                int((time.time() - last_play_ms / 1000.0) // (24 * 60 * 60)),
            )

        top_masteries = []
        for item in entries[:5]:
            champion_key = int(item.get("championId", 0) or 0)
            top_masteries.append(
                {
                    "champion_id": champion_key,
                    "champion_name": self._champion_catalog.champion_name(champion_key),
                    "level": int(item.get("championLevel", 0) or 0),
                    "points": int(item.get("championPoints", 0) or 0),
                }
            )

        return {
            "mastery_level": int(current.get("championLevel", 0) or 0) if current else 0,
            "mastery_points": int(current.get("championPoints", 0) or 0) if current else 0,
            "mastery_rank": mastery_rank,
            "mastery_last_play_days": last_play_days,
            "mastery_total_points": sum(
                int(item.get("championPoints", 0) or 0)
                for item in entries
            ),
            "mastery_champions": len(entries),
            "top_masteries": top_masteries,
        }

    @staticmethod
    def _format_rank(tier: str, division: str = "", lp: int = 0) -> str:
        normalized_tier = str(tier or "UNRANKED").upper()
        normalized_division = str(division or "").upper()
        if normalized_tier == "UNRANKED":
            return "Unranked"
        text = normalized_tier.title()
        if (
            normalized_tier not in {"MASTER", "GRANDMASTER", "CHALLENGER"}
            and normalized_division
        ):
            text += f" {normalized_division}"
        if lp:
            text += f" · {int(lp)} LP"
        return text

    @classmethod
    def _extract_previous_season_payload(
        cls,
        payload: Any,
    ) -> dict[str, Any] | None:
        """Best-effort extraction of Riot-reported previous-season rank fields."""
        tier_keys = (
            "highestPreviousSeasonEndTier",
            "previousSeasonHighestTier",
            "previousSeasonTier",
            "previousTier",
            "highestTierAchieved",
        )
        division_keys = (
            "highestPreviousSeasonEndDivision",
            "previousSeasonHighestDivision",
            "previousSeasonDivision",
            "previousDivision",
        )

        def walk(value: Any) -> dict[str, Any] | None:
            if isinstance(value, dict):
                tier = ""
                division = ""
                for key in tier_keys:
                    candidate = value.get(key)
                    if candidate:
                        tier = str(candidate).upper()
                        break
                for key in division_keys:
                    candidate = value.get(key)
                    if candidate:
                        division = str(candidate).upper()
                        break
                if tier and tier not in {"NA", "NONE", "UNRANKED"}:
                    return {
                        "previous_season_rank": cls._format_rank(tier, division),
                        "previous_season_tier": tier,
                        "previous_season_division": division,
                        "previous_season_source": "riot_reported",
                        "previous_season_available": True,
                    }
                for child in value.values():
                    result = walk(child)
                    if result:
                        return result
            elif isinstance(value, list):
                for child in value:
                    result = walk(child)
                    if result:
                        return result
            return None

        return walk(payload)

    @staticmethod
    def _merge_previous_season(
        ranked: dict[str, Any],
        previous: dict[str, Any] | None,
    ) -> dict[str, Any]:
        result = dict(ranked)
        if previous:
            source = str(previous.get("source", "") or "")
            tier = str(
                previous.get("previous_season_tier", "")
                or previous.get("tier", "")
                or ""
            ).upper()
            division = str(
                previous.get("previous_season_division", "")
                or previous.get("division", "")
                or ""
            ).upper()
            rank = str(
                previous.get("previous_season_rank", "")
                or previous.get("rank", "")
                or ""
            )
            if not rank and tier:
                rank = LiveMatchScout._format_rank(tier, division)
            if rank and rank.casefold() != "unranked":
                result.update(
                    {
                        "previous_season_rank": rank,
                        "previous_season_tier": tier,
                        "previous_season_division": division,
                        "previous_season_source": (
                            "riot_reported"
                            if source == "riot_reported"
                            else "local_snapshot"
                        ),
                        "previous_season_available": True,
                    }
                )
        result.setdefault("previous_season_rank", "")
        result.setdefault("previous_season_tier", "")
        result.setdefault("previous_season_division", "")
        result.setdefault("previous_season_source", "unavailable")
        result.setdefault("previous_season_available", False)
        return result

    @classmethod
    def _previous_season_from_samples(
        cls,
        samples: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        for sample in samples:
            participant = sample.get("participant", {})
            result = cls._extract_previous_season_payload(participant)
            if result:
                result["source"] = "riot_reported"
                return result
        return None

    def _ranked_entry(self, puuid: str, platform: str, api_key: str) -> dict[str, Any]:
        # Solo/Duo only. Flex, normals, ARAM and other queues are intentionally
        # excluded from the displayed win rate.
        url = (
            f"https://{platform}.api.riotgames.com/lol/league/v4/entries/by-puuid/"
            f"{quote(puuid, safe='')}"
        )
        try:
            entries = self._riot_json(url, api_key)
        except RiotApiError as exc:
            if exc.status in {400, 404}:
                logging.info("Rank lookup unavailable for PUUID: %s", exc)
                entries = []
            else:
                raise

        if not isinstance(entries, list):
            entries = []

        previous = self._extract_previous_season_payload(entries) or {}
        solo = next(
            (
                entry
                for entry in entries
                if isinstance(entry, dict)
                and str(entry.get("queueType", "")) == "RANKED_SOLO_5x5"
            ),
            None,
        )

        if solo is None:
            return {
                "rank": "Unranked",
                "tier": "UNRANKED",
                "division": "",
                "lp": 0,
                "wins": 0,
                "losses": 0,
                "games": 0,
                "win_rate": None,
                "ranked_queue": "RANKED_SOLO_5x5",
                **previous,
            }

        tier = str(solo.get("tier", "UNRANKED") or "UNRANKED").upper()
        division = str(solo.get("rank", "") or "").upper()
        lp = int(solo.get("leaguePoints", 0) or 0)
        wins = int(solo.get("wins", 0) or 0)
        losses = int(solo.get("losses", 0) or 0)
        games = wins + losses
        win_rate = round((wins / games) * 100.0, 1) if games else None

        return {
            "rank": self._format_rank(tier, division, lp),
            "tier": tier,
            "division": division,
            "lp": lp,
            "wins": wins,
            "losses": losses,
            "games": games,
            "win_rate": win_rate,
            "ranked_queue": "RANKED_SOLO_5x5",
            **previous,
        }

    def _recent_ranked_match_ids(
        self,
        puuid: str,
        route: str,
        api_key: str,
        count: int,
    ) -> list[str]:
        url = (
            f"https://{route}.api.riotgames.com/lol/match/v5/matches/by-puuid/"
            f"{quote(puuid, safe='')}/ids?queue=420&start=0&count={int(count)}"
        )
        payload = self._riot_json(url, api_key)
        return [str(item) for item in payload] if isinstance(payload, list) else []


    def _cache_file(self, directory: Path, match_id: str) -> Path:
        safe_id = "".join(
            character
            for character in str(match_id)
            if character.isalnum() or character in {"_", "-"}
        )
        return directory / f"{safe_id}.json"

    def _match(
        self,
        match_id: str,
        route: str,
        api_key: str,
    ) -> dict[str, Any]:
        with self._cache_lock:
            cached = self._match_cache.get(match_id)
            if cached is not None:
                return cached
            event = self._match_inflight.get(match_id)
            if event is None:
                event = threading.Event()
                self._match_inflight[match_id] = event
                owner = True
            else:
                owner = False

        if not owner:
            event.wait(timeout=12.0)
            with self._cache_lock:
                return dict(self._match_cache.get(match_id, {}))

        try:
            disk_path = self._cache_file(self._match_cache_dir, match_id)
            if disk_path.exists():
                try:
                    payload = json.loads(
                        disk_path.read_text(encoding="utf-8")
                    )
                    if isinstance(payload, dict):
                        with self._cache_lock:
                            self._match_cache[match_id] = payload
                        return payload
                except (OSError, ValueError, TypeError):
                    pass

            url = (
                f"https://{route}.api.riotgames.com/lol/match/v5/matches/"
                f"{quote(match_id, safe='')}"
            )
            payload = self._riot_json(url, api_key)
            result = payload if isinstance(payload, dict) else {}
            with self._cache_lock:
                self._match_cache[match_id] = result
            try:
                disk_path.write_text(
                    json.dumps(
                        result,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass
            return result
        finally:
            with self._cache_lock:
                inflight = self._match_inflight.pop(match_id, None)
                if inflight is not None:
                    inflight.set()

    def _timeline(
        self,
        match_id: str,
        route: str,
        api_key: str,
    ) -> dict[str, Any]:
        with self._cache_lock:
            cached = self._timeline_cache.get(match_id)
            if cached is not None:
                return cached
            event = self._timeline_inflight.get(match_id)
            if event is None:
                event = threading.Event()
                self._timeline_inflight[match_id] = event
                owner = True
            else:
                owner = False

        if not owner:
            event.wait(timeout=12.0)
            with self._cache_lock:
                return dict(self._timeline_cache.get(match_id, {}))

        try:
            disk_path = self._cache_file(
                self._timeline_cache_dir,
                match_id,
            )
            if disk_path.exists():
                try:
                    payload = json.loads(
                        disk_path.read_text(encoding="utf-8")
                    )
                    if isinstance(payload, dict):
                        with self._cache_lock:
                            self._timeline_cache[match_id] = payload
                        return payload
                except (OSError, ValueError, TypeError):
                    pass

            url = (
                f"https://{route}.api.riotgames.com/lol/match/v5/matches/"
                f"{quote(match_id, safe='')}/timeline"
            )
            payload = self._riot_json(url, api_key)
            result = payload if isinstance(payload, dict) else {}
            with self._cache_lock:
                self._timeline_cache[match_id] = result
            try:
                disk_path.write_text(
                    json.dumps(
                        result,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass
            return result
        finally:
            with self._cache_lock:
                inflight = self._timeline_inflight.pop(match_id, None)
                if inflight is not None:
                    inflight.set()

    @staticmethod
    def _participant(match: dict[str, Any], puuid: str) -> dict[str, Any] | None:
        info = match.get("info", {}) if isinstance(match, dict) else {}
        participants = info.get("participants", []) if isinstance(info, dict) else []
        for participant in participants if isinstance(participants, list) else []:
            if isinstance(participant, dict) and participant.get("puuid") == puuid:
                return participant
        return None


    @staticmethod
    def _analyse_samples(
        samples: list[dict[str, Any]],
        current_champion: str,
    ) -> dict[str, Any]:
        empty = {
            "sample_games": 0,
            "recent_wins": 0,
            "recent_win_rate": None,
            "avg_kills": 0.0,
            "avg_deaths": 0.0,
            "avg_assists": 0.0,
            "avg_kda": 0.0,
            "kda_volatility": 0.0,
            "death_volatility": 0.0,
            "avg_cs_min": 0.0,
            "avg_gold_min": 0.0,
            "avg_kp": 0.0,
            "avg_damage_min": 0.0,
            "avg_team_damage_share": 0.0,
            "avg_damage_taken_min": 0.0,
            "avg_vision_min": 0.0,
            "avg_control_wards": 0.0,
            "avg_turrets": 0.0,
            "avg_solo_kills": 0.0,
            "avg_objectives_stolen": 0.0,
            "avg_objective_participation": 0.0,
            "first_blood_rate": 0.0,
            "early_advantage_rate": 0.0,
            "high_death_game_rate": 0.0,
            "low_death_game_rate": 0.0,
            "champion_games": 0,
            "champion_wins": 0,
            "champion_win_rate": None,
            "champion_share": 0.0,
            "unique_champions": 0,
            "champion_counts": {},
            "champion_role_counts": {},
            "main_role": "",
            "secondary_role": "",
            "role_share": 0.0,
            "secondary_role_share": 0.0,
            "role_counts": {},
            "games_today": 0,
            "first_ranked_today": True,
            "last_ranked_minutes_ago": None,
            "streak_type": "",
            "streak_count": 0,
            "avg_game_minutes": 0.0,
            "late_game_games": 0,
            "late_game_win_rate": None,
            "short_game_games": 0,
            "short_game_win_rate": None,
            "session_games": 0,
            "session_span_minutes": 0,
            "days_since_last_ranked": None,
        }
        if not samples:
            return empty

        now_local = datetime.now().astimezone()
        today = now_local.date()
        wins = 0
        kills = deaths = assists = 0.0
        cs_min = gold_min = kp = damage_min = team_damage_share = 0.0
        damage_taken_min = vision_min = control_wards = turrets = 0.0
        solo_kills = objectives_stolen = objective_participation = 0.0
        first_bloods = early_advantage_games = 0
        high_death_games = low_death_games = 0
        champion_games = champion_wins = games_today = 0
        role_counts: dict[str, int] = {}
        champion_counts: dict[str, int] = {}
        champion_role_counts: dict[str, int] = {}
        result_order: list[bool] = []
        kda_values: list[float] = []
        death_values: list[float] = []
        duration_values: list[float] = []
        latest_end_ms: int | None = None
        late_games = late_wins = short_games = short_wins = 0

        for sample in samples:
            p = sample["participant"]
            info = sample.get("info", {})
            if not isinstance(info, dict):
                info = {}

            duration = float(info.get("gameDuration", 0) or 0)
            minutes = max(duration / 60.0, 1.0)
            duration_values.append(minutes)
            won = bool(p.get("win", False))
            result_order.append(won)
            wins += int(won)

            if minutes >= 30:
                late_games += 1
                late_wins += int(won)
            if minutes <= 25:
                short_games += 1
                short_wins += int(won)

            player_kills = float(p.get("kills", 0) or 0)
            player_deaths = float(p.get("deaths", 0) or 0)
            player_assists = float(p.get("assists", 0) or 0)
            kills += player_kills
            deaths += player_deaths
            assists += player_assists
            death_values.append(player_deaths)
            kda_values.append(
                (player_kills + player_assists) / max(player_deaths, 1.0)
            )
            high_death_games += int(player_deaths >= 8)
            low_death_games += int(player_deaths <= 3)

            cs = float(p.get("totalMinionsKilled", 0) or 0) + float(
                p.get("neutralMinionsKilled", 0) or 0
            )
            cs_min += cs / minutes
            gold_min += float(p.get("goldEarned", 0) or 0) / minutes

            team_kills = 0
            team_damage = 0.0
            participants = info.get("participants", [])
            for teammate in participants if isinstance(participants, list) else []:
                if (
                    isinstance(teammate, dict)
                    and teammate.get("teamId") == p.get("teamId")
                ):
                    team_kills += int(teammate.get("kills", 0) or 0)
                    team_damage += float(
                        teammate.get("totalDamageDealtToChampions", 0) or 0
                    )

            kp += (
                (player_kills + player_assists) / max(team_kills, 1)
            ) * 100.0
            player_damage = float(
                p.get("totalDamageDealtToChampions", 0) or 0
            )
            damage_min += player_damage / minutes
            if team_damage > 0:
                team_damage_share += (player_damage / team_damage) * 100.0

            damage_taken_min += float(
                p.get("totalDamageTaken", 0) or 0
            ) / minutes
            vision_min += float(p.get("visionScore", 0) or 0) / minutes
            control_wards += float(p.get("detectorWardsPlaced", 0) or 0)
            turrets += float(p.get("turretTakedowns", 0) or 0)
            objectives_stolen += float(p.get("objectivesStolen", 0) or 0)

            challenges = p.get("challenges", {})
            if isinstance(challenges, dict):
                solo_kills += float(challenges.get("soloKills", 0) or 0)
                early_advantage_games += int(
                    float(
                        challenges.get(
                            "earlyLaningPhaseGoldExpAdvantage",
                            0,
                        )
                        or 0
                    )
                    > 0
                )
                objective_participation += (
                    float(challenges.get("dragonTakedowns", 0) or 0)
                    + float(challenges.get("baronTakedowns", 0) or 0)
                    + float(challenges.get("riftHeraldTakedowns", 0) or 0)
                )

            first_bloods += int(
                bool(p.get("firstBloodKill", False))
                or bool(p.get("firstBloodAssist", False))
            )

            played_champion = str(p.get("championName", "") or "")
            champion_key = normalize_name(played_champion)
            if champion_key:
                champion_counts[champion_key] = (
                    champion_counts.get(champion_key, 0) + 1
                )

            role = str(
                p.get("teamPosition", "")
                or p.get("individualPosition", "")
                or ""
            ).upper()
            if role:
                role_counts[role] = role_counts.get(role, 0) + 1

            if normalize_name(played_champion) == normalize_name(current_champion):
                champion_games += 1
                champion_wins += int(won)
                if role:
                    champion_role_counts[role] = (
                        champion_role_counts.get(role, 0) + 1
                    )

            start_ms = int(info.get("gameStartTimestamp", 0) or 0)
            end_ms = int(info.get("gameEndTimestamp", 0) or 0)
            if not end_ms and start_ms:
                end_ms = start_ms + int(duration * 1000)
            if latest_end_ms is None and end_ms:
                latest_end_ms = end_ms
            if start_ms:
                game_local = datetime.fromtimestamp(
                    start_ms / 1000.0,
                    tz=timezone.utc,
                ).astimezone()
                games_today += int(game_local.date() == today)

        count = len(samples)
        ordered_roles = sorted(
            role_counts.items(),
            key=lambda item: item[1],
            reverse=True,
        )
        main_role = ordered_roles[0][0] if ordered_roles else ""
        secondary_role = ordered_roles[1][0] if len(ordered_roles) > 1 else ""
        role_share = role_counts.get(main_role, 0) / count if count else 0.0
        secondary_share = (
            role_counts.get(secondary_role, 0) / count if count else 0.0
        )

        streak_type = ""
        streak_count = 0
        if result_order:
            first_result = result_order[0]
            for result in result_order:
                if result != first_result:
                    break
                streak_count += 1
            streak_type = "win" if first_result else "loss"

        last_ranked_minutes_ago: int | None = None
        if latest_end_ms:
            last_end_local = datetime.fromtimestamp(
                latest_end_ms / 1000.0,
                tz=timezone.utc,
            ).astimezone()
            last_ranked_minutes_ago = max(
                0,
                int((now_local - last_end_local).total_seconds() // 60),
            )

        champion_wr = (
            round((champion_wins / champion_games) * 100.0, 1)
            if champion_games
            else None
        )

        return {
            "sample_games": count,
            "recent_wins": wins,
            "recent_win_rate": round((wins / count) * 100.0, 1),
            "avg_kills": round(kills / count, 1),
            "avg_deaths": round(deaths / count, 1),
            "avg_assists": round(assists / count, 1),
            "avg_kda": round(sum(kda_values) / count, 2),
            "kda_volatility": round(pstdev(kda_values), 2)
            if len(kda_values) > 1
            else 0.0,
            "death_volatility": round(pstdev(death_values), 2)
            if len(death_values) > 1
            else 0.0,
            "avg_cs_min": round(cs_min / count, 1),
            "avg_gold_min": round(gold_min / count, 0),
            "avg_kp": round(kp / count, 1),
            "avg_damage_min": round(damage_min / count, 0),
            "avg_team_damage_share": round(team_damage_share / count, 1),
            "avg_damage_taken_min": round(damage_taken_min / count, 0),
            "avg_vision_min": round(vision_min / count, 2),
            "avg_control_wards": round(control_wards / count, 1),
            "avg_turrets": round(turrets / count, 2),
            "avg_solo_kills": round(solo_kills / count, 2),
            "avg_objectives_stolen": round(objectives_stolen / count, 2),
            "avg_objective_participation": round(
                objective_participation / count,
                2,
            ),
            "first_blood_rate": round((first_bloods / count) * 100.0, 1),
            "early_advantage_rate": round(
                (early_advantage_games / count) * 100.0,
                1,
            ),
            "high_death_game_rate": round(
                (high_death_games / count) * 100.0,
                1,
            ),
            "low_death_game_rate": round(
                (low_death_games / count) * 100.0,
                1,
            ),
            "champion_games": champion_games,
            "champion_wins": champion_wins,
            "champion_win_rate": champion_wr,
            "champion_share": round(champion_games / count, 2),
            "unique_champions": len(champion_counts),
            "champion_counts": champion_counts,
            "champion_role_counts": champion_role_counts,
            "main_role": main_role,
            "secondary_role": secondary_role,
            "role_share": round(role_share, 2),
            "secondary_role_share": round(secondary_share, 2),
            "role_counts": role_counts,
            "games_today": games_today,
            "first_ranked_today": games_today == 0,
            "last_ranked_minutes_ago": last_ranked_minutes_ago,
            "streak_type": streak_type,
            "streak_count": streak_count,
            "avg_game_minutes": round(sum(duration_values) / count, 1),
            "late_game_games": late_games,
            "late_game_win_rate": round((late_wins / late_games) * 100.0, 1)
            if late_games
            else None,
            "short_game_games": short_games,
            "short_game_win_rate": round((short_wins / short_games) * 100.0, 1)
            if short_games
            else None,
            **derive_session_metrics(samples),
        }

    def _analyse_timelines(
        self,
        samples: list[dict[str, Any]],
        puuid: str,
        role: str,
        route: str,
        api_key: str,
    ) -> dict[str, Any]:
        result = {
            "timeline_games": 0,
            "lead_at_10_rate": 0.0,
            "behind_at_10_rate": 0.0,
            "early_death_rate": 0.0,
            "early_kill_participation_rate": 0.0,
            "early_roam_rate": 0.0,
            "early_objective_rate": 0.0,
            "comeback_rate": 0.0,
            "throw_rate": 0.0,
            "invader_kills": 0,
            "invader_deaths": 0,
            "invader_games": 0,
            "early_fights": 0,
            "avg_gold_diff_at_10": 0.0,
            "avg_xp_diff_at_10": 0.0,
            "avg_cs_diff_at_10": 0.0,
            "avg_lane_cs_at_10": 0.0,
            "avg_jungle_cs_at_6": 0.0,
            "solo_kill_rate": 0.0,
            "solo_death_rate": 0.0,
            "gank_before_5_rate": 0.0,
            "ward_before_10_rate": 0.0,
        }
        if not samples:
            return result

        lead10 = behind10 = early_death_games = early_kp_games = 0
        roam_games = objective_games = comeback_games = throw_games = 0
        invader_kills = invader_deaths = invader_games = early_fights = 0
        solo_kill_games = solo_death_games = gank_before_5_games = ward_before_10_games = 0
        gold_diff10_total = xp_diff10_total = cs_diff10_total = lane_cs10_total = 0.0
        jungle_cs6_total = 0.0
        lane_frame_games = jungle_frame_games = 0
        analysed = 0

        for sample in samples:
            p = sample.get("participant", {})
            info = sample.get("info", {})
            if not isinstance(p, dict) or not isinstance(info, dict):
                continue

            participant_id = int(p.get("participantId", 0) or 0)
            team_id = int(p.get("teamId", 0) or 0)
            match_id = str(sample.get("match_id", "") or "")
            if not participant_id or not team_id or not match_id:
                continue

            match_role = str(
                p.get("teamPosition", "")
                or p.get("individualPosition", "")
                or role
                or ""
            ).upper()
            opponent_id = self._lane_opponent_id(info, p, match_role)
            timeline = self._timeline(match_id, route, api_key)
            timeline_info = (
                timeline.get("info", {})
                if isinstance(timeline, dict)
                else {}
            )
            frames = (
                timeline_info.get("frames", [])
                if isinstance(timeline_info, dict)
                else []
            )
            if not isinstance(frames, list) or not frames:
                continue

            analysed += 1
            frame6 = frames[min(6, len(frames) - 1)]
            frame10 = frames[min(10, len(frames) - 1)]
            frame15 = frames[min(15, len(frames) - 1)]
            own6 = self._participant_frame(frame6, participant_id)
            own10 = self._participant_frame(frame10, participant_id)
            opp10 = self._participant_frame(frame10, opponent_id)
            own15 = self._participant_frame(frame15, participant_id)
            opp15 = self._participant_frame(frame15, opponent_id)

            if match_role == "JUNGLE" and own6:
                jungle_cs6_total += float(own6.get("jungleMinionsKilled", 0) or 0)
                jungle_frame_games += 1

            if own10 and opp10:
                gold_diff = float(own10.get("totalGold", 0) or 0) - float(
                    opp10.get("totalGold", 0) or 0
                )
                xp_diff = float(own10.get("xp", 0) or 0) - float(
                    opp10.get("xp", 0) or 0
                )
                own_cs = float(own10.get("minionsKilled", 0) or 0) + float(
                    own10.get("jungleMinionsKilled", 0) or 0
                )
                opp_cs = float(opp10.get("minionsKilled", 0) or 0) + float(
                    opp10.get("jungleMinionsKilled", 0) or 0
                )
                gold_diff10_total += gold_diff
                xp_diff10_total += xp_diff
                cs_diff10_total += own_cs - opp_cs
                lane_cs10_total += own_cs
                lane_frame_games += 1
                if gold_diff >= 300 or xp_diff >= 250:
                    lead10 += 1
                elif gold_diff <= -300 or xp_diff <= -250:
                    behind10 += 1

            if own15 and opp15:
                gold15 = float(own15.get("totalGold", 0) or 0) - float(
                    opp15.get("totalGold", 0) or 0
                )
                if bool(p.get("win", False)) and gold15 <= -600:
                    comeback_games += 1
                elif not bool(p.get("win", False)) and gold15 >= 600:
                    throw_games += 1

            game_early_death = False
            game_early_kp = False
            game_roam = False
            game_objective = False
            game_invade = False
            game_solo_kill = False
            game_solo_death = False
            game_gank_before_5 = False
            game_ward_before_10 = False

            for frame in frames:
                events = frame.get("events", []) if isinstance(frame, dict) else []
                for event in events if isinstance(events, list) else []:
                    if not isinstance(event, dict):
                        continue
                    timestamp = int(event.get("timestamp", 0) or 0)

                    if event.get("type") == "CHAMPION_KILL" and timestamp <= 10 * 60 * 1000:
                        killer = int(event.get("killerId", 0) or 0)
                        victim = int(event.get("victimId", 0) or 0)
                        assists = {
                            int(item)
                            for item in event.get("assistingParticipantIds", [])
                            if isinstance(item, int)
                        }
                        involved = participant_id in {killer, victim} or participant_id in assists
                        if not involved:
                            continue

                        early_fights += 1
                        if victim == participant_id:
                            game_early_death = True
                            if not assists:
                                game_solo_death = True
                        if killer == participant_id or participant_id in assists:
                            game_early_kp = True
                            if killer == participant_id and not assists:
                                game_solo_kill = True
                            if match_role == "JUNGLE" and timestamp <= 5 * 60 * 1000:
                                game_gank_before_5 = True

                        position = event.get("position", {})
                        x = (
                            float(position.get("x", 0) or 0)
                            if isinstance(position, dict)
                            else 0.0
                        )
                        y = (
                            float(position.get("y", 0) or 0)
                            if isinstance(position, dict)
                            else 0.0
                        )

                        if (
                            match_role not in {"JUNGLE", ""}
                            and (
                                killer == participant_id
                                or participant_id in assists
                            )
                        ):
                            if self._is_roam_position(match_role, x, y):
                                game_roam = True

                        enemy_half = (
                            (team_id == 100 and x + y > 15000)
                            or (team_id == 200 and x + y < 15000)
                        )
                        if match_role == "JUNGLE" and enemy_half:
                            game_invade = True
                            if killer == participant_id:
                                invader_kills += 1
                            elif victim == participant_id:
                                invader_deaths += 1

                    if (
                        event.get("type") in {"WARD_PLACED", "WARD_KILL"}
                        and timestamp <= 10 * 60 * 1000
                    ):
                        ward_actor = int(
                            event.get("creatorId", 0)
                            or event.get("killerId", 0)
                            or 0
                        )
                        if ward_actor == participant_id:
                            game_ward_before_10 = True

                    if (
                        event.get("type") == "ELITE_MONSTER_KILL"
                        and timestamp <= 15 * 60 * 1000
                    ):
                        killer = int(event.get("killerId", 0) or 0)
                        assists = {
                            int(item)
                            for item in event.get("assistingParticipantIds", [])
                            if isinstance(item, int)
                        }
                        if killer == participant_id or participant_id in assists:
                            game_objective = True

            early_death_games += int(game_early_death)
            early_kp_games += int(game_early_kp)
            roam_games += int(game_roam)
            objective_games += int(game_objective)
            invader_games += int(game_invade)
            solo_kill_games += int(game_solo_kill)
            solo_death_games += int(game_solo_death)
            gank_before_5_games += int(game_gank_before_5)
            ward_before_10_games += int(game_ward_before_10)

        if not analysed:
            return result

        return {
            "timeline_games": analysed,
            "lead_at_10_rate": round((lead10 / analysed) * 100.0, 1),
            "behind_at_10_rate": round((behind10 / analysed) * 100.0, 1),
            "early_death_rate": round(
                (early_death_games / analysed) * 100.0,
                1,
            ),
            "early_kill_participation_rate": round(
                (early_kp_games / analysed) * 100.0,
                1,
            ),
            "early_roam_rate": round((roam_games / analysed) * 100.0, 1),
            "early_objective_rate": round(
                (objective_games / analysed) * 100.0,
                1,
            ),
            "comeback_rate": round((comeback_games / analysed) * 100.0, 1),
            "throw_rate": round((throw_games / analysed) * 100.0, 1),
            "invader_kills": invader_kills,
            "invader_deaths": invader_deaths,
            "invader_games": invader_games,
            "early_fights": early_fights,
            "avg_gold_diff_at_10": round(gold_diff10_total / lane_frame_games, 1)
            if lane_frame_games
            else 0.0,
            "avg_xp_diff_at_10": round(xp_diff10_total / lane_frame_games, 1)
            if lane_frame_games
            else 0.0,
            "avg_cs_diff_at_10": round(cs_diff10_total / lane_frame_games, 1)
            if lane_frame_games
            else 0.0,
            "avg_lane_cs_at_10": round(lane_cs10_total / lane_frame_games, 1)
            if lane_frame_games
            else 0.0,
            "avg_jungle_cs_at_6": round(jungle_cs6_total / jungle_frame_games, 1)
            if jungle_frame_games
            else 0.0,
            "solo_kill_rate": round((solo_kill_games / analysed) * 100.0, 1),
            "solo_death_rate": round((solo_death_games / analysed) * 100.0, 1),
            "gank_before_5_rate": round((gank_before_5_games / analysed) * 100.0, 1),
            "ward_before_10_rate": round((ward_before_10_games / analysed) * 100.0, 1),
        }

    @staticmethod
    def _participant_frame(
        frame: dict[str, Any],
        participant_id: int,
    ) -> dict[str, Any]:
        if not participant_id or not isinstance(frame, dict):
            return {}
        participant_frames = frame.get("participantFrames", {})
        if not isinstance(participant_frames, dict):
            return {}
        payload = participant_frames.get(str(participant_id))
        if payload is None:
            payload = participant_frames.get(participant_id)
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _lane_opponent_id(
        info: dict[str, Any],
        participant: dict[str, Any],
        role: str,
    ) -> int:
        team_id = int(participant.get("teamId", 0) or 0)
        candidates = info.get("participants", [])
        for candidate in candidates if isinstance(candidates, list) else []:
            if not isinstance(candidate, dict):
                continue
            if int(candidate.get("teamId", 0) or 0) == team_id:
                continue
            candidate_role = str(
                candidate.get("teamPosition", "")
                or candidate.get("individualPosition", "")
                or ""
            ).upper()
            if candidate_role == role:
                return int(candidate.get("participantId", 0) or 0)
        return 0

    @staticmethod
    def _is_roam_position(role: str, x: float, y: float) -> bool:
        role = str(role or "").upper()
        if not x or not y:
            return False
        if role == "MIDDLE":
            return abs(x - y) > 2600
        if role == "TOP":
            # Top lane is mostly the upper/left edge of Summoner's Rift.
            return not (x < 8500 and y > 6500)
        if role in {"BOTTOM", "UTILITY"}:
            # Bottom lane is mostly the lower/right edge.
            return not (x > 6500 and y < 8500)
        return False

    @staticmethod
    def _role_status(current_role: str, analysis: dict[str, Any]) -> dict[str, Any]:
        sample_games = int(analysis.get("sample_games", 0) or 0)
        current_role = str(current_role or "").upper()
        main_role = str(analysis.get("main_role", "") or "").upper()
        role_counts = dict(analysis.get("role_counts", {}) or {})
        current_share = (
            role_counts.get(current_role, 0) / sample_games
            if current_role and sample_games
            else 0.0
        )

        if sample_games < 5 or not current_role or not main_role:
            state = "unclear"
            label = "ROLE UNCLEAR"
            tone = "neutral"
        elif current_role == main_role:
            state = "main"
            label = "MAIN ROLE"
            tone = "positive"
        else:
            # The user-facing rule is intentionally simple: whenever the
            # assigned current role differs from the recent main role, say
            # OFFROLE.  The tooltip still explains sample share and evidence.
            state = "off_role"
            label = "OFFROLE"
            tone = "negative" if current_share <= 0.25 else "warning"

        return {
            "role_state": state,
            "role_status_label": label,
            "role_status_tone": tone,
            "current_role_share": round(current_share, 2),
        }


    def _assign_team_roles(
        self,
        roster: dict[str, Any],
        profiles: dict[str, dict[str, Any]],
    ) -> None:
        roles = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")

        for team_key in ("allies", "enemies"):
            players = [
                player
                for player in roster.get(team_key, ())
                if str(player.get("player_key", "")) in profiles
                and profiles[str(player.get("player_key", ""))].get("state")
                in {"fast", "ready"}
            ]
            if not players:
                continue

            # A full team gets one of every role. Partial practice-tool rosters
            # simply receive the best unique subset.
            role_pool = roles[: len(players)] if len(players) < 5 else roles
            best_score = -math.inf
            best_assignment: tuple[str, ...] | None = None

            for assignment in permutations(roles, len(players)):
                if len(set(assignment)) != len(assignment):
                    continue
                score = 0.0
                for player, role in zip(players, assignment):
                    profile = profiles[str(player.get("player_key", ""))]
                    score += self._role_assignment_score(player, profile, role)
                if score > best_score:
                    best_score = score
                    best_assignment = assignment

            if best_assignment is None:
                continue

            for player, assigned_role in zip(players, best_assignment):
                player_key = str(player.get("player_key", ""))
                profile = profiles[player_key]
                profile["assigned_role"] = assigned_role
                profile["inferred_role"] = assigned_role
                profile["role_name"] = _ROLE_NAMES.get(
                    assigned_role,
                    "Unknown role",
                )

                role_scores = sorted(
                    (
                        self._role_assignment_score(player, profile, candidate),
                        candidate,
                    )
                    for candidate in roles
                )
                assigned_score = self._role_assignment_score(
                    player,
                    profile,
                    assigned_role,
                )
                alternative_scores = [
                    score
                    for score, candidate in role_scores
                    if candidate != assigned_role
                ]
                margin = assigned_score - max(alternative_scores, default=assigned_score)
                current_role = str(player.get("role", "") or "").upper()
                has_smite = any(
                    "smite" in str(value).casefold()
                    for value in player.get("spells", ())
                )
                if current_role == assigned_role or (
                    assigned_role == "JUNGLE" and has_smite
                ) or margin >= 25:
                    assignment_confidence = "high"
                elif margin >= 8:
                    assignment_confidence = "medium"
                else:
                    assignment_confidence = "low"
                profile["role_assignment_confidence"] = assignment_confidence
                profile["role_assignment_margin"] = round(margin, 1)

                role_status = self._role_status(assigned_role, profile)
                if assignment_confidence == "low" and not current_role:
                    role_status = {
                        "role_state": "unclear",
                        "role_status_label": "ROLE UNCLEAR",
                        "role_status_tone": "neutral",
                        "current_role_share": float(
                            profile.get("current_role_share", 0) or 0
                        ),
                    }
                profile.update(role_status)

                local_percentiles = self._baseline_store.percentiles(
                    assigned_role,
                    profile,
                )
                profile["local_percentiles"] = local_percentiles
                tags = self._build_tags(
                    profile,
                    profile,
                    assigned_role,
                    profile,
                    profile,
                    local_percentiles,
                )
                if role_status["role_state"] not in {"unclear", "main"}:
                    tags.append(
                        self._role_tag(assigned_role, profile, role_status)
                    )
                profile["tags"] = prioritize_tags(tags, limit=8)

    @staticmethod
    def _role_assignment_score(
        player: dict[str, Any],
        profile: dict[str, Any],
        role: str,
    ) -> float:
        current_role = str(player.get("role", "") or "").upper()
        spells = [
            str(value).casefold()
            for value in player.get("spells", ())
        ]
        has_smite = any("smite" in value for value in spells)

        role_counts = dict(profile.get("role_counts", {}) or {})
        sample = max(int(profile.get("sample_games", 0) or 0), 1)
        champion_role_counts = dict(
            profile.get("champion_role_counts", {}) or {}
        )
        champion_games = max(
            int(profile.get("champion_games", 0) or 0),
            1,
        )

        score = (float(role_counts.get(role, 0)) / sample) * 70.0
        score += (
            float(champion_role_counts.get(role, 0)) / champion_games
        ) * 35.0

        if current_role:
            score += 140.0 if current_role == role else -35.0

        if role == str(profile.get("main_role", "") or ""):
            score += 35.0
        if role == str(profile.get("secondary_role", "") or ""):
            score += 18.0

        if has_smite:
            score += 180.0 if role == "JUNGLE" else -80.0
        elif role == "JUNGLE":
            score -= 20.0

        # Support is the most common missing/ambiguous Live Client position.
        if (
            role == "UTILITY"
            and float(profile.get("avg_cs_min", 0) or 0) <= 2.5
        ):
            score += 18.0
        if (
            role == "BOTTOM"
            and float(profile.get("avg_cs_min", 0) or 0) >= 6.5
        ):
            score += 10.0

        return score

    def _apply_lane_matchups(
        self,
        roster: dict[str, Any],
        profiles: dict[str, dict[str, Any]],
    ) -> None:
        matchups = pair_lane_opponents(roster, profiles)
        for player_key, comparison in matchups.items():
            profile = profiles.get(player_key)
            if not isinstance(profile, dict):
                continue
            profile["lane_opponent"] = comparison
            tag = matchup_tag(
                comparison,
                int(profile.get("sample_games", 0) or 0),
            )
            tags = list(profile.get("tags", ()))
            if tag is not None:
                tags.append(tag)
            profile["tags"] = prioritize_tags(tags, limit=8)

    @staticmethod
    def _percentile_tags(
        percentiles: dict[str, Any],
    ) -> list[dict[str, str]]:
        candidates = [
            (
                "avg_team_damage_share",
                "TOP LOCAL DAMAGE",
                "positive",
                "team-damage share",
            ),
            (
                "avg_cs_min",
                "TOP LOCAL FARM",
                "positive",
                "CS per minute",
            ),
            (
                "avg_vision_min",
                "TOP LOCAL VISION",
                "positive",
                "vision score per minute",
            ),
            (
                "avg_kp",
                "TOP LOCAL IMPACT",
                "positive",
                "kill participation",
            ),
        ]
        tags: list[dict[str, str]] = []
        for metric, label, tone, description in candidates:
            percentile = percentiles.get(metric)
            sample = int(percentiles.get(f"{metric}_sample", 0) or 0)
            if percentile is None or float(percentile) < 90 or sample < 30:
                continue
            tags.append(
                {
                    "text": label,
                    "tone": tone,
                    "tooltip": (
                        f"Top {100 - float(percentile):.0f}% for "
                        f"{description} among {sample} same-role profiles "
                        "previously processed by this local app"
                    ),
                }
            )
        return tags[:1]

    def _apply_encounter_history(
        self,
        roster: dict[str, Any],
        profiles: dict[str, dict[str, Any]],
    ) -> None:
        players = list(roster.get("players", ()))
        active_player = next(
            (player for player in players if bool(player.get("is_active"))),
            None,
        )
        if active_player is None:
            return

        active_key = str(active_player.get("player_key", ""))
        active_profile = profiles.get(active_key, {})
        active_puuid = str(active_profile.get("puuid", "") or "")
        active_match_ids = set(
            str(item)
            for item in active_profile.get("recent_match_ids", ())
        )
        platform = str(
            getattr(self.config, "riot_platform", "euw1") or "euw1"
        ).casefold()
        route = _PLATFORM_TO_ROUTE.get(platform, "europe")
        api_key = str(getattr(self.config, "riot_api_key", "") or "").strip()

        for player in players:
            player_key = str(player.get("player_key", ""))
            if not player_key or player_key == active_key:
                continue
            profile = profiles.get(player_key)
            if not isinstance(profile, dict):
                continue

            encounter_key = self._encounter_key(player, profile)
            local = self._encounter_store.lookup(encounter_key)
            current_signature = self._stable_roster_signature(roster)
            current_started_at = float(roster.get("game_started_at", 0) or 0)
            if current_started_at <= 0:
                # Spectator can briefly omit gameStartTime.  Suppress only very
                # recent local records during that window to avoid V18 current-
                # game entries appearing as previous encounters.
                current_started_at = time.time() - 300.0
            previous_local_entries = filter_previous_encounters(
                list(local.get("entries", ())),
                current_game_started_at=current_started_at,
                current_game_signature=current_signature,
            )
            local_ally = sum(
                1 for entry in previous_local_entries
                if str(entry.get("relation", "") or "") == "ally"
            )
            local_enemy = sum(
                1 for entry in previous_local_entries
                if str(entry.get("relation", "") or "") == "enemy"
            )
            local_total = local_ally + local_enemy
            encounter_history: list[dict[str, Any]] = []

            for entry in previous_local_entries[:8]:
                encounter_history.append(
                    {
                        "source": "tracked",
                        "relation": str(entry.get("relation", "") or ""),
                        "my_champion": str(entry.get("my_champion", "") or "Unknown"),
                        "their_champion": str(entry.get("champion", "") or "Unknown"),
                        "timestamp": float(entry.get("timestamp", 0) or 0),
                        "won": entry.get("won") if isinstance(entry.get("won"), bool) else None,
                        "result": str(entry.get("result", "") or ""),
                        "my_kda": str(entry.get("my_kda", "") or ""),
                        "their_kda": str(entry.get("their_kda", "") or ""),
                        "queue_id": int(entry.get("queue_id", 0) or 0),
                        "match_id": str(entry.get("match_id", "") or ""),
                    }
                )

            shared_ids = [
                match_id
                for match_id in profile.get("recent_match_ids", ())
                if str(match_id) in active_match_ids
            ]
            api_ally = api_enemy = 0
            last_api_seen = 0

            if active_puuid and api_key:
                for match_id in shared_ids[:8]:
                    try:
                        match = self._match(str(match_id), route, api_key)
                    except Exception:
                        continue
                    active_participant = self._participant(
                        match,
                        active_puuid,
                    )
                    other_participant = self._participant(
                        match,
                        str(profile.get("puuid", "") or ""),
                    )
                    if not active_participant or not other_participant:
                        continue
                    same_team = (
                        active_participant.get("teamId")
                        == other_participant.get("teamId")
                    )
                    if same_team:
                        api_ally += 1
                        relation = "ally"
                    else:
                        api_enemy += 1
                        relation = "enemy"

                    info = match.get("info", {}) if isinstance(match, dict) else {}
                    game_timestamp_ms = int(
                        info.get("gameEndTimestamp", 0)
                        or info.get("gameStartTimestamp", 0)
                        or info.get("gameCreation", 0)
                        or 0
                    )
                    last_api_seen = max(last_api_seen, game_timestamp_ms)

                    encounter_history.append(
                        {
                            "source": "ranked",
                            "relation": relation,
                            "my_champion": str(
                                active_participant.get("championName", "")
                                or "Unknown"
                            ),
                            "their_champion": str(
                                other_participant.get("championName", "")
                                or "Unknown"
                            ),
                            "timestamp": (
                                game_timestamp_ms / 1000.0
                                if game_timestamp_ms
                                else 0.0
                            ),
                            "match_id": str(match_id),
                            "won": bool(active_participant.get("win", False)),
                            "result": (
                                "Victory"
                                if bool(active_participant.get("win", False))
                                else "Defeat"
                            ),
                            "my_kda": (
                                f"{int(active_participant.get('kills', 0) or 0)}/"
                                f"{int(active_participant.get('deaths', 0) or 0)}/"
                                f"{int(active_participant.get('assists', 0) or 0)}"
                            ),
                            "their_kda": (
                                f"{int(other_participant.get('kills', 0) or 0)}/"
                                f"{int(other_participant.get('deaths', 0) or 0)}/"
                                f"{int(other_participant.get('assists', 0) or 0)}"
                            ),
                            "queue_id": int(info.get("queueId", 0) or 0),
                        }
                    )

            api_total = api_ally + api_enemy
            previous_count = max(local_total, api_total)
            if previous_count <= 0:
                continue

            profile["encounter_count"] = previous_count
            profile["encounter_local_ally_count"] = local_ally
            profile["encounter_local_enemy_count"] = local_enemy
            profile["encounter_ranked_ally_count"] = api_ally
            profile["encounter_ranked_enemy_count"] = api_enemy
            profile["encounter_last_seen"] = max(
                max(
                    (float(entry.get("timestamp", 0) or 0) for entry in previous_local_entries),
                    default=0.0,
                ),
                last_api_seen / 1000.0 if last_api_seen else 0.0,
            )

            # Newest first. Keep enough entries for a useful tooltip without
            # turning the card tooltip into a full match-history browser.
            encounter_history.sort(
                key=lambda item: float(item.get("timestamp", 0) or 0),
                reverse=True,
            )
            deduped_history: list[dict[str, Any]] = []
            seen_match_ids: set[str] = set()
            for item in encounter_history:
                match_id = str(item.get("match_id", "") or "")
                if match_id and match_id in seen_match_ids:
                    continue
                relation = str(item.get("relation", "") or "")
                my_champion = str(item.get("my_champion", "") or "")
                their_champion = str(item.get("their_champion", "") or "")
                timestamp = float(item.get("timestamp", 0) or 0)
                duplicate = any(
                    str(existing.get("relation", "") or "") == relation
                    and str(existing.get("my_champion", "") or "") == my_champion
                    and str(existing.get("their_champion", "") or "") == their_champion
                    and timestamp
                    and float(existing.get("timestamp", 0) or 0)
                    and abs(float(existing.get("timestamp", 0) or 0) - timestamp)
                    <= 3 * 60 * 60
                    for existing in deduped_history
                )
                if duplicate:
                    continue
                if match_id:
                    seen_match_ids.add(match_id)
                deduped_history.append(item)
            profile["encounter_history"] = deduped_history[:8]
            encounter_summary = summarize_encounters(deduped_history)
            profile.update(encounter_summary)
            previous_count = max(
                previous_count,
                int(encounter_summary.get("encounter_history_count", 0) or 0),
            )
            profile["encounter_count"] = previous_count

            total_ally = max(local_ally, api_ally)
            total_enemy = max(local_enemy, api_enemy)
            if previous_count >= 2 and total_ally and total_enemy:
                label = f"SEEN {previous_count}X+"
            elif previous_count >= 3:
                label = f"SEEN {previous_count}X+"
            elif total_ally:
                label = "ALLY BEFORE"
            elif total_enemy:
                label = "ENEMY BEFORE"
            else:
                label = "PLAYED BEFORE"

            details = []
            if local_total:
                details.append(
                    "League Highlights recorded "
                    f"{local_ally} ally and {local_enemy} enemy game(s)"
                )
            if api_total:
                details.append(
                    "Recent Solo/Duo history found "
                    f"{api_ally} ally and {api_enemy} enemy game(s)"
                )
            record_wins = int(profile.get("encounter_wins", 0) or 0)
            record_losses = int(profile.get("encounter_losses", 0) or 0)
            if record_wins + record_losses:
                details.append(
                    f"Your recent record in these meetings: {record_wins}W-{record_losses}L"
                )
            last_seen = float(profile.get("encounter_last_seen", 0) or 0)
            if last_seen:
                when = datetime.fromtimestamp(last_seen).astimezone()
                details.append(
                    "Last seen "
                    + when.strftime("%Y-%m-%d %H:%M")
                )

            history_lines: list[str] = []
            for item in profile.get("encounter_history", ())[:5]:
                relation_text = (
                    "Ally"
                    if str(item.get("relation", "")) == "ally"
                    else "Enemy"
                )
                my_champion = str(item.get("my_champion", "") or "Unknown")
                their_champion = str(
                    item.get("their_champion", "") or "Unknown"
                )
                timestamp = float(item.get("timestamp", 0) or 0)
                when_text = ""
                if timestamp:
                    when_text = (
                        " · "
                        + datetime.fromtimestamp(timestamp)
                        .astimezone()
                        .strftime("%Y-%m-%d")
                    )
                result_text = str(item.get("result", "") or "")
                my_kda = str(item.get("my_kda", "") or "")
                their_kda = str(item.get("their_kda", "") or "")
                record_text = f" · {result_text}" if result_text else ""
                if my_kda or their_kda:
                    record_text += (
                        f" · KDA {my_kda or '—'} vs {their_kda or '—'}"
                    )
                history_lines.append(
                    f"{relation_text}: you played {my_champion}; "
                    f"they played {their_champion}{record_text}{when_text}"
                )

            tooltip_parts = list(details)
            if history_lines:
                tooltip_parts.append(
                    "Recent encounters:\n" + "\n".join(history_lines)
                )

            encounter_tag = make_evidence_tag(
                label,
                "neutral",
                "\n".join(tooltip_parts),
                priority=100,
                group="encounter",
                category="encounter",
                evidence_games=previous_count,
                exact_evidence=True,
            )
            tags = [
                tag
                for tag in list(profile.get("tags", ()))
                if not (
                    isinstance(tag, dict)
                    and str(tag.get("text", ""))
                    in {
                        "ALLY BEFORE",
                        "ENEMY BEFORE",
                        "PLAYED BEFORE",
                    }
                    or (
                        isinstance(tag, dict)
                        and str(tag.get("text", "")).startswith("SEEN ")
                    )
                )
            ]
            tags.insert(0, encounter_tag)
            profile["tags"] = self._dedupe_tags(tags)[:8]

    def _stage_live_encounters(
        self,
        roster: dict[str, Any],
        profiles: dict[str, dict[str, Any]],
        signature: str,
    ) -> None:
        players = list(roster.get("players", ()))
        active_player = next(
            (player for player in players if bool(player.get("is_active"))),
            None,
        )
        if active_player is None or not signature:
            return

        active_key = str(active_player.get("player_key", ""))
        active_team = str(active_player.get("team", "") or "")
        active_champion = str(active_player.get("champion", "") or "")
        records: list[dict[str, Any]] = []
        for player in players:
            player_key = str(player.get("player_key", ""))
            if player_key == active_key:
                continue
            profile = profiles.get(player_key, {})
            relation = (
                "ally"
                if str(player.get("team", "") or "") == active_team
                else "enemy"
            )
            records.append(
                {
                    "key": self._encounter_key(player, profile),
                    "riot_id": str(player.get("riot_id", "") or ""),
                    "relation": relation,
                    "champion": str(player.get("champion", "") or ""),
                    "my_champion": active_champion,
                    "game_signature": signature,
                }
            )

        now = time.time()
        game_started_at = float(roster.get("game_started_at", 0) or 0)
        existing = self._pending_encounter_game
        if existing and str(existing.get("signature", "")) == signature:
            existing["last_seen"] = now
            existing["records"] = records
            return
        if existing:
            self._flush_pending_encounters()
        self._pending_encounter_game = {
            "signature": signature,
            "first_seen": game_started_at if game_started_at > 0 else now,
            "last_seen": now,
            "records": records,
        }

    def _flush_pending_encounters(self) -> bool:
        pending = self._pending_encounter_game
        self._pending_encounter_game = None
        if not pending:
            return False
        first_seen = float(pending.get("first_seen", 0) or 0)
        # Avoid recording a cancelled loading screen or practice-tool blip.
        if time.time() - first_seen < 60.0:
            return False
        signature = str(pending.get("signature", "") or "")
        records = list(pending.get("records", ()))
        return self._encounter_store.record_game(signature, records)

    # Backwards-compatible private name used by older tests/extensions.  It now
    # stages instead of persisting the currently running game.
    def _record_live_encounters(
        self,
        roster: dict[str, Any],
        profiles: dict[str, dict[str, Any]],
    ) -> None:
        self._stage_live_encounters(
            roster,
            profiles,
            self._stable_roster_signature(roster),
        )

    @staticmethod
    def _encounter_key(
        player: dict[str, Any],
        profile: dict[str, Any],
    ) -> str:
        puuid = str(profile.get("puuid", "") or "")
        if puuid:
            return "puuid:" + puuid
        riot_id = str(
            player.get("riot_id", "")
            or player.get("player_key", "")
            or ""
        )
        return "riot:" + riot_id.casefold()

    def _record_local_baselines(
        self,
        profiles: dict[str, dict[str, Any]],
    ) -> None:
        for profile in profiles.values():
            if profile.get("state") != "ready":
                continue
            match_ids = list(profile.get("recent_match_ids", ()))
            unique_key = (
                str(profile.get("puuid", ""))
                + ":"
                + (str(match_ids[0]) if match_ids else "none")
            )
            role = str(
                profile.get("assigned_role", "")
                or profile.get("inferred_role", "")
                or ""
            )
            self._baseline_store.record(unique_key, role, profile)

    def _apply_premade_groups(
        self,
        roster: dict[str, Any],
        profiles: dict[str, dict[str, Any]],
    ) -> None:
        platform = str(
            getattr(self.config, "riot_platform", "euw1") or "euw1"
        ).casefold()
        route = _PLATFORM_TO_ROUTE.get(platform, "europe")
        api_key = str(getattr(self.config, "riot_api_key", "") or "").strip()

        for team_key in ("allies", "enemies"):
            players = list(roster.get(team_key, ()))
            player_by_key = {
                str(player.get("player_key", "")): player
                for player in players
                if str(player.get("player_key", ""))
            }
            keys = [key for key in player_by_key if key in profiles]
            adjacency: dict[str, set[str]] = {key: set() for key in keys}
            pair_details: dict[frozenset[str], dict[str, Any]] = {}

            for index, left_key in enumerate(keys):
                left_profile = profiles[left_key]
                left_matches = [
                    str(item)
                    for item in left_profile.get("recent_match_ids", ())
                    if str(item)
                ]
                left_puuid = str(left_profile.get("puuid", "") or "")
                if not left_matches or not left_puuid:
                    continue

                for right_key in keys[index + 1 :]:
                    right_profile = profiles[right_key]
                    right_matches = {
                        str(item)
                        for item in right_profile.get("recent_match_ids", ())
                        if str(item)
                    }
                    right_puuid = str(right_profile.get("puuid", "") or "")
                    if not right_puuid:
                        continue

                    shared = [match_id for match_id in left_matches if match_id in right_matches]
                    records: list[dict[str, Any]] = []
                    for match_id in shared[:12]:
                        try:
                            match = self._match(match_id, route, api_key)
                        except Exception:
                            continue
                        left_participant = self._participant(match, left_puuid)
                        right_participant = self._participant(match, right_puuid)
                        if not left_participant or not right_participant:
                            continue
                        if left_participant.get("teamId") != right_participant.get("teamId"):
                            continue
                        info = match.get("info", {}) if isinstance(match, dict) else {}
                        timestamp_ms = int(
                            info.get("gameEndTimestamp", 0)
                            or info.get("gameStartTimestamp", 0)
                            or info.get("gameCreation", 0)
                            or 0
                        )
                        records.append(
                            {
                                "match_id": match_id,
                                "timestamp": timestamp_ms / 1000.0 if timestamp_ms else 0.0,
                                "won": bool(left_participant.get("win", False)),
                            }
                        )

                    together = len(records)
                    if together < 2:
                        continue
                    verified_ids = {str(item.get("match_id", "")) for item in records}
                    consecutive = 0
                    for match_id in left_matches:
                        if match_id in verified_ids:
                            consecutive += 1
                        else:
                            break

                    timestamps = sorted(
                        float(item.get("timestamp", 0) or 0)
                        for item in records
                        if float(item.get("timestamp", 0) or 0) > 0
                    )
                    sessions = 0
                    previous_timestamp = 0.0
                    for timestamp in timestamps:
                        if not previous_timestamp or timestamp - previous_timestamp > 3 * 60 * 60:
                            sessions += 1
                        previous_timestamp = timestamp
                    sessions = max(sessions, 1 if records else 0)
                    wins = sum(int(bool(item.get("won", False))) for item in records)
                    confidence = premade_pair_confidence(together, consecutive, sessions)
                    if confidence.label == "Weak":
                        continue

                    left_role = str(
                        left_profile.get("assigned_role", "")
                        or left_profile.get("inferred_role", "")
                        or ""
                    )
                    right_role = str(
                        right_profile.get("assigned_role", "")
                        or right_profile.get("inferred_role", "")
                        or ""
                    )
                    label = premade_role_label(left_role, right_role, 2)
                    details = {
                        "games": together,
                        "wins": wins,
                        "win_rate": round((wins / together) * 100.0, 1),
                        "consecutive": consecutive,
                        "sessions": sessions,
                        "confidence": confidence.label,
                        "confidence_score": confidence.score,
                        "label": label,
                        "roles": (left_role, right_role),
                    }
                    adjacency[left_key].add(right_key)
                    adjacency[right_key].add(left_key)
                    pair_details[frozenset({left_key, right_key})] = details

            visited: set[str] = set()
            for start_key in keys:
                if start_key in visited or not adjacency[start_key]:
                    continue
                stack = [start_key]
                group: list[str] = []
                while stack:
                    current = stack.pop()
                    if current in visited:
                        continue
                    visited.add(current)
                    group.append(current)
                    stack.extend(adjacency[current] - visited)
                if len(group) < 2:
                    continue

                group_set = set(group)
                group_pairs = [
                    {"members": tuple(pair), **details}
                    for pair, details in pair_details.items()
                    if pair.issubset(group_set)
                ]
                if not group_pairs:
                    continue
                strongest = max(
                    group_pairs,
                    key=lambda item: (
                        int(item.get("confidence_score", 0) or 0),
                        int(item.get("games", 0) or 0),
                    ),
                )
                group_confidence_score = int(round(
                    sum(int(item.get("confidence_score", 0) or 0) for item in group_pairs)
                    / len(group_pairs)
                ))
                group_confidence = (
                    "High" if group_confidence_score >= 75 else "Medium"
                )
                together_games = int(strongest.get("games", 0) or 0)
                together_wins = int(strongest.get("wins", 0) or 0)
                win_rate = strongest.get("win_rate")
                sessions = max(int(item.get("sessions", 0) or 0) for item in group_pairs)
                consecutive = max(int(item.get("consecutive", 0) or 0) for item in group_pairs)

                for player_key in group:
                    profile = profiles[player_key]
                    members = [
                        str(player_by_key.get(member, {}).get("riot_id", "") or member)
                        for member in group
                        if member != player_key
                    ]
                    own_pairs = [
                        item for item in group_pairs
                        if player_key in set(item.get("members", ()))
                    ]
                    if len(group) == 2 and own_pairs:
                        label = str(own_pairs[0].get("label", "PREMADE DUO") or "PREMADE DUO")
                    else:
                        label = premade_role_label("", "", len(group))

                    profile["premade_size"] = len(group)
                    profile["premade_members"] = members
                    profile["premade_games_together"] = together_games
                    profile["premade_win_rate"] = win_rate
                    profile["premade_confidence"] = group_confidence
                    profile["premade_confidence_score"] = group_confidence_score
                    profile["premade_sessions"] = sessions
                    profile["premade_consecutive_games"] = consecutive
                    profile["premade_role_pair"] = label
                    profile["premade_pair_details"] = own_pairs
                    evidence_scope = "strongest_pair" if len(group) > 2 else "pair"
                    profile["premade_evidence_scope"] = evidence_scope

                    game_evidence_text = (
                        f"{together_games} verified recent game(s) on the strongest pair"
                        if evidence_scope == "strongest_pair"
                        else f"{together_games} verified recent game(s) together"
                    )
                    detail_parts = [
                        f"Repeated same-team history with {', '.join(members)}",
                        game_evidence_text,
                        f"{sessions} separate session(s)",
                    ]
                    if consecutive >= 2:
                        detail_parts.append(f"{consecutive} consecutive shared games")
                    if win_rate is not None:
                        detail_parts.append(f"{float(win_rate):.0f}% WR together")
                    detail_parts.append(
                        f"Premade confidence: {group_confidence} ({group_confidence_score}/100)"
                    )
                    premade_tag = make_evidence_tag(
                        label,
                        "warning",
                        " · ".join(detail_parts),
                        priority=92,
                        group="premade",
                        category="premade",
                        evidence_games=together_games,
                        exact_evidence=True,
                    )
                    tags = [
                        tag
                        for tag in list(profile.get("tags", ()))
                        if not (
                            isinstance(tag, dict)
                            and (
                                str(tag.get("category", "")) == "premade"
                                or "PREMADE" in str(tag.get("text", ""))
                                or str(tag.get("text", "")).endswith(" DUO")
                            )
                        )
                    ]
                    tags.append(premade_tag)
                    profile["tags"] = prioritize_tags(tags, limit=8)

    @staticmethod
    def _dedupe_tags(
        tags: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return prioritize_tags(tags, limit=8)

    @staticmethod
    def _build_tags(
        ranked: dict[str, Any],
        analysis: dict[str, Any],
        role: str,
        timeline: dict[str, Any],
        mastery: dict[str, Any],
        local_percentiles: dict[str, Any],
    ) -> list[dict[str, Any]]:
        sample = int(analysis.get("sample_games", 0) or 0)
        timeline_games = int(timeline.get("timeline_games", 0) or 0)
        candidates: list[dict[str, Any]] = []
        candidates.extend(session_tags(analysis))
        candidates.extend(champion_intelligence_tags(analysis, mastery, role))
        candidates.extend(role_timeline_tags(role, analysis, timeline))

        def add(
            text: str,
            tone: str = "positive",
            detail: str = "",
            priority: int = 50,
            group: str = "",
            evidence_games: int | None = None,
        ) -> None:
            if text in {
                str(tag.get("text", ""))
                for tag in candidates
            }:
                return
            evidence = sample if evidence_games is None else int(evidence_games)
            candidates.append(
                make_evidence_tag(
                    text,
                    tone,
                    detail,
                    priority=int(priority),
                    group=group,
                    evidence_games=evidence,
                    exact_evidence=group in {"rank", "session"} and evidence > 0,
                )
            )

        # Session-state tags are produced by session_tags(), which uses actual
        # timestamp gaps and current-session length rather than a single date flag.
        streak_count = int(analysis.get("streak_count", 0) or 0)
        streak_type = str(analysis.get("streak_type", "") or "")
        recent_wr = analysis.get("recent_win_rate")
        if streak_count >= 3 and streak_type == "win":
            add(
                f"{streak_count} WIN STREAK",
                "positive",
                f"Won the last {streak_count} Solo/Duo games",
                99,
                "form",
            )
        elif streak_count >= 3 and streak_type == "loss":
            add(
                f"{streak_count} LOSS STREAK",
                "negative",
                f"Lost the last {streak_count} Solo/Duo games",
                99,
                "form",
            )
        elif sample >= 6 and recent_wr is not None:
            if float(recent_wr) >= 65:
                add(
                    "STRONG FORM",
                    "positive",
                    f"Won {analysis.get('recent_wins', 0)} of the last {sample} ranked games",
                    86,
                    "form",
                )
            elif float(recent_wr) <= 35:
                add(
                    "POOR FORM",
                    "negative",
                    f"Won only {analysis.get('recent_wins', 0)} of the last {sample} ranked games",
                    86,
                    "form",
                )

        tier = str(ranked.get("tier", "") or "")
        if tier in {"MASTER", "GRANDMASTER", "CHALLENGER"}:
            add(
                "HIGH ELO",
                "positive",
                f"Current Solo/Duo rank: {ranked.get('rank', tier.title())}",
                76,
                "rank",
            )

        # Champion familiarity combines mastery and recent match history.
        champion_games = int(analysis.get("champion_games", 0) or 0)
        champion_wr = analysis.get("champion_win_rate")
        champion_share = float(analysis.get("champion_share", 0) or 0)
        mastery_level = int(mastery.get("mastery_level", 0) or 0)
        mastery_points = int(mastery.get("mastery_points", 0) or 0)
        mastery_rank = mastery.get("mastery_rank")
        mastery_days = mastery.get("mastery_last_play_days")

        mastery_detail = (
            f"Mastery {mastery_level} · {mastery_points:,} points"
            if mastery_points
            else "No meaningful mastery record was returned"
        )
        if mastery_rank:
            mastery_detail += f" · #{int(mastery_rank)} mastery champion"

        if (
            sample >= 8
            and champion_share >= 0.55
            and (mastery_points >= 250_000 or mastery_rank == 1)
        ):
            add(
                "CHAMPION MAIN",
                "positive",
                (
                    f"{mastery_detail} · current champion in "
                    f"{champion_games}/{sample} recent ranked games"
                ),
                99,
                "champion",
            )
        elif sample >= 10 and champion_share >= 0.60:
            add(
                "ONE-TRICK",
                "positive",
                (
                    f"Current champion in {champion_games}/{sample} recent games"
                    + (
                        f" · {float(champion_wr):.0f}% win rate"
                        if champion_wr is not None
                        else ""
                    )
                ),
                97,
                "champion",
            )
        elif (
            mastery_points >= 100_000
            or (sample >= 8 and champion_share >= 0.30)
        ):
            add(
                "COMFORT PICK",
                "positive",
                (
                    f"{mastery_detail} · {champion_games}/{sample} recent games"
                    + (
                        f" · {float(champion_wr):.0f}% win rate"
                        if champion_wr is not None
                        else ""
                    )
                ),
                90,
                "champion",
            )
        elif (
            sample >= 6
            and champion_games == 0
            and mastery_points < 25_000
            and mastery_level <= 4
        ):
            add(
                "CHAMP NEWBIE",
                "negative",
                (
                    f"No games on the current champion in the last {sample} ranked games"
                    f" · mastery {mastery_level} with {mastery_points:,} points"
                ),
                96,
                "champion",
            )
        elif sample >= 6 and champion_games == 0:
            add(
                "LOW CHAMP EXPERIENCE",
                "warning",
                (
                    f"No games on the current champion in the last {sample} ranked games"
                    f" · {mastery_detail}"
                ),
                91,
                "champion",
            )
        elif (
            mastery_days is not None
            and int(mastery_days) >= 180
            and champion_games <= 1
        ):
            add(
                "RETURNING PICK",
                "warning",
                f"Last mastery activity on this champion was about {int(mastery_days)} days ago",
                80,
                "champion",
            )

        unique_champions = int(analysis.get("unique_champions", 0) or 0)
        if sample >= 12 and unique_champions >= 10:
            add(
                "WIDE CHAMP POOL",
                "neutral",
                f"Played {unique_champions} different champions in {sample} recent ranked games",
                66,
                "champ_pool",
            )
        elif sample >= 12 and unique_champions <= 3:
            add(
                "NARROW CHAMP POOL",
                "neutral",
                f"Used only {unique_champions} champions in {sample} recent ranked games",
                68,
                "champ_pool",
            )

        # Match-detail behaviour.
        first_blood_rate = float(analysis.get("first_blood_rate", 0) or 0)
        early_advantage = float(
            analysis.get("early_advantage_rate", 0) or 0
        )
        avg_kp = float(analysis.get("avg_kp", 0) or 0)
        damage_share = float(
            analysis.get("avg_team_damage_share", 0) or 0
        )
        avg_solo_kills = float(
            analysis.get("avg_solo_kills", 0) or 0
        )
        avg_deaths = float(analysis.get("avg_deaths", 0) or 0)
        high_death_rate = float(
            analysis.get("high_death_game_rate", 0) or 0
        )
        low_death_rate = float(
            analysis.get("low_death_game_rate", 0) or 0
        )
        avg_cs = float(analysis.get("avg_cs_min", 0) or 0)
        avg_vision = float(analysis.get("avg_vision_min", 0) or 0)
        avg_control_wards = float(
            analysis.get("avg_control_wards", 0) or 0
        )
        objective_participation = float(
            analysis.get("avg_objective_participation", 0) or 0
        )

        if sample >= 8 and first_blood_rate >= 30:
            add(
                "FIRST BLOOD THREAT",
                "warning",
                f"Participated in first blood in {first_blood_rate:.0f}% of recent games",
                93,
                "early_action",
            )

        if (
            role in {"TOP", "MIDDLE", "BOTTOM"}
            and sample >= 8
            and early_advantage >= 40
        ):
            add(
                "LANE BULLY",
                "positive",
                f"Recorded an early gold/experience advantage in {early_advantage:.0f}% of recent games",
                91,
                "lane_pattern",
            )

        if avg_kp >= 62 and damage_share >= 24:
            add(
                "HIGH IMPACT",
                "positive",
                f"{avg_kp:.0f}% kill participation · {damage_share:.0f}% team damage share",
                91,
                "impact",
            )
        elif damage_share >= 28:
            add(
                "DAMAGE CARRY",
                "positive",
                f"Averages {damage_share:.0f}% of team champion damage",
                87,
                "impact",
            )

        if role in {"TOP", "MIDDLE"} and avg_solo_kills >= 0.75:
            add(
                "SOLO-KILL THREAT",
                "warning",
                f"Averages {avg_solo_kills:.2f} solo kills per ranked game",
                89,
                "duel",
            )

        if sample >= 8 and (avg_deaths >= 7.0 or high_death_rate >= 45):
            add(
                "HIGH DEATH RATE",
                "negative",
                f"Averages {avg_deaths:.1f} deaths · 8+ deaths in {high_death_rate:.0f}% of games",
                89,
                "safety",
            )
        elif sample >= 8 and avg_deaths <= 4.0 and low_death_rate >= 45:
            add(
                "SAFE PLAYER",
                "positive",
                f"Averages {avg_deaths:.1f} deaths · low-death game in {low_death_rate:.0f}% of games",
                77,
                "safety",
            )

        cs_thresholds = {
            "TOP": 7.0,
            "JUNGLE": 5.8,
            "MIDDLE": 7.2,
            "BOTTOM": 7.5,
            "UTILITY": 1.8,
        }
        if avg_cs >= cs_thresholds.get(role, 7.2):
            add(
                "STRONG FARMER",
                "positive",
                f"Averages {avg_cs:.1f} CS per minute over {sample} games",
                74,
                "farm",
            )

        if role == "UTILITY" and (
            avg_vision >= 1.25 or avg_control_wards >= 2.5
        ):
            add(
                "VISION CONTROL",
                "positive",
                f"{avg_vision:.2f} vision/min · {avg_control_wards:.1f} control wards/game",
                84,
                "vision",
            )
        elif role == "JUNGLE" and avg_vision >= 0.85:
            add(
                "GOOD VISION",
                "positive",
                f"Averages {avg_vision:.2f} vision score per minute",
                72,
                "vision",
            )

        if (
            role in {"TOP", "MIDDLE"}
            and float(analysis.get("avg_turrets", 0) or 0) >= 1.2
            and avg_kp < 55
        ):
            add(
                "SPLIT PUSHER",
                "warning",
                "High turret involvement with lower teamfight participation",
                81,
                "map_style",
            )

        if role == "TOP" and avg_kp <= 45 and avg_deaths <= 5.5:
            add(
                "WEAK-SIDE PLAYER",
                "neutral",
                f"Low {avg_kp:.0f}% kill participation while keeping deaths controlled",
                70,
                "map_style",
            )

        # Selective timeline analysis. This is intentionally sampled and cached.
        lead10 = float(timeline.get("lead_at_10_rate", 0) or 0)
        behind10 = float(timeline.get("behind_at_10_rate", 0) or 0)
        early_death = float(timeline.get("early_death_rate", 0) or 0)
        early_kp = float(
            timeline.get("early_kill_participation_rate", 0) or 0
        )
        early_roam = float(timeline.get("early_roam_rate", 0) or 0)
        early_objective = float(
            timeline.get("early_objective_rate", 0) or 0
        )
        comeback = float(timeline.get("comeback_rate", 0) or 0)
        throw_rate = float(timeline.get("throw_rate", 0) or 0)

        if timeline_games >= 2:
            if lead10 >= 50:
                add(
                    "EARLY LANE LEAD",
                    "positive",
                    f"Ahead of the lane opponent at 10 minutes in {lead10:.0f}% of {timeline_games} sampled games",
                    92,
                    "timeline_lane",
                    timeline_games,
                )
            elif behind10 >= 50:
                add(
                    "FALLS BEHIND EARLY",
                    "negative",
                    f"Behind the lane opponent at 10 minutes in {behind10:.0f}% of {timeline_games} sampled games",
                    92,
                    "timeline_lane",
                    timeline_games,
                )

            if early_death >= 50:
                add(
                    "EARLY DEATH RISK",
                    "negative",
                    f"Died before 10 minutes in {early_death:.0f}% of {timeline_games} sampled games",
                    94,
                    "early_survival",
                    timeline_games,
                )

            if role in {"MIDDLE", "UTILITY"} and early_roam >= 50:
                add(
                    "EARLY ROAMER",
                    "warning",
                    f"Joined an early fight away from the expected lane in {early_roam:.0f}% of sampled games",
                    91,
                    "roam",
                    timeline_games,
                )

            if role == "JUNGLE":
                invader_kills = int(
                    timeline.get("invader_kills", 0) or 0
                )
                invader_deaths = int(
                    timeline.get("invader_deaths", 0) or 0
                )
                invader_games = int(
                    timeline.get("invader_games", 0) or 0
                )
                if (
                    invader_deaths >= 2
                    and invader_deaths > invader_kills
                ):
                    add(
                        "RISKY INVADES",
                        "negative",
                        f"Died {invader_deaths} times in sampled enemy-side early fights",
                        96,
                        "jungle_early",
                        timeline_games,
                    )
                elif invader_games >= 2 or invader_kills >= 2:
                    add(
                        "EARLY INVADER",
                        "warning",
                        f"Enemy-side early fights in {invader_games} sampled games",
                        95,
                        "jungle_early",
                        timeline_games,
                    )

                if early_kp >= 50:
                    add(
                        "EARLY GANKER",
                        "warning",
                        f"Participated in a pre-10-minute kill in {early_kp:.0f}% of sampled games",
                        92,
                        "jungle_style",
                        timeline_games,
                    )
                elif early_kp == 0 and avg_cs >= 6.0:
                    add(
                        "FARMING JUNGLER",
                        "neutral",
                        "No early kill participation in the timeline sample and strong jungle CS",
                        77,
                        "jungle_style",
                        timeline_games,
                    )

            if (
                role in {"JUNGLE", "UTILITY"}
                and early_objective >= 50
            ):
                add(
                    "OBJECTIVE FOCUSED",
                    "positive",
                    f"Participated in an objective before 15 minutes in {early_objective:.0f}% of sampled games",
                    88,
                    "objective_style",
                    timeline_games,
                )

            if comeback >= 50:
                add(
                    "COMEBACK PLAYER",
                    "positive",
                    f"Won after being materially behind at 15 minutes in {comeback:.0f}% of sampled games",
                    83,
                    "lead_conversion",
                    timeline_games,
                )
            elif throw_rate >= 50:
                add(
                    "THROWS LEADS",
                    "negative",
                    f"Lost after holding a meaningful 15-minute lane lead in {throw_rate:.0f}% of sampled games",
                    88,
                    "lead_conversion",
                    timeline_games,
                )

        if role in {"MIDDLE", "UTILITY"} and avg_kp >= 67:
            add(
                "ROAMING",
                "positive",
                f"Very high {avg_kp:.0f}% kill participation for the role",
                78,
                "roam",
            )

        if role == "UTILITY" and first_blood_rate >= 25 and early_kp >= 50:
            add(
                "AGGRESSIVE SUPPORT",
                "warning",
                "High first-blood involvement and early kill participation",
                90,
                "support_style",
            )

        if role == "JUNGLE" and objective_participation >= 1.8:
            add(
                "OBJECTIVE FOCUSED",
                "positive",
                f"Averages {objective_participation:.1f} dragon/baron/herald takedowns per game",
                84,
                "objective_style",
            )

        late_games = int(analysis.get("late_game_games", 0) or 0)
        late_wr = analysis.get("late_game_win_rate")
        short_games = int(analysis.get("short_game_games", 0) or 0)
        short_wr = analysis.get("short_game_win_rate")
        if (
            role == "BOTTOM"
            and late_games >= 3
            and late_wr is not None
            and recent_wr is not None
            and float(late_wr) >= float(recent_wr) + 12
        ):
            add(
                "LATE-GAME PLAYER",
                "positive",
                f"{float(late_wr):.0f}% win rate in {late_games} games lasting 30+ minutes",
                79,
                "game_length",
            )
        elif (
            short_games >= 3
            and short_wr is not None
            and float(short_wr) >= 65
        ):
            add(
                "FAST GAME THREAT",
                "warning",
                f"{float(short_wr):.0f}% win rate in {short_games} games ending within 25 minutes",
                76,
                "game_length",
            )

        volatility = float(analysis.get("death_volatility", 0) or 0)
        if sample >= 10 and volatility >= 3.2:
            add(
                "VOLATILE",
                "warning",
                f"Death totals vary heavily between recent games (spread {volatility:.1f})",
                66,
                "consistency",
            )
        elif sample >= 10 and volatility <= 1.5:
            add(
                "CONSISTENT",
                "neutral",
                f"Very stable death totals across {sample} recent games",
                62,
                "consistency",
            )

        avg_steals = float(
            analysis.get("avg_objectives_stolen", 0) or 0
        )
        if role == "JUNGLE" and avg_steals >= 0.15:
            add(
                "OBJECTIVE THIEF",
                "warning",
                f"Averages {avg_steals:.2f} stolen objectives per recent game",
                84,
                "objective_special",
            )

        season_games = int(ranked.get("games", 0) or 0)
        season_wr = ranked.get("win_rate")
        if season_games >= 30 and season_wr is not None:
            if float(season_wr) >= 56:
                add(
                    "STRONG SEASON",
                    "positive",
                    f"{float(season_wr):.0f}% win rate over {season_games} Solo/Duo games",
                    68,
                    "season",
                )
            elif float(season_wr) <= 44:
                add(
                    "ROUGH SEASON",
                    "negative",
                    f"{float(season_wr):.0f}% win rate over {season_games} Solo/Duo games",
                    68,
                    "season",
                )

        candidates.extend(
            {
                **tag,
                "_priority": 75,
                "_group": "local_percentile",
            }
            for tag in LiveMatchScout._percentile_tags(local_percentiles)
        )

        return prioritize_tags(candidates, limit=8)

    @staticmethod
    def _local_json(endpoint: str) -> Any:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        request = Request(
            f"{_LOCAL_BASE}/{endpoint}",
            headers={"Accept": "application/json"},
        )
        with urlopen(request, timeout=2.5, context=context) as response:
            return json.loads(response.read().decode("utf-8"))

    def _riot_json(self, url: str, api_key: str) -> Any:
        # Every worker and every endpoint shares the same limiter. A 429 is
        # handled once internally using Riot's Retry-After header instead of
        # immediately failing the whole live-match analysis.
        for attempt in range(2):
            self._riot_limiter.acquire(url)
            request = Request(
                url,
                headers={
                    "Accept": "application/json",
                    "X-Riot-Token": api_key,
                    "User-Agent": "LeagueHighlights/LiveMatchV21",
                },
            )
            try:
                with urlopen(request, timeout=8.0) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw else None
            except HTTPError as exc:
                message = f"Riot API request failed ({exc.code})"
                retry_after = 1.0
                try:
                    retry_after = float(exc.headers.get("Retry-After", "1") or 1)
                except (TypeError, ValueError):
                    retry_after = 1.0

                try:
                    body = json.loads(exc.read().decode("utf-8"))
                    status = body.get("status", {}) if isinstance(body, dict) else {}
                    message = str(status.get("message", "") or message)
                except Exception:
                    pass

                if exc.code == 429:
                    self._riot_limiter.penalize(url, retry_after)
                    if attempt == 0:
                        continue

                raise RiotApiError(exc.code, message) from exc

        raise RiotApiError(429, "Riot API rate limit cooldown is active")
