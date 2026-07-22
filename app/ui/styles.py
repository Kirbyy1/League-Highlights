APP_STYLE = r"""
* {
    font-family: "Segoe UI";
    font-size: 13px;
    color: #E7ECF2;
}
QMainWindow, QWidget#Root {
    background: #090D12;
}
QToolTip {
    color: #F3F6F9;
    background: #171E27;
    border: 1px solid #303B47;
    padding: 6px 9px;
}

/* Window chrome */
QFrame#TitleBar {
    background: #0B1016;
    border-bottom: 1px solid #1A232D;
}
QFrame#TitleBrandBadge {
    background: #173322;
    border: 1px solid #2D6241;
    border-radius: 8px;
}
QLabel#BrandMark {
    color: #74E69A;
    font-size: 14px;
    font-weight: 800;
}
QLabel#WindowTitle {
    color: #E9EEF3;
    font-size: 13px;
    font-weight: 650;
}
QLabel#WindowSubtitle {
    color: #778390;
    font-size: 11px;
}
QToolButton#TitleButton, QToolButton#TitleCloseButton {
    min-width: 46px;
    max-width: 46px;
    min-height: 46px;
    max-height: 46px;
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 0;
}
QToolButton#TitleButton:hover { background: #18212B; }
QToolButton#TitleCloseButton:hover { background: #C42B3A; }

/* Main surfaces */
QFrame#Sidebar {
    background: #0E141B;
    border: 1px solid #202A35;
    border-radius: 14px;
}
QFrame#ContentPanel {
    background: #0D131A;
    border: 1px solid #202A35;
    border-radius: 14px;
}
QFrame#StatusCard, QFrame#SettingsSection, QFrame#StorageCard, QFrame#HintCard,
QFrame#GameCard, QFrame#ClipCard, QFrame#InfoCard {
    background: #111820;
    border: 1px solid #222E39;
    border-radius: 12px;
}
QFrame#StatusCard {
    background: #10171E;
}
QFrame#HintCard, QFrame#InfoCard {
    background: #0F171F;
}
QFrame#GameCard:hover, QFrame#ClipCard:hover {
    background: #131C25;
    border-color: #374653;
}
QFrame#Divider {
    background: #222D38;
    border: none;
}

/* Typography */
QLabel#SidebarBrand {
    color: #F0F3F7;
    font-size: 15px;
    font-weight: 700;
}
QLabel#PageTitle {
    color: #F3F6F9;
    font-size: 28px;
    font-weight: 720;
}
QLabel#PageSubtitle {
    color: #8D99A6;
    font-size: 14px;
}
QLabel#SectionTitle, QLabel#SettingsTitle {
    color: #EEF2F6;
    font-size: 16px;
    font-weight: 680;
}
QLabel#SectionEyebrow {
    color: #7F8B98;
    font-size: 11px;
    font-weight: 650;
}
QLabel#SettingName {
    color: #E9EDF2;
    font-size: 13px;
    font-weight: 620;
}
QLabel#Muted, QLabel#CardMuted, QLabel#SettingHelp {
    color: #8D99A6;
}
QLabel#EmptyTitle {
    color: #EAF0F5;
    font-size: 20px;
    font-weight: 680;
}

/* Sidebar and status */
QPushButton#NavButton {
    min-height: 48px;
    text-align: left;
    padding: 0 15px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    color: #A9B3BE;
    font-size: 14px;
    font-weight: 620;
}
QPushButton#NavButton:hover {
    background: #141C25;
    color: #E7ECF2;
}
QPushButton#NavButton[active="true"] {
    background: #13231B;
    border-color: #28533D;
    color: #6EE69A;
}
QLabel#StatusTitle {
    color: #EAF0F5;
    font-size: 12px;
    font-weight: 750;
    letter-spacing: 0.4px;
}
QLabel#StatusTime {
    color: #F4F7FA;
    font-size: 22px;
    font-weight: 720;
}
QLabel#StatusProfile {
    color: #8995A2;
    font-size: 11px;
}
QLabel#StatusDot {
    min-width: 10px;
    max-width: 10px;
    min-height: 10px;
    max-height: 10px;
    border-radius: 5px;
    background: #768391;
}
QProgressBar#SaveProgress {
    min-height: 4px;
    max-height: 4px;
    border: none;
    border-radius: 2px;
    background: #202A35;
    text-align: center;
}
QProgressBar#SaveProgress::chunk {
    background: #63DE8C;
    border-radius: 2px;
}
QLabel#StorageSummary {
    color: #E9EDF2;
    font-size: 13px;
    font-weight: 650;
}

/* Buttons */
QPushButton#PrimaryButton, QPushButton#DarkButton, QPushButton#DangerButton,
QPushButton#SaveClipButton, QPushButton#HotkeyButton, QPushButton#QuietButton {
    min-height: 40px;
    border-radius: 8px;
    padding: 0 14px;
    font-weight: 650;
}
QPushButton#PrimaryButton, QPushButton#SaveClipButton {
    background: #55D985;
    border: 1px solid #6BE697;
    color: #07110B;
}
QPushButton#PrimaryButton:hover, QPushButton#SaveClipButton:hover {
    background: #65E394;
}
QPushButton#PrimaryButton:disabled, QPushButton#SaveClipButton:disabled {
    background: #26342C;
    border-color: #33453A;
    color: #77847C;
}
QPushButton#DarkButton {
    background: #161F28;
    border: 1px solid #2C3844;
    color: #DFE5EB;
}
QPushButton#DarkButton:hover {
    background: #1B2731;
    border-color: #445260;
}
QPushButton#QuietButton {
    background: transparent;
    border: 1px solid transparent;
    color: #AAB5C0;
}
QPushButton#QuietButton:hover {
    background: #151E27;
    border-color: #2B3743;
    color: #E4E9EE;
}
QPushButton#DangerButton {
    background: transparent;
    border: 1px solid #8B3841;
    color: #FF7D86;
}
QPushButton#DangerButton:hover {
    background: #28171B;
    border-color: #BD4A55;
}
QPushButton#HotkeyButton {
    min-width: 180px;
    background: #171F28;
    border: 1px solid #394653;
    color: #F1F4F7;
    font-family: "Consolas";
    font-size: 14px;
}
QPushButton#HotkeyButton:hover {
    background: #1D2934;
    border-color: #576675;
}
QPushButton#HotkeyButton[capturing="true"] {
    background: #16251D;
    border-color: #4FAF70;
    color: #83EDAA;
}

/* Settings navigation */
QFrame#SettingsTabs {
    background: #0B1118;
    border: 1px solid #202B36;
    border-radius: 10px;
}
QPushButton#SettingsTab {
    min-height: 40px;
    padding: 0 16px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    color: #8F9BA8;
    font-weight: 620;
}
QPushButton#SettingsTab:hover {
    background: #151E27;
    color: #E5EAF0;
}
QPushButton#SettingsTab[active="true"] {
    background: #17251E;
    border-color: #2B5C42;
    color: #70E59B;
}

/* Inputs */
QCheckBox {
    color: #DCE3EA;
    spacing: 9px;
    min-height: 24px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 1px solid #43505E;
    border-radius: 5px;
    background: #111821;
}
QCheckBox::indicator:hover { border-color: #6F7E8E; }
QCheckBox::indicator:checked {
    background: #58D889;
    border-color: #58D889;
}
QComboBox {
    min-width: 160px;
    min-height: 40px;
    padding: 0 12px;
    border: 1px solid #2D3945;
    border-radius: 8px;
    background: #131B24;
    color: #E8EDF2;
}
QComboBox:hover { border-color: #4B5A69; }
QComboBox QAbstractItemView {
    background: #131B24;
    border: 1px solid #2D3945;
    selection-background-color: #23513A;
    outline: none;
}
QSlider::groove:horizontal {
    height: 6px;
    background: #26313D;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: #66DF88;
    border-radius: 3px;
}
QSlider::add-page:horizontal {
    background: #26313D;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    width: 16px;
    margin: -5px 0;
    background: #E9EEF3;
    border: 2px solid #66DF88;
    border-radius: 8px;
}
QSlider::handle:horizontal:hover {
    background: #FFFFFF;
    border-color: #7CF29A;
}
QLabel#VolumeValue {
    min-width: 48px;
    color: #DCE3EA;
    font-family: "Consolas";
    font-weight: 650;
}

/* Live status and callouts */
QLabel#LiveDataStatus {
    color: #E2B15C;
    background: #151E27;
    border: 1px solid #2B3743;
    border-radius: 7px;
    padding: 8px 10px;
    font-weight: 620;
}
QLabel#InfoBanner {
    color: #AAB5C0;
    background: #101A22;
    border: 1px solid #26333F;
    border-radius: 8px;
    padding: 10px 12px;
}

/* Game cards */
QLabel#GameTitle {
    color: #F1F4F7;
    font-size: 19px;
    font-weight: 700;
}
QLabel#GameSubtitle {
    color: #AAB5C0;
    font-size: 12px;
}
QLabel#GameHighlightsText {
    color: #85929F;
    font-size: 12px;
}
QLabel#VictoryChip, QLabel#DefeatChip, QLabel#NeutralChip, QLabel#MetaChip,
QLabel#ScoreChip, QLabel#ReadyChip {
    border-radius: 7px;
    padding: 4px 8px;
    font-size: 11px;
}
QLabel#VictoryChip {
    color: #78EAA1;
    background: #11271D;
    border: 1px solid #28573E;
    font-weight: 650;
}
QLabel#DefeatChip {
    color: #FF8991;
    background: #2B161B;
    border: 1px solid #65323A;
    font-weight: 650;
}
QLabel#NeutralChip, QLabel#MetaChip {
    color: #AEB8C3;
    background: #18212B;
    border: 1px solid #283440;
}
QLabel#ScoreChip {
    color: #8BE7AA;
    background: #14271C;
    border: 1px solid #2D5A3E;
    font-weight: 650;
}
QLabel#ReadyChip {
    color: #7AC8FF;
    background: #132435;
    border: 1px solid #29506F;
    font-weight: 650;
}

/* Clip cards */
QLabel#ClipLabel {
    color: #F0F3F6;
    font-size: 17px;
    font-weight: 680;
}
QLabel#ClipFileName {
    color: #8D99A6;
    font-size: 12px;
}
QLabel#ClipDate {
    color: #E2E7EC;
    font-size: 13px;
    font-weight: 620;
}
QLabel#ClipReasons {
    color: #A8B3BE;
    font-size: 11px;
}
QToolButton#CardAction, QToolButton#CardPlay, QToolButton#RatingButton {
    border-radius: 8px;
    padding: 0;
}
QToolButton#CardAction {
    min-width: 40px;
    max-width: 40px;
    min-height: 40px;
    max-height: 40px;
    background: #161F29;
    border: 1px solid #2B3743;
}
QToolButton#CardAction:hover {
    background: #1D2935;
    border-color: #465666;
}
QToolButton#CardPlay {
    min-width: 46px;
    max-width: 46px;
    min-height: 46px;
    max-height: 46px;
    background: #55D985;
    border: 1px solid #6BE697;
}
QToolButton#CardPlay:hover { background: #65E394; }
QToolButton#RatingButton {
    min-width: 34px;
    max-width: 34px;
    min-height: 34px;
    max-height: 34px;
    background: #151E27;
    border: 1px solid #2B3743;
}
QToolButton#RatingButton:hover {
    background: #1D2935;
    border-color: #4A5B6B;
}
QToolButton#RatingButton[active="true"] {
    background: #173023;
    border-color: #3B8A5B;
}

/* Scrolling and dialogs */
QScrollArea {
    border: none;
    background: transparent;
}
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical {
    background: transparent;
    width: 9px;
    margin: 2px 0 2px 2px;
}
QScrollBar::handle:vertical {
    background: #344251;
    min-height: 38px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover { background: #465767; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QMessageBox { background: #10171F; }


/* Built-in video player and trimmer */
QDialog#VideoPlayerDialog {
    background: #0C1218;
}
QLabel#PlayerTitle {
    color: #F2F6F9;
    font-size: 20px;
    font-weight: 700;
}
QLabel#PlayerMuted {
    color: #8E9AA7;
    font-size: 12px;
}
QFrame#VideoSurfaceFrame {
    background: #000000;
    border: 1px solid #26323E;
    border-radius: 12px;
}
QFrame#PlayerControls, QFrame#TrimPanel {
    background: #111922;
    border: 1px solid #26323E;
    border-radius: 12px;
}
QLabel#PlayerTime, QLabel#TrimValue {
    color: #C8D2DC;
    font-size: 12px;
    font-family: "Consolas";
}
QLabel#TrimTitle {
    color: #EEF3F7;
    font-size: 15px;
    font-weight: 680;
}
QLabel#TrimLabel {
    color: #D5DDE5;
    min-width: 38px;
    font-weight: 620;
}
QLabel#TrimLength {
    color: #55D985;
    background: #15271D;
    border: 1px solid #2D6843;
    border-radius: 8px;
    padding: 7px 10px;
    font-weight: 680;
}
QPushButton#DangerButton {
    background: #2A171B;
    color: #FF9AA3;
    border: 1px solid #66323A;
    border-radius: 8px;
    padding: 9px 14px;
    font-weight: 650;
}
QPushButton#DangerButton:hover {
    background: #371C22;
    border-color: #8B404B;
}
QPushButton#DangerButton:disabled {
    color: #765B60;
    background: #1C1518;
    border-color: #35272A;
}
"""

