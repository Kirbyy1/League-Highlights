from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from app.services.lcu_game_detector import LeagueClientConnection, LeagueClientIdentity


LCU_MATCH_SESSION_BUILD = "V22-LCU-LIFECYCLE-SESSION-MANAGER"

PRE_GAME_PHASES = {
    "Lobby",
    "Matchmaking",
    "ReadyCheck",
    "ChampSelect",
}
ACTIVE_GAME_PHASES = {
    "GameStart",
    "InProgress",
    "Reconnect",
}
POST_GAME_PHASES = {
    "WaitingForStats",
    "PreEndOfGame",
    "EndOfGame",
}
IDLE_PHASES = {
    "",
    "None",
    "Lobby",
    "Matchmaking",
    "ReadyCheck",
}

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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _walk_values(payload: Any):
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield str(key), value
            yield from _walk_values(value)
    elif isinstance(payload, list):
        for value in payload:
            yield from _walk_values(value)


def _find_first(payload: Any, keys: set[str]) -> Any:
    wanted = {key.casefold() for key in keys}
    for key, value in _walk_values(payload):
        if key.casefold() in wanted and value not in (None, ""):
            return value
    return None


def extract_queue_id(payload: Any) -> int | None:
    if isinstance(payload, dict):
        for key in (
            "queueId",
            "queueID",
            "queue_id",
            "gameQueueConfigId",
            "gameQueueId",
        ):
            parsed = _safe_int(payload.get(key))
            if parsed > 0:
                return parsed

        queue = payload.get("queue")
        if isinstance(queue, dict):
            for key in ("id", "queueId", "gameQueueConfigId"):
                parsed = _safe_int(queue.get(key))
                if parsed > 0:
                    return parsed

        game_data = payload.get("gameData")
        if isinstance(game_data, dict):
            parsed = extract_queue_id(game_data)
            if parsed is not None:
                return parsed

        for key, child in payload.items():
            if key in {"queue", "gameData"}:
                continue
            parsed = extract_queue_id(child)
            if parsed is not None:
                return parsed
    elif isinstance(payload, list):
        for child in payload:
            parsed = extract_queue_id(child)
            if parsed is not None:
                return parsed
    return None


def extract_game_id(payload: Any) -> str:
    value = _find_first(payload, {"gameId", "gameID"})
    # Avoid turning a random small nested ID into a match identifier.
    parsed = _safe_int(value)
    return str(parsed) if parsed >= 1000 else ""


def extract_map_id(payload: Any) -> int | None:
    value = _find_first(payload, {"mapId", "mapID"})
    parsed = _safe_int(value)
    return parsed if parsed > 0 else None


def extract_game_mode(payload: Any) -> str:
    value = _find_first(payload, {"gameMode", "mode", "queueType"})
    return str(value or "").strip()


def extract_duration_seconds(payload: Any) -> float:
    value = _find_first(
        payload,
        {
            "gameDuration",
            "gameTime",
            "duration",
            "durationSeconds",
            "gameLength",
        },
    )
    duration = _safe_float(value)
    # Some payloads use milliseconds.
    if duration > 100_000:
        duration /= 1000.0
    return max(0.0, duration)


def extract_result(payload: Any) -> str:
    direct = _find_first(payload, {"gameResult", "result", "outcome"})
    if isinstance(direct, str):
        value = direct.strip().upper()
        if value in {"WIN", "WON", "VICTORY"}:
            return "WIN"
        if value in {"LOSS", "LOSE", "LOST", "DEFEAT"}:
            return "LOSS"
        if value in {"REMAKE", "ABORTED"}:
            return "REMAKE"

    winner = _find_first(payload, {"isWinner", "winner", "win"})
    if isinstance(winner, bool):
        return "WIN" if winner else "LOSS"
    return ""


def extract_custom_game(payload: Any) -> bool:
    explicit = _find_first(payload, {"isCustomGame", "customGame"})
    if isinstance(explicit, bool):
        return explicit
    text = " ".join(
        str(value or "")
        for key, value in _walk_values(payload)
        if key.casefold() in {"gametype", "type", "queuetype"}
    ).casefold()
    return "custom_game" in text or "custom game" in text


