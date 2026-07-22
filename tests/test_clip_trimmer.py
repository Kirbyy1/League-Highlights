from pathlib import Path

from app.services.clip_trimmer import ClipTrimmer


def test_trimmed_copy_names_do_not_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "play.mp4"
    source.write_bytes(b"video")
    first = ClipTrimmer._next_copy_path(source)
    assert first.name == "play_trimmed.mp4"
    first.write_bytes(b"trim")
    second = ClipTrimmer._next_copy_path(source)
    assert second.name == "play_trimmed_2.mp4"