# v25 richer game metadata and match timeline
APP_STYLE += r'''
QLabel#GameMetadata {
    color: #AAB6C1;
    font-size: 12px;
    font-weight: 600;
}
QFrame#MatchTimeline {
    background: #121B24;
    border: 1px solid #25313C;
    border-radius: 12px;
}
QFrame#MatchTimeline:hover {
    border-color: #344756;
}
'''

# v26 editor-style match timeline and filmstrip trimmer (intentionally no waveform)
APP_STYLE += r'''
QFrame#MatchTimelineV26 {
    background: #111A23;
    border: 1px solid #263440;
    border-radius: 13px;
}
QFrame#MatchTimelineV26:hover {
    border-color: #385062;
}
QFrame#TrimPanelV26 {
    background: #101821;
    border: 1px solid #263440;
    border-radius: 13px;
}
QFrame#FilmstripTrimWidget {
    background: #0B1117;
    border: 1px solid #263440;
    border-radius: 10px;
}
QLabel#PlayerTimeStrong {
    color: #F0F5F8;
    font-size: 13px;
    font-family: "Consolas";
    font-weight: 700;
}
QLabel#TrimValueBox {
    color: #DDE5EC;
    background: #0C131A;
    border: 1px solid #2C3A47;
    border-radius: 7px;
    padding: 7px 10px;
    min-width: 84px;
    font-family: "Consolas";
    font-size: 12px;
}
'''

