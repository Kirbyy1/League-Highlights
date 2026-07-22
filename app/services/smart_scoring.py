from __future__ import annotations

from dataclasses import dataclass


SENSITIVITY_THRESHOLDS = {
    "strict": 50,
    "balanced": 30,
    "save_more": 12,
}


@dataclass(slots=True, frozen=True)
class PlayCandidate:
    kind: str
    kill_count: int = 0
    stolen: bool = False
    elder: bool = False
    solo_kills: int = 0
    outnumbered_kill_count: int = 0
    active_level: int = 0
    highest_victim_level: int = 0
    min_health_percent: float | None = None
    died_after: bool = False
    ace: bool = False
    action_seconds: float = 0.0
    assist_count: int = 0
    team_kills: int = 0
    assist_heavy: bool = False


@dataclass(slots=True, frozen=True)
class ScoreResult:
    score: int
    reasons: tuple[str, ...]
    keep: bool
    threshold: int


def score_candidate(
    candidate: PlayCandidate,
    *,
    enabled: bool = True,
    sensitivity: str = "balanced",
    threshold_adjustment: int = 0,
) -> ScoreResult:
    """Score a candidate using explainable rules.

    The scorer deliberately uses only information exposed by Riot's local live
    data: discrete events, current player state, levels, team, and death state.
    It does not claim to judge mechanics such as skill-shot dodges.
    """

    threshold = max(0, SENSITIVITY_THRESHOLDS.get(sensitivity, 30) + int(threshold_adjustment))
    score = 0
    reasons: list[str] = []
    kind = candidate.kind.casefold()

    if kind == "kill":
        kill_base = {1: 12, 2: 30, 3: 55, 4: 80, 5: 110}
        count = max(1, min(5, int(candidate.kill_count or 1)))
        score += kill_base[count]
        reasons.append(f"{count} champion kill{'s' if count != 1 else ''}")

        if candidate.solo_kills:
            bonus = 15 if candidate.solo_kills == 1 else min(25, 10 + candidate.solo_kills * 5)
            score += bonus
            reasons.append(f"{candidate.solo_kills} solo kill{'s' if candidate.solo_kills != 1 else ''}")

        outnumbered_count = max(0, int(candidate.outnumbered_kill_count))
        if outnumbered_count >= 2:
            bonus = min(30, 18 + (outnumbered_count - 2) * 6)
            score += bonus
            reasons.append(f"{outnumbered_count}v1 with no allied assists")

        if (
            candidate.highest_victim_level > 0
            and candidate.active_level > 0
            and candidate.highest_victim_level > candidate.active_level
        ):
            gap = candidate.highest_victim_level - candidate.active_level
            bonus = min(18, 8 + gap * 4)
            score += bonus
            reasons.append(f"beat a level {candidate.highest_victim_level} opponent")

        health = candidate.min_health_percent
        if health is not None:
            if health <= 10:
                score += 25
                reasons.append(f"survived at {health:.0f}% health")
            elif health <= 25:
                score += 12
                reasons.append(f"survived at {health:.0f}% health")

        if candidate.ace:
            score += 18
            reasons.append("team ace")

        if count >= 2 and candidate.action_seconds <= 4.0:
            score += 10
            reasons.append("rapid multikill")

        if candidate.died_after:
            score -= 25
            reasons.append("died immediately after")
            if count == 1:
                score -= 10
                reasons.append("one-for-one trade")

    elif kind == "assist":
        count = max(1, min(5, int(candidate.assist_count or 1)))
        assist_base = {1: 8, 2: 24, 3: 42, 4: 60, 5: 78}
        score += assist_base[count]
        reasons.append(f"{count} assist{'s' if count != 1 else ''} in one fight")

        if candidate.assist_heavy:
            score += 8
            reasons.append("assist-heavy support impact")

        health = candidate.min_health_percent
        if health is not None:
            if health <= 10:
                score += 28
                reasons.append(f"survived at {health:.0f}% health")
            elif health <= 25:
                score += 15
                reasons.append(f"survived at {health:.0f}% health")

        if candidate.ace:
            score += 20
            reasons.append("participated in team ace")

        if count >= 3 and candidate.action_seconds <= 12.0:
            score += 10
            reasons.append("rapid teamfight participation")

        if candidate.died_after:
            # Support engages can be excellent even when the initiator dies, so
            # this is only a small penalty instead of the kill scorer's harsh one.
            score -= 8
            reasons.append("died after the engage")
            if candidate.ace or count >= 4:
                score += 12
                reasons.append("team converted the engage")

    elif kind == "dragon":
        score += 50 if candidate.elder else 30
        reasons.append("Elder Dragon" if candidate.elder else "team Dragon")
        if candidate.stolen:
            score += 35
            reasons.append("objective steal")

    elif kind == "baron":
        score += 35
        reasons.append("team Baron")
        if candidate.stolen:
            score += 35
            reasons.append("objective steal")

    elif kind == "manual":
        score = 100
        reasons.append("manual clip")

    else:
        score += 15
        reasons.append(candidate.kind.replace("_", " ").strip() or "game event")

    # These are always worth preserving when the corresponding event option is on.
    force_keep = (
        candidate.kill_count >= 3
        or candidate.outnumbered_kill_count >= 2
        or candidate.stolen
        or candidate.elder
        or (kind == "assist" and candidate.assist_count >= 3)
        or (kind == "assist" and candidate.ace and candidate.assist_count >= 2)
        or (
            kind == "assist"
            and candidate.assist_count >= 1
            and candidate.min_health_percent is not None
            and candidate.min_health_percent <= 10
        )
        or kind == "manual"
    )
    keep = True if not enabled else (force_keep or score >= threshold)
    return ScoreResult(score=max(0, score), reasons=tuple(reasons), keep=keep, threshold=threshold)
