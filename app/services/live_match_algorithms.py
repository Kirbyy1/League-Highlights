from __future__ import annotations

"""Pure, testable Live Match analysis helpers.

This module deliberately has no Qt or network imports.  The live scout gathers
Riot data; these helpers turn that data into conservative, explainable
comparisons and tags.  Keeping the decision logic here lets us unit-test it
without a running League client or a Riot API key.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from math import log10
from typing import Any, Iterable


ROLE_NAMES = {
    "TOP": "Top",
    "JUNGLE": "Jungle",
    "MIDDLE": "Mid",
    "BOTTOM": "AD Carry",
    "UTILITY": "Support",
    "": "Unknown",
}

_CATEGORY_ORDER = {
    "encounter": 1000,
    "champion": 930,
    "role": 900,
    "matchup": 870,
    "timeline": 830,
    "premade": 790,
    "form": 750,
    "session": 710,
    "rank": 670,
    "local_percentile": 630,
    "season": 590,
    "general": 500,
}

_GROUP_TO_CATEGORY = {
    "encounter": "encounter",
    "champion": "champion",
    "champ_pool": "champion",
    "champion_role": "champion",
    "role": "role",
    "role_status": "role",
    "matchup": "matchup",
    "premade": "premade",
    "session": "session",
    "form": "form",
    "rank": "rank",
    "season": "season",
    "local_percentile": "local_percentile",
    "lane_state": "timeline",
    "lane_kills": "timeline",
    "lane_safety": "timeline",
    "jungle_early": "timeline",
    "jungle_style": "timeline",
    "objective_style": "timeline",
    "support_style": "timeline",
    "roam": "timeline",
    "game_length": "timeline",
    "lead_conversion": "timeline",
    "objective_special": "timeline",
    "deaths": "timeline",
    "damage": "timeline",
    "farm": "timeline",
    "vision": "timeline",
    "impact": "timeline",
}

# Tags in the same set cannot coexist.  This is in addition to the explicit
# ``_group`` field and protects against old cached/tag-producing code that used
# slightly different group names.
_CONTRADICTION_SETS = (
    frozenset({"SAFE PLAYER", "HIGH DEATH RATE", "EARLY DEATH RISK"}),
    frozenset({"LANE BULLY", "STRUGGLES IN LANE", "LANE VULNERABLE"}),
    frozenset({"STRONG FORM", "POOR FORM"}),
    frozenset({"STRONG SEASON", "ROUGH SEASON"}),
    frozenset({"FIRST-TIME RANKED PICK", "LOW EXPERIENCE", "COMFORT PICK", "CHAMPION MAIN", "ONE-TRICK"}),
    frozenset({"EARLY GANKER", "LEVEL-3 GANKER", "FULL-CLEAR PLAYER", "FARMING JUNGLER"}),
    frozenset({"ROAMING SUPPORT", "LANE-FOCUSED"}),
    frozenset({"FRESH SESSION", "BACK-TO-BACK", "WARMED UP", "LONG SESSION", "RETURNING PLAYER", "FIRST RANKED TODAY"}),
)


@dataclass(frozen=True, slots=True)
class Confidence:
    score: int
    label: str


def infer_category(group: str, text: str = "") -> str:
    group_key = str(group or "").strip().casefold()
    if group_key in _GROUP_TO_CATEGORY:
        return _GROUP_TO_CATEGORY[group_key]
    upper = str(text or "").upper()
    if upper.startswith("SEEN ") or upper in {"ALLY BEFORE", "ENEMY BEFORE", "PLAYED BEFORE"}:
        return "encounter"
    if "PREMADE" in upper or upper.endswith(" DUO"):
        return "premade"
    if upper in {"MAIN ROLE", "OFFROLE", "SECONDARY ROLE", "LIKELY OFF-ROLE", "FLEX ROLE", "ROLE UNCLEAR"}:
        return "role"
    if upper in {"ONE-TRICK", "CHAMPION MAIN", "COMFORT PICK", "LOW EXPERIENCE", "FIRST-TIME RANKED PICK", "RETURNING PICK", "ROLE-SWAPPED PICK", "SMALL CHAMPION POOL"}:
        return "champion"
    return "general"


def evidence_confidence(
    evidence_games: int,
    *,
    priority: int = 50,
    relevance: float = 1.0,
    consistency: float = 1.0,
    recency: float = 1.0,
    exact_evidence: bool = False,
) -> Confidence:
    """Return a conservative 0-100 confidence score.

    Exact evidence is used for factual relationships such as a shared match or
    a current rank.  Statistical tendencies still need sample size, even when
    their threshold is strong.
    """

    evidence = max(0, int(evidence_games or 0))
    sample_component = min(evidence / 12.0, 1.0) * 42.0
    if exact_evidence and evidence:
        sample_component = max(sample_component, 28.0)
    score = (
        sample_component
        + max(0.0, min(float(priority), 100.0)) * 0.24
        + max(0.0, min(float(relevance), 1.0)) * 18.0
        + max(0.0, min(float(recency), 1.0)) * 10.0
        + max(0.0, min(float(consistency), 1.0)) * 6.0
    )
    score_int = int(round(max(0.0, min(score, 100.0))))

    # A tiny statistical sample can never be labelled high confidence.
    if not exact_evidence:
        if evidence <= 2:
            score_int = min(score_int, 59)
        elif evidence <= 5:
            score_int = min(score_int, 74)

    if score_int >= 80:
        label = "High"
    elif score_int >= 60:
        label = "Medium"
    else:
        label = "Early signal"
    return Confidence(score_int, label)


def make_evidence_tag(
    text: str,
    tone: str = "neutral",
    detail: str = "",
    *,
    priority: int = 50,
    group: str = "",
    category: str = "",
    evidence_games: int = 0,
    relevance: float = 1.0,
    consistency: float = 1.0,
    recency: float = 1.0,
    exact_evidence: bool = False,
) -> dict[str, Any]:
    category_value = category or infer_category(group, text)
    confidence = evidence_confidence(
        evidence_games,
        priority=priority,
        relevance=relevance,
        consistency=consistency,
        recency=recency,
        exact_evidence=exact_evidence,
    )
    detail_text = str(detail or "").strip()
    evidence_text = (
        f"Evidence: {int(evidence_games)} ranked game(s)"
        if evidence_games
        else "Evidence: current profile data"
    )
    confidence_line = (
        f"Confidence: {confidence.label} ({confidence.score}/100) · {evidence_text}"
    )
    tooltip = f"{detail_text}\n{confidence_line}" if detail_text else confidence_line
    return {
        "text": str(text or "").strip(),
        "tone": str(tone or "neutral"),
        "tooltip": tooltip,
        "confidence": confidence.label,
        "confidence_score": confidence.score,
        "evidence_games": int(evidence_games or 0),
        "category": category_value,
        "_priority": int(priority),
        "_group": str(group or ""),
    }


def _contradiction_key(text: str) -> str:
    upper = str(text or "").upper()
    for index, group in enumerate(_CONTRADICTION_SETS):
        if upper in group:
            return f"conflict:{index}"
    return ""


def _tag_sort_score(tag: dict[str, Any]) -> float:
    category = str(tag.get("category", "") or infer_category(tag.get("_group", ""), tag.get("text", "")))
    return (
        float(_CATEGORY_ORDER.get(category, _CATEGORY_ORDER["general"]))
        + float(tag.get("_priority", 50) or 50) * 2.5
        + float(tag.get("confidence_score", 50) or 50)
    )


def prioritize_tags(tags: Iterable[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    """Deduplicate, remove contradictions and put the useful tags first."""

    normalized: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    for raw in tags:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text", "") or "").strip()
        if not text or text in seen_text:
            continue
        seen_text.add(text)
        tag = dict(raw)
        tag.setdefault("tone", "neutral")
        tag.setdefault("_priority", 50)
        tag.setdefault("_group", "")
        tag.setdefault("category", infer_category(tag.get("_group", ""), text))
        if "confidence_score" not in tag:
            confidence = evidence_confidence(
                int(tag.get("evidence_games", 0) or 0),
                priority=int(tag.get("_priority", 50) or 50),
                exact_evidence=str(tag.get("category")) in {"encounter", "premade", "rank"},
            )
            tag["confidence_score"] = confidence.score
            tag["confidence"] = confidence.label
        normalized.append(tag)

    normalized.sort(key=_tag_sort_score, reverse=True)

    selected: list[dict[str, Any]] = []
    used_groups: set[str] = set()
    used_conflicts: set[str] = set()
    for tag in normalized:
        group = str(tag.get("_group", "") or "")
        conflict = _contradiction_key(str(tag.get("text", "")))
        if group and group in used_groups:
            continue
        if conflict and conflict in used_conflicts:
            continue
        selected.append(tag)
        if group:
            used_groups.add(group)
        if conflict:
            used_conflicts.add(conflict)

    # The first visible row should be diverse and actionable.  Reserve at most
    # one of the first three slots for each major category, then fill normally.
    preferred = ("encounter", "champion", "role", "matchup", "timeline", "premade", "form", "session")
    visible: list[dict[str, Any]] = []
    remaining = list(selected)
    for category in preferred:
        if len(visible) >= 3:
            break
        match = next((tag for tag in remaining if str(tag.get("category", "")) == category), None)
        if match is not None:
            visible.append(match)
            remaining.remove(match)
    while len(visible) < min(3, len(selected)) and remaining:
        visible.append(remaining.pop(0))

    ordered = visible + [tag for tag in selected if tag not in visible]
    output: list[dict[str, Any]] = []
    for tag in ordered[: max(0, int(limit))]:
        output.append(
            {
                key: value
                for key, value in tag.items()
                if key not in {"_sort_score"}
            }
        )
    return output


def most_valid_tags(
    tags: Iterable[dict[str, Any]],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return every finding that clears a strict evidence threshold.

    There is intentionally no default per-player tag cap.  A player may show
    three or more tags, but only when each individual tag is strongly supported
    by a sufficiently large sample.  Exact factual relationships such as a
    verified completed encounter are allowed with a smaller sample because they
    are facts rather than statistical tendencies.

    ``limit`` remains available for tests or future compact surfaces, but the
    Live Match cards call this function without a limit.
    """

    candidates = prioritize_tags(tags, limit=64)
    accepted: list[dict[str, Any]] = []

    for raw in candidates:
        tag = dict(raw)
        text = str(tag.get("text", "") or "").strip().upper()
        category = str(
            tag.get("category", "")
            or infer_category(tag.get("_group", ""), text)
        ).strip().casefold()
        evidence = int(tag.get("evidence_games", 0) or 0)
        confidence = int(tag.get("confidence_score", 0) or 0)

        valid = False
        validation_rule = ""

        # Encounters are exact completed-match facts, not tendencies.
        if text in {"ALLY BEFORE", "ENEMY BEFORE", "PLAYED BEFORE"} or text.startswith("SEEN "):
            valid = evidence >= 1 and confidence >= 78
            validation_rule = "verified completed encounter"

        # Role classification needs a meaningful ranked-role sample.
        elif text == "OFFROLE":
            valid = evidence >= 10 and confidence >= 82
            validation_rule = "10+ ranked games and high role confidence"

        # Repeated teammates need several independent shared games.
        elif category == "premade":
            valid = evidence >= 4 and confidence >= 84
            validation_rule = "4+ verified shared games"

        # Champion identity is useful only after a sizeable recent sample.
        elif category == "champion":
            valid = evidence >= 10 and confidence >= 84
            validation_rule = "10+ ranked games and high champion confidence"

        # Role and lane comparisons require both a stable role assignment and
        # enough recent ranked history to avoid one-session noise.
        elif category in {"role", "matchup"}:
            valid = evidence >= 12 and confidence >= 86
            validation_rule = "12+ ranked games and very high confidence"

        # Behavioural and form labels are statistical tendencies and therefore
        # receive the strictest normal threshold.
        elif category in {"timeline", "form", "session"}:
            valid = evidence >= 12 and confidence >= 88
            validation_rule = "12+ ranked games and very high confidence"

        else:
            valid = evidence >= 15 and confidence >= 90
            validation_rule = "15+ ranked games and exceptional confidence"

        if not valid:
            continue

        tag["validation"] = "strict-large-sample"
        tag["validation_rule"] = validation_rule
        accepted.append(tag)

    if limit is not None:
        return accepted[: max(0, int(limit))]
    return accepted


