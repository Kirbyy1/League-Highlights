from __future__ import annotations

from typing import Any


def install_rank_record_ui_guard() -> None:
    from app.ui.live_match_page import PlayerScoutCard

    if getattr(PlayerScoutCard, "_rank_record_guard_installed", False):
        return
    original_apply_stats = PlayerScoutCard.apply_stats

    def guarded_apply_stats(self: Any, stats: dict[str, Any]) -> None:
        incoming = dict(stats or {})
        unavailable = incoming.get("ranked_record_available") is False
        original_apply_stats(self, incoming)
        merged = dict(getattr(self, "latest_stats", {}) or {})
        if not unavailable and merged.get("ranked_record_available") is not False:
            return
        rank_state = str(merged.get("rank_state", "") or "")
        sample = int(merged.get("sample_games", 0) or 0)
        if rank_state == "ready":
            self.win_rate_ring.clear_win_rate(
                "Season win/loss record is not exposed reliably by this local endpoint"
            )
            if not sample:
                self.quick_line.setText("Season record unavailable")

    PlayerScoutCard.apply_stats = guarded_apply_stats
    PlayerScoutCard._rank_record_guard_installed = True
