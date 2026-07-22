from __future__ import annotations

import logging
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QKeyEvent, QKeySequence, QPixmap, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.controller import RecorderController
from app.models import ClipInfo, GameHighlights, format_file_size
from app.services.discord_export_service import ClipTooLongForDiscord, DiscordExportResult
from app.services.share_export_service import ShareExportResult
from app.services.smart_trim_service import SmartTrimService
from app.ui.share_dialogs import (
    DiscordDestinationDialog,
    DiscordManageDialog,
    DiscordSetupDialog,
    ExportResultDialog,
    ShareChoiceDialog,
)
from app.ui.styles import APP_STYLE
from app.ui.timeline_widgets import FilmstripTrimWidget, MatchHighlightProgressBar

LOGGER = logging.getLogger(__name__)


def _clock(milliseconds: int) -> str:
    total = max(0, int(milliseconds // 1000))
    return f"{total // 60:02d}:{total % 60:02d}"


def _seconds_text(seconds: float) -> str:
    return f"{max(0.0, float(seconds)):.1f}s"


class _FullscreenHost(QDialog):
    exitRequested = Signal()

    def __init__(self, surface: QWidget, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InlineFullscreenHost")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setModal(False)
        self.setStyleSheet(APP_STYLE + "\nQDialog#InlineFullscreenHost { background:#000; }")
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(surface, 0, 0)
        self.exit_button = QPushButton("Exit fullscreen   Esc", self)
        self.exit_button.setObjectName("FullscreenExitButton")
        self.exit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.exit_button.setFixedSize(160, 40)
        self.exit_button.clicked.connect(self.exitRequested)
        escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        escape_shortcut.activated.connect(self.exitRequested)
        f11_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F11), self)
        f11_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        f11_shortcut.activated.connect(self.exitRequested)
        self._escape_shortcut = escape_shortcut
        self._f11_shortcut = f11_shortcut

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_F11):
            self.exitRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.exit_button.move(max(16, self.width() - self.exit_button.width() - 20), 20)
        self.exit_button.raise_()

    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        self.exitRequested.emit()


