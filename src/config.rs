//! Settings, stored in the same `settings.json` the Python version used.
//!
//! Unknown keys (Live Match API keys, Discord fields, ...) are preserved so the
//! file can be shared with or migrated from the original application.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock};

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

pub type SharedSettings = Arc<RwLock<Settings>>;

pub const SEGMENT_SECONDS: u32 = 2;
pub const MIN_FREE_DISK_MIB: u64 = 750;
pub const BUFFER_SAFETY_SECONDS: u32 = 12;

#[derive(Clone, Debug)]
pub struct Paths {
    pub root: PathBuf,
    pub buffer: PathBuf,
    pub logs: PathBuf,
    pub settings: PathBuf,
}

impl Paths {
    pub fn new() -> Self {
        // LH_DATA_DIR gives a portable / sandboxed data folder (also used for testing).
        let root = match std::env::var_os("LH_DATA_DIR") {
            Some(dir) => PathBuf::from(dir),
            None => std::env::var_os("LOCALAPPDATA")
                .map(PathBuf::from)
                .unwrap_or_else(home_dir)
                .join("LeagueHighlights"),
        };
        Paths {
            buffer: root.join("buffer"),
            logs: root.join("logs"),
            settings: root.join("settings.json"),
            root,
        }
    }
}

fn home_dir() -> PathBuf {
    std::env::var_os("USERPROFILE")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

pub fn default_clip_dir() -> PathBuf {
    crate::win::videos_folder()
        .unwrap_or_else(|| home_dir().join("Videos"))
        .join("League Highlights")
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub buffer_seconds: u32,
    pub segment_seconds: u32,
    pub fps: u32,
    pub width: u32,
    pub height: u32,
    pub quality: u32,
    pub audio_bitrate_kbps: u32,

    pub system_audio_enabled: bool,
    pub system_audio_device: String,
    pub system_audio_volume: u32,
    pub microphone_enabled: bool,
    pub microphone_device: String,
    pub microphone_volume: u32,

    pub recording_enabled: bool,
    pub recording_scope: String,
    pub recording_skip_custom_games: bool,
    pub recording_skip_arena: bool,

    pub auto_start: bool,
    pub launch_with_windows: bool,
    pub start_minimized: bool,
    pub draw_mouse: bool,
    pub notify_on_save: bool,
    pub champion_art: bool,
    /// Toast over the game when a clip is saved.
    pub overlay_notifications: bool,
    /// Delete clips older than this many days unless rated Good (0 = off).
    pub cleanup_days: u32,
    /// Upload limit used for Discord-ready copies, in MB.
    pub discord_target_mb: f64,
    /// Look for a newer GitHub release (notify only).
    pub check_for_updates: bool,
    /// The first-run setup check has been shown.
    pub setup_seen: bool,

    pub discord_auto_trim_events: bool,
    pub auto_clip_single_kill: bool,
    pub auto_clip_double_kill: bool,
    pub auto_clip_triple_kill: bool,
    pub auto_clip_quadra_kill: bool,
    pub auto_clip_pentakill: bool,
    pub auto_clip_dragon: bool,
    pub auto_clip_baron: bool,

    pub smart_highlights_enabled: bool,
    pub smart_sensitivity: String,

    pub hotkey_vk: u32,
    pub hotkey_modifiers: Vec<String>,
    pub hotkey_display: String,

    pub clip_dir: String,

    #[serde(flatten)]
    pub extra: Map<String, Value>,
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            buffer_seconds: 45,
            segment_seconds: SEGMENT_SECONDS,
            fps: 60,
            width: 1920,
            height: 1080,
            quality: 22,
            audio_bitrate_kbps: 160,
            system_audio_enabled: true,
            system_audio_device: String::new(),
            system_audio_volume: 100,
            microphone_enabled: false,
            microphone_device: String::new(),
            microphone_volume: 100,
            recording_enabled: true,
            recording_scope: "all".into(),
            recording_skip_custom_games: false,
            recording_skip_arena: false,
            auto_start: true,
            launch_with_windows: false,
            start_minimized: false,
            draw_mouse: false,
            notify_on_save: true,
            champion_art: true,
            overlay_notifications: true,
            cleanup_days: 0,
            discord_target_mb: 9.5,
            check_for_updates: true,
            setup_seen: false,
            discord_auto_trim_events: true,
            auto_clip_single_kill: true,
            auto_clip_double_kill: true,
            auto_clip_triple_kill: true,
            auto_clip_quadra_kill: true,
            auto_clip_pentakill: true,
            auto_clip_dragon: true,
            auto_clip_baron: true,
            smart_highlights_enabled: true,
            smart_sensitivity: "balanced".into(),
            hotkey_vk: 0x77,
            hotkey_modifiers: Vec::new(),
            hotkey_display: "F8".into(),
            clip_dir: default_clip_dir().to_string_lossy().into_owned(),
            extra: Map::new(),
        }
    }
}

pub const SCOPES: [&str; 6] = [
    "all",
    "exclude_aram",
    "ranked_only",
    "solo_duo_only",
    "flex_only",
    "normal_draft_only",
];

impl Settings {
    pub fn load(path: &Path) -> Settings {
        let Ok(text) = fs::read_to_string(path) else {
            return Settings::default();
        };
        let Ok(Value::Object(file)) = serde_json::from_str::<Value>(&text) else {
            log::warn!("settings.json is unreadable; using defaults");
            return Settings::default();
        };
        Self::from_map(file)
    }

