# v24 — System tray and automatic League startup

## Added

- Persistent Windows system tray icon.
- Closing the main window can keep the recorder running in the tray.
- Tray menu actions: open app, save rolling buffer, start/stop capture, open clips folder, and exit.
- Tray status and save action update with recorder state.
- Per-user **Launch with Windows** support through the Windows Run registry key.
- Start minimized in the tray.
- Automatic capture starts when the League game window appears and stops when it disappears.
- One-time tray notice so users know the app is still running after closing the window.

## Settings

New options are available under **Settings → Storage & app → Background behavior**.

Automatic launch uses the current user account only and does not require administrator rights.
