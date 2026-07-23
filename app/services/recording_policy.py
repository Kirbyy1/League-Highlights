from __future__ import annotations

import logging
from dataclasses import dataclass
from types import MethodType
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from app.models import MatchContext, RecorderState
from app.services.lcu_match_session import (
    ACTIVE_GAME_PHASES,
    POST_GAME_PHASES,
    LcuLifecycleEvent,
    LcuMatchSessionManager,
    LcuSessionSnapshot,
    extract_custom_game,
    extract_game_mode,
    extract_queue_id,
    queue_label,
)

LOGGER = logging.getLogger(__name__)

RECORDING_FILTER_BUILD = "V22-LCU-LIFECYCLE-RECORDING-CONTROLS"

RECORDING_SCOPE_ALL = "all"
RECORDING_SCOPE_EXCLUDE_ARAM = "exclude_aram"
RECORDING_SCOPE_RANKED_ONLY = "ranked_only"
RECORDING_SCOPE_SOLO_DUO_ONLY = "solo_duo_only"
RECORDING_SCOPE_FLEX_ONLY = "flex_only"
RECORDING_SCOPE_NORMAL_DRAFT_ONLY = "normal_draft_only"
RECORDING_SCOPES = {
    RECORDING_SCOPE_ALL,
    RECORDING_SCOPE_EXCLUDE_ARAM,
    RECORDING_SCOPE_RANKED_ONLY,
    RECORDING_SCOPE_SOLO_DUO_ONLY,
    RECORDING_SCOPE_FLEX_ONLY,
    RECORDING_SCOPE_NORMAL_DRAFT_ONLY,
}

RANKED_SOLO_QUEUE_IDS = {420}
RANKED_FLEX_QUEUE_IDS = {440}
RANKED_QUEUE_IDS = RANKED_SOLO_QUEUE_IDS | RANKED_FLEX_QUEUE_IDS
NORMAL_DRAFT_QUEUE_IDS = {400}
ARAM_QUEUE_IDS = {65, 67, 100, 300, 450, 720, 920, 2400}
ARENA_QUEUE_IDS = {1700, 1710}

SCOPE_LABELS = {
    RECORDING_SCOPE_ALL: "All League games",
    RECORDING_SCOPE_EXCLUDE_ARAM: "All games except ARAM",
    RECORDING_SCOPE_RANKED_ONLY: "Ranked games only",
    RECORDING_SCOPE_SOLO_DUO_ONLY: "Ranked Solo/Duo only",
    RECORDING_SCOPE_FLEX_ONLY: "Ranked Flex only",
    RECORDING_SCOPE_NORMAL_DRAFT_ONLY: "Normal Draft only",
}

_PLATFORM_ALIASES = {
    "BR": "br1",
    "BR1": "br1",
    "EUN": "eun1",
    "EUNE": "eun1",
    "EUN1": "eun1",
    "EUW": "euw1",
    "EUW1": "euw1",
    "JP": "jp1",
    "JP1": "jp1",
    "KR": "kr",
    "LA1": "la1",
    "LAN": "la1",
    "LA2": "la2",
    "LAS": "la2",
    "ME": "me1",
    "ME1": "me1",
    "NA": "na1",
    "NA1": "na1",
    "OC": "oc1",
    "OCE": "oc1",
    "OC1": "oc1",
    "PH": "ph2",
    "PH2": "ph2",
    "RU": "ru",
    "SG": "sg2",
    "SG2": "sg2",
    "TH": "th2",
    "TH2": "th2",
    "TR": "tr1",
    "TR1": "tr1",
    "TW": "tw2",
    "TW2": "tw2",
    "VN": "vn2",
    "VN2": "vn2",
}


@dataclass(frozen=True, slots=True)
class RecordingDecision:
    allowed: bool
    known: bool
    queue_id: int | None
    queue_label: str
    reason: str
    phase: str = ""
    is_ranked: bool = False
    is_aram: bool = False
    is_arena: bool = False
    is_custom: bool = False


