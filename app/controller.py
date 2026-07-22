from __future__ import annotations

import logging
from dataclasses import replace
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QTimer, Signal

from app.config import AppConfig
from app.models import (
    ClipInfo,
    HighlightRequest,
    LeagueWindowInfo,
    MatchContext,
    MatchLifecycleEvent,
    RecorderState,
)
from app.services.audio_loopback import LoopbackAudioBuffer
from app.services.clip_exporter import ClipExporter
from app.services.clip_library import ClipLibrary
from app.services.clip_trimmer import ClipTrimmer
from app.services.discord_export_service import (
    DiscordExportCancelled,
    DiscordExportPlan,
    DiscordExportResult,
    DiscordExportService,
)
from app.services.discord_webhook_service import (
    DiscordWebhookCancelled,
    DiscordWebhookService,
)
from app.services.secure_webhook_store import DiscordWebhookStore
from app.services.share_export_service import (
    ShareExportCancelled,
    ShareExportResult,
    ShareExportService,
)
from app.services.ffmpeg_tools import FfmpegTools
from app.services.highlight_event_tracker import HighlightEventTracker
from app.services.hotkey import GlobalHotkey
from app.services.league_events import LeagueEventMonitor
from app.services.league_window import LeagueWindowDetector
from app.services.video_recorder import VideoSegmentRecorder

LOGGER = logging.getLogger(__name__)


