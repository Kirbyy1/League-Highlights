from __future__ import annotations

import base64
import json
import logging
import shutil
import ssl
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.services.lcu_failure_diagnostics import (
    ActiveRequestTracker,
    diagnose_lcu_failure,
)

from app.services.live_match_diagnostics import (
    LiveMatchDiagnostics,
    active_diagnostics,
    record_lcu_request,
    register_diagnostics,
    summarize_response,
)

from app.services.live_match_performance_helpers import (
    ACTIVE_GAME_PHASES,
    ranked_record_credibility,
    roster_quality,
    should_defer_placeholder_roster,
    stable_match_key,
)


LOGGER = logging.getLogger(__name__)
LIVE_MATCH_SPEED_BUILD = "V14-BENCHMARK-METRICS"

# The recorder already requests these Live Client resources. Live Match reuses the
# most recent snapshots instead of downloading the same JSON a second time.
_LIVE_SNAPSHOT_LOCK = threading.RLock()
_LIVE_SNAPSHOTS: dict[str, tuple[float, Any]] = {}
_SCOUTS: set[Any] = set()

# Shared across every LeagueClientConnection instance. This prevents the Live
# Match scout and lifecycle monitor from downloading the same gameflow/session
# JSON simultaneously. Only successful short-lived responses are cached.
_LCU_SHARED_LOCK = threading.RLock()
_LCU_SHARED_CACHE: dict[str, tuple[float, Any]] = {}
_LCU_SHARED_FAILURES: dict[str, tuple[float, str]] = {}
_LCU_SHARED_INFLIGHT: dict[str, threading.Event] = {}
_SNAPSHOT_WAIT_LOG_AT = 0.0
_LCU_REQUEST_TRACKER = ActiveRequestTracker()
_FAILURE_PROBE_LOCK = threading.Lock()
_FAILURE_PROBE_STATE_LOCK = threading.RLock()
_LAST_FAILURE_PROBE_AT = 0.0
_FAILURE_PROBE_MIN_INTERVAL_SECONDS = 0.75


def _benchmark_metric(scout: Any, key: str, value: float = 1.0, *, maximum: bool = False) -> None:
    metrics = getattr(scout, "_live_match_benchmark_metrics", None)
    if not isinstance(metrics, dict):
        return
    lock = getattr(scout, "_live_match_benchmark_metrics_lock", None)
    if lock is None:
        lock = threading.RLock()
        scout._live_match_benchmark_metrics_lock = lock
    with lock:
        current = float(metrics.get(key, 0.0) or 0.0)
        metrics[key] = max(current, float(value)) if maximum else current + float(value)


def _snapshot_key(endpoint: str) -> str:
    return "/" + str(endpoint or "").strip().lstrip("/")


def _remember_snapshot(endpoint: str, payload: Any) -> None:
    key = _snapshot_key(endpoint)
    if key not in {"/playerlist", "/activeplayer", "/gamestats", "/eventdata"}:
        return
    with _LIVE_SNAPSHOT_LOCK:
        _LIVE_SNAPSHOTS[key] = (time.monotonic(), payload)


def _recent_snapshot(endpoint: str, max_age: float = 2.5) -> Any | None:
    key = _snapshot_key(endpoint)
    with _LIVE_SNAPSHOT_LOCK:
        entry = _LIVE_SNAPSHOTS.get(key)
    if entry is None:
        return None
    created_at, payload = entry
    if time.monotonic() - created_at > float(max_age):
        return None
    return payload


def _snapshot_roster_signature() -> str:
    players = _recent_snapshot("/playerlist", 3.0)
    if not isinstance(players, list) or not players:
        return ""

    parts: list[str] = []
    for raw in players:
        if not isinstance(raw, dict):
            continue
        team = str(raw.get("team", "") or "").upper()
        riot_id = str(
            raw.get("riotId", "")
            or raw.get("riotIdGameName", "")
            or raw.get("summonerName", "")
            or ""
        ).strip().casefold()
        champion = str(raw.get("championName", "") or "").strip().casefold()
        parts.append(f"{team}:{riot_id}:{champion}")
    return "|".join(sorted(parts))


