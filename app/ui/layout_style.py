from __future__ import annotations

LAYOUT_STYLE = r"""
/* ============================================================
   League Highlights — structural UI pass 2 / Live Match V9
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


/* Live Match V9 */
QFrame#LiveMatchStatusBar,
QFrame#LiveApiBanner,
QFrame#LiveTeamSection,
QFrame#LivePlayerCard {
    background: #10161D;
    border: 1px solid #202B36;
    border-radius: 8px;
}

QFrame#LiveApiBanner {
    background: #111A17;
    border-color: #294435;
}

QFrame#LiveTeamSection {
    background: #0E141A;
}

QFrame#LivePlayerCard {
    min-height: 212px;
    max-height: 236px;
}

QFrame#LivePlayerCard:hover {
    background: #141C24;
    border-color: #344250;
}

QLabel#LiveTeamHeading {
    color: #8B98A5;
    font-size: 11px;
    font-weight: 700;
    padding: 0 1px;
}

QLabel#LiveChampionBadge {
    color: #DFF2E5;
    padding: 0;
    background: #142019;
    border: 1px solid #31513D;
    border-radius: 7px;
    font-size: 17px;
    font-weight: 700;
}

QLabel#LivePlayerName {
    color: #EDF1F5;
    font-size: 12px;
    font-weight: 650;
}

QLabel#LiveChampionName {
    color: #95A2AF;
    font-size: 11px;
}

QLabel#LiveRankText {
    color: #D9E1E8;
    font-size: 12px;
    font-weight: 650;
}

QLabel#LiveRecordText,
QLabel#LiveRecentText {
    color: #81909D;
    font-size: 10px;
}

QLabel#LiveRoleChip,
QLabel#LiveTagChip {
    border-radius: 5px;
    padding: 2px 5px;
    font-size: 9px;
    font-weight: 650;
}

QLabel#LiveRoleChip {
    color: #B8C4CF;
    background: #18212A;
    border: 1px solid #2A3540;
}

QLabel#LiveTagChip {
    color: #7BE2A0;
    background: #13231A;
    border: 1px solid #28503A;
}

QLabel#LiveTagChip[tone="warning"] {
    color: #F1BD70;
    background: #281F13;
    border-color: #6C4B20;
}

QLabel#LiveTagChip[tone="negative"] {
    color: #F18B92;
    background: #26171B;
    border-color: #573038;
}

QLabel#LiveEmptyState {
    color: #7E8B98;
    min-height: 110px;
    padding: 18px 10px;
}

QLabel#LiveMatchStatusDot {
    min-width: 8px;
    max-width: 8px;
    min-height: 8px;
    max-height: 8px;
    border-radius: 4px;
    background: #697684;
}

QLabel#LiveMatchStatusDot[state="loading"] {
    background: #66B7FF;
}

QLabel#LiveMatchStatusDot[state="ready"] {
    background: #58DB8A;
}

QLabel#LiveMatchStatusDot[state="key_missing"],
QLabel#LiveMatchStatusDot[state="rate_limited"] {
    background: #E0B45F;
}

QLabel#LiveMatchStatusDot[state="key_invalid"],
QLabel#LiveMatchStatusDot[state="error"] {
    background: #EE6974;
}

QLabel#LiveMatchStatusText {
    color: #C7D0D9;
    font-size: 11px;
    font-weight: 600;
}

QScrollArea#LiveMatchScroll {
    border: none;
    background: transparent;
}

QLineEdit#RiotApiKeyInput {
    min-height: 36px;
    padding: 0 10px;
    color: #E2E7EC;
    background: #131A22;
    border: 1px solid #2C3742;
    border-radius: 6px;
}

QLineEdit#RiotApiKeyInput:focus {
    border-color: #4B8160;
}

/* Compact radius pass */
QFrame#SettingsSection,
QFrame#InfoCard,
QFrame#GameCardCompact,
QFrame#ClipCard,
QFrame#LibraryToolbar,
QFrame#SettingsSideNavigation {
    border-radius: 8px;
}

QPushButton#PrimaryButton,
QPushButton#DarkButton,
QPushButton#DangerButton,
QPushButton#ToolbarButton,
QPushButton#SettingsTab,
QPushButton#NavButton,
QComboBox,
QDoubleSpinBox,
QLineEdit#LibrarySearch {
    border-radius: 6px;
}

QLabel#LiveRoleDetail {
    color: #8D9AA7;
    font-size: 9px;
}

QLabel#LiveTeamSummary {
    color: #74818E;
    font-size: 10px;
}

QFrame#LivePlayerCard {
    min-height: 232px;
    max-height: 252px;
}



QFrame#LivePlayerCard {
    min-height: 266px;
    max-height: 286px;
    background: #0F151C;
    border-color: #22303B;
}

QLabel#LiveLevelChip {
    min-width: 44px;
    padding: 1px 6px;
    color: #BFE7CC;
    background: #132018;
    border: 1px solid #2E4B38;
    border-radius: 5px;
    font-size: 10px;
    font-weight: 700;
}

QLabel#LiveRoleIcon,
QLabel#LiveRankIcon {
    background: transparent;
    border: none;
}

QLabel#LiveRoleChip {
    min-height: 20px;
    padding: 1px 6px;
    color: #CBD5DE;
    background: #16202A;
    border: 1px solid #2A3540;
    border-radius: 6px;
    font-size: 10px;
    font-weight: 650;
}

QLabel#LiveRankText {
    color: #E6EDF4;
    font-size: 12px;
    font-weight: 700;
}

QLabel#LiveRecordText {
    color: #95A2AF;
    font-size: 10px;
}

QLabel#LiveRecentText,
QLabel#LiveRoleDetail,
QLabel#LiveChampionName {
    color: #8392A0;
    font-size: 10px;
}

QLabel#LiveTagChip {
    min-height: 18px;
    padding: 1px 6px;
    border-radius: 5px;
    font-size: 9px;
    font-weight: 650;
}

QLabel#LiveTeamSummary {
    color: #7F8C99;
    font-size: 10px;
    font-weight: 500;
}

QFrame#LiveMatchStatusBar {
    background: #0F151C;
}

QPushButton#DarkButton {
    min-height: 34px;
}



QFrame#LivePlayerCard {
    min-height: 292px;
    max-height: 316px;
}

QLabel#LiveRankIcon {
    min-width: 48px;
    max-width: 48px;
    min-height: 48px;
    max-height: 48px;
}

QLabel#LiveDivisionBadge {
    min-width: 28px;
    max-width: 42px;
    padding: 0 4px;
    color: #D6DEE6;
    background: #161D25;
    border: 1px solid #303B46;
    border-radius: 4px;
    font-size: 9px;
    font-weight: 700;
}

QLabel#LiveTagChip[tone="neutral"] {
    color: #AFC7DB;
    background: #15212B;
    border-color: #2E4658;
}

QLabel#LiveRoleIcon {
    min-width: 18px;
    max-width: 18px;
    min-height: 18px;
    max-height: 18px;
}

QLabel#LiveTagChip {
    min-height: 18px;
    padding: 1px 5px;
}



/* Live Match V10 — compact two-column player rows */
QFrame#LiveCompactTeam {
    background: #0E141A;
    border: 1px solid #202B36;
    border-radius: 8px;
}

QFrame#LivePlayerRow {
    background: #10171E;
    border: 1px solid #22303B;
    border-radius: 7px;
}

QFrame#LivePlayerRow:hover {
    background: #141D25;
    border-color: #3A4A58;
}

QLabel#LiveChampionPortrait {
    color: #DFF2E5;
    background: #142019;
    border: 1px solid #31513D;
    border-radius: 7px;
    font-size: 18px;
    font-weight: 700;
}

QLabel#LiveCompactPlayerName {
    color: #F0F3F6;
    font-size: 12px;
    font-weight: 700;
}

QLabel#LiveCompactLevel {
    min-width: 43px;
    padding: 1px 5px;
    color: #BFE7CC;
    background: #132018;
    border: 1px solid #2D4B37;
    border-radius: 5px;
    font-size: 9px;
    font-weight: 700;
}

QLabel#LiveCompactChampion,
QLabel#LiveCompactRole {
    color: #91A0AE;
    font-size: 10px;
}

QLabel#LiveCompactSeparator {
    color: #45515D;
    font-size: 10px;
}

QLabel#LiveCompactRoleIcon,
QLabel#LiveCompactRankIcon {
    background: transparent;
    border: none;
}

QLabel#LiveCompactRankText {
    color: #E5EBF1;
    font-size: 10px;
    font-weight: 700;
}

QLabel#LiveCompactTag,
QLabel#LiveCompactTagMore {
    min-height: 18px;
    padding: 1px 6px;
    border-radius: 5px;
    font-size: 8px;
    font-weight: 700;
}

QLabel#LiveCompactTag {
    color: #7BE2A0;
    background: #13231A;
    border: 1px solid #28503A;
}

QLabel#LiveCompactTag[tone="warning"] {
    color: #F1BD70;
    background: #281F13;
    border-color: #6C4B20;
}

QLabel#LiveCompactTag[tone="negative"] {
    color: #F18B92;
    background: #26171B;
    border-color: #573038;
}

QLabel#LiveCompactTag[tone="neutral"] {
    color: #A7C9E8;
    background: #14212C;
    border-color: #2B4A60;
}

QLabel#LiveCompactTagMore {
    color: #AAB5C0;
    background: #18212A;
    border: 1px solid #2D3944;
}

QLabel#LiveCompactTeamHeading {
    color: #A7B4C0;
    font-size: 10px;
    font-weight: 750;
}

QLabel#LiveCompactTeamSummary {
    color: #71808E;
    font-size: 9px;
}

QLabel#LiveCompactEmpty {
    min-height: 470px;
    color: #7E8B98;
    padding: 16px;
}

QFrame#LiveMatchStatusBar {
    min-height: 28px;
    max-height: 30px;
}



/* Live Match V11 — stacked teams, five compact cards across */
QFrame#LiveStackedTeam {
    background: #0E141A;
    border: 1px solid #202B36;
    border-radius: 8px;
}

QFrame#LiveStackedPlayerCard {
    background: #10171E;
    border: 1px solid #22303B;
    border-radius: 7px;
}

QFrame#LiveStackedPlayerCard:hover {
    background: #141D25;
    border-color: #3A4A58;
}

QLabel#LiveStackedChampion {
    color: #DFF2E5;
    background: #142019;
    border: 1px solid #31513D;
    border-radius: 7px;
    font-size: 18px;
    font-weight: 700;
}

QLabel#LiveStackedName {
    color: #F0F3F6;
    font-size: 11px;
    font-weight: 700;
}

QLabel#LiveStackedLevel {
    min-width: 42px;
    padding: 1px 5px;
    color: #BFE7CC;
    background: #132018;
    border: 1px solid #2D4B37;
    border-radius: 5px;
    font-size: 9px;
    font-weight: 700;
}

QLabel#LiveStackedChampionName,
QLabel#LiveStackedRole,
QLabel#LiveStackedQuickLine {
    color: #8795A3;
    font-size: 9px;
}

QLabel#LiveStackedRoleIcon,
QLabel#LiveStackedRankIcon {
    background: transparent;
    border: none;
}

QLabel#LiveStackedRankText {
    color: #E7EDF3;
    font-size: 11px;
    font-weight: 700;
}

QLabel#LiveStackedTag,
QLabel#LiveStackedTagMore {
    min-height: 18px;
    padding: 1px 5px;
    border-radius: 5px;
    font-size: 8px;
    font-weight: 700;
}

QLabel#LiveStackedTag {
    color: #7BE2A0;
    background: #13231A;
    border: 1px solid #28503A;
}

QLabel#LiveStackedTag[tone="warning"] {
    color: #F1BD70;
    background: #281F13;
    border-color: #6C4B20;
}

QLabel#LiveStackedTag[tone="negative"] {
    color: #F18B92;
    background: #26171B;
    border-color: #573038;
}

QLabel#LiveStackedTag[tone="neutral"] {
    color: #A7C9E8;
    background: #14212C;
    border-color: #2B4A60;
}

QLabel#LiveStackedTagMore {
    color: #AAB5C0;
    background: #18212A;
    border: 1px solid #2D3944;
}

QLabel#LiveStackedTeamHeading {
    color: #A7B4C0;
    font-size: 10px;
    font-weight: 750;
}

QLabel#LiveStackedTeamSummary {
    color: #71808E;
    font-size: 9px;
}

QLabel#LiveStackedEmpty {
    min-height: 190px;
    color: #7E8B98;
    padding: 14px;
}

QFrame#LiveMatchStatusBar {
    min-height: 26px;
    max-height: 28px;
}



/* Live Match V13 — ranked-only win rate and previous-season rank */
QLabel#LivePreviousSeasonRank {
    color: #748493;
    font-size: 8px;
}

QLabel#LivePreviousSeasonRank:hover {
    color: #9AACBC;
}



/* Live Match V15 — loading-screen states, V13 layout preserved */
QLabel#LiveMatchStatusDot[state="loading_screen"],
QLabel#LiveMatchStatusDot[state="champ_select"] {
    background: #66B7FF;
}



/* Live Match V16 — slightly denser cards */
QFrame#LiveStackedPlayerCard {
    min-height: 218px;
}



/* Live Match V19 — full-width readable tag stack */
QLabel#LiveStackedTag,
QLabel#LiveStackedTagMore {
    min-height: 19px;
    padding: 2px 6px;
    font-size: 8px;
}


"""