# UI + configurable hotkey update (v5)

## What changed

- Replaced broken title-bar text symbols with crisp vector-drawn minimize, maximize/restore, and close icons.
- The whole empty title-bar area is draggable; labels no longer swallow mouse events.
- Reworked the interface into a quieter, more consistent desktop layout with fewer decorative symbols and more restrained spacing.
- Highlight cards now show the actual MP4 file size.
- The storage card now shows total clip count and total disk usage.
- Clip-saved notifications now include duration and file size.
- Settings now include a configurable global clip shortcut.
- The shortcut can be a single key or a modifier combination such as `Ctrl+F8`, `Alt+K`, or `Shift+F10`.
- Shortcut changes take effect immediately and persist in `%LOCALAPPDATA%\LeagueHighlights\settings.json`.
- Added a **Reset to F8** button.

## Upgrade

Replace the whole project with this folder, or copy these changed files into v4:

- `app/config.py`
- `app/controller.py`
- `app/models.py`
- `app/services/clip_exporter.py`
- `app/services/clip_library.py`
- `app/services/hotkey.py`
- `app/ui/main_window.py`
- `app/ui/styles.py`

Your existing downloaded FFmpeg folder can be kept.
