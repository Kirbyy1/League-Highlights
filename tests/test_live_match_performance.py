from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.live_match_diagnostics import (
    LiveMatchDiagnostics,
    set_console_debug_override,
)
from app.services.live_match_performance_helpers import (
    ranked_record_credibility,
    roster_quality,
    should_defer_placeholder_roster,
    stable_match_key,
)


def roster(names, *, game_id="123", started=1000):
    players = []
    for index, name in enumerate(names):
        players.append({
            "riot_id": name,
            "player_key": f"id-{index}",
            "champion": f"Champion{index}",
            "champion_id": index + 1,
            "team": "ORDER" if index < 5 else "CHAOS",
        })
    return {
        "players": players,
        "game_id": game_id,
        "game_started_at": started,
        "queue_id": 420,
        "gameflow_phase": "InProgress",
    }


class HelperTests(unittest.TestCase):
    def test_source_handoff_keeps_same_match_key(self):
        placeholders = roster([f"Player {i}" for i in range(1, 11)])
        real = roster([f"Real{i}#EUW" for i in range(1, 11)])
        self.assertEqual(stable_match_key(placeholders), stable_match_key(real))

    def test_start_time_distinguishes_matches_without_game_id(self):
        first = roster([f"A{i}" for i in range(10)], game_id="", started=1000)
        second = roster([f"A{i}" for i in range(10)], game_id="", started=2000)
        self.assertNotEqual(stable_match_key(first), stable_match_key(second))

    def test_placeholder_grace(self):
        placeholders = roster([f"Player {i}" for i in range(1, 11)])
        self.assertTrue(should_defer_placeholder_roster(
            placeholders, first_seen_at=10.0, now=15.0,
            shared_playerlist_ready=False, grace_seconds=8.0,
        ))
        self.assertFalse(should_defer_placeholder_roster(
            placeholders, first_seen_at=10.0, now=19.0,
            shared_playerlist_ready=False, grace_seconds=8.0,
        ))

    def test_roster_quality(self):
        quality = roster_quality(roster([f"Real{i}#EUW" for i in range(10)]))
        self.assertEqual(quality["players"], 10)
        self.assertEqual(quality["real_names"], 10)

    def test_ranked_record_credibility(self):
        self.assertEqual(ranked_record_credibility({"wins": 7, "losses": 0})[0], True)
        self.assertEqual(ranked_record_credibility({"wins": 140, "losses": 0})[0], False)
        self.assertEqual(ranked_record_credibility({"wins": 10})[0], False)
        self.assertEqual(ranked_record_credibility({"wins": 50, "losses": 45})[0], True)


class DiagnosticsTests(unittest.TestCase):
    def test_cycle_summary_is_not_cumulative_and_noisy_requests_deduplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            class Config:
                log_dir = Path(directory)
                settings_file = Path(directory) / "settings.json"
            Config.settings_file.write_text(json.dumps({"console_debug_enabled": True}), encoding="utf-8")
            set_console_debug_override(True)
            trace = LiveMatchDiagnostics(Config())
            trace.cycle_started(1, force=False)
            trace.request_finished("/lol-gameflow/v1/gameflow-phase", 0.01, payload="InProgress")
            trace.request_finished("/lol-gameflow/v1/gameflow-phase", 0.01, payload="InProgress")
            trace.cycle_finished(1, outcome="waiting")
            text = trace.path.read_text(encoding="utf-8")
            # Identical phase payload is serialized once, while the cycle counter
            # still reports the two actual calls.
            self.assertEqual(text.count('"endpoint": "/lol-gameflow/v1/gameflow-phase"'), 1)
            self.assertIn('"count": 2', text)
            trace.cycle_started(2, force=False)
            trace.cycle_finished(2, outcome="waiting")
            text = trace.path.read_text(encoding="utf-8")
            last_cycle = text.rsplit('"event": "cycle_finished"', 1)[-1]
            self.assertIn('"cycle_request_summary": {}', last_cycle)
            set_console_debug_override(None)


if __name__ == "__main__":
    unittest.main()
