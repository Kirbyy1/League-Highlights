from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from app.services.live_match_benchmark import (
    clear_roster_fixture,
    fixture_status,
    format_result,
    result_path,
    run_benchmark,
    save_roster_fixture,
)


LOGGER = logging.getLogger(__name__)
BENCHMARK_UI_BUILD = "V14-LIVE-MATCH-BENCHMARK-SETTINGS"


class _BenchmarkBridge(QObject):
    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)


def _storage_settings_layout(window: Any) -> Any | None:
    pages = getattr(window, "settings_pages", None)
    if pages is None:
        return None
    buttons = list(getattr(window, "settings_tab_buttons", ()) or ())
    storage_index = -1
    for index, button in enumerate(buttons):
        try:
            if "storage" in str(button.text() or "").casefold():
                storage_index = index
                break
        except Exception:
            continue
    if storage_index < 0:
        storage_index = 3 if pages.count() > 3 else pages.count() - 1
    if storage_index < 0 or storage_index >= pages.count():
        return None
    page = pages.widget(storage_index)
    content = page.widget() if page is not None and hasattr(page, "widget") else page
    return content.layout() if content is not None else None


def _fixture_text(config: Any) -> str:
    status = fixture_status(config)
    if status["available"]:
        captured = status.get("captured_at_utc") or "unknown time"
        game_id = status.get("game_id") or "unknown game"
        return f"Saved 10-player roster available · game {game_id} · captured {captured}"
    return (
        "No saved 10-player roster yet. The next complete Live Match roster is stored "
        "automatically. Until then, the button runs a single-account LCU history stress test."
    )


def _install_settings(window: Any, config: Any) -> None:
    if getattr(window, "_live_match_benchmark_settings_installed", False):
        return
    layout = _storage_settings_layout(window)
    if layout is None:
        LOGGER.warning("Storage & app page was not found; benchmark controls were not added")
        return

    from PySide6.QtGui import QDesktopServices
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

    section = QFrame()
    section.setObjectName("SettingsSection")
    section_layout = QVBoxLayout(section)
    section_layout.setContentsMargins(20, 18, 20, 18)
    section_layout.setSpacing(11)

    title = QLabel("Live Match benchmark")
    title.setObjectName("SettingsTitle")
    description = QLabel(
        "Measure the real local rank and five-game history pipeline without entering a new game. "
        "With a saved roster it runs all 10 players, then measures the warm memory-cache pass."
    )
    description.setObjectName("CardMuted")
    description.setWordWrap(True)

    fixture_label = QLabel(_fixture_text(config))
    fixture_label.setObjectName("CardMuted")
    fixture_label.setWordWrap(True)

    status_label = QLabel("Ready")
    status_label.setObjectName("CardMuted")
    status_label.setWordWrap(True)

    buttons = QHBoxLayout()
    run_button = QPushButton("Run Live Match Benchmark")
    run_button.setObjectName("PrimaryButton")
    clear_button = QPushButton("Clear Saved Roster")
    clear_button.setObjectName("DarkButton")
    results_button = QPushButton("Open Result Folder")
    results_button.setObjectName("DarkButton")
    buttons.addWidget(run_button)
    buttons.addWidget(clear_button)
    buttons.addWidget(results_button)
    buttons.addStretch()

    detail = QLabel(
        "Requirements: keep the League Client open and signed in. The benchmark is blocked during "
        "Champion Select or an active game. Results are written to:\n"
        f"{result_path(config)}"
    )
    detail.setObjectName("CardMuted")
    detail.setWordWrap(True)

    section_layout.addWidget(title)
    section_layout.addWidget(description)
    section_layout.addWidget(fixture_label)
    section_layout.addLayout(buttons)
    section_layout.addWidget(status_label)
    section_layout.addWidget(detail)

    insert_index = max(0, layout.count() - 1)
    layout.insertWidget(insert_index, section)

    bridge = _BenchmarkBridge(window)
    window._live_match_benchmark_bridge = bridge

    def set_running(running: bool) -> None:
        run_button.setEnabled(not running)
        clear_button.setEnabled(not running)
        run_button.setText("Benchmark Running…" if running else "Run Live Match Benchmark")

    bridge.progress.connect(status_label.setText)

    def on_finished(result: Any) -> None:
        set_running(False)
        payload = dict(result) if isinstance(result, dict) else {}
        status_label.setText(format_result(payload))
        fixture_label.setText(_fixture_text(config))
        try:
            window._show_toast("BENCHMARK COMPLETE", format_result(payload))
        except Exception:
            pass

    def on_failed(message: str) -> None:
        set_running(False)
        status_label.setText(str(message))
        try:
            window._show_toast("BENCHMARK FAILED", str(message))
        except Exception:
            pass

    bridge.finished.connect(on_finished)
    bridge.failed.connect(on_failed)

    def run_clicked() -> None:
        page = getattr(window, "live_match_page", None)
        scout = getattr(page, "scout", None)
        if scout is None:
            on_failed("Live Match scout was not found")
            return
        set_running(True)
        status_label.setText("Starting benchmark…")

        def worker() -> None:
            try:
                result = run_benchmark(scout, config, bridge.progress.emit)
            except BaseException as exc:
                LOGGER.exception("Live Match benchmark failed")
                bridge.failed.emit(f"{type(exc).__name__}: {exc}")
            else:
                bridge.finished.emit(result)

        threading.Thread(
            target=worker,
            name="LeagueHighlightsBenchmark",
            daemon=True,
        ).start()

    def clear_clicked() -> None:
        removed = clear_roster_fixture(config)
        fixture_label.setText(_fixture_text(config))
        status_label.setText("Saved roster removed" if removed else "No saved roster was present")

    def open_results() -> None:
        folder = Path(result_path(config)).parent
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    run_button.clicked.connect(run_clicked)
    clear_button.clicked.connect(clear_clicked)
    results_button.clicked.connect(open_results)

    window.live_match_benchmark_button = run_button
    window.live_match_benchmark_status = status_label
    window._live_match_benchmark_settings_installed = True


