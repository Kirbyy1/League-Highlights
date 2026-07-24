from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt

from app.ui import live_match_page
from app.ui.win_rate_ring import CircularWinRate


def install_win_rate_ring() -> None:
    """Add a season-ranked Solo/Duo win-rate ring to Live Match cards."""

    original_card = live_match_page.PlayerScoutCard
    if getattr(original_card, "_circular_win_rate_installed", False):
        return

    class WinRatePlayerScoutCard(original_card):
        _circular_win_rate_installed = True

        def __init__(self, player: dict[str, Any]) -> None:
            super().__init__(player)

            self.win_rate_ring = CircularWinRate(self)

            root_layout = self.layout()
            rank_layout = None
            if root_layout is not None and root_layout.count() > 1:
                rank_layout = root_layout.itemAt(1).layout()

            if rank_layout is None:
                # Safe fallback if the original card layout changes later.
                self.win_rate_ring.setParent(self)
                self.win_rate_ring.move(max(0, self.width() - 66), 68)
                self.win_rate_ring.show()
            else:
                rank_layout.addWidget(
                    self.win_rate_ring,
                    0,
                    Qt.AlignmentFlag.AlignRight
                    | Qt.AlignmentFlag.AlignVCenter,
                )

            self._sync_ranked_win_rate_ring()

        def set_waiting_for_key(self) -> None:
            super().set_waiting_for_key()
            self.win_rate_ring.clear_win_rate(
                "Ranked Solo/Duo win rate unavailable"
            )

        def apply_stats(self, stats: dict[str, Any]) -> None:
            super().apply_stats(stats)
            self._sync_ranked_win_rate_ring()

        def _sync_ranked_win_rate_ring(self) -> None:
            """Display only the full-season Ranked Solo/Duo win rate.

            Recent-form win rates remain visible in the card text, but never
            control this ring.
            """

            stats = dict(getattr(self, "latest_stats", {}) or {})

            ranked_games = int(
                stats.get("ranked_games", stats.get("games", 0)) or 0
            )
            ranked_win_rate = stats.get(
                "ranked_win_rate",
                stats.get("win_rate"),
            )
            rank_state = str(
                stats.get("rank_state", "loading") or "loading"
            )

            if (
                rank_state == "ready"
                and ranked_games > 0
                and ranked_win_rate is not None
            ):
                value = float(ranked_win_rate)
                self.win_rate_ring.set_win_rate(value, ranked_games)
                self.win_rate_ring.setToolTip(
                    f"Season Ranked Solo/Duo: {value:.0f}% win rate "
                    f"over {ranked_games} games"
                )
                return

            if rank_state == "unranked":
                self.win_rate_ring.clear_win_rate(
                    "No Ranked Solo/Duo games this season"
                )
                return

            if rank_state == "unavailable":
                self.win_rate_ring.clear_win_rate(
                    "Ranked Solo/Duo win rate unavailable"
                )
                return

            self.win_rate_ring.clear_win_rate(
                "Ranked Solo/Duo win rate is loading"
            )

    WinRatePlayerScoutCard.__name__ = "PlayerScoutCard"
    WinRatePlayerScoutCard.__qualname__ = "PlayerScoutCard"
    live_match_page.PlayerScoutCard = WinRatePlayerScoutCard
