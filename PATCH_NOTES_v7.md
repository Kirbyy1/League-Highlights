# v7 — Smaller clips and capture profiles

## Added

- Recording resolution selector:
  - 1920×1080
  - 1600×900
  - 1280×720
- Frame-rate selector:
  - 60 FPS
  - 30 FPS
- File-size / quality presets:
  - High quality
  - Balanced
  - Smaller files
  - Minimum size
- The selected profile is saved in `%LOCALAPPDATA%\LeagueHighlights\settings.json`.
- Applying a new profile automatically restarts an active capture and warms a new rolling buffer.
- Changing the rolling-buffer duration also restarts capture automatically.
- Audio bitrate now follows the selected file-size preset.

## Recommended profiles

- Best quality: 1080p, 60 FPS, High quality
- Recommended default: 1080p, 60 FPS, Balanced
- Smaller clips: 720p, 30 FPS, Smaller files
- Smallest clips: 720p, 30 FPS, Minimum size

Lower settings affect new buffer segments and new clips only. Existing saved videos are not recompressed.
