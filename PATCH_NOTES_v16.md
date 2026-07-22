# v16 — Audio Stability Fix

This release fixes crackly, robotic, choppy, or unstable clip audio without changing video quality.

## Changes

- Replaced per-callback `time.time()` audio placement with a continuous, sample-accurate PCM timeline.
- Uses PortAudio's input-buffer timing information when available to preserve real capture gaps without creating callback-jitter gaps.
- Increased the PyAudio callback buffer from 1024 to 2048 frames to reduce callback overruns.
- Normalizes exported audio to 48 kHz stereo before AAC encoding.
- Uses FFmpeg asynchronous resampling only for genuine device-clock drift.
- Removed the limiter from normal single-source audio at 100% volume.
- Keeps a safety limiter only when audio is boosted above 100% or when microphone and system audio are mixed.
- Added clearer logging for device sample rate, channel count, and callback warning flags.

## Recommended settings

- System audio volume: 100%
- Microphone volume: 70–100%
- Audio quality: 192 kbps
- Select the same output device that League and Discord are using.

Old clips are not modified. The fix applies to clips recorded after restarting the application.
