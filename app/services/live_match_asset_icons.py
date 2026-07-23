
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

_ROLE_META = {
    "TOP": ("T", QColor("#6CA8FF"), QColor("#0F223B")),
    "JUNGLE": ("J", QColor("#56D67B"), QColor("#10291B")),
    "MIDDLE": ("M", QColor("#D18AFF"), QColor("#231534")),
    "BOTTOM": ("A", QColor("#F0C36A"), QColor("#31240E")),
    "UTILITY": ("S", QColor("#63C8FF"), QColor("#0D2230")),
    "": ("?", QColor("#95A2AF"), QColor("#1B232B")),
}

_TIER_META = {
    "IRON": ("IR", QColor("#8C7B72"), QColor("#2A2421")),
    "BRONZE": ("BR", QColor("#B68658"), QColor("#332418")),
    "SILVER": ("SI", QColor("#C9D2DA"), QColor("#28313A")),
    "GOLD": ("GO", QColor("#E8C66A"), QColor("#372D12")),
    "PLATINUM": ("PL", QColor("#5AC9B7"), QColor("#102B2A")),
    "EMERALD": ("EM", QColor("#41C26B"), QColor("#112917")),
    "DIAMOND": ("DI", QColor("#63A7FF"), QColor("#0E2038")),
    "MASTER": ("MA", QColor("#C27AFF"), QColor("#251138")),
    "GRANDMASTER": ("GM", QColor("#FF6D7C"), QColor("#381016")),
    "CHALLENGER": ("CH", QColor("#7AD9FF"), QColor("#102C36")),
    "UNRANKED": ("--", QColor("#7F8B98"), QColor("#1B232B")),
    "": ("--", QColor("#7F8B98"), QColor("#1B232B")),
}

@lru_cache(maxsize=16)
def make_role_icon(role: str, size: int = 18) -> QPixmap:
    role = str(role or "").upper()
    letter, accent, base = _ROLE_META.get(role, _ROLE_META[""])
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(0.0, 0.0, float(size), float(size), 5.0, 5.0)
    painter.fillPath(path, base)
    pen = QPen(accent)
    pen.setWidth(1)
    painter.setPen(pen)
    painter.drawPath(path)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(max(10, int(size * 0.55)))
    painter.setFont(font)
    painter.setPen(accent)
    painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, letter)
    painter.end()
    return pix

@lru_cache(maxsize=32)
def make_rank_emblem(tier: str, division: str = "", size: int = 28) -> QPixmap:
    tier = str(tier or "UNRANKED").upper()
    division = str(division or "").upper()
    short, accent, dark = _TIER_META.get(tier, _TIER_META["UNRANKED"])

    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    path = QPainterPath()
    path.moveTo(size * 0.5, 1.0)
    path.lineTo(size - 2.0, size * 0.34)
    path.lineTo(size * 0.84, size - 2.0)
    path.lineTo(size * 0.16, size - 2.0)
    path.lineTo(2.0, size * 0.34)
    path.closeSubpath()

    gradient = QLinearGradient(0, 0, 0, size)
    gradient.setColorAt(0.0, accent)
    gradient.setColorAt(1.0, dark)
    painter.fillPath(path, gradient)

    pen = QPen(accent.lighter(135))
    pen.setWidth(1)
    painter.setPen(pen)
    painter.drawPath(path)

    inner = QPainterPath()
    inset = max(4.0, size * 0.18)
    inner.addRoundedRect(
        inset,
        inset + 1.0,
        float(size) - inset * 2,
        float(size) - inset * 2 - 1.0,
        4.0,
        4.0,
    )
    painter.fillPath(inner, QColor(10, 14, 18, 185))

    font = QFont()
    font.setBold(True)
    font.setPixelSize(max(7, int(size * 0.23)))
    painter.setFont(font)
    painter.setPen(QColor("#F2F5F8"))
    painter.drawText(
        int(inset),
        int(inset + 1.0),
        int(size - inset * 2),
        int(size * 0.34),
        int(Qt.AlignmentFlag.AlignCenter),
        short,
    )

    secondary = "LP" if tier in {"MASTER", "GRANDMASTER", "CHALLENGER"} else (division or "--")
    small = QFont()
    small.setBold(True)
    small.setPixelSize(max(7, int(size * 0.22)))
    painter.setFont(small)
    painter.drawText(
        int(inset),
        int(size * 0.44),
        int(size - inset * 2),
        int(size * 0.28),
        int(Qt.AlignmentFlag.AlignCenter),
        secondary,
    )

    painter.end()
    return pix
