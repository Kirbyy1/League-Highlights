# League Highlights v39 — Share / Export + Discord onboarding + branding

## Share / Export
- Replaced the permanent “Export for Discord” action with one clean “Share / Export” button.
- The destination chooser appears only after the user presses the button.
- No X option is included.

### Save file
- Creates a separate high-quality H.264/AAC MP4 using the selected Smart Trim range.
- Uses `_share.mp4` filenames and never modifies the original highlight.
- Shows a result window with:
  - Open file
  - Show in folder
  - File name, size, and folder
- Share copies are hidden from the normal clip library so they do not appear as duplicate highlights.

### Discord
- A webhook is not required.
- Users can choose:
  - Save Discord-ready file
  - Connect webhook and send
- Existing connected users can still choose file-only export.
- Discord quality prediction is shown only inside the Discord destination dialog, not permanently on the trimmer.
- If sending fails after encoding, the valid Discord-ready MP4 is preserved and the result window makes it easy to find.

## Discord connection
- First-use setup appears only after the user explicitly chooses “Connect webhook & send”.
- Includes a masked webhook field and connection test.
- The webhook URL is encrypted with Windows DPAPI for the current Windows user.
- It is never written to logs.
- Connection management is available from the Discord destination dialog:
  - Change webhook
  - Remove webhook
- Upload uses one MP4 attachment and confirms the created Discord message.
- Handles cancellation, connection errors, invalid/deleted webhooks, and one Discord rate-limit retry.

## Branding
- Added the user-provided LH PNG and ICO assets.
- PNG logo is used in the title bar and Share/Discord dialogs.
- ICO is used for the application window, taskbar, system tray, and PyInstaller executable.
- PyInstaller build script now bundles both assets and applies the ICO.

## Validation
- Python compilation completed successfully.
- 47 automated tests passed.
- A real FFmpeg test created a valid high-quality `_share.mp4` while preserving the original.
- No new Python dependency was added.
- PySide6 visual behavior and a real Discord webhook upload still require final testing on Windows.
