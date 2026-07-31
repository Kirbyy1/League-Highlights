from __future__ import annotations

import re
from typing import Any

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ui import live_match_page

_ROLE_NAMES = {
    "TOP": "Top",
    "JUNGLE": "Jungler",
    "MIDDLE": "Mid",
    "BOTTOM": "AD Carry",
    "UTILITY": "Support",
    "": "Unknown",
}


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _champion_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _role_name(value: str) -> str:
    return _ROLE_NAMES.get(str(value or "").upper(), str(value or "Unknown"))


def _format_percent(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.0f}%"
    except Exception:
        return "—"


def _format_decimal(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.1f}"
    except Exception:
        return "—"


def _tone_colors(tag: dict[str, Any]) -> tuple[str, str, str]:
    tone = str(tag.get("tone", "") or tag.get("style", "") or "").casefold()
    text = str(tag.get("text", "") or "").casefold()

    if "danger" in tone or "negative" in tone or "loss" in text:
        return "#26171B", "#F18B92", "#573038"
    if (
        "bad" in tone
        or "warn" in tone
        or "autofilled" in text
        or "back-to-back" in text
        or "threat" in text
    ):
        return "#281F13", "#F1BD70", "#6C4B20"
    if (
        "blue" in tone
        or "neutral" in tone
        or "seen" in text
        or "casual" in text
        or "session" in text
    ):
        return "#14212C", "#A7C9E8", "#2B4A60"
    return "#13231A", "#7BE2A0", "#28503A"


