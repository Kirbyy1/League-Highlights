from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class HighlightEvent:
    """One lightweight League event kept while the rolling buffer is active.

    ``detected_at_monotonic`` is used for pruning because it cannot jump when the
    Windows clock changes. ``detected_at_wall`` is retained only so a completed
    clip can be mapped onto its reconstructed wall-clock segment timeline.
    """

    game_time: float
    event_type: str
    detected_at_monotonic: float
    detected_at_wall: float
    match_id: str = ""


@dataclass(slots=True, frozen=True)
class StoredHighlightEvent:
    """An event mapped into one saved video's local timeline."""

    relative_time: float
    event_type: str
    game_time: float | None = None

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "relative_time": round(max(0.0, float(self.relative_time)), 3),
            "type": str(self.event_type),
        }
        if self.game_time is not None:
            value["game_time"] = round(max(0.0, float(self.game_time)), 3)
        return value

    @classmethod
    def from_json(cls, value: object) -> "StoredHighlightEvent | None":
        if not isinstance(value, dict):
            return None
        try:
            relative_time = float(value.get("relative_time", 0.0))
        except (TypeError, ValueError):
            return None
        event_type = str(value.get("type") or value.get("event_type") or "").strip().upper()
        if not event_type:
            return None
        raw_game_time = value.get("game_time")
        try:
            game_time = float(raw_game_time) if raw_game_time is not None else None
        except (TypeError, ValueError):
            game_time = None
        return cls(max(0.0, relative_time), event_type, game_time)
