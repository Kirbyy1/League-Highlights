//! Explainable highlight scoring, the ratings feedback profile and Smart Trim.

use std::collections::HashMap;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::models::StoredEvent;

#[derive(Clone, Debug, Default)]
pub struct PlayCandidate {
    pub kind: &'static str,
    pub kill_count: i64,
    pub stolen: bool,
    pub elder: bool,
    pub solo_kills: i64,
    pub outnumbered_kill_count: i64,
    pub active_level: i64,
    pub highest_victim_level: i64,
    pub min_health_percent: Option<f64>,
    pub died_after: bool,
    pub ace: bool,
    pub action_seconds: f64,
    pub assist_count: i64,
    pub assist_heavy: bool,
}

#[derive(Clone, Debug)]
pub struct ScoreResult {
    pub score: i64,
    pub reasons: Vec<String>,
    pub keep: bool,
    pub threshold: i64,
}

pub fn threshold_for(sensitivity: &str) -> i64 {
    match sensitivity {
        "strict" => 50,
        "save_more" => 12,
        _ => 30,
    }
}

fn plural(count: i64, word: &str) -> String {
    format!("{count} {word}{}", if count == 1 { "" } else { "s" })
}

pub fn score_candidate(c: &PlayCandidate, enabled: bool, sensitivity: &str, adjust: i64) -> ScoreResult {
    let threshold = (threshold_for(sensitivity) + adjust).max(0);
    let mut score = 0i64;
    let mut reasons: Vec<String> = Vec::new();

    match c.kind {
        "kill" => {
            let count = c.kill_count.clamp(1, 5);
            score += [12, 30, 55, 80, 110][(count - 1) as usize];
            reasons.push(plural(count, "champion kill"));
            if c.solo_kills > 0 {
                score += if c.solo_kills == 1 { 15 } else { (10 + c.solo_kills * 5).min(25) };
                reasons.push(plural(c.solo_kills, "solo kill"));
            }
            if c.outnumbered_kill_count >= 2 {
                score += (18 + (c.outnumbered_kill_count - 2) * 6).min(30);
                reasons.push(format!("{}v1 with no allied assists", c.outnumbered_kill_count));
            }
            if c.highest_victim_level > 0 && c.active_level > 0 && c.highest_victim_level > c.active_level {
                let gap = c.highest_victim_level - c.active_level;
                score += (8 + gap * 4).min(18);
                reasons.push(format!("beat a level {} opponent", c.highest_victim_level));
            }
            if let Some(health) = c.min_health_percent {
                if health <= 10.0 {
                    score += 25;
                    reasons.push(format!("survived at {health:.0}% health"));
                } else if health <= 25.0 {
                    score += 12;
                    reasons.push(format!("survived at {health:.0}% health"));
                }
            }
            if c.ace {
                score += 18;
                reasons.push("team ace".into());
            }
            if count >= 2 && c.action_seconds <= 4.0 {
                score += 10;
                reasons.push("rapid multikill".into());
            }
            if c.died_after {
                score -= 25;
                reasons.push("died immediately after".into());
                if count == 1 {
                    score -= 10;
                    reasons.push("one-for-one trade".into());
                }
            }
        }
        "assist" => {
            let count = c.assist_count.clamp(1, 5);
            score += [8, 24, 42, 60, 78][(count - 1) as usize];
            reasons.push(format!("{} in one fight", plural(count, "assist")));
            if c.assist_heavy {
                score += 8;
                reasons.push("assist-heavy support impact".into());
            }
            if let Some(health) = c.min_health_percent {
                if health <= 10.0 {
                    score += 28;
                    reasons.push(format!("survived at {health:.0}% health"));
                } else if health <= 25.0 {
                    score += 15;
                    reasons.push(format!("survived at {health:.0}% health"));
                }
            }
            if c.ace {
                score += 20;
                reasons.push("participated in team ace".into());
            }
            if count >= 3 && c.action_seconds <= 12.0 {
                score += 10;
                reasons.push("rapid teamfight participation".into());
            }
            if c.died_after {
                score -= 8;
                reasons.push("died after the engage".into());
                if c.ace || count >= 4 {
                    score += 12;
                    reasons.push("team converted the engage".into());
                }
            }
        }
        "dragon" => {
            score += if c.elder { 50 } else { 30 };
            reasons.push(if c.elder { "Elder Dragon" } else { "team Dragon" }.into());
            if c.stolen {
                score += 35;
                reasons.push("objective steal".into());
            }
        }
        "baron" => {
            score += 35;
            reasons.push("team Baron".into());
            if c.stolen {
                score += 35;
                reasons.push("objective steal".into());
            }
        }
        _ => {
            score += 15;
            reasons.push("game event".into());
        }
    }

    let force_keep = c.kill_count >= 3
        || c.outnumbered_kill_count >= 2
        || c.stolen
        || c.elder
        || (c.kind == "assist" && c.assist_count >= 3)
        || (c.kind == "assist" && c.ace && c.assist_count >= 2)
        || (c.kind == "assist"
            && c.assist_count >= 1
            && c.min_health_percent.is_some_and(|h| h <= 10.0));
    let keep = !enabled || force_keep || score >= threshold;
    ScoreResult {
        score: score.max(0),
        reasons,
        keep,
        threshold,
    }
}

