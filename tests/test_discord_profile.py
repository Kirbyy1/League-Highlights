from app.services.discord_profile import (
    smart_output_profile,
    target_duration_seconds,
    target_size_bytes,
    video_bitrate_kbps,
)


def test_automatic_event_fallback_durations_are_short() -> None:
    assert target_duration_seconds("SINGLE KILL", 45, True) == 12
    assert target_duration_seconds("TRIPLE KILL", 45, True) == 20
    assert target_duration_seconds("BARON SECURED", 45, True) == 14
    assert target_duration_seconds("PENTAKILL", 30, True) == 30


def test_manual_clip_keeps_the_selected_buffer() -> None:
    assert target_duration_seconds("MANUAL CLIP", 60, True) == 60
    assert target_duration_seconds("TRIPLE KILL", 60, False) == 60


def test_target_size_uses_decimal_mb_and_nearly_full_budget() -> None:
    assert target_size_bytes(9.7) == 9_700_000
    bitrate = video_bitrate_kbps(20, 9.7, 56)
    assert 3600 <= bitrate <= 3750


def test_smart_resolution_tracks_clip_length_without_upscaling() -> None:
    assert smart_output_profile(12, 1920, 1080).label == "1080p30"
    assert smart_output_profile(20, 1920, 1080).label == "720p30"
    assert smart_output_profile(32, 1920, 1080).label == "540p30"
    lower_source = smart_output_profile(12, 1280, 720)
    assert (lower_source.width, lower_source.height) == (1280, 720)
