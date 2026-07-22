from app.highlight_event import StoredHighlightEvent
from app.services.smart_trim_service import SmartTrimService


def event(at: float, kind: str = "CHAMPION_KILL") -> StoredHighlightEvent:
    return StoredHighlightEvent(at, kind, at + 100)


def test_nearby_events_are_grouped_and_framed() -> None:
    service = SmartTrimService()
    events = [event(20), event(27), event(42)]
    groups = service.group_events(events)
    assert [len(group) for group in groups] == [2, 1]

    suggestion = service.suggest(45, events, 28, manual=False)
    assert suggestion.start_seconds == 10
    assert suggestion.end_seconds == 34


def test_group_closest_to_trigger_is_selected() -> None:
    service = SmartTrimService()
    suggestion = service.suggest(60, [event(10), event(35), event(39)], 38, manual=False)
    assert suggestion.start_seconds == 25
    assert suggestion.end_seconds == 46


def test_manual_rolling_clip_uses_last_fifteen_seconds_to_end() -> None:
    suggestion = SmartTrimService().suggest(45, [], 44.5, manual=True)
    assert 29.4 <= suggestion.start_seconds <= 29.6
    assert suggestion.end_seconds == 45


def test_fallback_is_never_shorter_than_eight_seconds() -> None:
    suggestion = SmartTrimService().suggest(30, [], 1, manual=False)
    assert suggestion.duration_seconds >= 8
    assert suggestion.start_seconds == 0
