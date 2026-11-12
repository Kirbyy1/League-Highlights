//! Smart Highlights: reads Riot's local Live Client Data API and turns kills,
//! assists and objective steals into scored, fight-grouped clip requests.
//!
//! Ported from `league_events.py` + `league_events_v2.py`. Differences:
//! * no network traffic at all while the League game window is absent;
//! * one keep-alive TLS connection instead of a new handshake per request;
//! * the full player list / game stats refresh every 3 s instead of every 1 s
//!   (the active player, which carries health for low-HP detection, stays 1 s);
//! * grouping timers are deadlines checked by the poll loop (no timer threads).

use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::Sender;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::config::SharedSettings;
use crate::models::{Cmd, EventTracker, HighlightRequest, MatchContext};
use crate::scoring::{score_candidate, FeedbackProfile, PlayCandidate};
use crate::util::{local_time, normalise_name, now, slug};
use std::path::PathBuf;

const BASE: &str = "https://127.0.0.1:2999/liveclientdata";

pub struct LiveShared {
    pub current_match: Mutex<Option<MatchContext>>,
    pub game_present: AtomicBool,
    pub status: Mutex<(String, bool)>,
    pub stop: AtomicBool,
}

impl LiveShared {
    pub fn new() -> Arc<LiveShared> {
        Arc::new(LiveShared {
            current_match: Mutex::new(None),
            game_present: AtomicBool::new(false),
            status: Mutex::new(("Waiting for active match data".into(), false)),
            stop: AtomicBool::new(false),
        })
    }
}

/// HTTPS agent for Riot's local endpoints (self-signed certificates).
pub fn local_agent() -> ureq::Agent {
    let mut builder = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_millis(400))
        .timeout(Duration::from_millis(1500))
        .user_agent("LeagueHighlights/1.0");
    if let Ok(tls) = native_tls::TlsConnector::builder()
        .danger_accept_invalid_certs(true)
        .danger_accept_invalid_hostnames(true)
        .build()
    {
        builder = builder.tls_connector(Arc::new(tls));
    }
    builder.build()
}

fn get_json(agent: &ureq::Agent, endpoint: &str) -> Result<Value, String> {
    let response = agent
        .get(&format!("{BASE}{endpoint}"))
        .set("Accept", "application/json")
        .call()
        .map_err(|e| e.to_string())?;
    let text = response.into_string().map_err(|e| e.to_string())?;
    serde_json::from_str(&text).map_err(|e| e.to_string())
}

// ---------------------------------------------------------------------------
// Helpers mirroring the Python behaviour
// ---------------------------------------------------------------------------

fn aliases_of(value: &Value) -> HashSet<String> {
    let mut set = HashSet::new();
    if let Some(text) = value.as_str() {
        let n = normalise_name(text);
        if !n.is_empty() {
            if let Some((name, _)) = n.split_once('#') {
                let name = name.trim();
                if !name.is_empty() {
                    set.insert(name.to_string());
                }
            }
            set.insert(n);
        }
    }
    set
}

fn field<'a>(event: &'a Value, key: &str) -> &'a Value {
    if let Some(v) = event.get(key) {
        return v;
    }
    if let Some(map) = event.as_object() {
        for (k, v) in map {
            if k.eq_ignore_ascii_case(key) {
                return v;
            }
        }
    }
    &Value::Null
}

fn as_bool(value: &Value) -> bool {
    match value {
        Value::Bool(b) => *b,
        Value::String(s) => matches!(s.trim().to_lowercase().as_str(), "1" | "true" | "yes"),
        Value::Number(n) => n.as_i64() == Some(1),
        _ => false,
    }
}

fn as_i64(value: &Value) -> i64 {
    value
        .as_i64()
        .or_else(|| value.as_f64().map(|f| f as i64))
        .or_else(|| value.as_str().and_then(|s| s.trim().parse().ok()))
        .unwrap_or(0)
}

fn as_f64(value: &Value) -> Option<f64> {
    value
        .as_f64()
        .or_else(|| value.as_str().and_then(|s| s.trim().parse().ok()))
}

fn text(value: &Value) -> String {
    value.as_str().unwrap_or("").to_string()
}

fn list(value: &Value) -> Vec<Value> {
    value.as_array().cloned().unwrap_or_default()
}

#[derive(Clone, Debug, Default)]
struct Identity {
    riot_id: String,
    game_name: String,
    summoner_name: String,
    champion_name: String,
    team: String,
    level: i64,
    is_dead: bool,
    kills: i64,
    deaths: i64,
    assists: i64,
    aliases: HashSet<String>,
}

impl Identity {
    fn display_name(&self) -> String {
        [&self.riot_id, &self.game_name, &self.summoner_name]
            .into_iter()
            .find(|s| !s.is_empty())
            .cloned()
            .unwrap_or_else(|| "Unknown player".into())
    }

    fn known(&self) -> bool {
        !self.aliases.is_empty()
    }
}

struct Snapshot {
    wall: f64,
    health: Option<f64>,
    level: i64,
    is_dead: bool,
}

struct KillRecord {
    wall: f64,
    game_time: f64,
    victim_name: String,
    victim_champion: String,
    victim_level: i64,
    assisters: Vec<String>,
    solo: bool,
    /// The victim's kill streak before this death (shutdown value).
    victim_streak: i64,
    first_blood: bool,
}

struct AssistRecord {
    wall: f64,
    game_time: f64,
    killer_name: String,
    victim_name: String,
    victim_champion: String,
}

struct Recent {
    match_id: String,
    start: f64,
    end: f64,
    kind: String,
    score: i64,
    seen: Instant,
}

const KILL_LABELS: [&str; 5] = ["SINGLE KILL", "DOUBLE KILL", "TRIPLE KILL", "QUADRA KILL", "PENTAKILL"];
const KILL_SETTLE: Duration = Duration::from_millis(10_500);
const ASSIST_SETTLE: Duration = Duration::from_millis(11_500);
const GROUP_GAP: f64 = 12.0;
const COMBAT_FLUSH: Duration = Duration::from_millis(1800);
const OBJECTIVE_FLUSH: Duration = Duration::from_secs(12);

pub struct Detector {
    cfg: SharedSettings,
    tx: Sender<Cmd>,
    tracker: Arc<EventTracker>,
    shared: Arc<LiveShared>,
    feedback: FeedbackProfile,

    last_event_id: Option<i64>,
    active_aliases: HashSet<String>,
    active_team: Option<String>,
    active: Identity,
    by_alias: HashMap<String, Identity>,
    next_full_refresh: Instant,
    next_active_refresh: Instant,

    snapshots: VecDeque<Snapshot>,
    kills: Vec<KillRecord>,
    assists: Vec<AssistRecord>,
    multikill_streak: i64,
    last_kill_at: Option<Instant>,
    last_kill_game_time: f64,
    last_assist_at: Option<Instant>,
    last_assist_game_time: f64,
    last_ace_wall: f64,

