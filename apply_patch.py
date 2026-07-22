from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path


PATCH_ID = "updater-ui-v1"
PATCH_DIR = Path(__file__).resolve().parent
PAYLOAD_DIR = PATCH_DIR / "payload"


class PatchError(RuntimeError):
    pass


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PatchError(f"Required file is missing: {path}") from exc


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise PatchError(f"Anchor {label!r} was expected once but was found {count} times.")
    return text.replace(old, new, 1)


def backup_file(project: Path, backup_root: Path, relative: str) -> None:
    source = project / relative
    if not source.exists():
        return
    destination = backup_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def patch_main(project: Path) -> None:
    path = project / "main.py"
    text = read_text(path)
    if "from app.services.update_manager import UpdateManager" in text:
        return

    text = replace_once(
        text,
        "from PySide6.QtCore import Qt\n",
        "from PySide6.QtCore import Qt, QTimer\n",
        "main Qt import",
    )
    text = replace_once(
        text,
        "from app.logging_setup import configure_logging\nfrom app.ui.main_window import MainWindow\n",
        "from app.logging_setup import configure_logging\n"
        "from app.services.update_manager import UpdateManager\n"
        "from app.ui.main_window import MainWindow\n",
        "main updater import",
    )
    text = replace_once(
        text,
        "        controller = RecorderController(config)\n"
        "        window = MainWindow(config, controller)\n",
        "        controller = RecorderController(config)\n"
        "        update_manager = UpdateManager(config)\n"
        "        window = MainWindow(config, controller, update_manager)\n",
        "main manager construction",
    )
    text = replace_once(
        text,
        "        else:\n"
        "            window.show_startup_notification()\n"
        "        return app.exec()\n",
        "        else:\n"
        "            window.show_startup_notification()\n"
        "        if update_manager.can_self_update:\n"
        "            QTimer.singleShot(1800, update_manager.check_for_updates)\n"
        "        return app.exec()\n",
        "main automatic check",
    )
    write_text(path, text)


def patch_config(project: Path) -> None:
    path = project / "app/config.py"
    text = read_text(path)
    if "last_seen_whats_new_version" in text:
        return

    text = replace_once(
        text,
        "    close_to_tray: bool = True\n    draw_mouse: bool = False\n",
        "    close_to_tray: bool = True\n"
        "    last_seen_whats_new_version: str = \"\"\n"
        "    draw_mouse: bool = False\n",
        "config field",
    )
    text = replace_once(
        text,
        '            "close_to_tray",\n            "draw_mouse",\n',
        '            "close_to_tray",\n'
        '            "last_seen_whats_new_version",\n'
        '            "draw_mouse",\n',
        "config scalar key",
    )
    text = replace_once(
        text,
        "        if not isinstance(self.close_to_tray, bool):\n"
        "            self.close_to_tray = True\n"
        "        if not isinstance(self.system_audio_enabled, bool):\n",
        "        if not isinstance(self.close_to_tray, bool):\n"
        "            self.close_to_tray = True\n"
        "        if not isinstance(self.last_seen_whats_new_version, str):\n"
        "            self.last_seen_whats_new_version = \"\"\n"
        "        if not isinstance(self.system_audio_enabled, bool):\n",
        "config validation",
    )
    write_text(path, text)