def install_live_match_benchmark() -> None:
    """Capture complete rosters and add a repeatable benchmark to Settings."""

    from app.services.live_match_scout import LiveMatchScout

    if not getattr(LiveMatchScout, "_benchmark_fixture_capture_installed", False):
        original_discover = LiveMatchScout._discover_roster

        def discover_and_capture(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
            roster = original_discover(self, *args, **kwargs)
            players = list(roster.get("players", ()) or ()) if isinstance(roster, dict) else []
            if len(players) == 10:
                identities = []
                real_names = 0
                for player in players:
                    if not isinstance(player, dict):
                        continue
                    identity = str(
                        player.get("puuid", "")
                        or player.get("lcu_player_id", "")
                        or player.get("summoner_id", "")
                        or player.get("account_id", "")
                        or ""
                    ).strip().casefold()
                    identities.append(identity)
                    display = str(
                        player.get("riot_id", "")
                        or player.get("game_name", "")
                        or ""
                    ).strip()
                    if display and not re.fullmatch(r"player\s+\d+", display.casefold()):
                        real_names += 1
                if len(identities) != 10 or any(not identity for identity in identities):
                    return roster
                signature = (
                    str(roster.get("game_id", "") or "")
                    + "|"
                    + "|".join(sorted(identities))
                )
                previous_signature = str(
                    getattr(self, "_live_match_benchmark_saved_signature", "") or ""
                )
                previous_quality = int(getattr(self, "_live_match_benchmark_saved_quality", -1) or -1)
                should_save = bool(signature and (signature != previous_signature or real_names > previous_quality))
                if should_save:
                    try:
                        success, detail = save_roster_fixture(self.config, roster)
                        if success:
                            self._live_match_benchmark_fixture_path = detail
                            self._live_match_benchmark_saved_signature = signature
                            self._live_match_benchmark_saved_quality = real_names
                    except Exception:
                        LOGGER.debug("Could not save benchmark roster", exc_info=True)
            return roster

        LiveMatchScout._discover_roster = discover_and_capture
        LiveMatchScout._benchmark_fixture_capture_installed = True

    try:
        from app.ui.enhanced_main_window import EnhancedMainWindow
    except Exception:
        EnhancedMainWindow = None

    if EnhancedMainWindow is not None and not getattr(
        EnhancedMainWindow, "_live_match_benchmark_ui_installed", False
    ):
        original_init = EnhancedMainWindow.__init__

        def benchmark_window_init(self: Any, config: Any, *args: Any, **kwargs: Any) -> None:
            original_init(self, config, *args, **kwargs)
            _install_settings(self, config)

        EnhancedMainWindow.__init__ = benchmark_window_init
        EnhancedMainWindow._live_match_benchmark_ui_installed = True

    LOGGER.info("Live Match benchmark patch %s enabled", BENCHMARK_UI_BUILD)
