$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Destination = Join-Path $ProjectRoot "tools\ffmpeg\bin"
$Archive = Join-Path $env:TEMP "ffmpeg-release-essentials.zip"
$Extracted = Join-Path $env:TEMP "league-highlights-ffmpeg"
$Url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

Write-Host "Downloading the current FFmpeg release essentials build..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
Remove-Item -Recurse -Force $Extracted -ErrorAction SilentlyContinue
Remove-Item -Force $Archive -ErrorAction SilentlyContinue

Invoke-WebRequest -Uri $Url -OutFile $Archive
Expand-Archive -Path $Archive -DestinationPath $Extracted -Force

$Ffmpeg = Get-ChildItem -Path $Extracted -Filter ffmpeg.exe -Recurse | Select-Object -First 1
$Ffprobe = Get-ChildItem -Path $Extracted -Filter ffprobe.exe -Recurse | Select-Object -First 1

if (-not $Ffmpeg -or -not $Ffprobe) {
    throw "The downloaded archive did not contain ffmpeg.exe and ffprobe.exe."
}

Copy-Item $Ffmpeg.FullName (Join-Path $Destination "ffmpeg.exe") -Force
Copy-Item $Ffprobe.FullName (Join-Path $Destination "ffprobe.exe") -Force

Remove-Item -Recurse -Force $Extracted -ErrorAction SilentlyContinue
Remove-Item -Force $Archive -ErrorAction SilentlyContinue

Write-Host "FFmpeg installed in $Destination" -ForegroundColor Green
& (Join-Path $Destination "ffmpeg.exe") -hide_banner -version | Select-Object -First 1
