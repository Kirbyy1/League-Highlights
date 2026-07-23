$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host ""
Write-Host "League Highlights - Live Match source verification" -ForegroundColor Cyan
Write-Host "Project root: $ProjectRoot"
Write-Host ""

Get-ChildItem -Path $ProjectRoot -Directory -Filter "__pycache__" -Recurse -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $PythonExe = $VenvPython
} else {
    $PythonExe = "python"
}

$VerifierPath = Join-Path $ProjectRoot "_verify_live_match_source.py"

$PythonCode = @'
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path


def module_path(name: str) -> Path:
    spec = importlib.util.find_spec(name)
    if spec is None or not spec.origin:
        raise RuntimeError(f"Could not locate module: {name}")
    return Path(spec.origin).resolve()


service_path = module_path("app.services.live_match_scout")
ui_path = module_path("app.ui.live_match_page")

service_source = service_path.read_text(encoding="utf-8", errors="replace")
ui_source = ui_path.read_text(encoding="utf-8", errors="replace")

uses_puuid = "entries/by-puuid" in service_source
old_error_present = "Riot summoner response did not include an ID" in service_source
vertical_ui = "PlayerScoutCard" in ui_source and "TeamSection" in ui_source

sample_match = re.search(r"MATCH_SAMPLE_SIZE\s*=\s*(\d+)", service_source)
sample_size = sample_match.group(1) if sample_match else "not found"

print(f"Python executable: {sys.executable}")
print(f"Service imported from: {service_path}")
print(f"UI imported from:      {ui_path}")
print(f"Uses League-v4 PUUID lookup: {uses_puuid}")
print(f"Old missing-ID error present: {old_error_present}")
print(f"Vertical Live Match UI detected: {vertical_ui}")
print(f"Match sample size: {sample_size}")
print()

if uses_puuid and not old_error_present and vertical_ui:
    print("PASS: Python is loading the updated Live Match files.")
    raise SystemExit(0)

print("FAIL: Python is still loading old or incomplete Live Match files.")
raise SystemExit(1)
'@

try {
    Set-Content -Path $VerifierPath -Value $PythonCode -Encoding UTF8
    & $PythonExe $VerifierPath
    $ExitCode = $LASTEXITCODE
}
finally {
    Remove-Item $VerifierPath -Force -ErrorAction SilentlyContinue
}

Write-Host ""
if ($ExitCode -eq 0) {
    Write-Host "Verification passed." -ForegroundColor Green
} else {
    Write-Host "Verification failed. Check the imported paths and file checks above." -ForegroundColor Red
}

exit $ExitCode
