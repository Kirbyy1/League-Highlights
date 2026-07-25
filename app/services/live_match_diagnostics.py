from __future__ import annotations

import json
import os
import threading
import time
import traceback
import weakref
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


LIVE_MATCH_DIAGNOSTICS_BUILD = "V12-COMPACT-CYCLE-DIAGNOSTICS"
MAX_LOG_BYTES = 10 * 1024 * 1024
_DIAGNOSTICS_LOCK = threading.RLock()
_DIAGNOSTICS_REFS: list[weakref.ReferenceType[LiveMatchDiagnostics]] = []
_CONSOLE_DEBUG_OVERRIDE: bool | None = None

_DEBUG_ATTRIBUTE_NAMES = (
    "console_debug", "console_debug_enabled", "debug_console",
    "debug_console_enabled", "enable_console_debug", "show_debug_console",
    "debug_logging", "debug_logging_enabled", "console_logging",
    "console_logging_enabled",
)
_SECRET_KEY_PARTS = (
    "authorization", "password", "api_key", "apikey", "riot_api_key",
    "token", "secret", "lockfile", "remoting_auth",
)
_NOISY_CATEGORIES = {
    "gameflow", "summoner", "playerlist", "activeplayer", "gamestats", "eventdata"
}


def set_console_debug_override(enabled: bool | None) -> None:
    global _CONSOLE_DEBUG_OVERRIDE
    with _DIAGNOSTICS_LOCK:
        _CONSOLE_DEBUG_OVERRIDE = None if enabled is None else bool(enabled)


def console_debug_enabled(config: Any) -> bool:
    with _DIAGNOSTICS_LOCK:
        override = _CONSOLE_DEBUG_OVERRIDE
    if override is not None:
        return bool(override)
    env_value = str(os.environ.get("LEAGUE_HIGHLIGHTS_CONSOLE_DEBUG", "")).strip().casefold()
    if env_value in {"1", "true", "yes", "on"}:
        return True
    for name in _DEBUG_ATTRIBUTE_NAMES:
        try:
            if bool(getattr(config, name)):
                return True
        except (AttributeError, TypeError):
            continue
    settings_path = getattr(config, "settings_file", None)
    try:
        if settings_path:
            raw = json.loads(Path(settings_path).read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for key, value in raw.items():
                    normalized = str(key).strip().casefold().replace("-", "_")
                    if (("console" in normalized and "debug" in normalized)
                            or normalized in {"debug_logging", "debug_mode"}):
                        if bool(value):
                            return True
    except (OSError, ValueError, TypeError):
        pass
    return False


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 7:
        return "<max depth reached>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 4000 else value[:4000] + "...<truncated>"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        limited = items[:80]
        result = [_safe_value(item, depth=depth + 1) for item in limited]
        if len(items) > len(limited):
            result.append(f"<{len(items) - len(limited)} more items>")
        return result
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:120]:
            key = str(raw_key)
            if any(part in key.casefold() for part in _SECRET_KEY_PARTS):
                result[key] = "<redacted>"
            else:
                result[key] = _safe_value(raw_value, depth=depth + 1)
        if len(value) > 120:
            result["<truncated_keys>"] = len(value) - 120
        return result
    return repr(value)


def _player_summary(player: Any) -> dict[str, Any]:
    if not isinstance(player, dict):
        return {"type": type(player).__name__}
    scores = player.get("scores", {}) if isinstance(player.get("scores"), dict) else {}
    return {
        "riot_id": player.get("riotId") or player.get("riot_id"),
        "game_name": player.get("riotIdGameName") or player.get("game_name"),
        "tag_line": player.get("riotIdTagLine") or player.get("tag_line"),
        "summoner_name": player.get("summonerName"),
        "puuid": player.get("puuid") or player.get("playerUuid") or player.get("lcu_player_id"),
        "summoner_id": player.get("summonerId") or player.get("summoner_id"),
        "champion": player.get("championName") or player.get("champion"),
        "champion_id": player.get("championId") or player.get("champion_id"),
        "team": player.get("team"),
        "role": player.get("position") or player.get("selectedPosition") or player.get("role"),
        "level": player.get("level") or player.get("account_level"),
        "scores": {
            "kills": scores.get("kills"), "deaths": scores.get("deaths"),
            "assists": scores.get("assists"),
        } if scores else {},
    }


