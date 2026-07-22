# League Highlights performance and reliability targets

These are internal engineering targets rather than guarantees for every PC.

- Idle process CPU: below 1% after the application settles.
- Recording with a healthy hardware encoder: use the lowest practical CPU path.
- UI memory: no continuous growth during normal navigation.
- Temporary files: every cache and rolling buffer has a hard bound.
- Exporting: all heavy work stays off the Qt UI thread.
- FFmpeg jobs: one non-recording FFmpeg job at a time.
- Disk safety: stop or reject work before the drive reaches a critically low level.

## Implemented controls

- Live Client polling backs off while League is closed.
- Window polling backs off while League is closed.
- Clip metadata scans are cached until files change.
- Match cards load in batches.
- Filmstrip previews are cached and bounded.
- Inactive video players release their source and decoded frames.
- Recording segments use a configured buffer plus a small safety margin.
- Hardware encoders are tried first and temporarily quarantined after startup failure.
- Unexpected capture stops receive limited automatic recovery attempts.
- Saved clips are checked with FFprobe before being accepted.
- Stale crash leftovers are cleaned on startup.
- Hidden/minimized UI diagnostics update less frequently.
