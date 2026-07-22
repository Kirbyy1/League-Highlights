from __future__ import annotations

import hashlib
import logging
import shutil
import threading
import uuid
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QVideoFrame

from app.services.performance_targets import TARGETS
from app.ui.inline_player import InlineHighlightPlayer

LOGGER = logging.getLogger(__name__)


class OptimizedInlineHighlightPlayer(InlineHighlightPlayer):
    """Caches unchanged filmstrips and releases inactive decoder resources."""

    FRAME_COUNT = 12

    def _start_filmstrip_extraction(self, clip, duration_ms: int) -> None:
        self._filmstrip_token += 1
        token = self._filmstrip_token

        try:
            stat = Path(clip.path).stat()
            fingerprint = (
                f"{Path(clip.path).resolve()}|{stat.st_size}|"
                f"{stat.st_mtime_ns}|{duration_ms}"
            )
        except OSError:
            fingerprint = f"{Path(clip.path)}|{duration_ms}"

        cache_key = hashlib.sha256(
            fingerprint.encode("utf-8", errors="replace")
        ).hexdigest()[:24]
        cache_root = (
            Path(self.controller.config.clip_dir)
            / ".cache"
            / "filmstrips"
        )
        cache_dir = cache_root / cache_key

        def cached_paths() -> list[Path]:
            paths = sorted(cache_dir.glob("frame_*.jpg"))
            if len(paths) < self.FRAME_COUNT:
                return []
            try:
                if any(path.stat().st_size < 512 for path in paths):
                    return []
            except OSError:
                return []
            return paths[: self.FRAME_COUNT]

        existing = cached_paths()
        if existing:
            self.filmstripReady.emit(
                (token, [str(path) for path in existing])
            )
            return

        def work() -> None:
            build_dir = cache_root / f".building-{cache_key}-{uuid.uuid4().hex[:8]}"
            try:
                cache_root.mkdir(parents=True, exist_ok=True)
                build_dir.mkdir(parents=True, exist_ok=True)

                ffmpeg = self.controller.ffmpeg
                ffmpeg.require()
                assert ffmpeg.ffmpeg is not None

                duration_seconds = max(0.25, duration_ms / 1000.0)
                output_pattern = build_dir / "frame_%02d.jpg"
                command = [
                    str(ffmpeg.ffmpeg),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(clip.path),
                    "-vf",
                    (
                        f"fps={self.FRAME_COUNT / duration_seconds:.8f},"
                        "scale=240:136:force_original_aspect_ratio=decrease:"
                        "force_divisible_by=2,"
                        "pad=240:136:(ow-iw)/2:(oh-ih)/2:color=black"
                    ),
                    "-frames:v",
                    str(self.FRAME_COUNT),
                    "-q:v",
                    "4",
                    str(output_pattern),
                ]
                ffmpeg.run(command, timeout=90, low_priority=True)

                generated = sorted(build_dir.glob("frame_*.jpg"))
                if len(generated) < self.FRAME_COUNT:
                    raise RuntimeError("FFmpeg did not produce all preview frames.")

                if cache_dir.exists():
                    shutil.rmtree(cache_dir, ignore_errors=True)
                build_dir.replace(cache_dir)
                self._prune_filmstrip_cache(cache_root)

                paths = cached_paths()
                if not paths:
                    raise RuntimeError("The generated preview cache is incomplete.")
                self.filmstripReady.emit(
                    (token, [str(path) for path in paths])
                )
            except Exception as exc:
                LOGGER.warning(
                    "Could not create cached filmstrip for %s: %s",
                    clip.path,
                    exc,
                )
                self.filmstripFailed.emit((token, str(exc)))
            finally:
                if build_dir.exists():
                    shutil.rmtree(build_dir, ignore_errors=True)

        threading.Thread(
            target=work,
            name="CachedFilmstripExtractor",
            daemon=True,
        ).start()

    @staticmethod
    def _prune_filmstrip_cache(cache_root: Path) -> None:
        try:
            entries = sorted(
                (
                    path
                    for path in cache_root.iterdir()
                    if path.is_dir() and not path.name.startswith(".building-")
                ),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
        except OSError:
            return

        for old in entries[TARGETS.filmstrip_cache_entries :]:
            shutil.rmtree(old, ignore_errors=True)

    def stop(self) -> None:
        # stop() is called whenever a user leaves a game detail page. Fully detach
        # the source so Qt releases decoder frames and file handles immediately.
        self._filmstrip_token += 1
        self.player.stop()
        self.player.setSource(QUrl())
        self.video_widget.videoSink().setVideoFrame(QVideoFrame())
        self._exit_fullscreen()

        if self._filmstrip_temp is not None:
            self._filmstrip_temp.cleanup()
            self._filmstrip_temp = None

        self._clip = None
        self._game = None
        self.poster.setPixmap(QPixmap())
        self.trim_timeline.set_thumbnails([])
