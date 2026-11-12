# Downloads the gyan.dev "release essentials" FFmpeg build, checks it against
# the publisher's SHA-256 and places ffmpeg.exe next to LeagueHighlights.exe
# (only ffmpeg.exe is needed; the Rust version no longer uses ffprobe).
#
# The essentials build has every component the app uses and is about half the
# size of the "full" build (100 MB vs 217 MB for FFmpeg 9.0).
param(
    [string]$Destination = (Join-Path (Split-Path -Parent $PSScriptRoot) "target\release")
)
$ErrorActionPreference = "Stop"
# Invoke-WebRequest is many times slower while it draws its progress bar.
$ProgressPreference = "SilentlyContinue"

$Url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
$Archive = Join-Path $env:TEMP "ffmpeg-release-essentials.zip"
$Extracted = Join-Path $env:TEMP "league-highlights-ffmpeg"

New-Item -ItemType Directory -Force -Path $Destination | Out-Null
Remove-Item -Recurse -Force $Extracted -ErrorAction SilentlyContinue
Remove-Item -Force $Archive -ErrorAction SilentlyContinue

Write-Host "Downloading FFmpeg release essentials (about 115 MB)..." -ForegroundColor Cyan
Invoke-WebRequest -Uri $Url -OutFile $Archive -UseBasicParsing

$published = (Invoke-WebRequest -Uri "$Url.sha256" -UseBasicParsing).Content
if ($published -is [byte[]]) { $published = [Text.Encoding]::ASCII.GetString($published) }
$expected = ($published.Trim() -split "\s+")[0].ToLower()
$actual = (Get-FileHash $Archive -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $expected) {
    Remove-Item -Force $Archive -ErrorAction SilentlyContinue
    throw "The FFmpeg download does not match its published SHA-256 (expected $expected, got $actual). Try again."
}
Write-Host "Checksum verified: $actual"

Expand-Archive -Path $Archive -DestinationPath $Extracted -Force

$Ffmpeg = Get-ChildItem -Path $Extracted -Filter ffmpeg.exe -Recurse | Select-Object -First 1
if (-not $Ffmpeg) { throw "The downloaded archive did not contain ffmpeg.exe." }
Copy-Item $Ffmpeg.FullName (Join-Path $Destination "ffmpeg.exe") -Force

Remove-Item -Recurse -Force $Extracted -ErrorAction SilentlyContinue
Remove-Item -Force $Archive -ErrorAction SilentlyContinue
Write-Host "FFmpeg installed in $Destination" -ForegroundColor Green
& (Join-Path $Destination "ffmpeg.exe") -hide_banner -version | Select-Object -First 1
