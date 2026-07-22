import json
from datetime import datetime
from pathlib import Path

from app.models import MatchContext
from app.services.clip_library import ClipLibrary


class FakeFfmpeg:
    @staticmethod
    def probe_duration(path: Path) -> float:
        return 12.0


def _write_clip(root: Path, name: str, match_id: str, champion: str, created: datetime) -> None:
    video = root / f"{name}.mp4"
    video.write_bytes(b"video")
    (root / f"{name}.json").write_text(
        json.dumps(
            {
                "label": name.upper(),
                "created_at": created.isoformat(),
                "duration_seconds": 12.0,
                "match_id": match_id,
                "player_name": "Alex#EUW",
                "champion_name": champion,
                "game_mode": "CLASSIC",
                "highlight_score": 50,
                "clip_window_start_wall": created.timestamp(),
                "clip_window_end_wall": created.timestamp() + 12,
                "is_match_reel": False,
            }
        ),
        encoding="utf-8",
    )


def test_groups_clips_by_match_and_orders_games(tmp_path: Path) -> None:
    first = datetime(2026, 7, 20, 12, 0, 0)
    second = datetime(2026, 7, 20, 13, 0, 0)
    _write_clip(tmp_path, "double_kill", "match_one", "Viego", first)
    _write_clip(tmp_path, "dragon", "match_one", "Viego", first.replace(minute=5))
    _write_clip(tmp_path, "triple_kill", "match_two", "Lee Sin", second)

    library = ClipLibrary(tmp_path, FakeFfmpeg())
    games = library.games()

    assert [game.match_id for game in games] == ["match_two", "match_one"]
    assert games[1].clip_count == 2
    assert games[1].champion_name == "Viego"


def test_finalize_match_updates_all_clip_metadata(tmp_path: Path) -> None:
    created = datetime(2026, 7, 20, 12, 0, 0)
    _write_clip(tmp_path, "kill", "match_one", "Viego", created)
    library = ClipLibrary(tmp_path, FakeFfmpeg())
    context = MatchContext(
        match_id="match_one",
        player_name="Alex#EUW",
        champion_name="Viego",
        game_mode="CLASSIC",
        map_name="Summoner's Rift",
        started_at=created.timestamp(),
    )

    library.finalize_match(context, "Win")
    clip = library.scan()[0]

    assert clip.match_result == "Win"
    assert clip.map_name == "Summoner's Rift"
    assert clip.match_started_at == created.timestamp()
