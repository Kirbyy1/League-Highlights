from __future__ import annotations

from dataclasses import dataclass

from app.highlight_event import StoredHighlightEvent

PRE_EVENT_SECONDS = 10.0
POST_EVENT_SECONDS = 7.0
EVENT_GROUP_GAP_SECONDS = 12.0
MIN_TRIM_SECONDS = 8.0
MANUAL_PRE_TRIGGER_SECONDS = 15.0


@dataclass(slots=True, frozen=True)
class TrimSuggestion:
    start_seconds: float
    end_seconds: float
    trigger_seconds: float
    reason: str

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)


class SmartTrimService:
    """Suggest a trim range using only saved timestamp metadata."""

    RELIABLE_TYPES = {
        "CHAMPION_KILL",
        "PLAYER_DEATH",
        "MULTIKILL",
        "ACE",
        "DRAGON",
        "DRAGON_STEAL",
        "BARON",
        "BARON_STEAL",
        "MANUAL_TRIGGER",
    }

    def __init__(
        self,
        pre_event_seconds: float = PRE_EVENT_SECONDS,
        post_event_seconds: float = POST_EVENT_SECONDS,
        group_gap_seconds: float = EVENT_GROUP_GAP_SECONDS,
    ) -> None:
        self.pre_event_seconds = max(0.0, float(pre_event_seconds))
        self.post_event_seconds = max(0.0, float(post_event_seconds))
        self.group_gap_seconds = max(0.0, float(group_gap_seconds))

    def suggest(
        self,
        duration_seconds: float,
        events: tuple[StoredHighlightEvent, ...] | list[StoredHighlightEvent],
        trigger_relative_seconds: float | None,
        *,
        manual: bool = False,
    ) -> TrimSuggestion:
        duration = max(0.25, float(duration_seconds))
        trigger = self._clamp(
            duration if trigger_relative_seconds is None else float(trigger_relative_seconds),
            0.0,
            duration,
        )

        if manual:
            start = max(0.0, trigger - MANUAL_PRE_TRIGGER_SECONDS)
            end = duration
            start, end = self._ensure_minimum(start, end, duration, trigger)
            return TrimSuggestion(start, end, trigger, "Manual trigger fallback")

        reliable = sorted(
            (
                event
                for event in events
                if event.event_type.upper() in self.RELIABLE_TYPES
                and 0.0 <= event.relative_time <= duration
            ),
            key=lambda item: item.relative_time,
        )
        groups = self.group_events(reliable)
        if groups:
            selected = min(groups, key=lambda group: self._distance_to_group(trigger, group))
            start = selected[0].relative_time - self.pre_event_seconds
            end = selected[-1].relative_time + self.post_event_seconds
            start = self._clamp(start, 0.0, duration)
            end = self._clamp(end, 0.0, duration)
            start, end = self._ensure_minimum(start, end, duration, trigger)
            return TrimSuggestion(start, end, trigger, "Nearby League events")

        start = max(0.0, trigger - self.pre_event_seconds)
        end = min(duration, trigger + self.post_event_seconds)
        start, end = self._ensure_minimum(start, end, duration, trigger)
        return TrimSuggestion(start, end, trigger, "Safe trigger fallback")

    def group_events(
        self, events: tuple[StoredHighlightEvent, ...] | list[StoredHighlightEvent]
    ) -> list[tuple[StoredHighlightEvent, ...]]:
        ordered = sorted(events, key=lambda item: item.relative_time)
        if not ordered:
            return []
        groups: list[list[StoredHighlightEvent]] = [[ordered[0]]]
        for event in ordered[1:]:
            if event.relative_time - groups[-1][-1].relative_time <= self.group_gap_seconds:
                groups[-1].append(event)
            else:
                groups.append([event])
        return [tuple(group) for group in groups]

    @staticmethod
    def _distance_to_group(
        trigger: float, group: tuple[StoredHighlightEvent, ...]
    ) -> float:
        start = group[0].relative_time
        end = group[-1].relative_time
        if start <= trigger <= end:
            return 0.0
        return min(abs(trigger - start), abs(trigger - end))

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _ensure_minimum(
        start: float,
        end: float,
        duration: float,
        focus: float,
    ) -> tuple[float, float]:
        minimum = min(MIN_TRIM_SECONDS, duration)
        if end - start >= minimum:
            return start, end
        half = minimum / 2.0
        start = max(0.0, focus - half)
        end = min(duration, start + minimum)
        start = max(0.0, end - minimum)
        return start, end
