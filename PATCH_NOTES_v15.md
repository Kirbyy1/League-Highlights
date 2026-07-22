# v15 — UI/UX overhaul

This release changes presentation and workflow without replacing the working recorder.

## Highlights library

- Keeps the game-first library: each card represents one League match.
- Cleaner game cards with one thumbnail, result, highlight count, duration, and total size.
- Opening a game shows its individual highlight clips in chronological order.
- Cleaner clip cards with reasons, smart score, audio source, size, rating, and actions.

## Recorder status

- Compact status card with clear Waiting, Starting, Recording, Saving, Stopped, and Error states.
- Visible recording profile and rolling-buffer length.
- Dedicated **Save last N seconds** button.
- Indeterminate progress bar while capture starts or a clip is being saved.

## Settings

Settings are split into four focused pages:

1. Recording
2. Audio
3. Smart highlights
4. Storage & app

The long wall of controls has been removed.

## Smart Highlights UX

- One master **Enable automatic Smart Highlights** switch.
- One sensitivity selector: Strict, Balanced, or Save more.
- Individual event checkboxes are no longer exposed.
- Turning Smart Highlights off now truly disables automatic clips; the manual shortcut remains active.
- Routine Dragon and Baron secures remain ignored; only steals are objective candidates.

## Visual polish

- One consistent green accent.
- Removed unrelated purple/neon styling.
- More consistent spacing, borders, typography, buttons, and input controls.
- Cleaner title bar icons and full-width drag area.
- No account/profile or fake dashboard statistics.

## Installation

Copy the patch's `app` folder over the existing project's `app` folder and choose **Replace files in the destination**.

The patch does not replace FFmpeg, the virtual environment, saved clips, or user settings.
