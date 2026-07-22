# MVP v2 fixes

- F8 now runs through a Qt signal on the GUI thread.
- Global F8 uses `RegisterHotKey` first and automatically falls back to a Windows low-level keyboard hook when another program has reserved F8.
- F8 auto-repeat is debounced so one press cannot create several clips.
- A non-focus-stealing popup appears immediately when a clip is requested and again when the clip is saved.
- Recorder failures no longer cause an endless auto-restart loop.
- FFmpeg encoder tests now log why NVENC/QSV/AMF were rejected.
- Unexpected FFmpeg exits now include the recent FFmpeg messages in the visible error.

## Replace an existing installation

Replace these files in your project:

- `app/controller.py`
- `app/services/hotkey.py`
- `app/services/ffmpeg_tools.py`
- `app/services/video_recorder.py`
- `app/ui/main_window.py`

Then restart the app.