/// Local preference model from Good / Not impressive ratings.
pub struct FeedbackProfile {
    clip_dir: PathBuf,
    next_refresh: Option<Instant>,
    adjustments: HashMap<String, i64>,
}

impl FeedbackProfile {
    pub fn new(clip_dir: PathBuf) -> FeedbackProfile {
        FeedbackProfile {
            clip_dir,
            next_refresh: None,
            adjustments: HashMap::new(),
        }
    }

    pub fn set_dir(&mut self, dir: PathBuf) {
        if dir != self.clip_dir {
            self.clip_dir = dir;
            self.next_refresh = None;
        }
    }

    pub fn adjustment(&mut self, kind: &str, label: &str) -> i64 {
        if self.next_refresh.is_none_or(|t| Instant::now() >= t) {
            self.refresh();
        }
        self.adjustments
            .get(&key(kind))
            .or_else(|| self.adjustments.get(&key(label)))
            .copied()
            .unwrap_or(0)
    }

    fn refresh(&mut self) {
        let mut votes: HashMap<String, Vec<i64>> = HashMap::new();
        if let Ok(entries) = std::fs::read_dir(&self.clip_dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.extension().and_then(|e| e.to_str()) != Some("json") {
                    continue;
                }
                let Ok(text) = std::fs::read_to_string(&path) else { continue };
                let Ok(data) = serde_json::from_str::<Value>(&text) else { continue };
                if data.get("is_match_reel").and_then(Value::as_bool).unwrap_or(false) {
                    continue;
                }
                let vote = match data.get("rating").and_then(Value::as_str) {
                    Some("good") => -2,
                    Some("bad") => 3,
                    _ => continue,
                };
                for field in ["event_kind", "label"] {
                    let k = key(data.get(field).and_then(Value::as_str).unwrap_or(""));
                    if !k.is_empty() {
                        votes.entry(k).or_default().push(vote);
                    }
                }
            }
        }
        self.adjustments = votes
            .into_iter()
            .filter(|(_, v)| v.len() >= 2)
            .map(|(k, v)| (k, v.iter().sum::<i64>().clamp(-10, 15)))
            .collect();
        self.next_refresh = Some(Instant::now() + Duration::from_secs(15));
    }
}

fn key(value: &str) -> String {
    value
        .replace('_', " ")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase()
}

// ---------------------------------------------------------------------------
// Smart Trim
// ---------------------------------------------------------------------------

const PRE_EVENT: f64 = 10.0;
const POST_EVENT: f64 = 7.0;
const GROUP_GAP: f64 = 12.0;
const MIN_TRIM: f64 = 8.0;
const MANUAL_PRE_TRIGGER: f64 = 15.0;

const RELIABLE_TYPES: [&str; 9] = [
    "CHAMPION_KILL",
    "PLAYER_DEATH",
    "MULTIKILL",
    "ACE",
    "DRAGON",
    "DRAGON_STEAL",
    "BARON",
    "BARON_STEAL",
    "MANUAL_TRIGGER",
];

#[derive(Clone, Debug)]
pub struct TrimSuggestion {
    pub start: f64,
    pub end: f64,
    pub reason: &'static str,
}

