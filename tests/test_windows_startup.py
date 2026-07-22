from pathlib import Path

from app.services import windows_startup


def test_development_startup_command_targets_main(monkeypatch):
    monkeypatch.setattr(windows_startup.sys, "frozen", False, raising=False)
    command = windows_startup.startup_command()
    assert "main.py" in command
    assert "--startup" in command
    assert command.startswith('"')


def test_frozen_startup_command_uses_executable(monkeypatch):
    monkeypatch.setattr(windows_startup.sys, "frozen", True, raising=False)
    monkeypatch.setattr(windows_startup.sys, "executable", str(Path("C:/Apps/LeagueHighlights.exe")))
    command = windows_startup.startup_command()
    assert "LeagueHighlights.exe" in command
    assert command.endswith("--startup")
