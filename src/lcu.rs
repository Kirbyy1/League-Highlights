//! Queue filter ("ranked only", "skip ARAM", ...) using the League Client API.
//!
//! The client's lockfile (next to LeagueClientUx.exe) holds the local port and
//! password. `/lol-gameflow/v1/session` is read once per game window, and the
//! player's own recent match history is read after a game whose result the
//! Live Client API did not report, to fill in Win or Loss.

use std::path::{Path, PathBuf};

use std::collections::HashMap;

use serde_json::Value;

use crate::config::{Settings, SharedSettings};
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

fn get(agent: &ureq::Agent, path: &str) -> Option<Value> {
    let (port, password) = credentials()?;
    let auth = format!("Basic {}", base64(format!("riot:{password}").as_bytes()));
    let response = agent
        .get(&format!("https://127.0.0.1:{port}{path}"))
        .set("Authorization", &auth)
        .set("Accept", "application/json")
        .call()
        .ok()?;
    serde_json::from_str(&response.into_string().ok()?).ok()
}

/// Read the current queue from the League Client, if it is running.
pub fn current_queue(agent: &ureq::Agent) -> Option<QueueInfo> {
    let session = get(agent, "/lol-gameflow/v1/session")?;
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

/// A finished game from the player's match history.
#[derive(Clone, Debug, PartialEq)]
pub struct PastGame {
    /// Unix seconds when the game was created (before loading).
    pub created: f64,
    pub duration: f64,
    pub win: bool,
}

/// Fill in unknown results in the background, retrying for a few minutes:
/// the client adds a game to its history shortly after the end-of-game screen.
pub fn fill_unknown_results_later(dir: PathBuf) {
    let _ = std::thread::Builder::new().name("match-results".into()).spawn(move || {
        let agent = history_agent();
        for wait in [3, 20, 30, 60, 60, 120] {
            std::thread::sleep(std::time::Duration::from_secs(wait));
            fill_unknown_results(&agent, &dir, &mut HashMap::new());
            if crate::library::unknown_results(&dir).is_empty() {
                break;
            }
        }
    });
}

/// The Porofessor live-game page for the current player while they are in
/// champion select or in a game, when the setting is on. Read by the UI.
pub static LIVE_PAGE: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);

/// Background watcher. Does nothing while the League Client is closed.
/// - Every 5 s, when live game stats are on: is the player in champion
///   select or in a game? If so, publish their Porofessor page.
/// - Once a minute: look up matches saved without a result (older versions,
///   or a game closed before its result arrived). A match that is not in the
///   recent history is given up on after a few tries.
pub fn watch_client(cfg: SharedSettings) {
    let _ = std::thread::Builder::new().name("league-client-watch".into()).spawn(move || {
        let agent = history_agent();
        let mut misses: HashMap<String, u32> = HashMap::new();
        // (client port, page): the account is looked up once per client run.
        let mut account: Option<(u16, String)> = None;
        let mut tick = 0u32;
        loop {
            let (live_on, dir) = {
                let s = cfg.read().unwrap();
                (s.live_stats, s.clip_dir())
            };
            let minute = tick % 12 == 0;
            let client = if live_on || minute { credentials() } else { None };
            let mut page = None;
            match &client {
                Some((port, _)) => {
                    if live_on && in_game_flow(&agent) {
                        if account.as_ref().is_none_or(|(p, _)| p != port) {
                            account = account_page(&agent).map(|url| (*port, url));
                        }
                        page = account.as_ref().map(|(_, url)| url.clone());
                    }
                    if minute {
                        fill_unknown_results(&agent, &dir, &mut misses);
                    }
                }
                None => account = None,
            }
            // Development aid: preview the panel without a game running.
            if let Ok(test) = std::env::var("LH_LIVE_PAGE") {
                page = Some(test);
            }
            {
                let mut current = LIVE_PAGE.lock().unwrap();
                if *current != page {
                    log::info!("Live game stats page {}", if page.is_some() { "available" } else { "closed" });
                    *current = page;
                }
            }
            tick = tick.wrapping_add(1);
            std::thread::sleep(std::time::Duration::from_secs(5));
        }
    });
}

/// True from champion select until the game ends.
fn in_game_flow(agent: &ureq::Agent) -> bool {
    get(agent, "/lol-gameflow/v1/gameflow-phase")
        .and_then(|v| v.as_str().map(str::to_string))
        .is_some_and(|phase| matches!(phase.as_str(), "ChampSelect" | "GameStart" | "InProgress" | "Reconnect"))
}

fn account_page(agent: &ureq::Agent) -> Option<String> {
    let summoner = get(agent, "/lol-summoner/v1/current-summoner")?;
    let region = get(agent, "/riotclient/region-locale")?;
    porofessor_url(
        region["region"].as_str().unwrap_or(""),
        summoner["gameName"].as_str().unwrap_or(""),
        summoner["tagLine"].as_str().unwrap_or(""),
    )
}