# v27 YouTube-style in-video controls and selection-only preview playback
APP_STYLE += r'''
QFrame#PlayerOverlay {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(0, 0, 0, 0),
        stop:0.24 rgba(0, 0, 0, 85),
        stop:1 rgba(0, 0, 0, 225)
    );
    border: none;
    border-radius: 0px 0px 10px 10px;
}
QToolButton#OverlayIconButton {
    background: transparent;
    color: #F4F7FA;
    border: none;
    border-radius: 6px;
    padding: 5px;
    min-width: 28px;
    min-height: 28px;
}
QToolButton#OverlayIconButton:hover {
    background: rgba(255, 255, 255, 28);
}
QToolButton#OverlayIconButton:pressed {
    background: rgba(255, 255, 255, 42);
}
QLabel#OverlayTime {
    color: #F4F7FA;
    font-size: 12px;
    font-family: "Consolas";
    font-weight: 650;
}
QSlider#OverlaySeekSlider {
    min-height: 14px;
    max-height: 14px;
}
QSlider#OverlaySeekSlider::groove:horizontal {
    height: 4px;
    border-radius: 2px;
    background: rgba(255, 255, 255, 78);
}
QSlider#OverlaySeekSlider::sub-page:horizontal {
    height: 4px;
    border-radius: 2px;
    background: #55D985;
}
QSlider#OverlaySeekSlider::handle:horizontal {
    width: 13px;
    height: 13px;
    margin: -5px 0;
    border-radius: 6px;
    background: #55D985;
    border: 1px solid #B7F4CE;
}
QSlider#OverlayVolumeSlider {
    min-height: 14px;
    max-height: 14px;
}
QSlider#OverlayVolumeSlider::groove:horizontal {
    height: 4px;
    border-radius: 2px;
    background: rgba(255, 255, 255, 70);
}
QSlider#OverlayVolumeSlider::sub-page:horizontal {
    height: 4px;
    border-radius: 2px;
    background: #F4F7FA;
}
QSlider#OverlayVolumeSlider::handle:horizontal {
    width: 11px;
    height: 11px;
    margin: -4px 0;
    border-radius: 5px;
    background: #F4F7FA;
}
QLabel#TrimCompactValue {
    color: #DDE5EC;
    font-family: "Consolas";
    font-size: 12px;
    font-weight: 650;
}
QLabel#TrimCompactMuted {
    color: #7F8D99;
    font-family: "Consolas";
    font-size: 12px;
}
'''

