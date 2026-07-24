from __future__ import annotations

import json
import logging
import math
import re
import ssl
import threading
import time
from itertools import permutations
from statistics import pstdev
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable
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
    session_tags,
    summarize_encounters,
)
from app.services.live_match_intelligence import (
    ChampionCatalog,
    EncounterStore,
    LocalBaselineStore,
    PlayerProfileDiskCache,
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


LIVE_MATCH_PATCH_BUILD = "V29.1-METHOD-BINDING-HOTFIX"


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
    PLAYER_CACHE_SECONDS = 5 * 60
    RANK_CACHE_SECONDS = 20 * 60
    HISTORY_CACHE_SECONDS = 5 * 60
    SUMMONER_CACHE_SECONDS = 12 * 60 * 60
    MASTERY_CACHE_SECONDS = 60 * 60
    FAST_SAMPLE_SIZE = 5
    PERFORMANCE_SAMPLE_SIZE = 10
    LCU_MATCH_SAMPLE_SIZE = 30
    RIOT_MATCH_SAMPLE_SIZE = 10
    HISTORY_MATCH_ID_COUNT = 30
    MAX_CONCURRENT_PLAYERS = 10

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
        self._lcu_rank_cache: dict[str, _CacheEntry] = {}
        self._lcu_history_cache: dict[str, _CacheEntry] = {}
        self._lcu_summoner_cache: dict[str, _CacheEntry] = {}
        self._mastery_cache: dict[tuple[str, str, str], _CacheEntry] = {}
        self._match_cache: dict[str, dict[str, Any]] = {}
        self._match_inflight: dict[str, threading.Event] = {}
        self._cache_lock = threading.RLock()
        cache_location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation
        )
        cache_root = Path(cache_location) if cache_location else Path.home() / ".league_highlights" / "cache"
        self._live_cache_root = cache_root / "live_match"
        self._match_cache_dir = self._live_cache_root / "matches"
        self._match_cache_dir.mkdir(parents=True, exist_ok=True)
        self._identity_cache_path = self._live_cache_root / "identity_puuids.json"
        self._identity_puuid_cache = self._load_identity_puuid_cache()
        self._profile_disk_cache = PlayerProfileDiskCache(
            self._live_cache_root / "players",
            ttl_seconds=self.PLAYER_CACHE_SECONDS,
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
        self._mastery_cache.clear()
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

                if not loading_phase:
                    self._flush_pending_encounters()
                    self._last_completed_signature = ""
                if not loading_phase or not self._last_roster_signature:
                    self._last_roster_signature = ""
                    self.roster_changed.emit(roster)

                if loading_phase:
                    if roster.get("spectator_rate_limited"):
                        self.status_changed.emit(
                            "rate_limited",
                            "Loading screen detected — Riot fallback is rate limited",
                        )
                    else:
                        self.status_changed.emit(
                            "loading_screen",
                            "Loading screen detected — waiting for the local roster",
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
                    self._last_completed_signature = ""
                self._last_roster_signature = signature
                self.roster_changed.emit(roster)
            elif self._analysis_is_current(signature, force):
                self.status_changed.emit("ready", "Live match ready — analysis cached")
                return

            total = len(roster["players"])
            completed = 0
            profiles: dict[str, dict[str, Any]] = {}
            progress_lock = threading.RLock()
            progress: dict[str, set[str]] = {
                "name": {
                    str(player.get("player_key", ""))
                    for player in roster["players"]
                    if self._is_real_display_name(
                        str(player.get("riot_id", "") or player.get("game_name", "") or "")
                    )
                },
                "rank": set(),
                "fast": set(),
                "ready": set(),
            }

            def progress_message() -> str:
                with progress_lock:
                    return (
                        f"Players {total}/{total} · Names {len(progress['name'])}/{total} · "
                        f"Ranks {len(progress['rank'])}/{total} · "
                        f"Quick {len(progress['fast'])}/{total} · "
                        f"30-game {len(progress['ready'])}/{total}"
                    )

            def report_progress(
                player_key: str,
                stage: str,
                payload: dict[str, Any] | None = None,
            ) -> None:
                if generation != self._generation:
                    return
                with progress_lock:
                    if payload and self._is_real_display_name(
                        str(payload.get("riot_id", "") or payload.get("game_name", "") or "")
                    ):
                        progress["name"].add(player_key)
                    if stage in progress:
                        progress[stage].add(player_key)
                self.status_changed.emit("loading", progress_message())

            self.status_changed.emit("loading", progress_message())

            with ThreadPoolExecutor(
                max_workers=max(1, min(self.MAX_CONCURRENT_PLAYERS, total)),
                thread_name_prefix="LiveScoutWorker",
            ) as executor:
                future_to_player = {
                    executor.submit(
                        self._player_profile,
                        player,
                        platform,
                        api_key,
                        report_progress,
                    ): player
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
                        # Public Riot data is only a fallback. Never discard local
                        # ranks/history or stop the other nine player workers.
                        logging.debug("Optional Riot fallback failed", exc_info=True)
                        stats = {
                            "state": "error",
                            "message": str(exc),
                            "rank_state": "unavailable",
                        }
                        if exc.status in {401, 403}:
                            self.status_changed.emit(
                                "key_invalid",
                                "Riot API fallback key was rejected — local scouting continues",
                            )
                        elif exc.status == 429:
                            self.status_changed.emit(
                                "rate_limited",
                                "Riot API fallback is rate limited — local scouting continues",
                            )
                    except Exception as exc:
                        logging.debug("Live Match player analysis failed", exc_info=True)
                        stats = {
                            "state": "error",
                            "message": str(exc),
                            "rank_state": "unavailable",
                        }

                    profiles[player_key] = stats
                    if str(stats.get("state", "")) in {"fast", "ready"}:
                        self.player_stats_changed.emit(player_key, dict(stats))
                    completed += 1

            self._assign_team_roles(roster, profiles)
            self._apply_lane_matchups(roster, profiles)
            self._apply_premade_groups(roster, profiles)
            self._apply_encounter_history(roster, profiles)
            self._record_local_baselines(profiles)

            for player_key, stats in profiles.items():
                stats["tags"] = most_valid_tags(list(stats.get("tags", ())))
                stats["state"] = (
                    "ready"
                    if str(stats.get("state", "")) in {"fast", "ready"}
                    else str(stats.get("state", "unavailable"))
                )
                self.player_stats_changed.emit(player_key, stats)

            self._stage_live_encounters(roster, profiles, signature)
            self._last_completed_signature = signature

            lcu_count = sum(
                1
                for stats in profiles.values()
                if str(stats.get("history_source", "")).startswith("lcu")
            )
            self.status_changed.emit(
                "ready",
                f"Live match ready — {completed}/{total} analysed · {lcu_count} local histories",
            )
        except (URLError, TimeoutError, ConnectionError, json.JSONDecodeError, OSError):
            # A short local-client interruption must not erase an already-rendered
            # roster. The next poll retries while the current cards stay visible.
            if not self._last_roster_signature:
                self.roster_changed.emit(
                    {"players": [], "allies": [], "enemies": [], "active_team": ""}
                )
            self.status_changed.emit("waiting", "Waiting for the League client")
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

    @staticmethod
    def _is_riot_api_puuid(value: Any) -> bool:
        """Return True only for a Riot API compatible encrypted PUUID.

        Some LCU/gameflow builds expose a 36-character internal player UUID in
        fields named ``puuid``/``playerUuid``. That value works with local LCU
        endpoints but Riot's public API rejects it with "Exception decrypting".
        Public API PUUIDs are substantially longer, so keep both identities
        separate and never send the local UUID to Riot.
        """
        candidate = str(value or "").strip()
        return len(candidate) >= 50

    @staticmethod
    def _is_real_display_name(value: str) -> bool:
        text = " ".join(str(value or "").strip().split())
        lowered = text.casefold()
        return bool(
            text
            and lowered not in {"unknown", "unknown player"}
            and not re.fullmatch(r"player\s+\d+", lowered)
        )

    @staticmethod
    def _cache_identity_hint(player: dict[str, Any]) -> str:
        riot_id = str(player.get("riot_id", "") or "").strip()
        game_name = str(player.get("game_name", "") or "").strip()
        tag_line = str(player.get("tag_line", "") or "").strip()
        named = riot_id or (f"{game_name}#{tag_line}" if game_name and tag_line else game_name)
        normalized = normalize_name(named)
        if normalized:
            return normalized
        return str(
            player.get("lcu_player_id", "")
            or player.get("puuid", "")
            or player.get("player_key", "")
            or "unknown"
        ).casefold()

    def _cache_lookup(
        self,
        store: dict[Any, _CacheEntry],
        key: Any,
        ttl_seconds: float,
    ) -> tuple[dict[str, Any] | None, float, bool]:
        with self._cache_lock:
            entry = store.get(key)
            if entry is None:
                return None, 0.0, False
            age = max(0.0, time.monotonic() - entry.created_at)
            return dict(entry.payload), age, age <= float(ttl_seconds)

    def _cache_store(
        self,
        store: dict[Any, _CacheEntry],
        key: Any,
        payload: dict[str, Any],
    ) -> None:
        with self._cache_lock:
            store[key] = _CacheEntry(time.monotonic(), dict(payload))

    @staticmethod
    def _static_profile_copy(
        payload: dict[str, Any],
        player: dict[str, Any],
    ) -> dict[str, Any]:
        cached = dict(payload)
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
            "role_assignment_confidence",
            "role_assignment_margin",
        ):
            cached.pop(dynamic_key, None)
        cached["tags"] = [
            tag
            for tag in list(cached.get("tags", ()))
            if not (
                isinstance(tag, dict)
                and (
                    str(tag.get("category", "")) in {"premade", "encounter", "matchup"}
                    or str(tag.get("text", "")).startswith("PREMADE ")
                    or str(tag.get("text", "")).startswith("SEEN ")
                    or str(tag.get("text", ""))
                    in {"ALLY BEFORE", "ENEMY BEFORE", "PLAYED BEFORE"}
                )
            )
        ]
        cached["current_role"] = str(player.get("role", "") or "").upper()
        cached["state"] = "ready"
        return cached

    def _discover_roster(self, platform: str, api_key: str) -> dict[str, Any]:
        """Build the roster with local data first and use Spectator once for PUUIDs.

        Port 2999 is free and gives names, champions, teams, roles and spells, but
        normally omits PUUIDs. A single Spectator-v5 response contains the PUUIDs
        for all ten players, so merging that response avoids up to ten separate
        Account-v1 identity lookups.
        """
        phase = ""
        current_summoner: dict[str, Any] = {}
        try:
            phase = self._lcu.gameflow_phase()
            self._last_gameflow_phase = phase
            self._last_gameflow_phase_at = time.monotonic()
        except Exception:
            if time.monotonic() - self._last_gameflow_phase_at <= 8.0:
                phase = self._last_gameflow_phase

        try:
            current_summoner = self._lcu.current_summoner()
        except Exception:
            current_summoner = {}

        self_player_id = str(
            current_summoner.get("puuid", "")
            or current_summoner.get("playerUuid", "")
            or ""
        ).strip()

        # The current-summoner endpoint can expose an internal 36-character UUID.
        # Keep it for local LCU calls, but only use a validated encrypted PUUID
        # with Spectator-v5 and other public Riot endpoints.
        spectator_self_puuid = (
            self_player_id
            if self._is_riot_api_puuid(self_player_id)
            else ""
        )
        current_identity = {
            "riot_id": str(current_summoner.get("riotId", "") or ""),
            "game_name": str(
                current_summoner.get("gameName", "")
                or current_summoner.get("riotIdGameName", "")
                or current_summoner.get("displayName", "")
                or ""
            ),
            "tag_line": str(
                current_summoner.get("tagLine", "")
                or current_summoner.get("riotIdTagLine", "")
                or ""
            ),
        }
        if not spectator_self_puuid:
            with self._cache_lock:
                cached_self = self._identity_puuid_cache.get(
                    self._identity_cache_key(current_identity),
                    "",
                )
            if self._is_riot_api_puuid(cached_self):
                spectator_self_puuid = cached_self
        if spectator_self_puuid:
            self._last_known_self_puuid = spectator_self_puuid
        elif self._is_riot_api_puuid(self._last_known_self_puuid):
            spectator_self_puuid = self._last_known_self_puuid

        local_roster: dict[str, Any] = {}
        try:
            local_roster = self._read_local_roster()
        except Exception:
            local_roster = {}

        if local_roster.get("players"):
            self._attach_local_summoner_data(
                local_roster,
                current_summoner=current_summoner,
                self_puuid=self_player_id,
            )
            self._apply_persistent_identity_cache(local_roster)

            # Port 2999 normally omits PUUIDs, while the local gameflow session
            # often has them. Merge those identities first; this costs no Riot
            # quota and lets LCU player-history work even without an API key.
            try:
                gameflow = self._lcu.gameflow_session()
            except Exception:
                gameflow = {}
            gameflow_identities = self._read_lcu_gameflow_roster(
                gameflow,
                current_summoner=current_summoner,
                self_puuid=self_player_id,
            )
            if gameflow_identities.get("players"):
                self._merge_roster_identities(local_roster, gameflow_identities)

            # Prefer the already-fetched loading-screen roster. When the app is
            # opened mid-game, spend one Spectator request to obtain all ten PUUIDs
            # instead of allowing every player worker to call Account-v1 separately.
            now = time.monotonic()
            identity_roster: dict[str, Any] = {}
            if (
                self._spectator_roster_cache.get("players")
                and now - self._spectator_roster_cached_at
                <= self.SPECTATOR_ROSTER_CACHE_SECONDS
            ):
                identity_roster = self._spectator_roster_cache
            elif (
                api_key
                and spectator_self_puuid
                and any(
                    not str(player.get("puuid", "") or "").strip()
                    for player in local_roster.get("players", ())
                    if isinstance(player, dict)
                )
                and now - self._last_spectator_attempt >= self.SPECTATOR_RETRY_SECONDS
            ):
                self._last_spectator_attempt = now
                try:
                    fetched = self._read_spectator_roster(
                        self_puuid=spectator_self_puuid,
                        platform=platform,
                        api_key=api_key,
                        resolve_missing_identities=False,
                    )
                except RiotApiError as exc:
                    # Spectator is identity enrichment only. A stale/internal
                    # UUID, expired key or transient Riot error must never replace
                    # working local ranks with an error card.
                    if exc.status == 429:
                        local_roster["spectator_rate_limited"] = True
                    elif exc.status in {401, 403}:
                        local_roster["spectator_key_invalid"] = True
                    logging.debug(
                        "Optional Spectator identity enrichment failed: %s",
                        exc,
                    )
                    fetched = {}
                if fetched.get("players"):
                    identity_roster = fetched
                    self._spectator_roster_cache = dict(fetched)
                    self._spectator_roster_cached_at = time.monotonic()

            if identity_roster.get("players"):
                self._merge_roster_identities(local_roster, identity_roster)

            self._remember_roster_identities(local_roster)
            local_roster["roster_source"] = "live_client"
            local_roster["gameflow_phase"] = phase or "InProgress"
            return local_roster

        # During loading the Live Client API on port 2999 may not exist yet, but
        # the LCU gameflow session can already expose both teams and their PUUIDs.
        # This avoids waiting for Spectator-v5 and also allows local-only scouting.
        if phase in {"GameStart", "InProgress", "Reconnect"}:
            try:
                gameflow = self._lcu.gameflow_session()
            except Exception:
                gameflow = {}
            gameflow_roster = self._read_lcu_gameflow_roster(
                gameflow,
                current_summoner=current_summoner,
                self_puuid=self_player_id,
            )
            if gameflow_roster.get("players"):
                self._attach_local_summoner_data(
                    gameflow_roster,
                    current_summoner=current_summoner,
                    self_puuid=self_player_id,
                )
                self._apply_persistent_identity_cache(gameflow_roster)

                # Older client builds can expose placeholder/missing PUUIDs in
                # gameflow. When a key exists, merge one Spectator response into
                # the local roster instead of resolving ten identities separately.
                missing_puuid = any(
                    not str(player.get("puuid", "") or "").strip()
                    for player in gameflow_roster.get("players", ())
                    if isinstance(player, dict)
                )
                if api_key and spectator_self_puuid and missing_puuid:
                    now = time.monotonic()
                    identity_roster: dict[str, Any] = {}
                    if (
                        self._spectator_roster_cache.get("players")
                        and now - self._spectator_roster_cached_at
                        <= self.SPECTATOR_ROSTER_CACHE_SECONDS
                    ):
                        identity_roster = self._spectator_roster_cache
                    elif now - self._last_spectator_attempt >= self.SPECTATOR_RETRY_SECONDS:
                        self._last_spectator_attempt = now
                        try:
                            fetched = self._read_spectator_roster(
                                self_puuid=spectator_self_puuid,
                                platform=platform,
                                api_key=api_key,
                                resolve_missing_identities=False,
                            )
                        except RiotApiError as exc:
                            fetched = {} if exc.status in {404, 429} else {}
                        if fetched.get("players"):
                            identity_roster = fetched
                            self._spectator_roster_cache = dict(fetched)
                            self._spectator_roster_cached_at = time.monotonic()
                    if identity_roster.get("players"):
                        self._merge_roster_identities(
                            gameflow_roster,
                            identity_roster,
                        )

                self._remember_roster_identities(gameflow_roster)
                gameflow_roster["gameflow_phase"] = phase
                gameflow_roster["roster_source"] = "lcu_gameflow"
                return gameflow_roster

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

        if not api_key or not spectator_self_puuid:
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
            self._remember_roster_identities(cached_roster)
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
                self_puuid=spectator_self_puuid,
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
            self._remember_roster_identities(roster)
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

    def _read_lcu_gameflow_roster(
        self,
        session: dict[str, Any],
        *,
        current_summoner: dict[str, Any],
        self_puuid: str,
    ) -> dict[str, Any]:
        """Build a ten-player roster from the local gameflow session.

        The endpoint is undocumented and its fields vary across client versions,
        so every field is optional and port 2999/Spectator remain fallback paths.
        """
        if not isinstance(session, dict):
            return {}
        game_data = session.get("gameData", {})
        if not isinstance(game_data, dict):
            return {}

        team_one = game_data.get("teamOne", [])
        team_two = game_data.get("teamTwo", [])
        if not isinstance(team_one, list) or not isinstance(team_two, list):
            return {}
        if not team_one and not team_two:
            return {}

        current_summoner_id = str(
            current_summoner.get("summonerId", "")
            or current_summoner.get("id", "")
            or ""
        )
        current_game_name = str(
            current_summoner.get("gameName", "")
            or current_summoner.get("riotIdGameName", "")
            or current_summoner.get("displayName", "")
            or ""
        ).casefold()

        players: list[dict[str, Any]] = []
        active_team = ""
        for team_name, raw_team in (("ORDER", team_one), ("CHAOS", team_two)):
            for index, raw in enumerate(raw_team):
                if not isinstance(raw, dict):
                    continue
                lcu_player_id = str(
                    raw.get("puuid", "")
                    or raw.get("playerUuid", "")
                    or ""
                ).strip()
                puuid = (
                    lcu_player_id
                    if self._is_riot_api_puuid(lcu_player_id)
                    else ""
                )
                game_name = str(
                    raw.get("riotIdGameName", "")
                    or raw.get("gameName", "")
                    or raw.get("summonerName", "")
                    or raw.get("summonerInternalName", "")
                    or ""
                ).strip()
                tag_line = str(
                    raw.get("riotIdTagLine", "")
                    or raw.get("tagLine", "")
                    or ""
                ).strip()
                riot_id = str(raw.get("riotId", "") or "").strip()
                if not riot_id and game_name and tag_line:
                    riot_id = f"{game_name}#{tag_line}"
                if not game_name and "#" in riot_id:
                    game_name, tag_line = riot_id.rsplit("#", 1)
                if not riot_id:
                    riot_id = game_name or f"Player {len(players) + 1}"

                champion_id = int(raw.get("championId", 0) or 0)
                champion = (
                    self._champion_catalog.champion_name(champion_id)
                    if champion_id
                    else "Unknown"
                )
                role = str(
                    raw.get("selectedPosition", "")
                    or raw.get("assignedPosition", "")
                    or raw.get("teamPosition", "")
                    or ""
                ).upper()
                spell_ids = {
                    int(raw.get("spell1Id", 0) or 0),
                    int(raw.get("spell2Id", 0) or 0),
                }
                if not role and 11 in spell_ids:
                    role = "JUNGLE"

                summoner_id = str(raw.get("summonerId", "") or "")
                is_active = bool(
                    (lcu_player_id and self_puuid and lcu_player_id == self_puuid)
                    or (
                        summoner_id
                        and current_summoner_id
                        and summoner_id == current_summoner_id
                    )
                    or (
                        game_name
                        and current_game_name
                        and game_name.casefold() == current_game_name
                    )
                )
                if is_active:
                    active_team = team_name

                player_key = (puuid or lcu_player_id or riot_id or f"{team_name}:{champion}:{index}").casefold()
                players.append(
                    {
                        "player_key": player_key,
                        "puuid": puuid,
                        "lcu_player_id": lcu_player_id,
                        "riot_id": riot_id,
                        "game_name": game_name,
                        "tag_line": tag_line,
                        "champion": champion,
                        "champion_id": champion_id,
                        "role": role,
                        "team": team_name,
                        "is_active": is_active,
                        "spells": ["Smite"] if 11 in spell_ids else [],
                        "roster_source": "lcu_gameflow",
                    }
                )

        if not players:
            return {}
        players.sort(
            key=lambda player: (
                0 if player.get("team") == active_team else 1,
                _ROLE_ORDER.get(str(player.get("role", "")), 8),
                str(player.get("riot_id", "")).casefold(),
            )
        )
        allies = (
            [p for p in players if p.get("team") == active_team]
            if active_team
            else [p for p in players if p.get("team") == "ORDER"]
        )
        enemies = (
            [p for p in players if p.get("team") != active_team]
            if active_team
            else [p for p in players if p.get("team") == "CHAOS"]
        )

        game_start_ms = int(
            game_data.get("gameStartTime", 0)
            or game_data.get("gameStartTimestamp", 0)
            or 0
        )
        game_started_at = game_start_ms // 1000 if game_start_ms > 10_000_000_000 else game_start_ms
        if game_started_at:
            game_started_at = (game_started_at // 60) * 60
        queue = game_data.get("queue", {})
        queue_id = int(queue.get("id", 0) or 0) if isinstance(queue, dict) else 0
        return {
            "players": players,
            "allies": allies,
            "enemies": enemies,
            "active_team": active_team,
            "game_started_at": game_started_at,
            "game_id": str(game_data.get("gameId", "") or ""),
            "queue_id": queue_id,
        }

    @staticmethod
    def _identity_cache_key(player: dict[str, Any]) -> str:
        riot_id = str(player.get("riot_id", "") or "").strip()
        if riot_id and "#" in riot_id:
            return riot_id.casefold()
        game_name = str(player.get("game_name", "") or "").strip()
        tag_line = str(player.get("tag_line", "") or "").strip()
        if game_name and tag_line:
            return f"{game_name}#{tag_line}".casefold()
        return ""

    def _load_identity_puuid_cache(self) -> dict[str, str]:
        try:
            raw = json.loads(self._identity_cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(key).casefold(): str(value)
            for key, value in raw.items()
            if (
                str(key).strip()
                and self._is_riot_api_puuid(value)
            )
        }

    def _save_identity_puuid_cache_locked(self) -> None:
        # Keep the file bounded while retaining the newest identities.
        if len(self._identity_puuid_cache) > 5000:
            excess = len(self._identity_puuid_cache) - 5000
            for key in list(self._identity_puuid_cache)[:excess]:
                self._identity_puuid_cache.pop(key, None)
        temporary = self._identity_cache_path.with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps(self._identity_puuid_cache, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(self._identity_cache_path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _remember_identity(self, player: dict[str, Any], puuid: str) -> None:
        key = self._identity_cache_key(player)
        puuid = str(puuid or "").strip()
        if not key or not self._is_riot_api_puuid(puuid):
            return
        with self._cache_lock:
            if self._identity_puuid_cache.get(key) == puuid:
                return
            self._identity_puuid_cache[key] = puuid
            self._save_identity_puuid_cache_locked()

    def _remember_roster_identities(self, roster: dict[str, Any]) -> None:
        updates: dict[str, str] = {}
        for player in roster.get("players", ()):
            if not isinstance(player, dict):
                continue
            key = self._identity_cache_key(player)
            puuid = str(player.get("puuid", "") or "").strip()
            if key and self._is_riot_api_puuid(puuid):
                updates[key] = puuid
        if not updates:
            return
        with self._cache_lock:
            changed = any(self._identity_puuid_cache.get(k) != v for k, v in updates.items())
            if not changed:
                return
            self._identity_puuid_cache.update(updates)
            self._save_identity_puuid_cache_locked()

    def _apply_persistent_identity_cache(self, roster: dict[str, Any]) -> None:
        with self._cache_lock:
            identities = dict(self._identity_puuid_cache)
        for player in roster.get("players", ()):
            if not isinstance(player, dict) or player.get("puuid"):
                continue
            puuid = identities.get(self._identity_cache_key(player), "")
            if self._is_riot_api_puuid(puuid):
                player["puuid"] = puuid
                player["player_key"] = puuid.casefold()

    def _attach_local_summoner_data(
        self,
        roster: dict[str, Any],
        *,
        current_summoner: dict[str, Any],
        self_puuid: str,
    ) -> None:
        current_riot_id = str(current_summoner.get("riotId", "") or "").strip()
        current_game_name = str(
            current_summoner.get("gameName", "")
            or current_summoner.get("displayName", "")
            or ""
        ).strip()
        current_tag = str(current_summoner.get("tagLine", "") or "").strip()
        if not current_riot_id and current_game_name and current_tag:
            current_riot_id = f"{current_game_name}#{current_tag}"

        active_team = str(roster.get("active_team", "") or "")
        for player in roster.get("players", ()):
            if not isinstance(player, dict):
                continue
            is_active = bool(player.get("is_active"))
            if not is_active and current_riot_id:
                is_active = self._identity_cache_key(player) == current_riot_id.casefold()
            if not is_active:
                continue
            player["is_active"] = True
            if self_puuid:
                player["lcu_player_id"] = self_puuid
                player["player_key"] = self_puuid.casefold()
                if self._is_riot_api_puuid(self_puuid):
                    player["puuid"] = self_puuid
            if "summonerLevel" in current_summoner:
                player["account_level"] = int(current_summoner.get("summonerLevel", 0) or 0)
            if "profileIconId" in current_summoner:
                player["profile_icon_id"] = int(current_summoner.get("profileIconId", 0) or 0)
            active_team = str(player.get("team", "") or active_team)
            break

        if active_team:
            roster["active_team"] = active_team
            roster["allies"] = [
                player for player in roster.get("players", ())
                if isinstance(player, dict) and player.get("team") == active_team
            ]
            roster["enemies"] = [
                player for player in roster.get("players", ())
                if isinstance(player, dict) and player.get("team") != active_team
            ]

    def _merge_roster_identities(
        self,
        local_roster: dict[str, Any],
        identity_roster: dict[str, Any],
    ) -> None:
        by_identity: dict[str, dict[str, Any]] = {}
        by_slot: dict[tuple[str, str], dict[str, Any]] = {}
        for source in identity_roster.get("players", ()):
            if not isinstance(source, dict):
                continue
            identity = self._identity_cache_key(source)
            if identity:
                by_identity[identity] = source
            slot = (
                str(source.get("team", "") or "").upper(),
                normalize_name(str(source.get("champion", "") or "")),
            )
            if slot[0] and slot[1]:
                by_slot[slot] = source

        for player in local_roster.get("players", ()):
            if not isinstance(player, dict):
                continue
            source = by_identity.get(self._identity_cache_key(player))
            if source is None:
                slot = (
                    str(player.get("team", "") or "").upper(),
                    normalize_name(str(player.get("champion", "") or "")),
                )
                source = by_slot.get(slot)
            if source is None:
                continue
            lcu_player_id = str(
                source.get("lcu_player_id", "")
                or source.get("puuid", "")
                or ""
            ).strip()
            if lcu_player_id:
                player["lcu_player_id"] = lcu_player_id
                player["player_key"] = lcu_player_id.casefold()
            puuid = str(source.get("puuid", "") or "").strip()
            if self._is_riot_api_puuid(puuid):
                player["puuid"] = puuid
                player["player_key"] = puuid.casefold()
            if not player.get("champion_id") and source.get("champion_id"):
                player["champion_id"] = source.get("champion_id")
            if not player.get("game_name") and source.get("game_name"):
                player["game_name"] = source.get("game_name")
            if not player.get("tag_line") and source.get("tag_line"):
                player["tag_line"] = source.get("tag_line")
            if not player.get("riot_id") and source.get("riot_id"):
                player["riot_id"] = source.get("riot_id")


    def _read_spectator_roster(
        self,
        self_puuid: str,
        platform: str,
        api_key: str,
        resolve_missing_identities: bool = True,
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

            candidate_puuid = str(
                raw.get("puuid", "")
                or raw.get("encryptedPUUID", "")
                or ""
            ).strip()
            puuid = (
                candidate_puuid
                if self._is_riot_api_puuid(candidate_puuid)
                else ""
            )
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
        if missing and resolve_missing_identities:
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

            puuid = str(
                raw.get("puuid", "")
                or raw.get("encryptedPUUID", "")
                or ""
            ).strip()
            player_key = (puuid or riot_id or f"{team}:{champion}:{index}").casefold()
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
                    "puuid": puuid,
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
        progress_callback: Callable[[str, str, dict[str, Any] | None], None] | None = None,
    ) -> dict[str, Any]:
        player_key = str(player.get("player_key", ""))
        champion = str(player.get("champion", "") or "")
        game_name = str(player.get("game_name", "") or "").strip()
        tag_line = str(player.get("tag_line", "") or "").strip()
        riot_id = str(player.get("riot_id", "") or "").strip()
        cache_key = (
            platform,
            self._cache_identity_hint(player),
            champion.casefold(),
        )
        now = time.monotonic()

        def report(stage: str, payload: dict[str, Any]) -> None:
            if progress_callback is not None:
                progress_callback(player_key, stage, payload)

        cached = self._player_cache.get(cache_key)
        if cached and now - cached.created_at < self.PLAYER_CACHE_SECONDS:
            payload = self._static_profile_copy(cached.payload, player)
            payload["cache_age_seconds"] = round(now - cached.created_at, 1)
            payload["profile_cache_state"] = "memory"
            report("rank", payload)
            report("fast", payload)
            report("ready", payload)
            return payload

        route = _PLATFORM_TO_ROUTE.get(platform, "europe")
        raw_player_id = str(
            player.get("lcu_player_id", "")
            or player.get("puuid", "")
            or ""
        ).strip()
        local_player_id = raw_player_id
        api_puuid = (
            str(player.get("puuid", "") or "").strip()
            if self._is_riot_api_puuid(player.get("puuid"))
            else ""
        )
        if not api_puuid:
            with self._cache_lock:
                cached_api_puuid = self._identity_puuid_cache.get(
                    self._identity_cache_key(player),
                    "",
                )
            if self._is_riot_api_puuid(cached_api_puuid):
                api_puuid = cached_api_puuid

        def ensure_api_puuid() -> str:
            nonlocal api_puuid
            if self._is_riot_api_puuid(api_puuid):
                return api_puuid
            if not api_key or not game_name or not tag_line:
                return ""
            account_url = (
                f"https://{route}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/"
                f"{quote(game_name, safe='')}/{quote(tag_line, safe='')}"
            )
            try:
                account = self._riot_json(account_url, api_key)
            except (RiotApiError, URLError, TimeoutError, OSError) as exc:
                logging.debug(
                    "Could not resolve public PUUID for %s: %s",
                    player.get("riot_id", game_name),
                    exc,
                )
                return ""
            candidate = str(account.get("puuid", "") or "").strip()
            if not self._is_riot_api_puuid(candidate):
                return ""
            api_puuid = candidate
            player["puuid"] = candidate
            self._remember_identity(player, candidate)
            return candidate

        if not local_player_id and not api_puuid:
            api_puuid = ensure_api_puuid()
            local_player_id = api_puuid
        if not local_player_id and not api_puuid:
            payload = {
                "state": "unavailable",
                "message": "Player identity unavailable from the local client",
                "rank_state": "unavailable",
                "riot_id": riot_id,
                "game_name": game_name,
                "tag_line": tag_line,
            }
            self._player_cache[cache_key] = _CacheEntry(now, payload)
            report("ready", payload)
            return payload

        if bool(player.get("is_active")) and self._is_riot_api_puuid(api_puuid):
            self._last_known_self_puuid = api_puuid

        profile_identity = api_puuid or local_player_id or player_key
        disk_cached = self._profile_disk_cache.load(profile_identity, champion)
        if disk_cached and int(disk_cached.get("profile_schema", 0) or 0) >= 30:
            payload = self._static_profile_copy(disk_cached, player)
            payload["puuid"] = api_puuid or local_player_id
            payload["profile_cache_state"] = "disk"
            self._player_cache[cache_key] = _CacheEntry(now, payload)
            report("rank", payload)
            report("fast", payload)
            report("ready", payload)
            return payload

        identity_fields = {
            "riot_id": riot_id,
            "game_name": game_name,
            "tag_line": tag_line,
        }

        local_level = player.get("account_level")
        local_icon = player.get("profile_icon_id")
        summoner_profile: dict[str, Any] | None = None
        if local_level is not None or local_icon is not None:
            summoner_profile = {
                "account_level": int(local_level) if local_level is not None else None,
                "profile_icon_id": int(local_icon) if local_icon is not None else None,
                "profile_cache_state": "roster",
            }
        if summoner_profile is None:
            summoner_profile = self._lcu_summoner_profile(local_player_id)
        profile_source = "lcu" if summoner_profile is not None else "unavailable"
        if summoner_profile is not None:
            profile_cache_state = str(summoner_profile.pop("_cache_state", "") or "")
            if profile_cache_state in {"fresh_cache", "stale"}:
                profile_source = "lcu_cache"
        if summoner_profile is None:
            fallback_puuid = ensure_api_puuid() if api_key else ""
            if fallback_puuid:
                try:
                    summoner_profile = self._summoner_profile(
                        fallback_puuid,
                        platform,
                        api_key,
                    )
                    profile_source = "riot"
                except (RiotApiError, URLError, TimeoutError, OSError):
                    logging.debug("Summoner profile fallback unavailable", exc_info=True)
                    summoner_profile = None
            if summoner_profile is None:
                summoner_profile = {
                    "account_level": None,
                    "profile_icon_id": None,
                }

        ranked = self._lcu_ranked_entry(local_player_id)
        rank_source = "lcu"
        if ranked is None:
            fallback_puuid = ensure_api_puuid() if api_key else ""
            if fallback_puuid:
                try:
                    ranked = self._ranked_entry(
                        fallback_puuid,
                        platform,
                        api_key,
                    )
                    rank_source = "riot"
                except (RiotApiError, URLError, TimeoutError, OSError):
                    logging.debug("Rank fallback unavailable", exc_info=True)
                    ranked = None
            if ranked is None:
                ranked = self._empty_ranked_entry("unavailable")
                rank_source = "unavailable"
        else:
            cache_state = str(ranked.pop("_cache_state", "fresh") or "fresh")
            rank_source = "lcu_cache" if cache_state in {"fresh_cache", "stale"} else "lcu"

        mastery = self._empty_mastery()
        mastery_source = "unavailable"
        if api_key and self._is_riot_api_puuid(api_puuid):
            mastery, mastery_source = self._cached_champion_mastery(
                api_puuid,
                champion,
                platform,
                api_key,
            )

        current_role = str(player.get("role", "") or "").upper()
        basic_analysis = self._analyse_samples([], champion)
        basic_payload = self._compose_profile_payload(
            state="partial",
            puuid=api_puuid or local_player_id,
            current_role=current_role,
            inferred_role=current_role,
            summoner_profile=summoner_profile,
            ranked=ranked,
            mastery=mastery,
            analysis=basic_analysis,
            role_status={
                "role_state": "unclear",
                "role_status_label": "",
                "role_status_tone": "",
            },
            match_ids=[],
            tags=[],
            local_percentiles={},
        )
        basic_payload.update(
            {
                **identity_fields,
                "profile_source": profile_source,
                "rank_source": rank_source,
                "history_source": "loading",
                "mastery_source": mastery_source,
                "analysis_target_games": self.LCU_MATCH_SAMPLE_SIZE,
                "analysis_stage": "rank",
            }
        )
        self.player_stats_changed.emit(player_key, basic_payload)
        report("rank", basic_payload)

        local_history = self._lcu_recent_ranked_history(
            local_player_id,
            self.HISTORY_MATCH_ID_COUNT,
        )
        history_cache_state = ""
        if local_history is not None:
            all_local_samples, match_ids, history_cache_state = local_history
            samples = list(all_local_samples[: self.LCU_MATCH_SAMPLE_SIZE])
            history_source = (
                "lcu_cache" if history_cache_state in {"fresh_cache", "stale"} else "lcu"
            )
        elif api_key and ensure_api_puuid():
            try:
                match_ids = self._recent_ranked_match_ids(
                    api_puuid,
                    route,
                    api_key,
                    self.HISTORY_MATCH_ID_COUNT,
                )
                samples = []
                history_source = "riot"
            except (RiotApiError, URLError, TimeoutError, OSError) as exc:
                logging.debug(
                    "Riot history fallback unavailable for %s: %s",
                    player.get("riot_id", game_name),
                    exc,
                )
                match_ids = []
                samples = []
                history_source = "unavailable"
        else:
            match_ids = []
            samples = []
            history_source = "unavailable"

        analysis_target_games = (
            self.LCU_MATCH_SAMPLE_SIZE
            if history_source.startswith("lcu")
            else self.RIOT_MATCH_SAMPLE_SIZE
            if history_source == "riot"
            else 0
        )
        fast_limit = min(
            self.FAST_SAMPLE_SIZE,
            len(samples) if history_source.startswith("lcu") else len(match_ids),
            analysis_target_games,
        )
        if history_source == "riot":
            for match_id in match_ids[:fast_limit]:
                try:
                    sample = self._sample_for_match(match_id, api_puuid, route, api_key)
                except (RiotApiError, URLError, TimeoutError, OSError):
                    logging.debug("Fast Riot match fallback failed", exc_info=True)
                    continue
                if sample is not None:
                    samples.append(sample)
            fast_samples = list(samples)
        else:
            fast_samples = list(samples[:fast_limit])

        fast_analysis = self._analyse_samples(fast_samples, champion)
        fast_role = current_role or str(fast_analysis.get("main_role", "") or "")
        fast_role_status = self._role_status(
            fast_role,
            fast_analysis,
            current_role_confirmed=bool(current_role),
            assignment_confidence="high" if current_role else "low",
        )
        fast_percentiles = self._baseline_store.percentiles(fast_role, fast_analysis)
        fast_tags = self._build_tags(
            ranked,
            fast_analysis,
            fast_role,
            mastery,
            fast_percentiles,
        )
        if fast_role_status["role_state"] == "off_role":
            fast_tags.insert(0, self._role_tag(fast_role, fast_analysis, fast_role_status))

        fast_payload = self._compose_profile_payload(
            state="fast",
            puuid=api_puuid or local_player_id,
            current_role=current_role,
            inferred_role=fast_role,
            summoner_profile=summoner_profile,
            ranked=ranked,
            mastery=mastery,
            analysis=fast_analysis,
            role_status=fast_role_status,
            match_ids=match_ids,
            tags=fast_tags,
            local_percentiles=fast_percentiles,
        )
        fast_payload.update(
            {
                **identity_fields,
                "profile_source": profile_source,
                "rank_source": rank_source,
                "history_source": history_source,
                "mastery_source": mastery_source,
                "analysis_target_games": analysis_target_games,
                "analysis_stage": "quick",
            }
        )
        self.player_stats_changed.emit(player_key, dict(fast_payload))
        report("fast", fast_payload)

        if history_source == "riot":
            for match_id in match_ids[fast_limit : self.RIOT_MATCH_SAMPLE_SIZE]:
                try:
                    sample = self._sample_for_match(match_id, api_puuid, route, api_key)
                except (RiotApiError, URLError, TimeoutError, OSError):
                    logging.debug("Deep Riot match fallback failed", exc_info=True)
                    continue
                if sample is not None:
                    samples.append(sample)

        analysis = self._analyse_samples_smart(samples, champion)
        inferred_role = current_role or str(analysis.get("main_role", "") or "")
        role_status = self._role_status(
            inferred_role,
            analysis,
            current_role_confirmed=bool(current_role),
            assignment_confidence="high" if current_role else "low",
        )

        local_percentiles = self._baseline_store.percentiles(inferred_role, analysis)
        tags = self._build_tags(
            ranked,
            analysis,
            inferred_role,
            mastery,
            local_percentiles,
        )
        if role_status["role_state"] == "off_role":
            tags.insert(0, self._role_tag(inferred_role, analysis, role_status))

        payload = self._compose_profile_payload(
            state="ready",
            puuid=api_puuid or local_player_id,
            current_role=current_role,
            inferred_role=inferred_role,
            summoner_profile=summoner_profile,
            ranked=ranked,
            mastery=mastery,
            analysis=analysis,
            role_status=role_status,
            match_ids=match_ids,
            tags=tags,
            local_percentiles=local_percentiles,
        )
        payload.update(
            {
                **identity_fields,
                "profile_source": profile_source,
                "rank_source": rank_source,
                "history_source": history_source,
                "mastery_source": mastery_source,
                "analysis_target_games": analysis_target_games,
                "analysis_stage": "final",
                "history_cache_state": history_cache_state,
            }
        )
        self._player_cache[cache_key] = _CacheEntry(now, payload)
        self._profile_disk_cache.save(profile_identity, champion, payload)
        report("ready", payload)
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
        role_status: dict[str, Any],
        match_ids: list[str],
        tags: list[dict[str, Any]],
        local_percentiles: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "profile_schema": 30,
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

    @staticmethod
    def _empty_ranked_entry(rank_state: str = "unranked") -> dict[str, Any]:
        state = str(rank_state or "unranked")
        explicit_unranked = state == "unranked"
        return {
            "rank": "Unranked" if explicit_unranked else "Rank unavailable",
            "tier": "UNRANKED" if explicit_unranked else "UNAVAILABLE",
            "division": "",
            "lp": 0,
            "wins": 0,
            "losses": 0,
            "games": 0,
            "win_rate": None,
            "rank_state": state,
            "ranked_queue": "RANKED_SOLO_5x5",
        }

    @staticmethod
    def _empty_mastery() -> dict[str, Any]:
        return {
            "mastery_available": False,
            "mastery_level": 0,
            "mastery_points": 0,
            "mastery_rank": None,
            "mastery_last_play_days": None,
            "mastery_total_points": 0,
            "mastery_champions": 0,
            "top_masteries": [],
        }

    def _lcu_summoner_profile(self, puuid: str) -> dict[str, Any] | None:
        if not puuid:
            return None
        key = str(puuid).casefold()
        cached, age, fresh = self._cache_lookup(
            self._lcu_summoner_cache,
            key,
            self.SUMMONER_CACHE_SECONDS,
        )
        if cached is not None and fresh:
            cached["_cache_state"] = "fresh_cache"
            cached["_cache_age_seconds"] = round(age, 1)
            return cached

        payload = self._lcu.get_json_optional(
            f"/lol-summoner/v2/summoners/puuid/{quote(puuid, safe='')}",
            None,
        )
        if isinstance(payload, dict) and payload:
            level = payload.get("summonerLevel", payload.get("level"))
            icon = payload.get("profileIconId")
            if level is not None or icon is not None:
                result = {
                    "account_level": int(level or 0) if level is not None else None,
                    "profile_icon_id": int(icon or 0) if icon is not None else None,
                }
                self._cache_store(self._lcu_summoner_cache, key, result)
                result["_cache_state"] = "live"
                return result

        if cached is not None:
            cached["_cache_state"] = "stale"
            cached["_cache_age_seconds"] = round(age, 1)
            return cached
        return None

    def _lcu_ranked_entry(self, puuid: str) -> dict[str, Any] | None:
        if not puuid:
            return None
        key = str(puuid).casefold()
        cached, age, fresh = self._cache_lookup(
            self._lcu_rank_cache,
            key,
            self.RANK_CACHE_SECONDS,
        )
        if cached is not None and fresh:
            cached["_cache_state"] = "fresh_cache"
            cached["_cache_age_seconds"] = round(age, 1)
            return cached

        payload = self._lcu.get_json_optional(
            f"/lol-ranked/v1/ranked-stats/{quote(puuid, safe='')}",
            None,
        )
        if isinstance(payload, dict):
            solo: dict[str, Any] | None = None
            queues = payload.get("queues")
            if isinstance(queues, list):
                solo = next(
                    (
                        q for q in queues
                        if isinstance(q, dict)
                        and str(q.get("queueType", "")) == "RANKED_SOLO_5x5"
                    ),
                    None,
                )
            if solo is None:
                queue_map = payload.get("queueMap")
                if isinstance(queue_map, dict):
                    candidate = queue_map.get("RANKED_SOLO_5x5")
                    solo = candidate if isinstance(candidate, dict) else None

            if solo is None:
                result = self._empty_ranked_entry("unranked")
            else:
                tier = str(solo.get("tier", "UNRANKED") or "UNRANKED").upper()
                if tier in {"NONE", "NA", ""}:
                    tier = "UNRANKED"
                division = str(
                    solo.get("division", "")
                    or solo.get("rank", "")
                    or ""
                ).upper()
                lp = int(solo.get("leaguePoints", 0) or solo.get("lp", 0) or 0)
                wins = int(solo.get("wins", 0) or 0)
                losses = int(solo.get("losses", 0) or 0)
                games = wins + losses
                result = {
                    "rank": self._format_rank(tier, division, lp),
                    "tier": tier,
                    "division": division,
                    "lp": lp,
                    "wins": wins,
                    "losses": losses,
                    "games": games,
                    "win_rate": round((wins / games) * 100.0, 1) if games else None,
                    "rank_state": "ready" if tier != "UNRANKED" else "unranked",
                    "ranked_queue": "RANKED_SOLO_5x5",
                }
            self._cache_store(self._lcu_rank_cache, key, result)
            result["_cache_state"] = "live"
            return result

        if cached is not None:
            cached["_cache_state"] = "stale"
            cached["_cache_age_seconds"] = round(age, 1)
            return cached
        return None

    def _lcu_recent_ranked_history(
        self,
        puuid: str,
        count: int,
    ) -> tuple[list[dict[str, Any]], list[str], str] | None:
        """Return up to ``count`` Solo/Duo samples from one local LCU request.

        Results are cached independently for five minutes. If the local endpoint
        temporarily fails, a stale in-memory result is preferred over a slow public
        API fallback and over erasing already-rendered player data.
        """
        if not puuid:
            return None
        key = str(puuid).casefold()
        cached, age, fresh = self._cache_lookup(
            self._lcu_history_cache,
            key,
            self.HISTORY_CACHE_SECONDS,
        )
        if cached is not None and fresh:
            return (
                list(cached.get("samples", ())),
                list(cached.get("match_ids", ())),
                "fresh_cache",
            )

        endpoint = (
            f"/lol-match-history/v1/products/lol/{quote(puuid, safe='')}/matches"
            f"?begIndex=0&endIndex={max(30, int(count) * 3)}"
        )
        payload = self._lcu.get_json_optional(endpoint, None)
        if not isinstance(payload, dict):
            payload = self._lcu.get_json_optional(
                f"/lol-match-history/v1/products/lol/{quote(puuid, safe='')}/matches",
                None,
            )
        if not isinstance(payload, dict):
            if cached is not None:
                return (
                    list(cached.get("samples", ())),
                    list(cached.get("match_ids", ())),
                    "stale",
                )
            return None

        games_value = payload.get("games", [])
        games = games_value.get("games", []) if isinstance(games_value, dict) else games_value
        if not isinstance(games, list):
            if cached is not None:
                return (
                    list(cached.get("samples", ())),
                    list(cached.get("match_ids", ())),
                    "stale",
                )
            return None

        samples: list[dict[str, Any]] = []
        match_ids: list[str] = []
        for game in games:
            if not isinstance(game, dict) or int(game.get("queueId", 0) or 0) != 420:
                continue
            sample = self._lcu_game_to_sample(game, puuid)
            if sample is None:
                continue
            match_id = str(game.get("gameId", "") or sample.get("match_id", ""))
            if match_id:
                match_ids.append(match_id)
            samples.append(sample)
            if len(samples) >= int(count):
                break

        payload_cache = {"samples": samples, "match_ids": match_ids}
        self._cache_store(self._lcu_history_cache, key, payload_cache)
        return samples, match_ids, "live"

    def _lcu_game_to_sample(
        self,
        game: dict[str, Any],
        puuid: str,
    ) -> dict[str, Any] | None:
        participants_raw = game.get("participants", [])
        if not isinstance(participants_raw, list) or not participants_raw:
            return None

        identities: dict[int, dict[str, Any]] = {}
        identities_raw = game.get("participantIdentities", [])
        if isinstance(identities_raw, list):
            for identity in identities_raw:
                if not isinstance(identity, dict):
                    continue
                participant_id = int(identity.get("participantId", 0) or 0)
                player = identity.get("player", {})
                if participant_id and isinstance(player, dict):
                    identities[participant_id] = player

        duration = int(game.get("gameDuration", 0) or 0)
        if duration > 100_000:
            duration //= 1000
        start_ms = int(game.get("gameCreation", 0) or game.get("gameStartTimestamp", 0) or 0)
        if 0 < start_ms < 10_000_000_000:
            start_ms *= 1000

        flattened: list[dict[str, Any]] = []
        target: dict[str, Any] | None = None
        for raw in participants_raw:
            if not isinstance(raw, dict):
                continue
            participant_id = int(raw.get("participantId", 0) or 0)
            identity = identities.get(participant_id, {})
            raw_puuid = str(
                raw.get("puuid", "")
                or identity.get("puuid", "")
                or ""
            )
            stats = raw.get("stats", {})
            if not isinstance(stats, dict):
                stats = {}
            timeline = raw.get("timeline", {})
            if not isinstance(timeline, dict):
                timeline = {}

            champion_id = int(raw.get("championId", 0) or 0)
            cs = int(stats.get("totalMinionsKilled", 0) or 0) + int(
                stats.get("neutralMinionsKilled", 0) or 0
            )
            vision = int(stats.get("visionScore", 0) or 0)
            role = self._lcu_history_position(
                raw,
                timeline,
                cs,
                int(stats.get("neutralMinionsKilled", 0) or 0),
                vision,
                duration,
            )
            participant = {
                "puuid": raw_puuid,
                "participantId": participant_id,
                "teamId": int(raw.get("teamId", 0) or 0),
                "championId": champion_id,
                "championName": self._champion_catalog.champion_name(champion_id),
                "win": self._lcu_bool(stats.get("win", False)),
                "kills": int(stats.get("kills", 0) or 0),
                "deaths": int(stats.get("deaths", 0) or 0),
                "assists": int(stats.get("assists", 0) or 0),
                "totalMinionsKilled": int(stats.get("totalMinionsKilled", 0) or 0),
                "neutralMinionsKilled": int(stats.get("neutralMinionsKilled", 0) or 0),
                "goldEarned": int(stats.get("goldEarned", 0) or 0),
                "totalDamageDealtToChampions": int(stats.get("totalDamageDealtToChampions", 0) or 0),
                "totalDamageTaken": int(stats.get("totalDamageTaken", 0) or 0),
                "visionScore": vision,
                "detectorWardsPlaced": int(
                    stats.get("visionWardsBoughtInGame", 0)
                    or stats.get("detectorWardsPlaced", 0)
                    or 0
                ),
                "turretTakedowns": int(
                    stats.get("turretKills", 0)
                    or stats.get("turretTakedowns", 0)
                    or 0
                ),
                "objectivesStolen": int(stats.get("objectivesStolen", 0) or 0),
                "firstBloodKill": self._lcu_bool(stats.get("firstBloodKill", False)),
                "firstBloodAssist": self._lcu_bool(stats.get("firstBloodAssist", False)),
                "teamPosition": role,
                "individualPosition": role,
                "challenges": {},
            }
            flattened.append(participant)
            if raw_puuid and raw_puuid == puuid:
                target = participant

        # The per-player history endpoint often returns only that player's compact
        # participant row, so the first row is the correct fallback.
        if target is None and flattened:
            target = flattened[0]
            target["puuid"] = puuid
        if target is None:
            return None

        info = {
            "queueId": int(game.get("queueId", 0) or 0),
            "gameDuration": duration,
            "gameStartTimestamp": start_ms,
            "gameEndTimestamp": start_ms + duration * 1000 if start_ms else 0,
            "participants": flattened,
            "teamStatsComplete": len(flattened) >= 10,
        }
        return {
            "match_id": str(game.get("gameId", "") or ""),
            "participant": target,
            "info": info,
            "source": "lcu",
        }

    @staticmethod
    def _lcu_bool(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().casefold() in {"true", "win", "victory", "1"}
        return bool(value)

    @staticmethod
    def _lcu_history_position(
        participant: dict[str, Any],
        timeline: dict[str, Any],
        cs: int,
        neutral_cs: int,
        vision: int,
        duration: int,
    ) -> str:
        spell_ids = {
            int(participant.get("spell1Id", 0) or 0),
            int(participant.get("spell2Id", 0) or 0),
        }
        if 11 in spell_ids:
            return "JUNGLE"
        lane = str(timeline.get("lane", "") or "").upper()
        role = str(timeline.get("role", "") or "").upper()
        minutes = max(float(duration) / 60.0, 1.0)
        cs_min = float(cs) / minutes
        neutral_min = float(neutral_cs) / minutes
        if lane in {"MIDDLE", "MID"}:
            return "MIDDLE"
        if lane == "TOP":
            return "TOP"
        if lane in {"BOTTOM", "BOT"}:
            if "SUPPORT" in role:
                return "UTILITY"
            if "CARRY" in role:
                return "BOTTOM"
            if vision / minutes > 1.5 or cs_min < 2.5:
                return "UTILITY"
            return "BOTTOM"
        if lane == "JUNGLE":
            # Compact LCU history sometimes omits spell IDs. Neutral farm is a
            # safer fallback than treating every missing-Smite jungle row as Top.
            if neutral_cs >= 35 or neutral_min >= 2.0:
                return "JUNGLE"
            return "TOP"
        if neutral_cs >= 45 or neutral_min >= 2.4:
            return "JUNGLE"
        return ""

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
            "mastery_available": True,
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

    def _cached_champion_mastery(
        self,
        puuid: str,
        champion_name: str,
        platform: str,
        api_key: str,
    ) -> tuple[dict[str, Any], str]:
        key = (platform, str(puuid).casefold(), normalize_name(champion_name))
        cached, age, fresh = self._cache_lookup(
            self._mastery_cache,
            key,
            self.MASTERY_CACHE_SECONDS,
        )
        if cached is not None and fresh:
            cached["mastery_cache_age_seconds"] = round(age, 1)
            return cached, "riot_cache"
        try:
            payload = self._champion_mastery(
                puuid,
                champion_name,
                platform,
                api_key,
            )
            self._cache_store(self._mastery_cache, key, payload)
            return payload, "riot"
        except (RiotApiError, URLError, TimeoutError, OSError):
            logging.debug("Champion mastery unavailable", exc_info=True)
            if cached is not None:
                cached["mastery_cache_age_seconds"] = round(age, 1)
                return cached, "riot_stale_cache"
            return self._empty_mastery(), "unavailable"

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

    def _ranked_entry(self, puuid: str, platform: str, api_key: str) -> dict[str, Any]:
        url = (
            f"https://{platform}.api.riotgames.com/lol/league/v4/entries/by-puuid/"
            f"{quote(puuid, safe='')}"
        )
        entries = self._riot_json(url, api_key)
        if not isinstance(entries, list):
            raise RiotApiError(502, "Riot rank response was not a list")

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
            return self._empty_ranked_entry("unranked")

        tier = str(solo.get("tier", "UNRANKED") or "UNRANKED").upper()
        division = str(solo.get("rank", "") or "").upper()
        lp = int(solo.get("leaguePoints", 0) or 0)
        wins = int(solo.get("wins", 0) or 0)
        losses = int(solo.get("losses", 0) or 0)
        games = wins + losses
        return {
            "rank": self._format_rank(tier, division, lp),
            "tier": tier,
            "division": division,
            "lp": lp,
            "wins": wins,
            "losses": losses,
            "games": games,
            "win_rate": round((wins / games) * 100.0, 1) if games else None,
            "rank_state": "ready" if tier != "UNRANKED" else "unranked",
            "ranked_queue": "RANKED_SOLO_5x5",
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
            "team_context_games": 0,
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
        kp_samples = damage_share_samples = 0
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

            team_stats_complete = bool(info.get("teamStatsComplete", True))
            if team_stats_complete and team_kills > 0:
                kp += (
                    (player_kills + player_assists) / team_kills
                ) * 100.0
                kp_samples += 1
            player_damage = float(
                p.get("totalDamageDealtToChampions", 0) or 0
            )
            damage_min += player_damage / minutes
            if team_stats_complete and team_damage > 0:
                team_damage_share += (player_damage / team_damage) * 100.0
                damage_share_samples += 1

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
            "avg_kp": round(kp / kp_samples, 1) if kp_samples else 0.0,
            "team_context_games": kp_samples,
            "avg_damage_min": round(damage_min / count, 0),
            "avg_team_damage_share": (
                round(team_damage_share / damage_share_samples, 1)
                if damage_share_samples
                else 0.0
            ),
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


    @classmethod
    def _analyse_samples_smart(
        cls,
        samples: list[dict[str, Any]],
        current_champion: str,
    ) -> dict[str, Any]:
        """Use separate windows for form, performance and stable role evidence."""
        ordered = list(samples[: cls.LCU_MATCH_SAMPLE_SIZE])
        full = cls._analyse_samples(ordered, current_champion)
        performance = cls._analyse_samples(
            ordered[: cls.PERFORMANCE_SAMPLE_SIZE],
            current_champion,
        )
        form = cls._analyse_samples(ordered[: cls.FAST_SAMPLE_SIZE], current_champion)

        result = dict(full)
        performance_keys = {
            "avg_kills", "avg_deaths", "avg_assists", "avg_kda",
            "kda_volatility", "death_volatility", "avg_cs_min", "avg_gold_min",
            "avg_kp", "team_context_games", "avg_damage_min",
            "avg_team_damage_share", "avg_damage_taken_min", "avg_vision_min",
            "avg_control_wards", "avg_turrets", "avg_solo_kills",
            "avg_objectives_stolen", "avg_objective_participation",
            "first_blood_rate", "early_advantage_rate", "high_death_game_rate",
            "low_death_game_rate", "champion_games", "champion_wins",
            "champion_win_rate", "champion_share", "champion_role_counts",
            "avg_game_minutes", "late_game_games", "late_game_win_rate",
            "short_game_games", "short_game_win_rate",
        }
        for key in performance_keys:
            if key in performance:
                result[key] = performance[key]

        form_keys = {
            "recent_wins", "recent_win_rate", "streak_type", "streak_count",
            "games_today", "first_ranked_today", "last_ranked_minutes_ago",
            "session_games", "session_span_minutes", "days_since_last_ranked",
        }
        for key in form_keys:
            if key in form:
                result[key] = form[key]

        # Exponential weighting keeps the newest result more important without
        # throwing away the clear five-game form window.
        weights = [0.78 ** index for index in range(len(ordered[:10]))]
        if weights:
            weighted_wins = sum(
                weight * (1.0 if bool(sample.get("participant", {}).get("win", False)) else 0.0)
                for weight, sample in zip(weights, ordered[:10])
            )
            result["weighted_recent_win_rate"] = round(
                weighted_wins / sum(weights) * 100.0,
                1,
            )
        else:
            result["weighted_recent_win_rate"] = None

        result["sample_games"] = len(ordered)
        result["form_sample_games"] = int(form.get("sample_games", 0) or 0)
        result["performance_sample_games"] = int(
            performance.get("sample_games", 0) or 0
        )
        result["role_sample_games"] = int(full.get("sample_games", 0) or 0)
        result["analysis_windows"] = {
            "form": result["form_sample_games"],
            "performance": result["performance_sample_games"],
            "role": result["role_sample_games"],
        }
        return result

    @staticmethod
    def _role_status(
        current_role: str,
        analysis: dict[str, Any],
        *,
        current_role_confirmed: bool = True,
        assignment_confidence: str = "high",
    ) -> dict[str, Any]:
        sample_games = int(analysis.get("role_sample_games", analysis.get("sample_games", 0)) or 0)
        current_role = str(current_role or "").upper()
        main_role = str(analysis.get("main_role", "") or "").upper()
        secondary_role = str(analysis.get("secondary_role", "") or "").upper()
        role_counts = dict(analysis.get("role_counts", {}) or {})
        main_count = int(role_counts.get(main_role, 0) or 0)
        current_count = int(role_counts.get(current_role, 0) or 0)
        role_share = main_count / sample_games if main_role and sample_games else 0.0
        current_share = current_count / sample_games if current_role and sample_games else 0.0
        assignment_confidence = str(assignment_confidence or "low").casefold()

        state = "unclear"
        label = "ROLE UNCLEAR"
        tone = "neutral"

        enough_evidence = sample_games >= 10 and role_share >= 0.45
        current_is_reliable = current_role_confirmed and assignment_confidence in {"high", "medium"}

        if enough_evidence and current_is_reliable and current_role and main_role:
            if current_role == main_role:
                state = "main"
                label = "MAIN ROLE"
                tone = "positive"
            elif current_role == secondary_role and current_share >= 0.20:
                state = "secondary"
                label = "SECONDARY ROLE"
                tone = "neutral"
            elif (
                assignment_confidence == "high"
                and role_share >= 0.55
                and current_share <= 0.20
                and main_count - current_count >= 4
            ):
                state = "off_role"
                label = "OFFROLE"
                tone = "negative" if current_share <= 0.10 else "warning"
            else:
                state = "flex"
                label = "FLEX ROLE"
                tone = "neutral"

        return {
            "role_state": state,
            "role_status_label": label,
            "role_status_tone": tone,
            "current_role_share": round(current_share, 2),
            "role_evidence_games": sample_games,
            "role_main_count": main_count,
            "role_current_count": current_count,
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
            # receive the best unique subset without inventing duplicate roles.
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

                role_status = self._role_status(
                    assigned_role,
                    profile,
                    current_role_confirmed=bool(current_role) or has_smite,
                    assignment_confidence=assignment_confidence,
                )
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
                    local_percentiles,
                )
                if role_status["role_state"] == "off_role":
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
            # Compact LCU history can omit the other nine participants. Do not
            # teach the local baseline store fake 0% KP/damage-share values.
            if (
                str(profile.get("history_source", "")) == "lcu"
                and int(profile.get("team_context_games", 0) or 0) < 3
            ):
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
        mastery: dict[str, Any],
        local_percentiles: dict[str, Any],
    ) -> list[dict[str, Any]]:
        sample = int(analysis.get("sample_games", 0) or 0)
        candidates: list[dict[str, Any]] = []
        candidates.extend(session_tags(analysis))
        if bool(mastery.get("mastery_available", True)):
            candidates.extend(champion_intelligence_tags(analysis, mastery, role))

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
        mastery_available = bool(mastery.get("mastery_available", True))
        mastery_level = int(mastery.get("mastery_level", 0) or 0)
        mastery_points = int(mastery.get("mastery_points", 0) or 0)
        mastery_rank = mastery.get("mastery_rank")
        mastery_days = mastery.get("mastery_last_play_days")

        mastery_detail = (
            f"Mastery {mastery_level} · {mastery_points:,} points"
            if mastery_available and mastery_points
            else (
                "No meaningful mastery record was returned"
                if mastery_available
                else "Mastery data unavailable"
            )
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
            mastery_available
            and sample >= 6
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
                    + (f" · {mastery_detail}" if mastery_available else "")
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
        team_context_games = int(analysis.get("team_context_games", 0) or 0)
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

        if team_context_games >= 3 and avg_kp >= 62 and damage_share >= 24:
            add(
                "HIGH IMPACT",
                "positive",
                f"{avg_kp:.0f}% kill participation · {damage_share:.0f}% team damage share",
                91,
                "impact",
            )
        elif team_context_games >= 3 and damage_share >= 29:
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
            team_context_games >= 3
            and role in {"TOP", "MIDDLE"}
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

        if (
            team_context_games >= 3
            and role == "TOP"
            and avg_kp <= 45
            and avg_deaths <= 5.5
        ):
            add(
                "WEAK-SIDE PLAYER",
                "neutral",
                f"Low {avg_kp:.0f}% kill participation while keeping deaths controlled",
                70,
                "map_style",
            )

        if (
            team_context_games >= 3
            and role in {"MIDDLE", "UTILITY"}
            and avg_kp >= 67
        ):
            add(
                "ROAMING",
                "positive",
                f"Very high {avg_kp:.0f}% kill participation for the role",
                78,
                "roam",
            )

        if role == "UTILITY" and sample >= 8 and first_blood_rate >= 30 and avg_kp >= 62:
            add(
                "AGGRESSIVE SUPPORT",
                "warning",
                "High first-blood involvement and strong recent kill participation",
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
