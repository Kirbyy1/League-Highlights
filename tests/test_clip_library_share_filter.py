import json
from pathlib import Path

from app.services.clip_library import ClipLibrary


class _Probe:
    def probe_duration(self, _path: Path) -> float:
        return 10.0


def test_share_and_discord_exports_are_hidden_from_library(tmp_path: Path) -> None:
    original = tmp_path / "clip.mp4"
    share = tmp_path / "clip_share.mp4"
    discord = tmp_path / "clip_discord.mp4"
    for path in (original, share, discord):
        path.write_bytes(b"x")
    share.with_suffix(".json").write_text(json.dumps({"is_share_copy": True}))
    discord.with_suffix(".json").write_text(json.dumps({"is_discord_copy": True}))

    clips = ClipLibrary(tmp_path, _Probe()).scan()
    assert [clip.path.name for clip in clips] == ["clip.mp4"]
