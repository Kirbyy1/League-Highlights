from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QPushButton, QSlider, QToolButton, QWidget


def _has_player_controls(widget: QWidget) -> bool:
    sliders = widget.findChildren(QSlider)
    buttons = widget.findChildren(QPushButton) + widget.findChildren(QToolButton)
    if not sliders or not buttons:
        return False
    labels = [str(label.text() or "") for label in widget.findChildren(QLabel)]
    return any(":" in text and "/" in text for text in labels) or len(sliders) >= 2


def _is_time_label(text: str) -> bool:
    text = str(text or "").strip()
    return ":" in text and "/" in text


def _polish_player(root: QWidget) -> None:
    # Global styling for the video player controls area.
    root.setStyleSheet(
        (root.styleSheet() or "")
        + """
        QSlider::groove:horizontal {
            background: rgba(255,255,255,0.16);
            height: 8px;
            border-radius: 4px;
            border: none;
        }
        QSlider::sub-page:horizontal {
            background: #F2B15F;
            border-radius: 4px;
            border: none;
        }
        QSlider::handle:horizontal {
            background: #FFFFFF;
            width: 16px;
            margin: -5px 0;
            border-radius: 8px;
            border: none;
        }
        QLabel {
            color: #F3F6F9;
        }
        """
    )

    for widget in root.findChildren(QWidget):
        if _has_player_controls(widget):
            widget.setStyleSheet("background: transparent; border: none;")

    for label in root.findChildren(QLabel):
        text = str(label.text() or "")
        if _is_time_label(text):
            label.setStyleSheet(
                "color: #F7FAFD; background: transparent; font-weight: 700; font-size: 14px;"
            )

    # General icon/transport button style
    for button in root.findChildren(QPushButton) + root.findChildren(QToolButton):
        text = str(button.text() or "").strip()

        if text in {"Auto Trim", "Use Full Clip"}:
            button.setStyleSheet(
                """
                QPushButton, QToolButton {
                    background: #FFFFFF;
                    color: #09131C;
                    border: none;
                    border-radius: 10px;
                    padding: 9px 18px;
                    font-weight: 700;
                }
                QPushButton:hover, QToolButton:hover {
                    background: #F1F4F7;
                }
                QPushButton:pressed, QToolButton:pressed {
                    background: #E3E8EE;
                }
                """
            )
            continue

        # Icon-only controls: play, mute, fullscreen, trim arrows, etc.
        if not text:
            button.setStyleSheet(
                """
                QPushButton, QToolButton {
                    background: rgba(255,255,255,0.06);
                    color: #FFFFFF;
                    border: 1px solid rgba(255,255,255,0.08);
                    border-radius: 10px;
                    padding: 8px;
                }
                QPushButton:hover, QToolButton:hover {
                    background: rgba(255,255,255,0.12);
                    border: 1px solid rgba(255,255,255,0.14);
                }
                QPushButton:pressed, QToolButton:pressed {
                    background: rgba(255,255,255,0.18);
                }
                """
            )
        else:
            # Any other small buttons in the player
            button.setStyleSheet(
                """
                QPushButton, QToolButton {
                    background: rgba(255,255,255,0.06);
                    color: #FFFFFF;
                    border: none;
                    border-radius: 8px;
                    padding: 6px 12px;
                    font-weight: 600;
                }
                QPushButton:hover, QToolButton:hover {
                    background: rgba(255,255,255,0.12);
                }
                """
            )


def install_video_controls_polish_patch() -> None:
    """Clean up the video player controls bar."""
    from app.ui.optimized_inline_player import OptimizedInlineHighlightPlayer

    player_class = OptimizedInlineHighlightPlayer
    if getattr(player_class, "_video_controls_polish_patch_installed", False):
        return

    original_init = player_class.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        QTimer.singleShot(0, lambda: _polish_player(self))
        QTimer.singleShot(200, lambda: _polish_player(self))
        QTimer.singleShot(800, lambda: _polish_player(self))

    player_class.__init__ = patched_init
    player_class._video_controls_polish_patch_installed = True
