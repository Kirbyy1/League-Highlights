from __future__ import annotations

import random
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QPushButton

from app.ui import live_match_page


_ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")

_ROLE_NAMES = {
    "TOP": "Top",
    "JUNGLE": "Jungle",
    "MIDDLE": "Mid",
    "BOTTOM": "AD Carry",
    "UTILITY": "Support",
}

_BLUE_TEAM = (
    ("Ornn", "DebugTop"),
    ("Viego", "DebugJungle"),
    ("Orianna", "DebugMid"),
    ("Miss Fortune", "DebugADC"),
    ("Zilean", "DebugSupport"),
)

_RED_TEAM = (
    ("Sett", "EnemyTop"),
    ("Wukong", "EnemyJungle"),
    ("Annie", "EnemyMid"),
    ("Caitlyn", "EnemyADC"),
    ("Vel'Koz", "EnemySupport"),
)

_RANKS = (
    ("SILVER", "I"),
    ("GOLD", "IV"),
    ("GOLD", "II"),
    ("PLATINUM", "IV"),
    ("PLATINUM", "II"),
    ("EMERALD", "IV"),
    ("EMERALD", "II"),
    ("DIAMOND", "IV"),
)

_POSITIVE_TAGS = (
    "COMFORT PICK",
    "STRONG FORM",
    "OBJECTIVE FOCUSED",
    "VISION CONTROL",
    "HIGH DAMAGE",
    "GOOD MOOD",
    "TURRET DESTROYER",
    "AGGRESSIVE LANER",
)

_NEUTRAL_TAGS = (
    "FRESH SESSION",
    "WARMED UP",
    "SEEN 3X+",
    "ONE-TRICK",
    "INVADER",
)

_WARNING_TAGS = (
    "AUTOFILLED?",
    "LOW CHAMP EXPERIENCE",
    "BACK-TO-BACK",
    "SOLO-KILL THREAT",
    "3 LOSS STREAK",
)


def _rank_text(tier: str, division: str, lp: int) -> str:
    if tier in {"MASTER", "GRANDMASTER", "CHALLENGER"}:
        return f"{tier.title()} · {lp} LP"
    return f"{tier.title()} {division} · {lp} LP"


def _make_tags(rng: random.Random, count: int) -> list[dict[str, Any]]:
    tags: list[dict[str, Any]] = []

    for text in rng.sample(_POSITIVE_TAGS, k=min(2, len(_POSITIVE_TAGS))):
        tags.append(
            {
                "text": text,
                "tone": "positive",
                "tooltip": "Generated debug tag.",
            }
        )

    if count >= 3:
        text = rng.choice(_NEUTRAL_TAGS)
        tags.append(
            {
                "text": text,
                "tone": "neutral",
                "tooltip": "Generated debug tag.",
            }
        )

    if count >= 4:
        text = rng.choice(_WARNING_TAGS)
        tags.append(
            {
                "text": text,
                "tone": "warning",
                "tooltip": "Generated debug tag.",
            }
        )

    return tags[:count]


def _fake_ranked_record(
    rng: random.Random,
    *,
    allow_unranked: bool,
) -> dict[str, Any]:
    if allow_unranked and rng.random() < 0.16:
        return {
            "rank": "Unranked",
            "tier": "UNRANKED",
            "division": "",
            "lp": 0,
            "wins": 0,
            "losses": 0,
            "games": 0,
            "win_rate": None,
            "ranked_wins": 0,
            "ranked_losses": 0,
            "ranked_games": 0,
            "ranked_win_rate": None,
            "rank_state": "unranked",
        }

    tier, division = rng.choice(_RANKS)
    games = rng.randint(45, 320)
    win_rate = round(rng.uniform(43.0, 61.0), 1)
    wins = max(0, min(games, round(games * win_rate / 100.0)))
    losses = games - wins
    lp = rng.randint(0, 99)

    return {
        "rank": _rank_text(tier, division, lp),
        "tier": tier,
        "division": division,
        "lp": lp,
        "wins": wins,
        "losses": losses,
        "games": games,
        "win_rate": round((wins / games) * 100.0, 1),
        "ranked_wins": wins,
        "ranked_losses": losses,
        "ranked_games": games,
        "ranked_win_rate": round((wins / games) * 100.0, 1),
        "rank_state": "ready",
    }


