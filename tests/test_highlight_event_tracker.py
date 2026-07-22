from app.services.highlight_event_tracker import HighlightEventTracker


def test_events_map_into_saved_clip_relative_time() -> None:
    tracker = HighlightEventTracker()
    tracker.record(
        "CHAMPION_KILL",
        game_time=321,
        match_id="match",
        detected_at_monotonic=100,
        detected_at_wall=1_020,
    )
    tracker.record(
        "BARON_STEAL",
        game_time=330,
        match_id="other",
        detected_at_monotonic=101,
        detected_at_wall=1_025,
    )
    events = tracker.events_for_clip(1_000, 1_045, match_id="match")
    assert len(events) == 1
    assert events[0].event_type == "CHAMPION_KILL"
    assert events[0].relative_time == 20
    assert events[0].game_time == 321
