# Patch v13 — Objective Steals Only

- Routine Dragon secures no longer create automatic clips.
- Routine Baron secures no longer create automatic clips.
- Dragon and Baron objective clips are created only when Riot reports `Stolen: true`.
- Settings labels now read **Dragon steals** and **Baron steals**.
- Elder steals are labelled **ELDER DRAGON STEAL**.

The existing `auto_clip_dragon` and `auto_clip_baron` setting keys are kept for compatibility; their meaning is now “clip steals of this objective.”
