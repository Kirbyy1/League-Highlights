<<<<<<< HEAD
# League Highlights — Python Recorder MVP

A Windows-only rolling highlight recorder designed for League of Legends.

## What this build does

- Detects the real `League of Legends.exe` game window.
- Automatically starts capture when a game appears.
- Records at a user-selectable 1080p, 900p, or 720p resolution and 30 or 60 FPS.
- Prefers FFmpeg `ddagrab` + NVIDIA NVENC for low-impact GPU capture on a 1080p League window.
- Falls back to FFmpeg `gdigrab` window capture when GPU desktop duplication is not applicable.
- Captures the default Windows playback device through WASAPI loopback.
- Keeps short 5-second video segments and about 75 seconds of compressed PCM audio in memory.
- Pressing the configurable global shortcut saves the latest 30, 45, or 60 seconds after the next segment boundary.
- Groups saved highlights by League game; opening a game shows and plays its individual clips.

## Recording size settings

Under **Settings → Recording**, choose the resolution, frame rate, and file-size/quality preset. Applying a profile while recording automatically restarts capture, so the rolling buffer needs a few seconds to warm up again.

Suggested profiles:

- **1080p / 60 FPS / Balanced** — recommended default
- **720p / 30 FPS / Smaller files** — much smaller clips and lower CPU/GPU load
- **720p / 30 FPS / Minimum size** — smallest available files

The settings apply to newly recorded segments. Existing saved clips are not modified.

## Important MVP limitation

Use League in **Borderless** mode. Exclusive fullscreen is not supported by this first test build.

## Recommended environment

- Windows 10/11 x64
- Python 3.12 x64
- JetBrains PyCharm
- Updated NVIDIA/AMD/Intel GPU drivers

## One-time setup

Open PowerShell in this folder and run:
=======
<p align="center">
  <img src="app/assets/logo.png" width="88" alt="League Highlights logo">
</p>

<h1 align="center">League Highlights</h1>

<p align="center">
  A lightweight, privacy-first League of Legends highlight recorder for Windows.
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white">
  <img alt="Platform" src="https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D4?logo=windows&logoColor=white">
  <img alt="UI" src="https://img.shields.io/badge/UI-PySide6-41CD52?logo=qt&logoColor=white">
  <img alt="Capture" src="https://img.shields.io/badge/Capture-FFmpeg-007808?logo=ffmpeg&logoColor=white">
</p>

League Highlights continuously keeps a short rolling buffer while a League match is running. Press a configurable global hotkey to save the latest action, or let Smart Highlights automatically preserve kills, objectives, outnumbered fights, support impact, low-health survivals, and other high-value moments.

The application is designed to stay local: clips remain on the computer unless the user explicitly exports or sends them.

## Features

### Lightweight recording

- Automatically detects the real League of Legends game window.
- Records 720p, 900p, or 1080p at 30 or 60 FPS.
- Uses FFmpeg Desktop Duplication (`ddagrab`) when available.
- Prefers NVIDIA NVENC and falls back safely when hardware encoding is unavailable.
- Captures Windows system audio through WASAPI loopback.
- Supports optional microphone capture with separate volume controls.
- Uses two-second rolling segments for accurate clip boundaries.
- Provides encoder, FPS, duplicated-frame, dropped-frame, and capture-backend diagnostics.

### Manual and automatic highlights

- Configurable global save hotkey; F8 by default.
- Single, double, triple, quadra, and pentakill detection.
- Outnumbered-play detection, including a strong 2v1 double-kill heuristic.
- Dragon, Baron, Elder, ace, and objective-steal detection.
- Low-health survival scoring.
- Support and teamfight-impact detection using grouped assists.
- Saves strong engages even when the support dies and the team converts the fight.
- Strict, Balanced, and Save More sensitivity modes.
- Explainable local scoring that records why a moment was kept.

### Media-first library and player

- Groups highlights by match instead of showing one flat file list.
- Clean, clickable game cards.
- Embedded player with the video as the main focus.
- Full-match event timeline and colored highlight markers.
- Trim handles, filmstrip preview, fullscreen playback, and Smart Trim suggestions.
- Keeps individual highlight files rather than merging an entire match into one video.

