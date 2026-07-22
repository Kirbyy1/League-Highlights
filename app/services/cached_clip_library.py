from __future__ import annotations

import threading
from pathlib import Path

from app.services.clip_library import ClipLibrary


class CachedClipLibrary(ClipLibrary):
    """Avoid reparsing unchanged JSON and probing unchanged media."""

    def __init__(self, clip_dir: Path, ffmpeg) -> None:
        super().__init__(clip_dir, ffmpeg)
        self._cache_lock = threading.RLock()
        self._scan_signature: tuple | None = None
        self._scan_cache = []

    def scan(self, *, include_match_reels: bool = False):
        signature = self._directory_signature()
        cache_key = (include_match_reels, signature)

        with self._cache_lock:
            if self._scan_signature == cache_key:
                return list(self._scan_cache)

        clips = super().scan(include_match_reels=include_match_reels)
        with self._cache_lock:
            self._scan_signature = cache_key
            self._scan_cache = list(clips)
        return clips

    def invalidate(self) -> None:
        with self._cache_lock:
            self._scan_signature = None
            self._scan_cache = []

    def finalize_match(self, context, result: str = "") -> None:
        super().finalize_match(context, result)
        self.invalidate()

    def set_rating(self, clip, rating: str) -> None:
        super().set_rating(clip, rating)
        self.invalidate()

    def delete(self, clip) -> None:
        super().delete(clip)
        self.invalidate()

    def _directory_signature(self) -> tuple:
        self.clip_dir.mkdir(parents=True, exist_ok=True)
        entries: list[tuple] = []
        for video in self.clip_dir.glob("*.mp4"):
            try:
                video_stat = video.stat()
            except OSError:
                continue

            metadata = video.with_suffix(".json")
            thumbnail = video.with_suffix(".jpg")
            try:
                metadata_mtime = metadata.stat().st_mtime_ns
            except OSError:
                metadata_mtime = 0
            try:
                thumbnail_mtime = thumbnail.stat().st_mtime_ns
            except OSError:
                thumbnail_mtime = 0

            entries.append(
                (
                    video.name,
                    video_stat.st_mtime_ns,
                    video_stat.st_size,
                    metadata_mtime,
                    thumbnail_mtime,
                )
            )
        return tuple(sorted(entries))