def _gameflow_summary(payload: Any) -> Any:
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return _safe_value(payload)
    game_data = payload.get("gameData", {}) if isinstance(payload.get("gameData"), dict) else {}
    players: list[dict[str, Any]] = []
    for team_key in ("teamOne", "teamTwo"):
        team = game_data.get(team_key, [])
        if isinstance(team, list):
            players.extend(_player_summary(p) for p in team if isinstance(p, dict))
    return {
        "phase": payload.get("phase"),
        "game_id": game_data.get("gameId"),
        "game_start_time": game_data.get("gameStartTime") or game_data.get("gameStartTimestamp"),
        "queue": _safe_value(game_data.get("queue")),
        "player_count": len(players),
        "players": players,
    }


def _rank_summary(payload: Any) -> Any:
    if not isinstance(payload, (dict, list)):
        return _safe_value(payload)
    queues: list[dict[str, Any]] = []
    seen: set[int] = set()
    def visit(value: Any, depth: int = 0) -> None:
        if depth > 5:
            return
        if isinstance(value, list):
            for child in value:
                visit(child, depth + 1)
            return
        if not isinstance(value, dict) or id(value) in seen:
            return
        seen.add(id(value))
        queue_type = str(value.get("queueType", "") or value.get("queue", "") or "")
        if queue_type or any(k in value for k in ("tier", "division", "leaguePoints", "wins", "losses")):
            if any(k in value for k in ("tier", "division", "leaguePoints", "wins", "losses")):
                queues.append({
                    "queueType": queue_type,
                    "tier": value.get("tier"), "division": value.get("division") or value.get("rank"),
                    "leaguePoints": value.get("leaguePoints", value.get("lp")),
                    "wins": value.get("wins"), "losses": value.get("losses"),
                    "currentSeasonWinsForRewards": value.get("currentSeasonWinsForRewards"),
                    "isProvisional": value.get("isProvisional"),
                })
        for child in value.values():
            if isinstance(child, (dict, list)):
                visit(child, depth + 1)
    visit(payload)
    top_keys = list(payload.keys()) if isinstance(payload, dict) else []
    return {"top_level_keys": top_keys, "queue_entries": queues[:20]}


