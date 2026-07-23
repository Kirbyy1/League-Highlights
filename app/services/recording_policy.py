from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from types import MethodType
from typing import Any

from PySide6.QtCore import QObject, QTimer

from app.models import MatchContext, RecorderState
from app.services.lcu_game_detector import LeagueClientConnection

LOGGER = logging.getLogger(__name__)

RECORDING_FILTER_BUILD = "V17-RECORDING-SCOPE-CONTROLS"

RECORDING_SCOPE_ALL = "all"
RECORDING_SCOPE_EXCLUDE_ARAM = "exclude_aram"
RECORDING_SCOPE_RANKED_ONLY = "ranked_only"
RECORDING_SCOPES = {
    RECORDING_SCOPE_ALL,
    RECORDING_SCOPE_EXCLUDE_ARAM,
    RECORDING_SCOPE_RANKED_ONLY,
}

# Riot's current queue constants. The filter also uses game-mode/map fallbacks
# so an older or newly introduced ARAM variant is not recorded accidentally.
RANKED_QUEUE_IDS = {420, 440}
ARAM_QUEUE_IDS = {65, 67, 100, 300, 450, 720, 920, 2400}

QUEUE_LABELS = {
    400: "Normal Draft",
    420: "Ranked Solo/Duo",
    430: "Normal Blind",
    440: "Ranked Flex",
    450: "ARAM",
    480: "Swiftplay",
    490: "Quickplay",
    700: "Clash",
    720: "ARAM Clash",
    1700: "Arena",
    1710: "Arena",
    2400: "ARAM: Mayhem",
}

SCOPE_LABELS = {
    RECORDING_SCOPE_ALL: "All League games",
    RECORDING_SCOPE_EXCLUDE_ARAM: "All games except ARAM",
    RECORDING_SCOPE_RANKED_ONLY: "Ranked games only",
}


@dataclass(frozen=True, slots=True)
class RecordingDecision:
    allowed: bool
    known: bool
    queue_id: int | None
    queue_label: str
    reason: str
    is_ranked: bool = False
    is_aram: bool = False


