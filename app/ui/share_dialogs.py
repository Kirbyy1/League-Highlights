from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.assets import icon_path, logo_path
from app.controller import RecorderController
from app.models import format_file_size
from app.ui.styles import APP_STYLE


def _logo_label(size: int = 64) -> QLabel:
    label = QLabel()
    label.setFixedSize(size, size)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    pixmap = QPixmap(str(logo_path()))
    if not pixmap.isNull():
        label.setPixmap(
            pixmap.scaled(
                size,
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
    return label


class _BaseDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setWindowIcon(QIcon(str(icon_path())))
        self.setStyleSheet(APP_STYLE)
        self.setObjectName("ShareDialog")


class ShareChoiceDialog(_BaseDialog):
    """Small destination chooser shown only after Share / Export is pressed."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.choice = ""
        self.setWindowTitle("Share / Export")
        self.setFixedWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)

        header = QHBoxLayout()
        header.addWidget(_logo_label(58))
        copy = QVBoxLayout()
        title = QLabel("Share / Export")
        title.setObjectName("DialogTitle")
        subtitle = QLabel("Choose what you need. Nothing is uploaded unless you select Discord and send it.")
        subtitle.setObjectName("CardMuted")
        subtitle.setWordWrap(True)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        header.addLayout(copy, 1)
        layout.addLayout(header)

        save_button = QPushButton("Save file\nHigh-quality MP4, ready to use anywhere")
        save_button.setObjectName("ShareChoiceButton")
        save_button.setMinimumHeight(72)
        save_button.clicked.connect(lambda: self._choose("save"))
        layout.addWidget(save_button)

        discord_button = QPushButton("Discord\nCreate a small Discord-ready MP4; sending is optional")
        discord_button.setObjectName("ShareChoiceButton")
        discord_button.setMinimumHeight(72)
        discord_button.clicked.connect(lambda: self._choose("discord"))
        layout.addWidget(discord_button)

        cancel = QPushButton("Cancel")
        cancel.setObjectName("QuietButton")
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel, alignment=Qt.AlignmentFlag.AlignRight)

    def _choose(self, value: str) -> None:
        self.choice = value
        self.accept()


class DiscordDestinationDialog(_BaseDialog):
    """Choose file-only or webhook delivery without exposing setup on the main UI."""

    def __init__(
        self,
        connected: bool,
        prediction: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.choice = ""
        self.setWindowTitle("Discord")
        self.setFixedWidth(500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.addWidget(_logo_label(54))
        copy = QVBoxLayout()
        title = QLabel("Discord")
        title.setObjectName("DialogTitle")
        status = QLabel(
            "A Discord connection is saved on this PC."
            if connected
            else "You do not need a webhook to get a Discord-ready file."
        )
        status.setObjectName("CardMuted")
        status.setWordWrap(True)
        copy.addWidget(title)
        copy.addWidget(status)
        header.addLayout(copy, 1)
        layout.addLayout(header)

        if prediction:
            prediction_label = QLabel(prediction)
            prediction_label.setObjectName("ExportPredictionCard")
            prediction_label.setWordWrap(True)
            layout.addWidget(prediction_label)

        save_only = QPushButton("Save Discord-ready file\nCreate the MP4 and show it in its folder")
        save_only.setObjectName("ShareChoiceButton")
        save_only.setMinimumHeight(68)
        save_only.clicked.connect(lambda: self._choose("discord_file"))
        layout.addWidget(save_only)

        send = QPushButton(
            "Send to Discord\nUse the saved webhook connection"
            if connected
            else "Connect webhook & send\nOnly for users who can create a channel webhook"
        )
        send.setObjectName("PrimaryChoiceButton")
        send.setMinimumHeight(68)
        send.clicked.connect(lambda: self._choose("discord_send" if connected else "discord_setup"))
        layout.addWidget(send)

        if connected:
            forget = QPushButton("Change or remove Discord connection")
            forget.setObjectName("QuietButton")
            forget.clicked.connect(lambda: self._choose("discord_manage"))
            layout.addWidget(forget)

        cancel = QPushButton("Cancel")
        cancel.setObjectName("QuietButton")
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel, alignment=Qt.AlignmentFlag.AlignRight)

    def _choose(self, value: str) -> None:
        self.choice = value
        self.accept()


class DiscordSetupDialog(_BaseDialog):
    """First-use webhook setup that appears only after the user chooses to send."""

    def __init__(self, controller: RecorderController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.connected = False
        self._testing = False
        self.setWindowTitle("Connect Discord")
        self.setFixedWidth(530)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(13)

        header = QHBoxLayout()
        header.addWidget(_logo_label(58))
        copy = QVBoxLayout()
        title = QLabel("Connect Discord")
        title.setObjectName("DialogTitle")
        subtitle = QLabel("Paste a channel webhook URL. It is encrypted for your Windows account and never written to logs.")
        subtitle.setObjectName("CardMuted")
        subtitle.setWordWrap(True)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        header.addLayout(copy, 1)
        layout.addLayout(header)

        self.webhook_input = QLineEdit()
        self.webhook_input.setPlaceholderText("https://discord.com/api/webhooks/…")
        self.webhook_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.webhook_input.textChanged.connect(self._sync_buttons)
        layout.addWidget(self.webhook_input)

        self.status = QLabel("The webhook can post only to the channel it belongs to.")
        self.status.setObjectName("CardMuted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        actions = QHBoxLayout()
        test = QPushButton("Test")
        test.setObjectName("DarkButton")
        test.clicked.connect(lambda: self._start_test(connect_after=False))
        self.test_button = test
        connect = QPushButton("Connect & send")
        connect.setObjectName("PrimaryButton")
        connect.clicked.connect(lambda: self._start_test(connect_after=True))
        self.connect_button = connect
        cancel = QPushButton("Cancel")
        cancel.setObjectName("QuietButton")
        cancel.clicked.connect(self.reject)
        actions.addWidget(test)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(connect)
        layout.addLayout(actions)

        self._connect_after_test = False
        self.controller.discord_webhook_test_finished.connect(self._test_finished)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        enabled = bool(self.webhook_input.text().strip()) and not self._testing
        self.test_button.setEnabled(enabled)
        self.connect_button.setEnabled(enabled)

    def _start_test(self, *, connect_after: bool) -> None:
        if self._testing:
            return
        self._testing = True
        self._connect_after_test = connect_after
        self.status.setText("Checking the Discord connection…")
        self._sync_buttons()
        self.controller.test_discord_webhook(self.webhook_input.text().strip())

    def _test_finished(self, success: bool, message: str) -> None:
        if not self.isVisible():
            return
        self._testing = False
        self.status.setText(message)
        self.status.setProperty("success", success)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self._sync_buttons()
        if success and self._connect_after_test:
            self.controller.save_discord_webhook(self.webhook_input.text().strip())
            self.connected = True
            self.accept()

    def done(self, result: int) -> None:
        try:
            self.controller.discord_webhook_test_finished.disconnect(self._test_finished)
        except (RuntimeError, TypeError):
            pass
        super().done(result)


class DiscordManageDialog(_BaseDialog):
    def __init__(self, controller: RecorderController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.action = ""
        self.setWindowTitle("Discord connection")
        self.setFixedWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(13)
        header = QHBoxLayout()
        header.addWidget(_logo_label(52))
        copy = QVBoxLayout()
        title = QLabel("Discord connection")
        title.setObjectName("DialogTitle")
        note = QLabel("Change the saved webhook or remove it from this PC.")
        note.setObjectName("CardMuted")
        copy.addWidget(title)
        copy.addWidget(note)
        header.addLayout(copy, 1)
        layout.addLayout(header)
        change = QPushButton("Connect a different webhook")
        change.setObjectName("DarkButton")
        change.clicked.connect(lambda: self._choose("change"))
        remove = QPushButton("Remove connection")
        remove.setObjectName("DangerButton")
        remove.clicked.connect(lambda: self._choose("remove"))
        layout.addWidget(change)
        layout.addWidget(remove)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("QuietButton")
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel, alignment=Qt.AlignmentFlag.AlignRight)

    def _choose(self, action: str) -> None:
        self.action = action
        self.accept()


class ExportResultDialog(_BaseDialog):
    def __init__(
        self,
        output_path: Path,
        *,
        sent_to_discord: bool = False,
        send_error: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.output_path = Path(output_path)
        self.setWindowTitle("Sent to Discord" if sent_to_discord else "Export ready")
        self.setFixedWidth(540)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)
        header = QHBoxLayout()
        header.addWidget(_logo_label(58))
        copy = QVBoxLayout()
        title_text = (
            "Sent to Discord"
            if sent_to_discord
            else "Discord-ready file saved"
            if send_error
            else "Your video is ready"
        )
        title = QLabel(title_text)
        title.setObjectName("DialogTitle")
        detail = QLabel(f"{self.output_path.name} · {format_file_size(self.output_path.stat().st_size)}")
        detail.setObjectName("SettingName")
        path = QLabel(str(self.output_path.parent))
        path.setObjectName("CardMuted")
        path.setWordWrap(True)
        copy.addWidget(title)
        copy.addWidget(detail)
        copy.addWidget(path)
        header.addLayout(copy, 1)
        layout.addLayout(header)

        if send_error:
            warning = QLabel(send_error)
            warning.setObjectName("ExportWarning")
            warning.setWordWrap(True)
            layout.addWidget(warning)

        actions = QHBoxLayout()
        open_file = QPushButton("Open file")
        open_file.setObjectName("DarkButton")
        open_file.clicked.connect(self._open_file)
        show_folder = QPushButton("Show in folder")
        show_folder.setObjectName("PrimaryButton")
        show_folder.clicked.connect(self._show_in_folder)
        close = QPushButton("Close")
        close.setObjectName("QuietButton")
        close.clicked.connect(self.accept)
        actions.addWidget(open_file)
        actions.addWidget(show_folder)
        actions.addStretch()
        actions.addWidget(close)
        layout.addLayout(actions)

    def _open_file(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_path)))

    def _show_in_folder(self) -> None:
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(self.output_path)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_path.parent)))