def _build_fake_match(
    match_number: int,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    rng = random.Random(11_000 + match_number)

    players: list[dict[str, Any]] = []
    stats_by_key: dict[str, dict[str, Any]] = {}

    team_definitions = (
        ("ORDER", _BLUE_TEAM),
        ("CHAOS", _RED_TEAM),
    )

    for team, definitions in team_definitions:
        for slot, ((champion, base_name), role) in enumerate(
            zip(definitions, _ROLES)
        ):
            player_key = (
                f"debug:{match_number}:{team.casefold()}:{slot}"
            )
            riot_id = f"{base_name}{match_number}#DBG"
            is_active = team == "ORDER" and slot == 1

            player = {
                "player_key": player_key,
                "puuid": player_key,
                "lcu_player_id": player_key,
                "riot_id": riot_id,
                "game_name": riot_id.split("#", 1)[0],
                "tag_line": "DBG",
                "champion": champion,
                "role": role,
                "team": team,
                "is_active": is_active,
                "spells": ["Smite"] if role == "JUNGLE" else [],
                "roster_source": "debug",
            }
            players.append(player)

            sample_games = rng.choice((5, 10, 12, 20, 30))
            recent_wins = rng.randint(
                max(0, sample_games // 3),
                min(sample_games, (sample_games * 4) // 5),
            )
            recent_win_rate = round(
                (recent_wins / sample_games) * 100.0,
                1,
            )

            deaths = round(rng.uniform(2.8, 7.2), 1)
            kills = round(rng.uniform(2.0, 10.5), 1)
            assists = round(
                rng.uniform(4.0, 14.0)
                if role != "UTILITY"
                else rng.uniform(8.0, 19.0),
                1,
            )
            avg_kda = round(
                (kills + assists) / max(1.0, deaths),
                1,
            )

            ranked = _fake_ranked_record(
                rng,
                allow_unranked=True,
            )

            secondary_role = rng.choice(
                [candidate for candidate in _ROLES if candidate != role]
            )
            role_share = round(rng.uniform(0.56, 0.93), 2)
            premade_size = (
                2
                if (team == "ORDER" and slot in {1, 2})
                or (team == "CHAOS" and slot in {0, 4})
                else 1
            )

            stats = {
                "profile_schema": 30,
                "state": "ready",
                "analysis_stage": "final",
                "ranked_only": True,
                "riot_id": riot_id,
                "game_name": riot_id.split("#", 1)[0],
                "tag_line": "DBG",
                "puuid": player_key,
                "account_level": rng.randint(35, 950),
                "profile_icon_id": 0,
                **ranked,
                "sample_games": sample_games,
                "recent_wins": recent_wins,
                "recent_win_rate": recent_win_rate,
                "avg_kills": kills,
                "avg_deaths": deaths,
                "avg_assists": assists,
                "avg_kda": avg_kda,
                "avg_cs_min": round(
                    rng.uniform(5.5, 9.2)
                    if role not in {"UTILITY", "JUNGLE"}
                    else rng.uniform(1.1, 6.8),
                    1,
                ),
                "avg_kp": round(rng.uniform(0.42, 0.78), 2),
                "avg_vision_min": round(
                    rng.uniform(1.0, 2.1)
                    if role == "UTILITY"
                    else rng.uniform(0.35, 1.15),
                    2,
                ),
                "current_role": role,
                "assigned_role": role,
                "inferred_role": role,
                "role_name": _ROLE_NAMES[role],
                "main_role": role,
                "main_role_name": _ROLE_NAMES[role],
                "secondary_role": secondary_role,
                "secondary_role_name": _ROLE_NAMES[secondary_role],
                "role_share": role_share,
                "role_state": "main_role",
                "role_assignment_confidence": "high",
                "role_assignment_margin": 0.75,
                "champion_games": rng.randint(3, 85),
                "champion_win_rate": round(rng.uniform(42, 68), 1),
                "mastery_available": True,
                "mastery_level": rng.randint(4, 10),
                "mastery_points": rng.randint(18_000, 650_000),
                "mastery_rank": rng.randint(1, 12),
                "premade_size": premade_size,
                "premade_members": (
                    ["Debug premade"]
                    if premade_size > 1
                    else []
                ),
                "premade_games_together": (
                    rng.randint(3, 20)
                    if premade_size > 1
                    else 0
                ),
                "premade_win_rate": (
                    round(rng.uniform(44, 70), 1)
                    if premade_size > 1
                    else None
                ),
                "rank_source": "debug",
                "history_source": "debug",
                "mastery_source": "debug",
                "profile_source": "debug",
                "analysis_target_games": sample_games,
                "tags": _make_tags(
                    rng,
                    rng.randint(2, 4),
                ),
                "local_percentiles": {},
                "recent_match_ids": [
                    f"DEBUG_{match_number}_{slot}_{index}"
                    for index in range(sample_games)
                ],
            }
            stats_by_key[player_key] = stats

    allies = [
        player for player in players
        if player["team"] == "ORDER"
    ]
    enemies = [
        player for player in players
        if player["team"] == "CHAOS"
    ]

    roster = {
        "players": players,
        "allies": allies,
        "enemies": enemies,
        "active_team": "ORDER",
        "game_started_at": 0,
        "game_id": f"DEBUG_MATCH_{match_number}",
        "queue_id": 420,
        "roster_source": "debug",
        "gameflow_phase": "InProgress",
    }
    return roster, stats_by_key


def _install_debug_controls(page: Any) -> None:
    root = page.layout()
    if root is None or root.count() == 0:
        return

    header = root.itemAt(0).layout()
    if header is None:
        return

    page.debug_match_button = QPushButton("Debug Match")
    page.debug_match_button.setObjectName("DarkButton")
    page.debug_match_button.setToolTip(
        "Stop live scouting and generate ten fake player cards."
    )
    page.debug_match_button.clicked.connect(
        page.generate_fake_match
    )

    page.resume_live_button = QPushButton("Resume Live")
    page.resume_live_button.setObjectName("DarkButton")
    page.resume_live_button.setToolTip(
        "Remove fake data and resume the real Live Match scout."
    )
    page.resume_live_button.clicked.connect(
        page.resume_live_scouting
    )
    page.resume_live_button.hide()

    refresh_index = max(0, header.count() - 1)
    header.insertWidget(
        refresh_index,
        page.debug_match_button,
        0,
        Qt.AlignmentFlag.AlignBottom,
    )
    header.insertWidget(
        refresh_index + 1,
        page.resume_live_button,
        0,
        Qt.AlignmentFlag.AlignBottom,
    )

    # The five cards already make team ownership obvious by row. Removing the
    # two text headers saves vertical space in both live and debug modes.
    for section in (page.allies_section, page.enemies_section):
        section.heading.hide()
        section.summary.hide()

        # Remove only the large outer team container. Individual player-card
        # borders remain untouched.
        section.setObjectName("BorderlessLiveTeamSection")
        section.setStyleSheet(
            """
            QFrame#BorderlessLiveTeamSection {
                background: transparent;
                border: none;
                border-radius: 0;
            }
            """
        )

        section_layout = section.layout()
        if section_layout is not None:
            section_layout.setContentsMargins(0, 0, 0, 0)
            section_layout.setSpacing(8)


def _install_equal_team_columns() -> None:
    """Force both team rows to use the same five equal-width columns."""

    team_class = live_match_page.TeamSection
    if getattr(team_class, "_equal_columns_installed", False):
        return

    original_add_players = team_class.add_players
    original_resize_event = team_class.resizeEvent

    def equalize(section: Any) -> None:
        cards = list(getattr(section, "player_cards", ()) or ())
        if not cards:
            return

        layout = section.cards_layout
        margins = layout.contentsMargins()
        spacing = max(0, int(layout.horizontalSpacing()))

        available = (
            section.contentsRect().width()
            - margins.left()
            - margins.right()
            - spacing * max(0, len(cards) - 1)
        )
        if available <= 0:
            return

        card_width = max(150, available // len(cards))

        for column in range(5):
            layout.setColumnStretch(column, 1)

        for card in cards:
            # Equal minimum and maximum widths stop long names or statistics
            # from making one card wider than the matching column below it.
            card.setFixedWidth(card_width)
            layout.setAlignment(
                card,
                Qt.AlignmentFlag.AlignHCenter
                | Qt.AlignmentFlag.AlignTop,
            )

    def equal_add_players(
        section: Any,
        cards: list[Any],
    ) -> None:
        original_add_players(section, cards)
        QTimer.singleShot(
            0,
            lambda: equalize(section),
        )

    def equal_resize_event(
        section: Any,
        event: Any,
    ) -> None:
        original_resize_event(section, event)
        QTimer.singleShot(
            0,
            lambda: equalize(section),
        )

    team_class.add_players = equal_add_players
    team_class.resizeEvent = equal_resize_event
    team_class._equal_columns_installed = True


def install_fake_match_debug() -> None:
    """Patch LiveMatchPage with a persistent fake-match debug mode."""

    _install_equal_team_columns()

    page_class = live_match_page.LiveMatchPage
    if getattr(page_class, "_fake_match_debug_installed", False):
        return

    original_init = page_class.__init__
    original_set_roster = page_class.set_roster
    original_apply_player_stats = page_class.apply_player_stats
    original_set_status = page_class.set_status
    original_refresh_now = page_class.refresh_now

    def debug_init(self: Any, *args: Any, **kwargs: Any) -> None:
        self._fake_match_active = False
        self._fake_match_injecting = False
        self._fake_match_counter = 0
        original_init(self, *args, **kwargs)
        _install_debug_controls(self)

    def guarded_set_roster(
        self: Any,
        payload: dict[str, Any],
    ) -> None:
        if (
            self._fake_match_active
            and not self._fake_match_injecting
        ):
            return
        original_set_roster(self, payload)

    def guarded_apply_player_stats(
        self: Any,
        player_key: str,
        stats: dict[str, Any],
    ) -> None:
        if (
            self._fake_match_active
            and not self._fake_match_injecting
        ):
            return
        original_apply_player_stats(
            self,
            player_key,
            stats,
        )

    def guarded_set_status(
        self: Any,
        state: str,
        message: str,
    ) -> None:
        if (
            self._fake_match_active
            and not self._fake_match_injecting
        ):
            return

        message = str(message or "").replace(
            "30-game",
            "Analysis",
        )

        normalized_state = str(state or "").casefold()
        if normalized_state == "loading":
            for card in self._cards.values():
                begin_refresh = getattr(
                    card,
                    "begin_tag_refresh",
                    None,
                )
                if callable(begin_refresh):
                    begin_refresh()

        original_set_status(self, state, message)

        if normalized_state == "ready":
            for card in self._cards.values():
                commit = getattr(card, "commit_tags", None)
                if callable(commit):
                    commit()

    def guarded_refresh_now(self: Any) -> None:
        if self._fake_match_active:
            return
        original_refresh_now(self)

    def generate_fake_match(self: Any) -> None:
        self._fake_match_counter += 1
        self._fake_match_active = True

        self.scout.stop()
        self.refresh_button.setEnabled(False)
        self.debug_match_button.setText("New Fake Match")
        self.resume_live_button.show()
        self.api_banner.hide()

        # Fake-mode state is already obvious from the buttons and generated
        # names, so the extra "Debug match #..." strip only wastes height.
        self.status_bar.hide()

        roster, stats_by_key = _build_fake_match(
            self._fake_match_counter
        )

        self._fake_match_injecting = True
        try:
            original_set_roster(self, roster)
            for player_key, stats in stats_by_key.items():
                original_apply_player_stats(
                    self,
                    player_key,
                    stats,
                )

            for card in self._cards.values():
                commit = getattr(card, "commit_tags", None)
                if callable(commit):
                    commit()

            original_set_status(
                self,
                "ready",
                (
                    f"Debug match #{self._fake_match_counter} "
                    "— generated fake data"
                ),
            )
        finally:
            self._fake_match_injecting = False

    def resume_live_scouting(self: Any) -> None:
        self._fake_match_active = False
        self._fake_match_injecting = True
        try:
            original_set_roster(
                self,
                {
                    "players": [],
                    "allies": [],
                    "enemies": [],
                    "active_team": "",
                },
            )
            original_set_status(
                self,
                "waiting",
                "Returning to live scouting",
            )
        finally:
            self._fake_match_injecting = False

        self.refresh_button.setEnabled(True)
        self.debug_match_button.setText("Debug Match")
        self.resume_live_button.hide()
        self.status_bar.show()
        self._sync_api_banner()
        self.scout.start()

    page_class.__init__ = debug_init
    page_class.set_roster = guarded_set_roster
    page_class.apply_player_stats = guarded_apply_player_stats
    page_class.set_status = guarded_set_status
    page_class.refresh_now = guarded_refresh_now
    page_class.generate_fake_match = generate_fake_match
    page_class.resume_live_scouting = resume_live_scouting
    page_class._fake_match_debug_installed = True