    current: Option<MatchContext>,
    match_ended: bool,
    last_game_time: f64,
    last_success: f64,

    pending: Vec<HighlightRequest>,
    pending_deadline: Option<Instant>,
    recent: VecDeque<Recent>,

    /// Kills since last death, per player (normalised name): shutdowns.
    streaks: HashMap<String, i64>,
    first_blood_pending: Option<Instant>,
    /// Automatic clips saved this match (noise budget).
    match_clips: u32,
    /// Wall time where the last automatic clip ends.
    last_auto_end: f64,
    data_root: PathBuf,
    learned: HashMap<String, crate::learn::Weight>,
    learned_refresh: Option<Instant>,
}

impl Detector {
    pub fn new(
        data_root: PathBuf,
        cfg: SharedSettings,
        tx: Sender<Cmd>,
        tracker: Arc<EventTracker>,
        shared: Arc<LiveShared>,
    ) -> Detector {
        let clip_dir = cfg.read().unwrap().clip_dir();
        let now_instant = Instant::now();
        Detector {
            cfg,
            tx,
            tracker,
            shared,
            feedback: FeedbackProfile::new(clip_dir),
            last_event_id: None,
            active_aliases: HashSet::new(),
            active_team: None,
            active: Identity::default(),
            by_alias: HashMap::new(),
            next_full_refresh: now_instant,
            next_active_refresh: now_instant,
            snapshots: VecDeque::with_capacity(300),
            kills: Vec::new(),
            assists: Vec::new(),
            multikill_streak: 0,
            last_kill_at: None,
            last_kill_game_time: 0.0,
            last_assist_at: None,
            last_assist_game_time: 0.0,
            last_ace_wall: 0.0,
            current: None,
            match_ended: false,
            last_game_time: 0.0,
            last_success: 0.0,
            pending: Vec::new(),
            pending_deadline: None,
            recent: VecDeque::new(),
            streaks: HashMap::new(),
            first_blood_pending: None,
            match_clips: 0,
            last_auto_end: 0.0,
            data_root,
            learned: HashMap::new(),
            learned_refresh: None,
        }
    }

    pub fn run(mut self) {
        let agent = local_agent();
        self.set_status("Waiting for active match data", false);
        while !self.shared.stop.load(Ordering::SeqCst) {
            let present = self.shared.game_present.load(Ordering::SeqCst);
            let mut connected = false;
            if present {
                match self.tick(&agent) {
                    Ok(()) => {
                        connected = true;
                        self.last_success = now();
                        let text = self.connected_text();
                        self.set_status(&text, true);
                    }
                    Err(_) => self.set_status("Waiting for active match data", false),
                }
            } else {
                self.set_status("Waiting for active match data", false);
            }
            if !connected
                && self.current.is_some()
                && !self.match_ended
                && self.last_success > 0.0
                && now() - self.last_success > 20.0
            {
                self.end_match("UNKNOWN");
            }
            self.flush_kills(false);
            self.flush_assists(false);
            if self.pending_deadline.is_some_and(|d| Instant::now() >= d) {
                self.flush_pending();
            }
            let wait = if connected {
                Duration::from_millis(500)
            } else if present {
                Duration::from_millis(1500)
            } else {
                Duration::from_secs(1)
            };
            std::thread::sleep(wait);
        }
        // Shutdown never starts a new export.
        self.pending.clear();
    }

    fn tick(&mut self, agent: &ureq::Agent) -> Result<(), String> {
        let instant = Instant::now();
        if instant >= self.next_full_refresh || self.active_aliases.is_empty() {
            self.refresh_live_state(agent, true)?;
            self.next_full_refresh = instant + Duration::from_secs(3);
            self.next_active_refresh = instant + Duration::from_secs(1);
        } else if instant >= self.next_active_refresh {
            self.refresh_live_state(agent, false)?;
            self.next_active_refresh = instant + Duration::from_secs(1);
        }
        let payload = get_json(agent, "/eventdata")?;
        let events = list(field(&payload, "Events"));
        self.consume_events(&events);
        Ok(())
    }

    fn refresh_live_state(&mut self, agent: &ureq::Agent, full: bool) -> Result<(), String> {
        let active = get_json(agent, "/activeplayer")?;
        if !active.is_object() {
            return Err("activeplayer unavailable".into());
        }
        if full {
            let players = get_json(agent, "/playerlist")?;
            let stats = get_json(agent, "/gamestats")?;
            let Some(players) = players.as_array() else {
                return Err("playerlist unavailable".into());
            };

            let mut active_aliases = HashSet::new();
            for key in ["riotId", "riotIdGameName", "summonerName"] {
                active_aliases.extend(aliases_of(&active[key]));
            }
            let mut by_alias = HashMap::new();
            let mut active_identity = Identity::default();
            for raw in players {
                let mut aliases = HashSet::new();
                for key in ["riotId", "riotIdGameName", "summonerName"] {
                    aliases.extend(aliases_of(&raw[key]));
                }
                let scores = &raw["scores"];
                let identity = Identity {
                    riot_id: text(&raw["riotId"]),
                    game_name: text(&raw["riotIdGameName"]),
                    summoner_name: text(&raw["summonerName"]),
                    champion_name: text(&raw["championName"]),
                    team: raw["team"].as_str().unwrap_or("UNKNOWN").to_uppercase(),
                    level: as_i64(&raw["level"]),
                    is_dead: as_bool(&raw["isDead"]),
                    kills: as_i64(&scores["kills"]),
                    deaths: as_i64(&scores["deaths"]),
                    assists: as_i64(&scores["assists"]),
                    aliases: aliases.clone(),
                };
                for alias in &aliases {
                    by_alias.insert(alias.clone(), identity.clone());
                }
                if !aliases.is_disjoint(&active_aliases) {
                    active_aliases.extend(aliases.iter().cloned());
                    active_identity = identity;
                }
            }
            if !active_identity.known() {
                active_identity = Identity {
                    riot_id: text(&active["riotId"]),
                    game_name: text(&active["riotIdGameName"]),
                    summoner_name: text(&active["summonerName"]),
                    level: as_i64(&active["level"]),
                    team: "UNKNOWN".into(),
                    aliases: active_aliases.clone(),
                    ..Default::default()
                };
            }
            let identity_changed = !self.active_aliases.is_empty()
                && !active_aliases.is_empty()
                && active_aliases != self.active_aliases;
            self.active_team = (active_identity.team != "UNKNOWN").then(|| active_identity.team.clone());
            self.active_aliases = active_aliases;
            self.active = active_identity;
            self.by_alias = by_alias;

            let game_time = as_f64(&stats["gameTime"]).unwrap_or(0.0);
            self.update_match_context(&stats, game_time);
            if identity_changed {
                log::info!("Active League player changed; resetting event baseline");
                self.last_event_id = None;
                self.clear_kills();
                self.clear_assists();
            }
        }
        self.append_snapshot(&active);
        Ok(())
    }

