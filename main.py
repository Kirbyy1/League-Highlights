from __future__ import annotations

import logging
import os
import sys

os.environ["QT_FFMPEG_DEBUG"] = "0"
_silent_qt_media_rules = (
    "qt.multimedia.ffmpeg=false;"
    "qt.multimedia.ffmpeg.*=false;"
    "*.ffmpeg.*=false"
)
_existing_qt_rules = os.environ.get("QT_LOGGING_RULES", "").strip().rstrip(";")
os.environ["QT_LOGGING_RULES"] = (
    f"{_existing_qt_rules};{_silent_qt_media_rules}"
    if _existing_qt_rules
    else _silent_qt_media_rules
)

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from app.assets import icon_path
from app.config import AppConfig
import app.controller as controller_module
import app.ui.main_window as main_window_module
from app.logging_setup import configure_logging
from app.services.cached_clip_library import CachedClipLibrary
from app.services.league_events_v2 import LeagueEventMonitorV2
from app.services.reliable_clip_exporter import ReliableClipExporter
from app.services.reliable_ffmpeg import ReliableFfmpegTools
from app.services.reliable_video_recorder import ReliableVideoSegmentRecorder
from app.services.update_manager import UpdateManager
from app.ui.layout_style import LAYOUT_STYLE
from app.ui.optimized_inline_player import OptimizedInlineHighlightPlayer
from app.ui.performance_main_window import PerformanceMainWindow
from app.ui.polish_style import POLISH_STYLE
from app.ui.reference_card_layout_patch import install_reference_card_layout
from app.services.live_match_speed_patch import install_live_match_speedups
from app.ui.fake_match_debug import install_fake_match_debug
from app.ui.rank_record_ui_guard import install_rank_record_ui_guard
from app.ui.local_only_api_patch import (
    install_local_only_api_removal,
    prepare_local_only_config,
)

controller_module.FfmpegTools = ReliableFfmpegTools
controller_module.VideoSegmentRecorder = ReliableVideoSegmentRecorder
controller_module.ClipExporter = ReliableClipExporter
controller_module.ClipLibrary = CachedClipLibrary
controller_module.LeagueEventMonitor = LeagueEventMonitorV2

main_window_module.InlineHighlightPlayer = OptimizedInlineHighlightPlayer

install_live_match_speedups()
install_reference_card_layout()
install_rank_record_ui_guard()
install_fake_match_debug()
install_local_only_api_removal()

from app.controller_performance import PerformanceRecorderController


def main() -> int:
    if os.name != "nt":
        print("League Highlights currently supports Windows 10/11 only. Be careful")

    config = AppConfig.create_default()
    prepare_local_only_config(config)
    configure_logging(config.log_dir)
    logging.info("Starting League Highlights")

    app = QApplication(sys.argv)
    app.setApplicationName("League Highlights")
    app.setWindowIcon(QIcon(str(icon_path())))
    app.setQuitOnLastWindowClosed(False)
    app.setOrganizationName("LeagueHighlights")
    app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeMenuBar, True)

    try:
        controller = PerformanceRecorderController(config)
        update_manager = UpdateManager(config)
        window = PerformanceMainWindow(config, controller, update_manager)
        window.setStyleSheet(
            f"{window.styleSheet()}\n{POLISH_STYLE}\n{LAYOUT_STYLE}"
        )

        launched_at_startup = "--startup" in sys.argv
        if not (config.start_minimized or launched_at_startup):
            window.show()
        else:
            window.show_startup_notification()

        if update_manager.can_self_update:
            QTimer.singleShot(1800, update_manager.check_for_updates)
        return app.exec()
    except Exception as exc:
        logging.exception("Fatal startup error")
        QMessageBox.critical(None, "League Highlights", f"The app could not start:\n\n{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
