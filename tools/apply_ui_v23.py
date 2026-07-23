from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

BUILD = "V23-SHARP-SYMBOL-FIRST-UI"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one source match, found {count}")
    return text.replace(old, new, 1)


def patch_main_window(text: str) -> str:
    icon_block = '''    elif kind == "live_match":
        # Distinct live-game / scouting symbol: focus corners with a live centre.
        painter.drawLine(4.0, 7.0, 4.0, 4.0)
        painter.drawLine(4.0, 4.0, 7.0, 4.0)
        painter.drawLine(13.0, 4.0, 16.0, 4.0)
        painter.drawLine(16.0, 4.0, 16.0, 7.0)
        painter.drawLine(4.0, 13.0, 4.0, 16.0)
        painter.drawLine(4.0, 16.0, 7.0, 16.0)
        painter.drawLine(13.0, 16.0, 16.0, 16.0)
        painter.drawLine(16.0, 13.0, 16.0, 16.0)
        painter.drawEllipse(QRectF(7.25, 7.25, 5.5, 5.5))
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(9.0, 9.0, 2.0, 2.0))
    elif kind == "settings":
'''
    text = replace_once(text, '    elif kind == "settings":\n', icon_block, "insert Live Match icon")
    text = replace_once(text, '        sidebar.setFixedWidth(164)\n', '        sidebar.setFixedWidth(58)\n', "compact sidebar")
    text = replace_once(text, '        self.highlights_nav = QPushButton("Highlights")\n', '        self.highlights_nav = QPushButton("")\n', "icon-only Highlights")
    text = replace_once(
        text,
        '        self.sidebar.setFixedWidth(58 if compact else 164)\n        self.highlights_nav.setText("" if compact else "Highlights")\n',
        '        self.sidebar.setFixedWidth(58)\n        self.highlights_nav.setText("")\n',
        "persistent compact sidebar",
    )
    text = text.replace('path.addRoundedRect(rect, 9, 9)', 'path.addRoundedRect(rect, 3, 3)')
    text = text.replace('painter.drawRoundedRect(badge, 6, 6)', 'painter.drawRoundedRect(badge, 3, 3)')
    text = text.replace('"border-radius:12px; }}"', '"border-radius:3px; }}"')
    marker = f'UI_POLISH_BUILD = "{BUILD}"'
    if marker not in text:
        text = text.replace('from __future__ import annotations\n', f'from __future__ import annotations\n\n{marker}\n', 1)
    return text


def patch_enhanced_window(text: str) -> str:
    text = replace_once(text, '        self.live_match_nav = QPushButton("Live Match")\n', '        self.live_match_nav = QPushButton("")\n', "icon-only Live Match")
    text = replace_once(text, '        self.live_match_nav.setIcon(_app_icon("highlights"))\n', '        self.live_match_nav.setIcon(_app_icon("live_match"))\n', "distinct Live Match icon")
    text = replace_once(text, '            self.live_match_nav.setText("" if compact else "Live Match")\n', '            self.live_match_nav.setText("")\n', "persistent Live Match symbol")
    text = text.replace('painter.drawRoundedRect(badge, 6, 6)', 'painter.drawRoundedRect(badge, 3, 3)')
    marker = f'ENHANCED_UI_POLISH_BUILD = "{BUILD}"'
    if marker not in text:
        text = text.replace('from __future__ import annotations\n', f'from __future__ import annotations\n\n{marker}\n', 1)
    return text


