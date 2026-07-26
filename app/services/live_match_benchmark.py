from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote


BENCHMARK_BUILD = "V17-STREAMER-NAME-OPTION"
ACTIVE_OR_SENSITIVE_PHASES = {"ChampSelect", "GameStart", "InProgress", "Reconnect"}
_FIXTURE_VERSION = 1


def benchmark_root(config: Any) -> Path:
    log_dir = Path(getattr(config, "log_dir", Path.cwd()))
    return log_dir.parent / "benchmark"


def fixture_path(config: Any) -> Path:
    return benchmark_root(config) / "live_match_fixture.json"


def result_path(config: Any) -> Path:
    return benchmark_root(config) / "live_match_benchmark_latest.json"


def _identity(player: dict[str, Any]) -> str:
    for key in ("puuid", "lcu_player_id", "summoner_id", "account_id"):
        value = str(player.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _display_name(player: dict[str, Any]) -> str:
    riot_id = str(player.get("riot_id", "") or "").strip()
    game_name = str(player.get("game_name", "") or "").strip()
    tag_line = str(player.get("tag_line", "") or "").strip()
    return riot_id or (f"{game_name}#{tag_line}" if game_name and tag_line else game_name)


def _sanitized_player(player: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "player_key",
        "puuid",
        "lcu_player_id",
        "summoner_id",
        "account_id",
        "riot_id",
        "game_name",
        "tag_line",
        "champion",
        "champion_id",
        "role",
        "team",
        "is_active",
        "spells",
    )
    clean = {key: player.get(key) for key in allowed if key in player}
    identity = _identity(clean)
    clean["player_key"] = str(clean.get("player_key", "") or identity).casefold()
    clean["riot_id"] = _display_name(clean) or str(clean.get("riot_id", "") or "Unknown player")
    clean["champion"] = str(clean.get("champion", "") or "Unknown")
    clean["role"] = str(clean.get("role", "") or "").upper()
    clean["team"] = str(clean.get("team", "") or "").upper()
    clean["spells"] = list(clean.get("spells", ()) or ())
    return clean


def validate_players(players: Any) -> tuple[bool, str, list[dict[str, Any]]]:
    if not isinstance(players, list):
        return False, "Fixture players must be a list", []
    cleaned = [_sanitized_player(player) for player in players if isinstance(player, dict)]
    if len(cleaned) != 10:
        return False, f"Fixture contains {len(cleaned)} players; exactly 10 are required", cleaned

    identities = [_identity(player).casefold() for player in cleaned]
    missing = [index + 1 for index, identity in enumerate(identities) if not identity]
    if missing:
        return False, f"Players without a usable local identifier: {missing}", cleaned
    if len(set(identities)) != 10:
        return False, "Fixture does not contain 10 unique local player identifiers", cleaned
    return True, "ok", cleaned


def save_roster_fixture(config: Any, roster: dict[str, Any]) -> tuple[bool, str]:
    players = list(roster.get("players", ()) or ()) if isinstance(roster, dict) else []
    valid, message, cleaned = validate_players(players)
    if not valid:
        return False, message

    payload = {
        "fixture_version": _FIXTURE_VERSION,
        "build": BENCHMARK_BUILD,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "game_id": str(roster.get("game_id", "") or ""),
        "queue_id": int(roster.get("queue_id", 0) or 0),
        "roster_source": str(roster.get("roster_source", "") or ""),
        "players": cleaned,
        "note": (
            "Local benchmark identifiers only. No League authentication token, "
            "lockfile password, or authorization header is stored."
        ),
    }
    path = fixture_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)
    return True, str(path)


def load_roster_fixture(config: Any) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    path = fixture_path(config)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], {}, "No saved 10-player roster is available yet"
    except (OSError, ValueError, TypeError) as exc:
        return [], {}, f"Could not read saved benchmark roster: {exc}"
    if not isinstance(payload, dict):
        return [], {}, "Saved benchmark roster is not a JSON object"
    valid, message, players = validate_players(payload.get("players"))
    if not valid:
        return [], payload, message
    return players, payload, "ok"


