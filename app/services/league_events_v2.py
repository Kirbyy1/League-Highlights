from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
from collections import deque
from collections.abc import Callable
from dataclasses import replace

from app.models import HighlightRequest, MatchLifecycleEvent
from app.services.league_events import LeagueEventMonitor

LOGGER = logging.getLogger(__name__)


class LeagueEventMonitorV2(LeagueEventMonitor):
    """Smart Highlights V2 compatibility layer.

    The existing LeagueEventMonitor remains responsible for exact Riot-event
    detection and explainable scoring. This subclass receives those scored
    requests before they reach the recorder and adds:

    * fight grouping for related kills, assists, and objective steals;
    * adaptive pre-roll and post-roll based on play context;
    * duplicate suppression for near-identical automatic clips;
    * stronger combined labels and score reasons.

    No video frames are inspected and no data leaves the computer.
    """

    GROUP_GAP_SECONDS = 12.0
    COMBAT_FLUSH_DELAY_SECONDS = 1.8
    OBJECTIVE_FLUSH_DELAY_SECONDS = 12.0
    RECENT_DUPLICATE_RETENTION_SECONDS = 45.0
    DUPLICATE_OVERLAP_RATIO = 0.72

    _GROUPABLE_KINDS = frozenset({"kill", "assist", "dragon", "baron", "fight"})
    _COMBAT_KINDS = frozenset({"kill", "assist", "fight"})
    _OBJECTIVE_KINDS = frozenset({"dragon", "baron"})

    def __init__(
        self,
        config,
        highlight_callback: Callable[[HighlightRequest], None],
        status_callback,
        match_callback=None,
        event_callback=None,
        **kwargs,
    ) -> None:
        self._v2_downstream_callback = highlight_callback
        self._v2_lock = threading.RLock()
        self._v2_pending: list[HighlightRequest] = []
        self._v2_timer: threading.Timer | None = None
        self._v2_recent: deque[
            tuple[str, float, float, str, int, float]
        ] = deque(maxlen=24)
        self._v2_stopping = False

        super().__init__(
            config,
            self._v2_collect,
            status_callback,
            match_callback,
            event_callback,
            **kwargs,
        )


    # ------------------------------------------------------------------
    # Adaptive Live Client polling
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Use fast polling in-game and a quiet backoff while League is closed."""

        disconnected_wait = 2.5
        self._set_status("Waiting for active match data", False)

        while not self._stop_event.is_set():
            connected_this_cycle = False
            try:
                now = time.monotonic()
                if now >= self._next_identity_refresh:
                    self._refresh_live_state()
                    self._next_identity_refresh = now + 1.0

                payload = self._fetch_json("/eventdata")
                raw_events = (
                    payload.get("Events", [])
                    if isinstance(payload, dict)
                    else []
                )
                events = [
                    event
                    for event in raw_events
                    if isinstance(event, dict)
                ]
                self._consume_snapshot(events)
                self._last_success_wall = time.time()
                self._set_status(self._connected_status_text(), True)
                connected_this_cycle = True
            except (
                OSError,
                TimeoutError,
                urllib.error.URLError,
                json.JSONDecodeError,
            ):
                self._set_status("Waiting for active match data", False)
                if (
                    self._current_match is not None
                    and not self._match_ended
                    and self._last_success_wall
                    and time.time() - self._last_success_wall > 20.0
                ):
                    self._end_match("UNKNOWN")
            except Exception:
                LOGGER.exception("League live-event monitor failed")
                self._set_status(
                    "Live event detection error — retrying",
                    False,
                )

            self._flush_pending_kills_if_ready()
            self._flush_pending_assists_if_ready()

            wait_seconds = (
                self.poll_interval
                if connected_this_cycle
                else disconnected_wait
            )
            self._stop_event.wait(wait_seconds)

    # ------------------------------------------------------------------
    # Candidate collection and fight grouping
    # ------------------------------------------------------------------

    def _v2_collect(self, request: HighlightRequest) -> None:
        """Collect related automatic candidates before sending one clip request."""

        if (
            self._v2_stopping
            or not request.automatic
            or request.event_kind.casefold() not in self._GROUPABLE_KINDS
            or not request.has_precise_window
        ):
            self._v2_emit_direct(request)
            return

        previous_batch: tuple[HighlightRequest, ...] = ()
        with self._v2_lock:
            if self._v2_pending and not self._v2_is_related_locked(request):
                previous_batch = self._v2_take_pending_locked()

            self._v2_pending.append(request)
            self._v2_schedule_locked()

        if previous_batch:
            self._v2_emit_merged(previous_batch)

    def _v2_is_related_locked(self, request: HighlightRequest) -> bool:
        if not self._v2_pending:
            return True

        pending_match = next(
            (item.match_id for item in self._v2_pending if item.match_id),
            "",
        )
        if pending_match and request.match_id and pending_match != request.match_id:
            return False

        pending_start = min(
            float(item.event_started_at)
            for item in self._v2_pending
            if item.event_started_at is not None
        )
        pending_end = max(
            float(item.event_ended_at)
            for item in self._v2_pending
            if item.event_ended_at is not None
        )
        request_start = float(request.event_started_at)
        request_end = float(request.event_ended_at)

        if request_start > pending_end:
            gap = request_start - pending_end
        elif pending_start > request_end:
            gap = pending_start - request_end
        else:
            gap = 0.0
        return gap <= self.GROUP_GAP_SECONDS

    def _v2_schedule_locked(self) -> None:
        if self._v2_timer is not None:
            self._v2_timer.cancel()

        kinds = {item.event_kind.casefold() for item in self._v2_pending}
        delay = (
            self.COMBAT_FLUSH_DELAY_SECONDS
            if kinds & self._COMBAT_KINDS
            else self.OBJECTIVE_FLUSH_DELAY_SECONDS
        )
        timer = threading.Timer(delay, self._v2_timer_fired)
        timer.daemon = True
        self._v2_timer = timer
        timer.start()

    def _v2_timer_fired(self) -> None:
        self._v2_flush_pending()

    def _v2_take_pending_locked(self) -> tuple[HighlightRequest, ...]:
        if self._v2_timer is not None:
            self._v2_timer.cancel()
            self._v2_timer = None
        batch = tuple(self._v2_pending)
        self._v2_pending.clear()
        return batch

    def _v2_flush_pending(self) -> None:
        with self._v2_lock:
            batch = self._v2_take_pending_locked()
        if batch:
            self._v2_emit_merged(batch)

    # ------------------------------------------------------------------
    # Merge, label, adaptive timing, and duplicate control
    # ------------------------------------------------------------------

    def _v2_emit_merged(self, requests: tuple[HighlightRequest, ...]) -> None:
        try:
            merged = self._v2_merge_requests(requests)
        except Exception:
            LOGGER.exception("Smart Highlights V2 could not merge a fight; using best candidate")
            merged = max(requests, key=lambda item: int(item.highlight_score or 0))

        if self._v2_is_duplicate(merged):
            LOGGER.info(
                "Smart Highlights V2 suppressed duplicate %s (score %s)",
                merged.clean_label,
                merged.highlight_score,
            )
            return

        LOGGER.info(
            "Smart Highlights V2 ready: %s • score %s • window %.1fs + %.1fs",
            merged.clean_label,
            merged.highlight_score,
            merged.pre_seconds,
            merged.post_seconds,
        )
        self._v2_emit_direct(merged)

    def _v2_emit_direct(self, request: HighlightRequest) -> None:
        try:
            self._v2_downstream_callback(request)
        except Exception:
            LOGGER.exception("Smart Highlights V2 downstream callback failed")

    def _v2_merge_requests(
        self,
        requests: tuple[HighlightRequest, ...],
    ) -> HighlightRequest:
        if not requests:
            raise ValueError("At least one request is required")

        valid = tuple(item for item in requests if item.has_precise_window)
        if not valid:
            return max(requests, key=lambda item: int(item.highlight_score or 0))

        base = max(
            valid,
            key=lambda item: (
                int(item.highlight_score or 0),
                self._v2_label_priority(item.clean_label),
            ),
        )
        starts = [float(item.event_started_at) for item in valid]
        ends = [float(item.event_ended_at) for item in valid]
        event_start = min(starts)
        event_end = max(ends)
        duration = max(0.0, event_end - event_start)

        kinds = {item.event_kind.casefold() for item in valid}
        combat_present = bool(kinds & self._COMBAT_KINDS)
        objective_present = bool(kinds & self._OBJECTIVE_KINDS)

        reasons = self._v2_unique(
            reason
            for item in valid
            for reason in item.score_reasons
            if reason
        )
        lowered_reasons = " ".join(reasons).casefold()
        labels = tuple(item.clean_label for item in valid)

        score = max(int(item.highlight_score or 0) for item in valid)
        if len(valid) > 1:
            score += min(18, 6 * (len(valid) - 1))
            reasons += (f"grouped {len(valid)} related highlight events",)
        if {"kill", "assist"} <= kinds:
            score += 8
            reasons += ("combined kill and teamfight participation",)
        if objective_present and combat_present:
            score += 15
            reasons += ("objective converted into the same fight",)
        score = min(150, score)

        pre_seconds = max(float(item.pre_seconds or 0.0) for item in valid)
        post_seconds = max(float(item.post_seconds or 0.0) for item in valid)

        # Give longer or higher-value fights more context without blindly saving
        # the entire rolling buffer.
        if duration >= 8.0:
            pre_seconds += 1.5
            post_seconds += 1.5
        if duration >= 14.0:
            pre_seconds += 1.5
            post_seconds += 1.5
        if score >= 70:
            pre_seconds += 1.5
            post_seconds += 1.0
        if score >= 100:
            pre_seconds += 1.5
            post_seconds += 1.0
        if "outnumbered" in lowered_reasons or "v1" in lowered_reasons:
            pre_seconds += 3.0
            post_seconds += 2.0
        if "survived at" in lowered_reasons or "low-health" in lowered_reasons:
            pre_seconds += 1.0
            post_seconds += 3.0
        if "ace" in lowered_reasons:
            post_seconds += 2.0
        if objective_present:
            pre_seconds = max(pre_seconds, 10.0)
            post_seconds = max(post_seconds, 8.0)
        if len(valid) > 1:
            pre_seconds += 1.0
            post_seconds += 1.0

        pre_seconds = min(16.0, pre_seconds)
        post_seconds = min(14.0, post_seconds)
        pre_seconds, post_seconds = self._v2_fit_to_buffer(
            duration,
            pre_seconds,
            post_seconds,
        )

        label = self._v2_build_label(valid, labels, kinds, reasons)

        event_game_times = [
            float(item.event_game_time)
            for item in valid
            if item.event_game_time is not None
        ]
        triggered_walls = [
            float(item.triggered_at_wall)
            for item in valid
            if item.triggered_at_wall is not None
        ]
        triggered_monotonic = [
            float(item.triggered_at_monotonic)
            for item in valid
            if item.triggered_at_monotonic is not None
        ]

        return replace(
            base,
            label=label,
            event_started_at=event_start,
            event_ended_at=event_end,
            pre_seconds=pre_seconds,
            post_seconds=post_seconds,
            match_id=self._v2_first_text(item.match_id for item in valid),
            player_name=self._v2_first_text(item.player_name for item in valid),
            champion_name=self._v2_first_text(item.champion_name for item in valid),
            game_mode=self._v2_first_text(item.game_mode for item in valid),
            event_game_time=min(event_game_times) if event_game_times else base.event_game_time,
            event_kind=(next(iter(kinds)) if len(kinds) == 1 else "fight"),
            automatic=True,
            triggered_at_wall=max(triggered_walls) if triggered_walls else base.triggered_at_wall,
            triggered_at_monotonic=(
                max(triggered_monotonic)
                if triggered_monotonic
                else base.triggered_at_monotonic
            ),
            highlight_score=score,
            score_reasons=self._v2_unique(reasons),
            victim_names=self._v2_unique(
                name for item in valid for name in item.victim_names if name
            ),
            victim_champions=self._v2_unique(
                name for item in valid for name in item.victim_champions if name
            ),
            assister_names=self._v2_unique(
                name for item in valid for name in item.assister_names if name
            ),
        )

    def _v2_build_label(
        self,
        requests: tuple[HighlightRequest, ...],
        labels: tuple[str, ...],
        kinds: set[str],
        reasons: tuple[str, ...],
    ) -> str:
        joined = " | ".join(labels)
        reason_text = " ".join(reasons).casefold()
        combat_present = bool(kinds & self._COMBAT_KINDS)
        objective_present = bool(kinds & self._OBJECTIVE_KINDS)

        # Preserve exact high-value Riot labels whenever possible.
        for marker in (
            "PENTAKILL",
            "QUADRA KILL",
            "TRIPLE KILL",
            "2V1 DOUBLE KILL",
            "DOUBLE KILL",
        ):
            if marker in joined:
                if objective_present:
                    if "ELDER" in joined:
                        return f"{marker} + ELDER STEAL"
                    if "BARON" in joined:
                        return f"{marker} + BARON STEAL"
                    return f"{marker} + DRAGON STEAL"
                return marker

        if objective_present and combat_present:
            if "ELDER" in joined:
                return "ELDER STEAL FIGHT"
            if "BARON" in joined:
                return "BARON STEAL FIGHT"
            return "DRAGON STEAL FIGHT"

        if "survived at" in reason_text and combat_present:
            return "LOW-HEALTH OUTPLAY"
        if "ace" in reason_text and combat_present:
            return "ACE TEAMFIGHT"
        if {"kill", "assist"} <= kinds:
            return "TEAMFIGHT OUTPLAY"

        # For one candidate, keep the original specific label. For grouped
        # support-only events, prefer the strongest existing label.
        return max(
            requests,
            key=lambda item: (
                self._v2_label_priority(item.clean_label),
                int(item.highlight_score or 0),
            ),
        ).clean_label

    @staticmethod
    def _v2_label_priority(label: str) -> int:
        upper = str(label or "").upper()
        priorities = (
            ("PENTAKILL", 100),
            ("QUADRA", 90),
            ("TRIPLE", 80),
            ("2V1", 76),
            ("DOUBLE", 70),
            ("ELDER", 68),
            ("BARON STEAL", 66),
            ("DRAGON STEAL", 64),
            ("ACE", 60),
            ("LOW-HEALTH", 58),
            ("TEAMFIGHT", 55),
            ("SUPPORT", 45),
            ("SINGLE", 35),
        )
        for marker, value in priorities:
            if marker in upper:
                return value
        return 10

    def _v2_fit_to_buffer(
        self,
        action_seconds: float,
        pre_seconds: float,
        post_seconds: float,
    ) -> tuple[float, float]:
        buffer_seconds = max(
            12.0,
            float(getattr(self.config, "buffer_seconds", 45.0) or 45.0),
        )
        context_budget = max(2.0, buffer_seconds - max(0.0, action_seconds) - 1.0)
        requested_context = pre_seconds + post_seconds
        if requested_context <= context_budget:
            return round(pre_seconds, 2), round(post_seconds, 2)

        # Keep more pre-roll than post-roll because the beginning of an engage is
        # usually more important than the scoreboard aftermath.
        pre_share = pre_seconds / requested_context if requested_context else 0.55
        fitted_pre = max(1.0, context_budget * pre_share)
        fitted_post = max(1.0, context_budget - fitted_pre)
        if fitted_pre + fitted_post > context_budget:
            overflow = fitted_pre + fitted_post - context_budget
            fitted_pre = max(1.0, fitted_pre - overflow)
        return round(fitted_pre, 2), round(fitted_post, 2)

    def _v2_is_duplicate(self, request: HighlightRequest) -> bool:
        if not request.has_precise_window:
            return False

        now = time.monotonic()
        match_id = request.match_id
        start = float(request.event_started_at)
        end = float(request.event_ended_at)
        kind = request.event_kind.casefold()
        score = int(request.highlight_score or 0)

        with self._v2_lock:
            while self._v2_recent and now - self._v2_recent[0][5] > self.RECENT_DUPLICATE_RETENTION_SECONDS:
                self._v2_recent.popleft()

            for old_match, old_start, old_end, old_kind, old_score, _seen_at in self._v2_recent:
                if match_id and old_match and match_id != old_match:
                    continue

                intersection = max(0.0, min(end, old_end) - max(start, old_start))
                shorter = max(0.1, min(max(0.1, end - start), max(0.1, old_end - old_start)))
                overlap_ratio = intersection / shorter
                center_distance = abs(((start + end) / 2.0) - ((old_start + old_end) / 2.0))
                related_kind = kind == old_kind or "fight" in {kind, old_kind}

                if (
                    related_kind
                    and (overlap_ratio >= self.DUPLICATE_OVERLAP_RATIO or center_distance <= 2.5)
                    and score <= old_score + 8
                ):
                    return True

            self._v2_recent.append((match_id, start, end, kind, score, now))
        return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _end_match(self, result: str) -> None:
        # The base implementation first emits any final pending kill. Flush V2
        # after that so the last fight is included before the match is finalized.
        super()._end_match(result)
        self._v2_flush_pending()

    def stop(self) -> None:
        # Application shutdown should not begin a new export. Match-end already
        # flushes genuine pending highlights.
        self._v2_stopping = True
        with self._v2_lock:
            self._v2_take_pending_locked()
        super().stop()

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _v2_unique(values) -> tuple[str, ...]:
        output: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            output.append(text)
        return tuple(output)

    @staticmethod
    def _v2_first_text(values) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""
