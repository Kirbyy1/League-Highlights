from __future__ import annotations

import logging
import os
import sys

# Qt Multimedia uses its bundled FFmpeg backend on Windows. Keep its developer
# diagnostics out of the normal PyCharm/console output. These variables must be
# configured before importing PySide6.
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

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from app.assets import icon_path
from app.config import AppConfig
from app.controller import RecorderController
from app.logging_setup import configure_logging
from app.ui.main_window import MainWindow


def main() -> int:
    if os.name != "nt":
        print("League Highlights currently supports Windows 10/11 only.")
        return 1

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
        controller = RecorderController(config)
        window = MainWindow(config, controller)
        launched_at_startup = "--startup" in sys.argv
        if not (config.start_minimized or launched_at_startup):
            window.show()
        else:
            window.show_startup_notification()
        return app.exec()
    except Exception as exc:  # last-resort startup guard
        logging.exception("Fatal startup error")
        QMessageBox.critical(None, "League Highlights", f"The app could not start:\n\n{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
