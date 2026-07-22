from __future__ import annotations

import logging
import os
import sys

# Qt Multimedia uses its bundled FFmpeg backend on Windows. Keep its developer
# diagnostics out of normal output. These variables must be configured before
# importing PySide6.
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

# RecorderController resolves these names from app.controller when an instance
# is created. Replacing the implementations here keeps the mature controller,
# settings, exporter signals, updater, and UI behavior intact.
controller_module.FfmpegTools = ReliableFfmpegTools
controller_module.VideoSegmentRecorder = ReliableVideoSegmentRecorder
controller_module.ClipExporter = ReliableClipExporter
controller_module.ClipLibrary = CachedClipLibrary
controller_module.LeagueEventMonitor = LeagueEventMonitorV2

# MainWindow resolves InlineHighlightPlayer from its module at runtime.
main_window_module.InlineHighlightPlayer = OptimizedInlineHighlightPlayer

from app.controller_performance import PerformanceRecorderController


def main() -> int:
    if os.name != "nt":
        print("League Highlights currently supports Windows 10/11 only. Be careful")

    config = AppConfig.create_default()
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