def extract_remake(payload: Any, duration_seconds: float = 0.0) -> bool:
    explicit = _find_first(
        payload,
        {
            "isRemake",
            "wasRemake",
            "gameEndedInEarlySurrender",
            "earlySurrender",
        },
    )
    if isinstance(explicit, bool) and explicit:
        return True
    result = extract_result(payload)
    if result == "REMAKE":
        return True
    duration = duration_seconds or extract_duration_seconds(payload)
    return 0 < duration <= 300.0


def queue_label(queue_id: int | None, game_mode: str = "") -> str:
    if queue_id in QUEUE_LABELS:
        return QUEUE_LABELS[int(queue_id)]
    cleaned = str(game_mode or "").replace("_", " ").strip()
    if cleaned:
        return cleaned.title()
    return f"Queue {queue_id}" if queue_id is not None else "Unknown queue"


@dataclass(frozen=True, slots=True)
class LcuSessionSnapshot:
    phase: str = ""
    previous_phase: str = ""
    state: str = "idle"
    session_id: str = ""
    game_id: str = ""
    queue_id: int | None = None
    queue_label: str = "Unknown queue"
    map_id: int | None = None
    game_mode: str = ""
    started_at: float = 0.0
    duration_seconds: float = 0.0
    result: str = ""
    was_active: bool = False
    is_reconnect: bool = False
    is_remake: bool = False
    is_custom_game: bool = False
    identity: LeagueClientIdentity = field(default_factory=LeagueClientIdentity)
    raw_session: dict[str, Any] = field(default_factory=dict)
    raw_end_of_game: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.phase in ACTIVE_GAME_PHASES or self.state in {"active", "reconnect"}

    @property
    def is_post_game(self) -> bool:
        return self.phase in POST_GAME_PHASES or self.state in {"post_game", "completed", "remake"}

    @property
    def description(self) -> str:
        if self.state == "champ_select":
            return f"{self.queue_label} · Champion Select"
        if self.state == "reconnect":
            return f"{self.queue_label} · Reconnecting"
        if self.state == "active":
            return f"{self.queue_label} · In game"
        if self.state == "post_game":
            return f"{self.queue_label} · Waiting for post-game results"
        if self.state == "remake":
            return f"{self.queue_label} · Remake"
        if self.state == "completed":
            result = self.result.title() if self.result else "Complete"
            return f"{self.queue_label} · {result}"
        if self.state == "dodged":
            return f"{self.queue_label} · Champion select ended"
        if self.phase:
            return self.phase.replace("_", " ")
        return "League Client unavailable"


@dataclass(frozen=True, slots=True)
class LcuLifecycleEvent:
    kind: str
    snapshot: LcuSessionSnapshot
    message: str


