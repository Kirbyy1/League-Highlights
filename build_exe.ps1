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

Write-Host "Build created in dist\LeagueHighlights" -ForegroundColor Green