def patch_main_window(project: Path) -> None:
    path = project / "app/ui/main_window.py"
    text = read_text(path)
    if "WhatsNewDialog" in text and "_bind_update_manager" in text:
        return

    text = replace_once(
        text,
        "from app.services.windows_startup import is_enabled as startup_is_enabled, set_enabled as set_startup_enabled\n"
        "from app.ui.styles import APP_STYLE\n"
        "from app.ui.inline_player import InlineHighlightPlayer\n",
        "from app.services.windows_startup import is_enabled as startup_is_enabled, set_enabled as set_startup_enabled\n"
        "from app.services.update_manager import UpdateInfo, UpdateManager\n"
        "from app.release_notes import notes_for_version\n"
        "from app.ui.styles import APP_STYLE\n"
        "from app.ui.inline_player import InlineHighlightPlayer\n"
        "from app.ui.whats_new_dialog import WhatsNewDialog\n"
        "from app.version import APP_VERSION\n",
        "main window imports",
    )
    text = replace_once(
        text,
        "    backRequested = Signal()\n    settingsRequested = Signal()\n",
        "    backRequested = Signal()\n"
        "    settingsRequested = Signal()\n"
        "    whatsNewRequested = Signal()\n",
        "title bar signal",
    )

    old_menu = '''        self.menu_button = QToolButton()\n        self.menu_button.setObjectName("MainMenuButton")\n        self.menu_button.setIcon(_app_icon("menu"))\n        self.menu_button.setIconSize(QSize(18, 18))\n        self.menu_button.setToolTip("Main menu")\n        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)\n\n        self.main_menu = QMenu(self.menu_button)\n        self.main_menu.setObjectName("MainMenu")\n        settings_action = QAction(_app_icon("settings"), "Settings", self.main_menu)\n        settings_action.triggered.connect(self.settingsRequested.emit)\n        self.main_menu.addAction(settings_action)\n        self.menu_button.setMenu(self.main_menu)\n'''
    new_menu = '''        self.menu_button = QToolButton()\n        self.menu_button.setObjectName("MainMenuButton")\n        self.menu_button.setIcon(_app_icon("menu"))\n        self.menu_button.setIconSize(QSize(18, 18))\n        self.menu_button.setToolTip("Main menu")\n\n        # Show the menu manually instead of attaching it as a QToolButton menu.\n        # This removes Qt's native menu-button subcontrol/pressed overlay.\n        self.main_menu = QMenu(self)\n        self.main_menu.setObjectName("MainMenu")\n        settings_action = QAction(_app_icon("settings"), "Settings", self.main_menu)\n        settings_action.triggered.connect(self.settingsRequested.emit)\n        self.main_menu.addAction(settings_action)\n        whats_new_action = QAction("What's new", self.main_menu)\n        whats_new_action.triggered.connect(self.whatsNewRequested.emit)\n        self.main_menu.addAction(whats_new_action)\n        self.menu_button.clicked.connect(self._show_main_menu)\n'''
    text = replace_once(text, old_menu, new_menu, "hamburger menu")
    text = replace_once(
        text,
        "    def show_default_context(self) -> None:\n",
        "    def _show_main_menu(self) -> None:\n"
        "        position = self.menu_button.mapToGlobal(QPoint(0, self.menu_button.height() + 4))\n"
        "        self.main_menu.popup(position)\n\n"
        "    def show_default_context(self) -> None:\n",
        "manual menu method",
    )
    text = replace_once(
        text,
        "class MainWindow(QMainWindow):\n"
        "    def __init__(self, config: AppConfig, controller: RecorderController) -> None:\n",
        "class MainWindow(QMainWindow):\n"
        "    def __init__(\n"
        "        self,\n"
        "        config: AppConfig,\n"
        "        controller: RecorderController,\n"
        "        update_manager: UpdateManager | None = None,\n"
        "    ) -> None:\n",
        "main window constructor",
    )
    text = replace_once(
        text,
        "        self._force_exit = False\n"
        "        self._tray_notice_shown = False\n"
        "        self._create_system_tray()\n",
        "        self._force_exit = False\n"
        "        self._tray_notice_shown = False\n"
        "        self.update_manager = update_manager\n"
        "        self._restart_after_update = False\n"
        "        self._update_launch_attempted = False\n"
        "        self._update_ready_notified = False\n"
        "        self._whats_new_scheduled = False\n"
        "        self._whats_new_dialog: WhatsNewDialog | None = None\n"
        "        self._create_system_tray()\n",
        "main window update fields",
    )
    text = replace_once(
        text,
        "        self.title_bar.backRequested.connect(self._back_to_games)\n"
        "        self.title_bar.settingsRequested.connect(lambda: self._show_page(1))\n",
        "        self.title_bar.backRequested.connect(self._back_to_games)\n"
        "        self.title_bar.settingsRequested.connect(lambda: self._show_page(1))\n"
        "        self.title_bar.whatsNewRequested.connect(lambda: self._show_whats_new(force=True))\n",
        "title bar what's new connection",
    )
    text = replace_once(
        text,
        "        self._sync_tray_state()\n\n    def _create_system_tray(self) -> None:\n",
        "        self._sync_tray_state()\n"
        "        self._bind_update_manager()\n\n"
        "    def _create_system_tray(self) -> None:\n",
        "bind update manager",
    )

    updates_block = '''        layout.addWidget(storage)\n\n        updates = QFrame()\n        updates.setObjectName("SettingsSection")\n        updates_layout = QVBoxLayout(updates)\n        updates_layout.setContentsMargins(20, 18, 20, 18)\n        updates_layout.setSpacing(10)\n        updates_title = QLabel("Updates")\n        updates_title.setObjectName("SettingsTitle")\n        installed_version = QLabel(f"Installed version {APP_VERSION}")\n        installed_version.setObjectName("SettingName")\n        self.update_status_label = QLabel(\n            "Updates are checked automatically. Downloads are verified and installed only after the app exits."\n        )\n        self.update_status_label.setObjectName("CardMuted")\n        self.update_status_label.setWordWrap(True)\n        update_actions = QHBoxLayout()\n        self.check_updates_button = QPushButton("Check for updates")\n        self.check_updates_button.setObjectName("DarkButton")\n        self.check_updates_button.clicked.connect(self._check_for_updates)\n        self.restart_update_button = QPushButton("Restart to update")\n        self.restart_update_button.setObjectName("PrimaryButton")\n        self.restart_update_button.clicked.connect(self._restart_to_update)\n        self.restart_update_button.hide()\n        update_actions.addWidget(self.check_updates_button)\n        update_actions.addWidget(self.restart_update_button)\n        update_actions.addStretch()\n        updates_layout.addWidget(updates_title)\n        updates_layout.addWidget(installed_version)\n        updates_layout.addWidget(self.update_status_label)\n        updates_layout.addLayout(update_actions)\n        layout.addWidget(updates)\n\n        recorder = QFrame()\n'''
    text = replace_once(
        text,
        "        layout.addWidget(storage)\n\n        recorder = QFrame()\n",
        updates_block,
        "updates settings section",
    )

    old_open_logs = '''    def _open_log_folder(self) -> None:\n        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.config.log_dir)))\n\n    def _set_launch_with_windows(self, checked: bool) -> None:\n'''
    new_open_logs = '''    def _open_log_folder(self) -> None:\n        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.config.log_dir)))\n\n    def _bind_update_manager(self) -> None:\n        if self.update_manager is None:\n            self.update_status_label.setText("Automatic updates are available in packaged builds.")\n            self.check_updates_button.setEnabled(False)\n            return\n        self.update_manager.status_changed.connect(self._on_update_status)\n        self.update_manager.update_available.connect(self._on_update_available)\n        self.update_manager.download_progress.connect(self._on_update_progress)\n        self.update_manager.update_ready.connect(self._on_update_ready)\n        self.update_manager.no_update.connect(self._on_no_update)\n        self.update_manager.error_occurred.connect(self._on_update_error)\n        pending = self.update_manager.pending_update\n        if pending is not None:\n            self._on_update_ready(pending)\n        else:\n            self.update_status_label.setText(self.update_manager.status_text)\n\n    def _check_for_updates(self) -> None:\n        if self.update_manager is None:\n            return\n        self.check_updates_button.setEnabled(False)\n        self.update_status_label.setText("Checking GitHub Releases for updates…")\n        self.update_manager.check_for_updates(manual=True)\n\n    def _on_update_status(self, message: str) -> None:\n        self.update_status_label.setText(message)\n\n    def _on_update_available(self, info: UpdateInfo) -> None:\n        self.update_status_label.setText(\n            f"Version {info.version} is available. Downloading and verifying it in the background…"\n        )\n\n    def _on_update_progress(self, percent: int, message: str) -> None:\n        progress = f"{max(0, min(100, int(percent)))}%" if percent > 0 else ""\n        self.update_status_label.setText(" — ".join(part for part in (message, progress) if part))\n\n    def _on_no_update(self, message: str) -> None:\n        self.check_updates_button.setEnabled(True)\n        self.update_status_label.setText(message)\n\n    def _on_update_error(self, message: str, manual: bool) -> None:\n        self.check_updates_button.setEnabled(True)\n        self.update_status_label.setText(message)\n        if manual:\n            QMessageBox.warning(self, "Update check", message)\n\n    def _on_update_ready(self, info: UpdateInfo) -> None:\n        self.check_updates_button.setEnabled(True)\n        self.restart_update_button.show()\n        self.update_status_label.setText(\n            f"Version {info.version} is ready. It will install after League Highlights fully exits."\n        )\n        if self.tray_icon.isVisible() and not self._update_ready_notified:\n            self._update_ready_notified = True\n            self.tray_icon.showMessage(\n                "League Highlights update ready",\n                f"Version {info.version} will install after the app exits.",\n                QSystemTrayIcon.MessageIcon.Information,\n                4500,\n            )\n\n    def _restart_to_update(self) -> None:\n        if self.update_manager is None or self.update_manager.pending_update is None:\n            QMessageBox.information(self, "League Highlights", "No staged update is ready yet.")\n            return\n        if self.controller.busy:\n            QMessageBox.information(\n                self,\n                "Update is waiting",\n                "A clip or export is still being processed. Try again after it finishes.",\n            )\n            return\n        if self.controller.recording:\n            result = QMessageBox.question(\n                self,\n                "Restart to update?",\n                "Recording will stop and the current rolling buffer will be discarded before the update installs.",\n                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,\n                QMessageBox.StandardButton.No,\n            )\n            if result != QMessageBox.StandardButton.Yes:\n                return\n        self._restart_after_update = True\n        self._exit_application()\n\n    def _launch_pending_update(self, restart: bool) -> None:\n        if self._update_launch_attempted or self.update_manager is None:\n            return\n        if self.update_manager.pending_update is None:\n            return\n        self._update_launch_attempted = True\n        if not self.update_manager.launch_pending_update(restart=restart):\n            self._update_launch_attempted = False\n            if restart:\n                QMessageBox.warning(\n                    self,\n                    "Update could not start",\n                    "The verified update remains staged. Exit normally and try again after checking the updater log.",\n                )\n\n    def _show_whats_new(self, force: bool = False) -> None:\n        if self._whats_new_dialog is not None and self._whats_new_dialog.isVisible():\n            self._whats_new_dialog.raise_()\n            return\n        if not force and self.config.last_seen_whats_new_version == APP_VERSION:\n            return\n        slides = notes_for_version(APP_VERSION)\n        if not slides:\n            return\n        dialog = WhatsNewDialog(self, APP_VERSION, slides)\n        self._whats_new_dialog = dialog\n        dialog.finished.connect(self._on_whats_new_closed)\n        dialog.open()\n\n    def _on_whats_new_closed(self, _result: int) -> None:\n        self.config.last_seen_whats_new_version = APP_VERSION\n        self.config.save_user_settings()\n        self._whats_new_dialog = None\n\n    def _set_launch_with_windows(self, checked: bool) -> None:\n'''
    text = replace_once(text, old_open_logs, new_open_logs, "update and what's new methods")

    text = replace_once(
        text,
        "    def changeEvent(self, event: QEvent) -> None:\n",
        "    def showEvent(self, event) -> None:\n"
        "        super().showEvent(event)\n"
        "        if not self._whats_new_scheduled:\n"
        "            self._whats_new_scheduled = True\n"
        "            QTimer.singleShot(450, self._show_whats_new)\n\n"
        "    def changeEvent(self, event: QEvent) -> None:\n",
        "show event what's new",
    )
    text = replace_once(
        text,
        "    def _exit_application(self) -> None:\n"
        "        self._force_exit = True\n"
        "        self.controller.shutdown()\n"
        "        self.tray_icon.hide()\n"
        "        QGuiApplication.quit()\n",
        "    def _exit_application(self) -> None:\n"
        "        self._force_exit = True\n"
        "        self.controller.shutdown()\n"
        "        self.tray_icon.hide()\n"
        "        self._launch_pending_update(self._restart_after_update)\n"
        "        QGuiApplication.quit()\n",
        "exit updater launch",
    )
    text = replace_once(
        text,
        "        if self._force_exit:\n"
        "            self.controller.shutdown()\n"
        "            self.tray_icon.hide()\n"
        "            event.accept()\n",
        "        if self._force_exit:\n"
        "            self.controller.shutdown()\n"
        "            self.tray_icon.hide()\n"
        "            self._launch_pending_update(self._restart_after_update)\n"
        "            event.accept()\n",
        "force close updater launch",
    )
    text = replace_once(
        text,
        "        self._force_exit = True\n"
        "        self.controller.shutdown()\n"
        "        self.tray_icon.hide()\n"
        "        event.accept()\n",
        "        self._force_exit = True\n"
        "        self.controller.shutdown()\n"
        "        self.tray_icon.hide()\n"
        "        self._launch_pending_update(False)\n"
        "        event.accept()\n",
        "normal close updater launch",
    )
    write_text(path, text)