def clear_roster_fixture(config: Any) -> bool:
    path = fixture_path(config)
    try:
        existed = path.exists()
        path.unlink(missing_ok=True)
        return existed
    except OSError:
        return False


def fixture_status(config: Any) -> dict[str, Any]:
    players, payload, message = load_roster_fixture(config)
    path = fixture_path(config)
    return {
        "available": len(players) == 10,
        "players": len(players),
        "captured_at_utc": str(payload.get("captured_at_utc", "") or ""),
        "game_id": str(payload.get("game_id", "") or ""),
        "path": str(path),
        "message": message,
    }


def _clear_benchmark_caches(scout: Any) -> None:
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
    scout._lean_request_counts = {"rank": 0, "history": 0}
    for name in ("_lean_rank_profile_keys", "_lean_history_profile_keys"):
        value = getattr(scout, name, None)
        if hasattr(value, "clear"):
            value.clear()
    scout._history_expected_profiles = 10
    coordinator = getattr(scout, "_history_batch_coordinator", None)
    if coordinator is not None and hasattr(coordinator, "reset"):
        coordinator.reset(10)


def _new_metrics() -> dict[str, Any]:
    return {
        "rank_endpoint_attempts": 0,
        "rank_endpoint_successes": 0,
        "rank_identity_fallbacks": 0,
        # Legacy names are retained, but now have strict raw-attempt semantics:
        # history_attempts == history_successes + history_failures.
        "history_attempts": 0,
        "history_successes": 0,
        "history_failures": 0,
        "history_retries": 0,
        # Explicit aliases/terminal counters remove ambiguity for result readers.
        "history_raw_attempts": 0,
        "history_raw_successes": 0,
        "history_raw_failures": 0,
        "history_retry_attempts": 0,
        "history_terminal_page_failures": 0,
        "history_first_page_terminal_failures": 0,
        "history_second_page_terminal_failures": 0,
        "history_first_page_attempts": 0,
        "history_first_page_retries": 0,
        "history_second_page_attempts": 0,
        "history_second_page_retries": 0,
        "history_warmup_runs": 0,
        "history_warmup_successes": 0,
        "history_warmup_failures": 0,
        "history_warmup_wait_total_ms": 0.0,
        "history_warmup_wait_peak_ms": 0.0,
        "history_first_phase_wait_total_ms": 0.0,
        "history_first_phase_wait_peak_ms": 0.0,
        "history_initial_round_wait_total_ms": 0.0,
        "history_initial_round_wait_peak_ms": 0.0,
        # Backward-compatible aliases for v15 result readers.
        "history_page2_barrier_wait_total_ms": 0.0,
        "history_page2_barrier_wait_peak_ms": 0.0,
        "history_gate_wait_total_ms": 0.0,
        "history_gate_wait_peak_ms": 0.0,
    }


def _set_metrics_target(scout: Any, target: dict[str, Any] | None) -> None:
    scout._live_match_benchmark_metrics = target
    if not hasattr(scout, "_live_match_benchmark_metrics_lock"):
        scout._live_match_benchmark_metrics_lock = threading.RLock()