    fn update_match_context(&mut self, stats: &Value, game_time: f64) {
        let wall = now();
        let start_epoch = wall - game_time.max(0.0);
        let player = self.active.display_name();
        let champion = self.active.champion_name.clone();
        let game_mode = text(&stats["gameMode"]);
        let map_name = text(&stats["mapName"]);

        let new_game = match &self.current {
            None => true,
            Some(current) => {
                game_time + 20.0 < self.last_game_time || (start_epoch - current.started_at).abs() > 60.0
            }
        };
        if new_game {
            if self.current.is_some() && !self.match_ended {
                self.end_match("UNKNOWN");
            }
            let match_id = format!("{}_{}", local_time(start_epoch).compact(), slug(&player));
            self.current = Some(MatchContext {
                match_id: match_id.clone(),
                player_name: player,
                champion_name: champion,
                game_mode,
                map_name,
                started_at: start_epoch,
                kills: self.active.kills,
                deaths: self.active.deaths,
                assists: self.active.assists,
                duration_seconds: game_time.max(0.0),
                team: self.active.team.clone(),
            });
            self.match_ended = false;
            self.last_event_id = None;
            self.clear_kills();
            self.clear_assists();
            self.snapshots.clear();
            self.streaks.clear();
            self.first_blood_pending = None;
            self.match_clips = 0;
            self.last_auto_end = 0.0;
            log::info!("Match session started: {match_id}");
            if let Some(ctx) = &self.current {
                let _ = self.tx.send(Cmd::MatchStarted(ctx.clone()));
            }
        } else if let Some(current) = self.current.as_mut() {
            if player != "Unknown player" {
                current.player_name = player;
            }
            if !champion.is_empty() {
                current.champion_name = champion;
            }
            if !game_mode.is_empty() {
                current.game_mode = game_mode;
            }
            if !map_name.is_empty() {
                current.map_name = map_name;
            }
            current.kills = self.active.kills;
            current.deaths = self.active.deaths;
            current.assists = self.active.assists;
            current.duration_seconds = game_time.max(0.0);
            current.team = self.active.team.clone();
        }
        self.last_game_time = game_time;
        *self.shared.current_match.lock().unwrap() = self.current.clone();
    }

    fn append_snapshot(&mut self, active: &Value) {
        let stats = &active["championStats"];
        let current = as_f64(&stats["currentHealth"]).unwrap_or(-1.0);
        let maximum = as_f64(&stats["maxHealth"]).unwrap_or(-1.0);
        let health = (current >= 0.0 && maximum > 0.0).then(|| (current / maximum * 100.0).clamp(0.0, 100.0));
        let level = match as_i64(&active["level"]) {
            0 => self.active.level,
            level => level,
        };
        if self.snapshots.len() >= 300 {
            self.snapshots.pop_front();
        }
        self.snapshots.push_back(Snapshot {
            wall: now(),
            health,
            level,
            is_dead: health.is_some_and(|h| h <= 0.0) || self.active.is_dead,
        });
    }

    fn consume_events(&mut self, events: &[Value]) {
        let mut indexed: Vec<(i64, &Value)> = events
            .iter()
            .filter_map(|e| {
                let id = field(e, "EventID");
                (!id.is_null()).then(|| (as_i64(id), e))
            })
            .filter(|(id, _)| *id >= 0)
            .collect();
        if indexed.is_empty() {
            return;
        }
        indexed.sort_by_key(|(id, _)| *id);
        let maximum = indexed.last().map(|(id, _)| *id).unwrap_or(0);
        let Some(last) = self.last_event_id else {
            for (_, event) in &indexed {
                if text(field(event, "EventName")).eq_ignore_ascii_case("ChampionKill") {
                    self.update_streaks(event);
                }
            }
            self.last_event_id = Some(maximum);
            log::info!("Live event baseline established at EventID {maximum}");
            return;
        };
        if maximum < last {
            log::info!("New League event stream detected; resetting baseline");
            self.last_event_id = Some(maximum);
            self.clear_kills();
            self.clear_assists();
            return;
        }
        for (id, event) in indexed {
            if id <= self.last_event_id.unwrap_or(-1) {
                continue;
            }
            self.handle_event(event);
            self.last_event_id = Some(id);
        }
    }

    fn handle_event(&mut self, event: &Value) {
        let name = text(field(event, "EventName")).to_lowercase();
        if name.is_empty() {
            return;
        }
        self.track(&name, event);
        let victim_streak = if name == "championkill" { self.update_streaks(event) } else { 0 };
        let (smart, dragon_on, baron_on, other_on, victory_on) = {
            let c = self.cfg.read().unwrap();
            (c.smart_highlights_enabled, c.auto_clip_dragon, c.auto_clip_baron, c.auto_clip_other_steals, c.auto_clip_victory)
        };
        if !smart && (matches!(name.as_str(), "championkill" | "multikill" | "ace" | "firstblood") || name.ends_with("kill")) {
            if name == "championkill" || name == "multikill" {
                self.clear_kills();
                self.clear_assists();
            }
            return;
        }

        if name == "championkill" && self.is_active(field(event, "KillerName")) {
            let game_time = event_time(event);
            if !self.kills.is_empty()
                && game_time > 0.0
                && self.last_kill_game_time > 0.0
                && game_time - self.last_kill_game_time > 12.0
            {
                self.emit_pending_kill();
            }
            let victim_raw = field(event, "VictimName");
            let victim = self.identity_for(victim_raw);
            let assisters = self.canonical_names(field(event, "Assisters"));
            self.kills.push(KillRecord {
                wall: now(),
                game_time,
                victim_name: if victim.known() {
                    victim.display_name()
                } else {
                    victim_raw.as_str().filter(|s| !s.is_empty()).unwrap_or("Enemy").to_string()
                },
                victim_champion: victim.champion_name.clone(),
                victim_level: victim.level,
                solo: assisters.is_empty(),
                assisters,
                victim_streak,
                first_blood: self.first_blood_pending.take().is_some_and(|t| t.elapsed() < Duration::from_secs(5)),
            });
            self.last_kill_at = Some(Instant::now());
            self.last_kill_game_time = game_time;
            log::info!(
                "Detected active-player champion kill: {} ({})",
                victim.display_name(),
                if victim.champion_name.is_empty() { "unknown champion" } else { &victim.champion_name }
            );
            return;
        }

        if name == "championkill" {
            let assisted = list(field(event, "Assisters"))
                .iter()
                .any(|a| !aliases_of(a).is_disjoint(&self.active_aliases));
            if assisted && self.belongs_to_active_team(event) {
                let game_time = event_time(event);
                if !self.assists.is_empty()
                    && game_time > 0.0
                    && self.last_assist_game_time > 0.0
                    && game_time - self.last_assist_game_time > 14.0
                {
                    self.emit_pending_assist();
                }
                let victim_raw = field(event, "VictimName");
                let killer_raw = field(event, "KillerName");
                let victim = self.identity_for(victim_raw);
                let killer = self.identity_for(killer_raw);
                self.assists.push(AssistRecord {
                    wall: now(),
                    game_time,
                    killer_name: if killer.known() { killer.display_name() } else { text(killer_raw) },
                    victim_name: if victim.known() { victim.display_name() } else { text(victim_raw) },
                    victim_champion: victim.champion_name.clone(),
                });
                self.last_assist_at = Some(Instant::now());
                self.last_assist_game_time = game_time;
                log::info!("Detected active-player assist");
                return;
            }
        }

        if name == "multikill" && self.is_active(field(event, "KillerName")) {
            let streak = as_i64(field(event, "KillStreak")).clamp(1, 5);
            self.multikill_streak = self.multikill_streak.max(streak);
            self.last_kill_at = Some(Instant::now());
            self.last_kill_game_time = event_time(event);
            log::info!("Detected exact Riot multikill event (streak={streak})");
            return;
        }
        match name.as_str() {
            "ace" => self.last_ace_wall = now(),
            "firstblood" if self.is_active(field(event, "Recipient")) => {
                // Usually arrives right after the kill; otherwise mark the next one.
                match self.kills.last_mut() {
                    Some(kill) => kill.first_blood = true,
                    None => self.first_blood_pending = Some(Instant::now()),
                }
            }
            "dragonkill" if dragon_on && self.belongs_to_active_team(event) => self.emit_objective(event, "dragon"),
            "baronkill" if baron_on && self.belongs_to_active_team(event) => self.emit_objective(event, "baron"),
            // Rift Herald, Voidgrubs, Atakhan and any future epic monster.
            other if other.ends_with("kill") && other != "dragonkill" && other != "baronkill" && other_on && self.belongs_to_active_team(event) => {
                self.emit_objective(event, "objective")
            }
            "gameend" => {
                self.flush_kills(true);
                self.flush_assists(true);
                let mut result = text(field(event, "Result"));
                if result.is_empty() {
                    result = text(field(event, "GameResult"));
                }
                let result = if result.is_empty() { "UNKNOWN".into() } else { result.to_uppercase() };
                self.end_match(&result);
                if result.starts_with("WIN") && victory_on && smart {
                    self.emit_victory(event);
                }
            }
            _ => {}
        }
    }

