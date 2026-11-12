//! Detector scenarios: synthetic Riot event streams through the real
//! event pipeline (no game or network needed).

use super::*;
use crate::config::Settings;
use serde_json::json;
use std::sync::mpsc::Receiver;
use std::sync::RwLock;

struct Harness {
    d: Detector,
    rx: Receiver<Cmd>,
    next_id: i64,
    dir: PathBuf,
}

impl Drop for Harness {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn identity(name: &str, team: &str) -> Identity {
    Identity {
        riot_id: name.into(),
        champion_name: format!("{name}-champ"),
        team: team.into(),
        level: 10,
        aliases: [normalise_name(name)].into_iter().collect(),
        ..Default::default()
    }
}

/// A detector in a match: "me" + allies on ORDER, enemies on CHAOS.
fn harness(tag: &str) -> Harness {
    let dir = std::env::temp_dir().join(format!("lh-detector-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let settings = Settings { clip_dir: dir.to_string_lossy().into_owned(), ..Settings::default() };
    let (tx, rx) = std::sync::mpsc::channel();
    let mut d = Detector::new(dir.clone(), Arc::new(RwLock::new(settings)), tx, Arc::new(EventTracker::new(120.0)), LiveShared::new());
    let mut by_alias = HashMap::new();
    for (name, team) in [
        ("me", "ORDER"), ("ally1", "ORDER"), ("ally2", "ORDER"), ("ally3", "ORDER"),
        ("enemy1", "CHAOS"), ("enemy2", "CHAOS"), ("enemy3", "CHAOS"),
    ] {
        by_alias.insert(normalise_name(name), identity(name, team));
    }
    d.active = identity("me", "ORDER");
    d.active_aliases = d.active.aliases.clone();
    d.active_team = Some("ORDER".into());
    d.by_alias = by_alias;
    d.current = Some(MatchContext { match_id: "m1".into(), started_at: now() - 900.0, ..Default::default() });
    // Establish the event baseline (EventID 0).
    d.consume_events(&[json!({ "EventID": 0, "EventName": "GameStart", "EventTime": 0.0 })]);
    Harness { d, rx, next_id: 1, dir }
}

impl Harness {
    fn send(&mut self, events: Vec<Value>) {
        let mut stamped = Vec::new();
        for mut e in events {
            e["EventID"] = json!(self.next_id);
            self.next_id += 1;
            stamped.push(e);
        }
        self.d.consume_events(&stamped);
    }

    fn kill(&self, killer: &str, victim: &str, assisters: &[&str]) -> Value {
        json!({ "EventName": "ChampionKill", "EventTime": 600.0 + self.next_id as f64, "KillerName": killer, "VictimName": victim, "Assisters": assisters })
    }

    /// Settle everything the detector is holding and return the clip requests.
    fn finish(&mut self) -> Vec<HighlightRequest> {
        self.d.flush_kills(true);
        self.d.flush_assists(true);
        self.d.flush_pending();
        self.rx
            .try_iter()
            .filter_map(|c| match c {
                Cmd::Highlight(r) => Some(r),
                _ => None,
            })
            .collect()
    }
}

#[test]
fn labels_a_shutdown() {
    let mut h = harness("shutdown");
    let streak: Vec<Value> = ["ally1", "ally2", "ally3"].iter().map(|v| h.kill("enemy1", v, &[])).collect();
    h.send(streak);
    let k = h.kill("me", "enemy1", &[]);
    h.send(vec![k]);
    let clips = h.finish();
    assert_eq!(clips.len(), 1);
    assert_eq!(clips[0].label, "SHUTDOWN");
    assert!(clips[0].score_reasons.iter().any(|r| r == "shut down a 3-kill streak"));
}

#[test]
fn knows_streaks_from_before_it_connected() {
    let mut h = harness("late");
    h.d.last_event_id = None; // reconnect mid-game: earlier events become the baseline
    let history: Vec<Value> = (0..4)
        .map(|i| json!({ "EventID": 100 + i, "EventName": "ChampionKill", "EventTime": 300.0 + i as f64, "KillerName": "enemy2", "VictimName": "ally1", "Assisters": [] }))
        .collect();
    h.d.consume_events(&history);
    h.next_id = 200;
    // Solo kill: 12 + 15 (solo) + 14 (4-streak shutdown) = 41, kept at Balanced.
    let k = h.kill("me", "enemy2", &[]);
    h.send(vec![k]);
    let clips = h.finish();
    assert_eq!(clips.len(), 1);
    assert_eq!(clips[0].label, "SHUTDOWN");
    assert!(clips[0].score_reasons.iter().any(|r| r == "shut down a 4-kill streak"));
}

#[test]
fn labels_first_blood() {
    let mut h = harness("firstblood");
    let k = h.kill("me", "enemy1", &[]);
    h.send(vec![k, json!({ "EventName": "FirstBlood", "EventTime": 601.0, "Recipient": "me" })]);
    let clips = h.finish();
    assert_eq!(clips.len(), 1);
    assert_eq!(clips[0].label, "FIRST BLOOD");
    assert!(clips[0].score_reasons.iter().any(|r| r == "first blood"));
}

#[test]
fn herald_steal_merges_into_the_fight() {
    let mut h = harness("herald");
    let kills = vec![h.kill("me", "enemy1", &[]), h.kill("me", "enemy2", &[]), h.kill("me", "enemy3", &[])];
    h.send(kills);
    h.d.flush_kills(true);
    h.send(vec![json!({ "EventName": "HeraldKill", "EventTime": 610.0, "KillerName": "me", "Stolen": "True", "Assisters": [] })]);
    let clips = h.finish();
    assert_eq!(clips.len(), 1, "one merged fight clip");
    assert_eq!(clips[0].label, "TRIPLE KILL + HERALD STEAL");
}

#[test]
fn routine_objectives_are_not_clipped() {
    let mut h = harness("routine");
    h.send(vec![json!({ "EventName": "HordeKill", "EventTime": 500.0, "KillerName": "ally1", "Stolen": "False", "Assisters": ["me"] })]);
    assert!(h.finish().is_empty());
}

#[test]
fn saves_the_winning_moment_unless_already_covered() {
    let mut h = harness("victory");
    h.send(vec![json!({ "EventName": "GameEnd", "EventTime": 1800.0, "Result": "Win" })]);
    assert_eq!(h.finish().iter().filter(|c| c.label == "VICTORY").count(), 1);

    let mut h = harness("victory-covered");
    h.d.last_auto_end = now();
    h.send(vec![json!({ "EventName": "GameEnd", "EventTime": 1800.0, "Result": "Win" })]);
    assert!(h.finish().iter().all(|c| c.label != "VICTORY"));

    let mut h = harness("defeat");
    h.send(vec![json!({ "EventName": "GameEnd", "EventTime": 1800.0, "Result": "Lose" })]);
    assert!(h.finish().iter().all(|c| c.label != "VICTORY"));
}

#[test]
fn budget_raises_the_bar_late_in_a_busy_match() {
    // A double kill with allied help scores 30: kept at Balanced...
    let mut h = harness("budget-normal");
    let kills = vec![h.kill("me", "enemy1", &["ally1"]), h.kill("me", "enemy2", &["ally1"])];
    h.send(kills);
    assert_eq!(h.finish().len(), 1);
    // ...but not once the match already has 8 automatic clips.
    let mut h = harness("budget-busy");
    h.d.match_clips = 8;
    let kills = vec![h.kill("me", "enemy1", &["ally1"]), h.kill("me", "enemy2", &["ally1"])];
    h.send(kills);
    assert!(h.finish().is_empty());
    // Standout plays are always kept.
    let mut h = harness("budget-standout");
    h.d.match_clips = 12;
    let kills = vec![h.kill("me", "enemy1", &[]), h.kill("me", "enemy2", &[]), h.kill("me", "enemy3", &[])];
    h.send(kills);
    assert_eq!(h.finish().len(), 1);
}

#[test]
fn learned_preferences_change_what_is_kept() {
    // A lone solo kill (score 27) is below Balanced (30)...
    let mut h = harness("learn-before");
    let k = h.kill("me", "enemy1", &[]);
    h.send(vec![k]);
    assert!(h.finish().is_empty());
    // ...until you've rated solo kills as good a couple of times.
    let mut h = harness("learn-after");
    crate::learn::record(&h.dir, &["1 solo kill".into()], crate::learn::GOOD);
    crate::learn::record(&h.dir, &["2 solo kills".into()], crate::learn::GOOD);
    let k = h.kill("me", "enemy1", &[]);
    h.send(vec![k]);
    let clips = h.finish();
    assert_eq!(clips.len(), 1);
    assert!(clips[0].score_reasons.iter().any(|r| r.starts_with("your ratings +")));
}