class _NoPersistentProfileCache:
    """Drop-in replacement that never reads or writes player profiles."""

    def load(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def save(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _clear_current_match_state(scout: Any, *, keep_roster: bool = False) -> None:
    """Clear only temporary scouting data; static assets and user features remain."""

    for name in (
        "_player_cache",
        "_lcu_rank_cache",
        "_lcu_history_cache",
        "_lcu_summoner_cache",
        "_mastery_cache",
        "_match_cache",
    ):
        value = getattr(scout, name, None)
        if hasattr(value, "clear"):
            value.clear()

    inflight = getattr(scout, "_match_inflight", None)
    if hasattr(inflight, "clear"):
        inflight.clear()

    scout._last_completed_signature = ""
    if not keep_roster:
        scout._last_roster_signature = ""
        scout._lean_frozen_roster = None
        scout._lean_snapshot_signature = ""
        scout._lean_match_key = ""
        scout._lean_placeholder_seen_at = 0.0
        scout._lean_placeholder_key = ""
        scout._lean_roster_logged_key = ""


def _queue_candidates(payload: Any) -> list[dict[str, Any]]:
    """Extract ranked queue records from the LCU's several historical schemas."""

    found: list[dict[str, Any]] = []
    seen: set[int] = set()

    def visit(value: Any, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(value, list):
            for item in value:
                visit(item, depth + 1)
            return
        if not isinstance(value, dict):
            return

        object_id = id(value)
        if object_id in seen:
            return
        seen.add(object_id)

        queue_name = str(
            value.get("queueType", "")
            or value.get("queue", "")
            or value.get("queueName", "")
            or ""
        ).upper()
        has_rank_fields = any(
            key in value
            for key in (
                "tier",
                "division",
                "leaguePoints",
                "lp",
                "wins",
                "losses",
            )
        )
        if has_rank_fields and queue_name in {
            "RANKED_SOLO_5X5",
            "RANKED_SOLO_5x5",
            "SOLO5V5",
            "RANKED_SOLO",
            "SOLO",
            "",
        }:
            found.append(value)

        for key, child in value.items():
            normalized = str(key).upper()
            if normalized in {
                "RANKED_SOLO_5X5",
                "RANKED_SOLO_5x5",
                "SOLO5V5",
                "SOLO",
            } and isinstance(child, dict):
                item = dict(child)
                item.setdefault("queueType", "RANKED_SOLO_5x5")
                found.append(item)
            elif isinstance(child, (dict, list)):
                visit(child, depth + 1)

    visit(payload)
    return found


def _solo_queue(payload: Any) -> dict[str, Any] | None:
    candidates = _queue_candidates(payload)
    for candidate in candidates:
        queue_name = str(
            candidate.get("queueType", "")
            or candidate.get("queue", "")
            or candidate.get("queueName", "")
            or ""
        ).upper()
        if queue_name in {
            "RANKED_SOLO_5X5",
            "RANKED_SOLO_5x5",
            "SOLO5V5",
            "RANKED_SOLO",
            "SOLO",
        }:
            return candidate
    return candidates[0] if len(candidates) == 1 else None


def install_live_match_speedups() -> None:
    """Install a local-only, one-analysis-per-match Live Match pipeline."""

    from app.services.lcu_game_detector import LeagueClientConnection
    from app.services.live_match_scout import LiveMatchScout
    from app.services.league_events_v2 import LeagueEventMonitorV2

    if getattr(LiveMatchScout, "_speed_patch_installed", False):
        return

    # Five recent Solo/Duo games keep the useful live information while cutting
    # response parsing and tag work substantially.
    LiveMatchScout.FAST_SAMPLE_SIZE = 5
    LiveMatchScout.PERFORMANCE_SAMPLE_SIZE = 5
    LiveMatchScout.LCU_MATCH_SAMPLE_SIZE = 5
    LiveMatchScout.HISTORY_MATCH_ID_COUNT = 5
    LiveMatchScout.RIOT_MATCH_SAMPLE_SIZE = 0
    LiveMatchScout.MAX_CONCURRENT_PLAYERS = 10

    # These are current-match memory lifetimes, not cross-match caches. They are
    # explicitly cleared when a new roster appears and on manual Refresh.
    current_match_lifetime = 8 * 60 * 60
    LiveMatchScout.PLAYER_CACHE_SECONDS = current_match_lifetime
    LiveMatchScout.RANK_CACHE_SECONDS = current_match_lifetime
    LiveMatchScout.HISTORY_CACHE_SECONDS = current_match_lifetime
    LiveMatchScout.SUMMONER_CACHE_SECONDS = current_match_lifetime
    LiveMatchScout.MASTERY_CACHE_SECONDS = current_match_lifetime

    # Completed cycles reuse the recorder snapshot and issue no Live Match LCU
    # calls. The timer remains as a quiet safety check for match transitions.
    LiveMatchScout.READY_POLL_INTERVAL_MS = 60_000

    # ------------------------------------------------------------------
    # Reuse the recorder's existing port-2999 requests.
    # ------------------------------------------------------------------
    original_event_fetch = LeagueEventMonitorV2._fetch_json

    def captured_event_fetch(self: Any, endpoint: str) -> Any:
        payload = original_event_fetch(self, endpoint)
        _remember_snapshot(endpoint, payload)
        return payload

    LeagueEventMonitorV2._fetch_json = captured_event_fetch

    # Live Match never sends its own port-2999 calls. The recorder already polls
    # playerlist/activeplayer/gamestats and publishes successful snapshots here.
    # Returning immediately when a snapshot is not ready removes the repeated
    # two-second connection-refused timeout seen in the diagnostic trace.
    def shared_local_json(endpoint: str) -> Any:
        global _SNAPSHOT_WAIT_LOG_AT
        cached = _recent_snapshot(endpoint, 4.0)
        if cached is not None:
            return cached
        now = time.monotonic()
        if now - _SNAPSHOT_WAIT_LOG_AT >= 3.0:
            _SNAPSHOT_WAIT_LOG_AT = now
            for trace in active_diagnostics():
                trace.event(
                    "live_client_snapshot_wait",
                    endpoint=endpoint,
                    direct_request_sent=False,
                    reason="Recorder snapshot is not ready",
                )
        raise URLError("shared Live Client snapshot is not ready")

    LiveMatchScout._local_json = staticmethod(shared_local_json)

    # ------------------------------------------------------------------
    # Remove persistent player/identity caches while keeping current-match RAM.
    # ------------------------------------------------------------------
    original_scout_init = LiveMatchScout.__init__

    def lean_scout_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_scout_init(self, *args, **kwargs)
        self._profile_disk_cache = _NoPersistentProfileCache()
        self._identity_puuid_cache = {}
        self._lean_frozen_roster = None
        self._lean_snapshot_signature = ""
        self._lean_match_key = ""
        self._lean_placeholder_seen_at = 0.0
        self._lean_placeholder_key = ""
        self._lean_roster_logged_key = ""
        self._lean_request_counts = {"rank": 0, "history": 0}
        self._lean_last_load_seconds = 0.0
        self._live_match_benchmark_metrics = None
        self._live_match_benchmark_metrics_lock = threading.RLock()
        self._live_match_diagnostics = LiveMatchDiagnostics(self.config)
        register_diagnostics(self._live_match_diagnostics)
        self._live_match_diagnostics.event(
            "scout_created",
            build=LIVE_MATCH_SPEED_BUILD,
            persistent_player_cache=False,
            external_api_enabled=False,
            history_target_games=5,
        )
        _SCOUTS.add(self)

        # Remove player cache files created by older versions. Champion catalog,
        # encounter history and user settings are intentionally preserved.
        root = Path(getattr(self, "_live_cache_root", Path()))
        if root:
            for child in (root / "players", root / "matches"):
                try:
                    shutil.rmtree(child, ignore_errors=True)
                except OSError:
                    pass
            identity_path = root / "identity_puuids.json"
            try:
                identity_path.unlink(missing_ok=True)
            except OSError:
                pass

    LiveMatchScout.__init__ = lean_scout_init

    # Keep identities in RAM when useful, but prevent the helper from writing
    # them back to disk. The exact method name varies across project revisions.
    for method_name in (
        "_save_identity_puuid_cache",
        "_persist_identity_puuid_cache",
        "_write_identity_puuid_cache",
    ):
        if hasattr(LiveMatchScout, method_name):
            setattr(LiveMatchScout, method_name, lambda self: None)

    # Preserve extra local identifiers already present in the same gameflow
    # response. The base parser intentionally keeps a compact roster, but rank
    # endpoints can vary between accepting PUUID and summoner ID. Keeping both
    # costs no additional request and fixes empty rank cards on some clients.
    original_gameflow_roster = LiveMatchScout._read_lcu_gameflow_roster

    def enriched_gameflow_roster(
        self: Any,
        session: dict[str, Any],
        *,
        current_summoner: dict[str, Any],
        self_puuid: str,
    ) -> dict[str, Any]:
        roster = original_gameflow_roster(
            self,
            session,
            current_summoner=current_summoner,
            self_puuid=self_puuid,
        )
        game_data = session.get("gameData", {}) if isinstance(session, dict) else {}
        raw_players: list[dict[str, Any]] = []
        if isinstance(game_data, dict):
            for team_key in ("teamOne", "teamTwo"):
                team = game_data.get(team_key, [])
                if isinstance(team, list):
                    raw_players.extend(item for item in team if isinstance(item, dict))

        by_local_id: dict[str, dict[str, Any]] = {}
        by_riot_id: dict[str, dict[str, Any]] = {}
        for raw in raw_players:
            local_id = str(raw.get("puuid", "") or raw.get("playerUuid", "") or "").strip().casefold()
            game_name = str(raw.get("riotIdGameName", "") or raw.get("gameName", "") or "").strip()
            tag_line = str(raw.get("riotIdTagLine", "") or raw.get("tagLine", "") or "").strip()
            riot_id = str(raw.get("riotId", "") or "").strip()
            if not riot_id and game_name and tag_line:
                riot_id = f"{game_name}#{tag_line}"
            details = {
                "summoner_id": str(raw.get("summonerId", "") or raw.get("id", "") or "").strip(),
                "account_id": str(raw.get("accountId", "") or "").strip(),
                "puuid": str(raw.get("puuid", "") or "").strip(),
            }
            if local_id:
                by_local_id[local_id] = details
            if riot_id:
                by_riot_id[riot_id.casefold()] = details

        for player in roster.get("players", ()) if isinstance(roster, dict) else ():
            if not isinstance(player, dict):
                continue
            local_id = str(player.get("lcu_player_id", "") or player.get("puuid", "") or "").strip().casefold()
            riot_id = str(player.get("riot_id", "") or "").strip().casefold()
            details = by_local_id.get(local_id) or by_riot_id.get(riot_id) or {}
            if details.get("summoner_id"):
                player["summoner_id"] = details["summoner_id"]
            if details.get("account_id"):
                player["account_id"] = details["account_id"]
        return roster

    LiveMatchScout._read_lcu_gameflow_roster = enriched_gameflow_roster

    # ------------------------------------------------------------------
    # Freeze a complete roster and clear temporary data on the next match.
    # External Riot/Spectator calls are disabled by always passing an empty key.
    # ------------------------------------------------------------------
    original_discover_roster = LiveMatchScout._discover_roster

    def lean_discover_roster(self: Any, platform: str, _api_key: str) -> dict[str, Any]:
        roster = original_discover_roster(self, platform, "")
        if not isinstance(roster, dict):
            roster = {}
        phase = str(roster.get("gameflow_phase", "") or "")
        active_phase = phase in ACTIVE_GAME_PHASES
        frozen = getattr(self, "_lean_frozen_roster", None)

        if not roster.get("players"):
            # A transient session/snapshot miss must not erase a complete match or
            # trigger another ten-player analysis.
            if frozen and active_phase:
                return frozen
            if not active_phase:
                _clear_current_match_state(self, keep_roster=False)
            return roster

        match_key = stable_match_key(roster) or self._stable_roster_signature(roster)
        current_key = str(getattr(self, "_lean_match_key", "") or "")
        quality = roster_quality(roster)
        snapshot_ready = isinstance(_recent_snapshot("/playerlist", 4.0), list)

        if active_phase and quality["players"] >= 10 and quality["real_names"] < 8:
            if self._lean_placeholder_key != match_key:
                self._lean_placeholder_key = match_key
                self._lean_placeholder_seen_at = time.monotonic()
            if should_defer_placeholder_roster(
                roster,
                first_seen_at=self._lean_placeholder_seen_at,
                now=time.monotonic(),
                shared_playerlist_ready=snapshot_ready,
                grace_seconds=8.0,
            ):
                diagnostics = getattr(self, "_live_match_diagnostics", None)
                if diagnostics is not None:
                    diagnostics.event(
                        "placeholder_roster_deferred",
                        match_key=match_key,
                        roster_quality=quality,
                        grace_seconds=8.0,
                    )
                return {
                    "players": [], "allies": [], "enemies": [],
                    "active_team": roster.get("active_team", ""),
                    "game_started_at": roster.get("game_started_at", 0),
                    "game_id": roster.get("game_id", ""),
                    "queue_id": roster.get("queue_id", 0),
                    "gameflow_phase": phase,
                    "roster_source": "waiting_for_shared_playerlist",
                }
        else:
            self._lean_placeholder_seen_at = 0.0
            self._lean_placeholder_key = ""

        if current_key and match_key and match_key != current_key:
            _clear_current_match_state(self, keep_roster=False)
            frozen = None

        self._lean_match_key = match_key
        self._lean_frozen_roster = roster
        self._lean_snapshot_signature = _snapshot_roster_signature()

        diagnostics = getattr(self, "_live_match_diagnostics", None)
        if diagnostics is not None and match_key != self._lean_roster_logged_key:
            self._lean_roster_logged_key = match_key
            diagnostics.event(
                "roster_discovered",
                match_key=match_key,
                roster_source=roster.get("roster_source", ""),
                gameflow_phase=phase,
                roster_quality=quality,
                roster=roster,
            )
        return roster

    LiveMatchScout._discover_roster = lean_discover_roster

    # Source hand-offs (gameflow placeholders -> real playerlist names) must keep
    # the same match identity. Game ID/start time + team/champion composition is
    # stable; Riot IDs are intentionally excluded.
    LiveMatchScout._stable_roster_signature = staticmethod(stable_match_key)

    original_refresh = LiveMatchScout.refresh

    def lean_refresh(self: Any, force: bool = False) -> None:
        if bool(getattr(self, "_live_match_benchmark_running", False)):
            return
        if force:
            # Manual Refresh is the one explicit way to request fresh data again
            # for the same match.
            diagnostics = getattr(self, "_live_match_diagnostics", None)
            if diagnostics is not None:
                diagnostics.event("manual_refresh_requested")
            _clear_current_match_state(self, keep_roster=True)
            self._lean_request_counts = {"rank": 0, "history": 0}
        original_refresh(self, force=force)

    LiveMatchScout.refresh = lean_refresh

    def local_only_update_credentials(self: Any) -> None:
        # There are no external credentials in local-only mode. Identity updates
        # must not clear a completed match and re-run all ten profiles.
        self.refresh(force=False)

    LiveMatchScout.update_credentials = local_only_update_credentials

    original_run_cycle = LiveMatchScout._run_cycle

    def diagnostic_run_cycle(self: Any, generation: int, force: bool) -> None:
        diagnostics = getattr(self, "_live_match_diagnostics", None)
        previous_completed = str(getattr(self, "_last_completed_signature", "") or "")
        if diagnostics is not None:
            diagnostics.cycle_started(generation, force=force)
        cycle_started = time.perf_counter()
        error: BaseException | None = None
        try:
            original_run_cycle(self, generation, force)
        except BaseException as exc:
            error = exc
            if diagnostics is not None:
                diagnostics.exception("cycle_unhandled_exception", exc, generation=generation, force=force)
            raise
        finally:
            duration = time.perf_counter() - cycle_started
            completed = str(getattr(self, "_last_completed_signature", "") or "")
            phase = str(getattr(self, "_last_gameflow_phase", "") or "")
            if error is not None:
                outcome = "failed"
            elif completed:
                outcome = "analysis_ready" if completed != previous_completed or force else "analysis_cached"
            elif phase in ACTIVE_GAME_PHASES:
                outcome = "waiting_for_roster"
            elif phase:
                outcome = "waiting_for_match"
            else:
                outcome = "no_client"
            if diagnostics is not None:
                diagnostics.cycle_finished(
                    generation, force=force, outcome=outcome,
                    completed_signature=completed,
                    previous_completed_signature=previous_completed,
                    successful=outcome in {"analysis_ready", "analysis_cached"},
                    match_key=str(getattr(self, "_lean_match_key", "") or ""),
                    lean_request_counts=dict(getattr(self, "_lean_request_counts", {}) or {}),
                )
            if completed and (not previous_completed or force or completed != previous_completed):
                self._lean_last_load_seconds = duration
                rank_count = int(getattr(self, "_lean_request_counts", {}).get("rank", 0))
                history_count = int(getattr(self, "_lean_request_counts", {}).get("history", 0))
                self.status_changed.emit(
                    "ready",
                    f"Live match ready — {duration:.2f}s total · "
                    f"{rank_count} ranks · {history_count} histories · 5-game form",
                )
                if diagnostics is not None:
                    diagnostics.event(
                        "match_analysis_summary",
                        match_key=str(getattr(self, "_lean_match_key", "") or completed),
                        total_duration_ms=round(duration * 1000.0, 2),
                        rank_profiles=rank_count, history_profiles=history_count,
                        roster_quality=roster_quality(getattr(self, "_lean_frozen_roster", {}) or {}),
                    )

    LiveMatchScout._run_cycle = diagnostic_run_cycle

    # ------------------------------------------------------------------
    # Skip nonessential per-player calls and all external Riot fallbacks.
    # ------------------------------------------------------------------
    LiveMatchScout._lcu_summoner_profile = lambda self, _player_id: None
    LiveMatchScout._cached_champion_mastery = (
        lambda self, _puuid, _champion, _platform, _api_key: (
            self._empty_mastery(),
            "disabled_live_match",
        )
    )

    original_player_profile = LiveMatchScout._player_profile
    profile_thread_state = threading.local()

    def local_only_player_profile(
        self: Any,
        player: dict[str, Any],
        platform: str,
        _api_key: str,
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        player_key = str(player.get("player_key", "") or "")
        candidates: list[str] = []
        for key in ("puuid", "lcu_player_id", "summoner_id", "player_key"):
            value = str(player.get(key, "") or "").strip()
            if value and value not in candidates:
                candidates.append(value)
        profile_thread_state.rank_candidates = candidates
        profile_thread_state.player = dict(player)
        diagnostics = getattr(self, "_live_match_diagnostics", None)
        if diagnostics is not None:
            diagnostics.player_started(player_key, player)

        def diagnostic_progress(
            callback_player_key: str,
            stage: str,
            payload: dict[str, Any] | None = None,
        ) -> None:
            if diagnostics is not None:
                diagnostics.player_stage(callback_player_key, stage, payload)
            if progress_callback is not None:
                progress_callback(callback_player_key, stage, payload)

        try:
            result = original_player_profile(
                self,
                player,
                platform,
                "",
                diagnostic_progress,
            )
            if diagnostics is not None:
                diagnostics.player_finished(player_key, result)
            return result
        except BaseException as exc:
            if diagnostics is not None:
                diagnostics.player_finished(player_key, {}, error=exc)
            raise
        finally:
            profile_thread_state.rank_candidates = []
            profile_thread_state.player = {}

    LiveMatchScout._player_profile = local_only_player_profile

    # ------------------------------------------------------------------
    # Reliable rank lookup.
    #
    # The existing version used one endpoint and treated a temporary empty
    # response as missing rank. This version tries the client's cached endpoint,
    # the normal endpoint, multiple locally supplied player identifiers, and
    # parses queueMap/list/nested response variants.
    # ------------------------------------------------------------------
    rank_gate = threading.BoundedSemaphore(4)

    def local_identity_candidates(self: Any, player: dict[str, Any]) -> list[str]:
        game_name = str(player.get("game_name", "") or "").strip()
        riot_id = str(player.get("riot_id", "") or "").strip()
        names: list[str] = []
        for value in (riot_id, game_name):
            if value and value not in names:
                names.append(value)
        resolved: list[str] = []
        for name in names:
            endpoint = f"/lol-summoner/v1/summoners?name={quote(name, safe='')}"
            payload = self._lcu.get_json_optional(endpoint, None)
            if not isinstance(payload, dict) or not payload:
                continue
            for key in ("puuid", "summonerId", "id", "accountId"):
                value = str(payload.get(key, "") or "").strip()
                if value and value not in resolved:
                    resolved.append(value)
            diagnostics = getattr(self, "_live_match_diagnostics", None)
            if diagnostics is not None:
                diagnostics.event(
                    "rank_identity_fallback",
                    requested_name=name,
                    resolved_identifiers=resolved,
                    response=payload,
                )
            if resolved:
                break
        return resolved

    def reliable_ranked_entry(self: Any, player_id: str) -> dict[str, Any] | None:
        candidates: list[str] = []
        for value in [
            player_id,
            *list(getattr(profile_thread_state, "rank_candidates", []) or []),
        ]:
            text = str(value or "").strip()
            if text and text not in candidates:
                candidates.append(text)
        if not candidates:
            return None

        cache_key = candidates[0].casefold()
        cached, age, fresh = self._cache_lookup(
            self._lcu_rank_cache,
            cache_key,
            self.RANK_CACHE_SECONDS,
        )
        if cached is not None and fresh:
            cached["_cache_state"] = "fresh_cache"
            cached["_cache_age_seconds"] = round(age, 1)
            return cached

        payload_seen = False

        def try_candidates(candidate_values: list[str]) -> dict[str, Any] | None:
            nonlocal payload_seen
            for candidate in candidate_values:
                encoded = quote(candidate, safe="")
                endpoints = (
                    f"/lol-ranked/v1/cached-ranked-stats/{encoded}",
                    f"/lol-ranked/v1/ranked-stats/{encoded}",
                )
                for endpoint in endpoints:
                    _benchmark_metric(self, "rank_endpoint_attempts")
                    payload = self._lcu.get_json_optional(endpoint, None)
                    if isinstance(payload, (dict, list)) and payload:
                        _benchmark_metric(self, "rank_endpoint_successes")
                    diagnostics = getattr(self, "_live_match_diagnostics", None)
                    if diagnostics is not None:
                        diagnostics.event(
                            "rank_endpoint_result",
                            candidate=candidate,
                            endpoint=endpoint,
                            payload_type=type(payload).__name__,
                            response_summary=summarize_response(endpoint, payload),
                        )
                    if not isinstance(payload, (dict, list)) or not payload:
                        continue
                    payload_seen = True
                    solo = _solo_queue(payload)
                    if solo is None:
                        continue

                    tier = str(solo.get("tier", "UNRANKED") or "UNRANKED").upper()
                    if tier in {"NONE", "NA", ""}:
                        tier = "UNRANKED"
                    division = str(
                        solo.get("division", "")
                        or solo.get("rank", "")
                        or ""
                    ).upper()
                    if division in {"NA", "NONE"}:
                        division = ""
                    lp = int(
                        solo.get("leaguePoints", 0)
                        or solo.get("lp", 0)
                        or solo.get("points", 0)
                        or 0
                    )
                    raw_wins = int(solo.get("wins", 0) or 0)
                    raw_losses = int(solo.get("losses", 0) or 0)
                    record_available, record_reason = ranked_record_credibility(solo)
                    wins = raw_wins if record_available else 0
                    losses = raw_losses if record_available else 0
                    games = wins + losses
                    result = {
                        "rank": self._format_rank(tier, division, lp),
                        "tier": tier,
                        "division": division,
                        "lp": lp,
                        "wins": wins,
                        "losses": losses,
                        "games": games,
                        "win_rate": round(wins / games * 100.0, 1) if games else None,
                        "rank_state": "ready" if tier != "UNRANKED" else "unranked",
                        "ranked_queue": "RANKED_SOLO_5x5",
                        "ranked_record_available": bool(record_available),
                        "ranked_record_reason": record_reason,
                        "raw_ranked_wins": raw_wins,
                        "raw_ranked_losses": raw_losses,
                    }
                    self._cache_store(self._lcu_rank_cache, cache_key, result)
                    self._lean_request_counts["rank"] = int(
                        self._lean_request_counts.get("rank", 0)
                    ) + 1
                    result["_cache_state"] = "live"
                    return result
            return None

        with rank_gate:
            result = try_candidates(candidates)
            if result is not None:
                return result

            # Only spend an extra local identity request when every identifier
            # already supplied by the roster failed. This preserves the lean path
            # while recovering ranked data on clients exposing internal UUIDs.
            player = dict(getattr(profile_thread_state, "player", {}) or {})
            resolved = local_identity_candidates(self, player) if player else []
            resolved = [value for value in resolved if value not in candidates]
            if resolved:
                _benchmark_metric(self, "rank_identity_fallbacks")
            if resolved:
                result = try_candidates(resolved)
                if result is not None:
                    return result

        # A valid ranked payload with no Solo/Duo entry means genuinely unranked.
        if payload_seen:
            result = self._empty_ranked_entry("unranked")
            self._cache_store(self._lcu_rank_cache, cache_key, result)
            result["_cache_state"] = "live"
            return result

        if cached is not None:
            cached["_cache_state"] = "stale"
            cached["_cache_age_seconds"] = round(age, 1)
            return cached
        return None

    LiveMatchScout._lcu_ranked_entry = reliable_ranked_entry

    # ------------------------------------------------------------------
    # Adaptive five-game history: request 15 raw games first and fetch a second
    # page only when mixed queues leave fewer than five Solo/Duo matches.
    # ------------------------------------------------------------------
    history_gate = threading.BoundedSemaphore(2)

    @staticmethod
    def _history_games(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        raw = payload.get("games", [])
        raw = raw.get("games", []) if isinstance(raw, dict) else raw
        return [game for game in raw if isinstance(game, dict)] if isinstance(raw, list) else []

    def lean_recent_ranked_history(
        self: Any,
        player_id: str,
        count: int,
    ) -> tuple[list[dict[str, Any]], list[str], str] | None:
        wanted = max(1, min(5, int(count or 5)))
        key = str(player_id or "").strip().casefold()
        if not key:
            return None

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

        encoded = quote(str(player_id), safe="")

        def fetch_history_page(begin: int, end: int) -> Any:
            endpoint = (
                f"/lol-match-history/v1/products/lol/{encoded}/matches"
                f"?begIndex={begin}&endIndex={end}"
            )
            diagnostics = getattr(self, "_live_match_diagnostics", None)
            for attempt in range(2):
                gate_started = time.perf_counter()
                history_gate.acquire()
                gate_wait_ms = round((time.perf_counter() - gate_started) * 1000.0, 2)
                _benchmark_metric(self, "history_attempts")
                _benchmark_metric(self, "history_gate_wait_total_ms", gate_wait_ms)
                _benchmark_metric(self, "history_gate_wait_peak_ms", gate_wait_ms, maximum=True)
                if begin > 0:
                    _benchmark_metric(self, "history_second_page_attempts")
                try:
                    if diagnostics is not None:
                        diagnostics.event(
                            "history_request_attempt",
                            player_id=player_id,
                            endpoint=endpoint,
                            page={"begin": begin, "end": end},
                            attempt=attempt + 1,
                            history_gate_wait_ms=gate_wait_ms,
                            request_activity=_LCU_REQUEST_TRACKER.snapshot(),
                        )
                    payload = self._lcu.get_json_optional(endpoint, None)
                finally:
                    history_gate.release()
                if isinstance(payload, dict):
                    _benchmark_metric(self, "history_successes")
                    if diagnostics is not None:
                        diagnostics.event(
                            "history_request_succeeded",
                            player_id=player_id,
                            endpoint=endpoint,
                            page={"begin": begin, "end": end},
                            attempt=attempt + 1,
                            history_gate_wait_ms=gate_wait_ms,
                        )
                    return payload
                if attempt == 0:
                    _benchmark_metric(self, "history_retries")
                else:
                    _benchmark_metric(self, "history_failures")
                if diagnostics is not None:
                    diagnostics.event(
                        "history_retry" if attempt == 0 else "history_failed",
                        player_id=player_id,
                        endpoint=endpoint,
                        page={"begin": begin, "end": end},
                        attempt=attempt + 1,
                        history_gate_wait_ms=gate_wait_ms,
                        request_activity=_LCU_REQUEST_TRACKER.snapshot(),
                    )
                if attempt == 0:
                    time.sleep(0.30)
            return None

        payload = fetch_history_page(0, 15)
        games = _history_games(payload)

        def convert(raw_games: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
                samples: list[dict[str, Any]] = []
                match_ids: list[str] = []
                seen_ids: set[str] = set()
                for game in raw_games:
                    if int(game.get("queueId", 0) or 0) != 420:
                        continue
                    sample = self._lcu_game_to_sample(game, player_id)
                    if sample is None:
                        continue
                    match_id = str(game.get("gameId", "") or sample.get("match_id", ""))
                    if match_id and match_id in seen_ids:
                        continue
                    if match_id:
                        seen_ids.add(match_id)
                        match_ids.append(match_id)
                    samples.append(sample)
                    if len(samples) >= wanted:
                        break
                return samples, match_ids

        samples, match_ids = convert(games)
        if len(samples) < wanted and len(games) >= 15:
            second = fetch_history_page(15, 35)
            second_games = _history_games(second)
            samples, match_ids = convert(games + second_games)

        if not games:
            if cached is not None:
                return (
                    list(cached.get("samples", ())),
                    list(cached.get("match_ids", ())),
                    "stale",
                )
            return None

        payload_cache = {"samples": samples, "match_ids": match_ids}
        self._cache_store(self._lcu_history_cache, key, payload_cache)
        self._lean_request_counts["history"] = int(
            self._lean_request_counts.get("history", 0)
        ) + 1
        return samples, match_ids, "live"

    LiveMatchScout._lcu_recent_ranked_history = lean_recent_ranked_history

    # ------------------------------------------------------------------
    # Reuse one SSL context and preserve credentials after transient timeouts.
    # ------------------------------------------------------------------
    original_lcu_init = LeagueClientConnection.__init__

    def patched_lcu_init(self: Any) -> None:
        original_lcu_init(self)
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self._league_highlights_ssl_context = context

    def shared_ttl(endpoint: str) -> float:
        lowered = endpoint.casefold()
        if "gameflow-phase" in lowered or "/lol-gameflow/v1/session" in lowered:
            return 0.65
        if "current-summoner" in lowered or "region-locale" in lowered:
            return 60.0
        if "champ-select" in lowered:
            return 0.5
        return 0.0

    def schedule_history_failure_probe(
        self: Any,
        endpoint: str,
        error: BaseException,
        credentials: Any,
        active_state: dict[str, int],
    ) -> None:
        global _LAST_FAILURE_PROBE_AT
        diagnostics = active_diagnostics()
        if not diagnostics or credentials is None:
            return

        now = time.monotonic()
        with _FAILURE_PROBE_STATE_LOCK:
            if now - _LAST_FAILURE_PROBE_AT < _FAILURE_PROBE_MIN_INTERVAL_SECONDS:
                for trace in diagnostics:
                    trace.event(
                        "history_failure_probe_skipped",
                        endpoint=endpoint,
                        reason="A probe already ran during this failure wave",
                        active_state=active_state,
                    )
                return
            _LAST_FAILURE_PROBE_AT = now

        if not _FAILURE_PROBE_LOCK.acquire(blocking=False):
            for trace in diagnostics:
                trace.event(
                    "history_failure_probe_skipped",
                    endpoint=endpoint,
                    reason="Another failure probe is still running",
                    active_state=active_state,
                )
            return

        def worker() -> None:
            try:
                probe = diagnose_lcu_failure(
                    port=int(credentials.port),
                    password=str(credentials.password),
                    protocol=str(credentials.protocol or "https"),
                    context=getattr(self, "_league_highlights_ssl_context", None),
                    original_error=error,
                    active_state=active_state,
                )
                for trace in active_diagnostics():
                    trace.event(
                        "history_failure_root_cause_probe",
                        endpoint=endpoint,
                        lcu_port=int(credentials.port),
                        probe=probe,
                    )
            except BaseException as probe_error:
                for trace in active_diagnostics():
                    trace.exception(
                        "history_failure_probe_error",
                        probe_error,
                        endpoint=endpoint,
                    )
            finally:
                _FAILURE_PROBE_LOCK.release()

        threading.Thread(
            target=worker,
            name="LeagueHighlightsLCUFailureProbe",
            daemon=True,
        ).start()

    def faster_get_json(self: Any, endpoint: str) -> Any:
        endpoint = str(endpoint or "")
        ttl = shared_ttl(endpoint)
        now = time.monotonic()
        owner = True
        waiter: threading.Event | None = None

        if ttl > 0:
            with _LCU_SHARED_LOCK:
                cached = _LCU_SHARED_CACHE.get(endpoint)
                if cached and now - cached[0] <= ttl:
                    return cached[1]
                failed = _LCU_SHARED_FAILURES.get(endpoint)
                if failed and now - failed[0] <= min(ttl, 0.75):
                    raise ConnectionError(failed[1])
                waiter = _LCU_SHARED_INFLIGHT.get(endpoint)
                if waiter is None:
                    waiter = threading.Event()
                    _LCU_SHARED_INFLIGHT[endpoint] = waiter
                else:
                    owner = False

        if ttl > 0 and not owner and waiter is not None:
            waiter.wait(timeout=1.65)
            with _LCU_SHARED_LOCK:
                cached = _LCU_SHARED_CACHE.get(endpoint)
                if cached and time.monotonic() - cached[0] <= ttl:
                    return cached[1]
                failed = _LCU_SHARED_FAILURES.get(endpoint)
                if failed and time.monotonic() - failed[0] <= 0.75:
                    raise ConnectionError(failed[1])
            # The owner did not publish a result; continue as a fallback owner.

        request_context: dict[str, Any] = {}

        def perform_request() -> Any:
            credentials = self._get_credentials()
            request_context["credentials"] = credentials
            if credentials is None:
                raise ConnectionError("League Client lockfile was not found")
            token = base64.b64encode(
                f"riot:{credentials.password}".encode("utf-8")
            ).decode("ascii")
            request = Request(
                f"{credentials.protocol}://127.0.0.1:{credentials.port}{endpoint}",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Basic {token}",
                    "User-Agent": "LeagueHighlights/LCULeanV13",
                    "Connection": "keep-alive",
                },
            )
            context = getattr(self, "_league_highlights_ssl_context", None)
            if context is None:
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                self._league_highlights_ssl_context = context
            try:
                with urlopen(request, timeout=1.5, context=context) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw else None
            except HTTPError as exc:
                status = int(getattr(exc, "code", 0) or 0)
                if status in {401, 403}:
                    with self._lock:
                        self._credentials = None
                        self._last_discovery = 0.0
                raise ConnectionError(
                    f"League Client API endpoint is unavailable ({status or 'HTTP'})"
                ) from exc
            except (TimeoutError, URLError, OSError) as exc:
                # Preserve the original exception as __cause__. Diagnostics inspect
                # the complete chain instead of flattening every failure to timeout.
                raise ConnectionError("League Client API transport request failed") from exc

        activity = _LCU_REQUEST_TRACKER.start(endpoint)
        try:
            payload = record_lcu_request(
                endpoint,
                perform_request,
                metadata=activity.as_dict(),
            )
            if ttl > 0:
                with _LCU_SHARED_LOCK:
                    _LCU_SHARED_CACHE[endpoint] = (time.monotonic(), payload)
                    _LCU_SHARED_FAILURES.pop(endpoint, None)
            return payload
        except ConnectionError as exc:
            failure_state = _LCU_REQUEST_TRACKER.snapshot()
            if "match-history" in endpoint.casefold():
                schedule_history_failure_probe(
                    self,
                    endpoint,
                    exc,
                    request_context.get("credentials"),
                    failure_state,
                )
            if ttl > 0:
                with _LCU_SHARED_LOCK:
                    _LCU_SHARED_FAILURES[endpoint] = (time.monotonic(), str(exc))
            raise
        finally:
            _LCU_REQUEST_TRACKER.finish(activity)
            if ttl > 0:
                with _LCU_SHARED_LOCK:
                    event = _LCU_SHARED_INFLIGHT.pop(endpoint, None)
                    if event is not None:
                        event.set()

    LeagueClientConnection.__init__ = patched_lcu_init
    LeagueClientConnection.get_json = faster_get_json


    # Opening the Live Match page is a view action, not an explicit data refresh.
    # Keep the visible Refresh button as the only force=True path.
    try:
        from app.ui import live_match_page

        def passive_refresh_now(self: Any) -> None:
            self.scout.refresh(force=False)

        def passive_update_credentials(self: Any) -> None:
            self.scout.refresh(force=False)

        live_match_page.LiveMatchPage.refresh_now = passive_refresh_now
        live_match_page.LiveMatchPage.update_credentials = passive_update_credentials
    except Exception:
        LOGGER.debug("Could not install passive Live Match page refresh", exc_info=True)

    # Local-only mode should not display a missing Riot API banner.
    try:
        from app.ui import live_match_page

        def local_only_api_banner(self: Any) -> None:
            self.api_banner.hide()

        live_match_page.LiveMatchPage._sync_api_banner = local_only_api_banner
    except Exception:
        LOGGER.debug("Could not hide the optional Riot API banner", exc_info=True)

    LiveMatchScout._speed_patch_installed = True
    LOGGER.info(
        "Live Match patch %s enabled: no persistent player cache, local-only, "
        "5-game history, reliable ranked lookup, shared snapshots, single-flight LCU, root-cause probes",
        LIVE_MATCH_SPEED_BUILD,
    )
