$ErrorActionPreference = "Stop"

$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$oldPatches = @(
    (Join-Path $project "app\ui\trim_panel_style_patch.py"),
    (Join-Path $project "app\ui\video_controls_polish_patch.py")
)

foreach ($file in $oldPatches) {
    if (Test-Path $file) {
        Remove-Item $file -Force
        Write-Host "Removed $file"
    }
}

Write-Host "Old player styling patches removed."
