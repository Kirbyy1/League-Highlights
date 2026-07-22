from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Callable

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QCursor,
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QStyle,
    QSystemTrayIcon,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.assets import icon_path, logo_path
from app.config import AppConfig
from app.controller import RecorderController
from app.models import ClipInfo, GameHighlights, RecorderState
from app.services.video_recorder import RecorderDiagnostics
from app.services.windows_startup import is_enabled as startup_is_enabled, set_enabled as set_startup_enabled
from app.services.update_manager import UpdateInfo, UpdateManager
from app.release_notes import notes_for_version
from app.ui.styles import APP_STYLE
from app.ui.inline_player import InlineHighlightPlayer
from app.ui.whats_new_dialog import WhatsNewDialog
from app.version import APP_VERSION


RESOLUTION_OPTIONS: dict[str, tuple[int, int]] = {
    "1080p (1920 × 1080)": (1920, 1080),
    "900p (1600 × 900)": (1600, 900),
    "720p (1280 × 720)": (1280, 720),
}
FPS_OPTIONS: dict[str, int] = {"60 FPS": 60, "30 FPS": 30}
AUDIO_BITRATE_OPTIONS: dict[str, int] = {"96 kbps": 96, "128 kbps": 128, "160 kbps": 160, "192 kbps": 192}

QUALITY_OPTIONS: dict[str, tuple[int, int]] = {
    "High quality — largest files": (20, 192),
    "Balanced — recommended": (23, 160),
    "Smaller files": (27, 128),
    "Minimum size": (30, 96),
}


def _quality_option_for_value(value: int) -> str:
    if value <= 20:
        return "High quality — largest files"
    if value <= 24:
        return "Balanced — recommended"
    if value <= 28:
        return "Smaller files"
    return "Minimum size"


def _window_control_icon(kind: str) -> QIcon:
    """Create crisp title-bar icons without depending on a symbol font."""

    pixmap = QPixmap(18, 18)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(QColor("#C7D0DA"), 1.35))

    if kind == "minimize":
        painter.drawLine(4, 10, 14, 10)
    elif kind == "maximize":
        painter.drawRect(4, 4, 10, 10)
    elif kind == "restore":
        painter.drawRect(6, 4, 8, 8)
        painter.drawRect(4, 6, 8, 8)
    elif kind == "close":
        painter.drawLine(5, 5, 13, 13)
        painter.drawLine(13, 5, 5, 13)

    painter.end()
    return QIcon(pixmap)


def _app_icon(kind: str, color: str = "#D7DFE8") -> QIcon:
    """Small monochrome application icons that stay consistent on every PC."""

    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color), 1.45)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if kind == "play":
        path = QPainterPath()
        path.moveTo(7, 5)
        path.lineTo(15, 10)
        path.lineTo(7, 15)
        path.closeSubpath()
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)
    elif kind == "folder":
        path = QPainterPath()
        path.moveTo(3.5, 6.5)
        path.lineTo(3.5, 15.5)
        path.lineTo(16.5, 15.5)
        path.lineTo(16.5, 7.5)
        path.lineTo(9.5, 7.5)
        path.lineTo(7.5, 5)
        path.lineTo(3.5, 5)
        path.closeSubpath()
        painter.drawPath(path)
    elif kind == "trash":
        painter.drawLine(5, 6, 15, 6)
        painter.drawLine(8, 4, 12, 4)
        painter.drawRoundedRect(QRectF(6.5, 7, 7, 9), 1.2, 1.2)
        painter.drawLine(9, 9, 9, 14)
        painter.drawLine(11, 9, 11, 14)
    elif kind == "refresh":
        painter.drawArc(QRectF(4, 4, 12, 12), 35 * 16, 285 * 16)
        path = QPainterPath()
        path.moveTo(13.7, 3.8)
        path.lineTo(16.4, 4.3)
        path.lineTo(15.0, 6.7)
        path.closeSubpath()
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)
    elif kind == "good":
        painter.drawLine(4.5, 10.5, 8.2, 14)
        painter.drawLine(8.2, 14, 15.5, 5.8)
    elif kind == "bad":
        painter.drawLine(5.5, 5.5, 14.5, 14.5)
        painter.drawLine(14.5, 5.5, 5.5, 14.5)
    elif kind == "back":
        painter.drawLine(14.5, 4.5, 8.5, 10)
        painter.drawLine(8.5, 10, 14.5, 15.5)
        painter.drawLine(8.8, 10, 17, 10)
    elif kind == "menu":
        painter.drawLine(4.5, 6.0, 15.5, 6.0)
        painter.drawLine(4.5, 10.0, 15.5, 10.0)
        painter.drawLine(4.5, 14.0, 15.5, 14.0)
    elif kind == "highlights":
        painter.drawRoundedRect(QRectF(4.0, 4.5, 12.0, 11.0), 2.2, 2.2)
        painter.drawLine(7.2, 4.8, 7.2, 15.2)
        path = QPainterPath()
        path.moveTo(9.2, 7.0)
        path.lineTo(13.2, 10.0)
        path.lineTo(9.2, 13.0)
        path.closeSubpath()
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)
    elif kind == "settings":
        painter.drawLine(4.0, 6.0, 16.0, 6.0)
        painter.drawLine(4.0, 10.0, 16.0, 10.0)
        painter.drawLine(4.0, 14.0, 16.0, 14.0)
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(6.0, 4.0, 4.0, 4.0))
        painter.drawEllipse(QRectF(10.5, 8.0, 4.0, 4.0))
        painter.drawEllipse(QRectF(7.5, 12.0, 4.0, 4.0))

    painter.end()
    return QIcon(pixmap)