class ReferencePlayerScoutCard(QFrame):
    def __init__(self, player: dict[str, Any]) -> None:
        super().__init__()

        self.player_key = _normalize_text(player.get("player_key", "")).casefold()
        self.player_name = _normalize_text(
            player.get("riot_id", "") or player.get("game_name", "") or "Unknown player"
        )
        self.champion_name = _normalize_text(player.get("champion", "") or "Unknown")
        self.champion_key = _champion_key(self.champion_name)
        self.role_code = str(player.get("role", "") or "").upper()
        self.rank_tier = ""
        self.latest_stats: dict[str, Any] = {"state": "loading"}
        self._pending_tags: list[dict[str, Any]] = []
        self._committed_tag_signature: tuple[tuple[str, str], ...] = ()
        self._tag_commit_allowed = True

        self._placeholder_champion = QPixmap(48, 48)
        self._placeholder_champion.fill(QColor("#202B37"))
        self._placeholder_icon = QPixmap(28, 28)
        self._placeholder_icon.fill(Qt.GlobalColor.transparent)

        self.setObjectName("ReferencePlayerScoutCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(304)
        self.setStyleSheet(
            """
            QFrame#ReferencePlayerScoutCard {
                background: #101820;
                border: 1px solid #25333E;
                border-radius: 5px;
            }
            QFrame#ReferencePlayerScoutCard:hover {
                background: #121B23;
                border-color: #40505E;
            }
            QFrame#ReferenceCardHeader {
                background: #0D141B;
                border: none;
                border-top-left-radius: 5px;
                border-top-right-radius: 5px;
            }
            QFrame#ReferenceCardBody {
                background: #101820;
                border: none;
                border-bottom-left-radius: 5px;
                border-bottom-right-radius: 5px;
            }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = QFrame()
        self.header.setObjectName("ReferenceCardHeader")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(9, 5, 9, 5)
        header_layout.setSpacing(6)

        self.name_label = QLabel(self.player_name)
        self.name_label.setStyleSheet("color: #F0F3F6; font-weight: 700; font-size: 11px;")
        self.name_label.setToolTip(self.player_name)
        header_layout.addWidget(self.name_label, 1)

        self.stage_chip = QLabel("Loading")
        self.stage_chip.setObjectName("ReferenceStageChip")
        self.stage_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(self.stage_chip, 0, Qt.AlignmentFlag.AlignRight)

        self.party_label = QLabel("")
        self.party_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.party_label.setStyleSheet(
            "color: #A7C9E8; background: #14212C; border: none; "
            "border-radius: 3px; padding: 2px 6px; font-size: 9px; font-weight: 650;"
        )
        self.party_label.setVisible(False)
        header_layout.addWidget(self.party_label)

        root.addWidget(self.header)

        self.body = QFrame()
        self.body.setObjectName("ReferenceCardBody")
        body = QVBoxLayout(self.body)
        body.setContentsMargins(8, 8, 8, 8)
        body.setSpacing(7)
        root.addWidget(self.body)

        top = QHBoxLayout()
        top.setSpacing(9)
        self.champion_label = QLabel()
        self.champion_label.setFixedSize(48, 48)
        self.champion_label.setPixmap(self._placeholder_champion)
        self.champion_label.setScaledContents(True)
        self.champion_label.setStyleSheet(
            "border: 1px solid #2B3D4A; background: #111922; border-radius: 4px;"
        )
        top.addWidget(self.champion_label, 0, Qt.AlignmentFlag.AlignTop)

        summary_col = QVBoxLayout()
        summary_col.setSpacing(2)

        self.champion_title = QLabel(self.champion_name)
        self.champion_title.setToolTip(self.champion_name)
        self.champion_title.setStyleSheet("color: #F1F6FA; font-size: 11px; font-weight: 700;")
        summary_col.addWidget(self.champion_title)

        self.quick_win_label = QLabel("0 Played")
        self.quick_win_label.setStyleSheet("color: #D8E1E8; font-size: 10px; font-weight: 650;")
        summary_col.addWidget(self.quick_win_label)

        self.kda_label = QLabel("— / — / —")
        self.kda_label.setStyleSheet("color: #7BE2A0; font-size: 10px; font-weight: 600;")
        summary_col.addWidget(self.kda_label)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(6)
        self.level_chip = QLabel("Lv —")
        self.level_chip.setStyleSheet(
            "color: #BFE7CC; background: #132018; border: none; border-radius: 5px; padding: 1px 6px; font-size: 9px; font-weight: 700;"
        )
        meta_row.addWidget(self.level_chip, 0, Qt.AlignmentFlag.AlignLeft)
        meta_row.addStretch()
        summary_col.addLayout(meta_row)

        top.addLayout(summary_col, 1)
        body.addLayout(top)

        self.rank_section = QFrame()
        rank_layout = QHBoxLayout(self.rank_section)
        rank_layout.setContentsMargins(0, 0, 0, 0)
        rank_layout.setSpacing(8)

        self.rank_icon_label = QLabel()
        self.rank_icon_label.setFixedSize(28, 28)
        self.rank_icon_label.setPixmap(self._placeholder_icon)
        self.rank_icon_label.setScaledContents(True)
        rank_layout.addWidget(self.rank_icon_label, 0, Qt.AlignmentFlag.AlignTop)

        rank_texts = QVBoxLayout()
        rank_texts.setSpacing(2)
        self.rank_label = QLabel("Loading rank…")
        self.rank_label.setStyleSheet("color: #E7EDF3; font-size: 11px; font-weight: 700;")
        rank_texts.addWidget(self.rank_label)
        self.rank_subtitle = QLabel("Ranked Solo/Duo")
        self.rank_subtitle.setStyleSheet("color: #8795A3; font-size: 9px;")
        rank_texts.addWidget(self.rank_subtitle)
        rank_layout.addLayout(rank_texts, 1)
        body.addWidget(self.rank_section)

        self.role_section = QFrame()
        role_layout = QHBoxLayout(self.role_section)
        role_layout.setContentsMargins(0, 0, 0, 0)
        role_layout.setSpacing(8)

        self.role_icon_label = QLabel()
        self.role_icon_label.setFixedSize(28, 28)
        self.role_icon_label.setPixmap(self._placeholder_icon)
        self.role_icon_label.setScaledContents(True)
        role_layout.addWidget(self.role_icon_label, 0, Qt.AlignmentFlag.AlignTop)

        role_texts = QVBoxLayout()
        role_texts.setSpacing(2)
        self.current_role_label = QLabel(f"{_role_name(self.role_code)} (Current game)")
        self.current_role_label.setStyleSheet("color: #E7EDF3; font-size: 11px; font-weight: 700;")
        role_texts.addWidget(self.current_role_label)
        self.main_roles_label = QLabel("Main Roles: —")
        self.main_roles_label.setStyleSheet("color: #8795A3; font-size: 9px;")
        role_texts.addWidget(self.main_roles_label)
        role_layout.addLayout(role_texts, 1)
        body.addWidget(self.role_section)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(8)
        self.left_stat = self._make_stat_box()
        self.right_stat = self._make_stat_box()
        stats_row.addWidget(self.left_stat["frame"])
        stats_row.addWidget(self.right_stat["frame"])
        body.addLayout(stats_row)

        self.tags_host = QWidget()
        self.tags_grid = QGridLayout(self.tags_host)
        self.tags_grid.setContentsMargins(0, 0, 0, 0)
        self.tags_grid.setHorizontalSpacing(6)
        self.tags_grid.setVerticalSpacing(6)
        body.addWidget(self.tags_host)

        body.addStretch(1)

        self._set_stat_box(self.left_stat, "Recent", "0", "0 wins")
        self._set_stat_box(self.right_stat, "Ranked", "0", "0 wins")
        self._set_stage("Loading", "loading")
        self._pending_tags = []
        self._committed_tag_signature = ()
        self._tag_commit_allowed = True
        self._render_tags([])
        self._update_tooltip()

    def _make_stat_box(self) -> dict[str, Any]:
        frame = QFrame()
        frame.setStyleSheet(
            "background: #0D141B; border: 1px solid #22303B; border-radius: 4px;"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(7, 5, 7, 5)
        layout.setSpacing(2)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(3)

        big = QLabel("0")
        big.setStyleSheet("color: #A7C9E8; font-size: 19px; font-weight: 600;")
        top.addWidget(big, 0, Qt.AlignmentFlag.AlignBottom)

        suffix = QLabel("")
        suffix.setStyleSheet("color: #71808E; font-size: 9px;")
        top.addWidget(suffix, 0, Qt.AlignmentFlag.AlignBottom)
        top.addStretch()

        title = QLabel("")
        title.setStyleSheet("color: #8795A3; font-size: 9px;")
        layout.addLayout(top)
        layout.addWidget(title)

        detail = QLabel("")
        detail.setStyleSheet("color: #C7D0D9; font-size: 9px;")
        layout.addWidget(detail)

        return {
            "frame": frame,
            "big": big,
            "suffix": suffix,
            "title": title,
            "detail": detail,
        }

    def _set_stat_box(self, box: dict[str, Any], title: str, big: str, detail: str, suffix: str = "") -> None:
        box["title"].setText(title)
        box["big"].setText(big)
        box["detail"].setText(detail)
        box["suffix"].setText(suffix)

    def _set_stage(self, text: str, state: str) -> None:
        colors = {
            "loading": ("#14212C", "#A7C9E8"),
            "partial": ("#281F13", "#F1BD70"),
            "fast": ("#13231A", "#7BE2A0"),
            "ready": ("#10251A", "#58D889"),
            "unavailable": ("#26171B", "#F18B92"),
        }
        background, foreground = colors.get(state, colors["loading"])
        self.stage_chip.setText(text)
        self.stage_chip.setStyleSheet(
            f"color: {foreground}; background: {background}; border: none; "
            "border-radius: 3px; padding: 2px 6px; font-size: 9px; font-weight: 700;"
        )

    def set_champion_icon(self, pixmap: QPixmap) -> None:
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            self.champion_label.setPixmap(
                pixmap.scaled(
                    QSize(48, 48),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def set_rank_asset(self, pixmap: QPixmap) -> None:
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            self.rank_icon_label.setPixmap(
                pixmap.scaled(
                    QSize(28, 28),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def set_role_asset(self, pixmap: QPixmap) -> None:
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            self.role_icon_label.setPixmap(
                pixmap.scaled(
                    QSize(28, 28),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def set_waiting_for_key(self) -> None:
        self.rank_label.setText("Local scouting unavailable")
        self.rank_subtitle.setText("Riot API fallback is not configured")
        self._set_stage("No data", "unavailable")
        self.level_chip.setText("Lv —")
        self.quick_win_label.setText("0 Played")
        self.kda_label.setText("— / — / —")
        self.current_role_label.setText(f"{_role_name(self.role_code)} (Current game)")
        self.main_roles_label.setText("Main Roles: —")
        self._set_stat_box(self.left_stat, "Recent", "0", "0 wins")
        self._set_stat_box(self.right_stat, "Ranked", "0", "0 wins")
        self._render_tags([])
        self._update_tooltip()

    @staticmethod
    def _merge_progressive_stats(
        previous: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep completed data when a later partial/error payload arrives."""

        if not previous:
            return dict(incoming)

        merged = dict(previous)
        previous_state = str(previous.get("state", "") or "")
        incoming_state = str(incoming.get("state", "") or "")
        order = {
            "": 0,
            "loading": 0,
            "partial": 1,
            "fast": 2,
            "ready": 3,
        }

        if (
            incoming_state in {"error", "unavailable"}
            and previous_state in {"partial", "fast", "ready"}
        ):
            merged["last_error"] = str(
                incoming.get("message", "") or ""
            )
            return merged

        if order.get(incoming_state, 0) < order.get(previous_state, 0):
            for key in (
                "riot_id",
                "game_name",
                "tag_line",
                "account_level",
                "profile_icon_id",
                "rank_source",
                "history_source",
                "mastery_source",
            ):
                value = incoming.get(key)
                if value not in {None, ""}:
                    merged[key] = value
        else:
            merged.update(incoming)

        previous_rank_state = str(
            previous.get("rank_state", "") or ""
        )
        incoming_rank_state = str(
            incoming.get("rank_state", "") or ""
        )
        if (
            previous_rank_state in {"ready", "unranked"}
            and incoming_rank_state
            in {"", "loading", "unavailable"}
        ):
            for key in (
                "rank",
                "tier",
                "division",
                "lp",
                "wins",
                "losses",
                "games",
                "win_rate",
                "ranked_wins",
                "ranked_losses",
                "ranked_games",
                "ranked_win_rate",
                "rank_state",
                "rank_source",
            ):
                if key in previous:
                    merged[key] = previous[key]

        return merged

    def begin_tag_refresh(self) -> None:
        """Allow one tag replacement when a new analysis cycle completes.

        Existing committed tags remain visible during a manual refresh.
        """

        self._tag_commit_allowed = True

    @staticmethod
    def _stable_tag_list(
        tags: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return a deterministic, unique tag list suitable for compact cards."""

        unique: list[dict[str, Any]] = []
        seen: set[str] = set()

        for raw in tags:
            if not isinstance(raw, dict):
                continue
            text = _normalize_text(
                raw.get("text", "")
                or raw.get("label", "")
                or ""
            )
            key = text.casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            item = dict(raw)
            item["text"] = text
            unique.append(item)
            if len(unique) >= 8:
                break

        return unique

    def queue_tags(
        self,
        tags: list[dict[str, Any]],
    ) -> None:
        stable = self._stable_tag_list(tags)
        if stable:
            self._pending_tags = stable

    def commit_tags(self) -> None:
        """Render the latest complete tag set at most once per cycle."""

        if not self._tag_commit_allowed:
            return

        signature = tuple(
            (
                _normalize_text(tag.get("text", "")).casefold(),
                str(
                    tag.get("tone", "")
                    or tag.get("style", "")
                    or ""
                ).casefold(),
            )
            for tag in self._pending_tags
        )

        if signature != self._committed_tag_signature:
            self._render_tags(self._pending_tags)
            self._committed_tag_signature = signature

        self._tag_commit_allowed = False

    def apply_stats(self, stats: dict[str, Any]) -> None:
        self.latest_stats = self._merge_progressive_stats(
            self.latest_stats,
            dict(stats or {}),
        )
        stats = self.latest_stats
        state = str(stats.get("state", "") or "")

        riot_id = _normalize_text(stats.get("riot_id", "") or stats.get("game_name", "") or self.player_name)
        if riot_id:
            self.player_name = riot_id
            self.name_label.setText(riot_id)
            self.name_label.setToolTip(riot_id)

        account_level = stats.get("account_level")
        self.level_chip.setText(f"Lv {int(account_level)}" if account_level not in {None, ""} else "Lv —")

        premade_size = int(stats.get("premade_size", 0) or 0)
        self.party_label.setText(f"Party {premade_size}" if premade_size > 1 else "")
        self.party_label.setVisible(premade_size > 1)

        sample_games = int(stats.get("sample_games", 0) or 0)
        recent_wr = stats.get("recent_win_rate")
        if sample_games > 0 and recent_wr is not None:
            self.quick_win_label.setText(f"{_format_percent(recent_wr)} Win ({sample_games} Played)")
        else:
            self.quick_win_label.setText(f"{sample_games} Played" if sample_games else "0 Played")

        kills = stats.get("avg_kills")
        deaths = stats.get("avg_deaths")
        assists = stats.get("avg_assists")
        self.kda_label.setText(
            f"{_format_decimal(kills)} / {_format_decimal(deaths)} / {_format_decimal(assists)}"
        )

        rank_state = str(stats.get("rank_state", "loading") or "loading")
        rank_text = str(stats.get("rank", "") or "").strip()
        tier_hint = str(stats.get("tier", "") or "").upper()
        # Some LCU builds return the rank values before/without an explicit
        # rank_state. Treat a real tier as ready so valid ranked data is not
        # rendered as an empty/loading card.
        if rank_state in {"", "loading", "unavailable"} and tier_hint not in {
            "", "LOADING", "UNAVAILABLE"
        }:
            rank_state = "unranked" if tier_hint == "UNRANKED" else "ready"
        if rank_state == "ready":
            self.rank_label.setText(rank_text or "Ranked")
            self.rank_tier = str(stats.get("tier", "") or "").upper()
        elif rank_state == "unranked":
            self.rank_label.setText("Unranked")
            self.rank_tier = "UNRANKED"
        elif rank_state == "unavailable":
            self.rank_label.setText("Rank unavailable")
            self.rank_tier = "UNAVAILABLE"
        else:
            self.rank_label.setText("Loading rank…")
            self.rank_tier = "LOADING"

        if state == "ready":
            self._set_stage("Ready", "ready")
        elif state == "fast":
            self._set_stage("Quick", "fast")
        elif state == "partial":
            self._set_stage("Rank", "partial")
        elif state in {"error", "unavailable"} or rank_state == "unavailable":
            self._set_stage("No data", "unavailable")
        else:
            self._set_stage("Loading", "loading")

        ranked_games = int(
            stats.get("ranked_games", stats.get("games", 0)) or 0
        )
        ranked_wr = stats.get("ranked_win_rate")
        if ranked_wr is None:
            ranked_wr = stats.get("win_rate")
        if ranked_games > 0 and ranked_wr is not None:
            self.rank_subtitle.setText(f"Ranked Solo/Duo · {_format_percent(ranked_wr)} WR ({ranked_games} Played)")
        elif rank_state == "unranked":
            self.rank_subtitle.setText("Ranked Solo/Duo · No games")
        elif rank_state == "unavailable":
            self.rank_subtitle.setText("Ranked Solo/Duo · Unavailable")
        else:
            self.rank_subtitle.setText("Ranked Solo/Duo · Loading")

        current_role = str(stats.get("current_role", "") or self.role_code).upper()
        inferred_role = str(stats.get("inferred_role", "") or stats.get("assigned_role", "") or "").upper()
        self.role_code = current_role or self.role_code
        self.current_role_label.setText(f"{_role_name(self.role_code)} (Current game)")

        main_role = _normalize_text(stats.get("main_role_name", "") or _role_name(inferred_role))
        secondary_role = _normalize_text(stats.get("secondary_role_name", "") or "")
        if main_role and secondary_role and secondary_role.casefold() != "unknown":
            self.main_roles_label.setText(f"Main Roles: {main_role}, {secondary_role}")
        elif main_role and main_role.casefold() != "unknown":
            self.main_roles_label.setText(f"Main Roles: {main_role}")
        else:
            self.main_roles_label.setText("Main Roles: —")

        recent_wins = int(stats.get("recent_wins", 0) or 0)
        self._set_stat_box(
            self.left_stat,
            "Recent",
            str(sample_games),
            f"{recent_wins} wins",
            "G"
        )

        ranked_wins = int(
            stats.get("ranked_wins", stats.get("wins", 0)) or 0
        )
        self._set_stat_box(
            self.right_stat,
            "Ranked",
            str(ranked_games),
            f"{ranked_wins} wins",
            "G"
        )

        # Tags are intentionally queued rather than drawn here. The scout sends
        # rank, quick, final, and team-enriched payloads. Rendering every stage
        # made chips visibly disappear and get replaced.
        self.queue_tags(list(stats.get("tags", []) or []))
        if str(stats.get("state", "") or "") in {"fast", "ready"}:
            self.commit_tags()
        self._update_tooltip()

    def _render_tags(self, tags: list[dict[str, Any]]) -> None:
        while self.tags_grid.count():
            item = self.tags_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        visible_tags = tags[:3]
        hidden_tags = tags[3:]

        for index, tag in enumerate(visible_tags):
            text = _normalize_text(tag.get("text", "") or tag.get("label", "") or "")
            if not text:
                continue
            bg, fg, border = _tone_colors(tag)
            chip = QLabel(text)
            chip.setWordWrap(False)
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setStyleSheet(
                f"background: {bg}; color: {fg}; border: 1px solid {border}; "
                "border-radius: 3px; padding: 3px 7px; font-size: 9px; font-weight: 650;"
            )
            self.tags_grid.addWidget(chip, index // 2, index % 2)

        if hidden_tags:
            more = QLabel(f"+{len(hidden_tags)}")
            more.setAlignment(Qt.AlignmentFlag.AlignCenter)
            more.setToolTip(
                "\n".join(
                    _normalize_text(tag.get("text", "") or tag.get("label", "") or "")
                    for tag in hidden_tags
                    if _normalize_text(tag.get("text", "") or tag.get("label", "") or "")
                )
            )
            more.setStyleSheet(
                "background: #17212A; color: #AAB5C0; border: 1px solid #2D3944; "
                "border-radius: 3px; padding: 3px 7px; font-size: 9px; font-weight: 650;"
            )
            index = len(visible_tags)
            self.tags_grid.addWidget(more, index // 2, index % 2)

    def _update_tooltip(self) -> None:
        stats = dict(self.latest_stats or {})
        lines = [
            f"<b>{self.player_name}</b>",
            f"{self.champion_name} · Lv {self.level_chip.text().replace('Lv ', '') if 'Lv ' in self.level_chip.text() else '—'}",
        ]

        rank_text = self.rank_label.text()
        if rank_text:
            lines.append(f"<b>Current Solo/Duo:</b> {rank_text}")

        sample_games = int(stats.get("sample_games", 0) or 0)
        recent_wr = stats.get("recent_win_rate")
        if sample_games > 0 and recent_wr is not None:
            lines.append(f"<b>Recent form:</b> {_format_percent(recent_wr)} WR ({sample_games} games)")

        ranked_games = int(
            stats.get("ranked_games", stats.get("games", 0)) or 0
        )
        ranked_wr = stats.get("ranked_win_rate")
        if ranked_wr is None:
            ranked_wr = stats.get("win_rate")
        if ranked_games > 0 and ranked_wr is not None:
            lines.append(f"<b>Season Ranked Solo/Duo:</b> {_format_percent(ranked_wr)} WR ({ranked_games} games)")

        role_text = self.current_role_label.text().replace(" (Current game)", "")
        main_roles = self.main_roles_label.text().replace("Main Roles: ", "")
        lines.append(f"<b>Current role:</b> {role_text}")
        lines.append(f"<b>Main roles:</b> {main_roles or '—'}")

        self.setToolTip("<br>".join(lines))


def install_reference_card_layout() -> None:
    original_card = live_match_page.PlayerScoutCard
    if getattr(original_card, "_reference_layout_installed", False):
        return

    ReferencePlayerScoutCard._reference_layout_installed = True
    ReferencePlayerScoutCard.__name__ = "PlayerScoutCard"
    ReferencePlayerScoutCard.__qualname__ = "PlayerScoutCard"

    live_match_page.PlayerScoutCard = ReferencePlayerScoutCard
