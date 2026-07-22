from __future__ import annotations

import time
import unittest
from types import SimpleNamespace

from app.models import MatchContext, PlayerIdentity, PlayerSnapshot
from app.services.league_events import LeagueEventMonitor


class LeagueEventMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.requests = []
        self.matches = []
        self.config = SimpleNamespace(
            auto_clip_single_kill=True,
            auto_clip_double_kill=True,
            auto_clip_triple_kill=True,
            auto_clip_quadra_kill=True,
            auto_clip_pentakill=True,
            auto_clip_dragon=True,
            auto_clip_baron=True,
            smart_highlights_enabled=True,
            smart_sensitivity="balanced",
        )
        self.monitor = LeagueEventMonitor(
            self.config,
            self.requests.append,
            lambda *_: None,
            self.matches.append,
            kill_settle_seconds=0.01,
        )
        alex = PlayerIdentity(
            riot_id="Alex#EUW",
            game_name="Alex",
            champion_name="Viego",
            team="ORDER",
            level=12,
            aliases=frozenset({"alex", "alex#euw"}),
        )
        enemy = PlayerIdentity(
            riot_id="Enemy#EUW",
            game_name="Enemy",
            champion_name="Ahri",
            team="CHAOS",
            level=13,
            aliases=frozenset({"enemy", "enemy#euw"}),
        )
        ally = PlayerIdentity(
            riot_id="Ally#EUW",
            game_name="Ally",
            champion_name="Nautilus",
            team="ORDER",
            level=11,
            aliases=frozenset({"ally", "ally#euw"}),
        )
        self.monitor._active_aliases = set(alex.aliases)
        self.monitor._active_identity = alex
        self.monitor._active_team = "ORDER"
        self.monitor._identity_by_alias = {
            alias: player
            for player in (alex, enemy, ally)
            for alias in player.aliases
        }
        self.monitor._team_by_alias = {
            alias: player.team
            for player in (alex, enemy, ally)
            for alias in player.aliases
        }
        self.monitor._current_match = MatchContext(
            "20260720_120000_Alex", "Alex#EUW", "Viego", "CLASSIC", "Map11", time.time() - 600
        )

    def test_exact_triple_kill_is_one_scored_clip_with_names(self) -> None:
        start = time.time()
        self.monitor._snapshots.extend(
            [
                PlayerSnapshot(start, 100.0, 80.0, 12, False, 5, 2, 3),
                PlayerSnapshot(start + 5, 105.0, 8.0, 12, False, 8, 2, 3),
            ]
        )
        for event_id, game_time in enumerate((100.0, 104.0, 108.0), start=1):
            self.monitor._handle_event(
                {
                    "EventID": event_id,
                    "EventName": "ChampionKill",
                    "EventTime": game_time,
                    "KillerName": "Alex#EUW",
                    "VictimName": "Enemy#EUW",
                    "Assisters": [],
                }
            )
        self.monitor._handle_event(
            {
                "EventName": "Multikill",
                "EventTime": 108.0,
                "KillerName": "Alex#EUW",
                "KillStreak": 3,
            }
        )
        self.monitor._last_player_kill_at = time.monotonic() - 1
        self.monitor._flush_pending_kills_if_ready()
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(request.clean_label, "TRIPLE KILL")
        self.assertEqual(request.player_name, "Alex#EUW")
        self.assertEqual(request.champion_name, "Viego")
        self.assertEqual(request.victim_names, ("Enemy#EUW", "Enemy#EUW", "Enemy#EUW"))
        self.assertGreaterEqual(request.highlight_score, 55)
        self.assertIn("survived at 8% health", request.score_reasons)

    def test_routine_single_kill_is_filtered_in_balanced_mode(self) -> None:
        self.monitor._handle_event(
            {
                "EventName": "ChampionKill",
                "EventTime": 100,
                "KillerName": "Alex",
                "VictimName": "Enemy",
                "Assisters": ["Ally"],
            }
        )
        self.monitor._last_player_kill_at = time.monotonic() - 1
        self.monitor._flush_pending_kills_if_ready()
        self.assertEqual(self.requests, [])

    def test_low_health_solo_single_is_kept(self) -> None:
        wall = time.time()
        self.monitor._snapshots.extend(
            [
                PlayerSnapshot(wall, 100, 20.0, 12, False, 1, 0, 0),
                PlayerSnapshot(wall + 1, 101, 7.0, 12, False, 2, 0, 0),
            ]
        )
        self.monitor._handle_event(
            {
                "EventName": "ChampionKill",
                "EventTime": 100,
                "KillerName": "Alex",
                "VictimName": "Enemy",
                "Assisters": [],
            }
        )
        self.monitor._last_player_kill_at = time.monotonic() - 1
        self.monitor._flush_pending_kills_if_ready()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].clean_label, "SINGLE KILL")
        self.assertIn("1 solo kill", self.requests[0].score_reasons)

    def test_routine_team_dragon_is_ignored(self) -> None:
        self.monitor._handle_event(
            {
                "EventName": "DragonKill",
                "EventTime": 321.0,
                "KillerName": "Ally",
                "DragonType": "Fire",
                "Stolen": "False",
            }
        )
        self.assertEqual(self.requests, [])

    def test_team_dragon_steal_is_saved_and_precisely_framed(self) -> None:
        self.monitor._handle_event(
            {
                "EventName": "DragonKill",
                "EventTime": 321.0,
                "KillerName": "Ally",
                "DragonType": "Fire",
                "Stolen": "True",
            }
        )
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].clean_label, "DRAGON STEAL")
        self.assertEqual(self.requests[0].pre_seconds, 8.0)
        self.assertEqual(self.requests[0].post_seconds, 6.0)
        self.assertGreaterEqual(self.requests[0].highlight_score, 65)

    def test_enemy_baron_is_ignored(self) -> None:
        self.monitor._handle_event(
            {"EventName": "BaronKill", "KillerName": "Enemy", "Stolen": "False"}
        )
        self.assertEqual(self.requests, [])


    def test_smart_master_switch_disables_automatic_clips(self) -> None:
        self.config.smart_highlights_enabled = False
        self.monitor._handle_event(
            {
                "EventName": "ChampionKill",
                "EventTime": 100,
                "KillerName": "Alex",
                "VictimName": "Enemy",
                "Assisters": [],
            }
        )
        self.monitor._handle_event(
            {
                "EventName": "DragonKill",
                "EventTime": 110,
                "KillerName": "Ally",
                "DragonType": "Elder",
                "Stolen": "True",
            }
        )
        self.monitor._last_player_kill_at = time.monotonic() - 1
        self.monitor._flush_pending_kills_if_ready()
        self.assertEqual(self.requests, [])

    def test_game_end_emits_match_lifecycle(self) -> None:
        self.monitor._handle_event({"EventName": "GameEnd", "Result": "Win"})
        self.assertEqual(len(self.matches), 1)
        self.assertEqual(self.matches[0].action, "ended")
        self.assertEqual(self.matches[0].result, "WIN")


if __name__ == "__main__":
    unittest.main()