# v28 compact game cards and beginner-friendly match details
APP_STYLE += r'''
QFrame#GameCardCompact {
    background: #111820;
    border: 1px solid #222E39;
    border-radius: 12px;
}
QFrame#GameCardCompact:hover {
    background: #131C25;
    border-color: #374653;
}
QLabel#GameTitleCompact {
    color: #F1F4F7;
    font-size: 17px;
    font-weight: 700;
}
QFrame#GameSummaryCard {
    background: #101820;
    border: 1px solid #25313C;
    border-radius: 11px;
}
QPushButton#TimelineToggle {
    min-height: 38px;
    text-align: left;
    padding: 0 12px;
    color: #C8D2DC;
    background: #111922;
    border: 1px solid #293744;
    border-radius: 9px;
    font-weight: 650;
}
QPushButton#TimelineToggle:hover {
    background: #151F29;
    border-color: #3A4B5A;
}
QPushButton#TimelineToggle:checked {
    color: #71E59A;
    border-color: #2F6845;
    background: #13231B;
}
QFrame#TimelineContainer {
    background: transparent;
    border: none;
}
QFrame#MatchTimelineV28 {
    background: #111A23;
    border: 1px solid #263440;
    border-radius: 12px;
}
QFrame#MatchTimelineV28:hover {
    border-color: #385062;
}
'''

