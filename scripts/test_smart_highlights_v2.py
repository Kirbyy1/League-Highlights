from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Running a file inside scripts\ makes Python use that folder as sys.path[0].
# Add the project root so imports such as "from app.models import ..." work.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.models import HighlightRequest
from app.services.league_events_v2 import LeagueEventMonitorV2


class _Config:
    buffer_seconds = 45

    def __init__(self, clip_dir: Path) -> None:
        self.clip_dir = clip_dir


def request(
    label: str,
    kind: str,
    start: float,
    end: float,
    score: int,
    *,
    reasons: tuple[str, ...] = (),
    victims: tuple[str, ...] = (),
) -> HighlightRequest:
    return HighlightRequest(
        label=label,
        event_started_at=start,
        event_ended_at=end,
        pre_seconds=8.0,
        post_seconds=7.0,
        match_id="test-match",
        player_name="Test Player",
        champion_name="Test Champion",
        event_game_time=100.0,
        event_kind=kind,
        automatic=True,
        highlight_score=score,
        score_reasons=reasons,
        victim_names=victims,
    )


def main() -> None:
    emitted: list[HighlightRequest] = []
    with tempfile.TemporaryDirectory() as temp:
        monitor = LeagueEventMonitorV2(
            _Config(Path(temp)),
            emitted.append,
            lambda _text, _connected: None,
        )

        monitor._v2_collect(
            request(
                "DOUBLE KILL",
                "kill",
                1000.0,
                1005.0,
                70,
                reasons=("2 champion kills", "survived at 9% health"),
                victims=("Enemy One", "Enemy Two"),
            )
        )
        monitor._v2_collect(
            request(
                "SUPPORT IMPACT",
                "assist",
                1002.0,
                1008.0,
                42,
                reasons=("2 assists in one fight",),
                victims=("Enemy Three",),
            )
        )
        monitor._v2_flush_pending()

        assert len(emitted) == 1, emitted
        merged = emitted[0]
        assert merged.label == "DOUBLE KILL", merged
        assert merged.event_kind == "fight", merged
        assert merged.event_started_at == 1000.0
        assert merged.event_ended_at == 1008.0
        assert merged.highlight_score > 70
        assert len(merged.victim_names) == 3
        assert merged.pre_seconds > 8.0
        assert merged.post_seconds > 7.0

        emitted.clear()
        monitor._v2_collect(
            request("DRAGON STEAL", "dragon", 2000.0, 2000.0, 65)
        )
        monitor._v2_collect(
            request(
                "SINGLE KILL",
                "kill",
                2002.0,
                2004.0,
                45,
                victims=("Enemy Jungler",),
            )
        )
        monitor._v2_flush_pending()

        assert len(emitted) == 1, emitted
        objective_fight = emitted[0]
        assert objective_fight.label == "DRAGON STEAL FIGHT", objective_fight
        assert objective_fight.event_kind == "fight"
        assert objective_fight.pre_seconds >= 10.0
        assert objective_fight.highlight_score >= 80

        monitor.stop()

    print("Smart Highlights V2 tests passed.")


if __name__ == "__main__":
    main()
