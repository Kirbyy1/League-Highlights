from __future__ import annotations

from typing import Any


def install_rank_record_ui_guard() -> None:
    """Hide untrustworthy season W/L without assuming one specific card layout."""

    from app.ui.live_match_page import PlayerScoutCard

    if getattr(PlayerScoutCard, "_rank_record_guard_installed", False):
        return
    original_apply_stats = PlayerScoutCard.apply_stats

    def guarded_apply_stats(self: Any, stats: dict[str, Any]) -> None:
        incoming = dict(stats or {})
        unavailable = incoming.get("ranked_record_available") is False
        if unavailable:
            # Preserve tier/division/LP, but never let an unreliable 100% local
            # season record reach either the original or replacement card layout.
            incoming.update(
                {
                    "wins": 0,
                    "losses": 0,
                    "games": 0,
                    "win_rate": None,
                    "ranked_games": 0,
                    "ranked_win_rate": None,
                }
            )

        original_apply_stats(self, incoming)
        merged = dict(getattr(self, "latest_stats", {}) or {})
        if not unavailable and merged.get("ranked_record_available") is not False:
            return
        if str(merged.get("rank_state", "") or "") != "ready":
            return

        ring = getattr(self, "win_rate_ring", None)
        clear_ring = getattr(ring, "clear_win_rate", None)
        if callable(clear_ring):
            clear_ring(
                "Season win/loss record is not exposed reliably by this local endpoint"
            )

        quick_line = getattr(self, "quick_line", None)
        if quick_line is not None and hasattr(quick_line, "setText"):
            quick_line.setText("Season record unavailable")

        rank_subtitle = getattr(self, "rank_subtitle", None)
        if rank_subtitle is not None and hasattr(rank_subtitle, "setText"):
            rank_subtitle.setText("Ranked Solo/Duo · Season record unavailable")

    PlayerScoutCard.apply_stats = guarded_apply_stats
    PlayerScoutCard._rank_record_guard_installed = True
