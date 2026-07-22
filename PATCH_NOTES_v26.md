# v26 — Timeline and Trimmer UX Overhaul

## Match timeline

- Replaces the old line-and-dot display with duration-aware clip blocks.
- Blocks are placed using Riot match time and the actual saved clip window where available.
- Separate lanes prevent overlapping highlights from covering each other.
- Combat, multikill, objective-steal, and manual clips use distinct visual categories.
- High-score plays, pentakills, and steals receive a best-play accent.
- Hovering shows clip details; clicking a block opens it in the built-in player.
- Adds a readable match-time ruler and compact legend.

## Filmstrip trimmer

- Replaces the separate start/end sliders with a single editor-style filmstrip.
- Generates up to 12 real preview frames in the background with FFmpeg.
- Green draggable IN/OUT handles control the kept range.
- White draggable playhead controls preview seeking.
- Outside footage is dimmed and the selected section is outlined.
- Includes precise IN/OUT readouts and set-from-playhead controls.
- Retains lossless stream-copy trimming, Save as copy, and Replace original.
- No audio waveform is generated or displayed.
