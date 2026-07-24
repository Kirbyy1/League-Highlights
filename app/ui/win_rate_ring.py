from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget


class CircularWinRate(QWidget):
    """Circular ranked Solo/Duo win-rate indicator."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value: float | None = None
        self._games = 0

        self.setFixedSize(58, 58)
        self.setToolTip("Ranked Solo/Duo win rate is loading")

    def set_win_rate(self, value: float | None, games: int = 0) -> None:
        self._games = max(0, int(games))

        if value is None:
            self._value = None
            self.setToolTip("Ranked Solo/Duo win rate unavailable")
        else:
            self._value = max(0.0, min(100.0, float(value)))
            game_word = "game" if self._games == 1 else "games"
            self.setToolTip(
                f"Ranked Solo/Duo: {self._value:.0f}% win rate "
                f"over {self._games} {game_word}"
            )

        self.update()

    def clear_win_rate(self, message: str = "Ranked win rate is loading") -> None:
        self._value = None
        self._games = 0
        self.setToolTip(message)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        stroke_width = 5.0
        inset = stroke_width / 2.0 + 2.0
        ring_rect = QRectF(
            inset,
            inset,
            self.width() - inset * 2.0,
            self.height() - inset * 2.0,
        )

        background_pen = QPen(QColor("#26323D"), stroke_width)
        background_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(background_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(ring_rect, 0, 360 * 16)

        if self._value is not None:
            if self._value >= 55:
                progress_color = QColor("#63D98B")
            elif self._value >= 50:
                progress_color = QColor("#D8B65C")
            else:
                progress_color = QColor("#E5777F")

            progress_pen = QPen(progress_color, stroke_width)
            progress_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(progress_pen)
            span_angle = -int(360 * 16 * (self._value / 100.0))
            painter.drawArc(ring_rect, 90 * 16, span_angle)

            percentage_text = f"{self._value:.0f}%"
            text_color = QColor("#F0F4F7")
        else:
            percentage_text = "—"
            text_color = QColor("#71808E")

        percentage_font = QFont(self.font())
        percentage_font.setPointSize(10)
        percentage_font.setBold(True)
        painter.setFont(percentage_font)
        painter.setPen(text_color)
        painter.drawText(
            self.rect().adjusted(0, -5, 0, 0),
            Qt.AlignmentFlag.AlignCenter,
            percentage_text,
        )

        label_font = QFont(self.font())
        label_font.setPointSize(6)
        label_font.setBold(False)
        painter.setFont(label_font)
        painter.setPen(QColor("#82909D"))
        painter.drawText(
            self.rect().adjusted(0, 20, 0, 0),
            Qt.AlignmentFlag.AlignCenter,
            "WR",
        )
