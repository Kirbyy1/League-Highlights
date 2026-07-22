# First-success test checklist

## Installation

- [ ] `setup.ps1` completes without a red error.
- [ ] `tools\ffmpeg\bin\ffmpeg.exe` exists.
- [ ] `tools\ffmpeg\bin\ffprobe.exe` exists.
- [ ] `run.bat` opens the UI.

## Detection and capture

- [ ] App initially says `WAITING FOR LEAGUE`.
- [ ] League is running in Borderless mode.
- [ ] App changes to `RECORDING` automatically.
- [ ] Status detail shows the selected encoder, ideally `h264_nvenc`.
- [ ] Recording timer increases.

## Rolling buffer

- [ ] Wait at least 50 seconds.
- [ ] `%LOCALAPPDATA%\LeagueHighlights\buffer` contains `.mkv` segment files.
- [ ] Old segment files are automatically deleted after the buffer grows.

## F8 clip

- [ ] Press the configured shortcut while League has focus.
- [ ] Status changes to `SAVING CLIP`.
- [ ] Within about 5–15 seconds, a new MP4 appears in `Videos\League Highlights`.
- [ ] The new clip appears in the UI.
- [ ] Clicking Play opens the clip.
- [ ] Video is smooth and close to 60 FPS.
- [ ] System/game audio is audible.
- [ ] Clip length is approximately 40–45 seconds after warm-up.

## Performance

Measure League FPS for the same Practice Tool scene:

- [ ] 60 seconds with recorder stopped.
- [ ] 60 seconds while recorder is running.
- [ ] Note encoder shown in the UI.
- [ ] Note average FPS difference.

Target for NVIDIA + ddagrab + NVENC: no obvious stutter and a small average FPS difference. Actual impact depends on GPU, resolution, driver, overlays, and League settings.

## If something fails

Open:

```text
%LOCALAPPDATA%\LeagueHighlights\logs\league_highlights.log
```

Copy the final 50–100 lines when reporting the issue.


## UI and shortcut

- [ ] Minimize, maximize/restore, and close icons are centered and visible.
- [ ] Dragging any blank part of the title bar moves the window.
- [ ] Every clip card displays its real file size.
- [ ] Storage shows total clip count and disk usage.
- [ ] Change the shortcut in Settings and confirm it works in League immediately.
- [ ] Restart the app and confirm the selected shortcut is remembered.

## Discord mode

- [ ] Enable Discord mode and select the 9.7 MB target.
- [ ] Save a manual clip and confirm the final card shows `Discord ready`.
- [ ] Confirm the MP4 is below 10,000,000 bytes.
- [ ] Confirm a clip up to 15 seconds exports at up to 1080p30.
- [ ] Confirm a 16–25 second clip exports at up to 720p30.
- [ ] Confirm a clip longer than 25 seconds exports at up to 540p30.
- [ ] Confirm game audio is present at AAC 56 kbps.
- [ ] Trigger a Single Kill and confirm the clip includes about 7 seconds before and after the kill.
- [ ] Trigger a Triple Kill and confirm one clip covers the first through last kill plus framing.
- [ ] Trigger Dragon or Baron and confirm approximately 8 seconds before and 6 seconds after.
- [ ] Disable precise event framing and confirm automatic clips use fallback/full-buffer behavior.
- [ ] Disable Discord mode and confirm new clips keep the normal recording profile.


## Smart highlights

- [ ] Settings shows Smart play filtering and Strict / Balanced / Save more sensitivity.
- [ ] A saved automatic clip shows the correct champion and active Riot ID when available.
- [ ] A kill clip metadata file contains victim names/champions and assisters.
- [ ] A solo kill is identified when Riot reports no assisters.
- [ ] A Triple/Quadra/Penta is labelled from Riot's Multikill event rather than duplicate single clips.
- [ ] A low-health survival adds a positive score reason.
- [ ] An ordinary single kill followed by immediate death is skipped in Balanced mode.
- [ ] Dragon/Baron secured by the enemy team is ignored.
- [ ] Dragon/Baron steals are kept and labelled as steals.
- [ ] Rating two similar clips Good or Not good changes the future threshold adjustment in metadata/logs.

## Game grouping

- [ ] Save at least two highlights during one League game.
- [ ] The Highlights page shows one game card, not two unrelated top-level clip cards.
- [ ] The game card shows the correct champion, player, highlight count, duration, and total size.
- [ ] Clicking the game opens its individual highlights in chronological order.
- [ ] Back to games returns to the game library.
- [ ] Starting a second game creates a separate game card.
- [ ] Deleting a clip updates the count and storage size for its game.
- [ ] Clips from older versions without a match ID appear under Older ungrouped clips.
- [ ] No combined MATCH_HIGHLIGHTS video is generated at GameEnd.
