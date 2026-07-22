from datetime import datetime
from pathlib import Path

from app.models import ClipInfo, GameHighlights


def test_game_metadata_helpers():
    clip = ClipInfo(
        path=Path('clip.mp4'),
        thumbnail_path=None,
        created_at=datetime.now(),
        duration_seconds=18.0,
        event_game_time=754.0,
    )
    game = GameHighlights(
        match_id='m1', clips=[clip], started_at=datetime.now(),
        champion_name='Viego', result='WIN', kills=12, deaths=4, assists=9,
        duration_seconds=1902.0,
    )
    assert game.kda_text == '12 / 4 / 9'
    assert game.match_duration_text == '31:42'
    assert game.normalized_result == 'Victory'
    assert game.timeline_duration_seconds == 1902.0
    assert clip.match_time_text == '12:34'