def patch_styles(project: Path) -> None:
    path = project / "app/ui/styles.py"
    text = read_text(path)
    if "v51 updater, PyCharm geometry" in text:
        return
    addition = r"""

# v51 updater, PyCharm geometry, manual hamburger popup, and What's New carousel
APP_STYLE += r'''
QFrame#TitleBrandBadge {
    border-radius: 4px;
}
QFrame#StatusCard, QFrame#SettingsSection, QFrame#StorageCard, QFrame#HintCard,
QFrame#GameCard, QFrame#ClipCard, QFrame#InfoCard {
    border-radius: 5px;
}
QFrame#GameCardCompact {
    border-radius: 3px;
}
QPushButton#NavButton,
QPushButton#PrimaryButton, QPushButton#DarkButton, QPushButton#DangerButton,
QPushButton#SaveClipButton, QPushButton#HotkeyButton, QPushButton#QuietButton,
QPushButton#SettingsTab {
    border-radius: 4px;
}
QToolButton#TitleBackButton, QToolButton#MainMenuButton,
QToolButton#HeaderIconButton, QToolButton#SidebarIconButton,
QToolButton#OverlayActionButton, QToolButton#CardAction, QToolButton#CardPlay,
QToolButton#RatingButton {
    border-radius: 4px;
}
QComboBox, QDoubleSpinBox {
    border-radius: 4px;
}
QCheckBox::indicator {
    border-radius: 3px;
}
QMenu#MainMenu {
    border-radius: 4px;
    padding: 3px;
}
QMenu#MainMenu::item {
    border-radius: 3px;
}
QToolButton#MainMenuButton:pressed,
QToolButton#MainMenuButton::menu-button,
QToolButton#MainMenuButton::menu-button:hover,
QToolButton#MainMenuButton::menu-button:pressed {
    background: transparent;
    border: none;
}

QFrame#WhatsNewOverlay {
    background: rgba(4, 7, 10, 150);
    border: none;
}
QFrame#WhatsNewCard {
    background: #11161C;
    border: 1px solid #3A4A36;
    border-radius: 6px;
}
QLabel#WhatsNewHeader {
    color: #E9EEF3;
    font-size: 12px;
    font-weight: 650;
    letter-spacing: 0.7px;
}
QFrame#WhatsNewHero {
    background: #080C11;
    border: 1px solid #263128;
    border-radius: 4px;
}
QLabel#WhatsNewEyebrow {
    color: #7F8B97;
    font-size: 11px;
    font-weight: 650;
    letter-spacing: 1px;
}
QLabel#WhatsNewTitle {
    color: #F3F6F9;
    font-size: 20px;
    font-weight: 680;
}
QLabel#WhatsNewDescription {
    color: #9CA7B3;
    font-size: 13px;
    padding: 0 42px;
}
QLabel#WhatsNewBullets {
    color: #B6C0CA;
    background: #0D1319;
    border: 1px solid #202B35;
    border-radius: 4px;
    padding: 10px 14px;
}
QLabel#WhatsNewDot {
    min-width: 12px;
    max-width: 12px;
    color: #44505C;
    font-size: 9px;
}
QLabel#WhatsNewDot[active="true"] {
    color: #63DE8C;
}
QPushButton#WhatsNewArrow {
    min-width: 34px;
    max-width: 34px;
    min-height: 32px;
    max-height: 32px;
    background: #171F27;
    border: 1px solid #2D3944;
    border-radius: 4px;
    color: #DCE3EA;
    font-size: 22px;
}
QPushButton#WhatsNewArrow:hover {
    background: #1D2832;
    border-color: #45525E;
}
QPushButton#WhatsNewArrow:disabled {
    color: #4D5862;
    background: #11171D;
    border-color: #202932;
}
QPushButton#WhatsNewPrimary {
    min-height: 34px;
    padding: 0 16px;
    background: #58D889;
    border: 1px solid #6BE697;
    border-radius: 4px;
    color: #07110B;
    font-weight: 650;
}
QPushButton#WhatsNewPrimary:hover {
    background: #68E397;
}
QFrame#WhatsNewDivider {
    background: #242D35;
    border: none;
}
QLabel#WhatsNewFooter {
    color: #B8C1CA;
    font-size: 12px;
}
'''
"""
    write_text(path, text.rstrip() + addition + "\n")