class RecordingPolicyDetector:
    """Classify the current queue using only local League data."""

    SESSION_CACHE_SECONDS = 2.0

    def __init__(self, config: Any) -> None:
        self.config = config
        self.lcu = LeagueClientConnection()
        self._cached_session: dict[str, Any] = {}
        self._cached_at = 0.0
        self.last_decision = RecordingDecision(
            allowed=True,
            known=False,
            queue_id=None,
            queue_label="Unknown queue",
            reason="Recording is allowed",
        )

    @property
    def scope_label(self) -> str:
        return SCOPE_LABELS.get(
            str(getattr(self.config, "recording_scope", RECORDING_SCOPE_ALL)),
            SCOPE_LABELS[RECORDING_SCOPE_ALL],
        )

    def invalidate(self) -> None:
        self._cached_session = {}
        self._cached_at = 0.0

    def decision(
        self,
        context: MatchContext | None = None,
        *,
        force: bool = False,
    ) -> RecordingDecision:
        scope = str(
            getattr(self.config, "recording_scope", RECORDING_SCOPE_ALL)
            or RECORDING_SCOPE_ALL
        )
        if scope not in RECORDING_SCOPES:
            scope = RECORDING_SCOPE_ALL

        if scope == RECORDING_SCOPE_ALL:
            result = RecordingDecision(
                allowed=True,
                known=True,
                queue_id=None,
                queue_label="All games",
                reason="All League games are enabled",
            )
            self.last_decision = result
            return result

        session = self._gameflow_session(force=force)
        queue_id = self._extract_queue_id(session)
        queue_type = self._extract_queue_type(session)

        game_mode = str(getattr(context, "game_mode", "") or "").strip()
        map_name = str(getattr(context, "map_name", "") or "").strip()
        session_text = f"{queue_type} {game_mode} {map_name}".casefold()

        is_ranked = bool(
            queue_id in RANKED_QUEUE_IDS
            or "ranked_solo" in session_text
            or "ranked_flex" in session_text
            or "ranked solo" in session_text
            or "ranked flex" in session_text
        )
        is_aram = bool(
            queue_id in ARAM_QUEUE_IDS
            or "aram" in session_text
            or "howling abyss" in session_text
        )

        known = queue_id is not None or bool(queue_type) or is_aram or is_ranked
        queue_label = self._queue_label(queue_id, queue_type, game_mode, map_name)

        if scope == RECORDING_SCOPE_EXCLUDE_ARAM:
            if is_aram:
                result = RecordingDecision(
                    allowed=False,
                    known=True,
                    queue_id=queue_id,
                    queue_label=queue_label,
                    reason=f"Skipped {queue_label} — ARAM recording is disabled",
                    is_ranked=is_ranked,
                    is_aram=True,
                )
            elif known:
                result = RecordingDecision(
                    allowed=True,
                    known=True,
                    queue_id=queue_id,
                    queue_label=queue_label,
                    reason=f"Recording {queue_label}",
                    is_ranked=is_ranked,
                    is_aram=False,
                )
            else:
                result = RecordingDecision(
                    allowed=False,
                    known=False,
                    queue_id=None,
                    queue_label="Unknown queue",
                    reason="Checking the game mode before recording",
                )
        else:  # ranked_only
            if is_ranked:
                result = RecordingDecision(
                    allowed=True,
                    known=True,
                    queue_id=queue_id,
                    queue_label=queue_label,
                    reason=f"Recording {queue_label}",
                    is_ranked=True,
                    is_aram=is_aram,
                )
            elif known:
                result = RecordingDecision(
                    allowed=False,
                    known=True,
                    queue_id=queue_id,
                    queue_label=queue_label,
                    reason=f"Skipped {queue_label} — ranked games only",
                    is_ranked=False,
                    is_aram=is_aram,
                )
            else:
                result = RecordingDecision(
                    allowed=False,
                    known=False,
                    queue_id=None,
                    queue_label="Unknown queue",
                    reason="Checking whether this is a ranked game",
                )

        self.last_decision = result
        return result

    def _gameflow_session(self, *, force: bool) -> dict[str, Any]:
        now = time.monotonic()
        if (
            not force
            and self._cached_session
            and now - self._cached_at <= self.SESSION_CACHE_SECONDS
        ):
            return dict(self._cached_session)

        try:
            payload = self.lcu.get_json("/lol-gameflow/v1/session")
        except Exception:
            payload = {}

        session = dict(payload) if isinstance(payload, dict) else {}
        self._cached_session = session
        self._cached_at = now
        return dict(session)

    @classmethod
    def _extract_queue_id(cls, payload: Any) -> int | None:
        if isinstance(payload, dict):
            for key in (
                "queueId",
                "queueID",
                "queue_id",
                "gameQueueConfigId",
                "gameQueueId",
            ):
                if key in payload:
                    try:
                        value = int(payload[key])
                    except (TypeError, ValueError):
                        value = 0
                    if value > 0:
                        return value

            queue = payload.get("queue")
            if isinstance(queue, dict):
                for key in ("id", "queueId", "gameQueueConfigId"):
                    try:
                        value = int(queue.get(key, 0) or 0)
                    except (TypeError, ValueError):
                        value = 0
                    if value > 0:
                        return value

            # Prefer the gameData branch before recursively inspecting unrelated
            # numeric IDs elsewhere in the session payload.
            game_data = payload.get("gameData")
            if isinstance(game_data, dict):
                value = cls._extract_queue_id(game_data)
                if value is not None:
                    return value

            for key, child in payload.items():
                if key == "gameData":
                    continue
                value = cls._extract_queue_id(child)
                if value is not None:
                    return value

        elif isinstance(payload, list):
            for child in payload:
                value = cls._extract_queue_id(child)
                if value is not None:
                    return value
        return None

    @classmethod
    def _extract_queue_type(cls, payload: Any) -> str:
        if isinstance(payload, dict):
            for key in (
                "queueType",
                "type",
                "gameMode",
                "mode",
                "description",
                "name",
            ):
                value = payload.get(key)
                if isinstance(value, str) and any(
                    token in value.casefold()
                    for token in ("ranked", "aram", "solo", "flex", "quickplay", "swiftplay")
                ):
                    return value

            queue = payload.get("queue")
            if isinstance(queue, dict):
                value = cls._extract_queue_type(queue)
                if value:
                    return value

            game_data = payload.get("gameData")
            if isinstance(game_data, dict):
                value = cls._extract_queue_type(game_data)
                if value:
                    return value

            for key, child in payload.items():
                if key in {"queue", "gameData"}:
                    continue
                value = cls._extract_queue_type(child)
                if value:
                    return value

        elif isinstance(payload, list):
            for child in payload:
                value = cls._extract_queue_type(child)
                if value:
                    return value
        return ""

    @staticmethod
    def _queue_label(
        queue_id: int | None,
        queue_type: str,
        game_mode: str,
        map_name: str,
    ) -> str:
        if queue_id in QUEUE_LABELS:
            return QUEUE_LABELS[int(queue_id)]
        for value in (queue_type, game_mode, map_name):
            cleaned = str(value or "").replace("_", " ").strip()
            if cleaned:
                return cleaned.title()
        return f"Queue {queue_id}" if queue_id is not None else "Unknown queue"


