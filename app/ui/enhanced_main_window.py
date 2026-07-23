from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.models import RecorderState
from app.services.recording_policy import (
    RECORDING_SCOPE_ALL,
    RECORDING_SCOPE_EXCLUDE_ARAM,
    RECORDING_SCOPE_FLEX_ONLY,
    RECORDING_SCOPE_NORMAL_DRAFT_ONLY,
    RECORDING_SCOPE_RANKED_ONLY,
    RECORDING_SCOPE_SOLO_DUO_ONLY,
    install_recording_policy,
)
from app.ui.live_match_page import LiveMatchPage
from app.ui.main_window import MainWindow, RoundedThumbnail, _app_icon


class MatchSummaryThumbnail(RoundedThumbnail):
    """Single-frame match thumbnail with a lightweight highlight timeline."""

    def __init__(self, game, width: int = 196, height: int = 96) -> None:
        duration = getattr(game, "match_duration_text", "") or getattr(
            game, "total_duration_text", ""
        )
        super().__init__(game.thumbnail_path, duration, width, height)
        self.game = game

    def _marker_positions(self) -> list[float]:
        clips = list(getattr(self.game, "clips", ()) or ())
        if not clips:
            return []

        duration = max(float(getattr(self.game, "timeline_duration_seconds", 1.0)), 1.0)
        positions: list[float] = []
        for clip in clips:
            game_time = getattr(clip, "event_game_time", None)
            if game_time is not None:
                positions.append(max(0.0, min(float(game_time) / duration, 1.0)))

        if positions:
            return sorted(positions)

        count = len(clips)
        return [(index + 1) / (count + 1) for index in range(count)]

    def paintEvent(self, event) -> None:
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        shade_height = 28
        painter.setBrush(QColor(5, 9, 14, 150))
        painter.drawRect(
            QRectF(0, self.height() - shade_height, self.width(), shade_height)
        )

        left = 10.0
        right = float(self.width() - 66)
        y = float(self.height() - 13)
        track = QRectF(left, y - 1.5, max(12.0, right - left), 3.0)
        painter.setBrush(QColor(215, 224, 233, 95))
        painter.drawRoundedRect(track, 1.5, 1.5)

        painter.setBrush(QColor("#35C97B"))
        for position in self._marker_positions():
            marker_x = left + (right - left) * position
            painter.drawEllipse(QRectF(marker_x - 2.75, y - 2.75, 5.5, 5.5))

        result = str(getattr(self.game, "normalized_result", "") or "")
        if result in {"Victory", "Defeat"}:
            badge = QRectF(8, 8, 58, 21)
            painter.setBrush(QColor(5, 9, 14, 215))
            painter.drawRoundedRect(badge, 6, 6)
            painter.setPen(QColor("#55E891" if result == "Victory" else "#FF6672"))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, result.upper())


