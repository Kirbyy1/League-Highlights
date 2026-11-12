# Downloads the gyan.dev "release essentials" FFmpeg build and places
# ffmpeg.exe next to LeagueHighlights.exe (only ffmpeg.exe is needed; the Rust
# version no longer uses ffprobe).
param(
    [string]$Destination = (Join-Path (Split-Path -Parent $PSScriptRoot) "target\release")
)
$ErrorActionPreference = "Stop"

$Url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
$Archive = Join-Path $env:TEMP "ffmpeg-release-essentials.zip"
$Extracted = Join-Path $env:TEMP "league-highlights-ffmpeg"

New-Item -ItemType Directory -Force -Path $Destination | Out-Null
Remove-Item -Recurse -Force $Extracted -ErrorAction SilentlyContinue
Remove-Item -Force $Archive -ErrorAction SilentlyContinue

Write-Host "Downloading FFmpeg release essentials..." -ForegroundColor Cyan
Invoke-WebRequest -Uri $Url -OutFile $Archive
Expand-Archive -Path $Archive -DestinationPath $Extracted -Force

$Ffmpeg = Get-ChildItem -Path $Extracted -Filter ffmpeg.exe -Recurse | Select-Object -First 1
if (-not $Ffmpeg) { throw "The downloaded archive did not contain ffmpeg.exe." }
Copy-Item $Ffmpeg.FullName (Join-Path $Destination "ffmpeg.exe") -Force

Remove-Item -Recurse -Force $Extracted -ErrorAction SilentlyContinue
Remove-Item -Force $Archive -ErrorAction SilentlyContinue
Write-Host "FFmpeg installed in $Destination" -ForegroundColor Green
& (Join-Path $Destination "ffmpeg.exe") -hide_banner -version | Select-Object -First 1