pub fn suggest_trim(duration: f64, events: &[StoredEvent], trigger: Option<f64>, manual: bool) -> TrimSuggestion {
    let duration = duration.max(0.25);
    let trigger = trigger.unwrap_or(duration).clamp(0.0, duration);
    if manual {
        let (start, end) = ensure_minimum((trigger - MANUAL_PRE_TRIGGER).max(0.0), duration, duration, trigger);
        return TrimSuggestion { start, end, reason: "Manual trigger fallback" };
    }
    let mut reliable: Vec<&StoredEvent> = events
        .iter()
        .filter(|e| RELIABLE_TYPES.contains(&e.event_type.to_uppercase().as_str()))
        .filter(|e| (0.0..=duration).contains(&e.relative_time))
        .collect();
    reliable.sort_by(|a, b| a.relative_time.total_cmp(&b.relative_time));

    let mut groups: Vec<(f64, f64)> = Vec::new();
    for event in reliable {
        match groups.last_mut() {
            Some(group) if event.relative_time - group.1 <= GROUP_GAP => group.1 = event.relative_time,
            _ => groups.push((event.relative_time, event.relative_time)),
        }
    }
    let distance = |g: &(f64, f64)| {
        if g.0 <= trigger && trigger <= g.1 {
            0.0
        } else {
            (trigger - g.0).abs().min((trigger - g.1).abs())
        }
    };
    if let Some(group) = groups.iter().min_by(|a, b| distance(a).total_cmp(&distance(b))) {
        let start = (group.0 - PRE_EVENT).clamp(0.0, duration);
        let end = (group.1 + POST_EVENT).clamp(0.0, duration);
        let (start, end) = ensure_minimum(start, end, duration, trigger);
        return TrimSuggestion { start, end, reason: "Nearby League events" };
    }
    let (start, end) = ensure_minimum(
        (trigger - PRE_EVENT).max(0.0),
        (trigger + POST_EVENT).min(duration),
        duration,
        trigger,
    );
    TrimSuggestion { start, end, reason: "Safe trigger fallback" }
}

fn ensure_minimum(start: f64, end: f64, duration: f64, focus: f64) -> (f64, f64) {
    let minimum = MIN_TRIM.min(duration);
    if end - start >= minimum {
        return (start, end);
    }
    let start = (focus - minimum / 2.0).max(0.0);
    let end = (start + minimum).min(duration);
    ((end - minimum).max(0.0), end)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pentakill_is_kept_even_when_strict() {
        let c = PlayCandidate { kind: "kill", kill_count: 5, ..Default::default() };
        let r = score_candidate(&c, true, "strict", 0);
        assert!(r.keep);
        assert!(r.score >= 110);
    }

    #[test]
    fn traded_single_kill_is_skipped_on_balanced() {
        let c = PlayCandidate { kind: "kill", kill_count: 1, died_after: true, ..Default::default() };
        let r = score_candidate(&c, true, "balanced", 0);
        assert!(!r.keep);
        assert_eq!(r.score, 0);
    }

    #[test]
    fn outnumbered_double_scores_high() {
        let c = PlayCandidate {
            kind: "kill",
            kill_count: 2,
            solo_kills: 2,
            outnumbered_kill_count: 2,
            action_seconds: 3.0,
            ..Default::default()
        };
        let r = score_candidate(&c, true, "strict", 0);
        // 30 + 20 (solo) + 18 (2v1) + 10 (rapid)
        assert_eq!(r.score, 78);
        assert!(r.reasons.iter().any(|x| x.contains("2v1")));
    }

    #[test]
    fn smart_trim_uses_nearby_events() {
        let events = vec![
            StoredEvent { relative_time: 20.0, event_type: "CHAMPION_KILL".into(), game_time: None },
            StoredEvent { relative_time: 24.0, event_type: "MULTIKILL".into(), game_time: None },
        ];
        let t = suggest_trim(45.0, &events, Some(24.0), false);
        assert_eq!((t.start, t.end), (10.0, 31.0));
        let manual = suggest_trim(45.0, &[], Some(44.0), true);
        assert_eq!((manual.start, manual.end), (29.0, 45.0));
    }
}