class EnhancedMainWindow(MainWindow):
    """Presentation and navigation enhancements layered over the stable app."""

    def __init__(self, config, controller, update_manager=None) -> None:
        self._recording_seconds = 0
        self.recording_policy = install_recording_policy(controller, config)
        super().__init__(config, controller, update_manager)

        self._install_live_match_page()
        self._add_recording_policy_settings()
        self._add_riot_api_settings()
        self._enhance_highlights_page()
        self._enhance_settings_navigation()
        self._build_bottom_status_bar()

        self.recording_policy.session_changed.connect(self._on_lcu_session_changed)
        self.recording_policy.identity_changed.connect(self._on_lcu_identity_changed)
        self.recording_policy.policy_status_changed.connect(
            lambda *_args: self._refresh_recording_policy_views()
        )

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
        self._refresh_recording_policy_views()

    # ------------------------------------------------------------------
    # Live Match
    # ------------------------------------------------------------------

    def _install_live_match_page(self) -> None:
        self.live_match_page = LiveMatchPage(self.config, self)
        self.live_match_page.settings_requested.connect(self._open_riot_api_settings)
        self.live_match_page_index = self.pages.addWidget(self.live_match_page)

        self.live_match_nav = QPushButton("Live Match")
        self.live_match_nav.setObjectName("NavButton")
        self.live_match_nav.setIcon(_app_icon("highlights"))
        self.live_match_nav.setIconSize(self.highlights_nav.iconSize())
        self.live_match_nav.setToolTip("Live Match")
        self.live_match_nav.clicked.connect(
            lambda: self._show_page(self.live_match_page_index)
        )

        sidebar_layout = self.sidebar.layout()
        sidebar_layout.insertWidget(1, self.live_match_nav)

    def _show_page(self, index: int) -> None:
        super()._show_page(index)
        if not hasattr(self, "live_match_nav"):
            return

        active = index == self.live_match_page_index
        self.live_match_nav.setProperty("active", active)
        self.live_match_nav.style().unpolish(self.live_match_nav)
        self.live_match_nav.style().polish(self.live_match_nav)
        if active:
            self.live_match_page.refresh_now()

    def _set_sidebar_compact(self, compact: bool) -> None:
        super()._set_sidebar_compact(compact)
        if hasattr(self, "live_match_nav"):
            self.live_match_nav.setText("" if compact else "Live Match")
            self.live_match_nav.setProperty("compact", compact)
            self.live_match_nav.style().unpolish(self.live_match_nav)
            self.live_match_nav.style().polish(self.live_match_nav)

    # ------------------------------------------------------------------
    # Recording availability
    # ------------------------------------------------------------------

    def _add_recording_policy_settings(self) -> None:
        recording_page = self.settings_pages.widget(0)
        content = recording_page.widget() if hasattr(recording_page, "widget") else None
        content_layout = content.layout() if content is not None else None
        if content_layout is None:
            return

        section = QFrame()
        section.setObjectName("SettingsSection")
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(20, 18, 20, 18)
        section_layout.setSpacing(14)

        title = QLabel("When to record")
        title.setObjectName("SettingsTitle")
        help_text = QLabel(
            "Choose which League games may start the rolling highlight buffer. "
            "Queue detection uses the local League Client API and does not consume "
            "your Riot API rate limit."
        )
        help_text.setObjectName("CardMuted")
        help_text.setWordWrap(True)
        section_layout.addWidget(title)
        section_layout.addWidget(help_text)
        section_layout.addWidget(self._divider())

        self.recording_enabled_checkbox = QCheckBox("Enable highlight recording")
        self.recording_enabled_checkbox.setChecked(
            bool(getattr(self.config, "recording_enabled", True))
        )
        self.recording_enabled_checkbox.setToolTip(
            "Turns video/audio capture, manual clips, and automatic highlights on or off. "
            "Live Match analysis continues to work."
        )
        section_layout.addWidget(self.recording_enabled_checkbox)

        scope_row = QHBoxLayout()
        scope_copy = QVBoxLayout()
        scope_name = QLabel("Games to record")
        scope_name.setObjectName("SettingName")
        scope_help = QLabel(
            "Choose all games, no ARAM, all ranked, Solo/Duo, Flex, or Normal Draft. "
            "The decision is made before FFmpeg and audio capture start."
        )
        scope_help.setObjectName("CardMuted")
        scope_help.setWordWrap(True)
        scope_copy.addWidget(scope_name)
        scope_copy.addWidget(scope_help)
        scope_row.addLayout(scope_copy, 1)

        self.recording_scope_combo = QComboBox()
        self.recording_scope_combo.addItem("All League games", RECORDING_SCOPE_ALL)
        self.recording_scope_combo.addItem(
            "All games except ARAM", RECORDING_SCOPE_EXCLUDE_ARAM
        )
        self.recording_scope_combo.addItem(
            "Ranked games only", RECORDING_SCOPE_RANKED_ONLY
        )
        self.recording_scope_combo.addItem(
            "Ranked Solo/Duo only", RECORDING_SCOPE_SOLO_DUO_ONLY
        )
        self.recording_scope_combo.addItem(
            "Ranked Flex only", RECORDING_SCOPE_FLEX_ONLY
        )
        self.recording_scope_combo.addItem(
            "Normal Draft only", RECORDING_SCOPE_NORMAL_DRAFT_ONLY
        )
        current_scope = str(
            getattr(self.config, "recording_scope", RECORDING_SCOPE_ALL)
            or RECORDING_SCOPE_ALL
        )
        current_index = self.recording_scope_combo.findData(current_scope)
        self.recording_scope_combo.setCurrentIndex(
            current_index if current_index >= 0 else 0
        )
        self.recording_scope_combo.setEnabled(
            self.recording_enabled_checkbox.isChecked()
        )
        scope_row.addWidget(self.recording_scope_combo)
        section_layout.addLayout(scope_row)

        self.recording_skip_custom_checkbox = QCheckBox("Skip custom games")
        self.recording_skip_custom_checkbox.setChecked(
            bool(getattr(self.config, "recording_skip_custom_games", False))
        )
        self.recording_skip_custom_checkbox.setToolTip(
            "Prevents custom lobbies from starting the recording buffer."
        )
        section_layout.addWidget(self.recording_skip_custom_checkbox)

        self.recording_skip_arena_checkbox = QCheckBox("Skip Arena")
        self.recording_skip_arena_checkbox.setChecked(
            bool(getattr(self.config, "recording_skip_arena", False))
        )
        self.recording_skip_arena_checkbox.setToolTip(
            "Prevents Arena queues from starting the recording buffer."
        )
        section_layout.addWidget(self.recording_skip_arena_checkbox)

        self.lcu_lifecycle_status = QLabel()
        self.lcu_lifecycle_status.setObjectName("CardMuted")
        self.lcu_lifecycle_status.setWordWrap(True)
        section_layout.addWidget(self.lcu_lifecycle_status)

        self.recording_policy_status = QLabel()
        self.recording_policy_status.setObjectName("InfoBanner")
        self.recording_policy_status.setWordWrap(True)
        section_layout.addWidget(self.recording_policy_status)

        self.recording_enabled_checkbox.toggled.connect(
            self._recording_policy_changed
        )
        self.recording_scope_combo.currentIndexChanged.connect(
            self._recording_policy_changed
        )
        self.recording_skip_custom_checkbox.toggled.connect(
            self._recording_policy_changed
        )
        self.recording_skip_arena_checkbox.toggled.connect(
            self._recording_policy_changed
        )
        self._refresh_recording_policy_status()

        insert_index = max(0, content_layout.count() - 1)
        content_layout.insertWidget(insert_index, section)

    def _recording_policy_changed(self, *_args: Any) -> None:
        enabled = self.recording_enabled_checkbox.isChecked()
        self.recording_scope_combo.setEnabled(enabled)
        self.recording_skip_custom_checkbox.setEnabled(enabled)
        self.recording_skip_arena_checkbox.setEnabled(enabled)
        scope = str(
            self.recording_scope_combo.currentData() or RECORDING_SCOPE_ALL
        )
        changed = self.recording_policy.apply(
            enabled,
            scope,
            self.recording_skip_custom_checkbox.isChecked(),
            self.recording_skip_arena_checkbox.isChecked(),
        )
        self._refresh_recording_policy_status()
        self._refresh_bottom_status()
        self._on_state_changed(self.controller.state, self.controller.detail)
        if changed:
            if not enabled:
                message = (
                    "Highlight recording is off. Live Match analysis remains active."
                )
            elif scope == RECORDING_SCOPE_RANKED_ONLY:
                message = "Only Ranked Solo/Duo and Ranked Flex will be recorded."
            elif scope == RECORDING_SCOPE_EXCLUDE_ARAM:
                message = "ARAM games will be skipped automatically."
            elif scope == RECORDING_SCOPE_SOLO_DUO_ONLY:
                message = "Only Ranked Solo/Duo will be recorded."
            elif scope == RECORDING_SCOPE_FLEX_ONLY:
                message = "Only Ranked Flex will be recorded."
            elif scope == RECORDING_SCOPE_NORMAL_DRAFT_ONLY:
                message = "Only Normal Draft will be recorded."
            else:
                message = "All League game modes may be recorded."
            self._show_toast("RECORDING FILTER UPDATED", message)

    def _refresh_recording_policy_status(self) -> None:
        if not hasattr(self, "recording_policy_status"):
            return
        enabled = self.recording_enabled_checkbox.isChecked()
        scope_label = self.recording_scope_combo.currentText()
        extras: list[str] = []
        if self.recording_skip_custom_checkbox.isChecked():
            extras.append("custom games skipped")
        if self.recording_skip_arena_checkbox.isChecked():
            extras.append("Arena skipped")

        if not enabled:
            text = (
                "Recording disabled — no video buffer or highlights will be created. "
                "Live Match and LCU lifecycle detection continue normally."
            )
        else:
            text = f"Recording filter: {scope_label}."
            if extras:
                text += " Additional rules: " + ", ".join(extras) + "."
        self.recording_policy_status.setText(text)

        if hasattr(self, "lcu_lifecycle_status"):
            self.lcu_lifecycle_status.setText(
                "League lifecycle: " + self.recording_policy.lifecycle_text()
            )

    def _refresh_recording_policy_views(self) -> None:
        self._refresh_recording_policy_status()
        self._refresh_riot_api_status()
        self._refresh_bottom_status()

    def _on_lcu_session_changed(self, _snapshot: object) -> None:
        self._refresh_recording_policy_views()

    def _on_lcu_identity_changed(self, identity: object) -> None:
        platform = str(getattr(self.config, "riot_platform", "") or "")
        if hasattr(self, "riot_platform_combo") and platform:
            index = self.riot_platform_combo.findData(platform)
            if index >= 0:
                self.riot_platform_combo.setCurrentIndex(index)
        self._refresh_recording_policy_views()
        if hasattr(self, "live_match_page"):
            self.live_match_page.update_credentials()

    # ------------------------------------------------------------------
    # Riot API settings
    # ------------------------------------------------------------------

    def _add_riot_api_settings(self) -> None:
        page_index = self.settings_pages.count()
        button = QPushButton("Riot API")
        button.setObjectName("SettingsTab")
        button.setProperty("active", False)
        button.clicked.connect(
            lambda checked=False, page=page_index: self._show_settings_section(page)
        )

        tabs_layout = self.settings_tab_buttons[0].parentWidget().layout()
        tabs_layout.insertWidget(max(0, tabs_layout.count() - 1), button)
        self.settings_tab_buttons.append(button)
        self.settings_pages.addWidget(self._build_riot_api_settings())
        self.riot_settings_index = page_index

    def _build_riot_api_settings(self) -> QWidget:
        scroll, layout = self._settings_scroll_page()

        section = QFrame()
        section.setObjectName("SettingsSection")
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(20, 18, 20, 18)
        section_layout.setSpacing(14)

        title = QLabel("Riot Games API")
        title.setObjectName("SettingsTitle")
        help_text = QLabel(
            "Live Match reads the ten players from the local game client, then uses "
            "your own Riot API key to load Solo/Duo rank, LP, wins, and losses."
        )
        help_text.setObjectName("CardMuted")
        help_text.setWordWrap(True)
        section_layout.addWidget(title)
        section_layout.addWidget(help_text)
        section_layout.addWidget(self._divider())

        key_label = QLabel("API key")
        key_label.setObjectName("SettingName")
        key_help = QLabel(
            "Development keys begin with RGAPI- and normally expire every 24 hours. "
            "The key is saved only in this PC's League Highlights settings."
        )
        key_help.setObjectName("CardMuted")
        key_help.setWordWrap(True)
        section_layout.addWidget(key_label)
        section_layout.addWidget(key_help)

        key_row = QHBoxLayout()
        self.riot_api_key_input = QLineEdit()
        self.riot_api_key_input.setObjectName("RiotApiKeyInput")
        self.riot_api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.riot_api_key_input.setPlaceholderText("RGAPI-...")
        self.riot_api_key_input.setText(self.config.riot_api_key)
        key_row.addWidget(self.riot_api_key_input, 1)

        clear_key = QPushButton("Clear")
        clear_key.setObjectName("DarkButton")
        clear_key.clicked.connect(self.riot_api_key_input.clear)
        key_row.addWidget(clear_key)
        section_layout.addLayout(key_row)

        show_key = QCheckBox("Show API key")
        show_key.toggled.connect(
            lambda checked: self.riot_api_key_input.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        section_layout.addWidget(show_key)
        section_layout.addWidget(self._divider())

        self.auto_detect_riot_account_checkbox = QCheckBox(
            "Detect account and server from the League Client"
        )
        self.auto_detect_riot_account_checkbox.setChecked(
            bool(getattr(self.config, "auto_detect_riot_account", True))
        )
        self.auto_detect_riot_account_checkbox.setToolTip(
            "Reads your logged-in Riot ID, PUUID, platform and locale from the local client."
        )
        self.auto_detect_riot_account_checkbox.toggled.connect(
            self._sync_riot_region_controls
        )
        section_layout.addWidget(self.auto_detect_riot_account_checkbox)

        self.detected_riot_account_status = QLabel()
        self.detected_riot_account_status.setObjectName("CardMuted")
        self.detected_riot_account_status.setWordWrap(True)
        section_layout.addWidget(self.detected_riot_account_status)

        region_row = QHBoxLayout()
        region_copy = QVBoxLayout()
        region_name = QLabel("League server")
        region_name.setObjectName("SettingName")
        region_help = QLabel("Choose the platform where your account and live matches are played.")
        region_help.setObjectName("CardMuted")
        region_copy.addWidget(region_name)
        region_copy.addWidget(region_help)
        region_row.addLayout(region_copy, 1)

        self.riot_platform_combo = QComboBox()
        regions = (
            ("EU West", "euw1"),
            ("EU Nordic & East", "eun1"),
            ("North America", "na1"),
            ("Korea", "kr"),
            ("Japan", "jp1"),
            ("Brazil", "br1"),
            ("LAN", "la1"),
            ("LAS", "la2"),
            ("Turkey", "tr1"),
            ("Russia", "ru"),
            ("Middle East", "me1"),
            ("Oceania", "oc1"),
            ("Philippines", "ph2"),
            ("Singapore", "sg2"),
            ("Thailand", "th2"),
            ("Taiwan", "tw2"),
            ("Vietnam", "vn2"),
        )
        for label, platform in regions:
            self.riot_platform_combo.addItem(label, platform)
        current_index = self.riot_platform_combo.findData(self.config.riot_platform)
        self.riot_platform_combo.setCurrentIndex(current_index if current_index >= 0 else 0)
        region_row.addWidget(self.riot_platform_combo)
        section_layout.addLayout(region_row)
        self._sync_riot_region_controls()

        actions = QHBoxLayout()
        get_key = QPushButton("Get a Riot API key")
        get_key.setObjectName("DarkButton")
        get_key.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://developer.riotgames.com/"))
        )
        save = QPushButton("Save Riot API settings")
        save.setObjectName("PrimaryButton")
        save.clicked.connect(self._save_riot_api_settings)
        actions.addWidget(get_key)
        actions.addStretch()
        actions.addWidget(save)
        section_layout.addLayout(actions)

        self.riot_api_status = QLabel()
        self.riot_api_status.setObjectName("InfoBanner")
        self.riot_api_status.setWordWrap(True)
        self._refresh_riot_api_status()
        section_layout.addWidget(self.riot_api_status)

        layout.addWidget(section)
        layout.addStretch()
        return scroll

    def _save_riot_api_settings(self) -> None:
        key = self.riot_api_key_input.text().strip()
        if key and not key.startswith("RGAPI-"):
            result = QMessageBox.question(
                self,
                "Save Riot API key?",
                "This key does not begin with RGAPI-. Save it anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if result != QMessageBox.StandardButton.Yes:
                return

        self.config.riot_api_key = key
        auto_detect = self.auto_detect_riot_account_checkbox.isChecked()
        self.config.auto_detect_riot_account = auto_detect
        if not auto_detect or not str(getattr(self.config, "detected_riot_platform", "") or ""):
            self.config.riot_platform = str(
                self.riot_platform_combo.currentData() or "euw1"
            )
        self.config.save_user_settings()
        self.recording_policy.set_auto_detect_identity(auto_detect)
        self._refresh_riot_api_status()
        self.live_match_page.update_credentials()
        self._show_toast(
            "RIOT API SETTINGS UPDATED",
            "Live Match will refresh using your saved key and detected League account."
            if key and auto_detect
            else "Live Match will refresh using your saved key and selected server."
            if key
            else "The API key was cleared. Live Match will still show the local roster.",
        )

    def _sync_riot_region_controls(self, *_args: Any) -> None:
        if not hasattr(self, "riot_platform_combo"):
            return
        automatic = self.auto_detect_riot_account_checkbox.isChecked()
        self.riot_platform_combo.setEnabled(not automatic)
        if automatic:
            platform = str(getattr(self.config, "riot_platform", "") or "")
            index = self.riot_platform_combo.findData(platform)
            if index >= 0:
                self.riot_platform_combo.setCurrentIndex(index)

    def _refresh_riot_api_status(self) -> None:
        if not hasattr(self, "riot_api_status"):
            return
        identity_text = self.recording_policy.identity_text()
        automatic = bool(getattr(self.config, "auto_detect_riot_account", True))
        if hasattr(self, "auto_detect_riot_account_checkbox"):
            automatic = self.auto_detect_riot_account_checkbox.isChecked()
        if hasattr(self, "detected_riot_account_status"):
            self.detected_riot_account_status.setText(
                f"Detected account: {identity_text}"
                if identity_text
                else "Detected account: waiting for the League Client"
            )

        key_configured = bool(self.riot_api_key_input.text().strip())
        if key_configured and automatic:
            self.riot_api_status.setText(
                "API key configured. Account and server follow the logged-in League Client automatically."
            )
        elif key_configured:
            self.riot_api_status.setText(
                "API key configured. The manually selected server will be used."
            )
        elif automatic:
            self.riot_api_status.setText(
                "No API key configured. Account and server are detected locally, but ranked stats stay hidden."
            )
        else:
            self.riot_api_status.setText(
                "No API key configured. Live Match can show the local roster, but ranked stats stay hidden."
            )

    def _open_riot_api_settings(self) -> None:
        self._show_page(1)
        self._show_settings_section(self.riot_settings_index)

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

            thumbnail_item = outer.itemAt(0)
            old_thumbnail = thumbnail_item.widget()
            if old_thumbnail is not None:
                replacement_thumbnail = MatchSummaryThumbnail(game, 196, 96)
                replacement_thumbnail.setAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
                )
                outer.replaceWidget(old_thumbnail, replacement_thumbnail)
                old_thumbnail.deleteLater()

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
    # Recording timer
    # ------------------------------------------------------------------

    def _on_state_changed(self, state: RecorderState, detail: str) -> None:
        """Keep the compatibility timer out of the navigation sidebar."""

        super()._on_state_changed(state, detail)
        self.status_time.hide()

        if hasattr(self, "bottom_recording_time"):
            self._refresh_bottom_status()

    def _update_recording_time(self, seconds: int) -> None:
        """Display session duration in the footer instead of the sidebar."""

        self._recording_seconds = max(0, int(seconds))
        self.status_time.hide()

        if hasattr(self, "bottom_recording_time"):
            self.bottom_recording_time.setText(
                self._format_recording_time(self._recording_seconds)
            )
            self._refresh_recording_timer_visibility()

    @staticmethod
    def _format_recording_time(seconds: int) -> str:
        seconds = max(0, int(seconds))
        return (
            f"{seconds // 3600:02d}:"
            f"{(seconds % 3600) // 60:02d}:"
            f"{seconds % 60:02d}"
        )

    def _refresh_recording_timer_visibility(self) -> None:
        if not hasattr(self, "bottom_recording_time"):
            return

        state = getattr(self.controller, "state", RecorderState.WAITING)
        visible = bool(
            getattr(self.controller, "recording", False)
            or state in {RecorderState.STARTING, RecorderState.SAVING}
        )
        self.bottom_recording_separator.setVisible(visible)
        self.bottom_recording_time.setVisible(visible)

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

        self.bottom_recording_separator = self._status_separator()
        self.bottom_recording_time = QLabel(
            self._format_recording_time(self._recording_seconds)
        )
        self.bottom_recording_time.setObjectName("BottomStatusMuted")
        self.bottom_recording_time.setToolTip("Current recording session duration")
        self.bottom_recording_separator.hide()
        self.bottom_recording_time.hide()

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
        layout.addWidget(self.bottom_recording_separator)
        layout.addWidget(self.bottom_recording_time)
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

        self.bottom_recording_time.setText(
            self._format_recording_time(self._recording_seconds)
        )
        self._refresh_recording_timer_visibility()

        live_text = self.recording_policy.lifecycle_text()
        identity_text = self.recording_policy.identity_text()
        if identity_text:
            live_text = f"{live_text} · {identity_text}"
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
