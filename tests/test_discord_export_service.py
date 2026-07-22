from pathlib import Path

import pytest

from app.services.discord_export_service import (
    DISCORD_TARGET_BYTES,
    ClipTooLongForDiscord,
    DiscordExportService,
)


class DummyFfmpeg:
    pass


def service() -> DiscordExportService:
    return DiscordExportService(DummyFfmpeg())


def test_exact_available_bitrate_formula() -> None:
    bitrate = service().available_video_bitrate_bps(21.4, DISCORD_TARGET_BYTES)
    expected = int((DISCORD_TARGET_BYTES * 8 * 0.96 / 21.4) - 96_000)
    assert bitrate == expected


def test_quality_ladder_selects_highest_profile_that_fits() -> None:
    exporter = service()
    assert exporter.plan(10).profile.label == "1080p60"
    assert exporter.plan(20).profile.label == "720p60"
    assert exporter.plan(30).profile.label == "720p30"
    with pytest.raises(ClipTooLongForDiscord):
        exporter.plan(60)


def test_discord_filename_never_overwrites_existing_copy(tmp_path: Path) -> None:
    source = tmp_path / "Sylas_MANUAL_CLIP.mp4"
    source.write_bytes(b"source")
    first = service().output_path_for(source)
    assert first.name == "Sylas_MANUAL_CLIP_discord.mp4"
    first.write_bytes(b"copy")
    second = service().output_path_for(source)
    assert second.name == "Sylas_MANUAL_CLIP_discord_2.mp4"


def test_retry_bitrate_uses_actual_overshoot() -> None:
    adjusted = service().retry_bitrate(3_500_000, 9_000_000, 10_000_000)
    assert adjusted == int(3_500_000 * 9_000_000 / 10_000_000 * 0.97)