    /// Forward tiny timestamps used for Smart Trim markers.
    fn track(&mut self, name: &str, event: &Value) {
        let event_type = match name {
            "championkill" => {
                if self.is_active(field(event, "KillerName")) {
                    "CHAMPION_KILL"
                } else if self.is_active(field(event, "VictimName")) {
                    "PLAYER_DEATH"
                } else if list(field(event, "Assisters"))
                    .iter()
                    .any(|a| !aliases_of(a).is_disjoint(&self.active_aliases))
                {
                    "ASSIST"
                } else {
                    ""
                }
            }
            "multikill" if self.is_active(field(event, "KillerName")) => "MULTIKILL",
            "ace" => "ACE",
            "dragonkill" if self.belongs_to_active_team(event) => {
                if as_bool(field(event, "Stolen")) { "DRAGON_STEAL" } else { "DRAGON" }
            }
            "baronkill" if self.belongs_to_active_team(event) => {
                if as_bool(field(event, "Stolen")) { "BARON_STEAL" } else { "BARON" }
            }
            other if other.ends_with("kill") && as_bool(field(event, "Stolen")) && self.belongs_to_active_team(event) => "OBJECTIVE_STEAL",
            _ => "",
        };
        if !event_type.is_empty() {
            self.tracker.record(event_type, event_time(event), &self.match_id(), now());
        }
    }

    fn emit_objective(&mut self, event: &Value, kind: &'static str) {
        // Routine secures create too many low-value clips; only steals are kept.
        if !as_bool(field(event, "Stolen")) {
            log::info!("Ignored routine {kind} secure; only objective steals are clipped");
            return;
        }
        let elder = kind == "dragon" && normalise_name(&text(field(event, "DragonType"))) == "elder";
        let monster = text(field(event, "EventName")).to_lowercase();
        let label = match (kind, elder) {
            ("dragon", true) => "ELDER DRAGON STEAL",
            ("dragon", false) => "DRAGON STEAL",
            ("baron", _) => "BARON STEAL",
            _ => match monster.as_str() {
                "heraldkill" => "RIFT HERALD STEAL",
                "hordekill" => "VOIDGRUB STEAL",
                "atakhankill" => "ATAKHAN STEAL",
                _ => "OBJECTIVE STEAL",
            },
        };
        let candidate = PlayCandidate { kind, stolen: true, elder, ..Default::default() };
        let Some(result) = self.score(&candidate, kind, label) else { return };
        let wall = now();
        log::info!("Automatic objective highlight: {label}, smart score {}", result.score);
        let request = HighlightRequest {
            label: label.into(),
            event_started_at: Some(wall),
            event_ended_at: Some(wall),
            pre_seconds: 8.0,
            post_seconds: 6.0,
            event_game_time: Some(event_time(event)),
            event_kind: kind.into(),
            automatic: true,
            highlight_score: result.score,
            score_reasons: result.reasons,
            assister_names: self.canonical_names(field(event, "Assisters")),
            ..self.request_base()
        };
        self.collect(request);
    }

    fn score(&mut self, c: &PlayCandidate, kind: &str, label: &str) -> Option<crate::scoring::ScoreResult> {
        let (enabled, sensitivity, clip_dir) = {
            let cfg = self.cfg.read().unwrap();
            (cfg.smart_highlights_enabled, cfg.smart_sensitivity.clone(), cfg.clip_dir())
        };
        self.feedback.set_dir(clip_dir);
        // After 6 automatic clips in one game, routine plays need more.
        let budget = if self.match_clips >= 6 { (6 * (self.match_clips as i64 - 5)).min(30) } else { 0 };
        let adjust = self.feedback.adjustment(kind, label) + budget;
        let mut result = score_candidate(c, enabled, &sensitivity, adjust);
        if self.learned_refresh.is_none_or(|t| t.elapsed() > Duration::from_secs(15)) {
            self.learned = crate::learn::load(&self.data_root);
            self.learned_refresh = Some(Instant::now());
        }
        let learned = crate::learn::bonus(&self.learned, &result.reasons);
        if learned != 0 {
            result.score = (result.score + learned).max(0);
            result.reasons.push(format!("your ratings {learned:+}"));
            if enabled && !result.keep && result.score >= result.threshold {
                result.keep = true;
            } else if enabled && result.keep && result.score < result.threshold && learned < 0 && c.kill_count < 3 && !c.stolen {
                result.keep = false;
            }
        }
        if budget > 0 && !result.keep {
            log::info!("Match already has {} automatic clips; skipping routine play", self.match_clips);
        }
        if !result.keep {
            log::info!(
                "Smart filter skipped {label} (score {}, threshold {}): {}",
                result.score,
                result.threshold,
                result.reasons.join(", ")
            );
            return None;
        }
        Some(result)
    }

