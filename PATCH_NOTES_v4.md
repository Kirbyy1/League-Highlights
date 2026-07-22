# v4: DirectX black-screen fix

- Removed FFmpeg `gdigrab` capture by League HWND. DirectX game windows can appear black through old GDI window capture.
- Desktop Duplication (`ddagrab`) is now used for NVIDIA, Intel, AMD, and software encoders.
- When the selected encoder is `libx264`, D3D11 frames are explicitly downloaded with `hwdownload,format=bgra` before encoding.
- If Desktop Duplication cannot start, the fallback captures the visible desktop region instead of the DirectX HWND.
- Audio and the global F8/popup changes from v3 are preserved.

Replace only `app/services/video_recorder.py` in an existing v3 project, or use the full v4 folder.
