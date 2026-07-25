from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QPushButton, QSlider, QToolButton, QWidget


_TARGET_BUTTONS = {"Auto Trim", "Use Full Clip"}
_PANEL_MARK = "LeagueHighlightsTrimPanel"


def _iter_ancestors(widget: QWidget | None):
    current = widget
    while current is not None:
        yield current
        parent = current.parent()
        current = parent if isinstance(parent, QWidget) else None


def _find_trim_panel(root: QWidget) -> QWidget | None:
    """Locate the Smart Trim section without depending on exact object names."""
    for label in root.findChildren(QLabel):
        text = str(label.text() or "")
        if "Smart Trim" not in text:
            continue

        for ancestor in _iter_ancestors(label):
            texts = {
                str(button.text() or "").strip()
                for button in ancestor.findChildren(QPushButton)
            } | {
                str(button.text() or "").strip()
                for button in ancestor.findChildren(QToolButton)
            }
            if _TARGET_BUTTONS & texts:
                return ancestor
    return None


def _style_action_button(button: QWidget) -> None:
    button.setStyleSheet(
        """
        QPushButton, QToolButton {
            background: #FFFFFF;
            color: #08111A;
            border: none;
            border-radius: 9px;
            padding: 8px 16px;
            font-weight: 700;
        }
        QPushButton:hover, QToolButton:hover {
            background: #F2F4F7;
        }
        QPushButton:pressed, QToolButton:pressed {
            background: #E3E8EE;
        }
        QPushButton:disabled, QToolButton:disabled {
            background: #C8CFD8;
            color: #5C6673;
        }
        """
    )


def _style_trim_panel(root: QWidget) -> None:
    panel = _find_trim_panel(root)
    if panel is None:
        return

    panel.setObjectName(_PANEL_MARK)
    panel.setStyleSheet(
        """
        QWidget#LeagueHighlightsTrimPanel,
        QWidget#LeagueHighlightsTrimPanel QLabel,
        QWidget#LeagueHighlightsTrimPanel QFrame {
            background: transparent;
            border: none;
        }
        QWidget#LeagueHighlightsTrimPanel QLabel {
            color: #EEF3F8;
        }
        """
    )

    for widget in panel.findChildren(QWidget):
        if isinstance(widget, (QPushButton, QToolButton, QSlider)):
            continue

        # Remove the dark-blue block look from the trim section containers.
        if widget is not panel:
            widget.setStyleSheet(
                "background: transparent; border: none;"
            )

    for button in list(panel.findChildren(QPushButton)) + list(panel.findChildren(QToolButton)):
        text = str(button.text() or "").strip()
        if text in _TARGET_BUTTONS:
            _style_action_button(button)


def install_trim_panel_style_patch() -> None:
    """Polish the Smart Trim area: remove blue panel and make action buttons white."""
    from app.ui.optimized_inline_player import OptimizedInlineHighlightPlayer

    player_class = OptimizedInlineHighlightPlayer
    if getattr(player_class, "_trim_panel_style_patch_installed", False):
        return

    original_init = player_class.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        QTimer.singleShot(0, lambda: _style_trim_panel(self))
        QTimer.singleShot(250, lambda: _style_trim_panel(self))

    player_class.__init__ = patched_init
    player_class._trim_panel_style_patch_installed = True