# v31 embedded game player above the sparse match timeline
APP_STYLE += r'''
QFrame#InlineHighlightPlayer {
    background: transparent;
    border: none;
}
QLabel#InlinePlayerTitle {
    color: #F2F6F9;
    font-size: 18px;
    font-weight: 700;
}
QLabel#InlinePlayerMeta {
    color: #8E9AA7;
    font-size: 12px;
}
QFrame#InlineVideoSurface {
    background: #000000;
    border: 1px solid #293743;
    border-radius: 12px;
}
QLabel#InlinePlayerPoster {
    color: #9AA7B4;
    background: #05080B;
    border: none;
    border-radius: 11px;
    padding: 24px;
    font-size: 14px;
}
QFrame#SparseMatchTimeline {
    background: #0E151C;
    border: 1px solid #263440;
    border-radius: 11px;
}
QFrame#SparseMatchTimeline:hover {
    border-color: #3A4E5E;
}
'''

# v32 full-match highlight progress bar inside the video player and robust fullscreen
APP_STYLE += r'''
QFrame#MatchHighlightProgressBar {
    background: transparent;
    border: none;
}
QDialog#InlineFullscreenHost {
    background: #000000;
}
QPushButton#FullscreenExitButton {
    min-height: 38px;
    margin: 18px;
    padding: 0 15px;
    color: #F4F7FA;
    background: rgba(10, 15, 20, 215);
    border: 1px solid rgba(255, 255, 255, 70);
    border-radius: 9px;
    font-weight: 650;
}
QPushButton#FullscreenExitButton:hover {
    background: rgba(28, 36, 46, 235);
    border-color: rgba(255, 255, 255, 120);
}
'''

# v33 cleaner in-player timeline: no floating event pins
APP_STYLE += r'''
QFrame#PlayerOverlay {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(0, 0, 0, 20),
        stop:0.18 rgba(0, 0, 0, 115),
        stop:1 rgba(0, 0, 0, 238)
    );
}
QLabel#OverlayTimelineLabel {
    color: rgba(244, 247, 250, 205);
    font-size: 10px;
    font-weight: 750;
    letter-spacing: 0.7px;
}
QLabel#OverlayTimelineStatus {
    color: rgba(224, 231, 238, 185);
    font-size: 11px;
}
QLabel#OverlayTime {
    color: #F4F7FA;
    font-size: 12px;
    font-family: "Consolas";
    font-weight: 700;
    padding-left: 2px;
}
QToolButton#OverlayIconButton {
    background: rgba(255, 255, 255, 8);
    color: #F4F7FA;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 5px;
    min-width: 30px;
    min-height: 30px;
}
QToolButton#OverlayIconButton:hover {
    background: rgba(255, 255, 255, 30);
    border-color: rgba(255, 255, 255, 32);
}
QFrame#MatchHighlightProgressBar {
    background: transparent;
    border: none;
}
'''

