$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host ""
Write-Host "League Highlights - Live Match V6 verification" -ForegroundColor Cyan
Write-Host "Project root: $ProjectRoot"
Write-Host ""

$ServicePath = Join-Path $ProjectRoot "app\services\live_match_scout.py"
$UiPath = Join-Path $ProjectRoot "app\ui\live_match_page.py"

if (-not (Test-Path $ServicePath)) { throw "Missing: $ServicePath" }
if (-not (Test-Path $UiPath)) { throw "Missing: $UiPath" }

$ServiceText = Get-Content $ServicePath -Raw
$UiText = Get-Content $UiPath -Raw

Write-Host "Service build V6: " -NoNewline
Write-Host ($ServiceText.Contains('LIVE_MATCH_PATCH_BUILD = "V6-CUMULATIVE-SOURCE"'))
Write-Host "UI build V6: " -NoNewline
Write-Host ($UiText.Contains('LIVE_MATCH_UI_BUILD = "V6-CUMULATIVE-SOURCE"'))
Write-Host "Uses PUUID lookup: " -NoNewline
Write-Host ($ServiceText.Contains("/entries/by-puuid/"))
Write-Host "Old missing-ID text present: " -NoNewline
Write-Host ($ServiceText.Contains("Riot summoner response did not include an ID"))
Write-Host "20-game sample: " -NoNewline
Write-Host ($ServiceText.Contains("MATCH_SAMPLE_SIZE = 20"))
Write-Host "Vertical UI: " -NoNewline
Write-Host ($UiText.Contains("class PlayerScoutCard"))

if (
    $ServiceText.Contains('LIVE_MATCH_PATCH_BUILD = "V6-CUMULATIVE-SOURCE"') -and
    $UiText.Contains('LIVE_MATCH_UI_BUILD = "V6-CUMULATIVE-SOURCE"') -and
    $ServiceText.Contains("/entries/by-puuid/") -and
    -not $ServiceText.Contains("Riot summoner response did not include an ID") -and
    $ServiceText.Contains("MATCH_SAMPLE_SIZE = 20") -and
    $UiText.Contains("class PlayerScoutCard")
) {
    Write-Host ""
    Write-Host "PASS: the cumulative Live Match source is installed." -ForegroundColor Green
    exit 0
}

Write-Host ""
Write-Host "FAIL: old or incomplete source files are still present." -ForegroundColor Red
exit 1