class RecordingPolicyDetector:
    """Classify a queue from a shared LCU session snapshot."""

    def __init__(self, config: Any) -> None:
        self.config = config
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
        self.last_decision = RecordingDecision(
            allowed=True,
            known=False,
            queue_id=None,
            queue_label="Unknown queue",
            reason="Recording policy refreshed",
        )

    def decision(
        self,
        context: MatchContext | None = None,
        *,
        snapshot: LcuSessionSnapshot | None = None,
        force: bool = False,
    ) -> RecordingDecision:
        del force  # Kept for compatibility with V17 callers.
        scope = str(
            getattr(self.config, "recording_scope", RECORDING_SCOPE_ALL)
            or RECORDING_SCOPE_ALL
        )
        if scope not in RECORDING_SCOPES:
            scope = RECORDING_SCOPE_ALL

        raw_session = dict(snapshot.raw_session) if snapshot is not None else {}
        queue_id = snapshot.queue_id if snapshot is not None else extract_queue_id(raw_session)
        game_mode = (
            snapshot.game_mode
            if snapshot is not None
            else extract_game_mode(raw_session)
        )
        phase = snapshot.phase if snapshot is not None else ""
        map_name = str(getattr(context, "map_name", "") or "").strip()
        context_mode = str(getattr(context, "game_mode", "") or "").strip()
        session_text = f"{game_mode} {context_mode} {map_name}".casefold()

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
        is_arena = bool(
            queue_id in ARENA_QUEUE_IDS
            or "arena" in session_text
            or "cherry" in session_text
        )
        is_custom = bool(
            (snapshot.is_custom_game if snapshot is not None else False)
            or extract_custom_game(raw_session)
            or "custom game" in session_text
            or "custom_game" in session_text
        )
        known = bool(
            queue_id is not None
            or game_mode
            or context_mode
            or is_ranked
            or is_aram
            or is_arena
            or is_custom
        )
        label = queue_label(queue_id, game_mode or context_mode or map_name)

        if bool(getattr(self.config, "recording_skip_custom_games", False)) and is_custom:
            result = RecordingDecision(
                False,
                True,
                queue_id,
                label,
                f"Skipped {label} — custom games are disabled",
                phase,
                is_ranked,
                is_aram,
                is_arena,
                True,
            )
            self.last_decision = result
            return result

        if bool(getattr(self.config, "recording_skip_arena", False)) and is_arena:
            result = RecordingDecision(
                False,
                True,
                queue_id,
                label,
                f"Skipped {label} — Arena recording is disabled",
                phase,
                is_ranked,
                is_aram,
                True,
                is_custom,
            )
            self.last_decision = result
            return result

        allowed = True
        reason = f"Recording {label}"

        if scope == RECORDING_SCOPE_EXCLUDE_ARAM:
            allowed = not is_aram if known else False
            reason = (
                f"Skipped {label} — ARAM recording is disabled"
                if is_aram
                else f"Recording {label}"
                if known
                else "Checking the game mode before recording"
            )
        elif scope == RECORDING_SCOPE_RANKED_ONLY:
            allowed = is_ranked
            reason = (
                f"Recording {label}"
                if is_ranked
                else f"Skipped {label} — ranked games only"
                if known
                else "Checking whether this is a ranked game"
            )
        elif scope == RECORDING_SCOPE_SOLO_DUO_ONLY:
            allowed = queue_id in RANKED_SOLO_QUEUE_IDS
            reason = (
                f"Recording {label}"
                if allowed
                else f"Skipped {label} — Solo/Duo only"
                if known
                else "Checking whether this is Ranked Solo/Duo"
            )
        elif scope == RECORDING_SCOPE_FLEX_ONLY:
            allowed = queue_id in RANKED_FLEX_QUEUE_IDS
            reason = (
                f"Recording {label}"
                if allowed
                else f"Skipped {label} — Ranked Flex only"
                if known
                else "Checking whether this is Ranked Flex"
            )
        elif scope == RECORDING_SCOPE_NORMAL_DRAFT_ONLY:
            allowed = queue_id in NORMAL_DRAFT_QUEUE_IDS
            reason = (
                f"Recording {label}"
                if allowed
                else f"Skipped {label} — Normal Draft only"
                if known
                else "Checking whether this is Normal Draft"
            )
        elif scope == RECORDING_SCOPE_ALL:
            allowed = True
            reason = f"Recording {label}" if known else "All League games are enabled"

        result = RecordingDecision(
            allowed=bool(allowed),
            known=known,
            queue_id=queue_id,
            queue_label=label,
            reason=reason,
            phase=phase,
            is_ranked=is_ranked,
            is_aram=is_aram,
            is_arena=is_arena,
            is_custom=is_custom,
        )
        self.last_decision = result
        return result


