# v27 — In-video controls and trim-range playback

- Removed the separate playback control bar below the video.
- Added YouTube-style controls directly over the bottom of the video.
- Controls appear on mouse movement and fade while the clip is playing.
- Clicking the video toggles play/pause.
- Added an in-video seek bar, time display, mute, volume and fullscreen controls.
- Removed the `Set IN at playhead` and `Set OUT at playhead` buttons.
- Replaced the bulky precision row with compact IN, OUT and selected-duration readouts.
- Playback and seeking are constrained to the selected trim range.
- Reaching the OUT handle pauses playback at OUT; pressing Play again starts from IN.
- Moving a handle past the playhead moves the preview back inside the selected range.
- The filmstrip remains video-only. No waveform is generated or displayed.
