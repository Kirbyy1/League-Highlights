from __future__ import annotations

import logging
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QCloseEvent, QKeySequence, QMouseEvent, QPixmap, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
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
from app.models import ClipInfo
from app.ui.timeline_widgets import FilmstripTrimWidget

LOGGER = logging.getLogger(__name__)


def _clock(milliseconds: int) -> str:
    total = max(0, int(milliseconds // 1000))
    return f"{total // 60:02d}:{total % 60:02d}"


class ClickSeekSlider(QSlider):
    """A seek slider that jumps directly to the clicked or dragged position."""

    seekRequested = Signal(int)

    def _value_at(self, x: float) -> int:
        return QStyle.sliderValueFromPosition(
            self.minimum(),
            self.maximum(),
            int(round(x)),
            max(1, self.width()),
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self.sliderPressed.emit()
            value = self._value_at(event.position().x())
            self.setValue(value)
            self.seekRequested.emit(value)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            value = self._value_at(event.position().x())
            self.setValue(value)
            self.seekRequested.emit(value)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            value = self._value_at(event.position().x())
            self.setValue(value)
            self.seekRequested.emit(value)
            self.setSliderDown(False)
            self.sliderReleased.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class VideoPlayerDialog(QDialog):
    """Built-in player with YouTube-style overlay controls and filmstrip trimming.

    Playback is intentionally constrained to the selected IN/OUT range. No
    waveform is generated or displayed.
    """

    filmstrip_ready = Signal(object)
    filmstrip_failed = Signal(str)

    def __init__(
        self,
        clip: ClipInfo,
        controller: RecorderController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.clip = clip
        self.controller = controller
        self.duration_ms = max(1, int(round(clip.duration_seconds * 1000)))
        self._trim_busy = False
        self._trim_replace_original = False
        self._pending_trim_request: tuple[float, float, bool] | None = None
        self._last_trim_bounds = (0.0, float(clip.duration_seconds))
        self._closing = False
        self._seeking = False
        self._resume_after_seek = False
        self._adjusting_position = False
        self._overlay_hovered = False
        self._fullscreen = False
        self._normal_geometry = None
        self._filmstrip_temp = tempfile.TemporaryDirectory(prefix="lh-filmstrip-")
        self._trim_release_timer = QTimer(self)
        self._trim_release_timer.setSingleShot(True)
        self._trim_release_timer.timeout.connect(self._dispatch_pending_trim)

        self.setWindowTitle(f"{clip.label.replace('_', ' ').title()} — League Highlights")
        self.setObjectName("VideoPlayerDialog")
        self.resize(1180, 820)
        self.setMinimumSize(850, 650)
        self.setModal(False)

        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(18, 16, 18, 18)
        self.root_layout.setSpacing(12)

        self.header_widget = QWidget()
        header = QHBoxLayout(self.header_widget)
        header.setContentsMargins(0, 0, 0, 0)
        copy = QVBoxLayout()
        title = QLabel(clip.label.replace("_", " ").title())
        title.setObjectName("PlayerTitle")
        subtitle_parts = [clip.champion_name, clip.player_name, clip.duration_text, clip.file_size_text]
        subtitle = QLabel(" • ".join(part for part in subtitle_parts if part))
        subtitle.setObjectName("PlayerMuted")
        copy.addWidget(title)
        copy.addWidget(subtitle)
        header.addLayout(copy, 1)
        close_button = QPushButton("Close")
        close_button.setObjectName("DarkButton")
        close_button.clicked.connect(self.close)
        header.addWidget(close_button)
        self.root_layout.addWidget(self.header_widget)

        self.video_frame = QFrame()
        self.video_frame.setObjectName("VideoSurfaceFrame")
        self.video_frame.setMouseTracking(True)
        video_layout = QGridLayout(self.video_frame)
        video_layout.setContentsMargins(2, 2, 2, 2)
        video_layout.setSpacing(0)

        self.video_widget = QVideoWidget()
        self.video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video_widget.setStyleSheet("background:#000;")
        self.video_widget.setMouseTracking(True)
        video_layout.addWidget(self.video_widget, 0, 0)

        self.player_overlay = self._build_player_overlay()
        video_layout.addWidget(
            self.player_overlay,
            0,
            0,
            Qt.AlignmentFlag.AlignBottom,
        )
        self.root_layout.addWidget(self.video_frame, 1)

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.85)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.setSource(QUrl.fromLocalFile(str(clip.path)))

        self.trim_panel = QFrame()
        self.trim_panel.setObjectName("TrimPanelV26")
        trim_layout = QVBoxLayout(self.trim_panel)
        trim_layout.setContentsMargins(16, 14, 16, 14)
        trim_layout.setSpacing(11)

        trim_header = QHBoxLayout()
        trim_copy = QVBoxLayout()
        trim_title = QLabel("Trim clip")
        trim_title.setObjectName("TrimTitle")
        trim_help = QLabel(
            "Drag the green IN and OUT handles to choose the part to keep. "
            "Preview playback stays inside the selected range."
        )
        trim_help.setObjectName("PlayerMuted")
        trim_help.setWordWrap(True)
        trim_copy.addWidget(trim_title)
        trim_copy.addWidget(trim_help)
        trim_header.addLayout(trim_copy, 1)
        self.trim_length = QLabel(f"Keep {clip.duration_text}")
        self.trim_length.setObjectName("TrimLength")
        trim_header.addWidget(self.trim_length)
        trim_layout.addLayout(trim_header)

        self.trim_timeline = FilmstripTrimWidget(self.duration_ms)
        self.trim_timeline.selectionChanged.connect(self._on_trim_selection_changed)
        self.trim_timeline.seekRequested.connect(self._seek_inside_selection)
        trim_layout.addWidget(self.trim_timeline)

        readout_row = QHBoxLayout()
        readout_row.setSpacing(12)
        self.start_value = QLabel(f"IN  {self._precise_clock(0)}")
        self.start_value.setObjectName("TrimCompactValue")
        self.end_value = QLabel(f"OUT  {self._precise_clock(self.duration_ms)}")
        self.end_value.setObjectName("TrimCompactValue")
        self.selection_value = QLabel(f"SELECTED  {self._precise_clock(self.duration_ms)}")
        self.selection_value.setObjectName("TrimCompactMuted")
        readout_row.addWidget(self.start_value)
        readout_row.addWidget(self.end_value)
        readout_row.addWidget(self.selection_value)
        readout_row.addStretch()
        trim_layout.addLayout(readout_row)

        actions = QHBoxLayout()
        self.trim_status = QLabel("Preview frames are loading. Trimming is available immediately.")
        self.trim_status.setObjectName("PlayerMuted")
        self.trim_progress = QProgressBar()
        self.trim_progress.setRange(0, 0)
        self.trim_progress.setTextVisible(False)
        self.trim_progress.setFixedWidth(120)
        self.trim_progress.hide()
        reset = QPushButton("Reset selection")
        reset.setObjectName("DarkButton")
        reset.clicked.connect(self._reset_trim)
        self.save_copy_button = QPushButton("Save trimmed copy")
        self.save_copy_button.setObjectName("PrimaryButton")
        self.save_copy_button.clicked.connect(lambda: self._save_trim(False))
        self.replace_button = QPushButton("Replace original")
        self.replace_button.setObjectName("DangerButton")
        self.replace_button.clicked.connect(lambda: self._save_trim(True))
        actions.addWidget(self.trim_status, 1)
        actions.addWidget(self.trim_progress)
        actions.addWidget(reset)
        actions.addWidget(self.save_copy_button)
        actions.addWidget(self.replace_button)
        trim_layout.addLayout(actions)
        self.root_layout.addWidget(self.trim_panel)

        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.errorOccurred.connect(self._on_player_error)
        self.controller.trim_finished.connect(self._on_trim_finished)
        self.controller.trim_failed.connect(self._on_trim_failed)
        self.filmstrip_ready.connect(self._apply_filmstrip)
        self.filmstrip_failed.connect(self._on_filmstrip_failed)

        for widget in (self.video_frame, self.video_widget, self.player_overlay):
            widget.installEventFilter(self)

        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self._toggle_playback)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=lambda: self._seek_relative(-5000))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=lambda: self._seek_relative(5000))
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self._escape)

        self._on_trim_selection_changed(0, self.duration_ms)
        self._set_fallback_thumbnail()
        self._show_overlay()
        QTimer.singleShot(30, self._start_filmstrip_extraction)
        QTimer.singleShot(120, self._start_selected_playback)

    def _build_player_overlay(self) -> QFrame:
        overlay = QFrame()
        overlay.setObjectName("PlayerOverlay")
        overlay.setMouseTracking(True)
        overlay.setMinimumHeight(82)
        overlay.setMaximumHeight(96)

        layout = QVBoxLayout(overlay)
        layout.setContentsMargins(14, 10, 14, 11)
        layout.setSpacing(6)

        self.overlay_seek = ClickSeekSlider(Qt.Orientation.Horizontal)
        self.overlay_seek.setObjectName("OverlaySeekSlider")
        self.overlay_seek.setRange(0, self.duration_ms)
        self.overlay_seek.setValue(0)
        self.overlay_seek.setTracking(True)
        self.overlay_seek.seekRequested.connect(self._seek_inside_selection)
        self.overlay_seek.sliderPressed.connect(self._begin_overlay_seek)
        self.overlay_seek.sliderReleased.connect(self._finish_overlay_seek)
        layout.addWidget(self.overlay_seek)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)

        self.play_button = QToolButton()
        self.play_button.setObjectName("OverlayIconButton")
        self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.play_button.setIconSize(QSize(21, 21))
        self.play_button.setToolTip("Play / pause (Space)")
        self.play_button.clicked.connect(self._toggle_playback)

        self.current_time = QLabel(f"00:00 / {_clock(self.duration_ms)}")
        self.current_time.setObjectName("OverlayTime")

        self.mute_button = QToolButton()
        self.mute_button.setObjectName("OverlayIconButton")
        self.mute_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
        self.mute_button.setIconSize(QSize(20, 20))
        self.mute_button.setToolTip("Mute")
        self.mute_button.clicked.connect(self._toggle_mute)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setObjectName("OverlayVolumeSlider")
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(85)
        self.volume_slider.setFixedWidth(92)
        self.volume_slider.valueChanged.connect(self._set_volume)

        self.fullscreen_button = QToolButton()
        self.fullscreen_button.setObjectName("OverlayIconButton")
        self.fullscreen_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarMaxButton))
        self.fullscreen_button.setIconSize(QSize(19, 19))
        self.fullscreen_button.setToolTip("Fullscreen")
        self.fullscreen_button.clicked.connect(self._toggle_fullscreen)

        controls.addWidget(self.play_button)
        controls.addWidget(self.current_time)
        controls.addStretch()
        controls.addWidget(self.mute_button)
        controls.addWidget(self.volume_slider)
        controls.addWidget(self.fullscreen_button)
        layout.addLayout(controls)

        self.overlay_effect = QGraphicsOpacityEffect(overlay)
        self.overlay_effect.setOpacity(1.0)
        overlay.setGraphicsEffect(self.overlay_effect)
        self.overlay_animation = QPropertyAnimation(self.overlay_effect, b"opacity", self)
        self.overlay_animation.setDuration(180)
        self.overlay_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.overlay_animation.finished.connect(self._on_overlay_animation_finished)
        self.overlay_hide_timer = QTimer(self)
        self.overlay_hide_timer.setSingleShot(True)
        self.overlay_hide_timer.setInterval(2200)
        self.overlay_hide_timer.timeout.connect(self._hide_overlay)
        return overlay

    @staticmethod
    def _precise_clock(milliseconds: int) -> str:
        value = max(0, int(milliseconds))
        minutes, remainder = divmod(value, 60_000)
        seconds, millis = divmod(remainder, 1000)
        return f"{minutes:02d}:{seconds:02d}.{millis:03d}"

    def _selection_bounds(self) -> tuple[int, int]:
        if hasattr(self, "trim_timeline"):
            return self.trim_timeline.start_ms, self.trim_timeline.end_ms
        return 0, self.duration_ms

    def _clamp_to_selection(self, position: int) -> int:
        start, end = self._selection_bounds()
        return max(start, min(int(position), end))

    def _set_fallback_thumbnail(self) -> None:
        if self.clip.thumbnail_path and self.clip.thumbnail_path.exists():
            pixmap = QPixmap(str(self.clip.thumbnail_path))
            if not pixmap.isNull():
                self.trim_timeline.set_thumbnails([pixmap] * 10)

    def _start_filmstrip_extraction(self) -> None:
        thread = threading.Thread(
            target=self._extract_filmstrip,
            name="FilmstripExtractor",
            daemon=True,
        )
        thread.start()

    def _extract_filmstrip(self) -> None:
        try:
            ffmpeg = self.controller.ffmpeg
            ffmpeg.require()
            assert ffmpeg.ffmpeg is not None
            frame_count = 12
            duration_seconds = max(0.25, self.duration_ms / 1000.0)
            fps_value = frame_count / duration_seconds
            output_pattern = Path(self._filmstrip_temp.name) / "frame_%02d.jpg"
            command = [
                str(ffmpeg.ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(self.clip.path),
                "-vf",
                (
                    f"fps={fps_value:.8f},"
                    "scale=240:136:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                    "pad=240:136:(ow-iw)/2:(oh-ih)/2:black"
                ),
                "-frames:v",
                str(frame_count),
                "-q:v",
                "4",
                str(output_pattern),
            ]
            ffmpeg.run(command, timeout=90, low_priority=True)
            paths = sorted(Path(self._filmstrip_temp.name).glob("frame_*.jpg"))
            if not paths:
                raise RuntimeError("FFmpeg did not produce preview frames.")
            if not self._closing:
                self.filmstrip_ready.emit([str(path) for path in paths])
        except Exception as exc:
            LOGGER.warning("Could not create filmstrip previews for %s: %s", self.clip.path, exc)
            if not self._closing:
                self.filmstrip_failed.emit(str(exc))

    def _apply_filmstrip(self, paths: list[str]) -> None:
        if self._closing:
            return
        pixmaps = [QPixmap(path) for path in paths]
        pixmaps = [pixmap for pixmap in pixmaps if not pixmap.isNull()]
        if pixmaps:
            self.trim_timeline.set_thumbnails(pixmaps)
            self.trim_status.setText("Drag the handles to choose the exact section to keep.")

    def _on_filmstrip_failed(self, _message: str) -> None:
        if self._closing:
            return
        if self.trim_timeline.thumbnails:
            self.trim_status.setText("Using the clip thumbnail as the timeline preview.")
        else:
            self.trim_status.setText("Preview frames unavailable. Trimming still works normally.")

    def _start_selected_playback(self) -> None:
        start, _ = self._selection_bounds()
        self.player.setPosition(start)
        self.player.play()

    def _on_position_changed(self, position: int) -> None:
        if self._adjusting_position:
            return

        start, end = self._selection_bounds()
        corrected = self._clamp_to_selection(position)
        if corrected != position:
            self._adjusting_position = True
            self.player.setPosition(corrected)
            self._adjusting_position = False
            position = corrected

        if (
            self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            and position >= max(start, end - 35)
        ):
            self.player.pause()
            self._adjusting_position = True
            self.player.setPosition(end)
            self._adjusting_position = False
            position = end
            self._show_overlay()

        relative = max(0, min(position, end) - start)
        selected_duration = max(0, end - start)
        self.current_time.setText(f"{_clock(relative)} / {_clock(selected_duration)}")
        if not self._seeking:
            self.overlay_seek.setValue(position)
        self.trim_timeline.set_playhead(position)

    def _on_duration_changed(self, duration: int) -> None:
        if duration <= 0:
            return
        old_duration = self.duration_ms
        self.duration_ms = duration
        self.trim_timeline.set_duration(duration)
        if self.trim_timeline.end_ms >= old_duration - 500:
            self.trim_timeline.set_selection(self.trim_timeline.start_ms, duration)
        else:
            self._on_trim_selection_changed(self.trim_timeline.start_ms, self.trim_timeline.end_ms)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        icon = QStyle.StandardPixmap.SP_MediaPause if playing else QStyle.StandardPixmap.SP_MediaPlay
        self.play_button.setIcon(self.style().standardIcon(icon))
        if playing:
            if not self._overlay_hovered:
                self.overlay_hide_timer.start()
        else:
            self._show_overlay(auto_hide=False)

    def _toggle_playback(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            return

        start, end = self._selection_bounds()
        position = self.player.position()
        if position < start or position >= end - 35:
            self.player.setPosition(start)
        self.player.play()

    def _seek_inside_selection(self, position: int) -> None:
        value = self._clamp_to_selection(position)
        self.player.setPosition(value)
        self.overlay_seek.setValue(value)
        self.trim_timeline.set_playhead(value)
        self._show_overlay()

    def _seek_relative(self, delta: int) -> None:
        self._seek_inside_selection(self.player.position() + delta)

    def _begin_overlay_seek(self) -> None:
        self._seeking = True
        self._resume_after_seek = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if self._resume_after_seek:
            self.player.pause()
        self._show_overlay(auto_hide=False)

    def _finish_overlay_seek(self) -> None:
        self._seeking = False
        self._seek_inside_selection(self.overlay_seek.value())
        if self._resume_after_seek:
            self.player.play()
        self._resume_after_seek = False

    def _set_volume(self, value: int) -> None:
        self.audio_output.setVolume(value / 100.0)
        if value > 0 and self.audio_output.isMuted():
            self.audio_output.setMuted(False)
        self._update_volume_icon()
        self._show_overlay()

    def _toggle_mute(self) -> None:
        self.audio_output.setMuted(not self.audio_output.isMuted())
        self._update_volume_icon()
        self._show_overlay()

    def _update_volume_icon(self) -> None:
        muted = self.audio_output.isMuted() or self.volume_slider.value() == 0
        icon = QStyle.StandardPixmap.SP_MediaVolumeMuted if muted else QStyle.StandardPixmap.SP_MediaVolume
        self.mute_button.setIcon(self.style().standardIcon(icon))
        self.mute_button.setToolTip("Unmute" if muted else "Mute")

    def _toggle_fullscreen(self) -> None:
        if not self._fullscreen:
            self._fullscreen = True
            self._normal_geometry = self.saveGeometry()
            self.header_widget.hide()
            self.trim_panel.hide()
            self.root_layout.setContentsMargins(0, 0, 0, 0)
            self.root_layout.setSpacing(0)
            self.fullscreen_button.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarNormalButton)
            )
            self.fullscreen_button.setToolTip("Exit fullscreen")
            self.showFullScreen()
        else:
            self._exit_fullscreen()
        self._show_overlay(auto_hide=False)

    def _exit_fullscreen(self) -> None:
        if not self._fullscreen:
            return
        self._fullscreen = False
        self.showNormal()
        if self._normal_geometry is not None:
            self.restoreGeometry(self._normal_geometry)
        self.root_layout.setContentsMargins(18, 16, 18, 18)
        self.root_layout.setSpacing(12)
        self.header_widget.show()
        self.trim_panel.show()
        self.fullscreen_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarMaxButton)
        )
        self.fullscreen_button.setToolTip("Fullscreen")

    def _escape(self) -> None:
        if self._fullscreen:
            self._exit_fullscreen()
        else:
            self.close()

    def _on_trim_selection_changed(self, start: int, end: int) -> None:
        self.start_value.setText(f"IN  {self._precise_clock(start)}")
        self.end_value.setText(f"OUT  {self._precise_clock(end)}")
        self.selection_value.setText(f"SELECTED  {self._precise_clock(end - start)}")
        self.trim_length.setText(f"Keep {_clock(end - start)}")
        self.overlay_seek.setRange(start, max(start + 1, end))

        position = self.player.position() if hasattr(self, "player") else start
        if position < start or position > end:
            if hasattr(self, "player"):
                self.player.pause()
                self.player.setPosition(start)
            position = start
        self.overlay_seek.setValue(position)
        selected_duration = max(0, end - start)
        self.current_time.setText(
            f"{_clock(max(0, min(position, end) - start))} / {_clock(selected_duration)}"
        )
        self._show_overlay()

    def _reset_trim(self) -> None:
        self.trim_timeline.set_selection(0, self.duration_ms)
        self.player.pause()
        self.player.setPosition(0)

    def _show_overlay(self, *, auto_hide: bool = True) -> None:
        if self._closing:
            return
        self.overlay_animation.stop()
        self.player_overlay.show()
        self.overlay_effect.setOpacity(1.0)
        self.overlay_hide_timer.stop()
        if (
            auto_hide
            and not self._overlay_hovered
            and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        ):
            self.overlay_hide_timer.start()

    def _hide_overlay(self) -> None:
        if (
            self._overlay_hovered
            or self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState
            or self._seeking
        ):
            return
        self.overlay_animation.stop()
        self.overlay_animation.setStartValue(self.overlay_effect.opacity())
        self.overlay_animation.setEndValue(0.0)
        self.overlay_animation.start()

    def _on_overlay_animation_finished(self) -> None:
        if self.overlay_effect.opacity() <= 0.01:
            self.player_overlay.hide()

    def eventFilter(self, watched, event) -> bool:
        if watched in (self.video_frame, self.video_widget):
            if event.type() in (QEvent.Type.Enter, QEvent.Type.MouseMove):
                self._show_overlay()
            elif (
                watched is self.video_widget
                and event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
            ):
                self._toggle_playback()
                self._show_overlay()
                return True

        if watched is self.player_overlay:
            if event.type() == QEvent.Type.Enter:
                self._overlay_hovered = True
                self._show_overlay(auto_hide=False)
            elif event.type() == QEvent.Type.Leave:
                self._overlay_hovered = False
                self._show_overlay()
            elif event.type() == QEvent.Type.MouseMove:
                self._show_overlay(auto_hide=False)

        return super().eventFilter(watched, event)

    def _save_trim(self, replace_original: bool) -> None:
        if self._trim_busy:
            return
        start = self.trim_timeline.start_ms / 1000.0
        end = self.trim_timeline.end_ms / 1000.0
        if end - start < 0.25:
            QMessageBox.warning(self, "Trim too short", "Keep at least 0.25 seconds.")
            return
        if replace_original:
            answer = QMessageBox.question(
                self,
                "Replace original clip?",
                "This replaces the original video with the selected range. This cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self._trim_busy = True
        self._trim_replace_original = replace_original
        self._last_trim_bounds = (start, end)
        self.player.pause()
        self.trim_progress.show()
        self.trim_status.setText("Saving trimmed clip…")
        self.save_copy_button.setEnabled(False)
        self.replace_button.setEnabled(False)

        if replace_original:
            # QMediaPlayer keeps an open Windows handle to the current MP4. Clear
            # the source first, then give the Qt/FFmpeg backend a moment to release
            # it before the worker atomically swaps the files.
            self._pending_trim_request = (start, end, True)
            self.player.stop()
            self.player.setSource(QUrl())
            self.trim_status.setText("Releasing video and replacing original…")
            self._trim_release_timer.start(450)
        else:
            self.controller.trim_clip(
                self.clip,
                start,
                end,
                replace_original=False,
            )

    def _dispatch_pending_trim(self) -> None:
        request = self._pending_trim_request
        self._pending_trim_request = None
        if request is None or self._closing:
            return
        start, end, replace_original = request
        self.controller.trim_clip(
            self.clip,
            start,
            end,
            replace_original=replace_original,
        )

    def _on_trim_finished(self, output: Path, replaced: bool) -> None:
        if not self._trim_busy:
            return
        self._trim_release_timer.stop()
        self._pending_trim_request = None
        self._trim_busy = False
        self.trim_progress.hide()
        self.save_copy_button.setEnabled(True)
        self.replace_button.setEnabled(True)
        if replaced:
            self.trim_status.setText("Original clip replaced successfully.")
            start, end = self._last_trim_bounds
            estimated_duration = max(250, int(round((end - start) * 1000)))
            self.duration_ms = estimated_duration
            self.clip.duration_seconds = estimated_duration / 1000.0
            self.trim_timeline.set_duration(estimated_duration)
            self.trim_timeline.set_selection(0, estimated_duration)
            self.player.setSource(QUrl.fromLocalFile(str(output)))
            QTimer.singleShot(180, self._start_selected_playback)
        else:
            self.trim_status.setText(f"Saved {Path(output).name}")
            QMessageBox.information(self, "Trimmed copy saved", f"Saved as:\n{Path(output).name}")
        self._trim_replace_original = False

    def _on_trim_failed(self, message: str) -> None:
        if not self._trim_busy:
            return
        self._trim_release_timer.stop()
        self._pending_trim_request = None
        replacing_original = self._trim_replace_original
        self._trim_replace_original = False
        self._trim_busy = False
        self.trim_progress.hide()
        self.save_copy_button.setEnabled(True)
        self.replace_button.setEnabled(True)
        self.trim_status.setText("Trim failed.")
        if replacing_original and self.clip.path.exists():
            self.player.setSource(QUrl.fromLocalFile(str(self.clip.path)))
            QTimer.singleShot(180, lambda: self.player.setPosition(self.trim_timeline.start_ms))
        QMessageBox.critical(self, "Could not trim clip", message)

    def _on_player_error(self, _error, message: str) -> None:
        if message:
            self.trim_status.setText(f"Player error: {message}")

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        self.overlay_hide_timer.stop()
        self.overlay_animation.stop()
        self._trim_release_timer.stop()
        self._pending_trim_request = None
        self.player.stop()
        self.player.setSource(QUrl())
        if self._fullscreen:
            self._exit_fullscreen()
        super().closeEvent(event)