def _run_profile_pass(
    scout: Any,
    players: list[dict[str, Any]],
    platform: str,
    *,
    label: str,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    total = len(players)
    started = time.perf_counter()
    lock = threading.RLock()
    rank_ready: set[str] = set()
    history_ready: set[str] = set()
    first_rank_ms: float | None = None
    all_ranks_ms: float | None = None
    first_history_ms: float | None = None
    all_histories_ms: float | None = None
    player_durations: dict[str, float] = {}
    player_states: dict[str, str] = {}
    errors: dict[str, str] = {}

    def now_ms() -> float:
        return round((time.perf_counter() - started) * 1000.0, 2)

    def report(player_key: str, stage: str, _payload: dict[str, Any] | None = None) -> None:
        nonlocal first_rank_ms, all_ranks_ms, first_history_ms, all_histories_ms
        key = str(player_key or "")
        with lock:
            if stage == "rank":
                rank_ready.add(key)
                if first_rank_ms is None:
                    first_rank_ms = now_ms()
                if len(rank_ready) == total and all_ranks_ms is None:
                    all_ranks_ms = now_ms()
            if stage in {"fast", "ready"}:
                history_ready.add(key)
                if first_history_ms is None:
                    first_history_ms = now_ms()
                if len(history_ready) == total and all_histories_ms is None:
                    all_histories_ms = now_ms()
        if progress is not None:
            progress(
                f"{label}: ranks {len(rank_ready)}/{total} · "
                f"histories {len(history_ready)}/{total}"
            )

    def analyse(player: dict[str, Any]) -> tuple[str, dict[str, Any], float]:
        key = str(player.get("player_key", "") or _identity(player)).casefold()
        player_started = time.perf_counter()
        result = scout._player_profile(player, platform, "", report)
        duration_ms = round((time.perf_counter() - player_started) * 1000.0, 2)
        return key, result if isinstance(result, dict) else {}, duration_ms

    with ThreadPoolExecutor(max_workers=10, thread_name_prefix="LiveBenchmark") as executor:
        futures = {executor.submit(analyse, player): player for player in players}
        for future in as_completed(futures):
            player = futures[future]
            fallback_key = str(player.get("player_key", "") or _identity(player)).casefold()
            try:
                key, result, duration_ms = future.result()
                player_durations[key] = duration_ms
                player_states[key] = str(result.get("state", "") or "unknown")
            except BaseException as exc:
                errors[fallback_key] = f"{type(exc).__name__}: {exc}"
                player_states[fallback_key] = "error"

    total_ms = now_ms()
    return {
        "label": label,
        "players": total,
        "first_rank_ms": first_rank_ms,
        "all_ranks_ms": all_ranks_ms,
        "first_history_ms": first_history_ms,
        "all_histories_ms": all_histories_ms,
        "total_complete_ms": total_ms,
        "rank_callbacks": len(rank_ready),
        "history_callbacks": len(history_ready),
        "player_durations_ms": player_durations,
        "player_states": player_states,
        "errors": errors,
    }


def _phase(scout: Any) -> str:
    try:
        value = scout._lcu.gameflow_phase()
        return "" if value is None else str(value or "")
    except Exception:
        return ""


def _write_result(config: Any, result: dict[str, Any]) -> str:
    path = result_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
    temporary.replace(path)
    return str(path)


def run_full_benchmark(
    scout: Any,
    config: Any,
    players: list[dict[str, Any]],
    fixture_meta: dict[str, Any],
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    phase = _phase(scout)
    if phase in ACTIVE_OR_SENSITIVE_PHASES:
        raise RuntimeError(f"Benchmark is disabled while League phase is {phase}")

    platform = str(getattr(config, "riot_platform", "euw1") or "euw1").casefold()
    metrics = _new_metrics()
    scout._live_match_benchmark_running = True
    try:
        if bool(getattr(scout, "_busy", False)):
            deadline = time.monotonic() + 3.0
            while bool(getattr(scout, "_busy", False)) and time.monotonic() < deadline:
                time.sleep(0.05)
            if bool(getattr(scout, "_busy", False)):
                raise RuntimeError(
                    "Live Match is still busy; wait a moment and run the benchmark again"
                )
        try:
            summoner = scout._lcu.current_summoner(max_age_seconds=0.0)
        except Exception as exc:
            raise RuntimeError(
                "Open the League Client and sign in before running the benchmark"
            ) from exc
        if not isinstance(summoner, dict) or not summoner:
            raise RuntimeError(
                "Open the League Client and sign in before running the benchmark"
            )
        _clear_benchmark_caches(scout)
        _set_metrics_target(scout, metrics)
        if progress is not None:
            progress("Cold pass: loading ranks and five-game histories for 10 saved players")
        cold = _run_profile_pass(scout, players, platform, label="Cold pass", progress=progress)

        # These are unique completed player callbacks, not raw endpoint calls.
        # The older counter only increased when a live history payload contained
        # games, which produced misleading values such as 6 or 8 despite 10/10
        # cards reaching ready.
        cold_counts = {
            "rank": int(cold.get("rank_callbacks", 0) or 0),
            "history": int(cold.get("history_callbacks", 0) or 0),
        }
        if progress is not None:
            progress("Warm pass: measuring the current-match memory cache")
        warm_started = time.perf_counter()
        warm = _run_profile_pass(scout, players, platform, label="Warm pass", progress=progress)
        warm["wall_clock_ms"] = round((time.perf_counter() - warm_started) * 1000.0, 2)

        result = {
            "benchmark_build": BENCHMARK_BUILD,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "mode": "saved_10_player_roster",
            "phase": phase,
            "platform": platform,
            "fixture": {
                "captured_at_utc": str(fixture_meta.get("captured_at_utc", "") or ""),
                "game_id": str(fixture_meta.get("game_id", "") or ""),
                "queue_id": int(fixture_meta.get("queue_id", 0) or 0),
                "roster_source": str(fixture_meta.get("roster_source", "") or ""),
                "players": 10,
            },
            "cold": cold,
            "warm": warm,
            "pipeline_counts": cold_counts,
            "pipeline_counts_note": (
                "Unique player profiles completed during the cold pass; raw LCU calls are in request_metrics."
            ),
            "request_metrics": dict(metrics),
            "request_metrics_consistent": (
                int(metrics.get("history_attempts", 0) or 0)
                == int(metrics.get("history_successes", 0) or 0)
                + int(metrics.get("history_failures", 0) or 0)
            ),
            "request_metrics_note": (
                "history_attempts/successes/failures are raw LCU attempts and reconcile exactly; "
                "history_retries counts retry attempts; history_terminal_page_failures counts pages "
                "still unavailable after their retry. Legacy page2_barrier metrics are soft initial-round waits in v16."
            ),
            "result_file": "",
            "limitations": [
                "Measures rank/history/profile processing with saved identities.",
                "Does not measure live game process startup or playerlist snapshot arrival.",
            ],
        }
        result["result_file"] = _write_result(config, result)
        return result
    finally:
        _set_metrics_target(scout, None)
        # Benchmark fixtures are historical by design; do not leave their player
        # profiles in the live scout's current-match RAM caches.
        _clear_benchmark_caches(scout)
        scout._history_expected_profiles = 0
        coordinator = getattr(scout, "_history_batch_coordinator", None)
        if coordinator is not None and hasattr(coordinator, "reset"):
            coordinator.reset(0)
        scout._live_match_benchmark_running = False


def _current_player_identifier(scout: Any) -> tuple[str, dict[str, Any]]:
    summoner = scout._lcu.current_summoner(max_age_seconds=0.0)
    if not isinstance(summoner, dict) or not summoner:
        return "", {}
    for key in ("puuid", "playerUuid", "summonerId", "id", "accountId"):
        value = str(summoner.get(key, "") or "").strip()
        if value:
            return value, summoner
    return "", summoner


def run_single_account_stress(
    scout: Any,
    config: Any,
    progress: Callable[[str], None] | None = None,
    *,
    request_count: int = 10,
    concurrency: int = 2,
) -> dict[str, Any]:
    phase = _phase(scout)
    if phase in ACTIVE_OR_SENSITIVE_PHASES:
        raise RuntimeError(f"Benchmark is disabled while League phase is {phase}")

    scout._live_match_benchmark_running = True
    try:
        if bool(getattr(scout, "_busy", False)):
            deadline = time.monotonic() + 3.0
            while bool(getattr(scout, "_busy", False)) and time.monotonic() < deadline:
                time.sleep(0.05)
            if bool(getattr(scout, "_busy", False)):
                raise RuntimeError(
                    "Live Match is still busy; wait a moment and run the benchmark again"
                )

        identifier, summoner = _current_player_identifier(scout)
        if not identifier:
            raise RuntimeError("Open the League Client and sign in before running the benchmark")

        endpoint = (
            f"/lol-match-history/v1/products/lol/{quote(identifier, safe='')}/matches"
            "?begIndex=0&endIndex=15"
        )
        started = time.perf_counter()
        lock = threading.RLock()
        completed = 0
        rows: list[dict[str, Any]] = []

        def request_once(index: int) -> dict[str, Any]:
            request_started = time.perf_counter()
            try:
                payload = scout._lcu.get_json(endpoint)
                success = isinstance(payload, dict)
                error = "" if success else "Response was not a JSON object"
            except BaseException as exc:
                success = False
                error = f"{type(exc).__name__}: {exc}"
            duration_ms = round((time.perf_counter() - request_started) * 1000.0, 2)
            return {
                "request": index + 1,
                "success": success,
                "duration_ms": duration_ms,
                "error": error,
            }

        with ThreadPoolExecutor(
            max_workers=max(1, min(4, concurrency)),
            thread_name_prefix="LCUStress",
        ) as executor:
            futures = [executor.submit(request_once, index) for index in range(request_count)]
            for future in as_completed(futures):
                row = future.result()
                rows.append(row)
                with lock:
                    completed += 1
                if progress is not None:
                    progress(
                        f"History stress test: {completed}/{request_count} requests complete"
                    )

        rows.sort(key=lambda row: int(row["request"]))
        durations = [float(row["duration_ms"]) for row in rows]
        successes = sum(1 for row in rows if row["success"])
        failures = request_count - successes
        result = {
            "benchmark_build": BENCHMARK_BUILD,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "mode": "single_account_history_stress",
            "phase": phase,
            "requests": request_count,
            "concurrency": concurrency,
            "successes": successes,
            "failures": failures,
            "total_complete_ms": round((time.perf_counter() - started) * 1000.0, 2),
            "minimum_request_ms": min(durations) if durations else None,
            "maximum_request_ms": max(durations) if durations else None,
            "average_request_ms": (
                round(sum(durations) / len(durations), 2) if durations else None
            ),
            "request_results": rows,
            "account": {
                "riot_id": str(summoner.get("riotId", "") or ""),
                "game_name": str(summoner.get("gameName", "") or ""),
                "tag_line": str(summoner.get("tagLine", "") or ""),
            },
            "result_file": "",
            "limitations": [
                "No saved 10-player fixture was available.",
                (
                    "This tests raw LCU history transport using one account, "
                    "not full ten-player card analysis."
                ),
            ],
        }
        result["result_file"] = _write_result(config, result)
        return result
    finally:
        scout._live_match_benchmark_running = False


def run_benchmark(
    scout: Any,
    config: Any,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    players, fixture_meta, _message = load_roster_fixture(config)
    if len(players) == 10:
        return run_full_benchmark(scout, config, players, fixture_meta, progress)
    return run_single_account_stress(scout, config, progress)


def format_result(result: dict[str, Any]) -> str:
    mode = str(result.get("mode", "") or "")
    if mode == "saved_10_player_roster":
        cold = dict(result.get("cold", {}) or {})
        warm = dict(result.get("warm", {}) or {})
        metrics = dict(result.get("request_metrics", {}) or {})
        errors = len(dict(cold.get("errors", {}) or {}))
        pipeline = dict(result.get("pipeline_counts", {}) or {})
        return (
            f"Full benchmark complete · cold {float(cold.get('total_complete_ms', 0) or 0):.2f} ms · "
            f"all ranks {cold.get('all_ranks_ms')} ms · all histories {cold.get('all_histories_ms')} ms · "
            f"profiles {int(pipeline.get('history', 0) or 0)}/10 · "
            f"warm {float(warm.get('total_complete_ms', 0) or 0):.2f} ms · "
            f"history attempts {int(metrics.get('history_attempts', 0) or 0)} · "
            f"raw failures {int(metrics.get('history_failures', 0) or 0)} · "
            f"retries {int(metrics.get('history_retries', 0) or 0)} · "
            f"terminal page failures {int(metrics.get('history_terminal_page_failures', 0) or 0)} · "
            f"player errors {errors}"
        )
    return (
        f"History stress test complete · {int(result.get('successes', 0) or 0)}/"
        f"{int(result.get('requests', 0) or 0)} succeeded · "
        f"total {float(result.get('total_complete_ms', 0) or 0):.2f} ms · "
        f"average {float(result.get('average_request_ms', 0) or 0):.2f} ms"
    )
