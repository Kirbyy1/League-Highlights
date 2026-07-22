from __future__ import annotations

from dataclasses import dataclass

# Fallback durations are used when Riot event timestamps are unavailable. New
# automatic highlights normally use HighlightRequest precise windows instead.
EVENT_DURATIONS: dict[str, int] = {
    "SINGLE KILL": 12,
    "DOUBLE KILL": 16,
    "TRIPLE KILL": 20,
    "QUADRA KILL": 26,
    "PENTAKILL": 32,
    "DRAGON SECURED": 14,
    "DRAGON STEAL": 14,
    "BARON SECURED": 14,
    "BARON STEAL": 14,
    "CLOUD DRAGON": 14,
    "MOUNTAIN DRAGON": 14,
    "INFERNAL DRAGON": 14,
    "OCEAN DRAGON": 14,
    "HEXTECH DRAGON": 14,
    "CHEMTECH DRAGON": 14,
    "ELDER DRAGON": 16,
}


@dataclass(slots=True, frozen=True)
class DiscordOutputProfile:
    width: int
    height: int
    fps: int
    label: str


def target_duration_seconds(label: str, buffer_seconds: int, auto_trim_events: bool) -> int:
    clean_label = str(label or "MANUAL CLIP").strip().upper()
    if not auto_trim_events or clean_label == "MANUAL CLIP":
        return int(buffer_seconds)
    return min(int(buffer_seconds), EVENT_DURATIONS.get(clean_label, int(buffer_seconds)))


def target_size_bytes(target_mb: float) -> int:
    """Return decimal MB bytes.

    Discord's boundary is expressed in decimal bytes. Using 1,000,000 here
    avoids the old MiB conversion accidentally pushing a nominal 9.7 MB clip
    above 10,000,000 bytes.
    """

    return int(float(target_mb) * 1_000_000)


def video_bitrate_kbps(duration_seconds: float, target_mb: float, audio_kbps: int) -> int:
    if duration_seconds <= 0:
        raise ValueError("Duration must be positive.")
    # Two-pass x264 can use nearly the full budget. Reserve three percent for MP4
    # headers, timestamp tables, AAC packet variance, and muxing overhead.
    total_kbps = (target_size_bytes(target_mb) * 8 / duration_seconds) / 1000
    return max(260, int(total_kbps * 0.97 - int(audio_kbps) - 16))


def smart_output_profile(
    duration_seconds: float,
    source_width: int,
    source_height: int,
) -> DiscordOutputProfile:
    """Select resolution from duration while never upscaling the source.

    Short clips receive more pixels because their bitrate budget is much larger.
    Longer clips reduce resolution so each pixel receives enough data to avoid the
    blocky look that a fixed 720p target caused.
    """

    duration = max(0.1, float(duration_seconds))
    if duration <= 15:
        wanted = (1920, 1080, "1080p30")
    elif duration <= 25:
        wanted = (1280, 720, "720p30")
    else:
        wanted = (960, 540, "540p30")

    width = min(int(source_width), wanted[0])
    height = min(int(source_height), wanted[1])

    # Preserve a 16:9 output and dimensions accepted by H.264 encoders.
    if width * 9 > height * 16:
        width = int(height * 16 / 9)
    else:
        height = int(width * 9 / 16)
    width -= width % 2
    height -= height % 2

    if height >= 1000:
        label = "1080p30"
    elif height >= 700:
        label = "720p30"
    else:
        label = "540p30"
    return DiscordOutputProfile(width=max(2, width), height=max(2, height), fps=30, label=label)