class InlineHighlightPlayer(QFrame):
    """Embedded full-match player, Smart Trim editor, and Discord exporter."""

    clipChanged = Signal(object)
    filmstripReady = Signal(object)
    filmstripFailed = Signal(object)

    def __init__(self, controller: RecorderController, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("InlineHighlightPlayer")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._clip: ClipInfo | None = None
        self._game: GameHighlights | None = None
        self._fullscreen_host: _FullscreenHost | None = None
        self._awaiting_first_frame = False
        self._frame_reveal_token = 0
        self._adjusting_position = False
        self._trim_suggestion = (0, 1)
        self._filmstrip_token = 0
        self._filmstrip_temp: tempfile.TemporaryDirectory[str] | None = None
        self._export_busy = False
        self._active_export_kind = ""
        # All recorder profiles currently produce a 16:9 output. Keep the
        # embedded viewport stable between clips instead of resizing it from
        # decoder frame hints, which can briefly include padded/cropped storage
        # dimensions and make the player appear to zoom when another highlight
        # is selected.
        self._video_aspect_ratio = 16 / 9
        self._controls_timer = QTimer(self)
        self._controls_timer.setSingleShot(True)
        self._controls_timer.setInterval(1400)
        self._controls_timer.timeout.connect(self._hide_video_controls)
        self.smart_trim = SmartTrimService()

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(9)

        self.surface = QFrame()
        self.surface.setObjectName("InlineVideoSurface")
        self.surface.setMinimumHeight(0)
        self.surface.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.surface_layout = QGridLayout(self.surface)
        self.surface_layout.setContentsMargins(0, 0, 0, 0)
        self.surface_layout.setSpacing(0)

        self.video_widget = QVideoWidget()
        self.video_widget.setStyleSheet("background:#000;")
        # Keep the whole frame visible. The surrounding surface is resized to the
        # actual media aspect ratio, which removes artificial letterboxing without
        # stretching or cropping gameplay.
        self.video_widget.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        # Ignore QVideoWidget's native-media sizeHint. Some Qt backends update
        # that hint when a new source starts decoding, which can enlarge the
        # layout and look like an unexpected zoom. The surrounding 16:9 surface
        # owns the geometry instead.
        self.video_widget.setMinimumSize(0, 0)
        self.video_widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.surface_layout.addWidget(self.video_widget, 0, 0)

        self.poster = QLabel("Select a colored highlight to play it here")
        self.poster.setObjectName("InlinePlayerPoster")
        self.poster.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.poster.setWordWrap(True)
        self.poster.setScaledContents(False)
        # A newly assigned thumbnail must not change the layout's sizeHint.
        self.poster.setMinimumSize(0, 0)
        self.poster.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.surface_layout.addWidget(self.poster, 0, 0)

        self.overlay = QFrame()
        self.overlay.setObjectName("PlayerOverlay")
        self.overlay.setMinimumHeight(68)
        self.overlay.setMaximumHeight(78)
        overlay_layout = QVBoxLayout(self.overlay)
        overlay_layout.setContentsMargins(14, 7, 14, 9)
        overlay_layout.setSpacing(4)
        self.match_seek = MatchHighlightProgressBar()
        self.match_seek.highlightActivated.connect(self._activate_highlight)
        overlay_layout.addWidget(self.match_seek)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.play_button = QToolButton()
        self.play_button.setObjectName("OverlayIconButton")
        self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.play_button.setIconSize(QSize(21, 21))
        self.play_button.setToolTip("Play / pause")
        self.play_button.clicked.connect(self.toggle_playback)
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("OverlayTime")
        self.mute_button = QToolButton()
        self.mute_button.setObjectName("OverlayIconButton")
        self.mute_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
        self.mute_button.setIconSize(QSize(20, 20))
        self.mute_button.setToolTip("Mute")
        self.mute_button.clicked.connect(self._toggle_mute)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setObjectName("OverlayVolumeSlider")
        self.volume.setRange(0, 100)
        self.volume.setValue(85)
        self.volume.setFixedWidth(92)
        self.volume.valueChanged.connect(self._set_volume)
        self.fullscreen_button = QToolButton()
        self.fullscreen_button.setObjectName("OverlayIconButton")
        self.fullscreen_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarMaxButton)
        )
        self.fullscreen_button.setIconSize(QSize(19, 19))
        self.fullscreen_button.setToolTip("Fullscreen")
        self.fullscreen_button.clicked.connect(self._toggle_fullscreen)
        controls.addWidget(self.play_button)
        controls.addWidget(self.time_label)
        controls.addStretch()
        controls.addWidget(self.mute_button)
        controls.addWidget(self.volume)
        controls.addWidget(self.fullscreen_button)
        overlay_layout.addLayout(controls)
        # Keep playback controls outside the video frame as well. In fullscreen
        # only ``surface`` is reparented, so no toolbar can cover the recording.
        self._root_layout.addWidget(self.surface)
        self._root_layout.addWidget(self.overlay)

        self.trim_panel = self._build_trim_panel()
        self._root_layout.addWidget(self.trim_panel)

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.85)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._state_changed)
        self.player.errorOccurred.connect(self._player_error)
        self.video_widget.videoSink().videoFrameChanged.connect(self._video_frame_changed)

        self.controller.discord_export_started.connect(self._on_export_started)
        self.controller.discord_export_progress.connect(self._on_export_progress)
        self.controller.discord_export_finished.connect(self._on_export_finished)
        self.controller.discord_export_failed.connect(self._on_export_failed)
        self.controller.discord_export_cancelled.connect(self._on_export_cancelled)
        self.controller.share_export_started.connect(self._on_share_export_started)
        self.controller.share_export_progress.connect(self._on_share_export_progress)
        self.controller.share_export_finished.connect(self._on_share_export_finished)
        self.controller.share_export_failed.connect(self._on_share_export_failed)
        self.controller.share_export_cancelled.connect(self._on_share_export_cancelled)
        self.filmstripReady.connect(self._apply_filmstrip)
        self.filmstripFailed.connect(self._filmstrip_failed)

        for widget in (self.surface, self.video_widget, self.poster, self.overlay):
            widget.setMouseTracking(True)
            widget.installEventFilter(self)
        self.surface.setMouseTracking(True)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self.toggle_playback)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=lambda: self._seek_relative(-5000))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=lambda: self._seek_relative(5000))
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, activated=self._toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self._exit_fullscreen)

    def _build_trim_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("InlineTrimPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)

        header = QHBoxLayout()
        title = QLabel("Smart Trim")
        title.setObjectName("SettingName")
        self.selection_summary = QLabel("Select a highlight")
        self.selection_summary.setObjectName("CardMuted")
        header.addWidget(title)
        header.addWidget(self.selection_summary)
        header.addStretch()
        self.auto_trim_button = QPushButton("Auto Trim")
        self.auto_trim_button.setObjectName("DarkButton")
        self.auto_trim_button.clicked.connect(self._use_auto_trim)
        self.full_clip_button = QPushButton("Use Full Clip")
        self.full_clip_button.setObjectName("DarkButton")
        self.full_clip_button.clicked.connect(self._use_full_clip)
        header.addWidget(self.auto_trim_button)
        header.addWidget(self.full_clip_button)
        layout.addLayout(header)

        self.trim_timeline = FilmstripTrimWidget(1)
        self.trim_timeline.selectionChanged.connect(self._trim_changed)
        self.trim_timeline.seekRequested.connect(self._seek_to)
        layout.addWidget(self.trim_timeline)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self.export_detail = QLabel("")
        self.export_detail.setObjectName("CardMuted")
        footer.addWidget(self.export_detail, 1)

        self.export_progress = QProgressBar()
        self.export_progress.setObjectName("DiscordExportProgress")
        self.export_progress.setRange(0, 100)
        self.export_progress.setFixedWidth(170)
        self.export_progress.hide()
        footer.addWidget(self.export_progress)
        self.cancel_export_button = QPushButton("Cancel")
        self.cancel_export_button.setObjectName("DangerButton")
        self.cancel_export_button.clicked.connect(self._cancel_export)
        self.cancel_export_button.hide()
        footer.addWidget(self.cancel_export_button)
        self.export_button = QPushButton("Share / Export")
        self.export_button.setObjectName("PrimaryButton")
        self.export_button.clicked.connect(self._open_share_export)
        self.export_button.setEnabled(False)
        footer.addWidget(self.export_button)
        layout.addLayout(footer)
        return panel

    @property
    def clip(self) -> ClipInfo | None:
        return self._clip

    def set_game(self, game: GameHighlights) -> None:
        self._game = game
        self.match_seek.set_game(game)
        if self._clip is not None:
            self.match_seek.select_clip(self._clip)
            self.match_seek.set_playback_position(self._clip, self.player.position())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fullscreen_host is None:
            self._update_normal_surface_height()
        if self._clip is not None and self.poster.isVisible():
            self._set_poster(self._clip)

    def _update_normal_surface_height(self) -> None:
        width = max(1, self.width())
        aspect = self._video_aspect_ratio if self._video_aspect_ratio > 0 else (16 / 9)
        # Match the player container to the exact decoded frame ratio. Do not cap the
        # height: a cap makes the container wider than the frame and creates side bars.
        target_height = round(width / aspect)
        self.surface.setFixedHeight(max(240, target_height))
        panel_height = self.trim_panel.sizeHint().height() if self.trim_panel.isVisible() else 0
        chrome_height = self.overlay.sizeHint().height()
        visible_blocks = 2 + (1 if self.trim_panel.isVisible() else 0)
        spacing_height = max(0, visible_blocks - 1) * self._root_layout.spacing()
        self.setMinimumHeight(
            self.surface.height() + chrome_height + panel_height + spacing_height
        )

    def load_clip(
        self,
        clip: ClipInfo,
        autoplay: bool = True,
        start_position_ms: int = 0,
    ) -> None:
        path = Path(clip.path)
        if not path.exists():
            self.poster.setText("This highlight file could not be found.")
            self.poster.show()
            return

        same_clip = self._clip is not None and Path(self._clip.path) == path
        self._frame_reveal_token += 1
        self._clip = clip
        self.match_seek.select_clip(clip)
        if self._fullscreen_host is None:
            self._update_normal_surface_height()

        duration_ms = max(250, int(round(clip.duration_seconds * 1000)))
        self.trim_timeline.set_duration(duration_ms)
        self._set_auto_suggestion(clip, duration_ms)
        self._set_fallback_thumbnail(clip)
        self._start_filmstrip_extraction(clip, duration_ms)

        if not same_clip:
            self._awaiting_first_frame = True
            self.player.stop()
            self.video_widget.videoSink().setVideoFrame(QVideoFrame())
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            self._set_poster(clip)
            self.poster.show()
        elif self.poster.isVisible():
            self._awaiting_first_frame = True

        self.clipChanged.emit(clip)

        def apply_state() -> None:
            start, end = self._selection_bounds()
            requested = int(start_position_ms)
            target = requested if start <= requested <= end else start
            self.player.setPosition(target)
            self.match_seek.set_playback_position(clip, target)
            if autoplay:
                self.player.play()
            else:
                self.player.pause()
                self.poster.show()

        QTimer.singleShot(90 if not same_clip else 0, apply_state)

    def _set_auto_suggestion(self, clip: ClipInfo, duration_ms: int) -> None:
        duration = duration_ms / 1000.0
        if clip.suggested_trim_start is not None and clip.suggested_trim_end is not None:
            start = max(0.0, min(duration, clip.suggested_trim_start))
            end = max(start + 0.25, min(duration, clip.suggested_trim_end))
        else:
            suggestion = self.smart_trim.suggest(
                duration,
                clip.events,
                clip.trigger_relative_seconds,
                manual=clip.label.strip().upper() == "MANUAL CLIP",
            )
            start, end = suggestion.start_seconds, suggestion.end_seconds
        self._trim_suggestion = (int(round(start * 1000)), int(round(end * 1000)))
        self.trim_timeline.set_selection(*self._trim_suggestion, emit=False)
        self.trim_timeline.set_playhead(self._trim_suggestion[0])
        self._trim_changed(*self._trim_suggestion)

    def _selection_bounds(self) -> tuple[int, int]:
        return self.trim_timeline.start_ms, self.trim_timeline.end_ms

    def _activate_highlight(self, clip: ClipInfo, local_milliseconds: int) -> None:
        same = self._clip is not None and Path(self._clip.path) == Path(clip.path)
        if same:
            self._seek_to(local_milliseconds)
            self.player.play()
            return
        self.load_clip(clip, autoplay=True, start_position_ms=local_milliseconds)

    def _show_video_controls(self, restart_timer: bool = True) -> None:
        # The controls are normal layout rows now, not overlays. Keep them visible
        # so the layout never jumps and the video is never obscured.
        self._controls_timer.stop()
        self.overlay.show()

    def _hide_video_controls(self) -> None:
        # Intentionally do nothing. Hiding an out-of-video toolbar would only make
        # the page jump; fullscreen contains the video surface alone.
        self._controls_timer.stop()

    def toggle_playback(self) -> None:
        if self._clip is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self._show_video_controls(restart_timer=False)
            return
        start, end = self._selection_bounds()
        if self.player.position() < start or self.player.position() >= end - 50:
            self.player.setPosition(start)
        if self.poster.isVisible():
            self._awaiting_first_frame = True
        self.player.play()
        self._show_video_controls(restart_timer=True)

    def _seek_to(self, value: int) -> None:
        if self._clip is None:
            return
        start, end = self._selection_bounds()
        target = max(start, min(int(value), end))
        self.poster.hide()
        self.player.setPosition(target)
        self.trim_timeline.set_playhead(target)

    def _seek_relative(self, offset: int) -> None:
        self._seek_to(self.player.position() + offset)

    def _position_changed(self, position: int) -> None:
        if self._clip is None or self._adjusting_position:
            return
        start, end = self._selection_bounds()
        if position < start:
            self._adjusting_position = True
            self.player.setPosition(start)
            self._adjusting_position = False
            position = start
        if (
            self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            and position >= max(start, end - 35)
        ):
            self.player.pause()
            self._adjusting_position = True
            self.player.setPosition(end)
            self._adjusting_position = False
            position = end
        self.match_seek.set_playback_position(self._clip, position)
        self.trim_timeline.set_playhead(position)
        self.time_label.setText(f"{_clock(position - start)} / {_clock(end - start)}")

    def _duration_changed(self, duration: int) -> None:
        if self._clip is None or duration <= 0:
            return
        old_duration = self.trim_timeline.duration_ms
        self.trim_timeline.set_duration(duration)
        if abs(old_duration - duration) > 500:
            self._set_auto_suggestion(self._clip, duration)
        self._position_changed(self.player.position())

    def _trim_changed(self, start: int, end: int) -> None:
        if end <= start:
            return
        position = self.player.position() if hasattr(self, "player") else start
        if position < start or position > end:
            if hasattr(self, "player"):
                self.player.pause()
                self.player.setPosition(start)
            position = start
        selected = (end - start) / 1000.0
        original = self.trim_timeline.duration_ms / 1000.0
        self.selection_summary.setText(
            f"Selected {_seconds_text(selected)} · Original {_seconds_text(original)}"
        )
        self.time_label.setText(f"{_clock(position - start)} / {_clock(end - start)}")
        self._update_export_prediction()

    def _use_auto_trim(self) -> None:
        self.trim_timeline.set_selection(*self._trim_suggestion)
        self.player.pause()
        self.player.setPosition(self._trim_suggestion[0])

    def _use_full_clip(self) -> None:
        self.trim_timeline.set_selection(0, self.trim_timeline.duration_ms)
        self.player.pause()
        self.player.setPosition(0)

    def _update_export_prediction(self) -> None:
        self.export_button.setEnabled(self._clip is not None and not self._export_busy)

    def _discord_prediction_text(self) -> str:
        if self._clip is None:
            raise RuntimeError("Select a highlight first.")
        start, end = self._selection_bounds()
        plan = self.controller.discord_export_plan(
            self._clip,
            start / 1000.0,
            end / 1000.0,
        )
        return (
            f"Selected {(end - start) / 1000.0:.1f}s · {plan.profile.label} · "
            f"about {format_file_size(plan.estimated_size_bytes)} · "
            f"{plan.video_bitrate_bps // 1000:,} kbps video"
        )

    def _open_share_export(self) -> None:
        if self._clip is None or self._export_busy:
            return
        chooser = ShareChoiceDialog(self)
        if chooser.exec() != QDialog.DialogCode.Accepted:
            return
        if chooser.choice == "save":
            self._start_share_export()
            return
        if chooser.choice == "discord":
            self._open_discord_destination()

    def _open_discord_destination(self) -> None:
        if self._clip is None or self._export_busy:
            return
        try:
            prediction = self._discord_prediction_text()
        except ClipTooLongForDiscord as exc:
            QMessageBox.warning(self, "Trim the clip further", str(exc))
            return
        except Exception as exc:
            QMessageBox.warning(self, "Discord export unavailable", str(exc))
            return

        while True:
            connected = self.controller.discord_webhook_configured
            destination = DiscordDestinationDialog(connected, prediction, self)
            if destination.exec() != QDialog.DialogCode.Accepted:
                return
            if destination.choice == "discord_file":
                self._start_discord_export(send=False)
                return
            if destination.choice == "discord_send":
                self._start_discord_export(send=True)
                return
            if destination.choice == "discord_setup":
                setup = DiscordSetupDialog(self.controller, self)
                if setup.exec() == QDialog.DialogCode.Accepted and setup.connected:
                    self._start_discord_export(send=True)
                return
            if destination.choice == "discord_manage":
                manage = DiscordManageDialog(self.controller, self)
                if manage.exec() != QDialog.DialogCode.Accepted:
                    continue
                if manage.action == "remove":
                    self.controller.clear_discord_webhook()
                    QMessageBox.information(self, "Discord disconnected", "The saved Discord connection was removed.")
                    continue
                if manage.action == "change":
                    setup = DiscordSetupDialog(self.controller, self)
                    setup.exec()
                    continue

    def _start_share_export(self) -> None:
        if self._clip is None:
            return
        start, end = self._selection_bounds()
        try:
            self.controller.export_share_copy(
                self._clip,
                start / 1000.0,
                end / 1000.0,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Cannot export video", str(exc))

    def _start_discord_export(self, *, send: bool) -> None:
        if self._clip is None:
            return
        start, end = self._selection_bounds()
        try:
            self.controller.export_for_discord(
                self._clip,
                start / 1000.0,
                end / 1000.0,
                send_to_discord=send,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Cannot export for Discord", str(exc))

    def _cancel_export(self) -> None:
        if self._clip is None:
            return
        if self._active_export_kind == "share":
            self.controller.cancel_share_export(self._clip)
        else:
            self.controller.cancel_discord_export(self._clip)

    def _same_export_clip(self, clip: ClipInfo) -> bool:
        return self._clip is not None and Path(self._clip.path) == Path(clip.path)

    def _begin_export_ui(self, kind: str, message: str) -> None:
        self._export_busy = True
        self._active_export_kind = kind
        self.export_button.setEnabled(False)
        self.auto_trim_button.setEnabled(False)
        self.full_clip_button.setEnabled(False)
        self.export_progress.setValue(0)
        self.export_progress.show()
        self.cancel_export_button.show()
        self.export_detail.setText(message)

    def _on_export_started(self, clip: ClipInfo, _plan) -> None:
        if self._same_export_clip(clip):
            self._begin_export_ui("discord", "Preparing Discord copy…")

    def _on_export_progress(self, clip: ClipInfo, percent: int, message: str) -> None:
        if not self._same_export_clip(clip):
            return
        self.export_progress.setValue(max(0, min(100, int(percent))))
        self.export_detail.setText(message)

    def _on_share_export_started(self, clip: ClipInfo) -> None:
        if self._same_export_clip(clip):
            self._begin_export_ui("share", "Preparing high-quality copy…")

    def _on_share_export_progress(self, clip: ClipInfo, percent: int, message: str) -> None:
        if not self._same_export_clip(clip):
            return
        self.export_progress.setValue(max(0, min(100, int(percent))))
        self.export_detail.setText(message)

    def _finish_export_ui(self) -> None:
        self._export_busy = False
        self._active_export_kind = ""
        self.export_progress.hide()
        self.cancel_export_button.hide()
        self.auto_trim_button.setEnabled(True)
        self.full_clip_button.setEnabled(True)
        self.export_detail.setText("")
        self._update_export_prediction()

    def _on_export_finished(self, clip: ClipInfo, result: DiscordExportResult) -> None:
        if not self._same_export_clip(clip):
            return
        self._finish_export_ui()
        ExportResultDialog(
            result.output_path,
            sent_to_discord=result.sent_to_discord,
            send_error=result.send_error,
            parent=self,
        ).exec()

    def _on_share_export_finished(self, clip: ClipInfo, result: ShareExportResult) -> None:
        if not self._same_export_clip(clip):
            return
        self._finish_export_ui()
        ExportResultDialog(result.output_path, parent=self).exec()

    def _on_export_failed(self, clip: ClipInfo, message: str) -> None:
        if not self._same_export_clip(clip):
            return
        self._finish_export_ui()
        QMessageBox.warning(self, "Discord export failed", message)

    def _on_share_export_failed(self, clip: ClipInfo, message: str) -> None:
        if not self._same_export_clip(clip):
            return
        self._finish_export_ui()
        QMessageBox.warning(self, "Export failed", message)

    def _on_export_cancelled(self, clip: ClipInfo) -> None:
        if not self._same_export_clip(clip):
            return
        self._finish_export_ui()
        self.export_detail.setText("Export cancelled. Temporary files were removed.")

    def _on_share_export_cancelled(self, clip: ClipInfo) -> None:
        if not self._same_export_clip(clip):
            return
        self._finish_export_ui()
        self.export_detail.setText("Export cancelled. Temporary files were removed.")

    def _set_fallback_thumbnail(self, clip: ClipInfo) -> None:
        if clip.thumbnail_path and clip.thumbnail_path.exists():
            pixmap = QPixmap(str(clip.thumbnail_path))
            if not pixmap.isNull():
                self.trim_timeline.set_thumbnails([pixmap] * 10)

    def _start_filmstrip_extraction(self, clip: ClipInfo, duration_ms: int) -> None:
        self._filmstrip_token += 1
        token = self._filmstrip_token
        if self._filmstrip_temp is not None:
            self._filmstrip_temp.cleanup()
        self._filmstrip_temp = tempfile.TemporaryDirectory(prefix="lh-inline-filmstrip-")
        temp_name = self._filmstrip_temp.name

        def work() -> None:
            try:
                ffmpeg = self.controller.ffmpeg
                ffmpeg.require()
                assert ffmpeg.ffmpeg is not None
                frame_count = 12
                duration_seconds = max(0.25, duration_ms / 1000.0)
                output_pattern = Path(temp_name) / "frame_%02d.jpg"
                command = [
                    str(ffmpeg.ffmpeg),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(clip.path),
                    "-vf",
                    (
                        f"fps={frame_count / duration_seconds:.8f},"
                        "scale=240:136:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                        "pad=240:136:(ow-iw)/2:(oh-ih)/2:color=black"
                    ),
                    "-frames:v",
                    str(frame_count),
                    "-q:v",
                    "4",
                    str(output_pattern),
                ]
                ffmpeg.run(command, timeout=90, low_priority=True)
                paths = sorted(Path(temp_name).glob("frame_*.jpg"))
                if not paths:
                    raise RuntimeError("FFmpeg did not produce preview frames.")
                self.filmstripReady.emit((token, [str(path) for path in paths]))
            except Exception as exc:
                LOGGER.warning("Could not create inline filmstrip for %s: %s", clip.path, exc)
                self.filmstripFailed.emit((token, str(exc)))

        threading.Thread(target=work, name="InlineFilmstripExtractor", daemon=True).start()

    def _apply_filmstrip(self, payload: object) -> None:
        token, paths = payload
        if int(token) != self._filmstrip_token:
            return
        pixmaps = [QPixmap(path) for path in paths]
        self.trim_timeline.set_thumbnails([pixmap for pixmap in pixmaps if not pixmap.isNull()])

    def _filmstrip_failed(self, payload: object) -> None:
        token, _message = payload
        if int(token) != self._filmstrip_token:
            return
        # The clip thumbnail remains visible; trimming does not depend on frames.

    def _set_poster(self, clip: ClipInfo) -> None:
        pixmap = QPixmap(str(clip.thumbnail_path)) if clip.thumbnail_path else QPixmap()
        if pixmap.isNull():
            self.poster.setPixmap(QPixmap())
            self.poster.setText("Press play to watch this highlight")
            return
        target = self.surface.size() if self.surface.width() > 0 else QSize(960, 540)
        self.poster.setText("")
        self.poster.setPixmap(
            pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _video_frame_changed(self, frame: QVideoFrame) -> None:
        if not frame.isValid():
            return
        # Do not resize the embedded player from decoder frame dimensions. The
        # FFmpeg/Qt backend can expose a padded coded frame before applying its
        # visible viewport, causing the UI to jump or appear zoomed after changing
        # highlights. QVideoWidget still uses KeepAspectRatio, so the complete
        # frame remains visible inside the stable 16:9 surface.
        if not self._awaiting_first_frame or self._clip is None:
            return
        self._awaiting_first_frame = False
        token = self._frame_reveal_token
        image = frame.toImage()
        if not image.isNull():
            pixmap = QPixmap.fromImage(image)
            target = self.surface.size() if self.surface.width() > 0 else QSize(960, 540)
            self.poster.setText("")
            self.poster.setPixmap(
                pixmap.scaled(
                    target,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        QTimer.singleShot(24, lambda current=token: self._reveal_video_frame(current))

    def _reveal_video_frame(self, token: int) -> None:
        if token == self._frame_reveal_token and not self._awaiting_first_frame:
            self.poster.hide()

    def _state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        icon = (
            QStyle.StandardPixmap.SP_MediaPause
            if state == QMediaPlayer.PlaybackState.PlayingState
            else QStyle.StandardPixmap.SP_MediaPlay
        )
        self.play_button.setIcon(self.style().standardIcon(icon))
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._show_video_controls(restart_timer=True)
        else:
            self._show_video_controls(restart_timer=False)

    def _toggle_mute(self) -> None:
        muted = not self.audio_output.isMuted()
        self.audio_output.setMuted(muted)
        icon = (
            QStyle.StandardPixmap.SP_MediaVolumeMuted
            if muted
            else QStyle.StandardPixmap.SP_MediaVolume
        )
        self.mute_button.setIcon(self.style().standardIcon(icon))

    def _set_volume(self, value: int) -> None:
        self.audio_output.setVolume(max(0.0, min(1.0, value / 100.0)))
        if value > 0 and self.audio_output.isMuted():
            self.audio_output.setMuted(False)

    def _toggle_fullscreen(self) -> None:
        if self._fullscreen_host is None:
            self._enter_fullscreen()
        else:
            self._exit_fullscreen()

    def _enter_fullscreen(self) -> None:
        if self._fullscreen_host is not None:
            return
        source_window = self.window()
        screen = source_window.screen() if source_window is not None else None
        self._controls_timer.stop()
        self._root_layout.removeWidget(self.surface)
        self.surface.setParent(None)
        self.surface.setMinimumHeight(0)
        self.surface.setMaximumHeight(16777215)
        self.surface.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        host = _FullscreenHost(self.surface)
        host.exitRequested.connect(self._exit_fullscreen)
        self._fullscreen_host = host
        if screen is not None:
            host.setGeometry(screen.geometry())
            host.move(screen.geometry().topLeft())
        host.show()
        if screen is not None and host.windowHandle() is not None:
            host.windowHandle().setScreen(screen)
        host.showFullScreen()
        self.surface.show()
        host.exit_button.raise_()

    def _exit_fullscreen(self) -> None:
        host = self._fullscreen_host
        if host is None:
            return
        self._fullscreen_host = None
        host.layout().removeWidget(self.surface)
        self.surface.setParent(self)
        # Restore the video above the playback controls.
        self._root_layout.insertWidget(0, self.surface)
        self.surface.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        host.hide()
        host.deleteLater()
        self._update_normal_surface_height()
        self.surface.show()
        self._show_video_controls(restart_timer=False)

    def release_media(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self.video_widget.videoSink().setVideoFrame(QVideoFrame())
        self._exit_fullscreen()

    def stop(self) -> None:
        self.player.stop()
        self._exit_fullscreen()
        self._filmstrip_token += 1
        if self._filmstrip_temp is not None:
            self._filmstrip_temp.cleanup()
            self._filmstrip_temp = None

    def _player_error(self, _error, error_string: str) -> None:
        self._awaiting_first_frame = False
        self._frame_reveal_token += 1
        self.poster.setPixmap(QPixmap())
        self.poster.setText(error_string or "The video could not be played.")
        self.poster.show()

    def eventFilter(self, watched, event) -> bool:
        if watched in (self.surface, self.video_widget, self.poster, self.overlay):
            if event.type() in (QEvent.Type.Enter, QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress):
                self._show_video_controls(restart_timer=True)
            if event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                self._toggle_fullscreen()
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self.toggle_playback()
                return True
        if event.type() == QEvent.Type.KeyPress and self._fullscreen_host is not None:
            if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_F11):
                self._exit_fullscreen()
                return True
        return super().eventFilter(watched, event)
