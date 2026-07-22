from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.models import RecorderState
from app.ui.main_window import MainWindow


class EnhancedMainWindow(MainWindow):
    """Presentation and navigation enhancements layered over the stable app."""

    def __init__(self, config, controller, update_manager=None) -> None:
        super().__init__(config, controller, update_manager)

        self._enhance_highlights_page()
        self._enhance_settings_navigation()
        self._build_bottom_status_bar()

        # Keep the extra UI synchronized without replacing the existing handlers.
        self.controller.state_changed.connect(
            lambda *_args: self._refresh_bottom_status()
        )
        self.controller.event_status_changed.connect(
            lambda *_args: self._refresh_bottom_status()
        )
        self.controller.diagnostics_changed.connect(
            lambda *_args: self._refresh_bottom_status()
        )
        self.controller.hotkey_changed.connect(
            lambda *_args: self._refresh_bottom_status()
        )

        self._decorate_game_cards()
        self._apply_highlight_filters()
        self._refresh_bottom_status()

    # ------------------------------------------------------------------
    # Highlights library
    # ------------------------------------------------------------------

    def _enhance_highlights_page(self) -> None:
        panel = self.pages.widget(0)
        panel_layout = panel.layout()

        self.library_subtitle = QLabel(
            "Browse your recorded matches, find a play quickly, and open its highlights."
        )
        self.library_subtitle.setObjectName("LibrarySubtitle")

        toolbar = QFrame()
        toolbar.setObjectName("LibraryToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 8, 10, 8)
        toolbar_layout.setSpacing(8)

        self.library_count_label = QLabel("0 matches")
        self.library_count_label.setObjectName("LibraryCount")
        toolbar_layout.addWidget(self.library_count_label)
        toolbar_layout.addStretch()

        self.highlight_search = QLineEdit()
        self.highlight_search.setObjectName("LibrarySearch")
        self.highlight_search.setPlaceholderText("Search matches")
        self.highlight_search.setClearButtonEnabled(True)
        self.highlight_search.setMinimumWidth(250)
        self.highlight_search.setMaximumWidth(380)
        self.highlight_search.textChanged.connect(self._apply_highlight_filters)
        toolbar_layout.addWidget(self.highlight_search)

        self.highlight_filter = QComboBox()
        self.highlight_filter.setObjectName("LibraryFilter")
        self.highlight_filter.addItems(("All matches", "Victories", "Defeats"))
        self.highlight_filter.currentTextChanged.connect(
            self._apply_highlight_filters
        )
        toolbar_layout.addWidget(self.highlight_filter)

        open_folder = QPushButton("Open folder")
        open_folder.setObjectName("ToolbarButton")
        open_folder.clicked.connect(self._open_clip_folder)
        toolbar_layout.addWidget(open_folder)

        # Original order: header, highlights stack.
        panel_layout.insertWidget(1, self.library_subtitle)
        panel_layout.insertWidget(2, toolbar)

    def refresh_clips(self) -> None:
        # MainWindow.__init__ calls this before the enhancement widgets exist.
        super().refresh_clips()
        if hasattr(self, "highlight_search"):
            QTimer.singleShot(0, self._after_library_refresh)

    def _after_library_refresh(self) -> None:
        self._decorate_game_cards()
        self._apply_highlight_filters()

    def _game_widgets(self) -> list[QWidget]:
        if not hasattr(self, "games_layout"):
            return []
        widgets: list[QWidget] = []
        for index in range(self.games_layout.count()):
            item = self.games_layout.itemAt(index)
            widget = item.widget()
            if widget is not None and hasattr(widget, "game"):
                widgets.append(widget)
        return widgets

    def _decorate_game_cards(self) -> None:
        for card in self._game_widgets():
            if card.property("enhancedLibraryCard"):
                continue

            game = card.game
            outer = card.layout()
            if outer is None or outer.count() < 2:
                continue

            info_item = outer.itemAt(1)
            info_layout = info_item.layout()
            if info_layout is None:
                continue

            detail_parts = [
                part
                for part in (
                    getattr(game, "game_mode", ""),
                    f"{getattr(game, 'date_text', '')} at {getattr(game, 'time_text', '')}".strip(),
                )
                if part
            ]
            meta = QLabel("  •  ".join(detail_parts))
            meta.setObjectName("GameMetaLine")
            meta.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
            )
            info_layout.insertWidget(1, meta)

            highlights = getattr(game, "clip_count", 0)
            duration = getattr(game, "total_duration_text", "")
            count_text = (
                f"{highlights} highlight" if highlights == 1
                else f"{highlights} highlights"
            )
            if duration:
                count_text += f"\n{duration}"

            count = QLabel(count_text)
            count.setObjectName("GameHighlightCount")
            count.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            count.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
            )
            outer.insertWidget(max(outer.count() - 1, 0), count)
            card.setProperty("enhancedLibraryCard", True)

    def _apply_highlight_filters(self, *_args: Any) -> None:
        if not hasattr(self, "highlight_search"):
            return

        query = self.highlight_search.text().strip().casefold()
        selected_filter = self.highlight_filter.currentText()
        visible_count = 0
        total_count = 0

        for card in self._game_widgets():
            total_count += 1
            game = card.game
            result = str(getattr(game, "normalized_result", "") or "")
            result_folded = result.casefold()

            search_parts = (
                getattr(game, "title_text", ""),
                getattr(game, "game_mode", ""),
                getattr(game, "date_text", ""),
                getattr(game, "time_text", ""),
                result,
            )
            haystack = " ".join(str(part) for part in search_parts).casefold()

            search_matches = not query or query in haystack
            filter_matches = (
                selected_filter == "All matches"
                or (selected_filter == "Victories" and result_folded == "victory")
                or (selected_filter == "Defeats" and result_folded == "defeat")
            )
            visible = search_matches and filter_matches
            card.setVisible(visible)
            if visible:
                visible_count += 1

        if query or selected_filter != "All matches":
            self.library_count_label.setText(
                f"{visible_count} of {total_count} matches"
            )
        else:
            label = "match" if total_count == 1 else "matches"
            self.library_count_label.setText(f"{total_count} {label}")

    # ------------------------------------------------------------------
    # Settings navigation
    # ------------------------------------------------------------------

    def _enhance_settings_navigation(self) -> None:
        if not getattr(self, "settings_tab_buttons", None):
            return

        panel = self.pages.widget(1)
        panel_layout = panel.layout()
        old_tabs = self.settings_tab_buttons[0].parentWidget()

        panel_layout.removeWidget(old_tabs)
        panel_layout.removeWidget(self.settings_pages)

        body = QWidget()
        body.setObjectName("SettingsBody")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(14)

        navigation = QFrame()
        navigation.setObjectName("SettingsSideNavigation")
        navigation.setFixedWidth(188)
        navigation_layout = QVBoxLayout(navigation)
        navigation_layout.setContentsMargins(6, 8, 6, 8)
        navigation_layout.setSpacing(3)

        eyebrow = QLabel("SETTINGS")
        eyebrow.setObjectName("SettingsNavigationEyebrow")
        navigation_layout.addWidget(eyebrow)

        for button in self.settings_tab_buttons:
            button.setParent(navigation)
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed,
            )
            navigation_layout.addWidget(button)

        navigation_layout.addStretch()

        body_layout.addWidget(navigation)
        body_layout.addWidget(self.settings_pages, 1)
        panel_layout.addWidget(body, 1)

        old_tabs.deleteLater()

    # ------------------------------------------------------------------
    # Bottom status bar
    # ------------------------------------------------------------------

    def _build_bottom_status_bar(self) -> None:
        self.bottom_status_bar = QFrame()
        self.bottom_status_bar.setObjectName("BottomStatusBar")
        self.bottom_status_bar.setFixedHeight(31)

        layout = QHBoxLayout(self.bottom_status_bar)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)

        self.bottom_state_dot = QLabel()
        self.bottom_state_dot.setObjectName("BottomStateDot")
        self.bottom_state_text = QLabel("Waiting")
        self.bottom_state_text.setObjectName("BottomStateText")

        self.bottom_live_text = QLabel("League data: waiting")
        self.bottom_live_text.setObjectName("BottomStatusMuted")

        self.bottom_profile_text = QLabel()
        self.bottom_profile_text.setObjectName("BottomStatusMuted")

        self.bottom_audio_text = QLabel()
        self.bottom_audio_text.setObjectName("BottomStatusMuted")

        self.bottom_hotkey_text = QLabel()
        self.bottom_hotkey_text.setObjectName("BottomStatusMuted")

        layout.addWidget(self.bottom_state_dot)
        layout.addWidget(self.bottom_state_text)
        layout.addWidget(self._status_separator())
        layout.addWidget(self.bottom_live_text)
        layout.addStretch()
        layout.addWidget(self.bottom_profile_text)
        layout.addWidget(self._status_separator())
        layout.addWidget(self.bottom_audio_text)
        layout.addWidget(self._status_separator())
        layout.addWidget(self.bottom_hotkey_text)

        root_layout = self.centralWidget().layout()
        root_layout.addWidget(self.bottom_status_bar)

    @staticmethod
    def _status_separator() -> QLabel:
        separator = QLabel("•")
        separator.setObjectName("BottomStatusSeparator")
        return separator

    def _refresh_bottom_status(self) -> None:
        if not hasattr(self, "bottom_state_text"):
            return

        state = getattr(self.controller, "state", RecorderState.WAITING)
        state_value = getattr(state, "value", str(state))
        detail = str(getattr(self.controller, "detail", "") or "").strip()
        state_title = str(state_value).replace("_", " ").title()
        self.bottom_state_text.setText(
            f"{state_title} — {detail}" if detail else state_title
        )

        state_key = str(state_value).casefold()
        dot_state = (
            "recording" if "record" in state_key
            else "saving" if "saving" in state_key
            else "error" if "error" in state_key
            else "waiting"
        )
        self.bottom_state_dot.setProperty("state", dot_state)
        self.bottom_state_dot.style().unpolish(self.bottom_state_dot)
        self.bottom_state_dot.style().polish(self.bottom_state_dot)

        live_text = str(
            getattr(self.controller, "event_status_text", "Waiting for League data")
            or "Waiting for League data"
        )
        self.bottom_live_text.setText(live_text)

        self.bottom_profile_text.setText(
            f"{self.config.width}×{self.config.height} • {self.config.fps} FPS"
        )

        audio_parts: list[str] = []
        if self.config.system_audio_enabled:
            audio_parts.append("System audio")
        if self.config.microphone_enabled:
            audio_parts.append("Mic")
        self.bottom_audio_text.setText(
            " + ".join(audio_parts) if audio_parts else "Audio off"
        )
        self.bottom_hotkey_text.setText(
            f"Save clip: {self.config.hotkey_display}"
        )
