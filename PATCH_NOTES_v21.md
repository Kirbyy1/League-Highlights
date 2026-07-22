# v21 — Built-in Player + Simple Trimmer

## Added

- Built-in video playback inside League Highlights.
- Play/pause, timeline seeking, ±5 second controls, volume, mute, fullscreen, and keyboard shortcuts.
- Start and end trim controls with “use playhead” buttons.
- Save a trimmed copy or replace the original after confirmation.
- FFmpeg stream-copy trimming: no second video/audio encode and no deliberate quality reduction.
- Trim work runs in the background so the interface stays responsive.
- Trimmed copies keep match, champion, score, rating, and audio metadata.
- New thumbnails and metadata are generated automatically.

## Keyboard shortcuts

- Space: play/pause
- Left/Right: seek 5 seconds
- Escape: leave fullscreen or close the player

## Note

Stream-copy trimming cuts on nearby encoded keyframes. This preserves quality and is fast, but a boundary can differ slightly from the selected millisecond. A future optional “frame accurate” mode can re-encode only when exact boundaries matter.