SHARP_STYLE = r'''
/* V23 — sharp, professional, symbol-first interface */
* { font-family: "Segoe UI Variable Text", "Segoe UI"; }
QFrame#TitleBrandBadge, QFrame#Sidebar, QFrame#ContentPanel,
QFrame#StatusCard, QFrame#SettingsSection, QFrame#StorageCard,
QFrame#HintCard, QFrame#GameCard, QFrame#GameCardCompact,
QFrame#ClipCard, QFrame#InfoCard, QFrame#SettingsTabs,
QFrame#PlayerScoutCard, QFrame#TeamSection, QFrame#LiveMatchHeader,
QFrame#BottomStatusBar, QFrame#ClipToastRoot { border-radius: 3px; }
QPushButton, QToolButton, QComboBox, QLineEdit, QDoubleSpinBox,
QProgressBar, QLabel#LiveDataStatus, QLabel#InfoBanner,
QLabel#VictoryChip, QLabel#DefeatChip, QLabel#NeutralChip,
QLabel#MetaChip, QLabel#ScoreChip, QLabel#ReadyChip { border-radius: 3px; }
QCheckBox::indicator, QSlider::handle:horizontal,
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { border-radius: 3px; }
QPushButton#NavButton {
    min-width: 38px; max-width: 38px; min-height: 42px; max-height: 42px;
    padding: 0; text-align: center; border-radius: 3px;
}
QToolButton#MainMenuButton,
QToolButton#MainMenuButton:hover,
QToolButton#MainMenuButton:pressed,
QToolButton#MainMenuButton:checked {
    border: none; outline: none; background: transparent; border-radius: 3px;
}
QMenu#MainMenu, QMenu { border: none; outline: none; border-radius: 3px; padding: 4px; }
QMenu::item { border: none; border-radius: 3px; padding: 7px 24px 7px 10px; }
QMenu::item:selected { border: none; }
QComboBox QAbstractItemView, QListView, QTreeView, QTableView {
    outline: none; border-radius: 3px;
}
QToolTip { border-radius: 3px; }
'''


def patch_styles(text: str) -> str:
    text = text.replace('font-family: "Segoe UI";', 'font-family: "Segoe UI Variable Text", "Segoe UI";', 1)
    if "/* V23 — sharp, professional, symbol-first interface */" in text:
        return text
    closing = text.rfind('"""')
    if closing < 0:
        raise RuntimeError("styles.py: APP_STYLE closing quote not found")
    return text[:closing] + SHARP_STYLE + "\n" + text[closing:]


def patch_project(project_root: Path, backup: bool = True) -> list[Path]:
    files = {
        project_root / "app" / "ui" / "main_window.py": patch_main_window,
        project_root / "app" / "ui" / "enhanced_main_window.py": patch_enhanced_window,
        project_root / "app" / "ui" / "styles.py": patch_styles,
    }
    missing = [str(path) for path in files if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing required files:\n" + "\n".join(missing))
    backup_root = project_root / f"_ui_v23_backup_{datetime.now():%Y%m%d-%H%M%S}"
    changed = []
    for path, transform in files.items():
        original = path.read_text(encoding="utf-8-sig")
        updated = transform(original)
        if updated == original:
            continue
        if backup:
            destination = backup_root / path.relative_to(project_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        path.write_text(updated, encoding="utf-8", newline="\n")
        changed.append(path)
    if changed and backup:
        print(f"Backup: {backup_root}")
    return changed


def verify(project_root: Path) -> None:
    main = (project_root / "app" / "ui" / "main_window.py").read_text(encoding="utf-8")
    enhanced = (project_root / "app" / "ui" / "enhanced_main_window.py").read_text(encoding="utf-8")
    styles = (project_root / "app" / "ui" / "styles.py").read_text(encoding="utf-8")
    checks = {
        "V23 main marker": f'UI_POLISH_BUILD = "{BUILD}"' in main,
        "V23 enhanced marker": f'ENHANCED_UI_POLISH_BUILD = "{BUILD}"' in enhanced,
        "Highlights icon-only": 'self.highlights_nav = QPushButton("")' in main,
        "Live Match icon-only": 'self.live_match_nav = QPushButton("")' in enhanced,
        "Distinct Live Match symbol": '_app_icon("live_match")' in enhanced and 'kind == "live_match"' in main,
        "Compact sidebar": 'self.sidebar.setFixedWidth(58)' in main,
        "Thumbnail radius 3": 'path.addRoundedRect(rect, 3, 3)' in main,
        "Professional font": 'Segoe UI Variable Text' in styles,
        "Sharp controls": 'border-radius: 3px' in styles,
        "Hamburger outline removed": 'QToolButton#MainMenuButton' in styles and 'outline: none' in styles,
        "Menu border removed": 'QMenu#MainMenu' in styles and 'border: none' in styles,
    }
    failed = []
    for name, passed in checks.items():
        print(("PASS  " if passed else "FAIL  ") + name)
        if not passed:
            failed.append(name)
    if failed:
        raise RuntimeError("V23 verification failed: " + ", ".join(failed))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve()
    if not args.verify_only:
        changed = patch_project(root, backup=not args.no_backup)
        print(f"Changed files: {len(changed)}")
    verify(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