class RecorderController(QObject):
    state_changed = Signal(object, str)
    clip_requested = Signal(str)
    clip_saved = Signal(object)
    error_occurred = Signal(str)
    library_changed = Signal()
    recording_time_changed = Signal(int)
    global_hotkey_pressed = Signal()
    automatic_highlight_detected = Signal(object)
    hotkey_changed = Signal(str)
    event_status_changed = Signal(str, bool)
    match_lifecycle_detected = Signal(object)
    trim_started = Signal(str)
    trim_finished = Signal(object, bool)
    trim_failed = Signal(str)
    diagnostics_changed = Signal(object)
    discord_export_started = Signal(object, object)
    discord_export_progress = Signal(object, int, str)
    discord_export_finished = Signal(object, object)
    discord_export_failed = Signal(object, str)
    discord_export_cancelled = Signal(object)
    share_export_started = Signal(object)
    share_export_progress = Signal(object, int, str)
    share_export_finished = Signal(object, object)
    share_export_failed = Signal(object, str)
    share_export_cancelled = Signal(object)
    discord_webhook_test_finished = Signal(bool, str)

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self.detector = LeagueWindowDetector()
        self.ffmpeg = FfmpegTools(config.ffmpeg_dir)
        self.audio = LoopbackAudioBuffer(config, max_seconds=config.buffer_seconds + 30)
        self.video = VideoSegmentRecorder(config, self.ffmpeg)
        self.event_tracker = HighlightEventTracker(config.buffer_seconds + 120)
        self.exporter = ClipExporter(
            config,
            self.ffmpeg,
            self.video,
            self.audio,
            self.event_tracker,
        )
        self.library = ClipLibrary(config.clip_dir, self.ffmpeg)
        self.trimmer = ClipTrimmer(self.ffmpeg)
        self.discord_exporter = DiscordExportService(self.ffmpeg)
        self.share_exporter = ShareExportService(self.ffmpeg)
        self.discord_webhook = DiscordWebhookService()
        self.discord_webhook_store = DiscordWebhookStore(config.discord_webhook_file)
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="RecorderWorker")

        self.global_hotkey_pressed.connect(self._save_manual_clip)
        self.automatic_highlight_detected.connect(self._save_automatic_highlight)
        self.match_lifecycle_detected.connect(self._on_match_lifecycle)
        self.hotkey = GlobalHotkey(
            self.global_hotkey_pressed.emit,
            self._hotkey_error,
            virtual_key=config.hotkey_vk,
            modifiers=config.hotkey_modifiers,
            display_name=config.hotkey_display,
        )
        self.event_status_text = "Waiting for active match data"
        self.event_status_connected = False
        self.league_events = LeagueEventMonitor(
            config,
            self.automatic_highlight_detected.emit,
            self._on_event_monitor_status,
            self.match_lifecycle_detected.emit,
            self.event_tracker.add,
        )

        self.current_window: LeagueWindowInfo | None = None
        self.active_match: MatchContext | None = None
        self.state = RecorderState.WAITING
        self.detail = "Start a League game in Borderless mode"
        self.recording_started_at: float | None = None
        self._shutdown = False
        self._auto_restart_blocked = False

        # All clip exports and match-reel builds are serialized. This prevents a
        # burst of automatic events from being dropped while another clip saves.
        self._work_lock = threading.Lock()
        self._clip_queue: deque[HighlightRequest] = deque()
        self._worker_active = False
        self._finished_matches: dict[str, tuple[MatchContext, str]] = {}

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(1300)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start()

        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(1000)
        self.clock_timer.timeout.connect(self._tick_clock)
        self.clock_timer.start()

        self.hotkey.start()
        self.league_events.start()
        QTimer.singleShot(100, self._initial_check)

    @property
    def recording(self) -> bool:
        return self.video.running

    @property
    def busy(self) -> bool:
        with self._work_lock:
            return self._worker_active or bool(self._clip_queue)

    def _initial_check(self) -> None:
        if not self.ffmpeg.available:
            self._set_state(RecorderState.ERROR, "FFmpeg missing — run setup.ps1")
            return
        self._set_state(RecorderState.WAITING, "Start a League game in Borderless mode")
        self._poll()

    def _poll(self) -> None:
        if self._shutdown:
            return
        try:
            window = self.detector.find()
        except Exception as exc:
            LOGGER.exception("League detection failed")
            self._set_state(RecorderState.ERROR, f"League detection failed: {exc}")
            return

        if window is None:
            self.current_window = None
            self._auto_restart_blocked = False
            if self.recording:
                self.stop_recording("League closed")
            elif self.state not in (RecorderState.ERROR, RecorderState.SAVING):
                self._set_state(RecorderState.WAITING, "Start a League game in Borderless mode")
            return

        changed_window = self.current_window is None or self.current_window.hwnd != window.hwnd
        self.current_window = window
        if changed_window:
            LOGGER.info("Detected League window: %s (%sx%s)", window.title, window.width, window.height)
            self._auto_restart_blocked = False

        if self.recording and self.video.process and self.video.process.poll() is not None:
            error = self.video.last_error or "The video recorder stopped unexpectedly."
            self.stop_recording(error)
            self._auto_restart_blocked = True
            self._set_state(RecorderState.ERROR, error)
            self.error_occurred.emit(error)
            return

        if (
            not self.recording
            and self.config.auto_start
            and not self._auto_restart_blocked
            and self.state in (RecorderState.WAITING, RecorderState.STOPPED)
        ):
            self.start_recording()

    def _recording_detail(self) -> str:
        encoder_note = self.video.encoder or "unknown encoder"
        if encoder_note == "libx264":
            encoder_note += " (CPU fallback)"
        quality_note = self._quality_label(self.config.quality)
        if self.recording:
            audio_note = self.audio.source_summary.title()
            if self.audio.warnings:
                audio_note += " (partial)"
        else:
            audio_sources: list[str] = []
            if self.config.system_audio_enabled:
                audio_sources.append("System")
            if self.config.microphone_enabled:
                audio_sources.append("Mic")
            audio_note = " + ".join(audio_sources) if audio_sources else "Video only"
        smart_note = (
            f" • Smart: {self.config.smart_sensitivity.replace('_', ' ').title()}"
            if self.config.smart_highlights_enabled
            else ""
        )
        return (
            f"{self.config.width}×{self.config.height} • {self.config.fps} FPS • "
            f"{quality_note} • {encoder_note} • Audio: {audio_note} • "
            f"{self.config.hotkey_display} saves last {self.config.buffer_seconds}s"
            f"{smart_note}"
        )

    @staticmethod
    def _quality_label(quality: int) -> str:
        if quality <= 20:
            return "High quality"
        if quality <= 24:
            return "Balanced"
        if quality <= 28:
            return "Smaller files"
        return "Minimum size"

    def start_recording(self) -> None:
        if self.recording:
            return
        window = self.current_window or self.detector.find()
        if window is None:
            self._set_state(RecorderState.WAITING, "League game window not found")
            return

        self._auto_restart_blocked = False
        self._set_state(RecorderState.STARTING, "Starting video and configured audio sources")
        try:
            self.audio.start()
            self.video.start(window)
        except Exception as exc:
            self.video.stop()
            self.audio.stop()
            LOGGER.exception("Recording could not start")
            self._auto_restart_blocked = True
            self._set_state(RecorderState.ERROR, str(exc))
            self.error_occurred.emit(str(exc))
            return

        self.current_window = window
        self.recording_started_at = time.monotonic()
        self._set_state(RecorderState.RECORDING, self._recording_detail())

    def stop_recording(self, reason: str = "Stopped") -> None:
        self.video.stop()
        self.audio.stop()
        self.recording_started_at = None
        self.recording_time_changed.emit(0)
        if not self._shutdown:
            self._set_state(RecorderState.STOPPED, reason)

    def toggle_recording(self) -> None:
        if self.recording:
            self.stop_recording("Stopped manually")
        else:
            self.start_recording()

    def _save_manual_clip(self) -> None:
        now_wall = time.time()
        now_monotonic = time.monotonic()
        context = self.active_match or self.league_events.current_match
        game_time = context.duration_seconds if context is not None else 0.0
        match_id = context.match_id if context is not None else ""
        self.event_tracker.record(
            "MANUAL_TRIGGER",
            game_time=game_time,
            match_id=match_id,
            detected_at_monotonic=now_monotonic,
            detected_at_wall=now_wall,
        )
        request = HighlightRequest(
            label="MANUAL CLIP",
            event_kind="manual",
            automatic=False,
            event_game_time=game_time or None,
            triggered_at_wall=now_wall,
            triggered_at_monotonic=now_monotonic,
            highlight_score=100,
            score_reasons=("manual clip",),
        ).with_match_context(context)
        self.save_clip(request)

    def save_clip(self, request: str | HighlightRequest = "MANUAL CLIP") -> None:
        if isinstance(request, HighlightRequest):
            highlight_request = request
        else:
            highlight_request = HighlightRequest(
                label=str(request or "MANUAL CLIP"),
                event_kind="manual",
                highlight_score=100,
                score_reasons=("manual clip",),
            )
        highlight_request = highlight_request.with_match_context(
            self.active_match or self.league_events.current_match
        )
        if highlight_request.triggered_at_wall is None:
            trigger_wall = (
                highlight_request.event_ended_at
                or highlight_request.event_started_at
                or time.time()
            )
            highlight_request = replace(
                highlight_request,
                triggered_at_wall=trigger_wall,
                triggered_at_monotonic=(
                    highlight_request.triggered_at_monotonic or time.monotonic()
                ),
            )
        label = highlight_request.clean_label
        if not self.recording:
            self.error_occurred.emit("Recording is not active. Start League first.")
            return

        with self._work_lock:
            self._clip_queue.append(highlight_request)
            should_start = not self._worker_active
            if should_start:
                self._worker_active = True
        self.clip_requested.emit(label)
        self._set_state(RecorderState.SAVING, f"Queued {label.title()}")
        if should_start:
            self.executor.submit(self._drain_work_queue)

    def _drain_work_queue(self) -> None:
        while not self._shutdown:
            request: HighlightRequest | None = None
            with self._work_lock:
                if self._clip_queue:
                    request = self._clip_queue.popleft()
                else:
                    self._worker_active = False
                    break

            try:
                if request is not None:
                    self._set_state(RecorderState.SAVING, f"Saving {request.clean_label.title()}")
                    clip = self.exporter.export_request(request)
                    finished = self._finished_matches.get(request.match_id)
                    if finished is not None:
                        context, result = finished
                        self.library.finalize_match(context, result)
                    self.clip_saved.emit(clip)
                    self.library_changed.emit()
            except Exception as exc:
                LOGGER.exception("Background highlight work failed")
                self.error_occurred.emit(str(exc))

        if self.recording:
            self._set_state(RecorderState.RECORDING, self._recording_detail())
        elif not self._shutdown:
            self._set_state(RecorderState.WAITING, "Start a League game in Borderless mode")

    def _ensure_worker(self) -> None:
        with self._work_lock:
            should_start = not self._worker_active
            if should_start:
                self._worker_active = True
        if should_start:
            self.executor.submit(self._drain_work_queue)

    def _save_automatic_highlight(self, request: HighlightRequest) -> None:
        if not isinstance(request, HighlightRequest):
            request = HighlightRequest(label=str(request or "HIGHLIGHT"), automatic=True)
        if not self.recording:
            LOGGER.info(
                "Automatic event %s detected while recording was inactive",
                request.clean_label,
            )
            return
        self.save_clip(request)

    def _on_match_lifecycle(self, event: MatchLifecycleEvent) -> None:
        if not isinstance(event, MatchLifecycleEvent):
            return
        if event.action == "started":
            self.active_match = event.context
            self.event_tracker.begin_match(event.context.match_id)
            LOGGER.info("Controller joined match session %s", event.context.match_id)
            return
        if event.action == "ended":
            self.active_match = event.context
            self._finished_matches[event.context.match_id] = (event.context, event.result)
            self.library.finalize_match(event.context, event.result)
            self.library_changed.emit()

    def update_capture_settings(
        self,
        width: int,
        height: int,
        fps: int,
        quality: int,
        audio_bitrate_kbps: int,
    ) -> bool:
        allowed_resolutions = {(1920, 1080), (1600, 900), (1280, 720)}
        if (width, height) not in allowed_resolutions:
            raise ValueError("Unsupported recording resolution.")
        if fps not in {30, 60}:
            raise ValueError("FPS must be 30 or 60.")
        if quality not in {20, 23, 27, 30}:
            raise ValueError("Unsupported quality preset.")
        if audio_bitrate_kbps not in {96, 128, 160, 192}:
            raise ValueError("Unsupported audio bitrate.")
        if self.busy:
            raise RuntimeError("Wait for queued clips to finish first.")

        changed = (
            self.config.width,
            self.config.height,
            self.config.fps,
            self.config.quality,
            self.config.audio_bitrate_kbps,
        ) != (width, height, fps, quality, audio_bitrate_kbps)
        if not changed:
            return False

        was_recording = self.recording
        self.config.width = width
        self.config.height = height
        self.config.fps = fps
        self.config.quality = quality
        self.config.audio_bitrate_kbps = audio_bitrate_kbps
        self.config.save_user_settings()
        self.audio.max_seconds = self.config.buffer_seconds + 30

        if was_recording:
            self.stop_recording("Applying capture settings")
            self._auto_restart_blocked = False
            QTimer.singleShot(350, self.start_recording)
        elif self.state not in (RecorderState.ERROR, RecorderState.SAVING):
            self._set_state(RecorderState.STOPPED, "Capture settings updated")
        return was_recording

    def update_buffer_seconds(self, seconds: int) -> bool:
        if seconds not in {30, 45, 60}:
            raise ValueError("Buffer length must be 30, 45, or 60 seconds.")
        if self.busy:
            raise RuntimeError("Wait for queued clips to finish first.")
        if self.config.buffer_seconds == seconds:
            return False

        was_recording = self.recording
        self.config.buffer_seconds = seconds
        self.config.save_user_settings()
        self.audio.max_seconds = seconds + 30
        if was_recording:
            self.stop_recording("Applying buffer length")
            self._auto_restart_blocked = False
            QTimer.singleShot(350, self.start_recording)
        return was_recording


    def update_audio_settings(
        self,
        system_enabled: bool,
        system_device: str,
        system_volume: int,
        microphone_enabled: bool,
        microphone_device: str,
        microphone_volume: int,
        audio_bitrate_kbps: int,
    ) -> bool:
        if not 0 <= int(system_volume) <= 200:
            raise ValueError("System volume must be between 0% and 200%.")
        if not 0 <= int(microphone_volume) <= 200:
            raise ValueError("Microphone volume must be between 0% and 200%.")
        if int(audio_bitrate_kbps) not in {96, 128, 160, 192}:
            raise ValueError("Unsupported audio bitrate.")
        if self.busy:
            raise RuntimeError("Wait for queued clips to finish first.")

        values = (
            bool(system_enabled),
            str(system_device or ""),
            int(system_volume),
            bool(microphone_enabled),
            str(microphone_device or ""),
            int(microphone_volume),
            int(audio_bitrate_kbps),
        )
        current = (
            self.config.system_audio_enabled,
            self.config.system_audio_device,
            self.config.system_audio_volume,
            self.config.microphone_enabled,
            self.config.microphone_device,
            self.config.microphone_volume,
            self.config.audio_bitrate_kbps,
        )
        if values == current:
            return False

        (
            self.config.system_audio_enabled,
            self.config.system_audio_device,
            self.config.system_audio_volume,
            self.config.microphone_enabled,
            self.config.microphone_device,
            self.config.microphone_volume,
            self.config.audio_bitrate_kbps,
        ) = values
        self.config.save_user_settings()

        was_recording = self.recording
        if was_recording:
            self.stop_recording("Applying audio settings")
            self._auto_restart_blocked = False
            QTimer.singleShot(350, self.start_recording)
        elif self.state not in (RecorderState.ERROR, RecorderState.SAVING):
            self._set_state(RecorderState.STOPPED, "Audio settings updated")
        return was_recording

    def update_discord_settings(
        self,
        enabled: bool,
        target_mb: float,
        auto_trim_events: bool,
    ) -> None:
        """Retained for compatibility with older UI code.

        The size-targeted export was removed because it visibly degraded gameplay
        footage. Clips now always keep the original recording quality.
        """
        if self.busy:
            raise RuntimeError("Wait for queued clips to finish first.")
        self.config.discord_mode = False
        self.config.discord_auto_trim_events = bool(auto_trim_events)
        self.config.save_user_settings()
        if self.recording:
            self._set_state(RecorderState.RECORDING, self._recording_detail())

    def update_smart_settings(self, enabled: bool, sensitivity: str) -> None:
        if sensitivity not in {"strict", "balanced", "save_more"}:
            raise ValueError("Unknown smart-highlight sensitivity.")
        self.config.smart_highlights_enabled = bool(enabled)
        self.config.smart_sensitivity = sensitivity
        self.config.save_user_settings()
        if self.recording:
            self._set_state(RecorderState.RECORDING, self._recording_detail())

    def update_hotkey(self, virtual_key: int, modifiers: list[str], display_name: str) -> None:
        self.config.hotkey_vk = int(virtual_key)
        self.config.hotkey_modifiers = list(modifiers)
        self.config.hotkey_display = display_name
        self.config.save_user_settings()
        self.hotkey.update(virtual_key, modifiers, display_name)
        self.hotkey_changed.emit(display_name)
        if self.recording:
            self._set_state(RecorderState.RECORDING, self._recording_detail())

    def set_hotkey_capture_mode(self, active: bool) -> None:
        self.hotkey.set_enabled(not active)

    def clips(self) -> list[ClipInfo]:
        return self.library.scan()

    def games(self):
        return self.library.games()

    def trim_clip(
        self,
        clip: ClipInfo,
        start_seconds: float,
        end_seconds: float,
        *,
        replace_original: bool = False,
    ) -> None:
        """Trim a clip without blocking the UI."""
        self.trim_started.emit(clip.path.name)

        def work() -> None:
            try:
                output = self.trimmer.trim(
                    clip,
                    start_seconds,
                    end_seconds,
                    replace_original=replace_original,
                )
            except Exception as exc:
                LOGGER.exception("Could not trim clip %s", clip.path)
                self.trim_failed.emit(str(exc))
                return
            self.library_changed.emit()
            self.trim_finished.emit(output, replace_original)

        self.executor.submit(work)

    def discord_export_plan(
        self,
        clip: ClipInfo,
        start_seconds: float,
        end_seconds: float,
    ) -> DiscordExportPlan:
        duration = max(0.0, float(end_seconds) - float(start_seconds))
        return self.discord_exporter.plan(duration, self.config.discord_target_bytes)

    def export_for_discord(
        self,
        clip: ClipInfo,
        start_seconds: float,
        end_seconds: float,
        *,
        send_to_discord: bool = False,
    ) -> None:
        plan = self.discord_export_plan(clip, start_seconds, end_seconds)
        self.discord_export_started.emit(clip, plan)

        def progress(percent: int, message: str) -> None:
            scaled = int(percent * 0.72) if send_to_discord else int(percent)
            self.discord_export_progress.emit(clip, scaled, str(message))

        def work() -> None:
            try:
                result = self.discord_exporter.export(
                    clip.path,
                    start_seconds,
                    end_seconds,
                    target_bytes=self.config.discord_target_bytes,
                    progress_callback=progress,
                )
                if send_to_discord:
                    webhook_url = self.discord_webhook_store.load()
                    if not webhook_url:
                        result = replace(result, send_error="No Discord webhook is connected.")
                    else:
                        def upload_progress(percent: int, message: str) -> None:
                            scaled = 72 + int(max(0, min(100, percent)) * 0.28)
                            self.discord_export_progress.emit(clip, scaled, str(message))

                        content_parts = [clip.label.replace("_", " ").title()]
                        if clip.champion_name:
                            content_parts.append(clip.champion_name)
                        if clip.match_time_text != "--:--":
                            content_parts.append(f"Match {clip.match_time_text}")
                        try:
                            upload = self.discord_webhook.upload(
                                webhook_url,
                                result.output_path,
                                content=" · ".join(content_parts),
                                progress_callback=upload_progress,
                            )
                        except DiscordWebhookCancelled:
                            result = replace(
                                result,
                                send_error="Sending was cancelled. The Discord-ready file was still saved.",
                            )
                        except Exception as exc:
                            LOGGER.exception("Discord send failed after export for %s", clip.path)
                            result = replace(
                                result,
                                send_error=f"Discord send failed: {exc}",
                            )
                        else:
                            result = replace(
                                result,
                                sent_to_discord=True,
                                discord_message_id=upload.message_id,
                            )
            except DiscordExportCancelled:
                self.discord_export_cancelled.emit(clip)
                return
            except Exception as exc:
                LOGGER.exception("Could not export/send Discord copy for %s", clip.path)
                self.discord_export_failed.emit(clip, str(exc))
                return
            self.discord_export_finished.emit(clip, result)

        self.executor.submit(work)

    def cancel_discord_export(self, clip: ClipInfo) -> bool:
        cancelled = self.discord_exporter.cancel(clip.path)
        self.discord_webhook.cancel_all()
        return cancelled

    def export_share_copy(
        self,
        clip: ClipInfo,
        start_seconds: float,
        end_seconds: float,
    ) -> None:
        self.share_export_started.emit(clip)

        def progress(percent: int, message: str) -> None:
            self.share_export_progress.emit(clip, int(percent), str(message))

        def work() -> None:
            try:
                result = self.share_exporter.export(
                    clip.path,
                    start_seconds,
                    end_seconds,
                    progress_callback=progress,
                )
            except ShareExportCancelled:
                self.share_export_cancelled.emit(clip)
                return
            except Exception as exc:
                LOGGER.exception("Could not create share copy for %s", clip.path)
                self.share_export_failed.emit(clip, str(exc))
                return
            self.share_export_finished.emit(clip, result)

        self.executor.submit(work)

    def cancel_share_export(self, clip: ClipInfo) -> bool:
        return self.share_exporter.cancel(clip.path)

    @property
    def discord_webhook_configured(self) -> bool:
        return self.discord_webhook_store.load() is not None

    def save_discord_webhook(self, webhook_url: str) -> None:
        # Validation is performed before save in the onboarding dialog.
        self.discord_webhook_store.save(webhook_url)

    def clear_discord_webhook(self) -> None:
        self.discord_webhook_store.clear()

    def test_discord_webhook(self, webhook_url: str) -> None:
        def work() -> None:
            try:
                info = self.discord_webhook.test_connection(webhook_url)
            except Exception as exc:
                self.discord_webhook_test_finished.emit(False, str(exc))
                return
            label = info.name or "Discord webhook"
            self.discord_webhook_test_finished.emit(True, f"Connected to {label}")

        self.executor.submit(work)

    def update_discord_target_mib(self, target_mib: float) -> None:
        value = float(target_mib)
        if not 1.0 <= value <= 100.0:
            raise ValueError("Discord target must be between 1 and 100 MiB.")
        self.config.discord_target_mib = value
        self.config.save_user_settings()

    def delete_clip(self, clip: ClipInfo) -> None:
        self.library.delete(clip)
        self.library_changed.emit()

    def rate_clip(self, clip: ClipInfo, rating: str) -> None:
        self.library.set_rating(clip, rating)
        self.league_events.feedback.invalidate()
        self.library_changed.emit()

    def shutdown(self) -> None:
        self._shutdown = True
        self.poll_timer.stop()
        self.clock_timer.stop()
        self.hotkey.stop()
        self.league_events.stop()
        self.discord_exporter.cancel_all()
        self.share_exporter.cancel_all()
        self.discord_webhook.cancel_all()
        self.video.stop()
        self.audio.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _tick_clock(self) -> None:
        self.diagnostics_changed.emit(self.video.diagnostics_snapshot())
        if self.recording_started_at is None:
            return
        self.recording_time_changed.emit(int(time.monotonic() - self.recording_started_at))

    def _set_state(self, state: RecorderState, detail: str) -> None:
        self.state = state
        self.detail = detail
        self.state_changed.emit(state, detail)

    def _on_event_monitor_status(self, message: str, connected: bool) -> None:
        self.event_status_text = message
        self.event_status_connected = connected
        self.event_status_changed.emit(message, connected)

    def _hotkey_error(self, message: str) -> None:
        self.error_occurred.emit(message)