### Share and export

- Save a separate high-quality `_share.mp4` copy.
- Create a size-targeted `_discord.mp4` without modifying the original clip.
- Open the exported file or reveal it in File Explorer immediately.
- Optionally send through the user's own Discord webhook.
- Protects saved webhook data with Windows DPAPI for the current Windows user.

## Privacy

- No Riot API key is required.
- Match context is read from Riot's local Live Client Data API while the game is running.
- Clips, ratings, settings, and metadata are stored locally.
- The application does not upload gameplay automatically.
- Discord sharing is optional and only occurs after an explicit user action.
- Webhook URLs are not written to logs and are stored encrypted with Windows DPAPI.

## Requirements

- Windows 10 or Windows 11, 64-bit
- League of Legends in Borderless or Windowed mode
- Python 3.11 to 3.14 for development
- Updated graphics and audio drivers

Exclusive fullscreen is not currently supported reliably because the recorder captures the composed Windows desktop region.

## Quick start

Open PowerShell in the project directory:
>>>>>>> origin/main

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
<<<<<<< HEAD
```

This creates `.venv`, installs the Python packages, and downloads FFmpeg plus FFprobe into the project.

Then run:

```powershell
.\run.bat
```

## PyCharm setup

1. Open this folder in PyCharm.
2. Select interpreter: `.venv\Scripts\python.exe`.
3. Create a Python run configuration for `main.py`.
4. Run it normally; no administrator rights should be required.

## Testing

1. Start the application.
2. Open League Practice Tool in Borderless mode.
3. Wait for **RECORDING**.
4. Wait at least 50 seconds for a complete rolling buffer.
5. Press **F8**.
6. The app waits up to five seconds to close the current segment and creates an MP4 under:
=======
.\run.bat
```

`setup.ps1` creates a local virtual environment, installs dependencies, and downloads FFmpeg and FFprobe into `tools/ffmpeg/bin`.

Saved clips are placed in:
>>>>>>> origin/main

```text
%USERPROFILE%\Videos\League Highlights
```

<<<<<<< HEAD
7. Click Play from the Highlights page.

## Logs

Runtime logs are written to:
=======
Logs are written to:
>>>>>>> origin/main

```text
%LOCALAPPDATA%\LeagueHighlights\logs\league_highlights.log
```

<<<<<<< HEAD
When a clip is video-only or capture fails, this log contains the underlying FFmpeg or WASAPI error.

## Build a distributable folder