    fn request_base(&self) -> HighlightRequest {
        HighlightRequest {
            match_id: self.match_id(),
            player_name: self.active.display_name(),
            champion_name: self.active.champion_name.clone(),
            game_mode: self.current.as_ref().map(|c| c.game_mode.clone()).unwrap_or_default(),
            match_started_at: self.current.as_ref().map(|c| c.started_at),
            ..Default::default()
        }
    }

    fn flush_kills(&mut self, force: bool) {
        if self.kills.is_empty() && self.multikill_streak == 0 {
            return;
        }
        if !force && !self.last_kill_at.is_some_and(|t| t.elapsed() >= KILL_SETTLE) {
            return;
        }
        self.emit_pending_kill();
    }

    fn emit_pending_kill(&mut self) {
        if self.kills.is_empty() && self.multikill_streak == 0 {
            return;
        }
        if !self.cfg.read().unwrap().smart_highlights_enabled {
            self.clear_kills();
            return;
        }
        let records = std::mem::take(&mut self.kills);
        let count = (records.len() as i64).max(self.multikill_streak).clamp(1, 5);
        let wall = now();
        let first_wall = records.first().map_or(wall, |r| r.wall);
        let last_wall = records.last().map_or(first_wall, |r| r.wall);
        let first_game_time = records.first().map_or(self.last_kill_game_time, |r| r.game_time);
        self.clear_kills();

        if !self.cfg.read().unwrap().kill_clip_enabled(count as usize) {
            log::info!("Skipped {} because it is disabled", KILL_LABELS[(count - 1) as usize]);
            return;
        }
        let window: Vec<&Snapshot> = self
            .snapshots
            .iter()
            .filter(|s| s.wall >= first_wall - 1.0 && s.wall <= last_wall + 7.0)
            .collect();
        let min_health = window
            .iter()
            .filter(|s| !s.is_dead)
            .filter_map(|s| s.health)
            .min_by(f64::total_cmp);
        let died_after = window
            .iter()
            .any(|s| s.is_dead && s.wall >= last_wall && s.wall <= last_wall + 6.0);
        let active_level = window
            .iter()
            .rfind(|s| s.wall <= first_wall + 1.0)
            .map_or(self.active.level, |s| s.level);
        let highest_victim_level = records.iter().map(|r| r.victim_level).max().unwrap_or(0);
        let solo_kills = records.iter().filter(|r| r.solo).count() as i64;
        let outnumbered = if count >= 2 && solo_kills == count { count } else { 0 };
        let ace = first_wall <= self.last_ace_wall && self.last_ace_wall <= last_wall + 6.0;

        let victim_streak = records.iter().map(|r| r.victim_streak).max().unwrap_or(0);
        let first_blood = records.iter().any(|r| r.first_blood);
        let mut label = KILL_LABELS[(count - 1) as usize].to_string();
        if count == 2 && solo_kills == 2 {
            label = "2V1 DOUBLE KILL".into();
        } else if count == 1 && first_blood {
            label = "FIRST BLOOD".into();
        } else if count == 1 && victim_streak >= 3 {
            label = "SHUTDOWN".into();
        }
        let candidate = PlayCandidate {
            kind: "kill",
            kill_count: count,
            solo_kills,
            outnumbered_kill_count: outnumbered,
            active_level,
            highest_victim_level,
            min_health_percent: min_health,
            died_after,
            ace,
            action_seconds: (last_wall - first_wall).max(0.0),
            victim_streak,
            first_blood,
            ..Default::default()
        };
        let Some(result) = self.score(&candidate, "kill", &label) else { return };
        let (pre, post) = if outnumbered >= 2 {
            (11.0, 9.0)
        } else if count >= 4 {
            (8.0, 8.0)
        } else {
            (7.0, 7.0)
        };
        let mut assisters: Vec<String> = Vec::new();
        for name in records.iter().flat_map(|r| r.assisters.iter()) {
            if !assisters.contains(name) {
                assisters.push(name.clone());
            }
        }
        log::info!("Automatic highlight ready: {label} (score {}): {}", result.score, result.reasons.join(", "));
        let request = HighlightRequest {
            label,
            event_started_at: Some(first_wall),
            event_ended_at: Some(last_wall),
            pre_seconds: pre,
            post_seconds: post,
            event_game_time: Some(first_game_time),
            event_kind: "kill".into(),
            automatic: true,
            highlight_score: result.score,
            score_reasons: result.reasons,
            victim_names: records.iter().map(|r| r.victim_name.clone()).filter(|s| !s.is_empty()).collect(),
            victim_champions: records
                .iter()
                .map(|r| r.victim_champion.clone())
                .filter(|s| !s.is_empty())
                .collect(),
            assister_names: assisters,
            ..self.request_base()
        };
        self.collect(request);
    }

    fn flush_assists(&mut self, force: bool) {
        if self.assists.is_empty() {
            return;
        }
        if !force && !self.last_assist_at.is_some_and(|t| t.elapsed() >= ASSIST_SETTLE) {
            return;
        }
        self.emit_pending_assist();
    }

    fn emit_pending_assist(&mut self) {
        if self.assists.is_empty() {
            return;
        }
        if !self.cfg.read().unwrap().smart_highlights_enabled {
            self.clear_assists();
            return;
        }
        let records = std::mem::take(&mut self.assists);
        self.clear_assists();
        let first_wall = records[0].wall;
        let last_wall = records[records.len() - 1].wall;
        let window: Vec<&Snapshot> = self
            .snapshots
            .iter()
            .filter(|s| s.wall >= first_wall - 2.0 && s.wall <= last_wall + 8.0)
            .collect();
        let min_health = window
            .iter()
            .filter(|s| !s.is_dead)
            .filter_map(|s| s.health)
            .min_by(f64::total_cmp);
        let died_after = window
            .iter()
            .any(|s| s.is_dead && s.wall >= first_wall && s.wall <= last_wall + 7.0);
        let ace = first_wall <= self.last_ace_wall && self.last_ace_wall <= last_wall + 7.0;
        let count = records.len() as i64;
        let assist_heavy = self.active.assists >= (self.active.kills + 2).max(3);
        let label = if ace && count >= 2 {
            "ACE PARTICIPATION"
        } else if min_health.is_some_and(|h| h <= 10.0) {
            "LOW-HEALTH TEAMFIGHT"
        } else if count >= 4 {
            "TEAMFIGHT IMPACT"
        } else if count == 3 {
            "TRIPLE ASSIST"
        } else {
            "SUPPORT IMPACT"
        };
        let candidate = PlayCandidate {
            kind: "assist",
            assist_count: count,
            min_health_percent: min_health,
            died_after,
            ace,
            action_seconds: (last_wall - first_wall).max(0.0),
            assist_heavy,
            ..Default::default()
        };
        let Some(result) = self.score(&candidate, "assist", label) else { return };
        let mut killers: Vec<String> = Vec::new();
        for record in &records {
            if !record.killer_name.is_empty() && !killers.contains(&record.killer_name) {
                killers.push(record.killer_name.clone());
            }
        }
        log::info!("Automatic support highlight ready: {label} (score {})", result.score);
        let request = HighlightRequest {
            label: label.into(),
            event_started_at: Some(first_wall),
            event_ended_at: Some(last_wall),
            pre_seconds: 10.0,
            post_seconds: 9.0,
            event_game_time: Some(records[0].game_time),
            event_kind: "assist".into(),
            automatic: true,
            highlight_score: result.score,
            score_reasons: result.reasons,
            victim_names: records.iter().map(|r| r.victim_name.clone()).filter(|s| !s.is_empty()).collect(),
            victim_champions: records
                .iter()
                .map(|r| r.victim_champion.clone())
                .filter(|s| !s.is_empty())
                .collect(),
            assister_names: killers,
            ..self.request_base()
        };
        self.collect(request);
    }

