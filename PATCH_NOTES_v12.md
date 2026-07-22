# v12 — Original Quality Export

- Removed the automatic Discord-size transcoding that downscaled and recompressed clips.
- Saved clips now copy the already-encoded rolling-buffer video without a second video encode.
- Precise smart-highlight selection remains, but segment boundaries may add a few seconds around an event to preserve quality.
- Removed Discord compression controls and Discord-ready labels from the UI.
- File-size display remains.
- Older settings files that enabled Discord mode are automatically ignored.

Recommended recording profile for best quality: 1920×1080, 60 FPS, High quality.