# v36 minimal player-first interface
APP_STYLE += r'''
QFrame#Sidebar {
    background: #0C1218;
    border: none;
    border-radius: 12px;
}
QFrame#ContentPanel {
    background: #0C1218;
    border: none;
    border-radius: 12px;
}
QFrame#StatusCard {
    background: #101820;
    border: none;
    border-radius: 11px;
}
QFrame#StorageCardCompact {
    background: transparent;
    border: 1px solid #1D2832;
    border-radius: 10px;
}
QLabel#PageTitle {
    font-size: 25px;
    font-weight: 720;
}
QToolButton#HeaderIconButton,
QToolButton#SidebarIconButton,
QToolButton#OverlayActionButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 7px;
    min-width: 34px;
    min-height: 34px;
}
QToolButton#HeaderIconButton:hover,
QToolButton#SidebarIconButton:hover,
QToolButton#OverlayActionButton:hover {
    background: rgba(255, 255, 255, 18);
    border-color: rgba(255, 255, 255, 28);
}
QToolButton#OverlayActionButton:disabled {
    background: transparent;
    border-color: transparent;
}
QLabel#StorageSummary {
    color: #DDE5EC;
    font-size: 13px;
    font-weight: 650;
}
QFrame#InlineHighlightPlayer {
    background: transparent;
    border: none;
}
QFrame#InlineVideoSurface {
    background: #000000;
    border: none;
    border-radius: 10px;
}
QFrame#InlineTopOverlay {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(0, 0, 0, 205),
        stop:1 rgba(0, 0, 0, 0)
    );
    border: none;
}
QLabel#InlinePlayerOverlayTitle {
    color: #F5F7FA;
    font-size: 13px;
    font-weight: 700;
}
QLabel#InlinePlayerOverlayMeta {
    color: rgba(232, 238, 244, 185);
    font-size: 12px;
}
QLabel#InlinePlayerPoster {
    border-radius: 10px;
}
QFrame#PlayerOverlay {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(0, 0, 0, 8),
        stop:0.20 rgba(0, 0, 0, 85),
        stop:1 rgba(0, 0, 0, 235)
    );
}
'''

# v38 embedded Smart Trim and explicit Discord export
APP_STYLE += r'''
QFrame#InlineTrimPanel {
    background: #101820;
    border: 1px solid #22303B;
    border-radius: 11px;
}
QFrame#FilmstripTrimWidget {
    background: #0C131A;
    border: 1px solid #23313D;
    border-radius: 10px;
}
QProgressBar#DiscordExportProgress {
    min-height: 8px;
    max-height: 8px;
    border: none;
    border-radius: 4px;
    background: #25313B;
}
QProgressBar#DiscordExportProgress::chunk {
    background: #55D985;
    border-radius: 4px;
}
QDoubleSpinBox {
    min-height: 38px;
    min-width: 110px;
    padding: 0 9px;
    color: #E7ECF2;
    background: #151E27;
    border: 1px solid #33414E;
    border-radius: 8px;
}
QDoubleSpinBox:hover, QDoubleSpinBox:focus {
    border-color: #4B5A69;
}
'''

# v39 share/export onboarding and branded dialogs.
APP_STYLE += r"""
QDialog#ShareDialog {
    background: #0D131A;
}
QLabel#DialogTitle {
    color: #F4F7FA;
    font-size: 21px;
    font-weight: 720;
}
QPushButton#ShareChoiceButton, QPushButton#PrimaryChoiceButton {
    text-align: left;
    padding: 10px 16px;
    border-radius: 10px;
    font-size: 14px;
    font-weight: 650;
}
QPushButton#ShareChoiceButton {
    background: #121B24;
    border: 1px solid #2A3743;
    color: #E8EDF2;
}
QPushButton#ShareChoiceButton:hover {
    background: #17232D;
    border-color: #486071;
}
QPushButton#PrimaryChoiceButton {
    background: #173421;
    border: 1px solid #3C8A58;
    color: #7CF0A5;
}
QPushButton#PrimaryChoiceButton:hover {
    background: #1C442B;
    border-color: #61C982;
}
QLabel#ExportPredictionCard {
    background: #101820;
    border: 1px solid #293744;
    border-radius: 9px;
    color: #AEB9C4;
    padding: 10px 12px;
}
QLineEdit {
    min-height: 40px;
    background: #111922;
    border: 1px solid #33414E;
    border-radius: 8px;
    padding: 0 11px;
    color: #EDF2F6;
    selection-background-color: #3A8D5A;
}
QLineEdit:focus {
    border-color: #56D987;
}
QLabel#CardMuted[success="true"] {
    color: #64E391;
}
"""
APP_STYLE += r"""
QLabel#ExportWarning {
    background: #251A12;
    border: 1px solid #6E4A27;
    border-radius: 8px;
    color: #F0C37B;
    padding: 9px 11px;
}
"""

