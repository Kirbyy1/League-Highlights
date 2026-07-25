from __future__ import annotations

import re
from typing import Any


ACTIVE_GAME_PHASES = frozenset({"GameStart", "InProgress", "Reconnect"})


def normalize_token(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def is_real_display_name(value: Any) -> bool:
    text = " ".join(str(value or "").strip().split())
    lowered = text.casefold()
    return bool(
        text
        and lowered not in {"unknown", "unknown player"}
        and not re.fullmatch(r"player\s+\d+", lowered)
    )


def roster_quality(roster: dict[str, Any] | None) -> dict[str, int]:
    roster = roster if isinstance(roster, dict) else {}
    players = [p for p in roster.get("players", ()) if isinstance(p, dict)]
    real_names = 0
    identifiers = 0
    known_champions = 0
    for player in players:
        display = (
            player.get("riot_id", "")
            or player.get("game_name", "")
            or player.get("summoner_name", "")
        )
        if is_real_display_name(display):
            real_names += 1
        if any(str(player.get(key, "") or "").strip() for key in (
            "puuid", "lcu_player_id", "summoner_id", "player_key"
        )):
            identifiers += 1
        champion = normalize_token(player.get("champion", ""))
        if champion and champion != "unknown":
            known_champions += 1
    return {
        "players": len(players),
        "real_names": real_names,
        "identifiers": identifiers,
        "known_champions": known_champions,
    }


def stable_match_key(roster: dict[str, Any] | None) -> str:
    """Create a source-independent key that survives placeholder -> real-name handoff."""
    roster = roster if isinstance(roster, dict) else {}
    game_id = str(roster.get("game_id", "") or "").strip()
    if game_id:
        return f"game:{game_id}"

    try:
        started_at = int(roster.get("game_started_at", 0) or 0)
    except (TypeError, ValueError):
        started_at = 0
    try:
        queue_id = int(roster.get("queue_id", 0) or 0)
    except (TypeError, ValueError):
        queue_id = 0

    composition: list[str] = []
    for player in roster.get("players", ()):
        if not isinstance(player, dict):
            continue
        team = str(player.get("team", "") or "").upper()
        champion_id = str(player.get("champion_id", "") or "").strip()
        champion = normalize_token(player.get("champion", "unknown")) or "unknown"
        composition.append(f"{team}:{champion_id or champion}")
    composition_key = "|".join(sorted(composition))
    if not composition_key:
        return ""
    if started_at:
        return f"start:{started_at}:q{queue_id}|{composition_key}"
    return f"draft:q{queue_id}|{composition_key}"


def should_defer_placeholder_roster(
    roster: dict[str, Any] | None,
    *,
    first_seen_at: float,
    now: float,
    shared_playerlist_ready: bool,
    grace_seconds: float = 8.0,
) -> bool:
    roster = roster if isinstance(roster, dict) else {}
    phase = str(roster.get("gameflow_phase", "") or "")
    quality = roster_quality(roster)
    if phase not in ACTIVE_GAME_PHASES or quality["players"] < 10:
        return False
    if quality["real_names"] >= 8 or shared_playerlist_ready:
        return False
    return max(0.0, float(now) - float(first_seen_at)) < float(grace_seconds)


def ranked_record_credibility(entry: dict[str, Any] | None) -> tuple[bool, str]:
    """Conservatively reject LCU records known to expose wins but hide losses."""
    if not isinstance(entry, dict):
        return False, "missing queue entry"
    if "wins" not in entry or "losses" not in entry:
        return False, "wins/losses fields are incomplete"
    try:
        wins = int(entry.get("wins", 0) or 0)
        losses = int(entry.get("losses", 0) or 0)
    except (TypeError, ValueError):
        return False, "wins/losses are not numeric"
    if wins < 0 or losses < 0:
        return False, "wins/losses are negative"
    if wins == 0 and losses == 0:
        return False, "no season record"
    # The local ranked endpoint can expose a large win total while suppressing
    # losses for other players. A small undefeated placement run remains valid.
    if losses == 0 and wins >= 20:
        return False, "LCU returned a large wins-only season record"
    return True, "complete"
