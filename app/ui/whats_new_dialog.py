from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QPixmap, QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsBlurEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.assets import logo_path


class WhatsNewDialog(QDialog):
    """Modal release carousel with a temporary blur on the application beneath it."""

    def __init__(
        self,
        parent: QWidget,
        version: str,
        slides: Sequence[dict[str, object]],
    ) -> None:
        super().__init__(parent)
        self.version = str(version)
        self.slides = list(slides)
        self._blur_target = parent.centralWidget() if hasattr(parent, "centralWidget") else parent
        self._blur_effect: QGraphicsBlurEffect | None = None
        self._dot_labels: list[QLabel] = []

        self.setModal(True)
        self.setWindowTitle(f"What's new in League Highlights {self.version}")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(760, 560)

        overlay = QFrame()
        overlay.setObjectName("WhatsNewOverlay")
        overlay_layout = QVBoxLayout(overlay)
        overlay_layout.setContentsMargins(34, 30, 34, 30)
        overlay_layout.addStretch()

        card = QFrame()
        card.setObjectName("WhatsNewCard")
        card.setFixedSize(790, 570)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(12)

        header = QHBoxLayout()
        header.setContentsMargins(4, 0, 0, 0)
        brand = QLabel()
        brand.setObjectName("WhatsNewBrand")
        brand.setFixedSize(30, 30)
        pixmap = QPixmap(str(logo_path()))
        if not pixmap.isNull():
            brand.setPixmap(
                pixmap.scaled(
                    26,
                    26,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        title = QLabel(f"WHAT'S NEW IN {self.version}")
        title.setObjectName("WhatsNewHeader")
        header.addWidget(brand)
        header.addWidget(title)
        header.addStretch()
        card_layout.addLayout(header)

        self.pages = QStackedWidget()
        self.pages.setObjectName("WhatsNewPages")
        for slide in self.slides:
            self.pages.addWidget(self._build_slide(slide))
        card_layout.addWidget(self.pages, 1)

        navigation = QHBoxLayout()
        navigation.setContentsMargins(4, 0, 4, 0)
        self.previous_button = QPushButton("‹")
        self.previous_button.setObjectName("WhatsNewArrow")
        self.previous_button.setToolTip("Previous feature")
        self.previous_button.clicked.connect(self._previous)
        navigation.addWidget(self.previous_button)
        navigation.addStretch()

        dots = QHBoxLayout()
        dots.setSpacing(6)
        for _ in self.slides:
            dot = QLabel("●")
            dot.setObjectName("WhatsNewDot")
            dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._dot_labels.append(dot)
            dots.addWidget(dot)
        navigation.addLayout(dots)
        navigation.addStretch()

        self.next_button = QPushButton("Next")
        self.next_button.setObjectName("WhatsNewPrimary")
        self.next_button.clicked.connect(self._next)
        navigation.addWidget(self.next_button)
        card_layout.addLayout(navigation)

        footer_divider = QFrame()
        footer_divider.setObjectName("WhatsNewDivider")
        footer_divider.setFixedHeight(1)
        card_layout.addWidget(footer_divider)

        footer = QHBoxLayout()
        footer.setContentsMargins(4, 0, 0, 0)
        footer_text = QLabel("League Highlights just updated")
        footer_text.setObjectName("WhatsNewFooter")
        finish = QPushButton("Finish")
        finish.setObjectName("WhatsNewPrimary")
        finish.clicked.connect(self.accept)
        footer.addWidget(footer_text)
        footer.addStretch()
        footer.addWidget(finish)
        card_layout.addLayout(footer)

        overlay_layout.addWidget(card, alignment=Qt.AlignmentFlag.AlignCenter)
        overlay_layout.addStretch()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(overlay)
        self._sync_navigation()

    def _build_slide(self, slide: dict[str, object]) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        hero = QFrame()
        hero.setObjectName("WhatsNewHero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(28, 24, 28, 24)
        hero_layout.setSpacing(10)
        hero_layout.addStretch()

        mark = QLabel()
        mark.setObjectName("WhatsNewHeroMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(str(logo_path()))
        if not pixmap.isNull():
            mark.setPixmap(
                pixmap.scaled(
                    74,
                    74,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        eyebrow = QLabel(str(slide.get("eyebrow", "NEW")))
        eyebrow.setObjectName("WhatsNewEyebrow")
        eyebrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_layout.addWidget(mark)
        hero_layout.addWidget(eyebrow)
        hero_layout.addStretch()
        layout.addWidget(hero, 1)

        title = QLabel(str(slide.get("title", "New in League Highlights")))
        title.setObjectName("WhatsNewTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setWordWrap(True)
        description = QLabel(str(slide.get("description", "")))
        description.setObjectName("WhatsNewDescription")
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(description)

        bullets = slide.get("bullets", ())
        if isinstance(bullets, (list, tuple)):
            bullet_text = "\n".join(f"—  {item}" for item in bullets if str(item).strip())
            if bullet_text:
                bullet_label = QLabel(bullet_text)
                bullet_label.setObjectName("WhatsNewBullets")
                bullet_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                bullet_label.setWordWrap(True)
                layout.addWidget(bullet_label)
        return page

    def _previous(self) -> None:
        self.pages.setCurrentIndex(max(0, self.pages.currentIndex() - 1))
        self._sync_navigation()

    def _next(self) -> None:
        if self.pages.currentIndex() >= self.pages.count() - 1:
            self.accept()
            return
        self.pages.setCurrentIndex(self.pages.currentIndex() + 1)
        self._sync_navigation()

    def _sync_navigation(self) -> None:
        current = self.pages.currentIndex()
        count = self.pages.count()
        self.previous_button.setEnabled(current > 0)
        self.next_button.setText("Got it" if current == count - 1 else "Next")
        for index, dot in enumerate(self._dot_labels):
            dot.setProperty("active", index == current)
            dot.style().unpolish(dot)
            dot.style().polish(dot)

    def showEvent(self, event: QShowEvent) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.frameGeometry())
        if self._blur_target is not None and self._blur_effect is None:
            self._blur_effect = QGraphicsBlurEffect(self._blur_target)
            self._blur_effect.setBlurRadius(7.5)
            self._blur_target.setGraphicsEffect(self._blur_effect)
        super().showEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._remove_blur()
        super().closeEvent(event)

    def done(self, result: int) -> None:
        self._remove_blur()
        super().done(result)

    def _remove_blur(self) -> None:
        if self._blur_target is not None and self._blur_effect is not None:
            self._blur_target.setGraphicsEffect(None)
            self._blur_effect = None