After the MVP works:
=======
## Development setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
.\scripts\download_ffmpeg.ps1
python main.py
```

For PyCharm, select `.venv\Scripts\python.exe` as the project interpreter and run `main.py`.

## Build the Windows application
>>>>>>> origin/main

```powershell
.\build_exe.ps1
```

<<<<<<< HEAD
The result is created under `dist\LeagueHighlights`. It contains the application, Python runtime, FFmpeg, and FFprobe. Users do not install Python or FFmpeg separately.

## Why the app waits after F8

The recorder creates independent five-second files. Waiting for the next boundary guarantees the latest file has been closed cleanly before it is joined into the MP4. This means the clip can include up to five seconds after the F8 press.


## Automatic highlights

While a match is active, the app reads Riot's local Live Client Data API and can automatically save Single/Double/Triple/Quadra/Pentakills plus Dragon and Baron objectives secured by your team. Every category is independently configurable under **Settings → Automatic highlights**. Manual hotkey clipping continues to work normally.

## Discord-ready export

Enable **Settings → Discord sharing → Make new clips Discord-ready** to create a high-quality H.264 MP4 below Discord's 10 MB limit. The default target is 9.7 decimal MB with AAC 56 kbps audio and two-pass `libx264` using the `slow` preset. Automatic highlights are framed around Riot event timestamps (normally 7–8 seconds before and 6–8 seconds after the action), and output resolution is selected by final duration: 1080p30 for very short clips, 720p30 for medium clips, and 540p30 for longer clips. Manual clips keep the chosen rolling-buffer duration and still receive smart resolution.


## Smart highlights and player context

Version 11 builds a local player directory from Riot's Live Client Data API, so saved clips can include the active Riot ID, champion, victim names/champions, and assisters. Exact multikill events are preferred when Riot exposes them. Lightweight health, level, death-state, score, Ace, and objective context is fed into an explainable Good Play Score. Choose **Strict**, **Balanced**, or **Save more** under **Settings → Automatic highlights**.

The scorer rewards multikills, solo kills, low-health survival, higher-level opponents, rapid kills, aces, Elder/Baron, and objective steals. It penalizes ordinary one-for-one plays and dying immediately after a weak candidate. Thumbs-up/down ratings are stored locally and gently tune future thresholds for similar events after enough feedback exists.

## Game-based highlight library

The Highlights page is organized by League game rather than as one flat clip list. Each game card shows the champion, player, mode, date, number of highlights, combined duration, and total file size. Open a game to see and play its individual highlights in chronological order. The app does **not** create a combined match video.

Clips created by older versions without a match ID appear under **Older ungrouped clips** so they are not lost.

This is event-and-state based detection rather than full video AI. It can reliably understand kills, multikills, names, assists, low-health survival, aces, deaths, Dragons, Barons, and steals. Mechanical details such as skillshot dodges, animation cancels, and visually beautiful movement are not yet judged from the video itself.

## v15 UI/UX

The interface now uses a game-first highlight library, compact recorder status, focused settings tabs, and a single Smart Highlights master control. See `PATCH_NOTES_v15.md`.

## Share / Export

The Smart Trim panel has one **Share / Export** button:

- **Save file** creates a separate high-quality H.264/AAC `_share.mp4` copy and immediately offers **Open file** and **Show in folder**.
- **Discord** creates the existing size-targeted `_discord.mp4` copy. A webhook is optional: users can save the file only, or connect a channel webhook and send it automatically after encoding.

Discord connection setup is shown only when the user chooses to send. The webhook is protected with Windows DPAPI for the current user and is never written to application logs. Exported share/Discord copies are kept out of the normal highlight library so they do not appear as duplicate clips.

The application uses the provided LH PNG and ICO assets for the title bar, dialogs, taskbar, system tray, and packaged executable.
=======
The packaged application is written to `dist\LeagueHighlights` and includes the Python runtime plus the bundled FFmpeg tools.

## Tests

```powershell
python -m pytest -q
```

The test suite covers smart scoring, support highlights, outnumbered plays, game grouping, timeline metadata, clip trimming, export behavior, webhook validation, secure webhook storage, startup behavior, and recorder diagnostics.

## How Smart Highlights works

The detector intentionally uses explainable, lightweight signals from Riot's local live data rather than running expensive video analysis.

A fight candidate can be scored using:

- kill or assist count;
- allied assistance;
- rapid event timing;
- player and opponent levels;
- minimum health reached;
- whether the player survived;
- ace and objective participation;
- whether a support engage was converted by the team.

Some plays cannot be identified perfectly from the local API alone. The application can recognize strong support participation, but it cannot prove that a particular shield, hook, heal, displacement, dodge, or animation cancel caused the result.

## Project structure

```text
app/
  services/        Recording, Riot events, scoring, trimming, export and storage
  ui/              PySide6 windows, player, timeline and dialogs
  assets/          Application logo and Windows icon
scripts/           FFmpeg setup scripts
tests/             Automated tests
main.py            Application entry point
build_exe.ps1      PyInstaller build script
setup.ps1          Development setup
```

## Current status

League Highlights is an active Windows desktop project. The core capture, Smart Highlights, support detection, player, trimming, library, diagnostics, tray integration, startup behavior, and export workflows are implemented. Visual recognition of mechanically impressive actions remains outside the current lightweight event-based design.

## Riot Games notice

League Highlights is an independent project and is not endorsed by Riot Games. League of Legends and Riot Games are trademarks or registered trademarks of Riot Games, Inc.
>>>>>>> origin/main
