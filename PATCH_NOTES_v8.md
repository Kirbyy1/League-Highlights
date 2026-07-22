# v8 — Discord-ready clips

## New

- Optional **Discord mode** under Settings → Discord sharing.
- Targets **8.0, 8.5, or 9.0 MB** for every newly saved clip.
- Converts final clips to **1280×720, 30 FPS, H.264, AAC 64 kbps**.
- Uses background **two-pass H.264 encoding** for predictable file size.
- Retries with a lower bitrate when MP4 overhead exceeds the selected target.
- Shows a **Discord ready** badge on clips below the upload boundary.
- Saved notification now includes the final file size and sharing status.

## Automatic event lengths

When “Automatically shorten kills and objective clips” is enabled:

- Single kill: 20 seconds
- Double kill: 25 seconds
- Triple kill: 30 seconds
- Quadra kill: 40 seconds
- Pentakill: up to 45 seconds
- Dragon / Baron: 25 seconds
- Elder Dragon: 30 seconds

Manual clips continue to use the selected 30 / 45 / 60-second rolling buffer.

## Notes

- The rolling recorder keeps using the selected recording profile. Compression starts only after a clip is requested, so active gameplay capture quality is not reduced.
- Discord compression runs in a worker thread. Saving can take longer than before, especially when FFmpeg uses CPU encoding.
- Existing clips are not recompressed automatically.
