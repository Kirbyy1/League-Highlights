; League Highlights installer (Inno Setup 6). Built by scripts\package.ps1:
;   ISCC /DAppVersion=2.0.0 /DStageDir=<staged files> installer\LeagueHighlights.iss
; Per-user install: no administrator prompt. Clips, settings and logs in
; %LOCALAPPDATA%\LeagueHighlights and the clip folder are kept on uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef StageDir
  #define StageDir "..\dist\stage"
#endif

[Setup]
AppId={{8F3B5C2A-4D1E-4B7A-9C6E-2A1F0B9D7E31}
AppName=League Highlights
AppVersion={#AppVersion}
AppVerName=League Highlights {#AppVersion}
AppPublisher=League Highlights
AppPublisherURL=https://github.com/Kirbyy1/League-Highlights
AppUpdatesURL=https://github.com/Kirbyy1/League-Highlights/releases
DefaultDirName={localappdata}\Programs\League Highlights
DisableDirPage=auto
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputBaseFilename=LeagueHighlightsSetup-{#AppVersion}
SetupIconFile=..\assets\league_highlights.ico
UninstallDisplayIcon={app}\LeagueHighlights.exe
UninstallDisplayName=League Highlights
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "startup"; Description: "Start with Windows (quietly, in the notification area)"
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#StageDir}\LeagueHighlights.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\ffmpeg.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\THIRD-PARTY-NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\League Highlights"; Filename: "{app}\LeagueHighlights.exe"
Name: "{userdesktop}\League Highlights"; Filename: "{app}\LeagueHighlights.exe"; Tasks: desktopicon

[Registry]
; Same value name the app's own "Launch with Windows" setting uses.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "League Highlights"; ValueData: """{app}\LeagueHighlights.exe"" --startup"; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\LeagueHighlights.exe"; Description: "Open League Highlights"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM LeagueHighlights.exe /F"; Flags: runhidden; RunOnceId: "StopLeagueHighlights"

[UninstallDelete]
; Only regenerable data; clips and settings stay.
Type: filesandordirs; Name: "{localappdata}\LeagueHighlights\buffer"
Type: filesandordirs; Name: "{localappdata}\LeagueHighlights\webview"
Type: filesandordirs; Name: "{localappdata}\LeagueHighlights\assets"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // The start-up entry may also have been enabled from the app's settings.
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'League Highlights');
end;
