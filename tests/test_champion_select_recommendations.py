from __future__ import annotations

import unittest

from app.services.champion_select_recommendations import build_champion_select_advice


CHAMPIONS = {
    3: "Galio",
    12: "Alistar",
    40: "Janna",
    90: "Malzahar",
    103: "Ahri",
    127: "Lissandra",
    157: "Yasuo",
    238: "Zed",
    360: "Samira",
    711: "Vex",
}


def champion_name(champion_id: int) -> str:
    return CHAMPIONS.get(champion_id, f"Champion {champion_id}")


class ChampionSelectRecommendationTests(unittest.TestCase):
    def test_recommends_mid_answers_to_enemy_assassins(self) -> None:
        advice = build_champion_select_advice(
            {
                "localPlayerCellId": 1,
                "myTeam": [
                    {"cellId": 1, "championId": 0, "assignedPosition": "middle"},
                    {"cellId": 2, "championId": 103, "assignedPosition": "utility"},
                ],
                "theirTeam": [
                    {"cellId": 6, "championId": 238, "assignedPosition": "middle"},
                    {"cellId": 7, "championId": 157, "assignedPosition": "top"},
                ],
                "bans": {"myTeamBans": [], "theirTeamBans": []},
            },
            champion_name,
        )

        self.assertEqual(advice["local_role"], "MIDDLE")
        self.assertEqual([player["champion"] for player in advice["enemies"]], ["Zed", "Yasuo"])
        recommended = [item["champion"] for item in advice["recommendations"][:4]]
        self.assertIn("Lissandra", recommended)
        self.assertIn("Vex", recommended)

    def test_respects_bans_and_current_picks(self) -> None:
        advice = build_champion_select_advice(
            {
                "localPlayerCellId": 1,
                "myTeam": [
                    {"cellId": 1, "championId": 0, "assignedPosition": "middle"},
                    {"cellId": 2, "championId": 711, "assignedPosition": "utility"},
                ],
                "theirTeam": [
                    {"cellId": 6, "championId": 238, "assignedPosition": "middle"},
                ],
                "bans": {"myTeamBans": [127], "theirTeamBans": []},
            },
            champion_name,
        )

        recommended = [item["champion"] for item in advice["recommendations"]]
        self.assertNotIn("Lissandra", recommended)
        self.assertNotIn("Vex", recommended)
        self.assertIn("Malzahar", recommended)

    def test_support_recommendations_use_enemy_and_ally_context(self) -> None:
        advice = build_champion_select_advice(
            {
                "localPlayerCellId": 5,
                "myTeam": [
                    {"cellId": 1, "championId": 103, "assignedPosition": "middle"},
                    {"cellId": 5, "championId": 0, "assignedPosition": "utility"},
                ],
                "theirTeam": [
                    {"cellId": 6, "championId": 360, "assignedPosition": "bottom"},
                ],
                "bans": {"myTeamBans": [], "theirTeamBans": []},
            },
            champion_name,
        )

        recommended = [item["champion"] for item in advice["recommendations"][:3]]
        self.assertIn("Janna", recommended)
        self.assertTrue(any("Samira" in reason for reason in advice["recommendations"][0]["reasons"]))
        self.assertIn("Ahri", [player["champion"] for player in advice["allies"]])


if __name__ == "__main__":
    unittest.main()
