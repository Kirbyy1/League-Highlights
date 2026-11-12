//! What Smart Highlights learns from you.
//!
//! Every clip carries explainable score reasons ("2 solo kills", "survived
//! at 6% health", ...). Ratings and post-match review choices add a signal to
//! each reason; future plays with those reasons score a little higher or
//! lower. Stored locally in `<data>\feedback.json`; bounded so a few votes
//! can't swamp the rules.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};

pub const GOOD: i64 = 2;
pub const BAD: i64 = -3;
pub const KEPT_IN_REVIEW: i64 = 1;
pub const DELETED_IN_REVIEW: i64 = -2;

const PER_REASON_CAP: i64 = 10;
const TOTAL_CAP: i64 = 15;
const MIN_VOTES: u32 = 2;

#[derive(Clone, Copy, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct Weight {
    pub net: i64,
    pub count: u32,
}

static WRITE: Mutex<()> = Mutex::new(());

fn path(root: &Path) -> PathBuf {
    root.join("feedback.json")
}

/// "survived at 6% health" -> "survived at #% health"; numbers and plurals
/// don't matter ("1 solo kill" and "3 solo kills" are the same reason).
pub fn reason_key(reason: &str) -> String {
    let mut out = String::with_capacity(reason.len());
    let mut in_number = false;
    for c in reason.trim().to_lowercase().chars() {
        if c.is_ascii_digit() {
            if !in_number {
                out.push('#');
            }
            in_number = true;
        } else {
            in_number = false;
            out.push(c);
        }
    }
    out.split(' ')
        .map(|w| if w.len() > 3 && w.ends_with('s') && !w.ends_with("ss") { &w[..w.len() - 1] } else { w })
        .collect::<Vec<_>>()
        .join(" ")
}

fn ignored(key: &str) -> bool {
    key == "manual clip" || key.starts_with("grouped ") || key.starts_with("your ratings")
}

pub fn load(root: &Path) -> HashMap<String, Weight> {
    fs::read_to_string(path(root))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default()
}

pub fn record(root: &Path, reasons: &[String], signal: i64) {
    if reasons.is_empty() || signal == 0 {
        return;
    }
    let _guard = WRITE.lock().unwrap_or_else(|e| e.into_inner());
    let mut map = load(root);
    for reason in reasons {
        let key = reason_key(reason);
        if key.is_empty() || ignored(&key) {
            continue;
        }
        let w = map.entry(key).or_default();
        w.net = (w.net + signal).clamp(-50, 50);
        w.count = w.count.saturating_add(1);
    }
    if let Ok(text) = serde_json::to_string_pretty(&map) {
        let _ = fs::create_dir_all(root);
        let temp = path(root).with_extension("json.tmp");
        if fs::write(&temp, text).is_ok() {
            let _ = fs::rename(&temp, path(root));
        }
    }
}

pub fn reset(root: &Path) {
    let _guard = WRITE.lock().unwrap_or_else(|e| e.into_inner());
    let _ = fs::remove_file(path(root));
}

fn weight_bonus(w: &Weight) -> i64 {
    if w.count < MIN_VOTES {
        0
    } else {
        w.net.clamp(-PER_REASON_CAP, PER_REASON_CAP)
    }
}

/// Score nudge for a play with these reasons.
pub fn bonus(map: &HashMap<String, Weight>, reasons: &[String]) -> i64 {
    let total: i64 = reasons
        .iter()
        .filter_map(|r| map.get(&reason_key(r)))
        .map(weight_bonus)
        .sum();
    total.clamp(-TOTAL_CAP, TOTAL_CAP)
}

/// Readable name for a reason key in the Settings summary.
fn display_name(key: &str) -> String {
    let named = [
        ("#v# with no allied assist", "outnumbered kills"),
        ("survived at #% health", "low-health survivals"),
        ("shut down a #-kill streak", "shutdowns"),
        ("beat a level # opponent", "beating higher-level opponents"),
        ("# assist in one fight", "teamfight assists"),
        ("# champion kill", "kills"),
        ("# solo kill", "solo kills"),
    ];
    if let Some((_, name)) = named.iter().find(|(k, _)| *k == key) {
        return (*name).to_string();
    }
    key.replace("# ", "").replace('#', "").trim().to_string()
}

/// Strongest learned likes and dislikes, for the Settings summary.
pub fn summary(map: &HashMap<String, Weight>) -> (Vec<(String, i64)>, Vec<(String, i64)>) {
    let mut items: Vec<(String, i64)> = map
        .iter()
        .map(|(k, w)| (display_name(k), weight_bonus(w)))
        .filter(|(_, b)| *b != 0)
        .collect();
    items.sort_by_key(|(_, b)| -b.abs());
    let likes = items.iter().filter(|(_, b)| *b > 0).take(4).cloned().collect();
    let dislikes = items.iter().filter(|(_, b)| *b < 0).take(4).cloned().collect();
    (likes, dislikes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keys_ignore_numbers() {
        assert_eq!(reason_key("survived at 6% health"), "survived at #% health");
        assert_eq!(reason_key("2 solo kills"), "# solo kill");
        assert_eq!(reason_key("1 solo kill"), "# solo kill");
        assert_eq!(reason_key("shut down a 12-kill streak"), "shut down a #-kill streak");
        assert_eq!(reason_key("4 assists in one fight"), "# assist in one fight");
    }

    #[test]
    fn learns_within_bounds() {
        let dir = std::env::temp_dir().join(format!("lh-learn-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let solo = vec!["1 solo kill".to_string()];
        let died = vec!["died immediately after".to_string()];
        record(&dir, &solo, GOOD);
        assert_eq!(bonus(&load(&dir), &solo), 0, "one vote is not enough");
        record(&dir, &["3 solo kills".to_string()], GOOD);
        assert_eq!(bonus(&load(&dir), &solo), 4);
        for _ in 0..10 {
            record(&dir, &died, BAD);
        }
        assert_eq!(bonus(&load(&dir), &died), -10, "capped per reason");
        let (likes, dislikes) = summary(&load(&dir));
        assert_eq!(likes[0].0, "solo kills");
        assert_eq!(dislikes[0].0, "died immediately after");
        reset(&dir);
        assert!(load(&dir).is_empty());
        let _ = fs::remove_dir_all(&dir);
    }
}
