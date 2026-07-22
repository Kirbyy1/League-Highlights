# v22 — NVENC diagnostics and dropped-frame monitoring

## Added
- Live FFmpeg progress monitoring without changing video quality.
- Clear active encoder name: NVIDIA NVENC, Intel Quick Sync, AMD AMF, or CPU x264.
- Hardware/software encoder indicator.
- Active capture backend indicator.
- Live capture FPS, processing speed, duplicated frames, dropped frames, and drop percentage.
- Capture health states: Good, Warning, and Poor.
- Compact diagnostics in the sidebar and expanded diagnostics in Settings.

FFmpeg reports these counters directly from the running capture process. The counters reset whenever recording restarts.