# v40 compact sidebar and reduced application chrome.
APP_STYLE += r"""
QFrame#Sidebar {
    background: #0B1117;
    border: none;
    border-radius: 10px;
}
QFrame#StatusCard {
    background: #101820;
    border: 1px solid #1C2933;
    border-radius: 10px;
}
QLabel#StatusTitle {
    color: #EAF0F5;
    font-size: 11px;
    font-weight: 720;
}
QLabel#StatusTimeCompact {
    color: #DDE6ED;
    font-family: "Consolas";
    font-size: 10px;
    font-weight: 700;
}
QLabel#SidebarErrorText {
    color: #FF7D86;
    font-size: 11px;
}
QFrame#StatusCard QPushButton#SaveClipButton,
QFrame#StatusCard QPushButton#DarkButton,
QFrame#StatusCard QPushButton#DangerButton {
    min-width: 0;
    min-height: 32px;
    max-height: 32px;
    padding: 0 7px;
    border-radius: 7px;
    font-size: 11px;
    font-weight: 680;
}
QPushButton#NavButton {
    min-height: 40px;
    max-height: 40px;
    padding: 0 10px;
    border-radius: 8px;
    text-align: left;
    font-size: 13px;
    font-weight: 620;
}
QFrame#SidebarFooter {
    background: transparent;
    border: none;
    border-radius: 7px;
}
QFrame#SidebarFooter:hover {
    background: rgba(255, 255, 255, 8);
}
QLabel#StorageSummary {
    color: #929FAB;
    font-size: 12px;
    font-weight: 620;
}
QFrame#SidebarFooter QToolButton#SidebarIconButton {
    min-width: 28px;
    max-width: 28px;
    min-height: 28px;
    max-height: 28px;
    padding: 4px;
    border-radius: 6px;
}
"""

# v43 media-first desktop refresh: flatter surfaces, compact navigation, tighter hierarchy
APP_STYLE += r'''
QFrame#TitleBar {
    background: #0A0F14;
    border-bottom: 1px solid #1B242D;
}
QFrame#TitleBrandBadge {
    border-radius: 5px;
}
QLabel#TitleStateDot {
    min-width: 8px;
    max-width: 8px;
    min-height: 8px;
    max-height: 8px;
    background: #7E8995;
    border-radius: 4px;
}
QLabel#TitleStateText {
    color: #8F9AA5;
    font-size: 11px;
    font-weight: 600;
}
QFrame#Sidebar {
    background: #0B1117;
    border: none;
    border-right: 1px solid #1A232C;
    border-radius: 0;
}
QFrame#ContentPanel {
    background: #0B1117;
    border: none;
    border-radius: 0;
}
QPushButton#NavButton {
    min-height: 42px;
    text-align: left;
    padding: 0 12px;
    background: transparent;
    border: none;
    border-left: 2px solid transparent;
    border-radius: 3px;
    color: #9EA9B4;
    font-size: 13px;
    font-weight: 560;
}
QPushButton#NavButton:hover {
    background: #121A22;
    color: #EEF2F6;
}
QPushButton#NavButton[active="true"] {
    background: #111D17;
    border-left-color: #58D889;
    color: #7BE9A0;
}
QPushButton#NavButton[compact="true"] {
    min-width: 38px;
    max-width: 38px;
    padding: 0;
    text-align: center;
}
QLabel#PageTitle {
    font-size: 24px;
    font-weight: 650;
}
QLabel#SectionTitle, QLabel#SettingsTitle {
    font-weight: 620;
}
QFrame#GameCard, QFrame#ClipCard, QFrame#SettingsSection,
QFrame#HintCard, QFrame#InfoCard, QFrame#StorageCard {
    border-radius: 6px;
}
QFrame#InlineVideoSurface {
    border-radius: 3px;
}
QLabel#InlinePlayerPoster {
    border-radius: 3px;
}
QFrame#InlineTrimPanel {
    border-radius: 5px;
}
QFrame#FilmstripTrimWidget {
    border-radius: 4px;
}
QToolButton#HeaderIconButton,
QToolButton#SidebarIconButton,
QToolButton#OverlayActionButton {
    border-radius: 3px;
}
QPushButton#PrimaryButton, QPushButton#DarkButton, QPushButton#DangerButton,
QPushButton#SaveClipButton, QPushButton#HotkeyButton, QPushButton#QuietButton {
    border-radius: 5px;
}
'''