class TitleBar(QFrame):
    """Polished frameless title bar with a persistent application menu."""

    backRequested = Signal()
    settingsRequested = Signal()
    whatsNewRequested = Signal()

    def __init__(self, window: QMainWindow) -> None:
        super().__init__()
        self.window = window
        self.drag_position: QPoint | None = None
        self.setObjectName("TitleBar")
        self.setFixedHeight(47)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 0, 0)
        layout.setSpacing(10)

        badge = QFrame()
        badge.setObjectName("TitleBrandBadge")
        badge.setFixedSize(32, 32)
        badge_layout = QVBoxLayout(badge)
        badge_layout.setContentsMargins(2, 2, 2, 2)
        mark = QLabel()
        mark.setObjectName("BrandMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        logo = QPixmap(str(logo_path()))
        if not logo.isNull():
            mark.setPixmap(
                logo.scaled(
                    28,
                    28,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        badge_layout.addWidget(mark)

        self.menu_button = QToolButton()
        self.menu_button.setObjectName("MainMenuButton")
        self.menu_button.setIcon(_app_icon("menu"))
        self.menu_button.setIconSize(QSize(18, 18))
        self.menu_button.setToolTip("Main menu")

        # Show the menu manually instead of attaching it as a QToolButton menu.
        # This removes Qt's native menu-button subcontrol/pressed overlay.
        self.main_menu = QMenu(self)
        self.main_menu.setObjectName("MainMenu")
        settings_action = QAction(_app_icon("settings"), "Settings", self.main_menu)
        settings_action.triggered.connect(self.settingsRequested.emit)
        self.main_menu.addAction(settings_action)
        whats_new_action = QAction("What's new", self.main_menu)
        whats_new_action.triggered.connect(self.whatsNewRequested.emit)
        self.main_menu.addAction(whats_new_action)
        self.menu_button.clicked.connect(self._show_main_menu)

        self.back_button = QToolButton()
        self.back_button.setObjectName("TitleBackButton")
        self.back_button.setIcon(_app_icon("back"))
        self.back_button.setIconSize(QSize(18, 18))
        self.back_button.setToolTip("Back to games")
        self.back_button.clicked.connect(self.backRequested.emit)
        self.back_button.hide()

        copy = QHBoxLayout()
        copy.setContentsMargins(0, 0, 0, 0)
        copy.setSpacing(10)
        self.title_label = QLabel("")
        self.title_label.setObjectName("WindowTitle")
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.context_label = QLabel("")
        self.context_label.setObjectName("WindowContext")
        self.context_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        copy.addWidget(self.title_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        copy.addWidget(self.context_label, alignment=Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(badge)
        layout.addWidget(self.menu_button)
        layout.addWidget(self.back_button)
        layout.addLayout(copy)
        layout.addStretch()

        self.state_dot = QLabel()
        self.state_dot.setObjectName("TitleStateDot")
        self.state_text = QLabel("Idle")
        self.state_text.setObjectName("TitleStateText")
        self.state_dot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.state_text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.state_dot)
        layout.addWidget(self.state_text)
        layout.addSpacing(12)

        self.minimize_button = self._window_button(
            "minimize", "Minimize", self.window.showMinimized
        )
        self.maximize_button = self._window_button(
            "maximize", "Maximize", self._toggle_maximize
        )
        self.close_button = self._window_button(
            "close", "Close", self.window.close, close=True
        )
        layout.addWidget(self.minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(self.close_button)

    def _show_main_menu(self) -> None:
        position = self.menu_button.mapToGlobal(QPoint(0, self.menu_button.height() + 4))
        self.main_menu.popup(position)

    def show_default_context(self) -> None:
        self.back_button.hide()
        self.title_label.clear()
        self.context_label.clear()

    def show_game_context(self, game: GameHighlights) -> None:
        title_parts = [game.title_text]
        if game.normalized_result:
            title_parts.append(game.normalized_result)
        self.title_label.setText(" — ".join(title_parts))
        details = [game.kda_text, game.match_duration_text]
        self.context_label.setText("  •  ".join(part for part in details if part))
        self.back_button.show()

    def _window_button(
        self,
        icon_kind: str,
        tooltip: str,
        callback: Callable[[], None],
        close: bool = False,
    ) -> QToolButton:
        button = QToolButton()
        button.setObjectName("TitleCloseButton" if close else "TitleButton")
        button.setIcon(_window_control_icon(icon_kind))
        button.setIconSize(QSize(13, 13))
        button.setToolTip(tooltip)
        button.clicked.connect(callback)
        return button

    def sync_maximize_icon(self) -> None:
        if self.window.isMaximized():
            self.maximize_button.setIcon(_window_control_icon("restore"))
            self.maximize_button.setToolTip("Restore")
        else:
            self.maximize_button.setIcon(_window_control_icon("maximize"))
            self.maximize_button.setToolTip("Maximize")

    def _toggle_maximize(self) -> None:
        if self.window.isMaximized():
            self.window.showNormal()
        else:
            self.window.showMaximized()
        self.sync_maximize_icon()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximize()
            event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self.window.isMaximized():
            self.drag_position = (
                event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if (
            self.drag_position is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and not self.window.isMaximized()
        ):
            self.window.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self.drag_position = None
        event.accept()

class RoundedThumbnail(QWidget):
    def __init__(
        self,
        image_path,
        duration_text: str,
        width: int = 184,
        height: int = 104,
    ) -> None:
        super().__init__()
        self.setFixedSize(width, height)
        self.duration_text = duration_text
        self.pixmap = QPixmap(str(image_path)) if image_path and image_path.exists() else QPixmap()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, 9, 9)
        painter.setClipPath(path)
        painter.fillRect(self.rect(), QColor("#151D26"))

        if not self.pixmap.isNull():
            scaled = self.pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            painter.setPen(QColor("#73808E"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No preview")

        badge_width = 48
        badge_height = 23
        badge = QRectF(
            self.width() - badge_width - 8,
            self.height() - badge_height - 8,
            badge_width,
            badge_height,
        )
        painter.setClipping(False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(5, 9, 14, 220))
        painter.drawRoundedRect(badge, 6, 6)
        painter.setPen(QColor("#F4F7FA"))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, self.duration_text)

class HotkeyCaptureButton(QPushButton):
    hotkey_captured = Signal(int, object, str)
    capture_started = Signal()
    capture_finished = Signal()

    _MODIFIER_KEYS = {
        int(Qt.Key.Key_Control),
        int(Qt.Key.Key_Shift),
        int(Qt.Key.Key_Alt),
        int(Qt.Key.Key_Meta),
        int(Qt.Key.Key_AltGr),
    }

    _FALLBACK_VKS = {
        int(Qt.Key.Key_Backspace): 0x08,
        int(Qt.Key.Key_Tab): 0x09,
        int(Qt.Key.Key_Return): 0x0D,
        int(Qt.Key.Key_Enter): 0x0D,
        int(Qt.Key.Key_Pause): 0x13,
        int(Qt.Key.Key_CapsLock): 0x14,
        int(Qt.Key.Key_Escape): 0x1B,
        int(Qt.Key.Key_Space): 0x20,
        int(Qt.Key.Key_PageUp): 0x21,
        int(Qt.Key.Key_PageDown): 0x22,
        int(Qt.Key.Key_End): 0x23,
        int(Qt.Key.Key_Home): 0x24,
        int(Qt.Key.Key_Left): 0x25,
        int(Qt.Key.Key_Up): 0x26,
        int(Qt.Key.Key_Right): 0x27,
        int(Qt.Key.Key_Down): 0x28,
        int(Qt.Key.Key_Insert): 0x2D,
        int(Qt.Key.Key_Delete): 0x2E,
        int(Qt.Key.Key_NumLock): 0x90,
        int(Qt.Key.Key_ScrollLock): 0x91,
    }

    def __init__(self, display_name: str) -> None:
        super().__init__(display_name)
        self.setObjectName("HotkeyButton")
        self.setMinimumWidth(190)
        self._display_name = display_name
        self._capturing = False
        self.clicked.connect(self.begin_capture)

    def begin_capture(self) -> None:
        if self._capturing:
            return
        self._capturing = True
        self.setProperty("capturing", True)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setText("Press a shortcut…")
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.grabKeyboard()
        self.capture_started.emit()

    def cancel_capture(self) -> None:
        if not self._capturing:
            return
        self._capturing = False
        self.releaseKeyboard()
        self.setText(self._display_name)
        self.setProperty("capturing", False)
        self.style().unpolish(self)
        self.style().polish(self)
        self.capture_finished.emit()

    def set_hotkey_text(self, display_name: str) -> None:
        self._display_name = display_name
        if not self._capturing:
            self.setText(display_name)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._capturing:
            super().keyPressEvent(event)
            return
        if event.isAutoRepeat():
            return

        key = int(event.key())
        if key == int(Qt.Key.Key_Escape):
            self.cancel_capture()
            return
        if key in self._MODIFIER_KEYS:
            return

        virtual_key = int(event.nativeVirtualKey()) or self._fallback_virtual_key(key)
        if virtual_key <= 0:
            self.setText("Unsupported key — try another")
            return

        modifiers: list[str] = []
        qt_modifiers = event.modifiers()
        if qt_modifiers & Qt.KeyboardModifier.ControlModifier:
            modifiers.append("ctrl")
        if qt_modifiers & Qt.KeyboardModifier.AltModifier:
            modifiers.append("alt")
        if qt_modifiers & Qt.KeyboardModifier.ShiftModifier:
            modifiers.append("shift")
        if qt_modifiers & Qt.KeyboardModifier.MetaModifier:
            modifiers.append("win")

        key_name = QKeySequence(key).toString(QKeySequence.SequenceFormat.NativeText)
        if not key_name:
            key_name = event.text().upper() or f"VK {virtual_key}"
        modifier_names = {
            "ctrl": "Ctrl",
            "alt": "Alt",
            "shift": "Shift",
            "win": "Win",
        }
        display = "+".join([*(modifier_names[name] for name in modifiers), key_name])

        self._display_name = display
        self._capturing = False
        self.releaseKeyboard()
        self.setText(display)
        self.setProperty("capturing", False)
        self.style().unpolish(self)
        self.style().polish(self)
        self.hotkey_captured.emit(virtual_key, modifiers, display)
        self.capture_finished.emit()

    @classmethod
    def _fallback_virtual_key(cls, key: int) -> int:
        if ord("A") <= key <= ord("Z") or ord("0") <= key <= ord("9"):
            return key
        f1 = int(Qt.Key.Key_F1)
        f24 = int(Qt.Key.Key_F24)
        if f1 <= key <= f24:
            return 0x70 + (key - f1)
        return cls._FALLBACK_VKS.get(key, 0)


class ClipCard(QFrame):
    def __init__(self, clip: ClipInfo, controller: RecorderController, refresh_callback, play_callback) -> None:
        super().__init__()
        self.clip = clip
        self.controller = controller
        self.refresh_callback = refresh_callback
        self.play_callback = play_callback
        self.setObjectName("ClipCard")
        self.setMinimumHeight(128)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 14, 12)
        layout.setSpacing(14)
        layout.addWidget(RoundedThumbnail(clip.thumbnail_path, clip.duration_text, 174, 98))

        info = QVBoxLayout()
        info.setSpacing(5)
        title = QLabel(clip.label.replace("_", " ").title())
        title.setObjectName("ClipLabel")
        info.addWidget(title)

        context_parts = [part for part in (clip.champion_name, clip.player_name) if part]
        if context_parts:
            context_text = " • ".join(context_parts)
        else:
            context_text = clip.path.name
        context = QLabel(context_text)
        context.setObjectName("ClipFileName")
        context.setToolTip(str(clip.path))
        info.addWidget(context)

        reason_text = ""
        if clip.score_reasons:
            reason_text = " • ".join(clip.score_reasons[:3])
        elif clip.victim_names:
            reason_text = "Victims: " + ", ".join(clip.victim_names[:3])
        if reason_text:
            reasons = QLabel(reason_text)
            reasons.setObjectName("ClipReasons")
            reasons.setToolTip("\n".join(clip.score_reasons) if clip.score_reasons else reason_text)
            reasons.setWordWrap(True)
            info.addWidget(reasons)

        info.addStretch()
        metadata = QHBoxLayout()
        metadata.setSpacing(7)
        for text_value in (clip.duration_text, clip.file_size_text, clip.audio_summary_text):
            chip = QLabel(text_value)
            chip.setObjectName("MetaChip")
            metadata.addWidget(chip)
        if clip.highlight_score:
            score_chip = QLabel(f"Score {clip.highlight_score}")
            score_chip.setObjectName("ScoreChip")
            metadata.addWidget(score_chip)
        metadata.addStretch()
        info.addLayout(metadata)
        layout.addLayout(info, 1)

        side = QVBoxLayout()
        side.setContentsMargins(0, 3, 0, 3)
        side.setSpacing(5)
        date = QLabel(clip.date_text)
        date.setObjectName("ClipDate")
        time_label = QLabel(clip.time_text)
        time_label.setObjectName("CardMuted")
        side.addWidget(date, alignment=Qt.AlignmentFlag.AlignRight)
        side.addWidget(time_label, alignment=Qt.AlignmentFlag.AlignRight)
        side.addStretch()

        rating_row = QHBoxLayout()
        rating_row.setSpacing(5)
        good = self._action_button(
            "good", "Good highlight — improves future scoring", lambda: self._rate("good")
        )
        bad = self._action_button(
            "bad", "Not impressive — improves future scoring", lambda: self._rate("bad")
        )
        good.setObjectName("RatingButton")
        bad.setObjectName("RatingButton")
        good.setProperty("active", clip.rating == "good")
        bad.setProperty("active", clip.rating == "bad")
        rating_row.addWidget(good)
        rating_row.addWidget(bad)
        side.addLayout(rating_row)
        layout.addLayout(side)

        play = self._action_button("play", "Play clip", self._play, primary=True)
        folder = self._action_button("folder", "Show in folder", self._open_folder)
        delete = self._action_button("trash", "Delete clip", self._delete)
        layout.addWidget(play)
        layout.addWidget(folder)
        layout.addWidget(delete)

    def _action_button(
        self,
        icon_kind: str,
        tooltip: str,
        callback,
        primary: bool = False,
    ) -> QToolButton:
        button = QToolButton()
        button.setObjectName("CardPlay" if primary else "CardAction")
        icon_color = "#07110B" if primary else "#CAD3DD"
        button.setIcon(_app_icon(icon_kind, icon_color))
        button.setIconSize(QSize(18 if primary else 16, 18 if primary else 16))
        button.setToolTip(tooltip)
        button.clicked.connect(callback)
        return button

    def _rate(self, rating: str) -> None:
        new_rating = "" if self.clip.rating == rating else rating
        self.controller.rate_clip(self.clip, new_rating)
        self.refresh_callback()

    def _play(self) -> None:
        self.play_callback(self.clip)

    def _open_folder(self) -> None:
        if os.name == "nt":
            os.system(f'explorer /select,"{self.clip.path}"')
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.clip.path.parent)))

    def _delete(self) -> None:
        result = QMessageBox.question(
            self,
            "Delete highlight?",
            f"This permanently deletes {self.clip.path.name}.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            self.controller.delete_clip(self.clip)
            self.refresh_callback()


class GameCard(QFrame):
    clicked = Signal(object)

    def __init__(self, game: GameHighlights) -> None:
        super().__init__()
        self.game = game
        self.setObjectName("GameCardCompact")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(102)
        self.setToolTip("Open game highlights")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 10, 15, 10)
        layout.setSpacing(14)

        # Keep the existing thumbnail exactly as-is for now. The card cleanup is
        # intentionally independent from future thumbnail selection work.
        thumbnail = RoundedThumbnail(
            game.thumbnail_path,
            game.total_duration_text,
            196,
            96,
        )
        thumbnail.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(thumbnail)

        info = QVBoxLayout()
        info.setSpacing(2)
        info.setContentsMargins(0, 8, 0, 8)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = QLabel(game.title_text)
        title.setObjectName("GameTitleCompact")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        title_row.addWidget(title)

        # Only show a result when it is meaningful. Placeholder values such as
        # "Unknown" no longer create a badge in the library.
        normalized_result = game.normalized_result
        if normalized_result in {"Victory", "Defeat"}:
            result_chip = QLabel(normalized_result)
            result_chip.setObjectName(
                "VictoryChip" if normalized_result == "Victory" else "DefeatChip"
            )
            result_chip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            title_row.addWidget(result_chip)
        title_row.addStretch()
        info.addLayout(title_row)

        detail_parts = [part for part in (game.game_mode, f"{game.date_text} at {game.time_text}") if part]
        if game.clip_count == 1:
            detail_parts.append(f"1 highlight • {game.total_duration_text}")
        else:
            detail_parts.append(f"{game.clip_count} highlights • {game.total_duration_text}")
        self.setToolTip(" • ".join(detail_parts))

        info.addStretch()
        layout.addLayout(info, 1)

        chevron = QLabel("›")
        chevron.setObjectName("GameCardChevron")
        chevron.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(chevron, alignment=Qt.AlignmentFlag.AlignVCenter)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.game)
            event.accept()
            return
        super().mouseReleaseEvent(event)

class ClipToast(QFrame):
    """Small non-focus-stealing popup shown above the game/taskbar."""

    def __init__(self, title: str, message: str, error: bool = False) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.ToolTip
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(360)

        root = QFrame(self)
        root.setObjectName("ClipToastRoot")
        border = "#B93E46" if error else "#35C97B"
        root.setStyleSheet(
            f"QFrame#ClipToastRoot {{ background:#101821; border:1px solid {border}; "
            "border-radius:12px; }}"
        )
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(5)

        title_label = QLabel(title)
        title_label.setStyleSheet(
            f"color:{'#FF6672' if error else '#55E891'}; font-size:14px; font-weight:700;"
        )
        message_label = QLabel(message)
        message_label.setWordWrap(True)
        message_label.setStyleSheet("color:#D8E0E8; font-size:12px;")
        layout.addWidget(title_label)
        layout.addWidget(message_label)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)
        self.adjustSize()
        QTimer.singleShot(4200 if error else 3200, self.close)

    def show_near_active_screen(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.adjustSize()
            self.move(area.right() - self.width() - 24, area.bottom() - self.height() - 24)
        self.show()
        self.raise_()


class MainWindow(QMainWindow):
    def __init__(
        self,
        config: AppConfig,
        controller: RecorderController,
        update_manager: UpdateManager | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.controller = controller
        self.setWindowTitle("League Highlights")
        self.setWindowIcon(QIcon(str(icon_path())))
        self.resize(1360, 830)
        self.setMinimumSize(1000, 650)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setStyleSheet(APP_STYLE)
        self._force_exit = False
        self._tray_notice_shown = False
        self.update_manager = update_manager
        self._restart_after_update = False
        self._update_launch_attempted = False
        self._update_ready_notified = False
        self._whats_new_scheduled = False
        self._whats_new_dialog: WhatsNewDialog | None = None
        self._create_system_tray()

        root = QWidget()
        root.setObjectName("Root")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.title_bar = TitleBar(self)
        self.title_bar.backRequested.connect(self._back_to_games)
        self.title_bar.settingsRequested.connect(lambda: self._show_page(1))
        self.title_bar.whatsNewRequested.connect(lambda: self._show_whats_new(force=True))
        root_layout.addWidget(self.title_bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.sidebar = self._build_sidebar()
        body.addWidget(self.sidebar)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_highlights_page())
        self.pages.addWidget(self._build_settings_page())
        body.addWidget(self.pages, 1)
        root_layout.addLayout(body, 1)
        self.setCentralWidget(root)

        self.controller.state_changed.connect(self._on_state_changed)
        self.controller.recording_time_changed.connect(self._update_recording_time)
        self.controller.clip_requested.connect(self._on_clip_requested)
        self.controller.clip_saved.connect(self._on_clip_saved)
        self.controller.error_occurred.connect(self._show_error)
        self.controller.library_changed.connect(self.refresh_clips)
        self.controller.hotkey_changed.connect(self._on_hotkey_changed)
        self.controller.event_status_changed.connect(self._on_event_status_changed)
        self.controller.diagnostics_changed.connect(self._on_diagnostics_changed)
        self._toast: ClipToast | None = None
        self.selected_match_id: str | None = None
        self.refresh_clips()
        self._on_state_changed(self.controller.state, self.controller.detail)
        self._on_event_status_changed(
            self.controller.event_status_text,
            self.controller.event_status_connected,
        )
        self._sync_tray_state()
        self._bind_update_manager()

    def _create_system_tray(self) -> None:
        self.tray_icon = QSystemTrayIcon(self)
        branded_icon = QIcon(str(icon_path()))
        self.tray_icon.setIcon(branded_icon if not branded_icon.isNull() else _app_icon("play", "#55D985"))
        self.tray_icon.setToolTip("League Highlights — waiting for League")

        menu = QMenu(self)
        self.tray_status_action = QAction("Waiting for League", self)
        self.tray_status_action.setEnabled(False)
        menu.addAction(self.tray_status_action)
        menu.addSeparator()

        open_action = QAction("Open League Highlights", self)
        open_action.triggered.connect(self._restore_from_tray)
        menu.addAction(open_action)

        self.tray_save_action = QAction(f"Save last {self.config.buffer_seconds}s", self)
        self.tray_save_action.setEnabled(False)
        self.tray_save_action.triggered.connect(lambda: self.controller.save_clip("MANUAL CLIP"))
        menu.addAction(self.tray_save_action)

        self.tray_capture_action = QAction("Start capture", self)
        self.tray_capture_action.triggered.connect(self.controller.toggle_recording)
        menu.addAction(self.tray_capture_action)

        open_folder_action = QAction("Open clips folder", self)
        open_folder_action.triggered.connect(self._open_clip_folder)
        menu.addAction(open_folder_action)
        menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self._exit_application)
        menu.addAction(exit_action)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._restore_from_tray()

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _exit_application(self) -> None:
        self._force_exit = True
        self.controller.shutdown()
        self.tray_icon.hide()
        self._launch_pending_update(self._restart_after_update)
        QGuiApplication.quit()

    def show_startup_notification(self) -> None:
        if self.tray_icon.isVisible():
            self.tray_icon.showMessage(
                "League Highlights is running",
                "It will start recording automatically when a League match appears.",
                QSystemTrayIcon.MessageIcon.Information,
                3500,
            )

    def _sync_tray_state(self) -> None:
        if not hasattr(self, "tray_icon"):
            return
        state = self.controller.state
        detail = self.controller.detail
        self.tray_status_action.setText(f"{state.value.title()} — {detail}")
        self.tray_icon.setToolTip(f"League Highlights — {state.value.title()}")
        self.tray_save_action.setEnabled(self.controller.recording and state != RecorderState.SAVING)
        self.tray_save_action.setText(f"Save last {self.config.buffer_seconds}s")
        self.tray_capture_action.setText("Stop capture" if self.controller.recording else "Start capture")

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(164)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(10, 14, 10, 12)
        layout.setSpacing(4)

        self.highlights_nav = QPushButton("Highlights")
        self.highlights_nav.setObjectName("NavButton")
        self.highlights_nav.setIcon(_app_icon("highlights"))
        self.highlights_nav.setIconSize(QSize(18, 18))
        self.highlights_nav.setProperty("active", True)
        self.highlights_nav.setToolTip("Highlights")
        self.highlights_nav.clicked.connect(lambda: self._show_page(0))

        layout.addWidget(self.highlights_nav)
        layout.addStretch()

        # Recorder controls remain available from the tray and global hotkey. These
        # hidden compatibility widgets keep state updates isolated from navigation.
        self.status_dot = QLabel(sidebar)
        self.status_title = QLabel(sidebar)
        self.status_time = QLabel(sidebar)
        self.status_detail = QLabel(sidebar)
        self.status_profile = QLabel(sidebar)
        self.status_diagnostics = QLabel(sidebar)
        self.status_progress = QProgressBar(sidebar)
        self.save_clip_button = QPushButton(sidebar)
        self.record_button = QPushButton(sidebar)
        for widget in (
            self.status_dot, self.status_title, self.status_time, self.status_detail,
            self.status_profile, self.status_diagnostics, self.status_progress,
            self.save_clip_button, self.record_button,
        ):
            widget.hide()
        return sidebar

    def _set_sidebar_compact(self, compact: bool) -> None:
        self.sidebar.setFixedWidth(58 if compact else 164)
        self.highlights_nav.setText("" if compact else "Highlights")
        self.highlights_nav.setProperty("compact", compact)
        self.highlights_nav.style().unpolish(self.highlights_nav)
        self.highlights_nav.style().polish(self.highlights_nav)

    def _build_highlights_page(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("ContentPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        self.highlights_header = QWidget()
        header = QHBoxLayout(self.highlights_header)
        header.setContentsMargins(0, 0, 0, 0)
        self.page_title = QLabel("Highlights")
        self.page_title.setObjectName("PageTitle")
        self.highlights_subtitle = QLabel("")
        self.highlights_subtitle.hide()
        header.addWidget(self.page_title)
        header.addStretch()
        refresh = QToolButton()
        refresh.setObjectName("HeaderIconButton")
        refresh.setIcon(_app_icon("refresh"))
        refresh.setIconSize(QSize(19, 19))
        refresh.setToolTip("Refresh highlights")
        refresh.clicked.connect(self.refresh_clips)
        open_folder = QToolButton()
        open_folder.setObjectName("HeaderIconButton")
        open_folder.setIcon(_app_icon("folder"))
        open_folder.setIconSize(QSize(19, 19))
        open_folder.setToolTip("Open clips folder")
        open_folder.clicked.connect(self._open_clip_folder)
        header.addWidget(refresh)
        header.addWidget(open_folder)
        layout.addWidget(self.highlights_header)

        self.highlights_stack = QStackedWidget()

        self.games_view = QWidget()
        games_layout = QVBoxLayout(self.games_view)
        games_layout.setContentsMargins(0, 0, 0, 0)
        self.games_scroll = QScrollArea()
        self.games_scroll.setWidgetResizable(True)
        self.games_container = QWidget()
        self.games_layout = QVBoxLayout(self.games_container)
        self.games_layout.setContentsMargins(0, 0, 4, 0)
        self.games_layout.setSpacing(11)
        self.games_scroll.setWidget(self.games_container)
        games_layout.addWidget(self.games_scroll)
        self.highlights_stack.addWidget(self.games_view)

        self.game_detail_view = QWidget()
        detail_root = QVBoxLayout(self.game_detail_view)
        detail_root.setContentsMargins(0, 0, 0, 0)
        detail_root.setSpacing(8)

        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_container = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_container)
        self.detail_layout.setContentsMargins(0, 0, 2, 0)
        self.detail_layout.setSpacing(8)
        self.detail_scroll.setWidget(self.detail_container)
        detail_root.addWidget(self.detail_scroll, 1)
        self.highlights_stack.addWidget(self.game_detail_view)

        layout.addWidget(self.highlights_stack, 1)
        return panel

    def _build_settings_page(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("ContentPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(28, 25, 28, 24)
        layout.setSpacing(16)

        title = QLabel("Settings")
        title.setObjectName("PageTitle")
        subtitle = QLabel("Configure recording, audio, automatic highlights, and the app.")
        subtitle.setObjectName("PageSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        tabs_frame = QFrame()
        tabs_frame.setObjectName("SettingsTabs")
        tabs_layout = QHBoxLayout(tabs_frame)
        tabs_layout.setContentsMargins(4, 4, 4, 4)
        tabs_layout.setSpacing(4)
        self.settings_tab_buttons: list[QPushButton] = []
        for index, label in enumerate(("Recording", "Audio", "Smart highlights", "Storage & app")):
            button = QPushButton(label)
            button.setObjectName("SettingsTab")
            button.setProperty("active", index == 0)
            button.clicked.connect(lambda checked=False, page=index: self._show_settings_section(page))
            self.settings_tab_buttons.append(button)
            tabs_layout.addWidget(button)
        tabs_layout.addStretch()
        layout.addWidget(tabs_frame)

        self.settings_pages = QStackedWidget()
        self.settings_pages.addWidget(self._build_recording_settings())
        self.settings_pages.addWidget(self._build_audio_settings())
        self.settings_pages.addWidget(self._build_smart_settings())
        self.settings_pages.addWidget(self._build_app_settings())
        layout.addWidget(self.settings_pages, 1)
        return panel

    def _settings_scroll_page(self) -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(14)
        scroll.setWidget(content)
        return scroll, layout

    def _build_recording_settings(self) -> QWidget:
        scroll, layout = self._settings_scroll_page()

        profile = QFrame()
        profile.setObjectName("InfoCard")
        profile_layout = QVBoxLayout(profile)
        profile_layout.setContentsMargins(16, 14, 16, 14)
        profile_layout.setSpacing(5)
        current_title = QLabel("CURRENT RECORDING PROFILE")
        current_title.setObjectName("SectionEyebrow")
        self.capture_profile_summary = QLabel()
        self.capture_profile_summary.setObjectName("SectionTitle")
        self._update_capture_profile_summary()
        current_help = QLabel(
            "Applying resolution, FPS, quality, or buffer changes restarts capture and warms the buffer again."
        )
        current_help.setObjectName("Muted")
        current_help.setWordWrap(True)
        profile_layout.addWidget(current_title)
        profile_layout.addWidget(self.capture_profile_summary)
        profile_layout.addWidget(current_help)
        layout.addWidget(profile)

        capture = QFrame()
        capture.setObjectName("SettingsSection")
        capture_layout = QVBoxLayout(capture)
        capture_layout.setContentsMargins(20, 18, 20, 18)
        capture_layout.setSpacing(14)
        capture_title = QLabel("Recording")
        capture_title.setObjectName("SettingsTitle")
        capture_layout.addWidget(capture_title)

        auto_start = QCheckBox("Start recording automatically when League appears")
        auto_start.setChecked(self.config.auto_start)
        auto_start.toggled.connect(self._set_auto_start)
        capture_layout.addWidget(auto_start)
        capture_layout.addWidget(self._divider())

        buffer_row = QHBoxLayout()
        buffer_copy = QVBoxLayout()
        buffer_name = QLabel("Rolling buffer")
        buffer_name.setObjectName("SettingName")
        buffer_help = QLabel("Amount of recent gameplay available to each manual clip.")
        buffer_help.setObjectName("CardMuted")
        buffer_copy.addWidget(buffer_name)
        buffer_copy.addWidget(buffer_help)
        self.buffer_combo = QComboBox()
        self.buffer_combo.addItems(["30 seconds", "45 seconds", "60 seconds"])
        self.buffer_combo.setCurrentText(f"{self.config.buffer_seconds} seconds")
        self.buffer_combo.currentTextChanged.connect(self._set_buffer_seconds)
        buffer_row.addLayout(buffer_copy, 1)
        buffer_row.addWidget(self.buffer_combo)
        capture_layout.addLayout(buffer_row)
        capture_layout.addWidget(self._divider())

        profile_grid = QGridLayout()
        profile_grid.setHorizontalSpacing(22)
        profile_grid.setVerticalSpacing(15)

        resolution_copy = QVBoxLayout()
        resolution_name = QLabel("Resolution")
        resolution_name.setObjectName("SettingName")
        resolution_help = QLabel("1080p gives the clearest UI text; lower settings reduce load and size.")
        resolution_help.setObjectName("CardMuted")
        resolution_copy.addWidget(resolution_name)
        resolution_copy.addWidget(resolution_help)
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(list(RESOLUTION_OPTIONS))
        current_resolution = next(
            (name for name, size in RESOLUTION_OPTIONS.items() if size == (self.config.width, self.config.height)),
            "1080p (1920 × 1080)",
        )
        self.resolution_combo.setCurrentText(current_resolution)
        profile_grid.addLayout(resolution_copy, 0, 0)
        profile_grid.addWidget(self.resolution_combo, 0, 1)

        fps_copy = QVBoxLayout()
        fps_name = QLabel("Frame rate")
        fps_name.setObjectName("SettingName")
        fps_help = QLabel("60 FPS is smoother; 30 FPS uses less storage and processing.")
        fps_help.setObjectName("CardMuted")
        fps_copy.addWidget(fps_name)
        fps_copy.addWidget(fps_help)
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(list(FPS_OPTIONS))
        self.fps_combo.setCurrentText(f"{self.config.fps} FPS")
        profile_grid.addLayout(fps_copy, 1, 0)
        profile_grid.addWidget(self.fps_combo, 1, 1)

        quality_copy = QVBoxLayout()
        quality_name = QLabel("Recording quality")
        quality_name.setObjectName("SettingName")
        quality_help = QLabel("Saved clips keep this original profile without a second video encode.")
        quality_help.setObjectName("CardMuted")
        quality_copy.addWidget(quality_name)
        quality_copy.addWidget(quality_help)
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(list(QUALITY_OPTIONS))
        self.quality_combo.setCurrentText(_quality_option_for_value(self.config.quality))
        profile_grid.addLayout(quality_copy, 2, 0)
        profile_grid.addWidget(self.quality_combo, 2, 1)
        profile_grid.setColumnStretch(0, 1)
        capture_layout.addLayout(profile_grid)

        apply_row = QHBoxLayout()
        quality_note = QLabel("New settings apply to future clips only.")
        quality_note.setObjectName("CardMuted")
        apply_capture = QPushButton("Apply recording settings")
        apply_capture.setObjectName("PrimaryButton")
        apply_capture.clicked.connect(self._apply_capture_settings)
        apply_row.addWidget(quality_note, 1)
        apply_row.addWidget(apply_capture)
        capture_layout.addLayout(apply_row)
        layout.addWidget(capture)
        layout.addStretch()
        return scroll

    def _build_audio_settings(self) -> QWidget:
        scroll, layout = self._settings_scroll_page()
        audio = QFrame()
        audio.setObjectName("SettingsSection")
        audio_layout = QVBoxLayout(audio)
        audio_layout.setContentsMargins(20, 18, 20, 18)
        audio_layout.setSpacing(14)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        audio_title = QLabel("Audio")
        audio_title.setObjectName("SettingsTitle")
        audio_help = QLabel("Choose the output and microphone mixed into saved clips.")
        audio_help.setObjectName("CardMuted")
        titles.addWidget(audio_title)
        titles.addWidget(audio_help)
        header.addLayout(titles, 1)
        refresh_audio = QPushButton("Refresh devices")
        refresh_audio.setObjectName("DarkButton")
        refresh_audio.clicked.connect(self._refresh_audio_devices)
        header.addWidget(refresh_audio)
        audio_layout.addLayout(header)
        audio_layout.addWidget(self._divider())

        self.system_audio_checkbox = QCheckBox("Include system audio")
        self.system_audio_checkbox.setChecked(self.config.system_audio_enabled)
        self.system_audio_checkbox.toggled.connect(self._sync_audio_controls)
        audio_layout.addWidget(self.system_audio_checkbox)

        system_grid = QGridLayout()
        system_grid.setHorizontalSpacing(22)
        system_grid.setVerticalSpacing(12)
        system_device_copy = QVBoxLayout()
        system_device_name = QLabel("Output device")
        system_device_name.setObjectName("SettingName")
        system_device_help = QLabel("League, music, and Discord voice using this output are included.")
        system_device_help.setObjectName("CardMuted")
        system_device_copy.addWidget(system_device_name)
        system_device_copy.addWidget(system_device_help)
        self.system_device_combo = QComboBox()
        system_grid.addLayout(system_device_copy, 0, 0)
        system_grid.addWidget(self.system_device_combo, 0, 1)

        system_volume_copy = QVBoxLayout()
        system_volume_name = QLabel("System volume")
        system_volume_name.setObjectName("SettingName")
        system_volume_help = QLabel("100% keeps the captured level unchanged.")
        system_volume_help.setObjectName("CardMuted")
        system_volume_copy.addWidget(system_volume_name)
        system_volume_copy.addWidget(system_volume_help)
        system_volume_row = QHBoxLayout()
        self.system_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.system_volume_slider.setRange(0, 200)
        self.system_volume_slider.setValue(self.config.system_audio_volume)
        self.system_volume_value = QLabel(f"{self.config.system_audio_volume}%")
        self.system_volume_value.setObjectName("VolumeValue")
        self.system_volume_slider.valueChanged.connect(
            lambda value: self.system_volume_value.setText(f"{value}%")
        )
        system_volume_row.addWidget(self.system_volume_slider, 1)
        system_volume_row.addWidget(self.system_volume_value)
        system_grid.addLayout(system_volume_copy, 1, 0)
        system_grid.addLayout(system_volume_row, 1, 1)
        system_grid.setColumnStretch(0, 1)
        audio_layout.addLayout(system_grid)
        audio_layout.addWidget(self._divider())

        self.microphone_checkbox = QCheckBox("Include microphone")
        self.microphone_checkbox.setChecked(self.config.microphone_enabled)
        self.microphone_checkbox.toggled.connect(self._sync_audio_controls)
        audio_layout.addWidget(self.microphone_checkbox)

        microphone_grid = QGridLayout()
        microphone_grid.setHorizontalSpacing(22)
        microphone_grid.setVerticalSpacing(12)
        microphone_device_copy = QVBoxLayout()
        microphone_device_name = QLabel("Microphone device")
        microphone_device_name.setObjectName("SettingName")
        microphone_device_help = QLabel("Select the input used for your voice.")
        microphone_device_help.setObjectName("CardMuted")
        microphone_device_copy.addWidget(microphone_device_name)
        microphone_device_copy.addWidget(microphone_device_help)
        self.microphone_device_combo = QComboBox()
        microphone_grid.addLayout(microphone_device_copy, 0, 0)
        microphone_grid.addWidget(self.microphone_device_combo, 0, 1)

        microphone_volume_copy = QVBoxLayout()
        microphone_volume_name = QLabel("Microphone volume")
        microphone_volume_name.setObjectName("SettingName")
        microphone_volume_help = QLabel("Raise this only when your voice is too quiet.")
        microphone_volume_help.setObjectName("CardMuted")
        microphone_volume_copy.addWidget(microphone_volume_name)
        microphone_volume_copy.addWidget(microphone_volume_help)
        microphone_volume_row = QHBoxLayout()
        self.microphone_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.microphone_volume_slider.setRange(0, 200)
        self.microphone_volume_slider.setValue(self.config.microphone_volume)
        self.microphone_volume_value = QLabel(f"{self.config.microphone_volume}%")
        self.microphone_volume_value.setObjectName("VolumeValue")
        self.microphone_volume_slider.valueChanged.connect(
            lambda value: self.microphone_volume_value.setText(f"{value}%")
        )
        microphone_volume_row.addWidget(self.microphone_volume_slider, 1)
        microphone_volume_row.addWidget(self.microphone_volume_value)
        microphone_grid.addLayout(microphone_volume_copy, 1, 0)
        microphone_grid.addLayout(microphone_volume_row, 1, 1)
        microphone_grid.setColumnStretch(0, 1)
        audio_layout.addLayout(microphone_grid)
        audio_layout.addWidget(self._divider())

        bitrate_row = QHBoxLayout()
        bitrate_copy = QVBoxLayout()
        bitrate_name = QLabel("Audio quality")
        bitrate_name.setObjectName("SettingName")
        bitrate_help = QLabel("160 kbps is recommended and does not affect video quality.")
        bitrate_help.setObjectName("CardMuted")
        bitrate_copy.addWidget(bitrate_name)
        bitrate_copy.addWidget(bitrate_help)
        self.audio_bitrate_combo = QComboBox()
        self.audio_bitrate_combo.addItems(list(AUDIO_BITRATE_OPTIONS))
        self.audio_bitrate_combo.setCurrentText(f"{self.config.audio_bitrate_kbps} kbps")
        bitrate_row.addLayout(bitrate_copy, 1)
        bitrate_row.addWidget(self.audio_bitrate_combo)
        audio_layout.addLayout(bitrate_row)

        note = QLabel(
            "Discord voice is included with system audio when Discord uses the selected output device."
        )
        note.setObjectName("InfoBanner")
        note.setWordWrap(True)
        audio_layout.addWidget(note)

        apply_row = QHBoxLayout()
        self.audio_summary = QLabel()
        self.audio_summary.setObjectName("CardMuted")
        apply_audio = QPushButton("Apply audio settings")
        apply_audio.setObjectName("PrimaryButton")
        apply_audio.clicked.connect(self._apply_audio_settings)
        apply_row.addWidget(self.audio_summary, 1)
        apply_row.addWidget(apply_audio)
        audio_layout.addLayout(apply_row)
        layout.addWidget(audio)
        layout.addStretch()

        self._refresh_audio_devices(show_message=False)
        self._sync_audio_controls()
        self._update_audio_summary()
        return scroll

    def _build_smart_settings(self) -> QWidget:
        scroll, layout = self._settings_scroll_page()
        automatic = QFrame()
        automatic.setObjectName("SettingsSection")
        automatic_layout = QVBoxLayout(automatic)
        automatic_layout.setContentsMargins(20, 18, 20, 18)
        automatic_layout.setSpacing(14)

        automatic_title = QLabel("Smart highlights")
        automatic_title.setObjectName("SettingsTitle")
        automatic_help = QLabel(
            "Riot events and match context are scored locally so routine plays can be filtered out."
        )
        automatic_help.setObjectName("CardMuted")
        automatic_help.setWordWrap(True)
        automatic_layout.addWidget(automatic_title)
        automatic_layout.addWidget(automatic_help)

        self.live_data_status = QLabel("Waiting for active match data")
        self.live_data_status.setObjectName("LiveDataStatus")
        automatic_layout.addWidget(self.live_data_status)
        automatic_layout.addWidget(self._divider())

        self.smart_highlights_checkbox = QCheckBox("Enable automatic Smart Highlights")
        self.smart_highlights_checkbox.setChecked(self.config.smart_highlights_enabled)
        self.smart_highlights_checkbox.toggled.connect(
            lambda checked: self.smart_sensitivity_combo.setEnabled(checked)
        )
        self.smart_highlights_checkbox.toggled.connect(lambda _checked: self._update_smart_summary())
        automatic_layout.addWidget(self.smart_highlights_checkbox)

        sensitivity_row = QHBoxLayout()
        sensitivity_copy = QVBoxLayout()
        sensitivity_name = QLabel("Sensitivity")
        sensitivity_name.setObjectName("SettingName")
        sensitivity_help = QLabel(
            "Strict keeps standout moments. Balanced is recommended. Save more is less selective."
        )
        sensitivity_help.setObjectName("CardMuted")
        sensitivity_help.setWordWrap(True)
        sensitivity_copy.addWidget(sensitivity_name)
        sensitivity_copy.addWidget(sensitivity_help)
        self.smart_sensitivity_combo = QComboBox()
        self.smart_sensitivity_combo.addItems(["Strict", "Balanced", "Save more"])
        sensitivity_display = {
            "strict": "Strict",
            "balanced": "Balanced",
            "save_more": "Save more",
        }.get(self.config.smart_sensitivity, "Balanced")
        self.smart_sensitivity_combo.setCurrentText(sensitivity_display)
        self.smart_sensitivity_combo.setEnabled(self.config.smart_highlights_enabled)
        self.smart_sensitivity_combo.currentTextChanged.connect(lambda _text: self._update_smart_summary())
        sensitivity_row.addLayout(sensitivity_copy, 1)
        sensitivity_row.addWidget(self.smart_sensitivity_combo)
        automatic_layout.addLayout(sensitivity_row)

        note = QLabel(
            "The app considers your kills, exact multikills, health, solo kills, level difference, "
            "death after the play, aces, and Dragon/Baron steals. Routine objective secures are ignored. "
            "Your manual shortcut always works, even when Smart Highlights is off."
        )
        note.setObjectName("InfoBanner")
        note.setWordWrap(True)
        automatic_layout.addWidget(note)

        apply_row = QHBoxLayout()
        self.smart_summary = QLabel()
        self.smart_summary.setObjectName("CardMuted")
        self._update_smart_summary()
        apply_smart = QPushButton("Apply smart highlights")
        apply_smart.setObjectName("PrimaryButton")
        apply_smart.clicked.connect(self._apply_smart_settings)
        apply_row.addWidget(self.smart_summary, 1)
        apply_row.addWidget(apply_smart)
        automatic_layout.addLayout(apply_row)
        layout.addWidget(automatic)
        layout.addStretch()
        return scroll

    def _build_app_settings(self) -> QWidget:
        scroll, layout = self._settings_scroll_page()

        behavior = QFrame()
        behavior.setObjectName("SettingsSection")
        behavior_layout = QVBoxLayout(behavior)
        behavior_layout.setContentsMargins(20, 18, 20, 18)
        behavior_layout.setSpacing(12)
        behavior_title = QLabel("Background behavior")
        behavior_title.setObjectName("SettingsTitle")
        behavior_help = QLabel(
            "League Highlights can stay in the system tray and automatically begin capture when the game window appears."
        )
        behavior_help.setObjectName("CardMuted")
        behavior_help.setWordWrap(True)
        behavior_layout.addWidget(behavior_title)
        behavior_layout.addWidget(behavior_help)

        self.launch_windows_checkbox = QCheckBox("Launch League Highlights when Windows starts")
        actual_startup = startup_is_enabled()
        self.config.launch_with_windows = actual_startup
        self.launch_windows_checkbox.setChecked(actual_startup)
        self.launch_windows_checkbox.toggled.connect(self._set_launch_with_windows)
        behavior_layout.addWidget(self.launch_windows_checkbox)

        self.start_minimized_checkbox = QCheckBox("Start minimized in the system tray")
        self.start_minimized_checkbox.setChecked(self.config.start_minimized)
        self.start_minimized_checkbox.toggled.connect(self._set_start_minimized)
        behavior_layout.addWidget(self.start_minimized_checkbox)

        self.close_to_tray_checkbox = QCheckBox("Closing the window keeps recording in the tray")
        self.close_to_tray_checkbox.setChecked(self.config.close_to_tray)
        self.close_to_tray_checkbox.toggled.connect(self._set_close_to_tray)
        behavior_layout.addWidget(self.close_to_tray_checkbox)

        auto_league = QCheckBox("Start and stop capture automatically with League")
        auto_league.setChecked(self.config.auto_start)
        auto_league.toggled.connect(self._set_auto_start)
        behavior_layout.addWidget(auto_league)
        layout.addWidget(behavior)

        shortcut = QFrame()
        shortcut.setObjectName("SettingsSection")
        shortcut_layout = QVBoxLayout(shortcut)
        shortcut_layout.setContentsMargins(20, 18, 20, 18)
        shortcut_layout.setSpacing(13)
        shortcut_title = QLabel("Global clip shortcut")
        shortcut_title.setObjectName("SettingsTitle")
        shortcut_help = QLabel(
            "Works while League has focus. Click the shortcut, then press the new key combination."
        )
        shortcut_help.setObjectName("CardMuted")
        shortcut_help.setWordWrap(True)
        shortcut_layout.addWidget(shortcut_title)
        shortcut_layout.addWidget(shortcut_help)
        shortcut_row = QHBoxLayout()
        shortcut_name = QLabel("Save the current rolling buffer")
        shortcut_name.setObjectName("SettingName")
        shortcut_row.addWidget(shortcut_name)
        shortcut_row.addStretch()
        self.hotkey_button = HotkeyCaptureButton(self.config.hotkey_display)
        self.hotkey_button.capture_started.connect(
            lambda: self.controller.set_hotkey_capture_mode(True)
        )
        self.hotkey_button.capture_finished.connect(
            lambda: self.controller.set_hotkey_capture_mode(False)
        )
        self.hotkey_button.hotkey_captured.connect(self._set_hotkey)
        reset_hotkey = QPushButton("Reset to F8")
        reset_hotkey.setObjectName("DarkButton")
        reset_hotkey.clicked.connect(lambda: self._set_hotkey(0x77, [], "F8"))
        shortcut_row.addWidget(self.hotkey_button)
        shortcut_row.addWidget(reset_hotkey)
        shortcut_layout.addLayout(shortcut_row)
        layout.addWidget(shortcut)

        storage = QFrame()
        storage.setObjectName("SettingsSection")
        storage_layout = QVBoxLayout(storage)
        storage_layout.setContentsMargins(20, 18, 20, 18)
        storage_layout.setSpacing(12)
        storage_title = QLabel("Storage")
        storage_title.setObjectName("SettingsTitle")
        storage_help = QLabel(str(self.config.clip_dir))
        storage_help.setObjectName("CardMuted")
        storage_help.setWordWrap(True)
        storage_actions = QHBoxLayout()
        open_clips = QPushButton("Open clip folder")
        open_clips.setObjectName("DarkButton")
        open_clips.clicked.connect(self._open_clip_folder)
        open_logs = QPushButton("Open diagnostic logs")
        open_logs.setObjectName("DarkButton")
        open_logs.clicked.connect(self._open_log_folder)
        storage_actions.addWidget(open_clips)
        storage_actions.addWidget(open_logs)
        storage_actions.addStretch()
        storage_layout.addWidget(storage_title)
        storage_layout.addWidget(storage_help)
        storage_layout.addLayout(storage_actions)

        discord_target_row = QHBoxLayout()
        discord_target_copy = QVBoxLayout()
        discord_target_name = QLabel("Discord export target")
        discord_target_name.setObjectName("SettingName")
        discord_target_help = QLabel(
            "Smart Trim exports one separate MP4 and leaves the original untouched."
        )
        discord_target_help.setObjectName("CardMuted")
        discord_target_copy.addWidget(discord_target_name)
        discord_target_copy.addWidget(discord_target_help)
        discord_target_row.addLayout(discord_target_copy, 1)
        self.discord_target_spin = QDoubleSpinBox()
        self.discord_target_spin.setRange(1.0, 100.0)
        self.discord_target_spin.setDecimals(1)
        self.discord_target_spin.setSingleStep(0.1)
        self.discord_target_spin.setSuffix(" MiB")
        self.discord_target_spin.setValue(self.config.discord_target_mib)
        self.discord_target_spin.valueChanged.connect(
            self.controller.update_discord_target_mib
        )
        discord_target_row.addWidget(self.discord_target_spin)
        storage_layout.addLayout(discord_target_row)
        layout.addWidget(storage)

        updates = QFrame()
        updates.setObjectName("SettingsSection")
        updates_layout = QVBoxLayout(updates)
        updates_layout.setContentsMargins(20, 18, 20, 18)
        updates_layout.setSpacing(10)
        updates_title = QLabel("Updates")
        updates_title.setObjectName("SettingsTitle")
        installed_version = QLabel(f"Installed version {APP_VERSION}")
        installed_version.setObjectName("SettingName")
        self.update_status_label = QLabel(
            "Updates are checked automatically. Downloads are verified and installed only after the app exits."
        )
        self.update_status_label.setObjectName("CardMuted")
        self.update_status_label.setWordWrap(True)
        update_actions = QHBoxLayout()
        self.check_updates_button = QPushButton("Check for updates")
        self.check_updates_button.setObjectName("DarkButton")
        self.check_updates_button.clicked.connect(self._check_for_updates)
        self.restart_update_button = QPushButton("Restart to update")
        self.restart_update_button.setObjectName("PrimaryButton")
        self.restart_update_button.clicked.connect(self._restart_to_update)
        self.restart_update_button.hide()
        update_actions.addWidget(self.check_updates_button)
        update_actions.addWidget(self.restart_update_button)
        update_actions.addStretch()
        updates_layout.addWidget(updates_title)
        updates_layout.addWidget(installed_version)
        updates_layout.addWidget(self.update_status_label)
        updates_layout.addLayout(update_actions)
        layout.addWidget(updates)

        recorder = QFrame()
        recorder.setObjectName("SettingsSection")
        recorder_layout = QVBoxLayout(recorder)
        recorder_layout.setContentsMargins(20, 18, 20, 18)
        recorder_layout.setSpacing(10)
        recorder_title = QLabel("Recorder diagnostics")
        recorder_title.setObjectName("SettingsTitle")
        self.ffmpeg_status = QLabel(
            "FFmpeg ready"
            if self.controller.ffmpeg.available
            else "FFmpeg missing — run scripts\\download_ffmpeg.ps1"
        )
        self.ffmpeg_status.setObjectName("SettingName")
        self.recorder_details = QLabel()
        self.recorder_details.setObjectName("CardMuted")
        self.recorder_details.setWordWrap(True)
        self.live_encoder_status = QLabel("Encoder: waiting for capture")
        self.live_encoder_status.setObjectName("SettingName")
        self.live_frame_status = QLabel("Capture health: waiting")
        self.live_frame_status.setObjectName("CardMuted")
        self.live_frame_status.setWordWrap(True)
        self._update_recorder_details()
        recorder_layout.addWidget(recorder_title)
        recorder_layout.addWidget(self.ffmpeg_status)
        recorder_layout.addWidget(self.live_encoder_status)
        recorder_layout.addWidget(self.live_frame_status)
        recorder_layout.addWidget(self.recorder_details)
        layout.addWidget(recorder)
        layout.addStretch()
        return scroll

    def _show_settings_section(self, index: int) -> None:
        self.settings_pages.setCurrentIndex(index)
        for button_index, button in enumerate(self.settings_tab_buttons):
            button.setProperty("active", button_index == index)
            button.style().unpolish(button)
            button.style().polish(button)

    @staticmethod
    def _divider() -> QFrame:
        divider = QFrame()
        divider.setObjectName("Divider")
        divider.setFixedHeight(1)
        return divider

    def refresh_clips(self) -> None:
        games = self.controller.games()
        clips = [clip for game in games for clip in game.clips]
        clip_word = "clip" if len(clips) == 1 else "clips"
        game_word = "game" if len(games) == 1 else "games"
        if hasattr(self, "storage_summary"):
            self.storage_summary.setText(f"{len(clips)} {clip_word}")

        self._clear_layout(self.games_layout)
        if not games:
            empty = QWidget()
            empty_layout = QVBoxLayout(empty)
            empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title = QLabel("No games with highlights yet")
            title.setObjectName("EmptyTitle")
            message = QLabel(
                f"Start League and press {self.config.hotkey_display}, or let automatic highlights save a play."
            )
            message.setObjectName("Muted")
            message.setWordWrap(True)
            empty_layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(message, alignment=Qt.AlignmentFlag.AlignCenter)
            self.games_layout.addWidget(empty, 1)
        else:
            for game in games:
                card = GameCard(game)
                card.clicked.connect(self._open_game)
                self.games_layout.addWidget(card)
            self.games_layout.addStretch()

        if self.selected_match_id is not None:
            selected = next((game for game in games if game.match_id == self.selected_match_id), None)
            if selected is None:
                self._back_to_games()
            else:
                self._populate_game_detail(selected)

    @staticmethod
    def _clear_layout(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                MainWindow._clear_layout(child_layout)

    def _open_game(self, game: GameHighlights) -> None:
        self.selected_match_id = game.match_id
        self._populate_game_detail(game)
        self.highlights_stack.setCurrentWidget(self.game_detail_view)
        self.highlights_header.hide()
        self._set_sidebar_compact(True)

    def _populate_game_detail(self, game: GameHighlights) -> None:
        previous_player = getattr(self, "inline_player", None)
        if previous_player is not None:
            previous_player.stop()
        self.inline_player = None
        self._clear_layout(self.detail_layout)
        count_text = f"{game.clip_count} highlight" if game.clip_count == 1 else f"{game.clip_count} highlights"
        self.title_bar.show_game_context(game)

        best_clip = max(
            game.clips,
            key=lambda clip: (clip.highlight_score, clip.created_at.timestamp()),
            default=None,
        )

        self.inline_player = InlineHighlightPlayer(self.controller)
        self.inline_player.set_game(game)
        self.detail_layout.addWidget(self.inline_player)
        if best_clip is not None:
            self.inline_player.load_clip(best_clip, autoplay=False)

        clips_header = QHBoxLayout()
        clips_title = QLabel("Clips")
        clips_title.setObjectName("SectionTitle")
        clips_header.addWidget(clips_title)
        clips_count = QLabel(count_text)
        clips_count.setObjectName("CardMuted")
        clips_header.addWidget(clips_count)
        clips_header.addStretch()
        self.detail_layout.addLayout(clips_header)

        for clip in sorted(game.clips, key=lambda item: (item.event_game_time is None, item.event_game_time or 0.0)):
            self.detail_layout.addWidget(
                ClipCard(clip, self.controller, self.refresh_clips, self._play_inline_clip)
            )

        self.detail_layout.addStretch()

    def _play_inline_clip(self, clip: ClipInfo) -> None:
        player = getattr(self, "inline_player", None)
        if player is None:
            return
        player.load_clip(clip, autoplay=True)
        QTimer.singleShot(0, lambda: self.detail_scroll.verticalScrollBar().setValue(0))

    def _back_to_games(self) -> None:
        player = getattr(self, "inline_player", None)
        if player is not None:
            player.stop()
        self.selected_match_id = None
        self.highlights_stack.setCurrentWidget(self.games_view)
        self.highlights_header.show()
        self._set_sidebar_compact(False)
        self.title_bar.show_default_context()
        self.page_title.setText("Highlights")
        self.highlights_subtitle.setText("")

    def _show_page(self, index: int) -> None:
        # Leaving a game's detail view through the sidebar must perform the same
        # cleanup as the Back button. Previously Settings only stopped playback,
        # leaving ``selected_match_id`` and ``highlights_stack`` on the hidden
        # detail page. Returning to Highlights then reopened a stale player that
        # could no longer navigate to the other games.
        leaving_game_detail = (
            index != 0
            and self.highlights_stack.currentWidget() is self.game_detail_view
        )
        if leaving_game_detail:
            self._back_to_games()
        elif index != 0:
            player = getattr(self, "inline_player", None)
            if player is not None:
                player.stop()

        self.pages.setCurrentIndex(index)

        # Highlights always has a valid visible sub-page. This also repairs stale
        # state from older sessions/builds without requiring the user to restart.
        if index == 0 and self.selected_match_id is None:
            self.highlights_stack.setCurrentWidget(self.games_view)
            self.highlights_header.show()

        if index != 0 or self.highlights_stack.currentWidget() is self.games_view:
            self.title_bar.show_default_context()
            self._set_sidebar_compact(False)

        self.highlights_nav.setProperty("active", index == 0)
        self.highlights_nav.style().unpolish(self.highlights_nav)
        self.highlights_nav.style().polish(self.highlights_nav)

    def _on_state_changed(self, state: RecorderState, detail: str) -> None:
        colors = {
            RecorderState.RECORDING: "#55D985",
            RecorderState.SAVING: "#66B7FF",
            RecorderState.ERROR: "#FF6672",
            RecorderState.STARTING: "#66B7FF",
            RecorderState.WAITING: "#E2B15C",
            RecorderState.STOPPED: "#8E9AA7",
        }
        color = colors.get(state, "#8E9AA7")
        display_names = {
            RecorderState.WAITING: "Waiting",
            RecorderState.STARTING: "Starting",
            RecorderState.RECORDING: "Recording",
            RecorderState.SAVING: "Saving",
            RecorderState.STOPPED: "Stopped",
            RecorderState.ERROR: "Error",
        }
        state_name = display_names.get(state, state.value.title())
        self.status_title.setText(state_name)
        self.title_bar.state_text.setText(state_name)
        self.title_bar.state_text.setStyleSheet(f"color:{color};")
        self.title_bar.state_dot.setStyleSheet(f"background:{color}; border-radius:4px;")
        self.status_title.setStyleSheet(f"color:{color};")
        self.status_dot.setStyleSheet(f"background:{color}; border-radius:5px;")
        self.status_detail.setText(detail)
        self.status_detail.setVisible(state == RecorderState.ERROR)
        self.status_time.setVisible(self.controller.recording or state in {RecorderState.STARTING, RecorderState.SAVING})
        self.record_button.setText("Stop" if self.controller.recording else "Start")
        self.record_button.setObjectName("DangerButton" if self.controller.recording else "DarkButton")
        self.record_button.style().unpolish(self.record_button)
        self.record_button.style().polish(self.record_button)
        self.save_clip_button.setEnabled(self.controller.recording and state != RecorderState.SAVING)
        busy = state in {RecorderState.SAVING, RecorderState.STARTING}
        self.status_progress.setVisible(busy)
        self.status_profile.setText(
            f"{self.config.width}×{self.config.height} • {self.config.fps} FPS • "
            f"{self.config.buffer_seconds}s buffer"
        )
        self._sync_tray_state()

    def _on_diagnostics_changed(self, diagnostics: RecorderDiagnostics) -> None:
        encoder_names = {
            "h264_nvenc": "NVIDIA NVENC",
            "h264_qsv": "Intel Quick Sync",
            "h264_amf": "AMD AMF",
            "libx264": "CPU x264",
            "Not started": "Waiting",
        }
        encoder_name = encoder_names.get(diagnostics.encoder, diagnostics.encoder)
        hardware_note = "hardware" if diagnostics.hardware_encoder else "software"
        if diagnostics.encoder in {"Not started", "Unknown"}:
            hardware_note = "not active"

        dropped_text = (
            f"Dropped: {diagnostics.dropped_frames:,} ({diagnostics.drop_rate:.2f}%)"
        )
        self.status_diagnostics.setText(f"{encoder_name} • {dropped_text}")

        if hasattr(self, "live_encoder_status"):
            self.live_encoder_status.setText(
                f"Encoder: {encoder_name} ({hardware_note}) • Capture: {diagnostics.capture_backend}"
            )
        if hasattr(self, "live_frame_status"):
            health_colors = {
                "Good": "#55D985",
                "Warning": "#E2B15C",
                "Poor": "#FF6672",
                "Waiting": "#8E9AA7",
            }
            color = health_colors.get(diagnostics.health, "#8E9AA7")
            fps_text = f"{diagnostics.fps:.1f}" if diagnostics.fps > 0 else "—"
            speed_text = f"{diagnostics.speed:.2f}×" if diagnostics.speed > 0 else "—"
            self.live_frame_status.setText(
                f"Capture health: {diagnostics.health} • Live FPS: {fps_text} / {self.config.fps} • "
                f"Dropped: {diagnostics.dropped_frames:,} ({diagnostics.drop_rate:.2f}%) • "
                f"Duplicated: {diagnostics.duplicated_frames:,} • Speed: {speed_text}"
            )
            self.live_frame_status.setStyleSheet(f"color:{color};")

    def _update_recording_time(self, seconds: int) -> None:
        self.status_time.setText(
            f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
        )

    def _on_clip_requested(self, label: str) -> None:
        manual = label == "MANUAL CLIP"
        self._show_toast(
            "CLIP REQUESTED" if manual else "AUTOMATIC HIGHLIGHT",
            f"{label.title()} — saving the original-quality highlight in the background…",
        )

    def _on_clip_saved(self, clip: ClipInfo) -> None:
        self.refresh_clips()
        toast_message = f"{clip.duration_text} • {clip.file_size_text} saved at original recording quality"
        if not clip.audio_included:
            toast_message += " • video only"
        self._show_toast(f"{clip.label.title()} SAVED", toast_message)

    def _show_error(self, message: str) -> None:
        self._show_toast("LEAGUE HIGHLIGHTS", message, error=True)

    def _show_toast(self, title: str, message: str, error: bool = False) -> None:
        if self._toast is not None:
            self._toast.close()
        self._toast = ClipToast(title, message, error=error)
        self._toast.show_near_active_screen()

    def _update_hotkey_hint(self) -> None:
        if hasattr(self, "hotkey_hint"):
            smart_text = (
                "Smart Highlights is monitoring the active match."
                if self.config.smart_highlights_enabled
                else "Automatic highlights are off; manual clips still work."
            )
            self.hotkey_hint.setText(
                f"Save the latest {self.config.buffer_seconds} seconds from anywhere. "
                f"{smart_text} Clips are grouped by League game."
            )
        if hasattr(self, "save_clip_button"):
            self.save_clip_button.setText(f"Save {self.config.buffer_seconds}s")
            self.save_clip_button.setToolTip(
                f"Save the latest {self.config.buffer_seconds} seconds ({self.config.hotkey_display})"
            )

    def _on_hotkey_changed(self, display_name: str) -> None:
        self.config.hotkey_display = display_name
        self.hotkey_button.set_hotkey_text(display_name)
        if hasattr(self, "hotkey_chip"):
            self.hotkey_chip.setText(display_name)
        self._update_hotkey_hint()
        self.refresh_clips()

    def _on_event_status_changed(self, message: str, connected: bool) -> None:
        self._event_status_cache = (message, connected)
        if not hasattr(self, "live_data_status"):
            return
        color = "#55E891" if connected else "#E2B15C"
        self.live_data_status.setText(message)
        self.live_data_status.setStyleSheet(
            f"color:{color}; background:#151E27; border:1px solid #2B3743; "
            "border-radius:7px; padding:7px 10px; font-weight:600;"
        )

    def _apply_smart_settings(self) -> None:
        sensitivity = {
            "Strict": "strict",
            "Balanced": "balanced",
            "Save more": "save_more",
        }.get(self.smart_sensitivity_combo.currentText(), "balanced")
        try:
            # Event categories stay enabled internally. The single Smart Highlights
            # switch is now the user-facing master control.
            for key in (
                "auto_clip_single_kill",
                "auto_clip_double_kill",
                "auto_clip_triple_kill",
                "auto_clip_quadra_kill",
                "auto_clip_pentakill",
                "auto_clip_dragon",
                "auto_clip_baron",
            ):
                setattr(self.config, key, True)
            self.controller.update_smart_settings(
                self.smart_highlights_checkbox.isChecked(),
                sensitivity,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot apply smart highlights", str(exc))
            return
        self._update_smart_summary()
        self._update_hotkey_hint()
        self._update_recorder_details()
        if self.config.smart_highlights_enabled:
            mode = self.smart_sensitivity_combo.currentText()
            message = f"Automatic detection is on with {mode.lower()} sensitivity."
        else:
            message = "Automatic detection is off. Your manual shortcut still works."
        self._show_toast("SMART HIGHLIGHTS UPDATED", message)

    def _update_smart_summary(self) -> None:
        if not all(
            hasattr(self, name)
            for name in ("smart_summary", "smart_highlights_checkbox", "smart_sensitivity_combo")
        ):
            return
        if self.smart_highlights_checkbox.isChecked():
            mode = self.smart_sensitivity_combo.currentText()
            self.smart_summary.setText(f"Automatic clips on • {mode} sensitivity")
        else:
            self.smart_summary.setText("Automatic clips off • manual shortcut remains active")

    def _set_auto_clip_option(self, key: str, checked: bool) -> None:
        if not hasattr(self.config, key):
            return
        setattr(self.config, key, bool(checked))
        self.config.save_user_settings()

    def _set_hotkey(self, virtual_key: int, modifiers: list[str], display_name: str) -> None:
        self.controller.update_hotkey(virtual_key, modifiers, display_name)
        self.hotkey_button.set_hotkey_text(display_name)
        self._show_toast("SHORTCUT UPDATED", f"{display_name} will now save a clip.")

    def _open_clip_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.config.clip_dir)))

    def _open_log_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.config.log_dir)))

    def _bind_update_manager(self) -> None:
        if self.update_manager is None:
            self.update_status_label.setText("Automatic updates are available in packaged builds.")
            self.check_updates_button.setEnabled(False)
            return
        self.update_manager.status_changed.connect(self._on_update_status)
        self.update_manager.update_available.connect(self._on_update_available)
        self.update_manager.download_progress.connect(self._on_update_progress)
        self.update_manager.update_ready.connect(self._on_update_ready)
        self.update_manager.no_update.connect(self._on_no_update)
        self.update_manager.error_occurred.connect(self._on_update_error)
        pending = self.update_manager.pending_update
        if pending is not None:
            self._on_update_ready(pending)
        else:
            self.update_status_label.setText(self.update_manager.status_text)

    def _check_for_updates(self) -> None:
        if self.update_manager is None:
            return
        self.check_updates_button.setEnabled(False)
        self.update_status_label.setText("Checking GitHub Releases for updates…")
        self.update_manager.check_for_updates(manual=True)

    def _on_update_status(self, message: str) -> None:
        self.update_status_label.setText(message)

    def _on_update_available(self, info: UpdateInfo) -> None:
        self.update_status_label.setText(
            f"Version {info.version} is available. Downloading and verifying it in the background…"
        )

    def _on_update_progress(self, percent: int, message: str) -> None:
        progress = f"{max(0, min(100, int(percent)))}%" if percent > 0 else ""
        self.update_status_label.setText(" — ".join(part for part in (message, progress) if part))

    def _on_no_update(self, message: str) -> None:
        self.check_updates_button.setEnabled(True)
        self.update_status_label.setText(message)

    def _on_update_error(self, message: str, manual: bool) -> None:
        self.check_updates_button.setEnabled(True)
        self.update_status_label.setText(message)
        if manual:
            QMessageBox.warning(self, "Update check", message)

    def _on_update_ready(self, info: UpdateInfo) -> None:
        self.check_updates_button.setEnabled(True)
        self.restart_update_button.show()
        self.update_status_label.setText(
            f"Version {info.version} is ready. It will install after League Highlights fully exits."
        )
        if self.tray_icon.isVisible() and not self._update_ready_notified:
            self._update_ready_notified = True
            self.tray_icon.showMessage(
                "League Highlights update ready",
                f"Version {info.version} will install after the app exits.",
                QSystemTrayIcon.MessageIcon.Information,
                4500,
            )

    def _restart_to_update(self) -> None:
        if self.update_manager is None or self.update_manager.pending_update is None:
            QMessageBox.information(self, "League Highlights", "No staged update is ready yet.")
            return
        if self.controller.busy:
            QMessageBox.information(
                self,
                "Update is waiting",
                "A clip or export is still being processed. Try again after it finishes.",
            )
            return
        if self.controller.recording:
            result = QMessageBox.question(
                self,
                "Restart to update?",
                "Recording will stop and the current rolling buffer will be discarded before the update installs.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if result != QMessageBox.StandardButton.Yes:
                return
        self._restart_after_update = True
        self._exit_application()

    def _launch_pending_update(self, restart: bool) -> None:
        if self._update_launch_attempted or self.update_manager is None:
            return
        if self.update_manager.pending_update is None:
            return
        self._update_launch_attempted = True
        if not self.update_manager.launch_pending_update(restart=restart):
            self._update_launch_attempted = False
            if restart:
                QMessageBox.warning(
                    self,
                    "Update could not start",
                    "The verified update remains staged. Exit normally and try again after checking the updater log.",
                )

    def _show_whats_new(self, force: bool = False) -> None:
        if self._whats_new_dialog is not None and self._whats_new_dialog.isVisible():
            self._whats_new_dialog.raise_()
            return
        if not force and self.config.last_seen_whats_new_version == APP_VERSION:
            return
        slides = notes_for_version(APP_VERSION)
        if not slides:
            return
        dialog = WhatsNewDialog(self, APP_VERSION, slides)
        self._whats_new_dialog = dialog
        dialog.finished.connect(self._on_whats_new_closed)
        dialog.open()

    def _on_whats_new_closed(self, _result: int) -> None:
        self.config.last_seen_whats_new_version = APP_VERSION
        self.config.save_user_settings()
        self._whats_new_dialog = None

    def _set_launch_with_windows(self, checked: bool) -> None:
        try:
            set_startup_enabled(bool(checked))
        except OSError as exc:
            QMessageBox.warning(self, "Windows startup", f"Could not update Windows startup:\n\n{exc}")
            self.launch_windows_checkbox.blockSignals(True)
            self.launch_windows_checkbox.setChecked(startup_is_enabled())
            self.launch_windows_checkbox.blockSignals(False)
            return
        self.config.launch_with_windows = bool(checked)
        self.config.save_user_settings()
        self._show_toast(
            "WINDOWS STARTUP UPDATED",
            "League Highlights will launch at sign-in." if checked else "Automatic launch was disabled.",
        )

    def _set_start_minimized(self, checked: bool) -> None:
        self.config.start_minimized = bool(checked)
        self.config.save_user_settings()

    def _set_close_to_tray(self, checked: bool) -> None:
        self.config.close_to_tray = bool(checked)
        self.config.save_user_settings()

    def _set_auto_start(self, checked: bool) -> None:
        self.config.auto_start = checked
        self.config.save_user_settings()

    def _set_buffer_seconds(self, text: str) -> None:
        try:
            seconds = int(text.split()[0])
            restarted = self.controller.update_buffer_seconds(seconds)
        except (ValueError, IndexError):
            return
        except RuntimeError as exc:
            QMessageBox.warning(self, "Cannot change buffer", str(exc))
            self.buffer_combo.setCurrentText(f"{self.config.buffer_seconds} seconds")
            return
        if self.selected_match_id is None:
            self.highlights_subtitle.setText(
                "Your games with saved highlights, grouped automatically."
            )
        self._update_hotkey_hint()
        if restarted:
            self._show_toast("BUFFER UPDATED", "Capture restarted and the rolling buffer is warming up.")

    def _refresh_audio_devices(self, show_message: bool = True) -> None:
        system_devices = self.controller.audio.list_system_devices()
        microphone_devices = self.controller.audio.list_microphone_devices()
        self._populate_audio_device_combo(
            self.system_device_combo,
            "Default output device",
            system_devices,
            self.config.system_audio_device,
        )
        self._populate_audio_device_combo(
            self.microphone_device_combo,
            "Default microphone",
            microphone_devices,
            self.config.microphone_device,
        )
        if show_message:
            self._show_toast(
                "AUDIO DEVICES REFRESHED",
                f"Found {len(system_devices)} output and {len(microphone_devices)} microphone device(s).",
            )

    @staticmethod
    def _populate_audio_device_combo(
        combo: QComboBox,
        default_label: str,
        device_names: list[str],
        selected_name: str,
    ) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(default_label, "")
        for name in device_names:
            combo.addItem(name, name)
        selected_index = combo.findData(selected_name) if selected_name else 0
        combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        combo.blockSignals(False)

    def _sync_audio_controls(self) -> None:
        system_enabled = self.system_audio_checkbox.isChecked()
        microphone_enabled = self.microphone_checkbox.isChecked()
        self.system_device_combo.setEnabled(system_enabled)
        self.system_volume_slider.setEnabled(system_enabled)
        self.system_volume_value.setEnabled(system_enabled)
        self.microphone_device_combo.setEnabled(microphone_enabled)
        self.microphone_volume_slider.setEnabled(microphone_enabled)
        self.microphone_volume_value.setEnabled(microphone_enabled)
        self._update_audio_summary()

    def _configured_audio_text(self) -> str:
        sources: list[str] = []
        if self.config.system_audio_enabled:
            sources.append(f"system {self.config.system_audio_volume}%")
        if self.config.microphone_enabled:
            sources.append(f"microphone {self.config.microphone_volume}%")
        return " + ".join(sources) if sources else "video only"

    def _update_audio_summary(self) -> None:
        if not hasattr(self, "audio_summary"):
            return
        sources: list[str] = []
        if self.system_audio_checkbox.isChecked():
            sources.append(f"System {self.system_volume_slider.value()}%")
        if self.microphone_checkbox.isChecked():
            sources.append(f"Mic {self.microphone_volume_slider.value()}%")
        summary = " + ".join(sources) if sources else "Video only"
        self.audio_summary.setText(
            f"Current selection: {summary} • {self.audio_bitrate_combo.currentText()}"
        )

    def _apply_audio_settings(self) -> None:
        bitrate = AUDIO_BITRATE_OPTIONS.get(self.audio_bitrate_combo.currentText())
        if bitrate is None:
            QMessageBox.warning(self, "Invalid audio settings", "The selected audio quality is invalid.")
            return
        try:
            restarted = self.controller.update_audio_settings(
                self.system_audio_checkbox.isChecked(),
                str(self.system_device_combo.currentData() or ""),
                self.system_volume_slider.value(),
                self.microphone_checkbox.isChecked(),
                str(self.microphone_device_combo.currentData() or ""),
                self.microphone_volume_slider.value(),
                bitrate,
            )
        except (RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "Cannot apply audio settings", str(exc))
            return
        self._update_audio_summary()
        self._update_recorder_details()
        message = (
            "Capture restarted. Allow the rolling audio buffer to warm up."
            if restarted
            else "The new audio mix will be used when capture starts."
        )
        self._show_toast("AUDIO SETTINGS UPDATED", message)

    def _apply_capture_settings(self) -> None:
        resolution = RESOLUTION_OPTIONS.get(self.resolution_combo.currentText())
        fps = FPS_OPTIONS.get(self.fps_combo.currentText())
        quality_profile = QUALITY_OPTIONS.get(self.quality_combo.currentText())
        if resolution is None or fps is None or quality_profile is None:
            QMessageBox.warning(self, "Invalid settings", "One of the recording settings is invalid.")
            return
        quality, _profile_audio_bitrate = quality_profile
        try:
            restarted = self.controller.update_capture_settings(
                resolution[0],
                resolution[1],
                fps,
                quality,
                self.config.audio_bitrate_kbps,
            )
        except (RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "Cannot apply settings", str(exc))
            return
        self._update_capture_profile_summary()
        self._update_recorder_details()
        self._on_state_changed(self.controller.state, self.controller.detail)
        message = (
            "Capture restarted. The rolling buffer is warming up."
            if restarted
            else "The new profile will be used when capture starts."
        )
        self._show_toast("RECORDING SETTINGS UPDATED", message)

    def _update_capture_profile_summary(self) -> None:
        if not hasattr(self, "capture_profile_summary"):
            return
        quality_name = _quality_option_for_value(self.config.quality).split(" — ")[0]
        self.capture_profile_summary.setText(
            f"Current: {self.config.width}×{self.config.height}, "
            f"{self.config.fps} FPS, {quality_name}"
        )

    def _update_recorder_details(self) -> None:
        if not hasattr(self, "recorder_details"):
            return
        quality_name = _quality_option_for_value(self.config.quality).split(" — ")[0]
        smart_line = (
            f"Smart highlights: {self.config.smart_sensitivity.replace('_', ' ').title()}"
            if self.config.smart_highlights_enabled
            else "Smart highlights: disabled"
        )
        self.recorder_details.setText(
            f"{self.config.width}×{self.config.height} • {self.config.fps} FPS • {quality_name}\n"
            "Desktop Duplication capture • live NVENC and dropped-frame diagnostics enabled\n"
            f"Audio: {self._configured_audio_text()} • AAC {self.config.audio_bitrate_kbps} kbps\n"
            f"Saved clips preserve the recording profile • {smart_line} • clips grouped by game"
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._whats_new_scheduled:
            self._whats_new_scheduled = True
            QTimer.singleShot(450, self._show_whats_new)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.title_bar.sync_maximize_icon()

    def closeEvent(self, event: QCloseEvent) -> None:
        player = getattr(self, "inline_player", None)
        if player is not None:
            player.stop()
        if self._force_exit:
            self.controller.shutdown()
            self.tray_icon.hide()
            self._launch_pending_update(self._restart_after_update)
            event.accept()
            return
        if self.config.close_to_tray and self.tray_icon.isVisible():
            event.ignore()
            self.hide()
            if not self._tray_notice_shown:
                self._tray_notice_shown = True
                self.tray_icon.showMessage(
                    "League Highlights is still running",
                    "Capture and automatic highlights continue in the system tray. Use Exit from the tray menu to close it.",
                    QSystemTrayIcon.MessageIcon.Information,
                    4500,
                )
            return
        self._force_exit = True
        self.controller.shutdown()
        self.tray_icon.hide()
        self._launch_pending_update(False)
        event.accept()
