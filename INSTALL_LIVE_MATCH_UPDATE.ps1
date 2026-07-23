param(
    [string]$ProjectRoot = ""
)

$ErrorActionPreference = "Stop"
$PatchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "League Highlights - Live Match V6 cumulative source installer" -ForegroundColor Cyan
Write-Host ""

if (-not $ProjectRoot) {
    $Current = (Get-Location).Path
    $KnownProject = "C:\Users\alekkum\PycharmProjects\LeagueHighlights"

    if ((Test-Path (Join-Path $Current "main.py")) -and
        (Test-Path (Join-Path $Current "app"))) {
        $ProjectRoot = $Current
    }
    elseif ((Test-Path (Join-Path $KnownProject "main.py")) -and
            (Test-Path (Join-Path $KnownProject "app"))) {
        $ProjectRoot = $KnownProject
    }
    else {
        $ProjectRoot = Read-Host "Paste your LeagueHighlights project folder"
    }
}

$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$PatchRoot = [System.IO.Path]::GetFullPath($PatchRoot)

if (-not (Test-Path (Join-Path $ProjectRoot "main.py"))) {
    throw "main.py was not found in: $ProjectRoot"
}
if (-not (Test-Path (Join-Path $ProjectRoot "app"))) {
    throw "The app folder was not found in: $ProjectRoot"
}

$PatchFiles = @(
    "app\services\live_match_scout.py",
    "app\ui\live_match_page.py",
    "app\ui\layout_style.py"
)

foreach ($Relative in $PatchFiles) {
    $Source = Join-Path $PatchRoot $Relative
    if (-not (Test-Path $Source)) {
        throw "Patch file missing: $Source"
    }
}

Write-Host "Project: $ProjectRoot"
Write-Host "Patch:   $PatchRoot"
Write-Host ""

$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$BackupRoot = Join-Path $ProjectRoot "_live_match_backup_$Timestamp"

$SameFolder = $PatchRoot.TrimEnd("\") -ieq $ProjectRoot.TrimEnd("\")

if (-not $SameFolder) {
    foreach ($Relative in $PatchFiles) {
        $Target = Join-Path $ProjectRoot $Relative
        if (Test-Path $Target) {
            $Backup = Join-Path $BackupRoot $Relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $Backup) -Force | Out-Null
            Copy-Item $Target $Backup -Force
        }
    }

    Write-Host "Backup created: $BackupRoot" -ForegroundColor DarkGray

    foreach ($Relative in $PatchFiles) {
        $Source = Join-Path $PatchRoot $Relative
        $Target = Join-Path $ProjectRoot $Relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $Target) -Force | Out-Null
        Copy-Item $Source $Target -Force
        Write-Host "Replaced $Relative"
    }
}
else {
    Write-Host "The update was extracted directly into the project folder." -ForegroundColor Yellow
    Write-Host "The files are already in their target locations; verifying them now."
}

Get-ChildItem -Path $ProjectRoot -Directory -Filter "__pycache__" -Recurse -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$ServicePath = Join-Path $ProjectRoot "app\services\live_match_scout.py"
$UiPath = Join-Path $ProjectRoot "app\ui\live_match_page.py"
$StylePath = Join-Path $ProjectRoot "app\ui\layout_style.py"

$ServiceText = Get-Content $ServicePath -Raw
$UiText = Get-Content $UiPath -Raw
$StyleText = Get-Content $StylePath -Raw

$Checks = [ordered]@{
    "V6 service build" = $ServiceText.Contains('LIVE_MATCH_PATCH_BUILD = "V6-CUMULATIVE-SOURCE"')
    "V6 UI build" = $UiText.Contains('LIVE_MATCH_UI_BUILD = "V6-CUMULATIVE-SOURCE"')
    "PUUID rank lookup" = $ServiceText.Contains("/entries/by-puuid/")
    "Old missing-ID error removed" = -not $ServiceText.Contains("Riot summoner response did not include an ID")
    "20-game analysis" = $ServiceText.Contains("MATCH_SAMPLE_SIZE = 20")
    "Vertical player cards" = $UiText.Contains("class PlayerScoutCard")
    "Two team sections" = $UiText.Contains("class TeamSection")
    "Background analysis thread" = $ServiceText.Contains("LeagueHighlightsLiveMatch")
    "Main-role analysis" = $ServiceText.Contains("LIKELY OFF-ROLE")
    "Premade analysis" = $ServiceText.Contains("Premade ")
    "Live Match V2 styles" = $StyleText.Contains("Live Match V2")
}

Write-Host ""
$Failed = $false
foreach ($Item in $Checks.GetEnumerator()) {
    if ($Item.Value) {
        Write-Host ("PASS  " + $Item.Key) -ForegroundColor Green
    }
    else {
        Write-Host ("FAIL  " + $Item.Key) -ForegroundColor Red
        $Failed = $true
    }
}

Write-Host ""
if ($Failed) {
    throw "The update did not install correctly."
}

Write-Host "UPDATE INSTALLED SUCCESSFULLY." -ForegroundColor Green
Write-Host ""
Write-Host "Important: fully stop the current PyCharm run and start main.py again."
Write-Host "Refreshing the page is not enough because Python has already loaded the old modules."
