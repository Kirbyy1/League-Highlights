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


LOGGER = logging.getLogger(__name__)
LIVE_MATCH_SPEED_BUILD = "V6-LEAN-CURRENT-MATCH"

# The recorder already requests these Live Client resources. Live Match reuses the
# most recent snapshots instead of downloading the same JSON a second time.
_LIVE_SNAPSHOT_LOCK = threading.RLock()
_LIVE_SNAPSHOTS: dict[str, tuple[float, Any]] = {}
_SCOUTS: set[Any] = set()


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

    original_local_json = LiveMatchScout._local_json

    def shared_local_json(endpoint: str) -> Any:
        cached = _recent_snapshot(endpoint, 2.5)
        if cached is not None:
            return cached
        return original_local_json(endpoint)

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
        self._lean_request_counts = {"rank": 0, "history": 0}
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

    # ------------------------------------------------------------------
    # Freeze a complete roster and clear temporary data on the next match.
    # External Riot/Spectator calls are disabled by always passing an empty key.
    # ------------------------------------------------------------------
    original_discover_roster = LiveMatchScout._discover_roster

    def lean_discover_roster(self: Any, platform: str, _api_key: str) -> dict[str, Any]:
        snapshot_signature = _snapshot_roster_signature()
        frozen = getattr(self, "_lean_frozen_roster", None)
        previous_snapshot_signature = str(
            getattr(self, "_lean_snapshot_signature", "") or ""
        )

        if (
            frozen
            and snapshot_signature
            and snapshot_signature == previous_snapshot_signature
        ):
            # The recorder is still receiving this exact live roster. Reuse it
            # without touching LCU or port 2999 from the Live Match scout.
            return frozen

        if (
            previous_snapshot_signature
            and snapshot_signature
            and snapshot_signature != previous_snapshot_signature
        ):
            _clear_current_match_state(self, keep_roster=False)

        roster = original_discover_roster(self, platform, "")
        if isinstance(roster, dict) and roster.get("players"):
            self._lean_frozen_roster = roster
            self._lean_snapshot_signature = snapshot_signature or self._stable_roster_signature(roster)
        else:
            phase = str(roster.get("gameflow_phase", "") or "") if isinstance(roster, dict) else ""
            if phase not in {"GameStart", "InProgress", "Reconnect"}:
                _clear_current_match_state(self, keep_roster=False)
        return roster

    LiveMatchScout._discover_roster = lean_discover_roster

    original_refresh = LiveMatchScout.refresh

    def lean_refresh(self: Any, force: bool = False) -> None:
        if force:
            # Manual Refresh is the one explicit way to request fresh data again
            # for the same match.
            _clear_current_match_state(self, keep_roster=True)
            self._lean_request_counts = {"rank": 0, "history": 0}
        original_refresh(self, force=force)

    LiveMatchScout.refresh = lean_refresh

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
        candidates: list[str] = []
        for key in ("puuid", "lcu_player_id", "summoner_id", "player_key"):
            value = str(player.get(key, "") or "").strip()
            if value and value not in candidates:
                candidates.append(value)
        profile_thread_state.rank_candidates = candidates
        try:
            return original_player_profile(
                self,
                player,
                platform,
                "",
                progress_callback,
            )
        finally:
            profile_thread_state.rank_candidates = []

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
        with rank_gate:
            for candidate in candidates:
                encoded = quote(candidate, safe="")
                endpoints = (
                    f"/lol-ranked/v1/cached-ranked-stats/{encoded}",
                    f"/lol-ranked/v1/ranked-stats/{encoded}",
                )
                for endpoint in endpoints:
                    payload = self._lcu.get_json_optional(endpoint, None)
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
                        "win_rate": round(wins / games * 100.0, 1) if games else None,
                        "rank_state": "ready" if tier != "UNRANKED" else "unranked",
                        "ranked_queue": "RANKED_SOLO_5x5",
                    }
                    self._cache_store(self._lcu_rank_cache, cache_key, result)
                    self._lean_request_counts["rank"] = int(
                        self._lean_request_counts.get("rank", 0)
                    ) + 1
                    result["_cache_state"] = "live"
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
    history_gate = threading.BoundedSemaphore(3)

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
        with history_gate:
            payload = self._lcu.get_json_optional(
                f"/lol-match-history/v1/products/lol/{encoded}/matches?begIndex=0&endIndex=15",
                None,
            )
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
                second = self._lcu.get_json_optional(
                    f"/lol-match-history/v1/products/lol/{encoded}/matches?begIndex=15&endIndex=35",
                    None,
                )
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

    def faster_get_json(self: Any, endpoint: str) -> Any:
        credentials = self._get_credentials()
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
                "User-Agent": "LeagueHighlights/LCULeanV6",
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
            raise ConnectionError("League Client API request timed out") from exc

    LeagueClientConnection.__init__ = patched_lcu_init
    LeagueClientConnection.get_json = faster_get_json

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
        "5-game history, reliable ranked lookup, shared recorder snapshots",
        LIVE_MATCH_SPEED_BUILD,
    )