class LcuLifecycleEngine:
    """Pure state machine for League gameflow transitions."""

    def __init__(self) -> None:
        self.snapshot = LcuSessionSnapshot()
        self._session_counter = 0
        self._session_id = ""
        self._ever_active = False
        self._started_at = 0.0
        self._finalized_session_ids: set[str] = set()

    def ingest(
        self,
        phase: str,
        session_payload: dict[str, Any] | None,
        identity: LeagueClientIdentity | None = None,
        end_of_game_payload: dict[str, Any] | None = None,
        *,
        now: float | None = None,
    ) -> tuple[LcuSessionSnapshot, list[LcuLifecycleEvent]]:
        now = float(now if now is not None else time.time())
        payload = dict(session_payload or {})
        eog = dict(end_of_game_payload or {})
        identity = identity or self.snapshot.identity
        phase = str(phase or "")
        previous_phase = self.snapshot.phase
        previous_state = self.snapshot.state
        events: list[LcuLifecycleEvent] = []

        queue_id = extract_queue_id(payload)
        game_id = extract_game_id(payload)
        map_id = extract_map_id(payload)
        game_mode = extract_game_mode(payload)
        duration = max(
            extract_duration_seconds(payload),
            extract_duration_seconds(eog),
            self.snapshot.duration_seconds,
        )
        custom_game = extract_custom_game(payload)

        if not self._session_id and (phase == "ChampSelect" or phase in ACTIVE_GAME_PHASES):
            self._session_counter += 1
            self._session_id = (
                f"lcu-{game_id}"
                if game_id
                else f"lcu-{int(now)}-{self._session_counter}"
            )
            self._ever_active = False
            self._started_at = now

        state = "idle"
        is_reconnect = False
        is_remake = False
        result = self.snapshot.result

        if phase == "ChampSelect":
            state = "champ_select"
            if previous_phase != "ChampSelect":
                provisional = self._build_snapshot(
                    phase,
                    previous_phase,
                    state,
                    game_id,
                    queue_id,
                    map_id,
                    game_mode,
                    duration,
                    result,
                    custom_game,
                    identity,
                    payload,
                    eog,
                )
                events.append(
                    LcuLifecycleEvent(
                        "champ_select_started",
                        provisional,
                        f"{provisional.queue_label} champion select detected",
                    )
                )

        elif phase in {"GameStart", "InProgress"}:
            state = "active"
            if not self._ever_active:
                self._ever_active = True
                self._started_at = self._started_at or now
                provisional = self._build_snapshot(
                    phase,
                    previous_phase,
                    state,
                    game_id,
                    queue_id,
                    map_id,
                    game_mode,
                    duration,
                    result,
                    custom_game,
                    identity,
                    payload,
                    eog,
                )
                events.append(
                    LcuLifecycleEvent(
                        "game_started",
                        provisional,
                        f"{provisional.queue_label} started",
                    )
                )
            elif previous_phase == "Reconnect" or previous_state == "reconnect":
                provisional = self._build_snapshot(
                    phase,
                    previous_phase,
                    state,
                    game_id,
                    queue_id,
                    map_id,
                    game_mode,
                    duration,
                    result,
                    custom_game,
                    identity,
                    payload,
                    eog,
                )
                events.append(
                    LcuLifecycleEvent(
                        "reconnect_finished",
                        provisional,
                        "Reconnected to the existing match session",
                    )
                )

        elif phase == "Reconnect":
            state = "reconnect"
            is_reconnect = True
            self._ever_active = True
            if previous_phase != "Reconnect":
                provisional = self._build_snapshot(
                    phase,
                    previous_phase,
                    state,
                    game_id,
                    queue_id,
                    map_id,
                    game_mode,
                    duration,
                    result,
                    custom_game,
                    identity,
                    payload,
                    eog,
                    is_reconnect=True,
                )
                events.append(
                    LcuLifecycleEvent(
                        "reconnect_started",
                        provisional,
                        "Reconnect detected — preserving the current match session",
                    )
                )

        elif phase in POST_GAME_PHASES:
            duration = max(duration, extract_duration_seconds(eog))
            result = extract_result(eog) or extract_result(payload) or result
            has_final_data = bool(eog) or phase == "EndOfGame"
            if not has_final_data:
                state = "post_game"
            else:
                is_remake = extract_remake(eog or payload, duration)
                state = "remake" if is_remake else "completed"
                if self._ever_active and self._session_id not in self._finalized_session_ids:
                    provisional = self._build_snapshot(
                        phase,
                        previous_phase,
                        state,
                        game_id,
                        queue_id,
                        map_id,
                        game_mode,
                        duration,
                        "REMAKE" if is_remake else result,
                        custom_game,
                        identity,
                        payload,
                        eog,
                        is_remake=is_remake,
                    )
                    kind = "remake" if is_remake else "game_completed"
                    message = (
                        "Remake detected — the session will not be treated as a normal game"
                        if is_remake
                        else f"{provisional.queue_label} completed"
                    )
                    events.append(LcuLifecycleEvent(kind, provisional, message))
                    self._finalized_session_ids.add(self._session_id)

        elif (
            previous_phase == "ChampSelect"
            and phase in IDLE_PHASES
            and not self._ever_active
            and self._session_id
        ):
            state = "dodged"
            provisional = self._build_snapshot(
                phase,
                previous_phase,
                state,
                game_id,
                queue_id,
                map_id,
                game_mode,
                duration,
                result,
                custom_game,
                identity,
                payload,
                eog,
            )
            events.append(
                LcuLifecycleEvent(
                    "dodge",
                    provisional,
                    "Champion select ended before the game launched",
                )
            )

        elif phase in IDLE_PHASES:
            state = "idle"

        snapshot = self._build_snapshot(
            phase,
            previous_phase,
            state,
            game_id,
            queue_id,
            map_id,
            game_mode,
            duration,
            "REMAKE" if is_remake else result,
            custom_game,
            identity,
            payload,
            eog,
            is_reconnect=is_reconnect,
            is_remake=is_remake,
        )
        self.snapshot = snapshot

        if state == "idle" and previous_state in {"completed", "remake", "dodged"}:
            self._session_id = ""
            self._ever_active = False
            self._started_at = 0.0

        return snapshot, events

    def _build_snapshot(
        self,
        phase: str,
        previous_phase: str,
        state: str,
        game_id: str,
        queue_id: int | None,
        map_id: int | None,
        game_mode: str,
        duration: float,
        result: str,
        custom_game: bool,
        identity: LeagueClientIdentity,
        payload: dict[str, Any],
        eog: dict[str, Any],
        *,
        is_reconnect: bool = False,
        is_remake: bool = False,
    ) -> LcuSessionSnapshot:
        return LcuSessionSnapshot(
            phase=phase,
            previous_phase=previous_phase,
            state=state,
            session_id=self._session_id,
            game_id=game_id or self.snapshot.game_id,
            queue_id=queue_id if queue_id is not None else self.snapshot.queue_id,
            queue_label=queue_label(
                queue_id if queue_id is not None else self.snapshot.queue_id,
                game_mode or self.snapshot.game_mode,
            ),
            map_id=map_id if map_id is not None else self.snapshot.map_id,
            game_mode=game_mode or self.snapshot.game_mode,
            started_at=self._started_at or self.snapshot.started_at,
            duration_seconds=max(0.0, duration),
            result=result,
            was_active=self._ever_active,
            is_reconnect=is_reconnect,
            is_remake=is_remake,
            is_custom_game=custom_game or self.snapshot.is_custom_game,
            identity=identity,
            raw_session=dict(payload),
            raw_end_of_game=dict(eog),
        )