/// `https://porofessor.gg/live/<server>/<name>-<tag>` for a League Client
/// region ("EUNE", "LA1", ...) and Riot ID.
pub fn porofessor_url(region: &str, game_name: &str, tag: &str) -> Option<String> {
    let server = match region.to_ascii_uppercase().as_str() {
        "EUW" | "EUW1" => "euw",
        "EUNE" | "EUN1" => "eune",
        "NA" | "NA1" => "na",
        "KR" => "kr",
        "BR" | "BR1" => "br",
        "LA1" | "LAN" => "lan",
        "LA2" | "LAS" => "las",
        "OC1" | "OCE" => "oce",
        "TR" | "TR1" => "tr",
        "RU" => "ru",
        "JP" | "JP1" => "jp",
        "SG" | "SG2" => "sg",
        "PH" | "PH2" => "ph",
        "TH" | "TH2" => "th",
        "TW" | "TW2" => "tw",
        "VN" | "VN2" => "vn",
        "ME" | "ME1" => "me",
        _ => return None,
    };
    if game_name.trim().is_empty() || tag.trim().is_empty() {
        return None;
    }
    Some(format!("https://porofessor.gg/live/{server}/{}-{}", percent(game_name.trim()), percent(tag.trim())))
}

fn percent(text: &str) -> String {
    let mut out = String::new();
    for byte in text.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~') {
            out.push(byte as char);
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    out
}

fn history_agent() -> ureq::Agent {
    let mut builder = ureq::AgentBuilder::new()
        .timeout_connect(std::time::Duration::from_millis(400))
        .timeout(std::time::Duration::from_secs(10));
    if let Ok(tls) = native_tls::TlsConnector::builder()
        .danger_accept_invalid_certs(true)
        .danger_accept_invalid_hostnames(true)
        .build()
    {
        builder = builder.tls_connector(std::sync::Arc::new(tls));
    }
    builder.build()
}

/// The current player's most recent games, newest first.
pub fn recent_games(agent: &ureq::Agent, count: usize) -> Option<Vec<PastGame>> {
    let path = format!("/lol-match-history/v1/products/lol/current-summoner/matches?begIndex=0&endIndex={count}");
    let data = get(agent, &path)?;
    let games = data["games"]["games"].as_array()?;
    Some(
        games
            .iter()
            .filter_map(|g| {
                let created = g["gameCreation"].as_f64()? / 1000.0;
                let win = g["participants"].get(0)?["stats"]["win"].as_bool()?;
                Some(PastGame { created, duration: g["gameDuration"].as_f64().unwrap_or(0.0), win })
            })
            .collect(),
    )
}

/// The history game that is the match whose clock started at `started`
/// (Live Client time) and lasted `duration` seconds. A game is created before
/// the loading screen, so its creation time is up to ten minutes earlier.
pub fn find_game(games: &[PastGame], started: f64, duration: f64) -> Option<&PastGame> {
    games
        .iter()
        .filter(|g| g.created <= started + 60.0 && started - g.created <= 600.0)
        .filter(|g| duration <= 0.0 || g.duration <= 0.0 || (g.duration - duration).abs() <= 180.0)
        .min_by(|a, b| (started - a.created).abs().total_cmp(&(started - b.created).abs()))
}

/// Fill in Win or Loss for clip groups saved with an unknown result, from the
/// League Client's match history. Returns how many groups were updated, or
/// None when the client could not be reached.
pub fn fill_unknown_results(agent: &ureq::Agent, dir: &Path, misses: &mut HashMap<String, u32>) -> Option<usize> {
    let unknown: Vec<_> = crate::library::unknown_results(dir)
        .into_iter()
        .filter(|(id, ..)| misses.get(id).copied().unwrap_or(0) < 5)
        .collect();
    if unknown.is_empty() {
        return Some(0);
    }
    let games = recent_games(agent, 20)?;
    let mut updated = 0;
    for (match_id, started, duration) in unknown {
        match find_game(&games, started, duration) {
            Some(game) => {
                let result = if game.win { "WIN" } else { "LOSE" };
                if crate::library::set_match_result(dir, &match_id, result) > 0 {
                    log::info!("Match result from the League Client: {match_id} ({result})");
                    updated += 1;
                }
            }
            None => *misses.entry(match_id).or_insert(0) += 1,
        }
    }
    Some(updated)
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
    fn history_game_matches_by_start_and_length() {
        let games = vec![
            PastGame { created: 10_000.0, duration: 1800.0, win: true },
            PastGame { created: 7_000.0, duration: 1500.0, win: false },
        ];
        // Clock started 90 s after creation, same length: the first game.
        assert_eq!(find_game(&games, 10_090.0, 1790.0).map(|g| g.win), Some(true));
        assert_eq!(find_game(&games, 7_100.0, 1490.0).map(|g| g.win), Some(false));
        // Too far from any game, or a very different length: no match.
        assert!(find_game(&games, 20_000.0, 1800.0).is_none());
        assert!(find_game(&games, 10_090.0, 600.0).is_none());
    }

    #[test]
    fn porofessor_page_uses_server_and_riot_id() {
        assert_eq!(
            porofessor_url("EUNE", "Y\u{f8}nko", "BAZ").as_deref(),
            Some("https://porofessor.gg/live/eune/Y%C3%B8nko-BAZ")
        );
        assert_eq!(porofessor_url("LA2", "Two Words", "na1").as_deref(), Some("https://porofessor.gg/live/las/Two%20Words-na1"));
        assert!(porofessor_url("PBE", "Name", "TAG").is_none());
        assert!(porofessor_url("EUW", "", "TAG").is_none());
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
