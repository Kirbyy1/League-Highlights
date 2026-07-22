from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.performance_targets import TARGETS
from app.services.reliable_clip_exporter import ReliableClipExporter
from app.services.reliable_ffmpeg import ReliableFfmpegTools


def main() -> None:
    assert TARGETS.idle_cpu_percent == 1.0
    assert TARGETS.max_concurrent_ffmpeg_jobs == 1
    assert TARGETS.ui_background_interval_ms > TARGETS.ui_active_interval_ms
    assert TARGETS.rolling_buffer_safety_seconds < 30
    assert hasattr(ReliableFfmpegTools, "mark_encoder_unhealthy")
    assert hasattr(ReliableClipExporter, "_validate_clip")
    print("Performance and reliability static tests passed.")


if __name__ == "__main__":
    main()