# v44 simplified media-library cards: fewer fields, whole-row navigation, flatter hierarchy
APP_STYLE += r'''
QFrame#GameCardCompact {
    background: #0F161D;
    border: 1px solid #1C2731;
    border-radius: 4px;
}
QFrame#GameCardCompact:hover {
    background: #121B23;
    border-color: #34424E;
}
QLabel#GameTitleCompact {
    color: #F1F4F7;
    font-size: 17px;
    font-weight: 650;
}
QLabel#GameSubtitle {
    color: #9AA6B2;
    font-size: 12px;
    font-weight: 500;
}
QLabel#GameCardSummary {
    color: #7F8B97;
    font-size: 12px;
    font-weight: 500;
}
QLabel#GameCardChevron {
    min-width: 24px;
    max-width: 24px;
    color: #75818D;
    font-size: 25px;
    font-weight: 400;
}
QFrame#GameCardCompact:hover QLabel#GameCardChevron {
    color: #D6DEE5;
}
QFrame#GameCardCompact QLabel#VictoryChip,
QFrame#GameCardCompact QLabel#DefeatChip {
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 10px;
    font-weight: 620;
}
'''


# v46 controls overlay: translucent and auto-hidden during playback
APP_STYLE += r'''
QFrame#InlineTopOverlay {
    background: rgba(0, 0, 0, 70);
    border: none;
}
QFrame#PlayerOverlay {
    background: rgba(0, 0, 0, 90);
    border: none;
}
'''


# v47 unobstructed player: title and controls are outside the video frame
APP_STYLE += r'''
QFrame#InlineTopOverlay {
    background: transparent;
    border: none;
}
QFrame#PlayerOverlay {
    background: #080C11;
    border: 1px solid #1B2530;
    border-top: none;
}
'''

# v48 match context in the native title bar and a full-width centered video frame
APP_STYLE += r'''
QLabel#WindowContext {
    color: #8E9AA7;
    font-size: 12px;
    font-weight: 560;
}
QToolButton#TitleBackButton {
    min-width: 34px;
    max-width: 34px;
    min-height: 34px;
    max-height: 34px;
    padding: 0;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
}
QToolButton#TitleBackButton:hover {
    background: #17212A;
    border-color: #2B3742;
}
'''

# v50 persistent title-bar menu and settings moved out of the sidebar
APP_STYLE += r'''
QToolButton#MainMenuButton {
    min-width: 34px;
    max-width: 34px;
    min-height: 34px;
    max-height: 34px;
    padding: 0;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
}
QToolButton#MainMenuButton:hover,
QToolButton#MainMenuButton:pressed,
QToolButton#MainMenuButton::menu-button:hover {
    background: #17212A;
    border-color: #2B3742;
}
QToolButton#MainMenuButton::menu-indicator {
    image: none;
    width: 0;
}
QMenu#MainMenu {
    background: #111820;
    border: 1px solid #2A3540;
    border-radius: 7px;
    padding: 5px;
}
QMenu#MainMenu::item {
    min-width: 150px;
    min-height: 34px;
    padding: 0 12px 0 10px;
    border-radius: 5px;
    color: #E4EAF0;
}
QMenu#MainMenu::item:selected {
    background: #1A2530;
    color: #FFFFFF;
}
QMenu#MainMenu::icon {
    padding-left: 5px;
}
'''

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