def patch_build_script(project: Path) -> None:
    path = project / "build_exe.ps1"
    text = read_text(path)
    if "LeagueHighlightsUpdater" in text:
        return
    text = replace_once(
        text,
        'Write-Host "Build created in dist\\LeagueHighlights" -ForegroundColor Green\n',
        '''$UpdaterDist = Join-Path $PSScriptRoot "build\\updater-dist"\n$UpdaterWork = Join-Path $PSScriptRoot "build\\updater-work"\n$UpdaterSpec = Join-Path $PSScriptRoot "build\\updater-spec"\nRemove-Item $UpdaterDist -Recurse -Force -ErrorAction SilentlyContinue\nRemove-Item $UpdaterWork -Recurse -Force -ErrorAction SilentlyContinue\nRemove-Item $UpdaterSpec -Recurse -Force -ErrorAction SilentlyContinue\nNew-Item -ItemType Directory -Force -Path $UpdaterDist, $UpdaterWork, $UpdaterSpec | Out-Null\n\n& $Python -m PyInstaller `\n    --noconfirm `\n    --clean `\n    --windowed `\n    --onefile `\n    --name "LeagueHighlightsUpdater" `\n    --distpath "$UpdaterDist" `\n    --workpath "$UpdaterWork" `\n    --specpath "$UpdaterSpec" `\n    updater.py\n\nCopy-Item `\n    (Join-Path $UpdaterDist "LeagueHighlightsUpdater.exe") `\n    (Join-Path $PSScriptRoot "dist\\LeagueHighlights\\LeagueHighlightsUpdater.exe") `\n    -Force\n\n& $Python (Join-Path $PSScriptRoot "scripts\\make_release.py")\n$Version = (& $Python -c "from app.version import APP_VERSION; print(APP_VERSION)").Trim()\n$Iscc = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue\nif ($Iscc) {\n    & $Iscc.Source "/DMyAppVersion=$Version" (Join-Path $PSScriptRoot "installer\\LeagueHighlights.iss")\n} else {\n    Write-Host "Inno Setup was not found in PATH; the ZIP and update manifest were still created." -ForegroundColor Yellow\n}\n\nWrite-Host "Build created in dist\\LeagueHighlights" -ForegroundColor Green\nWrite-Host "Release assets created in release\\$Version" -ForegroundColor Green\n''',
        "build release pipeline",
    )
    write_text(path, text)


