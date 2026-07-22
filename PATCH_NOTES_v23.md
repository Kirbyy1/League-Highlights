# League Highlights v23 — Accurate Two-Second Segments

- Changed the rolling replay buffer from 5-second segments to 2-second segments.
- Existing settings files are migrated automatically, so previous installations no longer keep the old 5-second value.
- Keyframes are forced at every two-second boundary to keep segment joins dependable.
- Manual and Smart Highlight exports can start and end much closer to the requested event window.
- Recording resolution, frame rate, bitrate/quality, audio settings, and NVENC selection are unchanged.
- A 45-second buffer now keeps roughly 23 active segments instead of roughly 9; cleanup limits already scale from the configured segment duration.
