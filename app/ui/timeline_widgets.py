from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QFrame, QSizePolicy

from app.models import ClipInfo, GameHighlights


def _clock(milliseconds: int) -> str:
    total = max(0, int(milliseconds // 1000))
    return f"{total // 60:02d}:{total % 60:02d}"


def _match_clock(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


@dataclass(slots=True)
class _TimelineBlock:
    clip: ClipInfo
    start_seconds: float
    end_seconds: float
    event_seconds: float
    lane: int


class MatchTimeline(QFrame):
    """Sparse full-match timeline where only colored highlight ranges are playable.

    The neutral rail represents the complete League match. Colored ranges are the
    only video that exists locally. Clicking empty match time never pretends that
    a full replay was recorded.
    """

    clipSelected = Signal(object)

    def __init__(self, game: GameHighlights) -> None:
        super().__init__()
        self.game = game
        self.setObjectName("SparseMatchTimeline")
        self.setMouseTracking(True)
        self._hovered_path = ""
        self._selected_path = ""
        self._notice_text = ""
        self._hit_boxes: list[tuple[QRectF, ClipInfo]] = []
        self._blocks = self._build_blocks()
        lane_count = max((block.lane for block in self._blocks), default=0) + 1
        self._lane_count = max(1, min(4, lane_count))
        self.setMinimumHeight(178 + max(0, self._lane_count - 1) * 8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    @staticmethod
    def _clip_key(clip: ClipInfo) -> str:
        return str(clip.path)

    def select_clip(self, clip: ClipInfo) -> None:
        """Highlight a clip selected from the player or clip list."""
        self._selected_path = self._clip_key(clip)
        self._notice_text = ""
        self.update()

    def _event_time(self, clip: ClipInfo) -> float | None:
        if clip.event_game_time is not None:
            return max(0.0, float(clip.event_game_time))
        if clip.match_started_at is not None and clip.clip_window_start_wall is not None:
            return max(
                0.0,
                float(clip.clip_window_start_wall - clip.match_started_at)
                + float(clip.duration_seconds) * 0.55,
            )
        return None

    def _clip_range(self, clip: ClipInfo) -> tuple[float, float, float] | None:
        duration = max(1.0, float(self.game.timeline_duration_seconds))
        event_time = self._event_time(clip)
        if event_time is None:
            return None

        if clip.match_started_at is not None and clip.clip_window_start_wall is not None:
            start = max(0.0, float(clip.clip_window_start_wall - clip.match_started_at))
            if clip.clip_window_end_wall is not None:
                end = max(start + 0.25, float(clip.clip_window_end_wall - clip.match_started_at))
            else:
                end = start + max(0.25, float(clip.duration_seconds))
        else:
            clip_duration = max(0.25, float(clip.duration_seconds))
            start = max(0.0, event_time - clip_duration * 0.58)
            end = start + clip_duration

        start = min(duration, start)
        end = min(duration, max(start + 0.25, end))
        event_time = min(duration, max(start, event_time))
        return start, end, event_time

    def _build_blocks(self) -> list[_TimelineBlock]:
        duration = max(1.0, float(self.game.timeline_duration_seconds))
        prepared: list[tuple[ClipInfo, float, float, float, float, float]] = []
        # The actual clip duration is preserved, but very short ranges receive a
        # small visual minimum so they remain easy to discover and click.
        minimum_visual_span = max(5.0, duration * 0.010)

        for clip in self.game.clips:
            clip_range = self._clip_range(clip)
            if clip_range is None:
                continue
            start, end, event_time = clip_range
            centre = (start + end) / 2.0
            visual_span = max(end - start, minimum_visual_span)
            visual_start = max(0.0, centre - visual_span / 2.0)
            visual_end = min(duration, visual_start + visual_span)
            visual_start = max(0.0, visual_end - visual_span)
            prepared.append((clip, start, end, event_time, visual_start, visual_end))

        prepared.sort(key=lambda item: (item[3], item[4], item[5]))
        lane_ends: list[float] = []
        blocks: list[_TimelineBlock] = []
        marker_gap = max(9.0, duration * 0.012)
        for clip, _start, _end, event_time, visual_start, visual_end in prepared:
            lane = 0
            while lane < len(lane_ends) and event_time < lane_ends[lane] + marker_gap:
                lane += 1
            if lane == len(lane_ends):
                lane_ends.append(event_time)
            else:
                lane_ends[lane] = event_time
            blocks.append(_TimelineBlock(clip, visual_start, visual_end, event_time, lane % 4))
        return blocks

    @staticmethod
    def _category(clip: ClipInfo) -> tuple[str, QColor]:
        label = clip.label.replace("_", " ").upper()
        kind = str(getattr(clip, "event_kind", "") or "").lower()
        if "STEAL" in label or "dragon" in kind or "baron" in kind:
            return "objective", QColor("#A98BFF")
        if "MANUAL" in label:
            return "manual", QColor("#55D985")
        if any(token in label for token in ("PENTA", "QUADRA", "TRIPLE", "DOUBLE")):
            return "multikill", QColor("#FFB45C")
        if "KILL" in label or "ACE" in label:
            return "combat", QColor("#FF7483")
        return "other", QColor("#66B7FF")

    def _timeline_geometry(self) -> tuple[float, float, float]:
        return 28.0, max(29.0, float(self.width()) - 28.0), 116.0

    def _rail_hit_rect(self) -> QRectF:
        left, right, rail_y = self._timeline_geometry()
        return QRectF(left, rail_y - 17.0, max(1.0, right - left), 38.0)

    @staticmethod
    def _nice_tick_seconds(duration: float) -> int:
        if duration <= 12 * 60:
            return 120
        if duration <= 25 * 60:
            return 300
        if duration <= 45 * 60:
            return 300
        return 600

    def _draw_marker_icon(
        self,
        painter: QPainter,
        category: str,
        color: QColor,
        centre: QPointF,
        hovered: bool,
        selected: bool,
    ) -> QRectF:
        size = 23.0 if selected else 21.0 if hovered else 19.0
        rect = QRectF(centre.x() - size / 2.0, centre.y() - size / 2.0, size, size)
        background = QColor("#101820")
        background.setAlpha(250)
        painter.setBrush(background)
        painter.setPen(QPen(QColor("#F5F8FB") if selected else color, 2.0 if selected else 1.35))
        painter.drawRoundedRect(rect, 5.0, 5.0)

        painter.setPen(QPen(color, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        cx, cy = centre.x(), centre.y()
        if category == "combat":
            painter.drawLine(QPointF(cx - 4.5, cy - 4.5), QPointF(cx + 4.5, cy + 4.5))
            painter.drawLine(QPointF(cx + 4.5, cy - 4.5), QPointF(cx - 4.5, cy + 4.5))
            painter.drawLine(QPointF(cx - 5.5, cy + 2.5), QPointF(cx - 2.5, cy + 5.5))
            painter.drawLine(QPointF(cx + 5.5, cy + 2.5), QPointF(cx + 2.5, cy + 5.5))
        elif category == "multikill":
            points = QPolygonF([
                QPointF(cx, cy - 6.0),
                QPointF(cx + 5.5, cy),
                QPointF(cx, cy + 6.0),
                QPointF(cx - 5.5, cy),
            ])
            painter.drawPolygon(points)
            painter.drawLine(QPointF(cx - 3.0, cy), QPointF(cx + 3.0, cy))
        elif category == "objective":
            painter.drawEllipse(QPointF(cx, cy), 5.5, 5.5)
            painter.drawEllipse(QPointF(cx, cy), 2.0, 2.0)
        elif category == "manual":
            painter.drawLine(QPointF(cx - 4.0, cy - 5.0), QPointF(cx - 4.0, cy + 5.0))
            painter.drawLine(QPointF(cx - 4.0, cy - 4.5), QPointF(cx + 4.5, cy - 2.0))
            painter.drawLine(QPointF(cx + 4.5, cy - 2.0), QPointF(cx - 4.0, cy + 0.5))
        else:
            painter.drawEllipse(QPointF(cx, cy), 4.5, 4.5)
        return rect

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width = max(1.0, float(self.width()))
        left, right, rail_y = self._timeline_geometry()
        available = max(1.0, right - left)
        duration = max(1.0, float(self.game.timeline_duration_seconds))

        heading_font = QFont(painter.font())
        heading_font.setPointSize(9)
        heading_font.setBold(True)
        painter.setFont(heading_font)
        painter.setPen(QColor("#EAF0F5"))
        painter.drawText(QRectF(left, 10, 250, 20), Qt.AlignmentFlag.AlignVCenter, "FULL MATCH")

        helper_font = QFont(painter.font())
        helper_font.setPointSize(8)
        helper_font.setBold(False)
        painter.setFont(helper_font)
        active_key = self._hovered_path or self._selected_path
        active_clip = next(
            (clip for _, clip in self._hit_boxes if self._clip_key(clip) == active_key),
            None,
        )
        helper = self._notice_text
        if not helper and active_clip is not None:
            helper = (
                f"{active_clip.match_time_text} · "
                f"{active_clip.label.replace('_', ' ').title()} · {active_clip.duration_text}"
            )
        if not helper:
            helper = "Only colored sections contain playable video"
        painter.setPen(QColor("#8E9BA8"))
        painter.drawText(
            QRectF(max(left + 250, width - 430), 10, 402, 20),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            helper,
        )

        # A full-length neutral track creates match context without implying that
        # the uncolored gaps are locally recorded video.
        rail_rect = QRectF(left, rail_y - 4.0, available, 8.0)
        painter.setBrush(QColor("#26323D"))
        painter.setPen(QPen(QColor("#34424E"), 1.0))
        painter.drawRoundedRect(rail_rect, 4.0, 4.0)

        # Minor ruler ticks resemble a video editor / match replay timeline.
        painter.setFont(helper_font)
        minor_count = 36
        for index in range(minor_count + 1):
            ratio = index / minor_count
            x = left + ratio * available
            height = 7.0 if index % 6 == 0 else 4.0
            painter.setPen(QPen(QColor("#465360"), 1.0))
            painter.drawLine(QPointF(x, rail_y + 8.0), QPointF(x, rail_y + 8.0 + height))

        tick_seconds = self._nice_tick_seconds(duration)
        tick_values = list(range(0, int(duration) + 1, tick_seconds))
        if not tick_values or tick_values[-1] != int(duration):
            tick_values.append(int(duration))
        last_label_right = -10_000.0
        for seconds in tick_values:
            ratio = max(0.0, min(1.0, seconds / duration))
            x = left + ratio * available
            label = _match_clock(seconds)
            label_width = painter.fontMetrics().horizontalAdvance(label)
            label_x = max(left, min(right - label_width, x - label_width / 2.0))
            if label_x < last_label_right + 12 and seconds not in {0, int(duration)}:
                continue
            painter.setPen(QColor("#84919E"))
            painter.drawText(QPointF(label_x, rail_y + 31.0), label)
            last_label_right = label_x + label_width

        self._hit_boxes.clear()
        # Paint the playable islands directly on the full-match rail.
        sorted_blocks = sorted(
            self._blocks,
            key=lambda block: self._clip_key(block.clip) == self._selected_path,
        )
        for block in sorted_blocks:
            x1 = left + (block.start_seconds / duration) * available
            x2 = left + (block.end_seconds / duration) * available
            segment = QRectF(x1, rail_y - 5.5, max(8.0, x2 - x1), 11.0)
            if segment.right() > right:
                segment.moveRight(right)
            if segment.left() < left:
                segment.moveLeft(left)

            category, color = self._category(block.clip)
            key = self._clip_key(block.clip)
            hovered = self._hovered_path == key
            selected = self._selected_path == key
            segment_fill = QColor(color)
            segment_fill.setAlpha(255 if selected else 235 if hovered else 205)
            painter.setBrush(segment_fill)
            painter.setPen(QPen(QColor("#FFFFFF") if selected else color, 2.0 if selected else 1.0))
            painter.drawRoundedRect(segment, 5.0, 5.0)

            event_x = left + (block.event_seconds / duration) * available
            event_x = max(segment.left(), min(segment.right(), event_x))
            icon_y = 79.0 - min(3, block.lane) * 20.0
            centre = QPointF(event_x, icon_y)
            painter.setPen(QPen(color, 1.15))
            painter.drawLine(QPointF(event_x, icon_y + 12.0), QPointF(event_x, rail_y - 7.0))
            marker_rect = self._draw_marker_icon(
                painter,
                category,
                color,
                centre,
                hovered,
                selected,
            )

            if selected:
                playhead = QPen(QColor("#F5F8FB"), 1.1)
                playhead.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(playhead)
                painter.drawLine(QPointF(event_x, 33.0), QPointF(event_x, rail_y + 21.0))
                triangle = QPolygonF([
                    QPointF(event_x - 4.5, 31.0),
                    QPointF(event_x + 4.5, 31.0),
                    QPointF(event_x, 37.0),
                ])
                painter.setBrush(QColor("#F5F8FB"))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawPolygon(triangle)

            hit_rect = segment.united(marker_rect).adjusted(-5.0, -5.0, 5.0, 5.0)
            self._hit_boxes.append((hit_rect, block.clip))

        footer = "Colored = saved video   •   Dark gaps = no recording"
        painter.setPen(QColor("#72808D"))
        painter.drawText(QRectF(left, rail_y + 39.0, available, 20.0), Qt.AlignmentFlag.AlignLeft, footer)

    def _clip_at(self, position: QPointF) -> ClipInfo | None:
        for rect, clip in reversed(self._hit_boxes):
            if rect.contains(position):
                return clip
        return None

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            clip = self._clip_at(event.position())
            if clip is not None:
                self._selected_path = self._clip_key(clip)
                self._notice_text = ""
                self.update()
                self.clipSelected.emit(clip)
                event.accept()
                return
            if self._rail_hit_rect().contains(event.position()):
                self._notice_text = "No saved video here — choose a colored highlight"
                self.update()
                QTimer.singleShot(2200, self._clear_notice)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def _clear_notice(self) -> None:
        if self._notice_text:
            self._notice_text = ""
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        clip = self._clip_at(event.position())
        key = self._clip_key(clip) if clip is not None else ""
        if key != self._hovered_path:
            self._hovered_path = key
            self.update()

        if clip is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            reasons = "\n".join(clip.score_reasons[:2])
            detail = (
                f"{clip.match_time_text} — {clip.label.replace('_', ' ').title()}\n"
                f"{clip.duration_text} playable highlight"
            )
            if reasons:
                detail += f"\n{reasons}"
            self.setToolTip(detail + "\nClick to play")
        elif self._rail_hit_rect().contains(event.position()):
            self.setCursor(Qt.CursorShape.ForbiddenCursor)
            self.setToolTip("No saved video at this match time")
        else:
            self.unsetCursor()
            self.setToolTip("")
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered_path = ""
        self.unsetCursor()
        self.setToolTip("")
        self.update()
        super().leaveEvent(event)

@dataclass(slots=True)
class _PlayableHighlightRange:
    clip: ClipInfo
    actual_start: float
    actual_end: float
    visual_start: float
    visual_end: float
    event_seconds: float


class MatchHighlightProgressBar(QFrame):
    """Full-match progress bar where only saved highlight ranges are playable.

    The neutral rail represents the complete match. Colored ranges represent the
    local clip files. Empty match time is deliberately non-interactive.
    """

    highlightActivated = Signal(object, int)

    def __init__(self, game: GameHighlights | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MatchHighlightProgressBar")
        self.setMouseTracking(True)
        self.setMinimumHeight(38)
        self.setMaximumHeight(38)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._game: GameHighlights | None = None
        self._ranges: list[_PlayableHighlightRange] = []
        self._hit_boxes: list[tuple[QRectF, _PlayableHighlightRange]] = []
        self._selected_path = ""
        self._hovered_path = ""
        self._current_match_seconds: float | None = None
        self._dragging = False
        if game is not None:
            self.set_game(game)

    @staticmethod
    def _clip_key(clip: ClipInfo) -> str:
        return str(clip.path)

    @staticmethod
    def _event_time(clip: ClipInfo) -> float | None:
        if clip.event_game_time is not None:
            return max(0.0, float(clip.event_game_time))
        if clip.match_started_at is not None and clip.clip_window_start_wall is not None:
            return max(
                0.0,
                float(clip.clip_window_start_wall - clip.match_started_at)
                + float(clip.duration_seconds) * 0.55,
            )
        return None

    def _actual_range(self, clip: ClipInfo) -> tuple[float, float, float] | None:
        if self._game is None:
            return None
        match_duration = max(1.0, float(self._game.timeline_duration_seconds))
        event_time = self._event_time(clip)
        if event_time is None:
            return None

        if clip.match_started_at is not None and clip.clip_window_start_wall is not None:
            start = max(0.0, float(clip.clip_window_start_wall - clip.match_started_at))
            if clip.clip_window_end_wall is not None:
                end = max(start + 0.25, float(clip.clip_window_end_wall - clip.match_started_at))
            else:
                end = start + max(0.25, float(clip.duration_seconds))
        else:
            clip_duration = max(0.25, float(clip.duration_seconds))
            start = max(0.0, event_time - clip_duration * 0.58)
            end = start + clip_duration

        start = min(match_duration, start)
        end = min(match_duration, max(start + 0.25, end))
        event_time = min(match_duration, max(start, event_time))
        return start, end, event_time

    def set_game(self, game: GameHighlights) -> None:
        self._game = game
        self._selected_path = ""
        self._hovered_path = ""
        self._current_match_seconds = None
        self._ranges = []
        duration = max(1.0, float(game.timeline_duration_seconds))

        # The bar remains chronological, but dense clusters are gently spread so
        # each saved clip stays visible and easy to click. This is intentionally a
        # visual navigation layout rather than a frame-perfect replay timeline.
        minimum_visual_span = max(10.0, duration * 0.014)
        prepared: list[tuple[ClipInfo, float, float, float, float]] = []
        for clip in game.clips:
            values = self._actual_range(clip)
            if values is None:
                continue
            actual_start, actual_end, event_seconds = values
            visual_span = max(actual_end - actual_start, minimum_visual_span)
            prepared.append((clip, actual_start, actual_end, event_seconds, visual_span))

        prepared.sort(key=lambda item: (item[3], item[1]))
        if not prepared:
            self.update()
            return

        # Highlights closer than this form a visual cluster. Their centres are
        # spaced evenly while the cluster remains centred near the real events.
        cluster_threshold = max(24.0, duration * 0.022)
        centre_step = max(20.0, duration * 0.020)
        clusters: list[list[tuple[ClipInfo, float, float, float, float]]] = []
        current: list[tuple[ClipInfo, float, float, float, float]] = []
        previous_event: float | None = None
        for item in prepared:
            event_seconds = item[3]
            if current and previous_event is not None and event_seconds - previous_event > cluster_threshold:
                clusters.append(current)
                current = []
            current.append(item)
            previous_event = event_seconds
        if current:
            clusters.append(current)

        visual_items: list[_PlayableHighlightRange] = []
        for cluster in clusters:
            cluster_centre = sum(item[3] for item in cluster) / len(cluster)
            first_half = cluster[0][4] / 2.0
            last_half = cluster[-1][4] / 2.0
            marker_gap = max(6.0, duration * 0.005)
            preferred_step = max(
                centre_step,
                max(item[4] for item in cluster) + marker_gap,
            )
            if len(cluster) > 1:
                maximum_step = max(
                    0.0,
                    (duration - first_half - last_half) / (len(cluster) - 1),
                )
                step = min(preferred_step, maximum_step) if maximum_step > 0.0 else 0.0
            else:
                step = 0.0
            positions = [
                cluster_centre + (index - (len(cluster) - 1) / 2.0) * step
                for index in range(len(cluster))
            ]

            # Shift the whole cluster back inside the match while preserving the
            # spacing between its markers.
            lower = positions[0] - first_half
            if lower < 0.0:
                positions = [position - lower for position in positions]
            upper = positions[-1] + last_half
            if upper > duration:
                shift = upper - duration
                positions = [position - shift for position in positions]

            for item, centre in zip(cluster, positions):
                clip, actual_start, actual_end, event_seconds, visual_span = item
                visual_start = max(0.0, centre - visual_span / 2.0)
                visual_end = min(duration, centre + visual_span / 2.0)
                if visual_end - visual_start < visual_span:
                    if visual_start <= 0.0:
                        visual_end = min(duration, visual_span)
                    elif visual_end >= duration:
                        visual_start = max(0.0, duration - visual_span)
                visual_items.append(
                    _PlayableHighlightRange(
                        clip=clip,
                        actual_start=actual_start,
                        actual_end=actual_end,
                        visual_start=visual_start,
                        visual_end=visual_end,
                        event_seconds=event_seconds,
                    )
                )

        self._ranges = sorted(visual_items, key=lambda item: (item.visual_start, item.event_seconds))
        self.update()

    def select_clip(self, clip: ClipInfo) -> None:
        self._selected_path = self._clip_key(clip)
        self.update()

    def set_playback_position(self, clip: ClipInfo, local_milliseconds: int) -> None:
        playable = self._range_for_clip(clip)
        if playable is None:
            self._current_match_seconds = None
            self.update()
            return
        local_duration_ms = max(1.0, float(clip.duration_seconds) * 1000.0)
        ratio = max(0.0, min(1.0, float(local_milliseconds) / local_duration_ms))
        # Follow the visual segment so the playhead always remains inside the
        # larger/spread highlight block shown to the user.
        self._current_match_seconds = playable.visual_start + ratio * (
            playable.visual_end - playable.visual_start
        )
        self.update()

    @property
    def current_match_seconds(self) -> float | None:
        return self._current_match_seconds

    @property
    def match_duration_seconds(self) -> float:
        return max(1.0, float(self._game.timeline_duration_seconds)) if self._game is not None else 1.0

    def clear_position(self) -> None:
        self._current_match_seconds = None
        self.update()

    def _range_for_clip(self, clip: ClipInfo) -> _PlayableHighlightRange | None:
        key = self._clip_key(clip)
        return next((item for item in self._ranges if self._clip_key(item.clip) == key), None)

    @staticmethod
    def _category_color(clip: ClipInfo) -> QColor:
        label = clip.label.replace("_", " ").upper()
        kind = str(getattr(clip, "event_kind", "") or "").lower()
        if "STEAL" in label or "dragon" in kind or "baron" in kind:
            return QColor("#A98BFF")
        if "MANUAL" in label:
            return QColor("#55D985")
        if any(token in label for token in ("PENTA", "QUADRA", "TRIPLE", "DOUBLE")):
            return QColor("#FFB45C")
        if "KILL" in label or "ACE" in label:
            return QColor("#FF7483")
        return QColor("#66B7FF")

    def _geometry(self) -> tuple[float, float, float]:
        return 6.0, max(7.0, float(self.width()) - 6.0), 19.0

    def _match_time_from_x(self, x: float) -> float:
        if self._game is None:
            return 0.0
        left, right, _ = self._geometry()
        ratio = max(0.0, min(1.0, (x - left) / max(1.0, right - left)))
        return ratio * max(1.0, float(self._game.timeline_duration_seconds))

    def _range_at(self, position: QPointF) -> _PlayableHighlightRange | None:
        candidates = [item for rect, item in self._hit_boxes if rect.contains(position)]
        if not candidates:
            return None
        visual_time = self._match_time_from_x(position.x())
        return min(
            candidates,
            key=lambda item: abs(((item.visual_start + item.visual_end) / 2.0) - visual_time),
        )

    def _local_ms_for_position(self, playable: _PlayableHighlightRange, x: float) -> int:
        if self._game is None:
            return 0
        visual_time = self._match_time_from_x(x)
        visual_time = max(playable.visual_start, min(playable.visual_end, visual_time))
        span = max(0.001, playable.visual_end - playable.visual_start)
        ratio = (visual_time - playable.visual_start) / span
        return int(round(ratio * max(0.0, float(playable.clip.duration_seconds)) * 1000.0))

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        left, right, rail_y = self._geometry()
        available = max(1.0, right - left)
        duration = max(
            1.0,
            float(self._game.timeline_duration_seconds) if self._game is not None else 1.0,
        )

        # The full-match rail is context only. Empty sections deliberately stay dark.
        rail = QRectF(left, rail_y - 7.0, available, 14.0)
        painter.setPen(QPen(QColor(255, 255, 255, 28), 1.0))
        painter.setBrush(QColor(255, 255, 255, 46))
        painter.drawRoundedRect(rail, 7.0, 7.0)

        self._hit_boxes.clear()
        for playable in self._ranges:
            x1 = left + playable.visual_start / duration * available
            x2 = left + playable.visual_end / duration * available
            rect = QRectF(x1, rail_y - 7.0, max(14.0, x2 - x1), 14.0)
            rect.setLeft(max(left, rect.left()))
            rect.setRight(min(right, rect.right()))
            key = self._clip_key(playable.clip)
            selected = key == self._selected_path
            hovered = key == self._hovered_path
            color = self._category_color(playable.clip)

            if selected:
                glow = rect.adjusted(-2.0, -2.0, 2.0, 2.0)
                glow_color = QColor(color)
                glow_color.setAlpha(80)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(glow_color)
                painter.drawRoundedRect(glow, 9.0, 9.0)

            fill = QColor(color)
            fill.setAlpha(255 if selected else 235 if hovered else 210)
            painter.setBrush(fill)
            painter.setPen(
                QPen(
                    QColor("#FFFFFF") if selected else QColor(255, 255, 255, 120) if hovered else color,
                    1.8 if selected else 1.0,
                )
            )
            painter.drawRoundedRect(rect, 7.0, 7.0)
            self._hit_boxes.append((rect.adjusted(-4.0, -7.0, 4.0, 7.0), playable))

        # Keep the playhead inside the rail; no floating pins or stems.
        if self._current_match_seconds is not None:
            playhead_x = left + max(0.0, min(duration, self._current_match_seconds)) / duration * available
            painter.setPen(QPen(QColor("#FFFFFF"), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(playhead_x, rail_y - 9.0), QPointF(playhead_x, rail_y + 9.0))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            playable = self._range_at(event.position())
            if playable is None:
                self._dragging = False
                self.setToolTip("No saved video at this match time")
                event.accept()
                return
            self._dragging = True
            key = self._clip_key(playable.clip)
            same_clip = key == self._selected_path
            self._selected_path = key
            local_ms = self._local_ms_for_position(playable, event.position().x()) if same_clip else 0
            self.highlightActivated.emit(playable.clip, local_ms)
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        playable = self._range_at(event.position())
        key = self._clip_key(playable.clip) if playable is not None else ""
        if key != self._hovered_path:
            self._hovered_path = key
            self.update()

        if self._dragging and playable is not None and key == self._selected_path:
            self.highlightActivated.emit(
                playable.clip,
                self._local_ms_for_position(playable, event.position().x()),
            )

        if playable is None:
            self.setCursor(Qt.CursorShape.ForbiddenCursor)
            self.setToolTip("No saved video here — colored sections are playable")
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            label = playable.clip.label.replace("_", " ").title()
            self.setToolTip(
                f"{playable.clip.match_time_text} — {label}\n"
                f"{playable.clip.duration_text} saved clip\nClick to play"
            )
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._dragging = False
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered_path = ""
        self._dragging = False
        self.unsetCursor()
        self.setToolTip("")
        self.update()
        super().leaveEvent(event)


class FilmstripTrimWidget(QFrame):
    """Editor-style filmstrip with trim handles and a draggable playhead.

    Deliberately video-only: no waveform is drawn or generated.
    """

    selectionChanged = Signal(int, int)
    seekRequested = Signal(int)

    def __init__(self, duration_ms: int, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("FilmstripTrimWidget")
        self.setMouseTracking(True)
        self.setMinimumHeight(178)
        self.setSizePolicy(self.sizePolicy().horizontalPolicy(), self.sizePolicy().verticalPolicy())
        self.duration_ms = max(1, int(duration_ms))
        self.start_ms = 0
        self.end_ms = self.duration_ms
        self.playhead_ms = 0
        self.thumbnails: list[QPixmap] = []
        self._drag_mode = ""

    def sizeHint(self) -> QSize:
        return QSize(900, 178)

    def set_duration(self, duration_ms: int) -> None:
        self.duration_ms = max(1, int(duration_ms))
        self.end_ms = min(self.duration_ms, max(self.start_ms + 250, self.end_ms))
        self.playhead_ms = min(self.duration_ms, self.playhead_ms)
        self.update()

    def set_thumbnails(self, thumbnails: list[QPixmap]) -> None:
        self.thumbnails = [item for item in thumbnails if not item.isNull()]
        self.update()

    def set_selection(self, start_ms: int, end_ms: int, *, emit: bool = True) -> None:
        start = max(0, min(int(start_ms), self.duration_ms - 250))
        end = max(start + 250, min(int(end_ms), self.duration_ms))
        changed = start != self.start_ms or end != self.end_ms
        self.start_ms = start
        self.end_ms = end
        self.playhead_ms = max(self.start_ms, min(self.playhead_ms, self.end_ms))
        self.update()
        if changed and emit:
            self.selectionChanged.emit(self.start_ms, self.end_ms)

    def set_playhead(self, position_ms: int) -> None:
        position = max(self.start_ms, min(int(position_ms), self.end_ms))
        if position != self.playhead_ms:
            self.playhead_ms = position
            self.update()

    def _track_rect(self) -> QRectF:
        return QRectF(18.0, 49.0, max(10.0, float(self.width()) - 36.0), 86.0)

    def _x_for_time(self, milliseconds: int) -> float:
        rect = self._track_rect()
        return rect.left() + (max(0, min(milliseconds, self.duration_ms)) / self.duration_ms) * rect.width()

    def _time_for_x(self, x: float) -> int:
        rect = self._track_rect()
        ratio = (x - rect.left()) / max(1.0, rect.width())
        return int(round(max(0.0, min(1.0, ratio)) * self.duration_ms))

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self._track_rect()

        header_font = QFont(painter.font())
        header_font.setPointSize(8)
        header_font.setBold(True)
        painter.setFont(header_font)
        painter.setPen(QColor("#DCE4EB"))
        painter.drawText(QRectF(rect.left(), 10, 170, 20), Qt.AlignmentFlag.AlignVCenter, "CLIP TIMELINE")
        painter.setPen(QColor("#91A0AD"))
        painter.drawText(
            QRectF(rect.right() - 190, 10, 190, 20),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{_clock(self.playhead_ms)} / {_clock(self.duration_ms)}",
        )

        ruler_y = 39.0
        painter.setPen(QPen(QColor("#364451"), 1.0))
        painter.drawLine(QPointF(rect.left(), ruler_y), QPointF(rect.right(), ruler_y))
        for index in range(9):
            ratio = index / 8.0
            x = rect.left() + ratio * rect.width()
            tick_height = 7 if index % 2 == 0 else 4
            painter.drawLine(QPointF(x, ruler_y), QPointF(x, ruler_y + tick_height))
            if index % 2 == 0:
                label = _clock(int(self.duration_ms * ratio))
                painter.setPen(QColor("#71808D"))
                label_width = painter.fontMetrics().horizontalAdvance(label)
                painter.drawText(QPointF(max(rect.left(), min(rect.right() - label_width, x - label_width / 2)), 34), label)
                painter.setPen(QPen(QColor("#364451"), 1.0))

        painter.setPen(QPen(QColor("#293744"), 1.0))
        painter.setBrush(QColor("#17212B"))
        painter.drawRoundedRect(rect, 9, 9)

        if self.thumbnails:
            tile_width = rect.width() / len(self.thumbnails)
            painter.save()
            for index, pixmap in enumerate(self.thumbnails):
                tile = QRectF(rect.left() + index * tile_width, rect.top(), tile_width + 0.8, rect.height())
                scaled = pixmap.scaled(
                    max(1, int(tile.width())),
                    max(1, int(tile.height())),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                source_x = max(0, (scaled.width() - int(tile.width())) // 2)
                source_y = max(0, (scaled.height() - int(tile.height())) // 2)
                source = QRectF(source_x, source_y, tile.width(), tile.height())
                painter.drawPixmap(tile, scaled, source)
                if index:
                    painter.setPen(QPen(QColor(0, 0, 0, 70), 1.0))
                    painter.drawLine(QPointF(tile.left(), tile.top()), QPointF(tile.left(), tile.bottom()))
            painter.restore()
        else:
            painter.setPen(QColor("#778692"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Loading preview frames…")

        start_x = self._x_for_time(self.start_ms)
        end_x = self._x_for_time(self.end_ms)
        playhead_x = self._x_for_time(self.playhead_ms)

        shade = QColor(4, 9, 14, 165)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shade)
        if start_x > rect.left():
            painter.drawRoundedRect(QRectF(rect.left(), rect.top(), start_x - rect.left(), rect.height()), 8, 8)
        if end_x < rect.right():
            painter.drawRoundedRect(QRectF(end_x, rect.top(), rect.right() - end_x, rect.height()), 8, 8)

        accent = QColor("#55D985")
        selection = QRectF(start_x, rect.top(), max(1.0, end_x - start_x), rect.height())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(accent, 2.5))
        painter.drawRoundedRect(selection, 7, 7)

        for x in (start_x, end_x):
            handle = QRectF(x - 6, rect.top() - 7, 12, rect.height() + 14)
            painter.setBrush(accent)
            painter.setPen(QPen(QColor("#A5F2C3"), 1.0))
            painter.drawRoundedRect(handle, 5, 5)
            painter.setPen(QPen(QColor("#183226"), 1.2))
            painter.drawLine(QPointF(x - 1.6, rect.center().y() - 8), QPointF(x - 1.6, rect.center().y() + 8))
            painter.drawLine(QPointF(x + 1.6, rect.center().y() - 8), QPointF(x + 1.6, rect.center().y() + 8))

        painter.setPen(QPen(QColor("#F4F7FA"), 1.5))
        painter.drawLine(QPointF(playhead_x, 30), QPointF(playhead_x, rect.bottom() + 8))
        painter.setBrush(QColor("#F4F7FA"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(playhead_x - 5, 30),
                    QPointF(playhead_x + 5, 30),
                    QPointF(playhead_x, 37),
                ]
            )
        )

        painter.setFont(header_font)
        painter.setPen(QColor("#55D985"))
        painter.drawText(QRectF(rect.left(), rect.bottom() + 12, 150, 22), Qt.AlignmentFlag.AlignLeft, f"IN  {_clock(self.start_ms)}")
        painter.drawText(QRectF(rect.right() - 150, rect.bottom() + 12, 150, 22), Qt.AlignmentFlag.AlignRight, f"OUT  {_clock(self.end_ms)}")
        painter.setPen(QColor("#71808D"))
        painter.drawText(
            QRectF(rect.left() + 155, rect.bottom() + 12, max(10.0, rect.width() - 310), 22),
            Qt.AlignmentFlag.AlignCenter,
            "Drag handles to trim · click or drag to seek",
        )

    def _handle_hit(self, x: float) -> str:
        if abs(x - self._x_for_time(self.start_ms)) <= 11:
            return "start"
        if abs(x - self._x_for_time(self.end_ms)) <= 11:
            return "end"
        return ""

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        track = self._track_rect().adjusted(-8, -15, 8, 15)
        if not track.contains(event.position()):
            super().mousePressEvent(event)
            return
        self._drag_mode = self._handle_hit(event.position().x()) or "playhead"
        self._apply_drag(event.position().x())
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_mode:
            self._apply_drag(event.position().x())
            event.accept()
            return
        handle = self._handle_hit(event.position().x())
        if handle and self._track_rect().adjusted(-8, -15, 8, 15).contains(event.position()):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif self._track_rect().adjusted(0, -15, 0, 15).contains(event.position()):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_mode:
            self._apply_drag(event.position().x())
            self._drag_mode = ""
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._drag_mode:
            self.unsetCursor()
        super().leaveEvent(event)

    def _apply_drag(self, x: float) -> None:
        value = self._time_for_x(x)
        if self._drag_mode == "start":
            self.set_selection(min(value, self.end_ms - 250), self.end_ms)
        elif self._drag_mode == "end":
            self.set_selection(self.start_ms, max(value, self.start_ms + 250))
        else:
            value = max(self.start_ms, min(value, self.end_ms))
            self.set_playhead(value)
            self.seekRequested.emit(value)
