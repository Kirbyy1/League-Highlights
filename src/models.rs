//! Shared data types and the lightweight event tracker used by Smart Trim.

use std::collections::VecDeque;
use std::sync::Mutex;

use serde::Serialize;
use serde_json::{json, Value};

#[derive(Clone, Debug, Default, Serialize)]
pub struct MatchContext {
    pub match_id: String,
    pub player_name: String,
    pub champion_name: String,
    pub game_mode: String,
    pub map_name: String,
    pub started_at: f64,
    pub kills: i64,
    pub deaths: i64,
    pub assists: i64,
    pub duration_seconds: f64,
    pub team: String,
}

#[derive(Clone, Debug)]
pub struct HighlightRequest {
    pub label: String,
    pub event_started_at: Option<f64>,
    pub event_ended_at: Option<f64>,
    pub pre_seconds: f64,
    pub post_seconds: f64,
    pub match_id: String,
    pub player_name: String,
    pub champion_name: String,
    pub game_mode: String,
    pub match_started_at: Option<f64>,
    pub event_game_time: Option<f64>,
    pub event_kind: String,
    pub automatic: bool,
    pub triggered_at_wall: Option<f64>,
    pub highlight_score: i64,
    pub score_reasons: Vec<String>,
    pub victim_names: Vec<String>,
    pub victim_champions: Vec<String>,
    pub assister_names: Vec<String>,
}

impl Default for HighlightRequest {
    fn default() -> Self {
        HighlightRequest {
            label: "MANUAL CLIP".into(),
            event_started_at: None,
            event_ended_at: None,
            pre_seconds: 0.0,
            post_seconds: 0.0,
            match_id: String::new(),
            player_name: String::new(),
            champion_name: String::new(),
            game_mode: String::new(),
            match_started_at: None,
            event_game_time: None,
            event_kind: "manual".into(),
            automatic: false,
            triggered_at_wall: None,
            highlight_score: 0,
            score_reasons: Vec::new(),
            victim_names: Vec::new(),
            victim_champions: Vec::new(),
            assister_names: Vec::new(),
        }
    }
}

impl HighlightRequest {
    pub fn clean_label(&self) -> String {
        let label = self.label.trim();
        if label.is_empty() {
            "MANUAL CLIP".into()
        } else {
            label.to_uppercase()
        }
    }

    pub fn precise_window(&self) -> Option<(f64, f64)> {
        match (self.event_started_at, self.event_ended_at) {
            (Some(start), Some(end)) if end >= start => Some((start, end)),
            _ => None,
        }
    }

    pub fn desired_window(&self) -> Option<(f64, f64)> {
        self.precise_window()
            .map(|(s, e)| (s - self.pre_seconds.max(0.0), e + self.post_seconds.max(0.0)))
    }

    pub fn with_context(mut self, context: Option<&MatchContext>) -> Self {
        if let Some(c) = context {
            if self.match_id.is_empty() {
                self.match_id = c.match_id.clone();
            }
            if self.player_name.is_empty() {
                self.player_name = c.player_name.clone();
            }
            if self.champion_name.is_empty() {
                self.champion_name = c.champion_name.clone();
            }
            if self.game_mode.is_empty() {
                self.game_mode = c.game_mode.clone();
            }
            if self.match_started_at.is_none() && c.started_at > 0.0 {
                self.match_started_at = Some(c.started_at);
            }
        }
        self
    }
}

/// One event mapped into a saved video's local timeline.
#[derive(Clone, Debug, PartialEq)]
pub struct StoredEvent {
    pub relative_time: f64,
    pub event_type: String,
    pub game_time: Option<f64>,
}

impl StoredEvent {
    pub fn to_json(&self) -> Value {
        let mut value = json!({
            "relative_time": round3(self.relative_time.max(0.0)),
            "type": self.event_type,
        });
        if let Some(game_time) = self.game_time {
            value["game_time"] = json!(round3(game_time.max(0.0)));
        }
        value
    }

    pub fn from_json(value: &Value) -> Option<StoredEvent> {
        let relative_time = value.get("relative_time")?.as_f64()?;
        let event_type = value
            .get("type")
            .or_else(|| value.get("event_type"))?
            .as_str()?
            .trim()
            .to_uppercase();
        if event_type.is_empty() {
            return None;
        }
        Some(StoredEvent {
            relative_time: relative_time.max(0.0),
            event_type,
            game_time: value.get("game_time").and_then(Value::as_f64),
        })
    }
}

pub fn round3(value: f64) -> f64 {
    (value * 1000.0).round() / 1000.0
}

#[derive(Clone, Debug)]
struct TrackedEvent {
    wall: f64,
    game_time: f64,
    event_type: String,
    match_id: String,
}

/// Keeps only tiny timestamps (never media) for Smart Trim markers.
pub struct EventTracker {
    retention: f64,
    events: Mutex<VecDeque<TrackedEvent>>,
}

impl EventTracker {
    pub fn new(retention_seconds: f64) -> EventTracker {
        EventTracker {
            retention: retention_seconds.max(60.0),
            events: Mutex::new(VecDeque::new()),
        }
    }

    pub fn record(&self, event_type: &str, game_time: f64, match_id: &str, wall: f64) {
        let mut events = self.events.lock().unwrap();
        events.push_back(TrackedEvent {
            wall,
            game_time: game_time.max(0.0),
            event_type: event_type.trim().to_uppercase(),
            match_id: match_id.to_string(),
        });
        let cutoff = wall - self.retention;
        while events.front().is_some_and(|e| e.wall < cutoff) || events.len() > 2048 {
            events.pop_front();
        }
    }

    pub fn events_for_clip(&self, start: f64, end: f64, match_id: &str) -> Vec<StoredEvent> {
        if end <= start {
            return Vec::new();
        }
        let events = self.events.lock().unwrap();
        let mut out: Vec<StoredEvent> = events
            .iter()
            .filter(|e| match_id.is_empty() || e.match_id.is_empty() || e.match_id == match_id)
            .filter(|e| e.wall >= start - 0.15 && e.wall <= end + 0.15)
            .map(|e| StoredEvent {
                relative_time: (e.wall - start).clamp(0.0, end - start),
                event_type: e.event_type.clone(),
                game_time: Some(e.game_time),
            })
            .collect();
        out.sort_by(|a, b| a.relative_time.total_cmp(&b.relative_time));
        out
    }
}

/// Messages handled by the controller thread.
pub enum Cmd {
    Hotkey,
    Highlight(HighlightRequest),
    MatchStarted(MatchContext),
    MatchEnded(MatchContext, String),
    ToggleRecording,
    SettingsChanged,
    ExportDone(Result<String, String>, String),
    Quit,
}
