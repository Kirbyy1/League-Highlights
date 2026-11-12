# Builds a release: dist\LeagueHighlights-<version>.zip (portable),
# dist\LeagueHighlightsSetup-<version>.exe (installer) and SHA256SUMS.txt.
#
#   .\scripts\package.ps1                 # uses ffmpeg.exe from target\release or PATH
#   .\scripts\package.ps1 -Ffmpeg C:\path\to\ffmpeg.exe
#   .\scripts\package.ps1 -NoInstaller    # zip only
param(
    [string]$Ffmpeg = "",
    [switch]$NoInstaller
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Version = (Select-String -Path "Cargo.toml" -Pattern '^version = "(.+)"' | Select-Object -First 1).Matches[0].Groups[1].Value
Write-Host "League Highlights $Version" -ForegroundColor Cyan

cargo build --release --locked
if ($LASTEXITCODE -ne 0) { throw "cargo build failed" }

$Dist = Join-Path $Root "dist"
$Stage = Join-Path $Dist "LeagueHighlights-$Version"
Remove-Item -Recurse -Force $Stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

Copy-Item "target\release\LeagueHighlights.exe" $Stage
Copy-Item "README.md" $Stage

if (-not $Ffmpeg) {
    $candidates = @("target\release\ffmpeg.exe")
    $onPath = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += $onPath.Source }
    $Ffmpeg = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $Ffmpeg) {
    Write-Host "No ffmpeg.exe found; downloading the release essentials build" -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot "download_ffmpeg.ps1") -Destination $Stage
} else {
    Copy-Item $Ffmpeg (Join-Path $Stage "ffmpeg.exe")
}
$ffVersion = (& (Join-Path $Stage "ffmpeg.exe") -hide_banner -version | Select-Object -First 1)
$gpl = (& (Join-Path $Stage "ffmpeg.exe") -hide_banner -version | Select-String "--enable-gpl") -ne $null

@"
League Highlights $Version

Bundled third-party software
----------------------------
ffmpeg.exe - $ffVersion
FFmpeg is free software licensed under the $(if ($gpl) { "GNU General Public License v3 (this build enables GPL components)" } else { "GNU Lesser General Public License v2.1" }).
Source code: https://ffmpeg.org/download.html  (Windows builds: https://www.gyan.dev/ffmpeg/builds/)

Champion art is loaded at runtime from Riot Games' Data Dragon. League Highlights
isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot Games
or anyone officially involved in producing or managing Riot Games properties.
Riot Games, and all associated properties are trademarks or registered trademarks
of Riot Games, Inc.
"@ | Set-Content (Join-Path $Stage "THIRD-PARTY-NOTICES.txt") -Encoding utf8

$Zip = Join-Path $Dist "LeagueHighlights-$Version.zip"
Remove-Item $Zip -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $Zip -CompressionLevel Optimal
$outputs = @($Zip)

if (-not $NoInstaller) {
    $iscc = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup), or pass -NoInstaller" }
    & $iscc /Q "/DAppVersion=$Version" "/DStageDir=$Stage" "/O$Dist" "installer\LeagueHighlights.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    $outputs += Join-Path $Dist "LeagueHighlightsSetup-$Version.exe"
}

$sums = $outputs | ForEach-Object { "{0}  {1}" -f (Get-FileHash $_ -Algorithm SHA256).Hash.ToLower(), (Split-Path $_ -Leaf) }
$sums | Set-Content (Join-Path $Dist "SHA256SUMS.txt") -Encoding ascii
Write-Host ""
$outputs | ForEach-Object { "{0,-44} {1,8:N1} MB" -f (Split-Path $_ -Leaf), ((Get-Item $_).Length / 1MB) }
Write-Host "Checksums in dist\SHA256SUMS.txt" -ForegroundColor Green
