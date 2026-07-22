$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Launcher = $null
$LauncherArgs = @()

& py -3.12 -c "import sys; assert sys.maxsize > 2**32" *> $null
if ($LASTEXITCODE -eq 0) {
    $Launcher = "py"
    $LauncherArgs = @("-3.12")
} else {
    & python -c "import sys; assert (3,11) <= sys.version_info[:2] < (3,15); assert sys.maxsize > 2**32" *> $null
    if ($LASTEXITCODE -eq 0) {
        $Launcher = "python"
    }
}

if (-not $Launcher) {
    throw "Python 3.12 x64 is recommended. Install it, then run setup.ps1 again."
}

Write-Host "Creating Python environment..." -ForegroundColor Cyan
& $Launcher @LauncherArgs -m venv .venv

$VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip wheel
& $VenvPython -m pip install -r requirements.txt

& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "scripts\download_ffmpeg.ps1")

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Run diagnose.bat once, then run.bat." -ForegroundColor Green
