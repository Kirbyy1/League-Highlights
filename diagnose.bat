@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo The Python environment is missing. Run setup.ps1 first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" diagnose.py
pause