    /// Update kill streaks for a ChampionKill and return the victim's
    /// streak before this death (3+ means a shutdown).
    fn update_streaks(&mut self, event: &Value) -> i64 {
        let key = |v: &Value| normalise_name(&text(v));
        let victim = key(field(event, "VictimName"));
        let killer = key(field(event, "KillerName"));
        let previous = self.streaks.remove(&victim).unwrap_or(0);
        if !killer.is_empty() && killer != victim {
            *self.streaks.entry(killer).or_insert(0) += 1;
        }
        previous
    }

    /// When you win: the final seconds before the Nexus falls, unless the
    /// last automatic clip already covers them.
    fn emit_victory(&mut self, event: &Value) {
        let wall = now();
        if wall - self.last_auto_end < 15.0 {
            log::info!("Victory moment already covered by the last fight clip");
            return;
        }
        let candidate = PlayCandidate { kind: "victory", ..Default::default() };
        let Some(result) = self.score(&candidate, "victory", "VICTORY") else { return };
        let request = HighlightRequest {
            label: "VICTORY".into(),
            event_started_at: Some(wall - 1.0),
            event_ended_at: Some(wall),
            pre_seconds: 18.0,
            post_seconds: 4.0,
            event_game_time: Some(event_time(event)),
            event_kind: "victory".into(),
            automatic: true,
            highlight_score: result.score,
            score_reasons: result.reasons,
            ..self.request_base()
        };
        log::info!("Saving the winning moment");
        self.tracker.record("VICTORY", event_time(event), &self.match_id(), wall);
        self.emit(request);
    }

    fn clear_kills(&mut self) {
        self.kills.clear();
        self.multikill_streak = 0;
        self.last_kill_at = None;
        self.last_kill_game_time = 0.0;
    }

    fn clear_assists(&mut self) {
        self.assists.clear();
        self.last_assist_at = None;
        self.last_assist_game_time = 0.0;
    }

    fn identity_for(&self, value: &Value) -> Identity {
        aliases_of(value)
            .iter()
            .find_map(|alias| self.by_alias.get(alias).cloned())
            .unwrap_or_default()
    }

    fn canonical_names(&self, values: &Value) -> Vec<String> {
        let mut out: Vec<String> = Vec::new();
        for value in list(values) {
            let identity = self.identity_for(&value);
            let name = if identity.known() {
                identity.display_name()
            } else {
                text(&value).trim().to_string()
            };
            if !name.is_empty() && !out.contains(&name) {
                out.push(name);
            }
        }
        out
    }

    fn is_active(&self, value: &Value) -> bool {
        !aliases_of(value).is_disjoint(&self.active_aliases)
    }

    fn belongs_to_active_team(&self, event: &Value) -> bool {
        let mut aliases = aliases_of(field(event, "KillerName"));
        for assister in list(field(event, "Assisters")) {
            aliases.extend(aliases_of(&assister));
        }
        if !aliases.is_disjoint(&self.active_aliases) {
            return true;
        }
        let Some(team) = &self.active_team else { return false };
        aliases
            .iter()
            .any(|alias| self.by_alias.get(alias).is_some_and(|i| &i.team == team))
    }

    fn match_id(&self) -> String {
        self.current.as_ref().map(|c| c.match_id.clone()).unwrap_or_default()
    }

    fn connected_text(&self) -> String {
        let name = self.active.display_name();
        if name == "Unknown player" {
            return "Live event detection connected".into();
        }
        if self.active.champion_name.is_empty() {
            format!("Connected as {name}")
        } else {
            format!("Connected as {name} • {}", self.active.champion_name)
        }
    }

    fn end_match(&mut self, result: &str) {
        if self.current.is_none() || self.match_ended {
            return;
        }
        self.flush_kills(true);
        self.match_ended = true;
        if let Some(ctx) = &self.current {
            log::info!("Match session ended: {} ({result})", ctx.match_id);
            let _ = self.tx.send(Cmd::MatchEnded(ctx.clone(), result.to_string()));
        }
        // Flush grouped fights after the final kill so it lands in this match.
        self.flush_pending();
    }

    fn set_status(&self, text: &str, connected: bool) {
        let mut status = self.shared.status.lock().unwrap();
        if status.0 != text || status.1 != connected {
            *status = (text.to_string(), connected);
        }
    }

    // -----------------------------------------------------------------------
    // Smart Highlights V2: fight grouping, adaptive timing, duplicate control
    // -----------------------------------------------------------------------

    fn collect(&mut self, request: HighlightRequest) {
        let groupable = matches!(request.event_kind.as_str(), "kill" | "assist" | "dragon" | "baron" | "objective" | "fight");
        if !request.automatic || !groupable || request.precise_window().is_none() {
            self.emit(request);
            return;
        }
        if !self.pending.is_empty() && !self.related(&request) {
            let batch = std::mem::take(&mut self.pending);
            self.emit_merged(batch);
        }
        self.pending.push(request);
        let combat = self
            .pending
            .iter()
            .any(|r| matches!(r.event_kind.as_str(), "kill" | "assist" | "fight"));
        self.pending_deadline = Some(Instant::now() + if combat { COMBAT_FLUSH } else { OBJECTIVE_FLUSH });
    }