class RecordingPolicyAdapter(QObject):
    """Use LCU gameflow as the controller for recording and match continuity."""

    session_changed = Signal(object)
    identity_changed = Signal(object)
    policy_status_changed = Signal(str)

    def __init__(
        self,
        controller: Any,
        config: Any,
        *,
        session_manager: LcuMatchSessionManager | None = None,
        auto_start_manager: bool = True,
    ) -> None:
        super().__init__(controller)
        self.controller = controller
        self.config = config
        self.detector = RecordingPolicyDetector(config)
        self.session_manager = session_manager or LcuMatchSessionManager(parent=self)
        self._original_start_recording = controller.start_recording
        self._original_save_clip = controller.save_clip
        self._state_guard = False
        self._last_session_id = ""
        self._last_policy_message = ""
        self._finalized_lcu_sessions: set[str] = set()
        self._original_event_end_match = None

        controller.start_recording = MethodType(self._start_wrapper, controller)
        controller.save_clip = MethodType(self._save_wrapper, controller)
        controller.state_changed.connect(self._on_controller_state)

        self._install_reconnect_guard()
        self.session_manager.snapshot_changed.connect(self._on_session_snapshot)
        self.session_manager.event_emitted.connect(self._on_lifecycle_event)
        self.session_manager.identity_changed.connect(self._on_identity_changed)
        self.session_manager.status_changed.connect(self._on_lcu_status)
        if auto_start_manager:
            self.session_manager.start()

    @property
    def current_snapshot(self) -> LcuSessionSnapshot:
        return self.session_manager.snapshot

    def _install_reconnect_guard(self) -> None:
        monitor = getattr(self.controller, "league_events", None)
        original = getattr(monitor, "_end_match", None)
        if monitor is None or not callable(original):
            return
        self._original_event_end_match = original

        adapter = self

        def guarded_end(_monitor_self: Any, result: str) -> None:
            snapshot = adapter.current_snapshot
            if snapshot.phase in ACTIVE_GAME_PHASES and not snapshot.is_post_game:
                LOGGER.info(
                    "Suppressed Live Client match end while LCU phase is %s",
                    snapshot.phase,
                )
                return
            original(result)

        monitor._end_match = MethodType(guarded_end, monitor)

    def _start_wrapper(self, _controller_self: Any) -> None:
        if not bool(getattr(self.config, "recording_enabled", True)):
            self._set_policy_state("Highlight recording is disabled in Settings")
            return

        snapshot = self.current_snapshot
        context = self.controller.active_match or self.controller.league_events.current_match

        if snapshot.phase and snapshot.phase not in ACTIVE_GAME_PHASES:
            if snapshot.phase == "ChampSelect":
                self._set_policy_state(
                    f"{snapshot.queue_label} champion select — recording starts when the game launches",
                    RecorderState.WAITING,
                )
            elif snapshot.phase in POST_GAME_PHASES:
                self._set_policy_state("The match has ended", RecorderState.STOPPED)
            else:
                self._set_policy_state(
                    f"Waiting for game start · {snapshot.description}",
                    RecorderState.WAITING,
                )
            return

        # Keep the existing window/port-2999 fallback when the unsupported LCU is
        # temporarily unavailable, but never bypass a known pre/post-game phase.
        if not snapshot.phase and context is None and self.controller.current_window is None:
            self._set_policy_state("Waiting for the League game window", RecorderState.WAITING)
            return

        decision = self.detector.decision(context, snapshot=snapshot)
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

        context = self.controller.active_match or self.controller.league_events.current_match
        decision = self.detector.decision(context, snapshot=self.current_snapshot)
        if not decision.allowed:
            self.controller.error_occurred.emit(decision.reason)
            return
        if self.current_snapshot.phase and self.current_snapshot.phase not in ACTIVE_GAME_PHASES:
            self.controller.error_occurred.emit("The League match is not in progress.")
            return

        self._original_save_clip(request)

    def apply(
        self,
        enabled: bool,
        scope: str,
        skip_custom_games: bool | None = None,
        skip_arena: bool | None = None,
    ) -> bool:
        normalized_scope = str(scope or RECORDING_SCOPE_ALL)
        if normalized_scope not in RECORDING_SCOPES:
            normalized_scope = RECORDING_SCOPE_ALL

        enabled = bool(enabled)
        skip_custom = (
            bool(skip_custom_games)
            if skip_custom_games is not None
            else bool(getattr(self.config, "recording_skip_custom_games", False))
        )
        skip_arena_value = (
            bool(skip_arena)
            if skip_arena is not None
            else bool(getattr(self.config, "recording_skip_arena", False))
        )
        changed = (
            bool(getattr(self.config, "recording_enabled", True)) != enabled
            or str(getattr(self.config, "recording_scope", RECORDING_SCOPE_ALL))
            != normalized_scope
            or bool(getattr(self.config, "recording_skip_custom_games", False))
            != skip_custom
            or bool(getattr(self.config, "recording_skip_arena", False))
            != skip_arena_value
        )

        self.config.recording_enabled = enabled
        self.config.recording_scope = normalized_scope
        self.config.recording_skip_custom_games = skip_custom
        self.config.recording_skip_arena = skip_arena_value
        self.config.save_user_settings()
        self.detector.invalidate()

        if not enabled:
            if self.controller.recording:
                self.controller.stop_recording("Highlight recording disabled")
            self._set_policy_state("Highlight recording is disabled in Settings")
            return changed

        self._sync_recording_to_snapshot(self.current_snapshot)
        return changed

    def set_auto_detect_identity(self, enabled: bool) -> None:
        self.config.auto_detect_riot_account = bool(enabled)
        self.config.save_user_settings()
        if enabled:
            identity = self.current_snapshot.identity
            if identity.stable_key:
                self._sync_identity_to_config(identity)

    def status_text(self) -> str:
        if not bool(getattr(self.config, "recording_enabled", True)):
            return "Recording disabled"
        return self.detector.scope_label

    def lifecycle_text(self) -> str:
        snapshot = self.current_snapshot
        if snapshot.phase or snapshot.session_id:
            return snapshot.description
        return "Waiting for League Client"

    def identity_text(self) -> str:
        identity = self.current_snapshot.identity
        if not identity.stable_key:
            saved = str(getattr(self.config, "detected_riot_id", "") or "")
            platform = str(getattr(self.config, "detected_riot_platform", "") or "")
            return " · ".join(part for part in (saved, platform.upper()) if part)
        platform = normalize_platform(identity.platform)
        return " · ".join(
            part for part in (identity.display_name, platform.upper()) if part
        )

    def _on_session_snapshot(self, snapshot: LcuSessionSnapshot) -> None:
        self._last_session_id = snapshot.session_id or self._last_session_id
        self._sync_recording_to_snapshot(snapshot)
        self.session_changed.emit(snapshot)
        self.policy_status_changed.emit(self._policy_summary(snapshot))

    def _sync_recording_to_snapshot(self, snapshot: LcuSessionSnapshot) -> None:
        if not bool(getattr(self.config, "recording_enabled", True)):
            if self.controller.recording:
                self.controller.stop_recording("Highlight recording disabled")
            return

        context = self.controller.active_match or self.controller.league_events.current_match
        decision = self.detector.decision(context, snapshot=snapshot)

        if snapshot.phase in POST_GAME_PHASES or snapshot.state in {"completed", "remake", "dodged"}:
            if self.controller.recording:
                reason = (
                    "Remake detected"
                    if snapshot.state == "remake"
                    else "Champion select ended"
                    if snapshot.state == "dodged"
                    else "Match ended"
                )
                self.controller.stop_recording(reason)
            self._set_policy_state(
                "Remake detected — recording stopped"
                if snapshot.state == "remake"
                else "Champion select ended before launch"
                if snapshot.state == "dodged"
                else "Match completed",
                RecorderState.STOPPED,
            )
            return

        if snapshot.phase == "ChampSelect":
            if self.controller.recording:
                self.controller.stop_recording("New champion select detected")
            self._set_policy_state(
                f"{snapshot.queue_label} champion select — recording starts after launch",
                RecorderState.WAITING,
            )
            return

        if snapshot.phase in ACTIVE_GAME_PHASES:
            if not decision.allowed:
                if self.controller.recording:
                    self.controller.stop_recording(decision.reason)
                self._set_policy_state(
                    decision.reason,
                    RecorderState.STOPPED if decision.known else RecorderState.WAITING,
                )
                return

            if snapshot.phase == "Reconnect" and not self.controller.current_window:
                self._set_policy_state(
                    "Reconnect detected — preserving the current match session",
                    RecorderState.WAITING,
                )
                return

            if (
                not self.controller.recording
                and self.controller.current_window is not None
                and bool(getattr(self.config, "auto_start", True))
            ):
                QTimer.singleShot(0, self.controller.start_recording)
            return

        if snapshot.phase and self.controller.recording:
            self.controller.stop_recording(f"League phase changed to {snapshot.phase}")

    def _on_lifecycle_event(self, event: LcuLifecycleEvent) -> None:
        snapshot = event.snapshot
        if event.kind == "dodge":
            if self.controller.recording:
                self.controller.stop_recording("Champion select dodge")
            self._set_policy_state(event.message, RecorderState.WAITING)
        elif event.kind == "reconnect_started":
            if not self.controller.recording:
                self._set_policy_state(event.message, RecorderState.WAITING)
        elif event.kind in {"game_completed", "remake"}:
            if self.controller.recording:
                self.controller.stop_recording(
                    "Remake detected" if event.kind == "remake" else "Match completed"
                )
            self._finalize_live_event_match(snapshot)
        self.policy_status_changed.emit(event.message)

    def _finalize_live_event_match(self, snapshot: LcuSessionSnapshot) -> None:
        if not snapshot.session_id or snapshot.session_id in self._finalized_lcu_sessions:
            return
        monitor = getattr(self.controller, "league_events", None)
        current_match = getattr(monitor, "_current_match", None)
        match_ended = bool(getattr(monitor, "_match_ended", False))
        if current_match is not None and not match_ended and callable(self._original_event_end_match):
            result = "REMAKE" if snapshot.is_remake else (snapshot.result or "UNKNOWN")
            self._original_event_end_match(result)
        self._finalized_lcu_sessions.add(snapshot.session_id)

    def _on_identity_changed(self, identity: Any) -> None:
        self._sync_identity_to_config(identity)
        self.identity_changed.emit(identity)

    def _sync_identity_to_config(self, identity: Any) -> None:
        if not bool(getattr(self.config, "auto_detect_riot_account", True)):
            return
        platform = normalize_platform(str(getattr(identity, "platform", "") or ""))
        changed = False
        values = {
            "detected_riot_id": str(getattr(identity, "display_name", "") or ""),
            "detected_riot_puuid": str(getattr(identity, "puuid", "") or ""),
            "detected_riot_platform": platform,
            "detected_client_locale": str(getattr(identity, "locale", "") or ""),
        }
        for key, value in values.items():
            if value and str(getattr(self.config, key, "") or "") != value:
                setattr(self.config, key, value)
                changed = True
        if platform and str(getattr(self.config, "riot_platform", "") or "") != platform:
            self.config.riot_platform = platform
            changed = True
        if changed:
            self.config.save_user_settings()

    def _on_lcu_status(self, code: str, text: str) -> None:
        self._last_policy_message = str(text or "")
        if code == "reconnect_wait" and not self.controller.recording:
            self._set_policy_state(
                self._last_policy_message,
                RecorderState.WAITING,
            )
        self.policy_status_changed.emit(self._last_policy_message)

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

    def _policy_summary(self, snapshot: LcuSessionSnapshot | None = None) -> str:
        snapshot = snapshot or self.current_snapshot
        decision = self.detector.decision(
            self.controller.active_match or self.controller.league_events.current_match,
            snapshot=snapshot,
        )
        if not bool(getattr(self.config, "recording_enabled", True)):
            return "Recording disabled — Live Match remains available"
        if snapshot.phase == "ChampSelect":
            return f"{snapshot.queue_label} detected — recording will start after game launch"
        if snapshot.phase in ACTIVE_GAME_PHASES:
            return decision.reason
        if snapshot.state == "remake":
            return "Remake detected — session finalized without treating it as a normal game"
        return f"{self.detector.scope_label} · {snapshot.description}"

    def _set_policy_state(
        self,
        detail: str,
        state: RecorderState = RecorderState.STOPPED,
    ) -> None:
        if self._state_guard:
            return
        current_state = getattr(self.controller, "state", RecorderState.WAITING)
        if current_state in {RecorderState.ERROR, RecorderState.SAVING}:
            return
        self._state_guard = True
        try:
            self.controller._set_state(state, detail)
        finally:
            self._state_guard = False


def normalize_platform(value: str) -> str:
    cleaned = str(value or "").strip().upper().replace("-", "").replace("_", "")
    if cleaned in _PLATFORM_ALIASES:
        return _PLATFORM_ALIASES[cleaned]
    lower = str(value or "").strip().casefold()
    if lower in set(_PLATFORM_ALIASES.values()):
        return lower
    return ""


def install_recording_policy(controller: Any, config: Any) -> RecordingPolicyAdapter:
    existing = getattr(controller, "_recording_policy_adapter", None)
    if isinstance(existing, RecordingPolicyAdapter):
        return existing
    adapter = RecordingPolicyAdapter(controller, config)
    controller._recording_policy_adapter = adapter
    return adapter