def payload_targets() -> tuple[str, ...]:
    return tuple(
        source.relative_to(PAYLOAD_DIR).as_posix()
        for source in sorted(PAYLOAD_DIR.rglob("*"))
        if source.is_file()
    )


def copy_payload(project: Path) -> None:
    for relative_text in payload_targets():
        source = PAYLOAD_DIR / relative_text
        destination = project / relative_text
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply the League Highlights updater/UI patch")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    args = parser.parse_args()
    project = args.project.expanduser().resolve()

    if not (project / "main.py").is_file() or not (project / "app/ui/main_window.py").is_file():
        raise PatchError(f"This does not look like the League Highlights project: {project}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = project / ".patch_backups" / f"{PATCH_ID}-{stamp}"
    patched_targets = (
        "main.py",
        "app/config.py",
        "app/ui/main_window.py",
        "app/ui/styles.py",
        "build_exe.ps1",
    )
    targets = tuple(dict.fromkeys((*patched_targets, *payload_targets())))
    existed_before = {relative: (project / relative).exists() for relative in targets}
    for relative in targets:
        backup_file(project, backup_root, relative)

    try:
        patch_main(project)
        patch_config(project)
        patch_main_window(project)
        patch_styles(project)
        patch_build_script(project)
        copy_payload(project)
    except Exception:
        for relative in targets:
            destination = project / relative
            backup = backup_root / relative
            if backup.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, destination)
            elif not existed_before[relative]:
                destination.unlink(missing_ok=True)
        raise

    marker = project / ".patch_backups" / f"{PATCH_ID}.applied"
    marker.write_text(f"Applied {stamp}\nBackup: {backup_root}\n", encoding="utf-8")
    print(f"Patch applied successfully to: {project}")
    print(f"Backup created at: {backup_root}")
    print("Run: python main.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PatchError as exc:
        print(f"Patch aborted: {exc}", file=sys.stderr)
        raise SystemExit(1)
