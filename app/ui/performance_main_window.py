from __future__ import annotations

from collections import deque

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.services.performance_targets import TARGETS
from app.ui.enhanced_main_window import EnhancedMainWindow
from app.ui.main_window import GameCard


class PerformanceMainWindow(EnhancedMainWindow):
    """UI performance layer: lazy cards and background throttling."""

    INITIAL_CARD_BATCH = 18
    NEXT_CARD_BATCH = 12

    def __init__(self, config, controller, update_manager=None) -> None:
        self._performance_ready = False
        self._pending_library_refresh = False
        self._all_games = []
        self._pending_games = deque()
        self._expanding_for_filter = False
        super().__init__(config, controller, update_manager)

        self._performance_ready = True
        self.games_scroll.verticalScrollBar().valueChanged.connect(
            self._on_games_scroll
        )

    def refresh_clips(self) -> None:
        # Avoid rebuilding cards and loading thumbnails while the UI is hidden in
        # the tray. The recorder/exporter continue normally.
        if (
            self._performance_ready
            and not self.isVisible()
        ):
            self._pending_library_refresh = True
            return

        self._pending_library_refresh = False
        games = self.controller.games()
        self._all_games = list(games)
        clips = [clip for game in games for clip in game.clips]

        if hasattr(self, "storage_summary"):
            clip_word = "clip" if len(clips) == 1 else "clips"
            self.storage_summary.setText(f"{len(clips)} {clip_word}")

        self._clear_layout(self.games_layout)
        self._pending_games = deque(games)

        if not games:
            empty = QWidget()
            empty_layout = QVBoxLayout(empty)
            empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title = QLabel("No games with highlights yet")
            title.setObjectName("EmptyTitle")
            message = QLabel(
                f"Start League and press {self.config.hotkey_display}, "
                "or let automatic highlights save a play."
            )
            message.setObjectName("Muted")
            message.setWordWrap(True)
            empty_layout.addWidget(
                title,
                alignment=Qt.AlignmentFlag.AlignCenter,
            )
            empty_layout.addWidget(
                message,
                alignment=Qt.AlignmentFlag.AlignCenter,
            )
            self.games_layout.addWidget(empty, 1)
        else:
            self._append_game_batch(self.INITIAL_CARD_BATCH)

        self._sync_selected_game()

        if hasattr(self, "highlight_search"):
            QTimer.singleShot(0, self._after_library_refresh)

    def _append_game_batch(
        self,
        limit: int,
        *,
        apply_filters: bool = True,
    ) -> None:
        added = 0
        while self._pending_games and added < limit:
            game = self._pending_games.popleft()
            card = GameCard(game)
            card.clicked.connect(self._open_game)
            self.games_layout.addWidget(card)
            added += 1

        if not self._pending_games:
            self.games_layout.addStretch()

        if hasattr(self, "highlight_search"):
            self._decorate_game_cards()
            if apply_filters:
                super()._apply_highlight_filters()
                self._correct_unfiltered_count()

    def _on_games_scroll(self, value: int) -> None:
        if not self._pending_games or self._expanding_for_filter:
            return
        bar = self.games_scroll.verticalScrollBar()
        if bar.maximum() - value <= 600:
            self._append_game_batch(self.NEXT_CARD_BATCH)

    def _apply_highlight_filters(self, *_args) -> None:
        if not hasattr(self, "highlight_search"):
            return

        active_filter = (
            bool(self.highlight_search.text().strip())
            or self.highlight_filter.currentText() != "All matches"
        )
        if active_filter and self._pending_games and not self._expanding_for_filter:
            self._expanding_for_filter = True
            self._expand_for_filter()
            return

        super()._apply_highlight_filters()
        self._correct_unfiltered_count()

    def _expand_for_filter(self) -> None:
        if not self._pending_games:
            self._expanding_for_filter = False
            super()._apply_highlight_filters()
            return

        self._append_game_batch(24, apply_filters=False)
        QTimer.singleShot(0, self._expand_for_filter)

    def _correct_unfiltered_count(self) -> None:
        if not hasattr(self, "library_count_label"):
            return
        if (
            not self.highlight_search.text().strip()
            and self.highlight_filter.currentText() == "All matches"
        ):
            count = len(self._all_games)
            label = "match" if count == 1 else "matches"
            self.library_count_label.setText(f"{count} {label}")

    def _sync_selected_game(self) -> None:
        if self.selected_match_id is None:
            return
        selected = next(
            (
                game
                for game in self._all_games
                if game.match_id == self.selected_match_id
            ),
            None,
        )
        if selected is None:
            self._back_to_games()
        else:
            self._populate_game_detail(selected)

    def _set_ui_background_mode(self, background: bool) -> None:
        timer = getattr(self.controller, "clock_timer", None)
        if timer is None:
            return
        interval = (
            TARGETS.ui_background_interval_ms
            if background
            else TARGETS.ui_active_interval_ms
        )
        if timer.interval() != interval:
            timer.setInterval(interval)

    def hideEvent(self, event) -> None:
        self._set_ui_background_mode(True)
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        self._set_ui_background_mode(False)
        super().showEvent(event)
        if self._pending_library_refresh:
            QTimer.singleShot(0, self.refresh_clips)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._set_ui_background_mode(self.isMinimized())
