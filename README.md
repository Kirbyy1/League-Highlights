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

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
.\run.bat
```

`setup.ps1` creates a local virtual environment, installs dependencies, and downloads FFmpeg and FFprobe into `tools/ffmpeg/bin`.

Saved clips are placed in:

```text
%USERPROFILE%\Videos\League Highlights
```

Logs are written to:

```text
%LOCALAPPDATA%\LeagueHighlights\logs\league_highlights.log
```

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

```powershell
.\build_exe.ps1
```

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