def champion_intelligence_tags(
    analysis: dict[str, Any],
    mastery: dict[str, Any],
    assigned_role: str,
) -> list[dict[str, Any]]:
    sample = int(analysis.get("sample_games", 0) or 0)
    champion_games = int(analysis.get("champion_games", 0) or 0)
    champion_share = float(analysis.get("champion_share", 0) or 0)
    champion_wr = analysis.get("champion_win_rate")
    unique = int(analysis.get("unique_champions", 0) or 0)
    points = int(mastery.get("mastery_points", 0) or 0)
    level = int(mastery.get("mastery_level", 0) or 0)
    rank = mastery.get("mastery_rank")
    last_days = mastery.get("mastery_last_play_days")
    tags: list[dict[str, Any]] = []

    detail_base = f"Mastery {level} · {points:,} points · {champion_games}/{sample} recent ranked games"
    if champion_wr is not None and champion_games:
        detail_base += f" · {float(champion_wr):.0f}% WR"

    if sample >= 10 and champion_share >= 0.70 and unique <= 4:
        tags.append(make_evidence_tag(
            "ONE-TRICK", "positive", detail_base,
            priority=100, group="champion", category="champion",
            evidence_games=sample, relevance=1.0, consistency=min(1.0, champion_share + 0.2),
        ))
    elif (points >= 250_000 or rank == 1) and sample >= 6 and (champion_share >= 0.30 or champion_games >= 4):
        tags.append(make_evidence_tag(
            "CHAMPION MAIN", "positive", detail_base,
            priority=98, group="champion", category="champion",
            evidence_games=sample, exact_evidence=False,
        ))
    elif points >= 75_000 or champion_games >= 3:
        tags.append(make_evidence_tag(
            "COMFORT PICK", "positive", detail_base,
            priority=90, group="champion", category="champion",
            evidence_games=max(sample, champion_games),
        ))
    elif sample >= 8 and champion_games == 0 and points < 6_000 and level <= 2:
        tags.append(make_evidence_tag(
            "FIRST-TIME RANKED PICK", "negative",
            f"No current-champion game in the last {sample} ranked matches · mastery {level} with {points:,} points",
            priority=99, group="champion", category="champion",
            evidence_games=sample,
        ))
    elif sample >= 6 and champion_games == 0 and points < 25_000:
        tags.append(make_evidence_tag(
            "LOW EXPERIENCE", "warning",
            f"No current-champion game in the last {sample} ranked matches · mastery {level} with {points:,} points",
            priority=94, group="champion", category="champion",
            evidence_games=sample,
        ))
    elif last_days is not None and int(last_days) >= 120 and champion_games <= 1:
        tags.append(make_evidence_tag(
            "RETURNING PICK", "warning",
            f"Last recorded mastery activity was about {int(last_days)} days ago · {champion_games} recent ranked game(s)",
            priority=82, group="champion", category="champion",
            evidence_games=max(sample, 1),
        ))

    champion_roles = {
        str(role or "").upper(): int(count or 0)
        for role, count in dict(analysis.get("champion_role_counts", {}) or {}).items()
    }
    if champion_games >= 3 and champion_roles:
        usual_role, usual_count = max(champion_roles.items(), key=lambda item: item[1])
        if (
            assigned_role
            and usual_role
            and assigned_role != usual_role
            and usual_count / max(champion_games, 1) >= 0.67
        ):
            tags.append(make_evidence_tag(
                "ROLE-SWAPPED PICK", "warning",
                f"Recent games on this champion were mainly {ROLE_NAMES.get(usual_role, usual_role)} ({usual_count}/{champion_games}); assigned now to {ROLE_NAMES.get(assigned_role, assigned_role)}",
                priority=92, group="champion_role", category="champion",
                evidence_games=champion_games,
            ))

    if sample >= 12 and unique <= 3:
        tags.append(make_evidence_tag(
            "SMALL CHAMPION POOL", "neutral",
            f"Used {unique} champion(s) across {sample} recent ranked games",
            priority=74, group="champ_pool", category="champion",
            evidence_games=sample,
        ))
    elif sample >= 12 and unique >= 10:
        tags.append(make_evidence_tag(
            "WIDE CHAMPION POOL", "neutral",
            f"Used {unique} champions across {sample} recent ranked games",
            priority=64, group="champ_pool", category="champion",
            evidence_games=sample,
        ))
    return tags


