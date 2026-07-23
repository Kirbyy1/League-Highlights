$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "League Highlights Live Match source verification" -ForegroundColor Cyan
Write-Host "Project root: $PWD"

$pythonCandidates = @(
    ".\.venv\Scripts\python.exe",
    ".\venv\Scripts\python.exe",
    "python.exe",
    "py.exe"
)

$python = $null
foreach ($candidate in $pythonCandidates) {
    try {
        if ($candidate -in @("python.exe", "py.exe")) {
            $command = Get-Command $candidate -ErrorAction Stop
            $python = $command.Source
            break
        }
        if (Test-Path $candidate) {
            $python = (Resolve-Path $candidate).Path
            break
        }
    } catch {}
}

if (-not $python) {
    throw "Python was not found. Run this from your LeagueHighlights project root."
}

Get-ChildItem -Path . -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$check = @'
from pathlib import Path
import app.services.live_match_scout as scout
import app.ui.live_match_page as page

service_path = Path(scout.__file__).resolve()
page_path = Path(page.__file__).resolve()
service_text = service_path.read_text(encoding="utf-8")

print("Python:", __import__("sys").executable)
print("Service loaded from:", service_path)
print("UI loaded from:", page_path)
print("Service build:", getattr(scout, "LIVE_MATCH_PATCH_BUILD", "MISSING"))
print("UI build:", getattr(page, "LIVE_MATCH_UI_BUILD", "MISSING"))
print("Uses League-v4 PUUID lookup:", "/entries/by-puuid/" in service_text)
print("Old missing-ID error still present:", "Riot summoner response did not include an ID" in service_text)
print("Match sample size:", scout.LiveMatchScout.MATCH_SAMPLE_SIZE)

assert getattr(scout, "LIVE_MATCH_PATCH_BUILD", "") == "V5-CUMULATIVE-SOURCE"
assert getattr(page, "LIVE_MATCH_UI_BUILD", "") == "V5-CUMULATIVE-SOURCE"
assert "/entries/by-puuid/" in service_text
assert "Riot summoner response did not include an ID" not in service_text
assert scout.LiveMatchScout.MATCH_SAMPLE_SIZE == 20
print("\nPASS: PyCharm/source should load the new cumulative Live Match files.")
'@

& $python -c $check
if ($LASTEXITCODE -ne 0) {
    throw "Verification failed. Your run configuration may point to another project folder."
}

Write-Host "\nClose every running League Highlights process, then run main.py again." -ForegroundColor Green
Read-Host "Press Enter to close"
