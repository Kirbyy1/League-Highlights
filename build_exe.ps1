$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$Ffmpeg = Join-Path $PSScriptRoot "tools\ffmpeg\bin\ffmpeg.exe"
$Ffprobe = Join-Path $PSScriptRoot "tools\ffmpeg\bin\ffprobe.exe"
$AppIcon = Join-Path $PSScriptRoot "app\assets\league_highlights.ico"
$Assets = Join-Path $PSScriptRoot "app\assets"

if (-not (Test-Path $Python)) { throw "Run setup.ps1 first." }
if (-not (Test-Path $Ffmpeg) -or -not (Test-Path $Ffprobe)) { throw "FFmpeg is missing. Run setup.ps1 first." }
if (-not (Test-Path $AppIcon)) { throw "Application icon is missing." }

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name "LeagueHighlights" `
    --icon "$AppIcon" `
    --add-data "$Assets;app\assets" `
    --add-binary "$Ffmpeg;tools\ffmpeg\bin" `
    --add-binary "$Ffprobe;tools\ffmpeg\bin" `
    --collect-all pyaudiowpatch `
    main.py

$UpdaterDist = Join-Path $PSScriptRoot "build\updater-dist"
$UpdaterWork = Join-Path $PSScriptRoot "build\updater-work"
$UpdaterSpec = Join-Path $PSScriptRoot "build\updater-spec"
Remove-Item $UpdaterDist -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $UpdaterWork -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $UpdaterSpec -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $UpdaterDist, $UpdaterWork, $UpdaterSpec | Out-Null

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onefile `
    --name "LeagueHighlightsUpdater" `
    --distpath "$UpdaterDist" `
    --workpath "$UpdaterWork" `
    --specpath "$UpdaterSpec" `
    updater.py

Copy-Item `
    (Join-Path $UpdaterDist "LeagueHighlightsUpdater.exe") `
    (Join-Path $PSScriptRoot "dist\LeagueHighlights\LeagueHighlightsUpdater.exe") `
    -Force

& $Python (Join-Path $PSScriptRoot "scripts\make_release.py")
$Version = (& $Python -c "from app.version import APP_VERSION; print(APP_VERSION)").Trim()
$Iscc = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
if ($Iscc) {
    & $Iscc.Source "/DMyAppVersion=$Version" (Join-Path $PSScriptRoot "installer\LeagueHighlights.iss")
} else {
    Write-Host "Inno Setup was not found in PATH; the ZIP and update manifest were still created." -ForegroundColor Yellow
}

Write-Host "Build created in dist\LeagueHighlights" -ForegroundColor Green
Write-Host "Release assets created in release\$Version" -ForegroundColor Green
