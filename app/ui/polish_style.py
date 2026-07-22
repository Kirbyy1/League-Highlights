from __future__ import annotations

# Visual override layer applied after the existing APP_STYLE.
# It intentionally changes presentation only; no recorder or updater behavior.
POLISH_STYLE = r"""
/* ============================================================
   League Highlights — UI polish pass 1
   JetBrains precision + Discord familiarity
   ============================================================ */

* {
    font-family: "Segoe UI";
    font-size: 13px;
}

QMainWindow,
QWidget#Root {
    background: #0B0F14;
}

/* ---------- Window chrome ---------- */

QFrame#TitleBar {
    background: #10151B;
    border-bottom: 1px solid #202832;
}

QFrame#TitleBrandBadge {
    background: #122219;
    border: 1px solid #294535;
    border-radius: 6px;
}

QLabel#BrandMark {
    color: #6FE397;
}

QLabel#AppVersionLabel {
    color: #7D8996;
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 0 2px;
    font-size: 11px;
    font-weight: 500;
}

QLabel#WindowTitle {
    color: #E5EAF0;
    font-size: 13px;
    font-weight: 600;
}

QLabel#WindowContext,
QLabel#WindowSubtitle {
    color: #788492;
    font-size: 12px;
}

QLabel#TitleStateText {
    color: #C9D2DC;
    font-size: 12px;
    font-weight: 500;
}

QLabel#TitleStateDot {
    min-width: 8px;
    max-width: 8px;
    min-height: 8px;
    max-height: 8px;
    border-radius: 4px;
    background: #53D989;
}

QToolButton#MainMenuButton,
QToolButton#TitleBackButton {
    min-width: 32px;
    max-width: 32px;
    min-height: 32px;
    max-height: 32px;
    padding: 0;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
}

QToolButton#MainMenuButton:hover,
QToolButton#TitleBackButton:hover {
    background: #192129;
    border-color: #2B3642;
}

QToolButton#MainMenuButton:pressed,
QToolButton#TitleBackButton:pressed {
    background: #121920;
}

QToolButton#TitleButton,
QToolButton#TitleCloseButton {
    min-width: 44px;
    max-width: 44px;
    min-height: 46px;
    max-height: 46px;
    padding: 0;
    background: transparent;
    border: none;
    border-radius: 0;
}

QToolButton#TitleButton:hover {
    background: #1A222B;
}

QToolButton#TitleCloseButton:hover {
    background: #B52D3A;
}

/* ---------- Main layout ---------- */

QFrame#Sidebar {
    background: #0E1319;
    border: none;
    border-right: 1px solid #202832;
    border-radius: 0;
}

QFrame#ContentPanel {
    background: #0C1117;
    border: none;
    border-radius: 0;
}

QPushButton#NavButton {
    min-height: 38px;
    text-align: left;
    padding: 0 12px;
    background: transparent;
    border: 1px solid transparent;
    border-left: 2px solid transparent;
    border-radius: 5px;
    color: #9DA8B4;
    font-size: 13px;
    font-weight: 500;
}

QPushButton#NavButton:hover {
    color: #E3E8ED;
    background: #151C23;
    border-color: #222D38;
}

QPushButton#NavButton[active="true"] {
    color: #EAF4ED;
    background: #132019;
    border-color: #26392E;
    border-left: 2px solid #59D98A;
}

/* ---------- Typography ---------- */

QLabel#PageTitle {
    color: #F0F3F6;
    font-size: 23px;
    font-weight: 700;
}

QLabel#PageSubtitle {
    color: #8894A1;
    font-size: 13px;
}

QLabel#SectionTitle,
QLabel#SettingsTitle {
    color: #E9EDF2;
    font-size: 15px;
    font-weight: 650;
}

QLabel#SectionEyebrow {
    color: #7E8A97;
    font-size: 11px;
    font-weight: 600;
}

QLabel#SettingName,
QLabel#GameTitleCompact,
QLabel#GameTitle {
    color: #E4E9EE;
    font-size: 13px;
    font-weight: 600;
}

QLabel#Muted,
QLabel#CardMuted,
QLabel#SettingHelp,
QLabel#GameSubtitle,
QLabel#GameHighlightsText,
QLabel#StorageSummary {
    color: #84909D;
}

/* ---------- Surfaces and cards ---------- */

QFrame#StatusCard,
QFrame#SettingsSection,
QFrame#StorageCard,
QFrame#HintCard,
QFrame#GameCard,
QFrame#GameCardCompact,
QFrame#ClipCard,
QFrame#InfoCard {
    background: #10161D;
    border: 1px solid #202B36;
    border-radius: 7px;
}

QFrame#SettingsSection {
    background: #0F151C;
}

QFrame#HintCard,
QFrame#InfoCard {
    background: #0E161D;
}

QFrame#GameCard:hover,
QFrame#GameCardCompact:hover,
QFrame#ClipCard:hover {
    background: #141C24;
    border-color: #344250;
}

QFrame#Divider {
    min-height: 1px;
    max-height: 1px;
    background: #222C36;
    border: none;
}

/* ---------- Match result chips ---------- */

QLabel#VictoryChip,
QLabel#DefeatChip,
QLabel#NeutralChip,
QLabel#MetaChip,
QLabel#ScoreChip,
QLabel#ReadyChip {
    border-radius: 5px;
    padding: 2px 7px;
    font-size: 11px;
    font-weight: 600;
}

QLabel#VictoryChip {
    color: #7BE2A0;
    background: #13231A;
    border: 1px solid #28503A;
}

QLabel#DefeatChip {
    color: #F18B92;
    background: #26171B;
    border: 1px solid #573038;
}

QLabel#NeutralChip,
QLabel#MetaChip {
    color: #AFBAC5;
    background: #18212A;
    border: 1px solid #2A3540;
}

QLabel#ScoreChip,
QLabel#ReadyChip {
    color: #87E2A7;
    background: #14231A;
    border: 1px solid #2B5039;
}

/* ---------- Buttons ---------- */

QPushButton#PrimaryButton,
QPushButton#DarkButton,
QPushButton#DangerButton,
QPushButton#SaveClipButton,
QPushButton#HotkeyButton,
QPushButton#QuietButton {
    min-height: 36px;
    padding: 0 12px;
    border-radius: 5px;
    font-weight: 600;
}

QPushButton#PrimaryButton,
QPushButton#SaveClipButton {
    color: #07110B;
    background: #5ADB8B;
    border: 1px solid #6BE49A;
}

QPushButton#PrimaryButton:hover,
QPushButton#SaveClipButton:hover {
    background: #69E397;
}

QPushButton#PrimaryButton:pressed,
QPushButton#SaveClipButton:pressed {
    background: #50CC7E;
}

QPushButton#PrimaryButton:disabled,
QPushButton#SaveClipButton:disabled {
    color: #77827B;
    background: #26332C;
    border-color: #34443B;
}

QPushButton#DarkButton,
QPushButton#HotkeyButton {
    color: #DDE4EB;
    background: #151C24;
    border: 1px solid #2B3642;
}

QPushButton#DarkButton:hover,
QPushButton#HotkeyButton:hover {
    background: #1A232C;
    border-color: #40505F;
}

QPushButton#QuietButton {
    color: #A6B0BB;
    background: transparent;
    border: 1px solid transparent;
}

QPushButton#QuietButton:hover {
    color: #E3E8ED;
    background: #151C23;
    border-color: #2A3540;
}

QPushButton#DangerButton {
    color: #F1848C;
    background: transparent;
    border: 1px solid #71333B;
}

QPushButton#DangerButton:hover {
    color: #FF979E;
    background: #27171B;
    border-color: #A0414B;
}

QPushButton#HotkeyButton {
    min-width: 176px;
    font-family: "Consolas";
    font-size: 13px;
}

QPushButton#HotkeyButton[capturing="true"] {
    color: #81E7A5;
    background: #14231A;
    border-color: #43845E;
}

/* ---------- Settings navigation ---------- */

QFrame#SettingsTabs {
    background: #0D1319;
    border: 1px solid #202A34;
    border-radius: 7px;
}

QPushButton#SettingsTab {
    min-height: 36px;
    padding: 0 13px;
    color: #929EAA;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    font-weight: 550;
}

QPushButton#SettingsTab:hover {
    color: #E2E7EC;
    background: #151C23;
}

QPushButton#SettingsTab[active="true"] {
    color: #DFF1E5;
    background: #142019;
    border-color: #2B4937;
}

/* ---------- Inputs ---------- */

QCheckBox {
    min-height: 24px;
    spacing: 9px;
    color: #D8DFE6;
}

QCheckBox::indicator {
    width: 17px;
    height: 17px;
    background: #111820;
    border: 1px solid #45515E;
    border-radius: 4px;
}

QCheckBox::indicator:hover {
    border-color: #6B7886;
}

QCheckBox::indicator:checked {
    background: #59D98A;
    border-color: #59D98A;
}

QComboBox,
QDoubleSpinBox {
    min-height: 36px;
    min-width: 150px;
    padding: 0 10px;
    color: #E2E7EC;
    background: #131A22;
    border: 1px solid #2C3742;
    border-radius: 5px;
}

QComboBox:hover,
QDoubleSpinBox:hover {
    background: #161E27;
    border-color: #485866;
}

QComboBox:focus,
QDoubleSpinBox:focus {
    border-color: #4B8160;
}

QComboBox QAbstractItemView {
    color: #E2E7EC;
    background: #141B23;
    border: 1px solid #2C3742;
    selection-color: #FFFFFF;
    selection-background-color: #20422F;
    outline: none;
}

QSlider::groove:horizontal {
    height: 5px;
    background: #27313B;
    border-radius: 2px;
}

QSlider::sub-page:horizontal {
    background: #5DD989;
    border-radius: 2px;
}

QSlider::add-page:horizontal {
    background: #27313B;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    width: 14px;
    margin: -5px 0;
    background: #E8EDF2;
    border: 2px solid #5DD989;
    border-radius: 7px;
}

QLabel#VolumeValue {
    min-width: 44px;
    color: #D9E0E7;
    font-family: "Consolas";
    font-weight: 600;
}

/* ---------- Callouts ---------- */

QLabel#LiveDataStatus,
QLabel#InfoBanner {
    color: #AAB4BF;
    background: #101820;
    border: 1px solid #26323D;
    border-radius: 5px;
    padding: 8px 10px;
}

QLabel#LiveDataStatus {
    color: #DDB36C;
}

/* ---------- Menus, tooltips and icon buttons ---------- */

QMenu,
QMenu#MainMenu {
    color: #DCE3EA;
    background: #151B22;
    border: 1px solid #2A3541;
    border-radius: 6px;
    padding: 5px;
}

QMenu::item {
    padding: 7px 12px;
    border-radius: 4px;
}

QMenu::item:selected {
    background: #202A34;
}

QToolTip {
    color: #EEF2F5;
    background: #171E25;
    border: 1px solid #303B46;
    padding: 5px 8px;
}

QToolButton#HeaderIconButton {
    min-width: 32px;
    max-width: 32px;
    min-height: 32px;
    max-height: 32px;
    padding: 0;
    color: #BAC4CE;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
}

QToolButton#HeaderIconButton:hover {
    color: #EEF2F5;
    background: #182028;
    border-color: #2B3641;
}

/* ---------- Scrollbars ---------- */

QScrollArea {
    background: transparent;
    border: none;
}

QScrollBar:vertical {
    width: 10px;
    margin: 2px;
    background: transparent;
}

QScrollBar::handle:vertical {
    min-height: 36px;
    background: #303A45;
    border-radius: 4px;
}

QScrollBar::handle:vertical:hover {
    background: #46525E;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {
    height: 0;
    background: transparent;
}

QScrollBar:horizontal {
    height: 10px;
    margin: 2px;
    background: transparent;
}

QScrollBar::handle:horizontal {
    min-width: 36px;
    background: #303A45;
    border-radius: 4px;
}

QScrollBar::handle:horizontal:hover {
    background: #46525E;
}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {
    width: 0;
    background: transparent;
}

/* ---------- Progress ---------- */

QProgressBar {
    min-height: 5px;
    max-height: 5px;
    color: transparent;
    background: #242E38;
    border: none;
    border-radius: 2px;
}

QProgressBar::chunk {
    background: #5BD98A;
    border-radius: 2px;
}
"""