class LcuMatchSessionManager(QObject):
    """Poll the LCU off the UI thread and publish one stable match session."""

    snapshot_changed = Signal(object)
    event_emitted = Signal(object)
    identity_changed = Signal(object)
    status_changed = Signal(str, str)

    POLL_INTERVAL_MS = 800

    def __init__(
        self,
        connection: LeagueClientConnection | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.connection = connection or LeagueClientConnection()
        self.engine = LcuLifecycleEngine()
        self._snapshot = self.engine.snapshot
        self._lock = threading.RLock()
        self._busy = False
        self._stopped = False
        self._last_identity_signature = ""
        self._last_snapshot_key = ""
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)

    @property
    def snapshot(self) -> LcuSessionSnapshot:
        with self._lock:
            return self._snapshot

    def start(self) -> None:
        self._stopped = False
        if not self._timer.isActive():
            self._timer.start()
        self.refresh()

    def stop(self) -> None:
        self._stopped = True
        self._timer.stop()

    def refresh(self) -> None:
        if self._stopped or self._busy:
            return
        self._busy = True
        threading.Thread(
            target=self._poll_worker,
            name="LeagueHighlightsLCUSession",
            daemon=True,
        ).start()

    def _poll_worker(self) -> None:
        try:
            phase = self.connection.gameflow_phase()
            session = self.connection.gameflow_session()
            identity = self.connection.current_identity(max_age_seconds=5.0)
            eog = (
                self.connection.end_of_game_stats()
                if phase in POST_GAME_PHASES
                else {}
            )
            snapshot, events = self.engine.ingest(
                phase,
                session,
                identity,
                eog,
            )
            with self._lock:
                self._snapshot = snapshot

            identity_signature = "|".join(
                (
                    identity.stable_key,
                    identity.riot_id,
                    identity.platform,
                    identity.locale,
                )
            )
            if identity.stable_key and identity_signature != self._last_identity_signature:
                self._last_identity_signature = identity_signature
                self.identity_changed.emit(identity)

            snapshot_key = "|".join(
                (
                    snapshot.session_id,
                    snapshot.phase,
                    snapshot.state,
                    str(snapshot.queue_id or ""),
                    snapshot.result,
                    str(int(snapshot.duration_seconds)),
                )
            )
            if snapshot_key != self._last_snapshot_key:
                self._last_snapshot_key = snapshot_key
                self.snapshot_changed.emit(snapshot)

            for event in events:
                self.event_emitted.emit(event)

            self.status_changed.emit("connected", snapshot.description)
        except Exception:
            snapshot = self.snapshot
            if snapshot.was_active and not snapshot.is_post_game:
                self.status_changed.emit(
                    "reconnect_wait",
                    "League Client unavailable — preserving the current match session",
                )
            else:
                self.status_changed.emit("offline", "Waiting for the League Client")
            logging.debug("LCU match session poll failed", exc_info=True)
        finally:
            self._busy = False
