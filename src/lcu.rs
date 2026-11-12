//! Queue filter ("ranked only", "skip ARAM", ...) using the League Client API.
//!
//! The client's lockfile (next to LeagueClientUx.exe) holds the local port and
//! password. Only `/lol-gameflow/v1/session` is read, once per game window.

use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::config::Settings;
use crate::util::base64;

const RANKED_SOLO: [i64; 1] = [420];
const RANKED_FLEX: [i64; 1] = [440];
const NORMAL_DRAFT: [i64; 1] = [400];
const ARAM: [i64; 8] = [65, 67, 100, 300, 450, 720, 920, 2400];
const ARENA: [i64; 2] = [1700, 1710];

#[derive(Clone, Debug, Default)]
pub struct QueueInfo {
    pub queue_id: Option<i64>,
    pub game_mode: String,
    pub is_custom: bool,
}

#[derive(Clone, Debug)]
pub struct Decision {
    pub allowed: bool,
    pub known: bool,
    pub reason: String,
}

fn lockfile_candidates() -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = crate::win::find_process_paths(&["leagueclientux.exe", "leagueclient.exe"])
        .into_iter()
        .filter_map(|p| Path::new(&p).parent().map(|d| d.join("lockfile")))
        .collect();
    let drive = std::env::var("SystemDrive").unwrap_or_else(|_| "C:".into());
    out.push(PathBuf::from(format!("{drive}\\Riot Games\\League of Legends\\lockfile")));
    out
}

fn credentials() -> Option<(u16, String)> {
    for path in lockfile_candidates() {
        let Ok(text) = std::fs::read_to_string(&path) else { continue };
        let parts: Vec<&str> = text.trim().split(':').collect();
        if parts.len() >= 5 {
            if let Ok(port) = parts[2].parse::<u16>() {
                return Some((port, parts[3].to_string()));
            }
        }
    }
    None
}

/// Read the current queue from the League Client, if it is running.
pub fn current_queue(agent: &ureq::Agent) -> Option<QueueInfo> {
    let (port, password) = credentials()?;
    let auth = format!("Basic {}", base64(format!("riot:{password}").as_bytes()));
    let response = agent
        .get(&format!("https://127.0.0.1:{port}/lol-gameflow/v1/session"))
        .set("Authorization", &auth)
        .set("Accept", "application/json")
        .call()
        .ok()?;
    let session: Value = serde_json::from_str(&response.into_string().ok()?).ok()?;
    let game_data = &session["gameData"];
    let queue = &game_data["queue"];
    let queue_id = queue["id"].as_i64().or_else(|| game_data["queueId"].as_i64());
    let game_mode = queue["gameMode"]
        .as_str()
        .or_else(|| session["map"]["gameMode"].as_str())
        .unwrap_or("")
        .to_string();
    Some(QueueInfo {
        queue_id: queue_id.filter(|id| *id >= 0),
        game_mode,
        is_custom: game_data["isCustomGame"].as_bool().unwrap_or(false),
    })
}

pub fn queue_label(queue_id: Option<i64>, mode: &str) -> String {
    let name = match queue_id {
        Some(420) => "Ranked Solo/Duo",
        Some(440) => "Ranked Flex",
        Some(400) => "Normal Draft",
        Some(430) => "Normal Blind",
        Some(490) => "Quickplay",
        Some(450) => "ARAM",
        Some(1700) | Some(1710) => "Arena",
        Some(900) | Some(1900) => "URF",
        Some(0) => "Custom game",
        _ => "",
    };
    if !name.is_empty() {
        return name.into();
    }
    match mode.to_uppercase().as_str() {
        "ARAM" => "ARAM".into(),
        "CHERRY" => "Arena".into(),
        "PRACTICETOOL" => "Practice Tool".into(),
        "" => "League game".into(),
        other => other.to_lowercase(),
    }
}

/// Port of `RecordingPolicyDetector.decision`.
pub fn decide(settings: &Settings, info: Option<&QueueInfo>, live_mode: &str) -> Decision {
    let queue_id = info.and_then(|i| i.queue_id);
    let mode = info.map(|i| i.game_mode.as_str()).filter(|m| !m.is_empty()).unwrap_or(live_mode);
    let text = mode.to_lowercase();
    let in_list = |list: &[i64]| queue_id.is_some_and(|id| list.contains(&id));
    let is_ranked = in_list(&RANKED_SOLO) || in_list(&RANKED_FLEX);
    let is_aram = in_list(&ARAM) || text.contains("aram");
    let is_arena = in_list(&ARENA) || text.contains("cherry") || text.contains("arena");
    let is_custom = info.is_some_and(|i| i.is_custom) || queue_id == Some(0);
    let known = queue_id.is_some() || !mode.is_empty();
    let label = queue_label(queue_id, mode);

    let skip = |reason: String| Decision { allowed: false, known: true, reason };
    if settings.recording_skip_custom_games && is_custom {
        return skip(format!("Skipped {label} — custom games are disabled"));
    }
    if settings.recording_skip_arena && is_arena {
        return skip(format!("Skipped {label} — Arena recording is disabled"));
    }
    let (allowed, wanted) = match settings.recording_scope.as_str() {
        "exclude_aram" => (known && !is_aram, "ARAM recording is disabled"),
        "ranked_only" => (is_ranked, "ranked games only"),
        "solo_duo_only" => (in_list(&RANKED_SOLO), "Solo/Duo only"),
        "flex_only" => (in_list(&RANKED_FLEX), "Ranked Flex only"),
        "normal_draft_only" => (in_list(&NORMAL_DRAFT), "Normal Draft only"),
        _ => (true, ""),
    };
    let reason = if allowed {
        format!("Recording {label}")
    } else if known {
        format!("Skipped {label} — {wanted}")
    } else {
        "Checking the game mode before recording".into()
    };
    Decision { allowed, known, reason }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn settings(scope: &str) -> Settings {
        Settings { recording_scope: scope.into(), ..Settings::default() }
    }

    #[test]
    fn ranked_only_rejects_aram() {
        let info = QueueInfo { queue_id: Some(450), game_mode: "ARAM".into(), is_custom: false };
        let d = decide(&settings("ranked_only"), Some(&info), "");
        assert!(!d.allowed && d.known);
        let info = QueueInfo { queue_id: Some(420), ..Default::default() };
        assert!(decide(&settings("ranked_only"), Some(&info), "").allowed);
    }

    #[test]
    fn unknown_queue_waits() {
        let d = decide(&settings("solo_duo_only"), None, "");
        assert!(!d.allowed && !d.known);
        // Live Client game mode is enough to exclude ARAM.
        let d = decide(&settings("exclude_aram"), None, "ARAM");
        assert!(!d.allowed && d.known);
    }
}
