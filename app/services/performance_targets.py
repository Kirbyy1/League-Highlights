from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PerformanceTargets:
    """Internal engineering targets, not hard promises for every computer."""

    idle_cpu_percent: float = 1.0
    ram_growth_warning_mib: int = 256
    minimum_free_disk_mib: int = 750
    ui_active_interval_ms: int = 1000
    ui_background_interval_ms: int = 5000
    disconnected_live_poll_seconds: float = 2.5
    max_concurrent_ffmpeg_jobs: int = 1
    rolling_buffer_safety_seconds: int = 12
    filmstrip_cache_entries: int = 120


TARGETS = PerformanceTargets()