def _history_summary(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return _safe_value(payload)
    raw = payload.get("games", [])
    raw = raw.get("games", []) if isinstance(raw, dict) else raw
    if not isinstance(raw, list):
        return {"top_level_keys": list(payload.keys()), "games_type": type(raw).__name__}
    queue_counts: Counter[str] = Counter()
    games: list[dict[str, Any]] = []
    for game in raw[:35]:
        if not isinstance(game, dict):
            continue
        queue_id = str(game.get("queueId", ""))
        queue_counts[queue_id] += 1
        participants = game.get("participants", [])
        games.append({
            "game_id": game.get("gameId"), "queue_id": game.get("queueId"),
            "created": game.get("gameCreation"), "duration": game.get("gameDuration"),
            "participant_count": len(participants) if isinstance(participants, list) else 0,
        })
    return {
        "top_level_keys": list(payload.keys()), "raw_game_count": len(raw),
        "queue_counts": dict(queue_counts), "games": games,
    }


def summarize_response(endpoint: str, payload: Any) -> Any:
    lowered = str(endpoint or "").casefold()
    if "match-history" in lowered:
        return _history_summary(payload)
    if "ranked-stats" in lowered:
        return _rank_summary(payload)
    if "gameflow" in lowered:
        return _gameflow_summary(payload)
    if "playerlist" in lowered and isinstance(payload, list):
        return {"player_count": len(payload), "players": [_player_summary(p) for p in payload]}
    if "current-summoner" in lowered and isinstance(payload, dict):
        return _player_summary(payload)
    if any(name in lowered for name in ("activeplayer", "gamestats", "eventdata")):
        if "eventdata" in lowered and isinstance(payload, dict):
            events = payload.get("Events", [])
            return {"event_count": len(events) if isinstance(events, list) else 0,
                    "last_event": _safe_value(events[-1]) if isinstance(events, list) and events else None}
        return _safe_value(payload)
    return _safe_value(payload)


class LiveMatchDiagnostics:
    def __init__(self, config: Any) -> None:
        self.config = config
        self.path = Path(getattr(config, "log_dir", Path.cwd())) / "live_match_debug.txt"
        self.backup_path = self.path.with_name("live_match_debug.previous.txt")
        self._lock = threading.RLock()
        self._opened = False
        self._session_started = time.monotonic()
        self._cycle_started: dict[int, float] = {}
        self._cycle_baselines: dict[int, tuple[Counter[str], Counter[str], dict[str, int]]] = {}
        self._request_count: Counter[str] = Counter()
        self._request_failures: Counter[str] = Counter()
        self._request_durations: dict[str, list[float]] = defaultdict(list)
        self._player_stage_started: dict[str, float] = {}
        self._player_stage_times: dict[str, dict[str, float]] = defaultdict(dict)
        self._last_request_signature: dict[str, tuple[str, float]] = {}
        self._suppressed: Counter[str] = Counter()

    @property
    def enabled(self) -> bool:
        return console_debug_enabled(self.config)

    def _header_text(self) -> str:
        header = {
            "event": "diagnostics_started", "build": LIVE_MATCH_DIAGNOSTICS_BUILD,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(), "file": str(self.path),
            "max_file_mb": MAX_LOG_BYTES // (1024 * 1024),
            "note": "Compact local Live Match timing and parsed data. Credentials and tokens are redacted.",
        }
        return "LEAGUE HIGHLIGHTS — LIVE MATCH DEBUG\n" + json.dumps(header, ensure_ascii=False, indent=2) + "\n\n"

    def _ensure_open(self) -> bool:
        if not self.enabled:
            return False
        with self._lock:
            if self._opened:
                return True
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(self._header_text(), encoding="utf-8")
            self._opened = True
            return True

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        try:
            size = self.path.stat().st_size if self.path.exists() else 0
        except OSError:
            size = 0
        if size + incoming_bytes <= MAX_LOG_BYTES:
            return
        try:
            self.backup_path.unlink(missing_ok=True)
            if self.path.exists():
                self.path.replace(self.backup_path)
            self.path.write_text(self._header_text(), encoding="utf-8")
        except OSError:
            pass

    def event(self, event: str, **fields: Any) -> None:
        if not self._ensure_open():
            return
        safe_fields: dict[str, Any] = {}
        for raw_key, raw_value in fields.items():
            key = str(raw_key)
            safe_fields[key] = "<redacted>" if any(p in key.casefold() for p in _SECRET_KEY_PARTS) else _safe_value(raw_value)
        record = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_since_app_trace_s": round(time.monotonic() - self._session_started, 4),
            "thread": threading.current_thread().name, "event": str(event), **safe_fields,
        }
        serialized = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=False) + "\n\n"
        with self._lock:
            self._rotate_if_needed(len(serialized.encode("utf-8")))
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(serialized)

    def exception(self, event: str, exc: BaseException, **fields: Any) -> None:
        self.event(event, error_type=type(exc).__name__, error=str(exc),
                   traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), **fields)

    @staticmethod
    def endpoint_category(endpoint: str) -> str:
        lowered = str(endpoint or "").casefold()
        if "ranked-stats" in lowered: return "rank"
        if "match-history" in lowered: return "history"
        if "gameflow" in lowered: return "gameflow"
        if "summoner" in lowered: return "summoner"
        if "playerlist" in lowered: return "playerlist"
        if "activeplayer" in lowered: return "activeplayer"
        if "gamestats" in lowered: return "gamestats"
        if "eventdata" in lowered: return "eventdata"
        return "other"

    def request_finished(self, endpoint: str, duration_s: float, *, payload: Any = None,
                         error: BaseException | None = None) -> None:
        category = self.endpoint_category(endpoint)
        self._request_count[category] += 1
        self._request_durations[category].append(float(duration_s))
        if error is not None:
            self._request_failures[category] += 1
        response = summarize_response(endpoint, payload) if error is None else None
        signature_payload = {
            "endpoint": endpoint, "success": error is None,
            "error": f"{type(error).__name__}:{error}" if error is not None else "",
            "response": response,
        }
        signature = json.dumps(_safe_value(signature_payload), ensure_ascii=False, sort_keys=True)
        dedup_key = f"{category}:{endpoint}"
        now = time.monotonic()
        previous = self._last_request_signature.get(dedup_key)
        window = 5.0 if error is not None else 15.0
        if category in _NOISY_CATEGORIES and previous and previous[0] == signature and now - previous[1] < window:
            self._suppressed[dedup_key] += 1
            return
        suppressed = int(self._suppressed.pop(dedup_key, 0))
        self._last_request_signature[dedup_key] = (signature, now)
        self.event(
            "lcu_request", endpoint=endpoint, category=category,
            duration_ms=round(float(duration_s) * 1000.0, 2), success=error is None,
            error_type=type(error).__name__ if error is not None else "",
            error=str(error) if error is not None else "", response=response,
            suppressed_identical_requests=suppressed,
        )

    def cycle_started(self, generation: int, *, force: bool) -> None:
        generation = int(generation)
        self._cycle_started[generation] = time.monotonic()
        self._cycle_baselines[generation] = (
            Counter(self._request_count), Counter(self._request_failures),
            {key: len(values) for key, values in self._request_durations.items()},
        )
        self._player_stage_times = defaultdict(dict)
        self.event("cycle_started", generation=generation, force=force)

    def _cycle_request_summary(self, generation: int) -> dict[str, Any]:
        counts0, failures0, lengths0 = self._cycle_baselines.pop(
            int(generation), (Counter(), Counter(), {})
        )
        result: dict[str, Any] = {}
        categories = set(self._request_count) | set(counts0)
        for category in sorted(categories):
            count = int(self._request_count[category] - counts0[category])
            if count <= 0:
                continue
            failures = int(self._request_failures[category] - failures0[category])
            values = self._request_durations.get(category, [])[lengths0.get(category, 0):]
            result[category] = {
                "count": count, "failures": failures,
                "total_ms": round(sum(values) * 1000.0, 2),
                "average_ms": round(sum(values) / len(values) * 1000.0, 2) if values else 0.0,
                "slowest_ms": round(max(values) * 1000.0, 2) if values else 0.0,
            }
        return result

    def cycle_finished(self, generation: int, **fields: Any) -> float:
        start = self._cycle_started.pop(int(generation), time.monotonic())
        duration = max(0.0, time.monotonic() - start)
        self.event(
            "cycle_finished", generation=generation,
            total_duration_ms=round(duration * 1000.0, 2),
            cycle_request_summary=self._cycle_request_summary(generation),
            player_stage_times=dict(self._player_stage_times), **fields,
        )
        return duration

    def player_started(self, player_key: str, player: dict[str, Any]) -> None:
        self._player_stage_started[player_key] = time.monotonic()
        self.event("player_started", player_key=player_key, player=_player_summary(player))

    def player_stage(self, player_key: str, stage: str, payload: dict[str, Any] | None) -> None:
        started = self._player_stage_started.get(player_key, time.monotonic())
        elapsed = max(0.0, time.monotonic() - started)
        self._player_stage_times[player_key][str(stage)] = round(elapsed, 4)
        # The final player event contains the complete parsed card data. Intermediate
        # events stay compact to keep the trace useful and bounded.
        compact = {
            key: (payload or {}).get(key) for key in (
                "state", "rank", "tier", "division", "lp", "rank_state",
                "ranked_games", "ranked_win_rate", "sample_games", "recent_win_rate",
                "avg_kda", "history_source", "rank_source", "ranked_record_available",
            ) if key in (payload or {})
        }
        self.event("player_stage", player_key=player_key, stage=stage,
                   elapsed_ms=round(elapsed * 1000.0, 2), data=compact)

    def player_finished(self, player_key: str, payload: dict[str, Any],
                        error: BaseException | None = None) -> None:
        started = self._player_stage_started.pop(player_key, time.monotonic())
        elapsed = max(0.0, time.monotonic() - started)
        if error is None:
            self.event("player_finished", player_key=player_key,
                       total_duration_ms=round(elapsed * 1000.0, 2), final_data=payload)
        else:
            self.exception("player_failed", error, player_key=player_key,
                           total_duration_ms=round(elapsed * 1000.0, 2))

    def request_summary(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for category in sorted(self._request_count):
            values = self._request_durations.get(category, [])
            result[category] = {
                "count": int(self._request_count[category]),
                "failures": int(self._request_failures[category]),
                "total_ms": round(sum(values) * 1000.0, 2),
                "average_ms": round(sum(values) / len(values) * 1000.0, 2) if values else 0.0,
                "slowest_ms": round(max(values) * 1000.0, 2) if values else 0.0,
            }
        return result


def register_diagnostics(diagnostics: LiveMatchDiagnostics) -> None:
    with _DIAGNOSTICS_LOCK:
        _DIAGNOSTICS_REFS.append(weakref.ref(diagnostics))


def active_diagnostics() -> list[LiveMatchDiagnostics]:
    active: list[LiveMatchDiagnostics] = []
    retained: list[weakref.ReferenceType[LiveMatchDiagnostics]] = []
    with _DIAGNOSTICS_LOCK:
        for ref in _DIAGNOSTICS_REFS:
            diagnostics = ref()
            if diagnostics is None:
                continue
            retained.append(ref)
            if diagnostics.enabled:
                active.append(diagnostics)
        _DIAGNOSTICS_REFS[:] = retained
    return active


def record_lcu_request(endpoint: str, operation: Callable[[], Any]) -> Any:
    diagnostics = active_diagnostics()
    started = time.perf_counter()
    try:
        payload = operation()
    except BaseException as exc:
        duration = time.perf_counter() - started
        for trace in diagnostics:
            trace.request_finished(endpoint, duration, error=exc)
        raise
    duration = time.perf_counter() - started
    for trace in diagnostics:
        trace.request_finished(endpoint, duration, payload=payload)
    return payload
