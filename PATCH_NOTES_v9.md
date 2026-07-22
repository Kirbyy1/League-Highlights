# v9 — Higher-quality Discord exports

- Uses a **9.7 decimal MB** default target, safely below Discord's 10,000,000-byte boundary.
- Uses **two-pass libx264 with the slow preset** and removes the restrictive constant max-rate cap.
- Reduces Discord audio to **AAC 56 kbps**, leaving more of the budget for video.
- Adds **precise event framing** from Riot event timing:
  - Single/Double/Triple: 7 seconds before the first kill through 7 seconds after the last.
  - Quadra/Penta: 8 seconds before through 8 seconds after.
  - Dragon/Baron: 8 seconds before through 6 seconds after.
- Adds **smart output resolution**:
  - Up to 15 seconds: up to 1080p30.
  - 16–25 seconds: up to 720p30.
  - Longer clips: up to 540p30.
  - Never upscales a lower-resolution rolling recording.
- Uses one final high-quality encode for Discord mode rather than trimming through an unnecessary extra lossy encode.
- Stores the chosen Discord profile and precise trim information in each clip's JSON metadata.
- Keeps the manual hotkey, automatic event toggles, audio capture, and working Desktop Duplication recorder unchanged.
- Runs the heavy two-pass Discord encoder at **below-normal Windows process priority** to reduce in-game FPS impact while compression is active.
