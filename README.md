# League Highlights (Rust)

A Rust rewrite of [League Highlights](https://github.com/Kirbyy1/League-Highlights):
a privacy-first League of Legends highlight recorder for Windows that keeps a
rolling buffer while a match runs, saves clips on a global shortcut, and lets
Smart Highlights keep kills, multikills, outnumbered plays, support impact,
low-health survivals and objective steals automatically.

It is one ~1.8 MB executable plus `ffmpeg.exe`. There is no Python runtime, no
Qt, and no ffprobe.

## Footprint

Measured on the release build:

| | Rust version |
|---|---|
| Executable | 1.8 MB (no runtime to ship) |
| Idle, no game running | 0 ms CPU over 30 s, 2.5 MB private memory, 11 threads |
| Polling a live match (recording off) | 0.37 % of one core, 5.5 MB private memory |
| Saving a clip | 1 FFmpeg process, ~1.7 s for a 10 s clip |

Recording adds the FFmpeg capture process, which runs at below-normal
priority. With Desktop Duplication + NVENC, frames never leave the GPU.

## What changed compared to the Python version

- **Tray app plus on-demand UI.** The recorder runs from the notification
  area. The library, player, trim and settings UI is a small embedded web page
  served on `127.0.0.1`. It starts only when you open it, so no UI toolkit
  sits in memory.
- **Event-driven shortcut.** A low-level keyboard hook replaces 40 Hz
  `GetAsyncKeyState` polling. The key still reaches the game.
- **No network while League is closed.** The Live Client API is polled only
  while the game window exists, over one keep-alive TLS connection.
- **One FFmpeg run per clip.** Segment boundaries come from FFmpeg's own
  `-segment_list` (no ffprobe per segment). Concat, audio mix and thumbnail
  happen in a single process (the Python version needed about 25). Clips are
  written as `.part` and renamed when complete.
- **Native WASAPI audio.** Loopback and microphone capture ask Windows for
  48 kHz stereo PCM directly into a fixed ring buffer. Packets are placed by
  their QPC timestamp, so silent stretches in loopback no longer shift later
  audio. Default-device changes reopen the stream.
- **One clock for everything.** Video progress, audio packets and Riot events
  share a QPC-based clock, so Windows clock adjustments cannot desync clips.
- **Exact ddagrab output index.** Matched by monitor handle via DXGI instead
  of guessing from `DISPLAYn`.
- **Robustness.** FFmpeg runs in a kill-on-close job object and cannot outlive
  the app. Capture restarts when the game window changes size or monitor.
  Settings load per field, so one bad value no longer resets everything.
  Recorder crashes are retried 3 times within 2 minutes. A low-disk guard
  stops recording before the drive fills.

Compatible with the existing data: same `settings.json` (unknown keys are
preserved), same clip folder, same clip `.json` metadata, and the same
"Launch with Windows" registry value.

### Not ported (yet)

Live Match scouting and champion-select recommendations (Riot API key),
Discord size-targeted export and webhook sending, and the auto-updater.

## Build

```powershell
cargo build --release --locked
.\scripts\download_ffmpeg.ps1   # puts ffmpeg.exe next to target\release\LeagueHighlights.exe
```

FFmpeg is found next to the executable, in `ffmpeg\` or `tools\ffmpeg\bin\`
beside it, or on `PATH`.

## Run

- `LeagueHighlights.exe` starts in the tray and opens the library (unless
  "Start minimized" is on).
- `LeagueHighlights.exe --startup` starts quietly (used by Launch with Windows).
- `LeagueHighlights.exe --selftest` records the primary monitor for 8 seconds
  and saves one clip, to check capture, encoder and audio without a game.
  `--selftest --synthetic` does the same with a CPU test pattern instead of
  screen capture.
- Set `LH_DATA_DIR` to use a portable or sandboxed data folder.

League must run in Borderless or Windowed mode.

## Files

| What | Where |
|---|---|
| Clips | `%USERPROFILE%\Videos\League Highlights` (configurable) |
| Settings | `%LOCALAPPDATA%\LeagueHighlights\settings.json` |
| Log | `%LOCALAPPDATA%\LeagueHighlights\logs\league_highlights.log` |
| Rolling buffer | `%LOCALAPPDATA%\LeagueHighlights\buffer` |

## Layout

```
src/main.rs        startup, threads, single instance
src/controller.rs  game-window state machine, recovery, export worker
src/recorder.rs    FFmpeg rolling segment capture and timing
src/audio.rs       WASAPI loopback / microphone ring buffers
src/live.rs        Live Client polling, Smart Highlights V2 grouping
src/scoring.rs     explainable scoring, ratings feedback, Smart Trim
src/lcu.rs         queue filter via the League Client lockfile
src/exporter.rs    segments to clip (single FFmpeg run)
src/library.rs     clip metadata, grouping, trim, rating, delete
src/web.rs         localhost UI server (token, host and CSRF checks)
src/tray.rs        notification-area icon and menu
src/hotkey.rs      low-level keyboard hook
ui/index.html      library, player, trim and settings page
```

## Riot Games notice

League Highlights is an independent project and is not endorsed by Riot Games.
League of Legends and Riot Games are trademarks or registered trademarks of
Riot Games, Inc.