def derive_session_metrics(samples: list[dict[str, Any]], now_timestamp: float | None = None) -> dict[str, Any]:
    now = float(now_timestamp) if now_timestamp is not None else datetime.now(tz=timezone.utc).timestamp()
    matches: list[tuple[float, float]] = []
    for sample in samples:
        info = sample.get("info", {}) if isinstance(sample, dict) else {}
        if not isinstance(info, dict):
            continue
        duration = float(info.get("gameDuration", 0) or 0)
        start = float(info.get("gameStartTimestamp", 0) or 0) / 1000.0
        end = float(info.get("gameEndTimestamp", 0) or 0) / 1000.0
        if not end and start:
            end = start + duration
        if start and end:
            matches.append((start, end))
    matches.sort(key=lambda pair: pair[1], reverse=True)
    if not matches:
        return {
            "session_games": 0,
            "session_span_minutes": 0,
            "last_ranked_minutes_ago": None,
            "days_since_last_ranked": None,
        }

    latest_start, latest_end = matches[0]
    session = [(latest_start, latest_end)]
    newer_start = latest_start
    for older_start, older_end in matches[1:]:
        gap_minutes = max(0.0, (newer_start - older_end) / 60.0)
        if gap_minutes > 120.0:
            break
        session.append((older_start, older_end))
        newer_start = older_start

    oldest_start = min(start for start, _ in session)
    newest_end = max(end for _, end in session)
    last_minutes = max(0, int((now - latest_end) // 60))
    return {
        "session_games": len(session),
        "session_span_minutes": int(max(0.0, (newest_end - oldest_start) / 60.0)),
        "last_ranked_minutes_ago": last_minutes,
        "days_since_last_ranked": round(last_minutes / 1440.0, 1),
    }


def session_tags(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    sample = int(analysis.get("sample_games", 0) or 0)
    games_today = int(analysis.get("games_today", 0) or 0)
    session_games = int(analysis.get("session_games", 0) or 0)
    session_span = int(analysis.get("session_span_minutes", 0) or 0)
    last_minutes = analysis.get("last_ranked_minutes_ago")
    tags: list[dict[str, Any]] = []
    if not sample:
        return tags

    if last_minutes is not None and int(last_minutes) >= 7 * 24 * 60:
        tags.append(make_evidence_tag(
            "RETURNING PLAYER", "neutral",
            f"No Solo/Duo game found for about {float(last_minutes) / 1440.0:.1f} days",
            priority=97, group="session", category="session",
            evidence_games=sample,
        ))
    elif last_minutes is not None and int(last_minutes) >= 8 * 60:
        tags.append(make_evidence_tag(
            "FRESH SESSION", "neutral",
            f"Most recent Solo/Duo game ended about {int(last_minutes) // 60} hours ago",
            priority=92, group="session", category="session",
            evidence_games=sample,
        ))
    elif session_games >= 6 or session_span >= 240:
        tags.append(make_evidence_tag(
            "LONG SESSION", "warning",
            f"Current ranked session contains {session_games} games over about {session_span} minutes",
            priority=96, group="session", category="session",
            evidence_games=max(session_games, 1),
        ))
    elif last_minutes is not None and int(last_minutes) <= 20:
        tags.append(make_evidence_tag(
            "BACK-TO-BACK", "warning",
            f"Last Solo/Duo game ended about {int(last_minutes)} minutes ago",
            priority=94, group="session", category="session",
            evidence_games=max(session_games, 1), exact_evidence=True,
        ))
    elif 2 <= session_games <= 5 and last_minutes is not None and int(last_minutes) <= 180:
        tags.append(make_evidence_tag(
            "WARMED UP", "neutral",
            f"Already played {session_games} games in the current ranked session",
            priority=78, group="session", category="session",
            evidence_games=session_games,
        ))
    elif games_today == 0:
        tags.append(make_evidence_tag(
            "FIRST RANKED TODAY", "neutral",
            "No earlier Solo/Duo game was found today; the current live game is not yet in Match-v5 history",
            priority=80, group="session", category="session",
            evidence_games=sample,
        ))
    return tags


def role_timeline_tags(
    role: str,
    analysis: dict[str, Any],
    timeline: dict[str, Any],
) -> list[dict[str, Any]]:
    role = str(role or "").upper()
    sample = int(analysis.get("sample_games", 0) or 0)
    timeline_games = int(timeline.get("timeline_games", 0) or 0)
    if not timeline_games:
        return []

    lead = float(timeline.get("lead_at_10_rate", 0) or 0)
    behind = float(timeline.get("behind_at_10_rate", 0) or 0)
    gold_diff = float(timeline.get("avg_gold_diff_at_10", 0) or 0)
    cs_diff = float(timeline.get("avg_cs_diff_at_10", 0) or 0)
    early_death = float(timeline.get("early_death_rate", 0) or 0)
    solo_kill = float(timeline.get("solo_kill_rate", 0) or 0)
    solo_death = float(timeline.get("solo_death_rate", 0) or 0)
    early_kp = float(timeline.get("early_kill_participation_rate", 0) or 0)
    roam = float(timeline.get("early_roam_rate", 0) or 0)
    objective = float(timeline.get("early_objective_rate", 0) or 0)
    gank5 = float(timeline.get("gank_before_5_rate", 0) or 0)
    ward10 = float(timeline.get("ward_before_10_rate", 0) or 0)
    jungle_cs6 = float(timeline.get("avg_jungle_cs_at_6", 0) or 0)
    avg_cs = float(analysis.get("avg_cs_min", 0) or 0)
    avg_deaths = float(analysis.get("avg_deaths", 0) or 0)
    damage_share = float(analysis.get("avg_team_damage_share", 0) or 0)
    vision = float(analysis.get("avg_vision_min", 0) or 0)
    first_blood = float(analysis.get("first_blood_rate", 0) or 0)
    tags: list[dict[str, Any]] = []

    def add(text: str, tone: str, detail: str, priority: int, group: str) -> None:
        tags.append(make_evidence_tag(
            text, tone, detail,
            priority=priority, group=group, category="timeline",
            evidence_games=timeline_games,
            relevance=1.0,
            consistency=min(1.0, max(0.45, timeline_games / 4.0)),
        ))

    if role == "TOP":
        if lead >= 60 and gold_diff >= 250:
            add("LANE BULLY", "warning", f"Ahead at 10 in {lead:.0f}% of sampled games · average gold difference {gold_diff:+.0f}", 97, "lane_state")
        elif behind >= 60 and gold_diff <= -250:
            add("LANE VULNERABLE", "negative", f"Behind at 10 in {behind:.0f}% of sampled games · average gold difference {gold_diff:+.0f}", 94, "lane_state")
        if solo_kill >= 35:
            add("SOLO-KILL THREAT", "warning", f"Recorded an unassisted pre-10-minute kill in {solo_kill:.0f}% of sampled games", 96, "lane_kills")
        if early_death >= 50 or solo_death >= 35:
            add("EARLY DEATH RISK", "negative", f"Early death in {early_death:.0f}% of sampled games · solo deaths {solo_death:.0f}%", 94, "lane_safety")

    elif role == "JUNGLE":
        invader_kills = int(timeline.get("invader_kills", 0) or 0)
        invader_deaths = int(timeline.get("invader_deaths", 0) or 0)
        invader_games = int(timeline.get("invader_games", 0) or 0)
        if invader_deaths >= 2 and invader_deaths > invader_kills:
            add("RISKY INVADES", "negative", f"Enemy-side early fights: {invader_kills} kill(s), {invader_deaths} death(s)", 99, "jungle_early")
        elif invader_games >= 2 or invader_kills >= 2:
            add("EARLY INVADER", "warning", f"Enemy-side early fighting in {invader_games} sampled game(s)", 97, "jungle_early")
        if gank5 >= 50:
            add("LEVEL-3 GANKER", "warning", f"Participated in a champion kill before 5:00 in {gank5:.0f}% of sampled games", 96, "jungle_style")
        elif gank5 <= 20 and jungle_cs6 >= 30:
            add("FULL-CLEAR PLAYER", "neutral", f"Average jungle CS at 6:00: {jungle_cs6:.0f} · pre-5:00 kill participation {gank5:.0f}%", 90, "jungle_style")
        if objective >= 50:
            add("OBJECTIVE FOCUSED", "positive", f"Participated in an objective before 15:00 in {objective:.0f}% of sampled games", 91, "objective_style")

    elif role == "MIDDLE":
        if roam >= 50:
            add("EARLY ROAMER", "warning", f"Recorded an early kill participation away from mid lane in {roam:.0f}% of sampled games", 96, "roam")
        if lead >= 60 and gold_diff >= 200:
            add("LANE DOMINANT", "warning", f"Ahead at 10 in {lead:.0f}% of sampled games · average gold difference {gold_diff:+.0f}", 94, "lane_state")
        if solo_kill >= 35 or first_blood >= 30:
            add("FIRST-BLOOD THREAT", "warning", f"Solo-kill signal {solo_kill:.0f}% · first-blood involvement {first_blood:.0f}%", 93, "lane_kills")

    elif role == "BOTTOM":
        if avg_cs >= 7.5 or cs_diff >= 8:
            add("STRONG FARMER", "positive", f"{avg_cs:.1f} CS/min · average 10-minute CS difference {cs_diff:+.1f}", 90, "farm")
        if avg_deaths <= 4.5 and early_death <= 20:
            add("SAFE POSITIONING", "positive", f"{avg_deaths:.1f} deaths/game · early death in {early_death:.0f}% of timeline games", 88, "deaths")
        if damage_share >= 28:
            add("DAMAGE CARRY", "positive", f"Averages {damage_share:.0f}% of team champion damage", 92, "damage")

    elif role == "UTILITY":
        if roam >= 50:
            add("ROAMING SUPPORT", "positive", f"Early kill participation away from bottom lane in {roam:.0f}% of sampled games", 96, "roam")
        elif roam <= 20 and lead >= 50:
            add("LANE-FOCUSED", "neutral", f"Low early-roam rate ({roam:.0f}%) with lane lead in {lead:.0f}% of sampled games", 82, "roam")
        if vision >= 1.5 or ward10 >= 50:
            add("VISION CONTROL", "positive", f"{vision:.2f} vision score/min · early ward event in {ward10:.0f}% of sampled games", 91, "vision")
        if early_kp >= 50 or first_blood >= 30:
            add("AGGRESSIVE SUPPORT", "warning", f"Early kill participation {early_kp:.0f}% · first-blood involvement {first_blood:.0f}%", 92, "support_style")
        elif sample >= 8 and float(analysis.get("avg_kp", 0) or 0) < 45:
            add("LOW PARTICIPATION", "negative", f"Average kill participation is {float(analysis.get('avg_kp', 0) or 0):.0f}%", 84, "support_style")

    comeback = float(timeline.get("comeback_rate", 0) or 0)
    throw_rate = float(timeline.get("throw_rate", 0) or 0)
    if comeback >= 50:
        add("COMEBACK PLAYER", "positive", f"Won after being materially behind at 15:00 in {comeback:.0f}% of sampled games", 84, "lead_conversion")
    elif throw_rate >= 50:
        add("THROWS LEADS", "negative", f"Lost after holding a meaningful 15-minute lead in {throw_rate:.0f}% of sampled games", 89, "lead_conversion")
    return tags


_TIER_VALUE = {
    "UNRANKED": 0,
    "IRON": 1,
    "BRONZE": 2,
    "SILVER": 3,
    "GOLD": 4,
    "PLATINUM": 5,
    "EMERALD": 6,
    "DIAMOND": 7,
    "MASTER": 8,
    "GRANDMASTER": 9,
    "CHALLENGER": 10,
}
_DIVISION_VALUE = {"IV": 0, "III": 1, "II": 2, "I": 3}


def rank_numeric(profile: dict[str, Any]) -> float:
    tier = str(profile.get("tier", "UNRANKED") or "UNRANKED").upper()
    division = str(profile.get("division", "") or "").upper()
    lp = int(profile.get("lp", 0) or 0)
    return _TIER_VALUE.get(tier, 0) * 400.0 + _DIVISION_VALUE.get(division, 0) * 100.0 + min(lp, 100)


def profile_strength(profile: dict[str, Any]) -> float:
    rank_component = min(rank_numeric(profile) / 4000.0, 1.0) * 40.0
    points = int(profile.get("mastery_points", 0) or 0)
    mastery_component = min(log10(max(points, 1)) / 6.0, 1.0) * 18.0
    champion_games = int(profile.get("champion_games", 0) or 0)
    champion_component = min(champion_games / 10.0, 1.0) * 10.0
    wr = profile.get("recent_win_rate")
    form_component = 10.0
    if wr is not None:
        form_component = max(0.0, min(20.0, 10.0 + (float(wr) - 50.0) * 0.35))
    role_state = str(profile.get("role_state", "") or "")
    role_component = {
        "main": 12.0,
        "secondary": 8.0,
        "flex": 6.0,
        "unclear": 4.0,
        "off_role": 0.0,
    }.get(role_state, 4.0)
    return round(rank_component + mastery_component + champion_component + form_component + role_component, 1)


def _player_label(player: dict[str, Any]) -> str:
    return str(player.get("riot_id", "") or player.get("player_key", "") or "Unknown player")


def pair_lane_opponents(
    roster: dict[str, Any],
    profiles: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Pair players by the already assigned team-wide role."""

    by_team: dict[str, dict[str, tuple[dict[str, Any], dict[str, Any]]]] = {"allies": {}, "enemies": {}}
    for team_key in ("allies", "enemies"):
        for player in roster.get(team_key, ()):
            key = str(player.get("player_key", "") or "")
            profile = profiles.get(key)
            if not key or not isinstance(profile, dict):
                continue
            role = str(profile.get("assigned_role", "") or profile.get("inferred_role", "") or player.get("role", "") or "").upper()
            if role:
                by_team[team_key][role] = (player, profile)

    output: dict[str, dict[str, Any]] = {}
    for role in ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"):
        ally = by_team["allies"].get(role)
        enemy = by_team["enemies"].get(role)
        if ally is None or enemy is None:
            continue
        for own, opponent in ((ally, enemy), (enemy, ally)):
            own_player, own_profile = own
            opponent_player, opponent_profile = opponent
            own_key = str(own_player.get("player_key", ""))
            own_strength = profile_strength(own_profile)
            opponent_strength = profile_strength(opponent_profile)
            delta = round(own_strength - opponent_strength, 1)
            if delta >= 12:
                edge = "Clear profile edge"
                tone = "positive"
            elif delta >= 5:
                edge = "Slight profile edge"
                tone = "positive"
            elif delta <= -12:
                edge = "Tough profile comparison"
                tone = "warning"
            elif delta <= -5:
                edge = "Slight profile disadvantage"
                tone = "warning"
            else:
                edge = "Even profile comparison"
                tone = "neutral"

            own_rank = str(own_profile.get("rank", "Unranked") or "Unranked")
            opp_rank = str(opponent_profile.get("rank", "Unranked") or "Unranked")
            own_wr = own_profile.get("recent_win_rate")
            opp_wr = opponent_profile.get("recent_win_rate")
            explanation = [
                f"{ROLE_NAMES.get(role, role)} profile comparison",
                f"You: {own_rank} · {int(own_profile.get('mastery_points', 0) or 0):,} mastery",
                f"Opponent: {opp_rank} · {int(opponent_profile.get('mastery_points', 0) or 0):,} mastery",
            ]
            if own_wr is not None and opp_wr is not None:
                explanation.append(f"Recent ranked WR: {float(own_wr):.0f}% vs {float(opp_wr):.0f}%")
            explanation.append("This compares player history only; it is not a champion counter prediction.")

            output[own_key] = {
                "role": role,
                "role_name": ROLE_NAMES.get(role, role),
                "opponent_key": str(opponent_player.get("player_key", "")),
                "opponent_name": _player_label(opponent_player),
                "opponent_champion": str(opponent_player.get("champion", "") or "Unknown"),
                "opponent_rank": opp_rank,
                "opponent_mastery_points": int(opponent_profile.get("mastery_points", 0) or 0),
                "opponent_recent_win_rate": opp_wr,
                "opponent_role_state": str(opponent_profile.get("role_state", "") or ""),
                "own_strength": own_strength,
                "opponent_strength": opponent_strength,
                "strength_delta": delta,
                "edge": edge,
                "tone": tone,
                "tooltip": "\n".join(explanation),
            }
    return output


def matchup_tag(matchup: dict[str, Any], evidence_games: int) -> dict[str, Any] | None:
    delta = float(matchup.get("strength_delta", 0) or 0)
    if abs(delta) < 10:
        return None
    text = "PROFILE EDGE" if delta > 0 else "TOUGH PROFILE"
    return make_evidence_tag(
        text,
        str(matchup.get("tone", "neutral") or "neutral"),
        str(matchup.get("tooltip", "") or ""),
        priority=91,
        group="matchup",
        category="matchup",
        evidence_games=max(1, int(evidence_games or 0)),
    )


def premade_pair_confidence(
    together_games: int,
    consecutive_games: int,
    session_count: int,
) -> Confidence:
    together = max(0, int(together_games or 0))
    consecutive = max(0, int(consecutive_games or 0))
    sessions = max(0, int(session_count or 0))
    score = min(together * 12, 60) + min(consecutive * 12, 24) + min(sessions * 8, 16)
    score = min(score, 100)
    if score >= 75:
        return Confidence(score, "High")
    if score >= 55:
        return Confidence(score, "Medium")
    return Confidence(score, "Weak")


def premade_role_label(left_role: str, right_role: str, group_size: int = 2) -> str:
    if int(group_size) > 2:
        return f"LIKELY PREMADE {int(group_size)}"
    roles = frozenset({str(left_role or "").upper(), str(right_role or "").upper()})
    if roles == {"MIDDLE", "JUNGLE"}:
        return "MID + JUNGLE DUO"
    if roles == {"BOTTOM", "UTILITY"}:
        return "BOT DUO"
    if roles == {"TOP", "JUNGLE"}:
        return "TOP + JUNGLE DUO"
    return "PREMADE DUO"



def filter_previous_encounters(
    entries: Iterable[dict[str, Any]],
    *,
    current_game_started_at: float = 0.0,
    current_game_signature: str = "",
) -> list[dict[str, Any]]:
    """Return only genuine earlier encounters.

    V18 recorded the currently running roster immediately.  A later refresh
    could therefore label every current player as ALly/ENEMY BEFORE.  V19
    excludes entries belonging to the active game and deduplicates source
    transition copies before encounter counts are calculated.
    """

    start = max(0.0, float(current_game_started_at or 0.0))
    active_signature = str(current_game_signature or "")
    filtered: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for raw in entries:
        if not isinstance(raw, dict):
            continue
        entry = dict(raw)
        timestamp = max(0.0, float(entry.get("timestamp", 0) or 0))
        signature = str(entry.get("game_signature", "") or "")

        if active_signature and signature and signature == active_signature:
            continue
        # Keep a two-minute tolerance because game-start timestamps from the
        # local client and Spectator can differ slightly.
        if start and timestamp >= start - 120.0:
            continue

        match_id = str(entry.get("match_id", "") or "")
        if match_id:
            identity = ("match", match_id)
        else:
            # Old local entries have no match id.  Source transitions generally
            # produced copies only seconds apart, while real League games are
            # separated by substantially more than this five-minute bucket.
            identity = (
                "local",
                str(entry.get("relation", "") or ""),
                str(entry.get("my_champion", "") or "").casefold(),
                str(entry.get("champion", entry.get("their_champion", "")) or "").casefold(),
                int(timestamp // 300) if timestamp else 0,
            )
        if identity in seen:
            continue
        seen.add(identity)
        filtered.append(entry)

    filtered.sort(
        key=lambda item: float(item.get("timestamp", 0) or 0),
        reverse=True,
    )
    return filtered

def summarize_encounters(entries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    total = wins = losses = 0
    ally = enemy = ally_wins = enemy_wins = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        relation = str(entry.get("relation", "") or "")
        won = entry.get("won")
        total += 1
        if relation == "ally":
            ally += 1
        elif relation == "enemy":
            enemy += 1
        if isinstance(won, bool):
            wins += int(won)
            losses += int(not won)
            if relation == "ally":
                ally_wins += int(won)
            elif relation == "enemy":
                enemy_wins += int(won)
    return {
        "encounter_history_count": total,
        "encounter_wins": wins,
        "encounter_losses": losses,
        "encounter_ally_count": ally,
        "encounter_enemy_count": enemy,
        "encounter_ally_wins": ally_wins,
        "encounter_enemy_wins": enemy_wins,
        "encounter_win_rate": round((wins / (wins + losses)) * 100.0, 1) if wins + losses else None,
    }
