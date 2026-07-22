from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path

LOGGER = logging.getLogger(__name__)


class FeedbackProfile:
    """Tiny local preference model built from Good / Not impressive ratings.

    It never inspects video or uploads data. Ratings adjust the score threshold for
    similar future event kinds: repeated bad ratings make the filter stricter,
    while repeated good ratings make it slightly more permissive.
    """

    def __init__(self, clip_dir: Path, refresh_seconds: float = 15.0) -> None:
        self.clip_dir = clip_dir
        self.refresh_seconds = refresh_seconds
        self._next_refresh = 0.0
        self._adjustments: dict[str, int] = {}

    def threshold_adjustment(self, event_kind: str, label: str) -> int:
        if time.monotonic() >= self._next_refresh:
            self._refresh()
        kind_key = self._key(event_kind)
        label_key = self._key(label)
        return self._adjustments.get(kind_key, self._adjustments.get(label_key, 0))

    def invalidate(self) -> None:
        self._next_refresh = 0.0

    def _refresh(self) -> None:
        votes: dict[str, list[int]] = defaultdict(list)
        try:
            paths = list(self.clip_dir.glob("*.json"))
        except OSError:
            paths = []
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict) or data.get("is_match_reel"):
                continue
            rating = str(data.get("rating", ""))
            if rating not in {"good", "bad"}:
                continue
            event_kind = self._key(str(data.get("event_kind", "")))
            label = self._key(str(data.get("label", "")))
            vote = -2 if rating == "good" else 3
            if event_kind:
                votes[event_kind].append(vote)
            if label:
                votes[label].append(vote)

        adjustments: dict[str, int] = {}
        for key, values in votes.items():
            # Require at least two opinions before changing automatic behavior.
            if len(values) < 2:
                continue
            adjustments[key] = max(-10, min(15, sum(values)))
        self._adjustments = adjustments
        self._next_refresh = time.monotonic() + self.refresh_seconds
        LOGGER.debug("Loaded smart-highlight feedback adjustments: %s", adjustments)

    @staticmethod
    def _key(value: str) -> str:
        return " ".join(value.strip().casefold().replace("_", " ").split())