    fn related(&self, request: &HighlightRequest) -> bool {
        let pending_match = self.pending.iter().find(|r| !r.match_id.is_empty()).map(|r| r.match_id.as_str());
        if let Some(m) = pending_match {
            if !request.match_id.is_empty() && m != request.match_id {
                return false;
            }
        }
        let windows: Vec<(f64, f64)> = self.pending.iter().filter_map(|r| r.precise_window()).collect();
        let start = windows.iter().map(|w| w.0).fold(f64::INFINITY, f64::min);
        let end = windows.iter().map(|w| w.1).fold(f64::NEG_INFINITY, f64::max);
        let Some((rs, re)) = request.precise_window() else { return false };
        let gap = if rs > end {
            rs - end
        } else if start > re {
            start - re
        } else {
            0.0
        };
        gap <= GROUP_GAP
    }

    fn flush_pending(&mut self) {
        self.pending_deadline = None;
        let batch = std::mem::take(&mut self.pending);
        if !batch.is_empty() {
            self.emit_merged(batch);
        }
    }

    fn emit(&mut self, request: HighlightRequest) {
        if request.automatic {
            self.match_clips += 1;
            if let Some((_, end)) = request.desired_window() {
                self.last_auto_end = self.last_auto_end.max(end);
            }
        }
        let _ = self.tx.send(Cmd::Highlight(request));
    }

    fn emit_merged(&mut self, batch: Vec<HighlightRequest>) {
        let buffer = f64::from(self.cfg.read().unwrap().buffer_seconds);
        let merged = merge_requests(batch, buffer);
        if self.is_duplicate(&merged) {
            log::info!(
                "Smart Highlights V2 suppressed duplicate {} (score {})",
                merged.clean_label(),
                merged.highlight_score
            );
            return;
        }
        log::info!(
            "Smart Highlights V2 ready: {} • score {} • window {:.1}s + {:.1}s",
            merged.clean_label(),
            merged.highlight_score,
            merged.pre_seconds,
            merged.post_seconds
        );
        self.emit(merged);
    }

    fn is_duplicate(&mut self, request: &HighlightRequest) -> bool {
        let Some((start, end)) = request.precise_window() else { return false };
        let kind = request.event_kind.to_lowercase();
        let score = request.highlight_score;
        while self.recent.front().is_some_and(|r| r.seen.elapsed() > Duration::from_secs(45)) {
            self.recent.pop_front();
        }
        for old in &self.recent {
            if !request.match_id.is_empty() && !old.match_id.is_empty() && request.match_id != old.match_id {
                continue;
            }
            let intersection = (end.min(old.end) - start.max(old.start)).max(0.0);
            let shorter = (end - start).max(0.1).min((old.end - old.start).max(0.1)).max(0.1);
            let overlap = intersection / shorter;
            let center_distance = ((start + end) / 2.0 - (old.start + old.end) / 2.0).abs();
            let related_kind = kind == old.kind || kind == "fight" || old.kind == "fight";
            if related_kind && (overlap >= 0.72 || center_distance <= 2.5) && score <= old.score + 8 {
                return true;
            }
        }
        if self.recent.len() >= 24 {
            self.recent.pop_front();
        }
        self.recent.push_back(Recent {
            match_id: request.match_id.clone(),
            start,
            end,
            kind,
            score,
            seen: Instant::now(),
        });
        false
    }
}

fn event_time(event: &Value) -> f64 {
    as_f64(field(event, "EventTime")).unwrap_or(0.0)
}

fn unique(values: impl IntoIterator<Item = String>) -> Vec<String> {
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for value in values {
        let value = value.trim().to_string();
        if !value.is_empty() && seen.insert(value.to_lowercase()) {
            out.push(value);
        }
    }
    out
}

pub fn label_priority(label: &str) -> i64 {
    let upper = label.to_uppercase();
    const PRIORITIES: [(&str, i64); 20] = [
        ("PENTAKILL", 100),
        ("QUADRA", 90),
        ("TRIPLE", 80),
        ("2V1", 76),
        ("DOUBLE", 70),
        ("ELDER", 68),
        ("BARON STEAL", 66),
        ("ATAKHAN STEAL", 66),
        ("DRAGON STEAL", 64),
        ("HERALD STEAL", 62),
        ("VOIDGRUB STEAL", 52),
        ("OBJECTIVE STEAL", 50),
        ("SHUTDOWN", 42),
        ("FIRST BLOOD", 40),
        ("VICTORY", 30),
        ("ACE", 60),
        ("LOW-HEALTH", 58),
        ("TEAMFIGHT", 55),
        ("SUPPORT", 45),
        ("SINGLE", 35),
    ];
    PRIORITIES
        .iter()
        .find(|(marker, _)| upper.contains(marker))
        .map_or(10, |(_, v)| *v)
}

fn steal_name(joined: &str) -> &'static str {
    for (marker, name) in [
        ("ELDER", "ELDER STEAL"),
        ("BARON", "BARON STEAL"),
        ("ATAKHAN", "ATAKHAN STEAL"),
        ("HERALD", "HERALD STEAL"),
        ("VOIDGRUB", "VOIDGRUB STEAL"),
        ("DRAGON", "DRAGON STEAL"),
    ] {
        if joined.contains(marker) {
            return name;
        }
    }
    "OBJECTIVE STEAL"
}

