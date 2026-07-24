
from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig
from app.services.champion_icon_provider import ChampionIconProvider, normalize_champion_name
from app.services.live_match_asset_icons import make_rank_emblem, make_role_icon
from app.services.live_match_client_asset_provider import LiveMatchClientAssetProvider
from app.services.live_match_scout import LiveMatchScout


LIVE_MATCH_UI_BUILD = "V29-RELIABLE-PROGRESSIVE-SMART-WINDOWS"

_ROLE_NAMES = {
    "TOP": "Top",
    "JUNGLE": "Jungle",
    "MIDDLE": "Mid",
    "BOTTOM": "AD Carry",
    "UTILITY": "Support",
    "": "Unknown",
}


class PlayerScoutCard(QFrame):
    """Compact five-across player card with details moved into tooltips."""


    def __init__(self, player: dict[str, Any]) -> None:
        super().__init__()
        self.player = player
        self.latest_stats: dict[str, Any] = {}
        self.player_key = str(player.get("player_key", ""))
        self.role_code = str(player.get("role", "") or "").upper()
        self.rank_tier = ""

        champion = str(player.get("champion", "Unknown") or "Unknown")
        self.champion_name = champion
        self.champion_key = normalize_champion_name(champion)

        self.setObjectName("LiveStackedPlayerCard")
        self.setMinimumWidth(170)
        self.setMinimumHeight(192)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(7)

        self.champion_badge = QLabel(champion[:1].upper() if champion else "?")
        self.champion_badge.setObjectName("LiveStackedChampion")
        self.champion_badge.setFixedSize(54, 54)
        self.champion_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.champion_badge.setToolTip(champion)
        top.addWidget(self.champion_badge)

        identity = QVBoxLayout()
        identity.setSpacing(2)

        name_row = QHBoxLayout()
        name_row.setSpacing(4)

        self.name_label = QLabel(str(player.get("riot_id", "Unknown player")))
        self.name_label.setObjectName("LiveStackedName")
        self.name_label.setToolTip(str(player.get("riot_id", "Unknown player")))
        self.name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        name_row.addWidget(self.name_label, 1)

        self.level_chip = QLabel("Lv —")
        self.level_chip.setObjectName("LiveStackedLevel")
        self.level_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_row.addWidget(self.level_chip)

        identity.addLayout(name_row)

        self.champion_label = QLabel(champion)
        self.champion_label.setObjectName("LiveStackedChampionName")
        identity.addWidget(self.champion_label)

        role_row = QHBoxLayout()
        role_row.setSpacing(4)
        self.role_icon = QLabel()
        self.role_icon.setObjectName("LiveStackedRoleIcon")
        self.role_icon.setFixedSize(20, 20)
        self.role_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        role_row.addWidget(self.role_icon)

        self.role_label = QLabel(_ROLE_NAMES.get(self.role_code, "Unknown"))
        self.role_label.setObjectName("LiveStackedRole")
        role_row.addWidget(self.role_label)
        role_row.addStretch()
        identity.addLayout(role_row)

        top.addLayout(identity, 1)
        root.addLayout(top)

        rank_row = QHBoxLayout()
        rank_row.setSpacing(7)

        self.rank_icon = QLabel()
        self.rank_icon.setObjectName("LiveStackedRankIcon")
        self.rank_icon.setFixedSize(62, 54)
        self.rank_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rank_row.addWidget(self.rank_icon)

        rank_texts = QVBoxLayout()
        rank_texts.setSpacing(1)

        self.rank_label = QLabel("Loading")
        self.rank_label.setObjectName("LiveStackedRankText")
        self.rank_label.setWordWrap(True)
        rank_texts.addWidget(self.rank_label)

        self.quick_line = QLabel("Loading ranked record")
        self.quick_line.setObjectName("LiveStackedQuickLine")
        self.quick_line.setWordWrap(True)
        rank_texts.addWidget(self.quick_line)

        rank_row.addLayout(rank_texts, 1)
        root.addLayout(rank_row)

        # Full-width vertical chips keep every tag readable on five-across
        # cards.  The former single horizontal row clipped longer labels.
        self.tags_row = QVBoxLayout()
        self.tags_row.setContentsMargins(0, 0, 0, 0)
        self.tags_row.setSpacing(3)
        root.addLayout(self.tags_row)

        root.addStretch()

        self._apply_default_icons()
        self._update_card_tooltip()

    @staticmethod
    def _rounded_pixmap(
        source: QPixmap,
        target_size: tuple[int, int],
        radius: float,
    ) -> QPixmap:
        width, height = target_size
        side = max(width, height)
        scaled = source.scaled(
            side,
            side,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = max((scaled.width() - width) // 2, 0)
        y = max((scaled.height() - height) // 2, 0)
        cropped = scaled.copy(x, y, width, height)

        rounded = QPixmap(width, height)
        rounded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, float(width), float(height), radius, radius)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, cropped)
        painter.end()
        return rounded

    def _apply_default_icons(self) -> None:
        self.set_role_icon(self.role_code)
        self.set_rank_icon("", "")

    def set_champion_icon(self, source: QPixmap) -> None:
        if source.isNull():
            return
        self.champion_badge.setText("")
        self.champion_badge.setPixmap(
            self._rounded_pixmap(source, (54, 54), 7.0)
        )

    def set_role_icon(self, role: str) -> None:
        self.role_code = str(role or "").upper()
        self.role_icon.setPixmap(make_role_icon(self.role_code, 20))
        self.role_icon.setToolTip(
            f"Current or inferred role: {_ROLE_NAMES.get(self.role_code, 'Unknown')}"
        )

    def set_role_asset(self, source: QPixmap) -> None:
        if source.isNull():
            return
        self.role_icon.setPixmap(
            source.scaled(
                20,
                20,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def set_rank_icon(self, tier: str, division: str) -> None:
        self.rank_tier = str(tier or "").upper()
        if self.rank_tier in {"", "LOADING", "UNAVAILABLE"}:
            self.rank_icon.clear()
            self.rank_icon.setToolTip(
                "Rank loading" if self.rank_tier != "UNAVAILABLE" else "Rank unavailable"
            )
            return
        self.rank_icon.setPixmap(make_rank_emblem(self.rank_tier, division, 54))
        self.rank_icon.setToolTip(self.rank_tier.title())

    def set_rank_asset(self, source: QPixmap) -> None:
        if source.isNull():
            return
        self.rank_icon.setPixmap(
            source.scaled(
                62,
                54,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def set_waiting_for_key(self) -> None:
        self.rank_label.setText("Local scouting unavailable")
        self.quick_line.setText("Riot API fallback is not configured")
        self.level_chip.setText("Lv —")
        self._set_tags([])
        self.setToolTip(
            "The local League client did not expose this player. A Riot API key can be used as a fallback."
        )

    def update_identity(self, stats: dict[str, Any]) -> None:
        riot_id = str(stats.get("riot_id", "") or "").strip()
        game_name = str(stats.get("game_name", "") or "").strip()
        tag_line = str(stats.get("tag_line", "") or "").strip()
        display = riot_id or (f"{game_name}#{tag_line}" if game_name and tag_line else game_name)
        if display and not display.casefold().startswith("player "):
            self.name_label.setText(display)
            self.name_label.setToolTip(display)
            self.player["riot_id"] = display

    @staticmethod
    def _merge_stats(
        previous: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        if not previous:
            return dict(incoming)
        merged = dict(previous)
        incoming_state = str(incoming.get("state", "") or "")
        previous_state = str(previous.get("state", "") or "")
        order = {"": 0, "partial": 1, "fast": 2, "ready": 3}

        if incoming_state in {"error", "unavailable"} and previous_state in {"partial", "fast", "ready"}:
            merged["last_error"] = str(incoming.get("message", "") or "")
            return merged

        if order.get(incoming_state, 0) < order.get(previous_state, 0):
            safe_keys = {
                "riot_id", "game_name", "tag_line", "account_level",
                "profile_icon_id", "rank_source", "history_source",
                "mastery_source", "cache_age_seconds",
            }
            for key in safe_keys:
                value = incoming.get(key)
                if value not in {None, ""}:
                    merged[key] = value
        else:
            merged.update(incoming)

        incoming_rank_state = str(incoming.get("rank_state", "") or "")
        previous_rank_state = str(previous.get("rank_state", "") or "")
        if (
            incoming_rank_state in {"", "loading", "unavailable"}
            and previous_rank_state in {"ready", "unranked"}
        ):
            for key in (
                "rank", "tier", "division", "lp", "wins", "losses",
                "games", "win_rate", "ranked_games", "ranked_win_rate",
                "rank_state", "rank_source",
            ):
                if key in previous:
                    merged[key] = previous[key]
        return merged

    def apply_stats(self, stats: dict[str, Any]) -> None:
        self.latest_stats = self._merge_stats(self.latest_stats, dict(stats))
        stats = self.latest_stats
        self.update_identity(stats)
        state = str(stats.get("state", ""))

        account_level = stats.get("account_level")
        self.level_chip.setText(
            f"Lv {int(account_level)}" if account_level else "Lv —"
        )

        role_code = str(
            stats.get("assigned_role", "")
            or stats.get("inferred_role", "")
            or stats.get("current_role", "")
            or stats.get("main_role", "")
            or self.role_code
            or ""
        ).upper()
        role_name = str(
            stats.get("role_name", "")
            or stats.get("main_role_name", "")
            or _ROLE_NAMES.get(role_code, "Unknown")
        )
        if role_name == "Unknown role":
            role_name = str(stats.get("main_role_name", "Unknown") or "Unknown")

        lane_opponent = stats.get("lane_opponent", {})
        if isinstance(lane_opponent, dict) and lane_opponent.get("opponent_champion"):
            opponent_champion = str(lane_opponent.get("opponent_champion", "Unknown") or "Unknown")
            self.role_label.setText(f"{role_name} · vs {opponent_champion}")
            self.role_label.setToolTip(str(lane_opponent.get("tooltip", "") or ""))
        else:
            self.role_label.setText(role_name)
            self.role_label.setToolTip("")
        self.set_role_icon(role_code)

        rank_state = str(stats.get("rank_state", "loading") or "loading")
        tier = str(stats.get("tier", "") or "").upper()
        division = str(stats.get("division", "") or "").upper()
        if rank_state == "ready":
            self.set_rank_icon(tier, division)
            rank_text = tier.title()
            if tier not in {"MASTER", "GRANDMASTER", "CHALLENGER"} and division:
                rank_text += f" {division}"
            lp = int(stats.get("lp", 0) or 0)
            if lp:
                rank_text += f" · {lp} LP"
            self.rank_label.setText(rank_text)
        elif rank_state == "unranked":
            self.set_rank_icon("UNRANKED", "")
            self.rank_label.setText("Unranked")
        elif rank_state == "unavailable":
            self.set_rank_icon("UNAVAILABLE", "")
            self.rank_label.setText("Rank unavailable")
        else:
            self.set_rank_icon("LOADING", "")
            self.rank_label.setText("Loading rank")

        ranked_games = int(stats.get("ranked_games", stats.get("games", 0)) or 0)
        ranked_wr = stats.get("ranked_win_rate", stats.get("win_rate"))
        if rank_state == "ready" and ranked_games and ranked_wr is not None:
            ranked_record_text = f"Ranked WR {float(ranked_wr):.0f}% · {ranked_games} games"
        elif rank_state == "unranked":
            ranked_record_text = "No ranked Solo/Duo games"
        elif rank_state == "unavailable":
            ranked_record_text = "Ranked record unavailable"
        else:
            ranked_record_text = "Loading ranked record"

        if state == "partial":
            self.quick_line.setText("Rank loaded · history loading")
            self._set_tags([])
            self._update_card_tooltip()
            return

        sample = int(stats.get("sample_games", 0) or 0)
        recent_wr = stats.get("recent_win_rate")
        avg_kda = float(stats.get("avg_kda", 0) or 0)
        cache_age = float(stats.get("cache_age_seconds", 0) or 0)
        cache_prefix = ""
        if cache_age > 0:
            cache_prefix = f"Cached {max(1, int(cache_age // 60))}m · "

        if state == "fast":
            if sample and recent_wr is not None:
                self.quick_line.setText(
                    f"{cache_prefix}Quick {sample} · {float(recent_wr):.0f}% WR · {avg_kda:.1f} KDA"
                )
            else:
                self.quick_line.setText(f"{cache_prefix}{ranked_record_text}")
            self._set_tags(list(stats.get("tags", ())))
            self._update_card_tooltip()
            return

        if state != "ready":
            self.quick_line.setText(str(stats.get("message", "Player data unavailable")))
            self._set_tags([])
            self._update_card_tooltip()
            return

        if sample and recent_wr is not None:
            self.quick_line.setText(
                f"{cache_prefix}Final {sample} · {float(recent_wr):.0f}% WR · {avg_kda:.1f} KDA"
            )
        else:
            self.quick_line.setText(f"{cache_prefix}{ranked_record_text}")
        self._set_tags(list(stats.get("tags", ())))
        self._update_card_tooltip()

    def _set_tags(self, tags: list[Any]) -> None:
        while self.tags_row.count():
            item = self.tags_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        normalized: list[dict[str, str]] = []
        for raw in tags:
            if isinstance(raw, dict):
                text = str(raw.get("text", "") or "")
                tone = str(raw.get("tone", "positive") or "positive")
                tooltip = str(raw.get("tooltip", "") or "")
            else:
                text = str(raw)
                tone = "positive"
                tooltip = ""
            if text:
                normalized.append(
                    {"text": text, "tone": tone, "tooltip": tooltip or text}
                )

        visible = normalized[:3]
        hidden = normalized[3:]
        for tag in visible:
            chip = QLabel(tag["text"])
            chip.setObjectName("LiveStackedTag")
            chip.setProperty("tone", tag["tone"])
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setWordWrap(True)
            chip.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Minimum,
            )
            chip.setToolTip(tag["tooltip"])
            self.tags_row.addWidget(chip)

        if hidden:
            more = QLabel(f"+{len(hidden)} more")
            more.setObjectName("LiveStackedTag")
            more.setProperty("tone", "neutral")
            more.setAlignment(Qt.AlignmentFlag.AlignCenter)
            more.setToolTip("\n\n".join(
                f"{tag['text']}\n{tag['tooltip']}" for tag in hidden
            ))
            self.tags_row.addWidget(more)

    def _update_card_tooltip(self) -> None:
        stats = self.latest_stats
        if not stats:
            self.setToolTip(
                f"<b>{escape(str(self.player.get('riot_id', 'Unknown player')))}</b><br>"
                f"{escape(self.champion_name)}<br>"
                "Scouting analysis is loading."
            )
            return

        games = int(stats.get("games", 0) or 0)
        season_wr = stats.get("win_rate")
        sample = int(stats.get("sample_games", 0) or 0)
        recent_wr = stats.get("recent_win_rate")
        kda = float(stats.get("avg_kda", 0) or 0)
        cs = float(stats.get("avg_cs_min", 0) or 0)
        main_role = str(stats.get("main_role_name", "Unknown") or "Unknown")
        second_role = str(stats.get("secondary_role_name", "Unknown") or "Unknown")
        role_share = float(stats.get("role_share", 0) or 0) * 100.0
        champion_games = int(stats.get("champion_games", 0) or 0)
        champion_wr = stats.get("champion_win_rate")
        premades = list(stats.get("premade_members", ()))

        lines = [
            f"<b>{escape(str(self.player.get('riot_id', 'Unknown player')))}</b>",
            f"{escape(self.champion_name)} · {escape(self.level_chip.text())}",
            f"<b>Current Solo/Duo:</b> {escape(str(stats.get('rank', 'Rank loading')))}",
        ]

        if games and season_wr is not None:
            lines.append(
                f"<b>Ranked Solo/Duo WR:</b> {season_wr:.0f}% over {games} games"
            )
        if sample and recent_wr is not None:
            windows = dict(stats.get("analysis_windows", {}) or {})
            form_games = int(windows.get("form", stats.get("form_sample_games", min(sample, 5))) or 0)
            performance_games = int(windows.get("performance", stats.get("performance_sample_games", min(sample, 10))) or 0)
            role_games = int(windows.get("role", stats.get("role_sample_games", sample)) or 0)
            lines.append(
                f"<b>Recent form {form_games}:</b> {recent_wr:.0f}% WR · "
                f"<b>Performance {performance_games}:</b> {kda:.1f} KDA · {cs:.1f} CS/min"
            )
            lines.append(
                f"<b>Role/champion evidence:</b> {role_games} ranked games"
            )
        if main_role != "Unknown":
            role_text = f"<b>Main role:</b> {escape(main_role)} ({role_share:.0f}%)"
            if second_role != "Unknown":
                role_text += f" · Secondary {escape(second_role)}"
            assignment_confidence = str(
                stats.get("role_assignment_confidence", "") or ""
            )
            if assignment_confidence:
                role_text += (
                    f" · team assignment {escape(assignment_confidence)} confidence"
                )
            lines.append(role_text)

        lane_opponent = stats.get("lane_opponent", {})
        if isinstance(lane_opponent, dict) and lane_opponent.get("opponent_name"):
            opponent_name = escape(str(lane_opponent.get("opponent_name", "Unknown") or "Unknown"))
            opponent_champion = escape(str(lane_opponent.get("opponent_champion", "Unknown") or "Unknown"))
            opponent_rank = escape(str(lane_opponent.get("opponent_rank", "Unranked") or "Unranked"))
            edge = escape(str(lane_opponent.get("edge", "Even profile comparison") or "Even profile comparison"))
            delta = float(lane_opponent.get("strength_delta", 0) or 0)
            lines.append(
                f"<b>Lane profile:</b> vs {opponent_name} on {opponent_champion} · "
                f"{opponent_rank} · {edge} ({delta:+.1f})"
            )
            lines.append(
                "<i>Profile comparison uses rank, role familiarity, mastery and recent ranked form; "
                "it is not a champion counter prediction.</i>"
            )
        mastery_level = int(stats.get("mastery_level", 0) or 0)
        mastery_points = int(stats.get("mastery_points", 0) or 0)
        mastery_rank = stats.get("mastery_rank")
        if mastery_points:
            mastery_text = (
                f"<b>Mastery:</b> level {mastery_level} · "
                f"{mastery_points:,} points"
            )
            if mastery_rank:
                mastery_text += f" · #{int(mastery_rank)} mastery champion"
            lines.append(mastery_text)

        if champion_games:
            champ_text = f"<b>Current champion:</b> {champion_games} recent games"
            if champion_wr is not None:
                champ_text += f" · {champion_wr:.0f}% WR"
            lines.append(champ_text)

        encounter_count = int(stats.get("encounter_count", 0) or 0)
        if encounter_count:
            record_wins = int(stats.get("encounter_wins", 0) or 0)
            record_losses = int(stats.get("encounter_losses", 0) or 0)
            record_text = (
                f" · your record {record_wins}W-{record_losses}L"
                if record_wins + record_losses
                else ""
            )
            lines.append(
                "<b>Previous encounters:</b> "
                f"tracked ally {int(stats.get('encounter_local_ally_count', 0) or 0)}, "
                f"tracked enemy {int(stats.get('encounter_local_enemy_count', 0) or 0)}; "
                f"recent ranked ally {int(stats.get('encounter_ranked_ally_count', 0) or 0)}, "
                f"recent ranked enemy {int(stats.get('encounter_ranked_enemy_count', 0) or 0)}"
                f"{record_text}"
            )

            encounter_history = list(stats.get("encounter_history", ()))
            for item in encounter_history[:6]:
                if not isinstance(item, dict):
                    continue
                relation = (
                    "Ally"
                    if str(item.get("relation", "")) == "ally"
                    else "Enemy"
                )
                my_champion = escape(
                    str(item.get("my_champion", "") or "Unknown")
                )
                their_champion = escape(
                    str(item.get("their_champion", "") or "Unknown")
                )
                source = str(item.get("source", "") or "")
                source_text = (
                    "ranked history"
                    if source == "ranked"
                    else "League Highlights record"
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
                result_text = escape(str(item.get("result", "") or ""))
                my_kda = escape(str(item.get("my_kda", "") or ""))
                their_kda = escape(str(item.get("their_kda", "") or ""))
                outcome = f" · {result_text}" if result_text else ""
                if my_kda or their_kda:
                    outcome += f" · KDA {my_kda or '—'} vs {their_kda or '—'}"
                lines.append(
                    f"<b>{relation} before:</b> you played {my_champion}; "
                    f"they played {their_champion}{outcome} · {escape(source_text)}"
                    f"{when_text}"
                )

        if premades:
            role_pair = str(stats.get("premade_role_pair", "") or "Premade")
            premade_text = f"<b>{escape(role_pair.title())} with:</b> " + escape(", ".join(premades))
            together = int(stats.get("premade_games_together", 0) or 0)
            together_wr = stats.get("premade_win_rate")
            sessions = int(stats.get("premade_sessions", 0) or 0)
            consecutive = int(stats.get("premade_consecutive_games", 0) or 0)
            confidence = str(stats.get("premade_confidence", "") or "")
            confidence_score = int(stats.get("premade_confidence_score", 0) or 0)
            if together:
                evidence_scope = str(stats.get("premade_evidence_scope", "pair") or "pair")
                if evidence_scope == "strongest_pair":
                    premade_text += f" · {together} verified games on strongest pair"
                else:
                    premade_text += f" · {together} verified recent games"
            if sessions:
                premade_text += f" · {sessions} session(s)"
            if consecutive >= 2:
                premade_text += f" · {consecutive} consecutive"
            if together_wr is not None:
                premade_text += f" · {float(together_wr):.0f}% WR together"
            if confidence:
                premade_text += f" · {escape(confidence)} confidence ({confidence_score}/100)"
            lines.append(premade_text)

        source_bits = []
        for key, label in (("rank_source", "rank"), ("history_source", "history"), ("mastery_source", "mastery")):
            value = str(stats.get(key, "") or "")
            if value:
                source_bits.append(f"{label} {value.replace('_', ' ')}")
        if source_bits:
            lines.append("<b>Sources:</b> " + " · ".join(escape(bit) for bit in source_bits))

        percentiles = dict(stats.get("local_percentiles", {}) or {})
        benchmark_parts = []
        for key, label in (
            ("avg_team_damage_share", "damage"),
            ("avg_cs_min", "farm"),
            ("avg_vision_min", "vision"),
            ("avg_kp", "impact"),
        ):
            value = percentiles.get(key)
            if value is not None:
                benchmark_parts.append(f"{label} {float(value):.0f}th")
        if benchmark_parts:
            lines.append(
                "<b>Local same-role benchmark:</b> "
                + " · ".join(benchmark_parts)
            )

        if str(stats.get("state", "")) == "fast":
            target_games = int(stats.get("analysis_target_games", 30) or 30)
            lines.append(
                f"<i>Final {target_games}-game analysis is still loading.</i>"
            )

        self.setToolTip("<br>".join(lines))


class TeamSection(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("LiveStackedTeam")
        self.player_cards: list[PlayerScoutCard] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(9, 7, 9, 9)
        root.setSpacing(6)

        header = QHBoxLayout()
        self.heading = QLabel(title)
        self.heading.setObjectName("LiveStackedTeamHeading")
        self.summary = QLabel("Loading")
        self.summary.setObjectName("LiveStackedTeamSummary")
        self.summary.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        header.addWidget(self.heading)
        header.addStretch()
        header.addWidget(self.summary)
        root.addLayout(header)

        self.cards_layout = QGridLayout()
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setHorizontalSpacing(7)
        self.cards_layout.setVerticalSpacing(0)
        for column in range(5):
            self.cards_layout.setColumnStretch(column, 1)
        root.addLayout(self.cards_layout)

    def clear(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.player_cards.clear()

    def add_players(self, cards: list[PlayerScoutCard]) -> None:
        self.clear()
        self.player_cards = list(cards[:5])
        self.summary.setText("Analysing")
        for column, card in enumerate(self.player_cards):
            self.cards_layout.addWidget(card, 0, column)

    def update_summary(self) -> None:
        progressed = [
            card
            for card in self.player_cards
            if getattr(card, "latest_stats", {}).get("state") in {"partial", "fast", "ready"}
        ]
        final = [
            card for card in progressed
            if card.latest_stats.get("state") == "ready"
        ]
        if not progressed:
            self.summary.setText("Analysing")
            return

        verified_offroles = sum(
            1 for card in final
            if card.latest_stats.get("role_state") == "off_role"
        )
        text = f"{len(final)}/{len(self.player_cards)} analysed"
        if len(final) < len(self.player_cards):
            text += f" · quick {len(progressed)}/{len(self.player_cards)}"
        if verified_offroles:
            text += f" · {verified_offroles} off-role"
        self.summary.setText(text)

    def show_empty(self, text: str) -> None:
        self.clear()
        label = QLabel(text)
        label.setObjectName("LiveStackedEmpty")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        self.cards_layout.addWidget(label, 0, 0, 1, 5)


class LiveMatchPage(QFrame):
    settings_requested = Signal()

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.setObjectName("ContentPanel")
        self._cards: dict[str, PlayerScoutCard] = {}
        self._roster_signature = ""

        self.icon_provider = ChampionIconProvider(self)
        self.icon_provider.icon_ready.connect(self._apply_champion_icon)

        self.client_asset_provider = LiveMatchClientAssetProvider(self)
        self.client_asset_provider.rank_icon_ready.connect(self._apply_rank_asset)
        self.client_asset_provider.role_icon_ready.connect(self._apply_role_asset)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 10)
        root.setSpacing(7)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)

        title = QLabel("Live Match")
        title.setObjectName("PageTitle")
        titles.addWidget(title)

        subtitle = QLabel("Quick scouting for all ten players.")
        subtitle.setObjectName("PageSubtitle")
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("DarkButton")
        header.addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(header)

        self.status_bar = QFrame()
        self.status_bar.setObjectName("LiveMatchStatusBar")
        status_layout = QHBoxLayout(self.status_bar)
        status_layout.setContentsMargins(10, 5, 10, 5)
        status_layout.setSpacing(7)

        self.status_dot = QLabel()
        self.status_dot.setObjectName("LiveMatchStatusDot")
        self.status_text = QLabel("Waiting for an active League match")
        self.status_text.setObjectName("LiveMatchStatusText")
        status_layout.addWidget(self.status_dot)
        status_layout.addWidget(self.status_text, 1)
        root.addWidget(self.status_bar)

        self.api_banner = QFrame()
        self.api_banner.setObjectName("LiveApiBanner")
        api_layout = QHBoxLayout(self.api_banner)
        api_layout.setContentsMargins(10, 6, 10, 6)
        api_help = QLabel(
            "Riot API key is optional — it adds champion mastery and fallback coverage."
        )
        api_help.setObjectName("CardMuted")
        api_layout.addWidget(api_help, 1)
        settings_button = QPushButton("API settings")
        settings_button.setObjectName("PrimaryButton")
        settings_button.clicked.connect(self.settings_requested.emit)
        api_layout.addWidget(settings_button)
        root.addWidget(self.api_banner)

        self.allies_section = TeamSection("YOUR TEAM")
        self.enemies_section = TeamSection("ENEMY TEAM")
        root.addWidget(self.allies_section)
        root.addWidget(self.enemies_section)
        root.addStretch()

        self.scout = LiveMatchScout(config, self)
        self.scout.roster_changed.connect(self.set_roster)
        self.scout.player_stats_changed.connect(self.apply_player_stats)
        self.scout.status_changed.connect(self.set_status)
        self.refresh_button.clicked.connect(lambda: self.scout.refresh(force=True))

        self._show_empty_state()
        self._sync_api_banner()
        self.scout.start()

    def refresh_now(self) -> None:
        self.scout.refresh(force=True)

    def update_credentials(self) -> None:
        self._sync_api_banner()
        self.scout.update_credentials()

    def _sync_api_banner(self) -> None:
        self.api_banner.setVisible(not bool(self.config.riot_api_key.strip()))

    def set_status(self, state: str, message: str) -> None:
        self.status_text.setText(message)
        self.status_dot.setProperty("state", state)
        self.status_dot.style().unpolish(self.status_dot)
        self.status_dot.style().polish(self.status_dot)

        if state in {"key_missing", "key_invalid"}:
            self.api_banner.show()
        elif state in {"ready", "loading", "loading_screen", "champ_select"}:
            self.api_banner.hide()

    @staticmethod
    def _payload_roster_signature(payload: dict[str, Any]) -> str:
        parts: list[str] = []
        for player in payload.get("players", ()):
            if not isinstance(player, dict):
                continue
            team = str(player.get("team", "") or "").upper()
            champion = normalize_champion_name(
                str(player.get("champion", "") or "Unknown")
            )
            identity = " ".join(
                str(
                    player.get("riot_id", "")
                    or player.get("game_name", "")
                    or ""
                ).strip().casefold().split()
            )
            parts.append(f"{team}:{identity or champion}:{champion}")
        return "|".join(sorted(parts))

    def set_roster(self, payload: dict[str, Any]) -> None:
        allies = list(payload.get("allies", ()))
        enemies = list(payload.get("enemies", ()))

        if not allies and not enemies:
            self._roster_signature = ""
            self._cards.clear()
            self._show_empty_state()
            return

        signature = self._payload_roster_signature(payload)
        if signature and signature == self._roster_signature and self._cards:
            # Repeated polls, manual refreshes and the Spectator -> port 2999
            # hand-off must not destroy and recreate the ten existing cards.
            return

        self._roster_signature = signature
        self._cards.clear()
        self.allies_section.add_players(
            [self._create_card(player) for player in allies]
        )
        self.enemies_section.add_players(
            [self._create_card(player) for player in enemies]
        )

    def _create_card(self, player: dict[str, Any]) -> PlayerScoutCard:
        card = PlayerScoutCard(player)
        self._cards[card.player_key] = card
        riot_id_alias = " ".join(
            str(
                player.get("riot_id", "")
                or player.get("game_name", "")
                or ""
            ).strip().casefold().split()
        )
        if riot_id_alias:
            # Spectator profiles are keyed by PUUID while port 2999 profiles are
            # keyed by Riot ID. Both aliases target the same persistent card.
            self._cards[riot_id_alias] = card

        self.icon_provider.request_icon(card.champion_name)
        self.client_asset_provider.request_role(card.role_code)
        return card

    def _apply_champion_icon(self, champion_key: str, pixmap: QPixmap) -> None:
        for card in self._cards.values():
            if card.champion_key == champion_key:
                card.set_champion_icon(pixmap)

    def _apply_rank_asset(self, tier: str, pixmap: QPixmap) -> None:
        for card in self._cards.values():
            if card.rank_tier == tier:
                card.set_rank_asset(pixmap)

    def _apply_role_asset(self, role: str, pixmap: QPixmap) -> None:
        for card in self._cards.values():
            if card.role_code == role:
                card.set_role_asset(pixmap)

    def apply_player_stats(self, player_key: str, stats: dict[str, Any]) -> None:
        card = self._cards.get(player_key)
        if card is None:
            return

        card.apply_stats(stats)
        if card.rank_tier not in {"", "LOADING", "UNAVAILABLE"}:
            self.client_asset_provider.request_rank(card.rank_tier)
        self.client_asset_provider.request_role(card.role_code)
        self.allies_section.update_summary()
        self.enemies_section.update_summary()

    def _show_empty_state(self) -> None:
        self.allies_section.show_empty(
            "Start or join a League match to load your team."
        )
        self.enemies_section.show_empty(
            "Enemy players appear when the live roster is available."
        )
