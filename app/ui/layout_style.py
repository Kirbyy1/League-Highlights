from __future__ import annotations

LAYOUT_STYLE = r"""
/* ============================================================
   League Highlights — structural UI pass 2
   ============================================================ */

/* Highlights header and library tools */
QLabel#LibrarySubtitle {
    color: #8793A0;
    font-size: 13px;
    padding: 0 0 2px 1px;
}

QFrame#LibraryToolbar {
    background: #0F151C;
    border: 1px solid #202A35;
    border-radius: 6px;
}

QLabel#LibraryCount {
    color: #A8B2BD;
    font-size: 12px;
    font-weight: 600;
}

QLineEdit#LibrarySearch {
    min-height: 34px;
    padding: 0 10px;
    color: #E4E9EE;
    background: #121920;
    border: 1px solid #2A3540;
    border-radius: 5px;
    selection-background-color: #24563A;
}

QLineEdit#LibrarySearch:hover {
    border-color: #3D4B59;
}

QLineEdit#LibrarySearch:focus {
    border-color: #4D8C66;
    background: #151D25;
}

QLineEdit#LibrarySearch::placeholder {
    color: #737F8C;
}

QComboBox#LibraryFilter {
    min-width: 125px;
    min-height: 34px;
}

QPushButton#ToolbarButton {
    min-height: 34px;
    padding: 0 11px;
    color: #D9E0E7;
    background: #151C24;
    border: 1px solid #2B3642;
    border-radius: 5px;
    font-weight: 600;
}

QPushButton#ToolbarButton:hover {
    background: #1A232C;
    border-color: #40505F;
}

/* Improved match rows */
QFrame#GameCardCompact {
    min-height: 96px;
    max-height: 108px;
    background: #10161D;
    border: 1px solid #202B36;
    border-radius: 7px;
}

QFrame#GameCardCompact:hover {
    background: #141C24;
    border-color: #3A4856;
}

QLabel#GameTitleCompact {
    color: #EDF1F5;
    font-size: 14px;
    font-weight: 650;
}

QLabel#GameMetaLine {
    color: #83909C;
    font-size: 12px;
}

QLabel#GameHighlightCount {
    min-width: 92px;
    color: #AAB5C0;
    font-size: 11px;
    line-height: 1.2;
}

QLabel#GameCardChevron {
    color: #687582;
    font-size: 21px;
    font-weight: 400;
}

QFrame#GameCardCompact:hover QLabel#GameCardChevron {
    color: #C9D2DC;
}

/* Vertical settings navigation */
QWidget#SettingsBody {
    background: transparent;
}

QFrame#SettingsSideNavigation {
    background: #0E141A;
    border: 1px solid #202A35;
    border-radius: 7px;
}

QLabel#SettingsNavigationEyebrow {
    color: #697684;
    font-size: 10px;
    font-weight: 700;
    padding: 5px 8px 8px 8px;
}

QFrame#SettingsSideNavigation QPushButton#SettingsTab {
    min-height: 38px;
    text-align: left;
    padding: 0 11px;
    color: #929EAA;
    background: transparent;
    border: 1px solid transparent;
    border-left: 2px solid transparent;
    border-radius: 5px;
}

QFrame#SettingsSideNavigation QPushButton#SettingsTab:hover {
    color: #E1E7EC;
    background: #151C23;
    border-color: #242F3A;
}

QFrame#SettingsSideNavigation QPushButton#SettingsTab[active="true"] {
    color: #E3F2E8;
    background: #142019;
    border-color: #294435;
    border-left: 2px solid #59D98A;
}

/* JetBrains-style bottom status bar */
QFrame#BottomStatusBar {
    background: #0D1217;
    border-top: 1px solid #202832;
    border-radius: 0;
}

QLabel#BottomStateDot {
    min-width: 8px;
    max-width: 8px;
    min-height: 8px;
    max-height: 8px;
    border-radius: 4px;
    background: #697684;
}

QLabel#BottomStateDot[state="recording"] {
    background: #58DB8A;
}

QLabel#BottomStateDot[state="saving"] {
    background: #E0B45F;
}

QLabel#BottomStateDot[state="error"] {
    background: #EE6974;
}

QLabel#BottomStateDot[state="waiting"] {
    background: #697684;
}

QLabel#BottomStateText {
    color: #C7D0D9;
    font-size: 11px;
    font-weight: 600;
}

QLabel#BottomStatusMuted {
    color: #778491;
    font-size: 11px;
}

QLabel#BottomStatusSeparator {
    color: #3B4651;
    font-size: 10px;
}
"""