    /// Merge a JSON object onto the defaults, keeping only values whose JSON
    /// type matches the expected field type. One malformed value can no longer
    /// reset every other setting.
    pub fn from_map(file: Map<String, Value>) -> Settings {
        let Ok(Value::Object(mut merged)) = serde_json::to_value(Settings::default()) else {
            return Settings::default();
        };
        for (key, value) in file {
            match merged.get(&key) {
                Some(expected) if same_kind(expected, &value) => {
                    merged.insert(key, value);
                }
                Some(_) => {}
                None => {
                    merged.insert(key, value);
                }
            }
        }
        let mut settings: Settings =
            serde_json::from_value(Value::Object(merged)).unwrap_or_default();
        settings.validate();
        settings
    }

    pub fn validate(&mut self) {
        let d = Settings::default();
        if ![(1280, 720), (1600, 900), (1920, 1080)].contains(&(self.width, self.height)) {
            self.width = d.width;
            self.height = d.height;
        }
        if ![30, 60].contains(&self.fps) {
            self.fps = 60;
        }
        if ![30, 45, 60, 90].contains(&self.buffer_seconds) {
            self.buffer_seconds = 45;
        }
        self.segment_seconds = SEGMENT_SECONDS;
        if !(16..=35).contains(&self.quality) {
            self.quality = 22;
        }
        if ![96, 128, 160, 192].contains(&self.audio_bitrate_kbps) {
            self.audio_bitrate_kbps = 160;
        }
        self.system_audio_volume = self.system_audio_volume.min(200);
        self.microphone_volume = self.microphone_volume.min(200);
        if !SCOPES.contains(&self.recording_scope.as_str()) {
            self.recording_scope = "all".into();
        }
        if !["strict", "balanced", "save_more"].contains(&self.smart_sensitivity.as_str()) {
            self.smart_sensitivity = "balanced".into();
        }
        if self.hotkey_vk == 0 || self.hotkey_vk > 0xFE {
            self.hotkey_vk = 0x77;
            self.hotkey_modifiers.clear();
            self.hotkey_display = "F8".into();
        }
        self.hotkey_modifiers.retain(|m| ["ctrl", "alt", "shift", "win"].contains(&m.as_str()));
        if self.hotkey_display.trim().is_empty() {
            self.hotkey_display = "F8".into();
        }
        if ![0, 14, 30, 60, 90].contains(&self.cleanup_days) {
            self.cleanup_days = 0;
        }
        if !(1.0..=500.0).contains(&self.discord_target_mb) {
            self.discord_target_mb = 9.5;
        }
        if self.clip_dir.trim().is_empty() {
            self.clip_dir = d.clip_dir;
        }
    }

    pub fn save(&self, path: &Path) -> std::io::Result<()> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let text = serde_json::to_string_pretty(self).map_err(std::io::Error::other)?;
        let temp = path.with_extension("json.tmp");
        fs::write(&temp, text)?;
        fs::rename(&temp, path)
    }

    pub fn clip_dir(&self) -> PathBuf {
        PathBuf::from(&self.clip_dir)
    }

    /// Settings that require the capture pipeline to restart when changed.
    pub fn capture_signature(&self) -> String {
        format!(
            "{}x{}@{} q{} a{} buf{} mouse{} sys{}:{} mic{}:{}",
            self.width,
            self.height,
            self.fps,
            self.quality,
            self.audio_bitrate_kbps,
            self.buffer_seconds,
            self.draw_mouse,
            self.system_audio_enabled,
            self.system_audio_device,
            self.microphone_enabled,
            self.microphone_device
        )
    }

    pub fn needs_queue_policy(&self) -> bool {
        self.recording_scope != "all" || self.recording_skip_custom_games || self.recording_skip_arena
    }

    pub fn kill_clip_enabled(&self, count: usize) -> bool {
        match count {
            1 => self.auto_clip_single_kill,
            2 => self.auto_clip_double_kill,
            3 => self.auto_clip_triple_kill,
            4 => self.auto_clip_quadra_kill,
            _ => self.auto_clip_pentakill,
        }
    }
}

fn same_kind(expected: &Value, actual: &Value) -> bool {
    match (expected, actual) {
        (Value::Bool(_), Value::Bool(_)) => true,
        (Value::String(_), Value::String(_)) => true,
        (Value::Array(_), Value::Array(_)) => true,
        (Value::Number(e), Value::Number(a)) => {
            if e.is_u64() {
                a.is_u64()
            } else {
                true
            }
        }
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bad_values_fall_back_individually() {
        let file: Map<String, Value> = serde_json::from_str(
            r#"{"fps": 30, "quality": "high", "width": 1280, "height": 720,
                "riot_api_key": "keep-me", "buffer_seconds": 61}"#,
        )
        .unwrap();
        let s = Settings::from_map(file);
        assert_eq!(s.fps, 30);
        assert_eq!(s.quality, 22);
        assert_eq!((s.width, s.height), (1280, 720));
        assert_eq!(s.buffer_seconds, 45);
        assert_eq!(s.extra.get("riot_api_key").and_then(Value::as_str), Some("keep-me"));
    }
}