/// Merge related candidates into one clip request (Smart Highlights V2).
pub fn merge_requests(requests: Vec<HighlightRequest>, buffer_seconds: f64) -> HighlightRequest {
    let valid: Vec<HighlightRequest> = requests.iter().filter(|r| r.precise_window().is_some()).cloned().collect();
    if valid.is_empty() {
        return requests
            .into_iter()
            .max_by_key(|r| r.highlight_score)
            .unwrap_or_default();
    }
    let base = valid
        .iter()
        .max_by_key(|r| (r.highlight_score, label_priority(&r.clean_label())))
        .cloned()
        .unwrap();
    let windows: Vec<(f64, f64)> = valid.iter().filter_map(|r| r.precise_window()).collect();
    let event_start = windows.iter().map(|w| w.0).fold(f64::INFINITY, f64::min);
    let event_end = windows.iter().map(|w| w.1).fold(f64::NEG_INFINITY, f64::max);
    let duration = (event_end - event_start).max(0.0);
    let kinds: HashSet<String> = valid.iter().map(|r| r.event_kind.to_lowercase()).collect();
    let combat = kinds.iter().any(|k| matches!(k.as_str(), "kill" | "assist" | "fight"));
    let objective = kinds.iter().any(|k| matches!(k.as_str(), "dragon" | "baron" | "objective"));

    let mut reasons = unique(valid.iter().flat_map(|r| r.score_reasons.iter().cloned()));
    let mut score = valid.iter().map(|r| r.highlight_score).max().unwrap_or(0);
    if valid.len() > 1 {
        score += (6 * (valid.len() as i64 - 1)).min(18);
        reasons.push(format!("grouped {} related highlight events", valid.len()));
    }
    if kinds.contains("kill") && kinds.contains("assist") {
        score += 8;
        reasons.push("combined kill and teamfight participation".into());
    }
    if objective && combat {
        score += 15;
        reasons.push("objective converted into the same fight".into());
    }
    let score = score.min(150);
    let lowered = reasons.join(" ").to_lowercase();

    let mut pre = valid.iter().map(|r| r.pre_seconds).fold(0.0, f64::max);
    let mut post = valid.iter().map(|r| r.post_seconds).fold(0.0, f64::max);
    if duration >= 8.0 {
        pre += 1.5;
        post += 1.5;
    }
    if duration >= 14.0 {
        pre += 1.5;
        post += 1.5;
    }
    if score >= 70 {
        pre += 1.5;
        post += 1.0;
    }
    if score >= 100 {
        pre += 1.5;
        post += 1.0;
    }
    if lowered.contains("outnumbered") || lowered.contains("v1") {
        pre += 3.0;
        post += 2.0;
    }
    if lowered.contains("survived at") || lowered.contains("low-health") {
        pre += 1.0;
        post += 3.0;
    }
    if lowered.contains("ace") {
        post += 2.0;
    }
    if objective {
        pre = pre.max(10.0);
        post = post.max(8.0);
    }
    if valid.len() > 1 {
        pre += 1.0;
        post += 1.0;
    }
    let (pre, post) = fit_to_buffer(duration, pre.min(16.0), post.min(14.0), buffer_seconds);

    let labels: Vec<String> = valid.iter().map(|r| r.clean_label()).collect();
    let label = build_label(&valid, &labels, combat, objective, &kinds, &lowered);
    let first_text = |f: fn(&HighlightRequest) -> &String| {
        valid.iter().map(f).find(|s| !s.is_empty()).cloned().unwrap_or_default()
    };
    HighlightRequest {
        label,
        event_started_at: Some(event_start),
        event_ended_at: Some(event_end),
        pre_seconds: pre,
        post_seconds: post,
        match_id: first_text(|r| &r.match_id),
        player_name: first_text(|r| &r.player_name),
        champion_name: first_text(|r| &r.champion_name),
        game_mode: first_text(|r| &r.game_mode),
        event_game_time: valid.iter().filter_map(|r| r.event_game_time).min_by(f64::total_cmp).or(base.event_game_time),
        event_kind: if kinds.len() == 1 { kinds.iter().next().cloned().unwrap() } else { "fight".into() },
        automatic: true,
        triggered_at_wall: valid.iter().filter_map(|r| r.triggered_at_wall).max_by(f64::total_cmp),
        highlight_score: score,
        score_reasons: unique(reasons),
        victim_names: unique(valid.iter().flat_map(|r| r.victim_names.iter().cloned())),
        victim_champions: unique(valid.iter().flat_map(|r| r.victim_champions.iter().cloned())),
        assister_names: unique(valid.iter().flat_map(|r| r.assister_names.iter().cloned())),
        ..base
    }
}

fn build_label(
    requests: &[HighlightRequest],
    labels: &[String],
    combat: bool,
    objective: bool,
    kinds: &HashSet<String>,
    reasons: &str,
) -> String {
    let joined = labels.join(" | ");
    for marker in ["PENTAKILL", "QUADRA KILL", "TRIPLE KILL", "2V1 DOUBLE KILL", "DOUBLE KILL"] {
        if joined.contains(marker) {
            if objective {
                let steal = steal_name(&joined);
                return format!("{marker} + {steal}");
            }
            return marker.into();
        }
    }
    if objective && combat {
        return format!("{} FIGHT", steal_name(&joined));
    }
    if reasons.contains("survived at") && combat {
        return "LOW-HEALTH OUTPLAY".into();
    }
    if reasons.contains("ace") && combat {
        return "ACE TEAMFIGHT".into();
    }
    if kinds.contains("kill") && kinds.contains("assist") {
        return "TEAMFIGHT OUTPLAY".into();
    }
    requests
        .iter()
        .max_by_key(|r| (label_priority(&r.clean_label()), r.highlight_score))
        .map(|r| r.clean_label())
        .unwrap_or_else(|| "HIGHLIGHT".into())
}

fn fit_to_buffer(action: f64, pre: f64, post: f64, buffer_seconds: f64) -> (f64, f64) {
    let buffer = buffer_seconds.max(12.0);
    let budget = (buffer - action.max(0.0) - 1.0).max(2.0);
    let requested = pre + post;
    let round2 = |v: f64| (v * 100.0).round() / 100.0;
    if requested <= budget {
        return (round2(pre), round2(post));
    }
    let share = if requested > 0.0 { pre / requested } else { 0.55 };
    let mut fitted_pre = (budget * share).max(1.0);
    let fitted_post = (budget - fitted_pre).max(1.0);
    if fitted_pre + fitted_post > budget {
        fitted_pre = (fitted_pre - (fitted_pre + fitted_post - budget)).max(1.0);
    }
    (round2(fitted_pre), round2(fitted_post))
}

#[cfg(test)]
#[path = "live_tests.rs"]
mod detector_tests;

#[cfg(test)]
mod tests {
    use super::*;

    fn req(label: &str, kind: &str, start: f64, end: f64, score: i64) -> HighlightRequest {
        HighlightRequest {
            label: label.into(),
            event_kind: kind.into(),
            event_started_at: Some(start),
            event_ended_at: Some(end),
            pre_seconds: 7.0,
            post_seconds: 7.0,
            automatic: true,
            highlight_score: score,
            score_reasons: vec![format!("{kind} reason")],
            ..Default::default()
        }
    }

    #[test]
    fn merges_kill_and_assist_into_teamfight() {
        let merged = merge_requests(
            vec![req("SINGLE KILL", "kill", 100.0, 100.0, 27), req("SUPPORT IMPACT", "assist", 103.0, 104.0, 24)],
            45.0,
        );
        assert_eq!(merged.label, "TEAMFIGHT OUTPLAY");
        assert_eq!(merged.event_kind, "fight");
        // 27 + 6 (grouped) + 8 (kill+assist)
        assert_eq!(merged.highlight_score, 41);
        assert_eq!(merged.event_started_at, Some(100.0));
        assert_eq!(merged.event_ended_at, Some(104.0));
    }

    #[test]
    fn keeps_multikill_label_with_steal() {
        let merged = merge_requests(
            vec![req("TRIPLE KILL", "kill", 10.0, 14.0, 80), req("BARON STEAL", "baron", 15.0, 15.0, 70)],
            45.0,
        );
        assert_eq!(merged.label, "TRIPLE KILL + BARON STEAL");
        assert!(merged.pre_seconds >= 10.0);
    }

    #[test]
    fn fits_context_inside_buffer() {
        let (pre, post) = fit_to_buffer(20.0, 16.0, 14.0, 30.0);
        assert!(pre + post <= 9.0 + 1e-9);
        assert!(pre > post);
    }

    #[test]
    fn aliases_include_game_name() {
        let a = aliases_of(&Value::String("Faker  #KR1".into()));
        assert!(a.contains("faker #kr1"));
        assert!(a.contains("faker"));
    }
}
