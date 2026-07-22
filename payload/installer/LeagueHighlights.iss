#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

#define MyAppName "League Highlights"
#define MyAppPublisher "Kirbyy1"
#define MyAppExeName "LeagueHighlights.exe"

[Setup]
AppId={{2F65DA42-1E02-4E50-972A-33BD57FD668A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\LeagueHighlights
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\release\{#MyAppVersion}
OutputBaseFilename=LeagueHighlightsSetup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\app\assets\league_highlights.ico
UninstallDisplayIcon={app}\current\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Files]
Source: "..\dist\LeagueHighlights\*"; DestDir: "{app}\current"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\current\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\current\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Run]
Filename: "{app}\current\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
