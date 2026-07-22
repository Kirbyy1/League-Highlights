from __future__ import annotations

import json

from app.config import AppConfig


def test_default_segment_duration_is_two_seconds(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config = AppConfig()
    assert config.segment_seconds == 2


def test_old_five_second_setting_is_migrated(tmp_path):
    config = AppConfig(
        settings_file=tmp_path / "settings.json",
        clip_dir=tmp_path / "clips",
        temp_dir=tmp_path / "buffer",
        log_dir=tmp_path / "logs",
    )
    config.settings_file.write_text(json.dumps({"segment_seconds": 5}), encoding="utf-8")
    config._load_user_settings()
    assert config.segment_seconds == 2
