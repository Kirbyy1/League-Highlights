from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app.services.clip_exporter import ClipExporter
from app.services.performance_targets import TARGETS

LOGGER = logging.getLogger(__name__)


class ReliableClipExporter(ClipExporter):
    """Adds safe preflight and post-export validation."""

    def export_request(self, request):
        self.config.clip_dir.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self.config.clip_dir).free
        minimum = TARGETS.minimum_free_disk_mib * 1024 * 1024
        if free < minimum:
            raise RuntimeError(
                "Not enough free disk space to save a highlight. "
                f"Free at least {TARGETS.minimum_free_disk_mib} MiB and try again."
            )

        clip = super().export_request(request)
        try:
            self._validate_clip(clip.path)
        except Exception:
            LOGGER.exception("Saved clip failed validation: %s", clip.path)
            for path in (
                clip.path,
                clip.path.with_suffix(".jpg"),
                clip.path.with_suffix(".json"),
            ):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    LOGGER.debug(
                        "Could not remove invalid output %s",
                        path,
                        exc_info=True,
                    )
            raise
        return clip

    def _validate_clip(self, path: Path) -> None:
        if not path.is_file() or path.stat().st_size < 4096:
            raise RuntimeError("The saved highlight file is incomplete.")
        duration = self.ffmpeg.probe_duration(path)
        if duration < 1.0:
            raise RuntimeError("The saved highlight has no usable video duration.")

    def _generate_thumbnail(
        self,
        video_path: Path,
        output_path: Path,
        duration: float,
    ) -> None:
        try:
            current = (
                output_path.is_file()
                and output_path.stat().st_size > 1024
                and output_path.stat().st_mtime_ns
                >= video_path.stat().st_mtime_ns
            )
        except OSError:
            current = False

        if current:
            LOGGER.debug("Reusing unchanged thumbnail %s", output_path)
            return
        super()._generate_thumbnail(video_path, output_path, duration)
