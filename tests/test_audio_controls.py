from datetime import datetime
from pathlib import Path

from app.models import ClipInfo


def _clip(**kwargs) -> ClipInfo:
    values = {
        "path": Path("clip.mp4"),
        "thumbnail_path": None,
        "created_at": datetime.now(),
        "duration_seconds": 10.0,
    }
    values.update(kwargs)
    return ClipInfo(**values)


def test_audio_summary_system_and_microphone() -> None:
    clip = _clip(system_audio_included=True, microphone_included=True)
    assert clip.audio_summary_text == "System + microphone"


def test_audio_summary_microphone_only() -> None:
    clip = _clip(
        audio_included=True,
        system_audio_included=False,
        microphone_included=True,
    )
    assert clip.audio_summary_text == "Microphone"


def test_audio_summary_video_only() -> None:
    clip = _clip(
        audio_included=False,
        system_audio_included=False,
        microphone_included=False,
    )
    assert clip.audio_summary_text == "Video only"
