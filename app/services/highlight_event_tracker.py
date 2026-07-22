from __future__ import annotations

import threading
import time
from collections import deque

from app.highlight_event import HighlightEvent, StoredHighlightEvent


class HighlightEventTracker:
    """Keep only tiny timestamp objects needed by Smart Trim.

    No media is inspected. Events are captured from Riot's local Live Client Data
    API and later mapped into a saved video's local timeline. The conceptual
    conversion is::

        clip_relative_time = event_capture_monotonic - clip_buffer_start_monotonic

    The recorder reconstructs completed segment bounds with wall timestamps, so
    ``events_for_clip`` performs the equivalent wall-clock subtraction while the
    monotonic timestamp remains the authoritative value for pruning.
    """

    def __init__(self, retention_seconds: float = 180.0) -> None:
        self.retention_seconds = max(60.0, float(retention_seconds))
        self._events: deque[HighlightEvent] = deque(maxlen=2048)
        self._lock = threading.Lock()
        self._current_match_id = ""

    def begin_match(self, match_id: str) -> None:
        with self._lock:
            self._current_match_id = str(match_id or "")
            self._prune_locked(time.monotonic())

    def add(self, event: HighlightEvent) -> None:
        with self._lock:
            self._events.append(event)
            if event.match_id:
                self._current_match_id = event.match_id
            self._prune_locked(event.detected_at_monotonic)

    def record(
        self,
        event_type: str,
        *,
        game_time: float = 0.0,
        match_id: str = "",
        detected_at_monotonic: float | None = None,
        detected_at_wall: float | None = None,
    ) -> HighlightEvent:
        event = HighlightEvent(
            game_time=max(0.0, float(game_time)),
            event_type=str(event_type or "UNKNOWN").strip().upper(),
            detected_at_monotonic=(
                time.monotonic() if detected_at_monotonic is None else float(detected_at_monotonic)
            ),
            detected_at_wall=(time.time() if detected_at_wall is None else float(detected_at_wall)),
            match_id=str(match_id or self._current_match_id),
        )
        self.add(event)
        return event

    def events_for_clip(
        self,
        clip_start_wall: float,
        clip_end_wall: float,
        *,
        match_id: str = "",
    ) -> tuple[StoredHighlightEvent, ...]:
        start = float(clip_start_wall)
        end = float(clip_end_wall)
        if end <= start:
            return ()
        wanted_match = str(match_id or "")
        with self._lock:
            snapshot = tuple(self._events)
        output: list[StoredHighlightEvent] = []
        for event in snapshot:
            if wanted_match and event.match_id and event.match_id != wanted_match:
                continue
            if not (start - 0.15 <= event.detected_at_wall <= end + 0.15):
                continue
            output.append(
                StoredHighlightEvent(
                    relative_time=max(0.0, min(end - start, event.detected_at_wall - start)),
                    event_type=event.event_type,
                    game_time=event.game_time,
                )
            )
        output.sort(key=lambda item: item.relative_time)
        return tuple(output)

    def _prune_locked(self, now_monotonic: float) -> None:
        cutoff = float(now_monotonic) - self.retention_seconds
        while self._events and self._events[0].detected_at_monotonic < cutoff:
            self._events.popleft()