class RecordingPolicyAdapter(QObject):
    """Attach recording filters to the existing recorder without replacing it."""

    def __init__(self, controller: Any, config: Any) -> None:
        super().__init__(controller)
        self.controller = controller
        self.config = config
        self.detector = RecordingPolicyDetector(config)
        self._original_start_recording = controller.start_recording
        self._original_save_clip = controller.save_clip
        self._state_guard = False

        controller.start_recording = MethodType(self._start_wrapper, controller)
        controller.save_clip = MethodType(self._save_wrapper, controller)
        controller.state_changed.connect(self._on_controller_state)

    def _start_wrapper(self, _controller_self: Any) -> None:
        if not bool(getattr(self.config, "recording_enabled", True)):
            self._set_policy_state("Highlight recording is disabled in Settings")
            return

        context = self.controller.active_match or self.controller.league_events.current_match
        decision = self.detector.decision(context, force=True)
        if not decision.allowed:
            state = RecorderState.WAITING if not decision.known else RecorderState.STOPPED
            self._set_policy_state(decision.reason, state)
            return

        self._original_start_recording()

    def _save_wrapper(self, _controller_self: Any, request: Any = "MANUAL CLIP") -> None:
        if not bool(getattr(self.config, "recording_enabled", True)):
            self.controller.error_occurred.emit(
                "Highlight recording is disabled in Settings."
            )
            return

        if not self.controller.recording:
            context = self.controller.active_match or self.controller.league_events.current_match
            decision = self.detector.decision(context)
            if not decision.allowed:
                self.controller.error_occurred.emit(decision.reason)
                return

        self._original_save_clip(request)

    def apply(self, enabled: bool, scope: str) -> bool:
        normalized_scope = str(scope or RECORDING_SCOPE_ALL)
        if normalized_scope not in RECORDING_SCOPES:
            normalized_scope = RECORDING_SCOPE_ALL

        enabled = bool(enabled)
        changed = (
            bool(getattr(self.config, "recording_enabled", True)) != enabled
            or str(getattr(self.config, "recording_scope", RECORDING_SCOPE_ALL))
            != normalized_scope
        )

        self.config.recording_enabled = enabled
        self.config.recording_scope = normalized_scope
        self.config.save_user_settings()
        self.detector.invalidate()

        if not enabled:
            if self.controller.recording:
                self.controller.stop_recording("Highlight recording disabled")
            self._set_policy_state("Highlight recording is disabled in Settings")
            return changed

        context = self.controller.active_match or self.controller.league_events.current_match
        decision = self.detector.decision(context, force=True)

        if self.controller.recording and not decision.allowed:
            self.controller.stop_recording(decision.reason)
        elif (
            not self.controller.recording
            and self.controller.current_window is not None
            and bool(getattr(self.config, "auto_start", True))
            and decision.allowed
        ):
            QTimer.singleShot(0, self.controller.start_recording)
        elif not decision.allowed and self.controller.current_window is not None:
            state = RecorderState.WAITING if not decision.known else RecorderState.STOPPED
            self._set_policy_state(decision.reason, state)
        elif self.controller.current_window is None:
            self._set_policy_state("Start a League game in Borderless mode", RecorderState.WAITING)

        return changed

    def status_text(self) -> str:
        if not bool(getattr(self.config, "recording_enabled", True)):
            return "Recording disabled"
        return self.detector.scope_label

    def _on_controller_state(self, state: RecorderState, detail: str) -> None:
        if self._state_guard:
            return
        if not bool(getattr(self.config, "recording_enabled", True)) and state not in {
            RecorderState.ERROR,
            RecorderState.SAVING,
        }:
            if "disabled" not in str(detail).casefold():
                QTimer.singleShot(
                    0,
                    lambda: self._set_policy_state(
                        "Highlight recording is disabled in Settings"
                    ),
                )

    def _set_policy_state(
        self,
        detail: str,
        state: RecorderState = RecorderState.STOPPED,
    ) -> None:
        if self._state_guard:
            return
        self._state_guard = True
        try:
            self.controller._set_state(state, detail)
        finally:
            self._state_guard = False


def install_recording_policy(controller: Any, config: Any) -> RecordingPolicyAdapter:
    existing = getattr(controller, "_recording_policy_adapter", None)
    if isinstance(existing, RecordingPolicyAdapter):
        return existing
    adapter = RecordingPolicyAdapter(controller, config)
    controller._recording_policy_adapter = adapter
    return adapter
